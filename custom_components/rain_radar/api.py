"""Async HTTP client helpers for Rain Radar providers."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
import logging
from typing import Any

from aiohttp import ClientError, ClientResponse
from homeassistant.core import HomeAssistant
from homeassistant.helpers import aiohttp_client

from .const import get_user_agent
from .providers.models import CacheMetadata

_LOGGER = logging.getLogger(__name__)
_MAX_CACHE_ENTRIES = 256


class RainRadarApiError(Exception):
    """Base API error."""


class RainRadarApiAuthError(RainRadarApiError):
    """Authentication or User-Agent/contact error."""


class RainRadarApiTemporaryError(RainRadarApiError):
    """Temporary provider or connection error."""


class RainRadarApiRateLimitedError(RainRadarApiTemporaryError):
    """Provider rate limit error."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        """Initialize a rate-limit error with an optional retry delay."""
        super().__init__(message)
        self.retry_after = retry_after


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

    async def async_get_json(
        self,
        cache_key: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        request_timeout: int = 15,
        auth_required: bool = True,
    ) -> tuple[dict[str, Any] | list[Any], CacheMetadata]:
        """Fetch JSON and reuse cached payload on 304 responses."""
        session = aiohttp_client.async_get_clientsession(self.hass)
        headers = self._headers(cache_key)

        try:
            async with asyncio.timeout(request_timeout):
                async with session.get(url, params=params, headers=headers) as response:
                    if response.status == 304:
                        cached = self._cache.get(cache_key)
                        if cached is None:
                            raise RainRadarApiError(
                                f"{url} returned 304 without cached data"
                            )
                        metadata = _validated_cache_metadata(response, cached.metadata)
                        self._store_cache(
                            cache_key, _CachedResponse(cached.payload, metadata)
                        )
                        if not isinstance(cached.payload, (dict, list)):
                            raise RainRadarApiError(
                                f"{url} returned cached non-JSON data"
                            )
                        return cached.payload, metadata

                    await self._raise_for_status(
                        response, url, auth_required=auth_required
                    )
                    payload = await response.json()
                    metadata = _cache_metadata_from_response(response)
        except TimeoutError as err:
            raise RainRadarApiTemporaryError(f"Timed out fetching {url}") from err
        except ClientError as err:
            raise RainRadarApiTemporaryError(f"Error fetching {url}: {err}") from err

        self._store_cache(cache_key, _CachedResponse(payload, metadata))
        return payload, metadata

    async def async_get_bytes(
        self,
        cache_key: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        request_timeout: int = 15,
        accept: str = "image/png",
    ) -> tuple[bytes, CacheMetadata, str]:
        """Fetch bytes and reuse cached payload on 304 responses."""
        session = aiohttp_client.async_get_clientsession(self.hass)
        headers = self._headers(cache_key, accept=accept)

        try:
            async with asyncio.timeout(request_timeout):
                async with session.get(url, params=params, headers=headers) as response:
                    if response.status == 304:
                        cached = self._cache.get(cache_key)
                        if cached is None:
                            raise RainRadarApiError(
                                f"{url} returned 304 without cached data"
                            )
                        if not isinstance(cached.payload, bytes):
                            raise RainRadarApiError(
                                f"{url} returned cached non-binary data"
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
                    content_type = response.headers.get(
                        "Content-Type", "application/octet-stream"
                    ).split(";", 1)[0]
        except TimeoutError as err:
            raise RainRadarApiTemporaryError(f"Timed out fetching {url}") from err
        except ClientError as err:
            raise RainRadarApiTemporaryError(f"Error fetching {url}: {err}") from err

        self._store_cache(cache_key, _CachedResponse(payload, metadata, content_type))
        return payload, metadata, content_type

    def _store_cache(self, cache_key: str, cached: _CachedResponse) -> None:
        """Store a bounded response cache entry."""
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
                )
            raise RainRadarApiAuthError(
                f"Provider rejected the request for {url}: HTTP {response.status}"
            )
        if response.status == 429:
            raise RainRadarApiRateLimitedError(
                f"Provider rate limited the request for {url}",
                retry_after=_parse_retry_after(response.headers.get("Retry-After")),
            )
        if response.status in (408, 425) or 500 <= response.status < 600:
            _LOGGER.debug(
                "Provider request temporarily failed: url=%s status=%s body=%s",
                url,
                response.status,
                body,
            )
            raise RainRadarApiTemporaryError(
                f"Provider temporarily unavailable for {url}: HTTP {response.status}"
            )

        _LOGGER.debug(
            "Provider request failed: url=%s status=%s body=%s",
            url,
            response.status,
            body,
        )
        raise RainRadarApiError(f"Provider returned HTTP {response.status}: {body}")


def _cache_metadata_from_response(response: ClientResponse) -> CacheMetadata:
    now = datetime.now(UTC)
    expires_at = _parse_expires(response.headers.get("Expires"))
    max_age = _parse_cache_control_max_age(response.headers.get("Cache-Control"))
    if max_age is not None:
        expires_at = now + timedelta(seconds=max_age)

    return CacheMetadata(
        fetched_at=now,
        expires_at=expires_at,
        etag=response.headers.get("ETag"),
        last_modified=response.headers.get("Last-Modified"),
        from_cache=False,
    )


def _validated_cache_metadata(
    response: ClientResponse,
    cached: CacheMetadata,
) -> CacheMetadata:
    """Merge cache headers after a successful conditional validation."""
    metadata = _cache_metadata_from_response(response)
    expires_at = metadata.expires_at
    if expires_at is None and cached.expires_at is not None:
        if cached.expires_at > metadata.fetched_at:
            expires_at = cached.expires_at

    return CacheMetadata(
        fetched_at=metadata.fetched_at,
        expires_at=expires_at,
        etag=metadata.etag or cached.etag,
        last_modified=metadata.last_modified or cached.last_modified,
        from_cache=True,
    )


def _parse_expires(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
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
        return max(0.0, float(value))
    except ValueError:
        pass

    try:
        retry_at = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=UTC)
    return max(0.0, (retry_at.astimezone(UTC) - datetime.now(UTC)).total_seconds())
