"""Async HTTP client helpers for Rain Radar providers."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
import json
import logging
import math
import random
from typing import Any

from aiohttp import ClientError, ClientResponse, ContentTypeError
from homeassistant.core import HomeAssistant
from homeassistant.helpers import aiohttp_client

from .const import get_user_agent
from .providers.models import CacheMetadata

_LOGGER = logging.getLogger(__name__)
_MAX_CACHE_ENTRIES = 256


class RainRadarApiError(Exception):
    """Base API error."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        reason: str | None = None,
    ) -> None:
        """Initialize an error with safe, structured request diagnostics."""
        super().__init__(message)
        self.status_code = status_code
        self.reason = reason
        self.next_retry: datetime | None = None
        self.last_attempt: datetime | None = None


class RainRadarApiAuthError(RainRadarApiError):
    """Authentication or User-Agent/contact error."""


class RainRadarApiTemporaryError(RainRadarApiError):
    """Temporary provider or connection error."""

    def __init__(
        self,
        message: str,
        *,
        retry_after: float | None = None,
        status_code: int | None = None,
        reason: str | None = None,
    ) -> None:
        """Initialize a temporary error with the provider's retry delay."""
        super().__init__(message, status_code=status_code, reason=reason)
        self.retry_after = retry_after


class RainRadarApiRateLimitedError(RainRadarApiTemporaryError):
    """Provider rate limit error."""


@dataclass(slots=True)
class _CachedResponse:
    """Cached response and HTTP cache validators."""

    payload: dict[str, Any] | list[Any] | bytes
    metadata: CacheMetadata
    content_type: str | None = None


class RainRadarApiClient:
    """Small async JSON client with conditional request support."""

    def __init__(self, hass: HomeAssistant, contact: str) -> None:
        """Initialize the API client."""
        self.hass = hass
        self._contact = contact.strip()
        self._cache: OrderedDict[str, _CachedResponse] = OrderedDict()
        self._inflight: dict[str, asyncio.Task] = {}
        self._failures: OrderedDict[str, tuple[datetime, RainRadarApiError, int]] = (
            OrderedDict()
        )
        self.last_attempt: datetime | None = None
        self.last_success: datetime | None = None

    @property
    def contact(self) -> str:
        """Return configured provider contact."""
        return self._contact

    def set_contact(self, contact: str) -> None:
        """Update configured provider contact."""
        self._contact = contact.strip()

    def _headers(
        self,
        cache_key: str | None = None,
        *,
        accept: str = "application/json",
    ) -> dict[str, str]:
        headers = {
            "Accept": accept,
            "User-Agent": get_user_agent(self.hass),
        }
        if self._contact:
            headers["From"] = self._contact

        if cache_key and (cached := self._cache.get(cache_key)):
            if cached.metadata.etag:
                headers["If-None-Match"] = cached.metadata.etag
            if cached.metadata.last_modified:
                headers["If-Modified-Since"] = cached.metadata.last_modified
        return headers

    async def _async_request(
        self, cache_key: str, fetch: Callable[[], Coroutine]
    ) -> tuple:
        """Reuse fresh data, one in-flight request, and bounded failure backoff."""
        now = datetime.now(UTC)
        cached = self._cache.get(cache_key)
        if cached and cached.metadata.expires_at and cached.metadata.expires_at > now:
            self._cache.move_to_end(cache_key)
            metadata = replace(cached.metadata, from_cache=True)
            if isinstance(cached.payload, bytes):
                return (
                    cached.payload,
                    metadata,
                    cached.content_type or "application/octet-stream",
                )
            return cached.payload, metadata
        if (task := self._inflight.get(cache_key)) and not task.done():
            return await asyncio.shield(task)
        failure = self._failures.get(cache_key)
        if failure and failure[0] > now:
            raise failure[1]

        async def run() -> tuple:
            self.last_attempt = datetime.now(UTC)
            try:
                result = await fetch()
            except RainRadarApiError as err:
                err.last_attempt = self.last_attempt
                count = failure[2] + 1 if failure else 1
                delay = min(900, 30 * 2 ** min(count - 1, 5)) * random.uniform(1, 1.2)
                delay = max(delay, getattr(err, "retry_after", None) or 0)
                err.next_retry = datetime.now(UTC) + timedelta(seconds=delay)
                self._failures[cache_key] = (err.next_retry, err, count)
                self._failures.move_to_end(cache_key)
                while len(self._failures) > _MAX_CACHE_ENTRIES:
                    self._failures.popitem(last=False)
                raise
            else:
                self._failures.pop(cache_key, None)
                self.last_success = datetime.now(UTC)
                return result
            finally:
                self._inflight.pop(cache_key, None)

        task = self.hass.async_create_background_task(
            run(), "Rain Radar HTTP request", eager_start=False
        )
        self._inflight[cache_key] = task
        return await asyncio.shield(task)

    async def async_get_json(self, cache_key: str, url: str, **kwargs) -> tuple:
        """Fetch JSON respecting freshness, concurrent consumers, and retry delays."""
        return await self._async_request(
            cache_key, lambda: self._async_fetch_json(cache_key, url, **kwargs)
        )

    async def async_get_bytes(self, cache_key: str, url: str, **kwargs) -> tuple:
        """Fetch an image once while its HTTP cache remains fresh."""
        return await self._async_request(
            cache_key, lambda: self._async_fetch_bytes(cache_key, url, **kwargs)
        )

    async def _async_fetch_json(
        self,
        cache_key: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        request_timeout: int = 15,
        auth_required: bool = True,
        default_cache_ttl: int = 0,
    ) -> tuple[dict[str, Any] | list[Any], CacheMetadata]:
        """Fetch JSON and reuse cached payload on 304 responses."""
        session = aiohttp_client.async_get_clientsession(self.hass)
        cached = self._cache.get(cache_key)
        headers = self._headers(cache_key)

        try:
            async with asyncio.timeout(request_timeout):
                async with session.get(url, params=params, headers=headers) as response:
                    if response.status == 304:
                        if cached is None:
                            raise RainRadarApiError(
                                f"{url} returned 304 without cached data",
                                status_code=304,
                                reason="invalid_response",
                            )
                        if not isinstance(cached.payload, (dict, list)):
                            raise RainRadarApiError(
                                f"{url} returned cached non-JSON data",
                                status_code=304,
                                reason="invalid_response",
                            )
                        metadata = _validated_cache_metadata(response, cached.metadata)
                        self._store_cache(
                            cache_key, _CachedResponse(cached.payload, metadata)
                        )
                        return cached.payload, metadata

                    await self._raise_for_status(
                        response, url, auth_required=auth_required
                    )
                    try:
                        payload = await response.json()
                    except (ContentTypeError, ValueError) as err:
                        raise RainRadarApiTemporaryError(
                            f"Invalid JSON response from {url}",
                            status_code=response.status,
                            reason="invalid_response",
                            retry_after=_parse_retry_after(
                                response.headers.get("Retry-After")
                            ),
                        ) from err
                    metadata = _cache_metadata_from_response(response)
                    if metadata.expires_at is None and default_cache_ttl:
                        metadata = replace(
                            metadata,
                            expires_at=metadata.fetched_at
                            + timedelta(seconds=default_cache_ttl),
                        )
        except TimeoutError as err:
            raise RainRadarApiTemporaryError(
                f"Timed out fetching {url}", reason="timeout"
            ) from err
        except ClientError as err:
            raise RainRadarApiTemporaryError(
                f"Error fetching {url}: {err}", reason="network"
            ) from err

        self._store_cache(cache_key, _CachedResponse(payload, metadata))
        return payload, metadata

    async def _async_fetch_bytes(
        self,
        cache_key: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        request_timeout: int = 15,
        accept: str = "image/png",
        immutable: bool = False,
    ) -> tuple[bytes, CacheMetadata, str]:
        """Fetch bytes and reuse cached payload on 304 responses."""
        session = aiohttp_client.async_get_clientsession(self.hass)
        cached = self._cache.get(cache_key)
        headers = self._headers(cache_key, accept=accept)

        try:
            async with asyncio.timeout(request_timeout):
                async with session.get(url, params=params, headers=headers) as response:
                    if response.status == 304:
                        if cached is None:
                            raise RainRadarApiError(
                                f"{url} returned 304 without cached data",
                                status_code=304,
                                reason="invalid_response",
                            )
                        if not isinstance(cached.payload, bytes):
                            raise RainRadarApiError(
                                f"{url} returned cached non-binary data",
                                status_code=304,
                                reason="invalid_response",
                            )
                        metadata = _validated_cache_metadata(response, cached.metadata)
                        self._store_cache(
                            cache_key,
                            _CachedResponse(
                                cached.payload, metadata, cached.content_type
                            ),
                        )
                        return (
                            cached.payload,
                            metadata,
                            cached.content_type or "application/octet-stream",
                        )

                    await self._raise_for_status(response, url)
                    payload = await response.read()
                    metadata = _cache_metadata_from_response(response)
                    if immutable and metadata.expires_at is None:
                        metadata = replace(
                            metadata, expires_at=metadata.fetched_at + timedelta(days=1)
                        )
                    content_type = response.headers.get(
                        "Content-Type", "application/octet-stream"
                    ).split(";", 1)[0]
        except TimeoutError as err:
            raise RainRadarApiTemporaryError(
                f"Timed out fetching {url}", reason="timeout"
            ) from err
        except ClientError as err:
            raise RainRadarApiTemporaryError(
                f"Error fetching {url}: {err}", reason="network"
            ) from err

        self._store_cache(cache_key, _CachedResponse(payload, metadata, content_type))
        return payload, metadata, content_type

    def _store_cache(self, cache_key: str, cached: _CachedResponse) -> None:
        """Store a bounded response cache entry."""
        if cached.metadata.no_store:
            self._cache.pop(cache_key, None)
            return
        self._cache[cache_key] = cached
        self._cache.move_to_end(cache_key)
        while len(self._cache) > _MAX_CACHE_ENTRIES:
            self._cache.popitem(last=False)

    async def _raise_for_status(
        self,
        response: ClientResponse,
        url: str,
        *,
        auth_required: bool = True,
    ) -> None:
        if response.status == 200:
            return

        text = await response.text()
        body = text[:300]
        if response.status in (401, 403):
            if not auth_required:
                raise RainRadarApiRateLimitedError(
                    f"Provider rejected the keyless request for {url}: "
                    f"HTTP {response.status}",
                    retry_after=_parse_retry_after(response.headers.get("Retry-After")),
                    status_code=response.status,
                    reason="request_rejected",
                )
            raise RainRadarApiAuthError(
                f"Provider rejected the request for {url}: HTTP {response.status}",
                status_code=response.status,
                reason="request_rejected",
            )
        if response.status == 429:
            reason = _rate_limit_reason(text)
            message = (
                f"Provider temporarily busy for {url}: HTTP 429"
                if reason == "server_busy"
                else f"Provider rate limited the request for {url}"
            )
            raise RainRadarApiRateLimitedError(
                message,
                retry_after=_parse_retry_after(response.headers.get("Retry-After")),
                status_code=response.status,
                reason=reason,
            )
        if response.status in (408, 425) or 500 <= response.status < 600:
            _LOGGER.debug(
                "Provider request temporarily failed: url=%s status=%s body=%s",
                url,
                response.status,
                body,
            )
            raise RainRadarApiTemporaryError(
                f"Provider temporarily unavailable for {url}: HTTP {response.status}",
                status_code=response.status,
                reason="server_error" if response.status >= 500 else "http_error",
                retry_after=_parse_retry_after(response.headers.get("Retry-After")),
            )

        _LOGGER.debug(
            "Provider request failed: url=%s status=%s body=%s",
            url,
            response.status,
            body,
        )
        raise RainRadarApiError(
            f"Provider returned HTTP {response.status}: {body}",
            status_code=response.status,
            reason="http_error",
        )


def _rate_limit_reason(body: str) -> str:
    """Distinguish DMI's known busy response from other HTTP 429 responses."""
    try:
        payload = json.loads(body)
    except ValueError:
        return "rate_limited"
    if (
        isinstance(payload, dict)
        and payload.get("message") == "Server is busy. Please try again later."
    ):
        return "server_busy"
    return "rate_limited"


def _cache_metadata_from_response(response: ClientResponse) -> CacheMetadata:
    now = datetime.now(UTC)
    expires_at = _parse_expires(response.headers.get("Expires"))
    max_age = _parse_cache_control_max_age(response.headers.get("Cache-Control"))
    if max_age is not None:
        try:
            age = max(0, int(response.headers.get("Age", "0")))
        except ValueError:
            age = 0
        expires_at = now + timedelta(seconds=max(0, max_age - age))
    directives = (response.headers.get("Cache-Control") or "").lower()
    if "no-cache" in directives or "no-store" in directives:
        expires_at = now

    return CacheMetadata(
        fetched_at=now,
        expires_at=expires_at,
        etag=response.headers.get("ETag"),
        last_modified=response.headers.get("Last-Modified"),
        from_cache=False,
        no_store="no-store" in directives,
    )


def _validated_cache_metadata(
    response: ClientResponse,
    cached: CacheMetadata,
) -> CacheMetadata:
    """Merge cache headers after a successful conditional validation."""
    metadata = _cache_metadata_from_response(response)
    expires_at = metadata.expires_at
    if (
        expires_at is None
        and cached.expires_at is not None
        and cached.expires_at > metadata.fetched_at
    ):
        expires_at = cached.expires_at

    return CacheMetadata(
        fetched_at=metadata.fetched_at,
        expires_at=expires_at,
        etag=metadata.etag or cached.etag,
        last_modified=metadata.last_modified or cached.last_modified,
        from_cache=True,
        no_store=metadata.no_store or cached.no_store,
    )


def _parse_expires(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except TypeError, ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _parse_cache_control_max_age(value: str | None) -> int | None:
    if not value:
        return None
    for part in value.split(","):
        key, _, raw_value = part.strip().partition("=")
        if key.lower() != "max-age" or not raw_value:
            continue
        try:
            return max(0, int(raw_value))
        except ValueError:
            return None
    return None


def _parse_retry_after(value: str | None) -> float | None:
    """Parse Retry-After delta seconds or an HTTP date."""
    if not value:
        return None
    try:
        delay = float(value)
    except ValueError:
        pass
    else:
        if not math.isfinite(delay):
            return None
        delay = max(0.0, delay)
        try:
            datetime.now(UTC) + timedelta(seconds=delay)
        except OverflowError:
            return None
        return delay

    try:
        retry_at = parsedate_to_datetime(value)
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        return max(0.0, (retry_at.astimezone(UTC) - datetime.now(UTC)).total_seconds())
    except TypeError, ValueError, OverflowError:
        return None
