"""DMI forecast provider implementation."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
import hashlib
from itertools import groupby
import logging
import math
from typing import Any

from homeassistant.util import dt as dt_util

from ..api import (
    RainRadarApiClient,
    RainRadarApiError,
    RainRadarApiRateLimitedError,
    RainRadarApiTemporaryError,
)
from ..const import (
    DMI_ATTRIBUTION,
    DMI_FORECAST_PARAMETERS,
    DMI_FORECAST_URL,
    DOMAIN,
    PROVIDER_DMI,
)
from .models import (
    CacheMetadata,
    CoverageStatus,
    Location,
    PrecipitationForecast,
    PrecipitationSample,
    RadarFrameSet,
    RainRadarOptions,
    RainRiskForecast,
    RainRiskHour,
)

_LOGGER = logging.getLogger(__name__)

_DATA_DMI_REQUEST_MANAGER = f"{DOMAIN}_dmi_request_manager"
_MODEL_CYCLE = timedelta(hours=3)
_MODEL_AVAILABILITY_DELAY = timedelta(hours=2, minutes=55)
_MAX_STALE_AGE = timedelta(hours=6)
_BACKOFF_DELAYS = (
    timedelta(minutes=15),
    timedelta(minutes=30),
    timedelta(hours=1),
    timedelta(hours=3),
)
_MAX_SHARED_CACHE_ENTRIES = 32
_MAX_REFRESH_JITTER_SECONDS = 300


@dataclass(slots=True)
class _DmiRequestError:
    """A bounded point-local query failure, separate from a provider outage."""

    error_type: str
    reason: str
    status_code: int | None
    retry_at: datetime
    consecutive_failures: int
    unavailable_logged: bool = False

    @property
    def message(self) -> str:
        """Return safe error details without the original URL or response body."""
        if self.status_code is None:
            return self.reason
        return f"{self.reason} (HTTP {self.status_code})"


@dataclass(frozen=True, slots=True)
class _DmiForecastCache:
    """Cached DMI point forecast shared by precipitation and risk sensors."""

    request_key: str
    payload: dict[str, Any] | None
    cache: CacheMetadata
    refresh_at: datetime
    coverage_status: CoverageStatus = CoverageStatus.OK
    request_error: _DmiRequestError | None = None


class _DmiRequestManager:
    """Share DMI cache, single-flight requests, and backoff across entries."""

    def __init__(self) -> None:
        """Initialize shared DMI request state."""
        self.lock = asyncio.Lock()
        self.inflight: dict[
            str,
            asyncio.Task[tuple[dict[str, Any] | None, CacheMetadata, CoverageStatus]],
        ] = {}
        self._cache: OrderedDict[str, _DmiForecastCache] = OrderedDict()
        self.backoff_until: datetime | None = None
        self.consecutive_failures = 0
        self.last_error: str | None = None
        self.last_error_type: str | None = None
        self.last_error_reason: str | None = None
        self.last_error_status_code: int | None = None
        self.last_success: datetime | None = None
        self.last_attempt: datetime | None = None
        self.last_request_duration_seconds: float | None = None
        self.request_count = 0
        self.cache_hits = 0
        self._unavailable_logged = False

    def cached(self, request_key: str) -> _DmiForecastCache | None:
        """Return and touch a shared cache entry."""
        cached = self._cache.get(request_key)
        if cached is not None:
            self._cache.move_to_end(request_key)
        return cached

    def store(self, cached: _DmiForecastCache) -> None:
        """Store a bounded shared cache entry."""
        self._cache[cached.request_key] = cached
        self._cache.move_to_end(cached.request_key)
        while len(self._cache) > _MAX_SHARED_CACHE_ENTRIES:
            self._cache.popitem(last=False)

    def is_backing_off(self, now: datetime) -> bool:
        """Return whether DMI requests are currently paused."""
        return self.backoff_until is not None and self.backoff_until > now

    def record_failure(
        self,
        err: RainRadarApiError,
        now: datetime,
        request_key: str,
        *,
        using_cached_data: bool = False,
    ) -> None:
        """Apply Retry-After or bounded exponential backoff."""
        first_failure = self.consecutive_failures == 0
        self.consecutive_failures += 1
        delay = _BACKOFF_DELAYS[
            min(self.consecutive_failures - 1, len(_BACKOFF_DELAYS) - 1)
        ]
        jitter_limit = min(60, max(1, round(delay.total_seconds() / 10)))
        delay += timedelta(
            seconds=_stable_jitter_seconds(
                f"{request_key}:{self.consecutive_failures}", jitter_limit
            )
        )
        candidate = now + delay
        retry_after = (
            err.retry_after if isinstance(err, RainRadarApiTemporaryError) else None
        )
        if retry_after is not None and math.isfinite(retry_after):
            # Malformed provider delays must not bypass the local backoff.
            with suppress(OverflowError):
                candidate = max(candidate, now + timedelta(seconds=retry_after))
        if self.backoff_until is None or candidate > self.backoff_until:
            self.backoff_until = candidate
        self.last_error_type = type(err).__name__
        self.last_error_reason = err.reason or (
            "rate_limited"
            if isinstance(err, RainRadarApiRateLimitedError)
            else "request_failed"
        )
        self.last_error_status_code = err.status_code
        self.last_error = self.last_error_reason
        if err.status_code is not None:
            self.last_error += f" (HTTP {err.status_code})"
        if first_failure and using_cached_data:
            _LOGGER.info(
                "Forecast refresh deferred (%s); using cached data, next attempt at %s",
                self.last_error,
                self.backoff_until,
            )

    def record_unavailable(self) -> None:
        """Warn once when an outage leaves no usable forecast, not on every tick."""
        if self._unavailable_logged:
            return
        self._unavailable_logged = True
        _LOGGER.warning(
            "Forecast data unavailable (%s); next attempt at %s",
            self.last_error or "cached forecast no longer covers the current time",
            self.backoff_until,
        )

    def record_success(self, now: datetime) -> None:
        """Reset backoff after a successful DMI response."""
        if self.consecutive_failures or self._unavailable_logged:
            _LOGGER.info("Forecast requests recovered")
        self.backoff_until = None
        self.consecutive_failures = 0
        self.last_error = None
        self.last_error_type = None
        self.last_error_reason = None
        self.last_error_status_code = None
        self.last_success = now
        self._unavailable_logged = False


@dataclass(frozen=True, slots=True)
class _DmiSample:
    """Mean precipitation over one explicitly bounded forecast interval."""

    time: datetime
    end_time: datetime
    precipitation_rate: float
    precipitation_amount: float
    precipitation_type: str | None


class DmiProvider:
    """DMI point forecast provider."""

    def __init__(
        self,
        client: RainRadarApiClient,
        *,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        """Initialize provider."""
        self.client = client
        self._now = now_fn or _utcnow
        self._coverage_status = CoverageStatus.UNKNOWN
        self._request_manager = _request_manager_for(client)
        self._last_request_key: str | None = None

    @property
    def provider_id(self) -> str:
        """Return provider identifier."""
        return PROVIDER_DMI

    @property
    def provider_name(self) -> str:
        """Return provider display name."""
        return "DMI"

    @property
    def attribution(self) -> str:
        """Return provider attribution."""
        return DMI_ATTRIBUTION

    @property
    def coverage_status(self) -> CoverageStatus:
        """Return latest known coverage status."""
        return self._coverage_status

    @property
    def next_refresh_at(self) -> datetime | None:
        """Return the next planned DMI model refresh."""
        cached = self._last_cache()
        return cached.refresh_at if cached is not None else None

    @property
    def backoff_until(self) -> datetime | None:
        """Return the applicable provider-wide or point-local retry deadline."""
        local_error = self._local_request_error()
        deadlines = [
            deadline
            for deadline in (
                self._request_manager.backoff_until,
                local_error.retry_at if local_error is not None else None,
            )
            if deadline is not None
        ]
        return max(deadlines, default=None)

    @property
    def last_error(self) -> str | None:
        """Return the most recent DMI request error."""
        if local_error := self._local_request_error():
            return local_error.message
        return self._request_manager.last_error

    @property
    def last_error_type(self) -> str | None:
        """Return the most recent DMI request error type."""
        if local_error := self._local_request_error():
            return local_error.error_type
        return self._request_manager.last_error_type

    @property
    def last_success(self) -> datetime | None:
        """Return the last successful DMI network fetch."""
        return self._request_manager.last_success

    @property
    def diagnostics(self) -> dict[str, Any]:
        """Return non-sensitive DMI request diagnostics."""
        now = self._now()
        cached = self._last_cache()
        local_error = cached.request_error if cached is not None else None
        fetched_at = cached.cache.fetched_at if cached is not None else None
        return {
            "backoff_until": _isoformat(self.backoff_until),
            "cache_age_seconds": round((now - fetched_at).total_seconds())
            if fetched_at is not None
            else None,
            "cache_is_stale": cached.refresh_at <= now if cached is not None else None,
            "consecutive_failures": local_error.consecutive_failures
            if local_error is not None
            else self._request_manager.consecutive_failures,
            "last_error": self.last_error_type,
            "last_error_reason": local_error.reason
            if local_error is not None
            else self._request_manager.last_error_reason,
            "status_code": local_error.status_code
            if local_error is not None
            else self._request_manager.last_error_status_code,
            "last_success": _isoformat(self._request_manager.last_success),
            "last_attempt": _isoformat(self._request_manager.last_attempt),
            "last_request_duration_seconds": self._request_manager.last_request_duration_seconds,
            "request_count": self._request_manager.request_count,
            "cache_hits": self._request_manager.cache_hits,
            "cache_usable": self._usable_cache(self._last_request_key, now) is not None,
            "next_refresh_at": _isoformat(self.next_refresh_at),
        }

    async def async_get_precipitation_forecast(
        self,
        location: Location,
        options: RainRadarOptions,
    ) -> PrecipitationForecast:
        """Fetch DMI point precipitation forecast data."""
        payload, cache = await self._async_get_forecast(location, options)
        if payload is None:
            return PrecipitationForecast(
                coverage_status=self._coverage_status,
                cache=cache,
                data_kind="model",
                window_complete=False,
            )

        samples = _parse_samples(payload)
        self._coverage_status = CoverageStatus.OK
        now = self._now()
        current = _current_precipitation(samples, now)
        rain_now = None if current is None else current >= options.rain_threshold
        rain_arrival = (
            0
            if rain_now is True
            else _arrival_minutes(samples, options.rain_threshold, now)
        )
        rain_soon = None
        if (
            rain_arrival is not None
            and rain_arrival <= options.rain_soon_window_minutes
        ):
            rain_soon = True
        elif _covers_period(
            samples,
            now,
            now + timedelta(minutes=options.rain_soon_window_minutes),
        ):
            rain_soon = False
        latest_time = max((sample.end_time for sample in samples), default=None)
        complete = _covers_period(
            samples, now, now + timedelta(minutes=options.rain_soon_window_minutes)
        )

        return PrecipitationForecast(
            samples=[
                PrecipitationSample(
                    time=sample.time,
                    precipitation_rate=sample.precipitation_rate,
                    interval_start=sample.time,
                    interval_end=sample.end_time,
                )
                for sample in samples
            ],
            current_precipitation=current,
            rain_now=rain_now,
            rain_soon=rain_soon,
            rain_arrival_minutes=rain_arrival,
            updated_at=cache.fetched_at,
            latest_time=latest_time,
            coverage_status=self._coverage_status,
            is_stale=_is_stale(cache, now),
            cache=cache,
            data_kind="model",
            resolution_minutes=_resolution_minutes(samples),
            window_complete=complete,
            reason="stale_data"
            if _is_stale(cache, now)
            else "incomplete_window"
            if not complete
            else None,
        )

    async def async_get_rain_risk(
        self,
        location: Location,
        options: RainRadarOptions,
    ) -> RainRiskForecast:
        """Fetch DMI threshold-based rain-risk forecast data."""
        payload, cache = await self._async_get_forecast(location, options)
        if payload is None:
            return RainRiskForecast(
                max_probability=None,
                cache=cache,
                data_kind="model",
                window_complete=False,
                coverage_status=self._coverage_status,
            )

        samples = _parse_samples(payload)
        self._coverage_status = CoverageStatus.OK
        now = self._now()
        hourly = _rain_risk_hours(
            samples,
            options.rain_risk_horizon_hours,
            options.rain_threshold,
            now,
        )
        max_probability = max((hour.probability for hour in hourly), default=None)
        if max_probability == 0 and not _covers_period(
            samples, now, now + timedelta(hours=options.rain_risk_horizon_hours)
        ):
            max_probability = None

        complete = _covers_period(
            samples, now, now + timedelta(hours=options.rain_risk_horizon_hours)
        )
        return RainRiskForecast(
            max_probability=max_probability,
            hourly=hourly,
            updated_at=cache.fetched_at,
            is_stale=_is_stale(cache, now),
            cache=cache,
            data_kind="model",
            resolution_minutes=_resolution_minutes(samples),
            window_complete=complete,
            reason="stale_data"
            if _is_stale(cache, now)
            else "incomplete_window"
            if not complete
            else None,
        )

    async def async_get_radar_frames(
        self,
        location: Location,
        options: RainRadarOptions,
    ) -> RadarFrameSet:
        """Return no radar frames for DMI forecast-only usage."""
        return RadarFrameSet(
            attribution=DMI_ATTRIBUTION,
            coverage_status=self._coverage_status,
        )

    async def _async_get_forecast(
        self,
        location: Location,
        options: RainRadarOptions,
    ) -> tuple[dict[str, Any] | None, CacheMetadata]:
        """Keep shared delivery alive through entry reload or caller cancellation."""
        request_key = _cache_key(location, options)
        self._last_request_key = request_key
        now = self._now()
        if cached := self._fresh_cache(request_key, now):
            return self._cached_result(cached, now)
        if self._request_manager.is_backing_off(now):
            return self._temporary_result(request_key, now)

        task = self._request_manager.inflight.get(request_key)
        if task is None:

            async def fetch() -> tuple[
                dict[str, Any] | None, CacheMetadata, CoverageStatus
            ]:
                try:
                    payload, cache = await self._async_fetch_forecast(location, options)
                    coverage = (
                        CoverageStatus.OK
                        if payload is not None
                        else self._coverage_status
                    )
                    return payload, cache, coverage
                finally:
                    self._request_manager.inflight.pop(request_key, None)

            if hass := getattr(self.client, "hass", None):
                task = hass.async_create_background_task(
                    fetch(), "Rain Radar shared DMI forecast", eager_start=False
                )
            else:
                task = asyncio.create_task(fetch())
            self._request_manager.inflight[request_key] = task
        payload, cache, self._coverage_status = await asyncio.shield(task)
        return payload, cache

    async def _async_fetch_forecast(
        self,
        location: Location,
        options: RainRadarOptions,
    ) -> tuple[dict[str, Any] | None, CacheMetadata]:
        """Fetch a shared DMI forecast payload."""
        request_key = _cache_key(location, options)
        self._last_request_key = request_key
        now = self._now()
        if cached := self._fresh_cache(request_key, now):
            return self._cached_result(cached, now)
        if self._request_manager.is_backing_off(now):
            return self._temporary_result(request_key, now)

        async with self._request_manager.lock:
            now = self._now()
            if cached := self._fresh_cache(request_key, now):
                return self._cached_result(cached, now)
            if self._request_manager.is_backing_off(now):
                return self._temporary_result(request_key, now)

            model_run, start, end = _query_window(now, options)
            # The query horizon and cache deadline must belong to the same run,
            # even if a response arrives after the next model boundary.
            refresh_at = _next_model_refresh(now, request_key)
            http_cache_key = _http_cache_key(
                request_key,
                model_run,
                start,
                end,
            )

            self._request_manager.request_count += 1
            self._request_manager.last_attempt = now
            try:
                payload, cache = await self.client.async_get_json(
                    http_cache_key,
                    DMI_FORECAST_URL,
                    params={
                        "coords": (
                            f"POINT({round(location.longitude, 6)} "
                            f"{round(location.latitude, 6)})"
                        ),
                        "crs": "crs84",
                        "parameter-name": DMI_FORECAST_PARAMETERS,
                        "datetime": _datetime_range(start, end),
                        "f": "GeoJSON",
                    },
                    auth_required=False,
                )
            except RainRadarApiError as err:
                now = self._request_finished_at()
                if _is_outside_coverage_error(err):
                    refresh_at = _next_model_refresh(now, request_key)
                    cache = CacheMetadata(fetched_at=now, expires_at=refresh_at)
                    self._request_manager.store(
                        _DmiForecastCache(
                            request_key=request_key,
                            payload=None,
                            cache=cache,
                            refresh_at=refresh_at,
                            coverage_status=CoverageStatus.OUTSIDE_COVERAGE,
                        )
                    )
                    self._coverage_status = CoverageStatus.OUTSIDE_COVERAGE
                    return None, cache
                if err.status_code in (400, 404):
                    cached = self._record_local_request_error(err, request_key, now)
                    return self._cached_result(cached, now)
                self._clear_local_request_error(request_key)
                self._request_manager.record_failure(
                    err,
                    now,
                    request_key,
                    using_cached_data=self._usable_cache(request_key, now) is not None,
                )
                return self._temporary_result(request_key, now)

            now = self._request_finished_at()
            if not isinstance(payload, dict) or not _has_usable_samples(
                _parse_samples(payload), now
            ):
                self._clear_local_request_error(request_key)
                self._request_manager.record_failure(
                    RainRadarApiTemporaryError(
                        "Forecast response has no usable precipitation data",
                        reason="invalid_response",
                        status_code=200,
                    ),
                    now,
                    request_key,
                    using_cached_data=self._usable_cache(request_key, now) is not None,
                )
                return self._temporary_result(request_key, now)

            effective_cache = CacheMetadata(
                fetched_at=now,
                expires_at=refresh_at,
                etag=cache.etag,
                last_modified=cache.last_modified,
                from_cache=cache.from_cache,
            )
            recovered_local_error = self._local_request_error() is not None
            self._request_manager.store(
                _DmiForecastCache(
                    request_key=request_key,
                    payload=payload,
                    cache=effective_cache,
                    refresh_at=refresh_at,
                )
            )
            if recovered_local_error:
                _LOGGER.info("Forecast point query recovered")
            self._request_manager.record_success(now)
            return payload, effective_cache

    def _request_finished_at(self) -> datetime:
        """Use response completion time for server retry delays and cache age."""
        now = self._now()
        started = self._request_manager.last_attempt
        if started is not None:
            self._request_manager.last_request_duration_seconds = round(
                max(0.0, (now - started).total_seconds()), 3
            )
        return now

    def _cached_result(
        self,
        cached: _DmiForecastCache,
        now: datetime,
    ) -> tuple[dict[str, Any] | None, CacheMetadata]:
        """Return cached coverage or still-usable forecast data without a new request."""
        if (
            cached.request_error is not None
            and self._usable_cache(cached.request_key, now) is None
        ):
            self._coverage_status = CoverageStatus.TEMPORARILY_UNAVAILABLE
            self._record_local_unavailable(cached.request_error)
            return None, CacheMetadata()
        if cached.payload is not None and not _has_usable_samples(
            _parse_samples(cached.payload), now
        ):
            self._coverage_status = CoverageStatus.TEMPORARILY_UNAVAILABLE
            self._request_manager.record_unavailable()
            return None, _cached_metadata(cached.cache)
        self._coverage_status = cached.coverage_status
        self._request_manager.cache_hits += 1
        return cached.payload, _cached_metadata(cached.cache)

    def _local_request_error(self) -> _DmiRequestError | None:
        """Return a query failure for this provider's current point only."""
        cached = self._last_cache()
        return cached.request_error if cached is not None else None

    def _clear_local_request_error(self, request_key: str) -> None:
        """Let a new provider-wide error replace an earlier query error."""
        cached = self._request_manager.cached(request_key)
        if cached is not None and cached.request_error is not None:
            self._request_manager.store(replace(cached, request_error=None))

    def _record_local_request_error(
        self,
        err: RainRadarApiError,
        request_key: str,
        now: datetime,
    ) -> _DmiForecastCache:
        """Cache a point query's failure without pausing unrelated locations."""
        cached = self._request_manager.cached(request_key)
        previous = cached.request_error if cached is not None else None
        failures = previous.consecutive_failures + 1 if previous is not None else 1
        delay = _BACKOFF_DELAYS[min(failures - 1, len(_BACKOFF_DELAYS) - 1)]
        retry_at = (
            now
            + delay
            + timedelta(seconds=_stable_jitter_seconds(f"{request_key}:{failures}", 60))
        )
        local_error = _DmiRequestError(
            error_type=type(err).__name__,
            reason=err.reason or "http_error",
            status_code=err.status_code,
            retry_at=retry_at,
            consecutive_failures=failures,
            unavailable_logged=previous.unavailable_logged if previous else False,
        )
        using_cached_data = self._usable_cache(request_key, now) is not None
        result = _DmiForecastCache(
            request_key=request_key,
            payload=cached.payload if cached is not None else None,
            cache=cached.cache if cached is not None else CacheMetadata(),
            refresh_at=cached.refresh_at if cached is not None else retry_at,
            coverage_status=CoverageStatus.OK
            if using_cached_data
            else CoverageStatus.TEMPORARILY_UNAVAILABLE,
            request_error=local_error,
        )
        self._request_manager.store(result)
        if previous is None and using_cached_data:
            _LOGGER.info(
                "Forecast point query deferred (%s); using cached data, next attempt at %s",
                local_error.message,
                local_error.retry_at,
            )
        return result

    @staticmethod
    def _record_local_unavailable(local_error: _DmiRequestError) -> None:
        """Warn once for one point, independently of other points' recovery."""
        if local_error.unavailable_logged:
            return
        local_error.unavailable_logged = True
        _LOGGER.warning(
            "Forecast point data unavailable (%s); next attempt at %s",
            local_error.message,
            local_error.retry_at,
        )

    def _last_cache(self) -> _DmiForecastCache | None:
        """Return the cache used by this provider without exposing its key."""
        if self._last_request_key is None:
            return None
        return self._request_manager.cached(self._last_request_key)

    def _fresh_cache(
        self,
        request_key: str,
        now: datetime,
    ) -> _DmiForecastCache | None:
        """Return shared data until the next expected complete model."""
        cached = self._request_manager.cached(request_key)
        if cached is not None and cached.request_error is not None:
            return cached if cached.request_error.retry_at > now else None
        if cached is None or cached.refresh_at <= now:
            return None
        return cached

    def _temporary_result(
        self,
        request_key: str,
        now: datetime,
    ) -> tuple[dict[str, Any] | None, CacheMetadata]:
        """Reuse bounded stale data during a temporary DMI backoff."""
        cached = self._usable_cache(request_key, now)
        if cached is not None:
            return self._cached_result(cached, now)
        self._coverage_status = CoverageStatus.TEMPORARILY_UNAVAILABLE
        if local_error := self._local_request_error():
            self._record_local_unavailable(local_error)
        else:
            self._request_manager.record_unavailable()
        return None, CacheMetadata()

    def _usable_cache(
        self,
        request_key: str | None,
        now: datetime,
    ) -> _DmiForecastCache | None:
        """Never serve exhausted or excessively old data as a current forecast."""
        if request_key is None:
            return None
        cached = self._request_manager.cached(request_key)
        if (
            cached is not None
            and cached.payload is not None
            and _cache_age(cached.cache, now) <= _MAX_STALE_AGE
            and _has_usable_samples(_parse_samples(cached.payload), now)
        ):
            return cached
        return None


def _cache_key(location: Location, options: RainRadarOptions) -> str:
    """Return a stable cache key for a DMI point forecast request."""
    return (
        "dmi_harmonie_dini_sf_"
        f"{round(location.longitude, 6)}_"
        f"{round(location.latitude, 6)}_"
        f"{_forecast_hours(options)}"
    )


def _forecast_hours(options: RainRadarOptions) -> int:
    """Return enough hourly samples for configured soon/risk horizons."""
    soon_hours = (options.rain_soon_window_minutes + 59) // 60
    return max(3, min(24, max(options.rain_risk_horizon_hours, soon_hours) + 1))


def _query_window(
    now: datetime,
    options: RainRadarOptions,
) -> tuple[datetime, datetime, datetime]:
    """Return a stable query window for the expected complete model."""
    model_run = _latest_expected_model_run(now)
    # Keep the baseline for the first hourly interval available at publication,
    # not earlier historical steps that cannot contribute to current forecasts.
    start = (model_run + _MODEL_AVAILABILITY_DELAY).replace(
        minute=0, second=0, microsecond=0
    )
    next_refresh = model_run + _MODEL_CYCLE + _MODEL_AVAILABILITY_DELAY
    end_anchor = next_refresh.replace(minute=0, second=0, microsecond=0)
    if end_anchor < next_refresh:
        end_anchor += timedelta(hours=1)
    end = end_anchor + timedelta(hours=_forecast_hours(options))
    return model_run, start, end


def _datetime_range(start: datetime, end: datetime) -> str:
    """Return the DMI EDR datetime range for a model-aware point query."""
    return f"{_format_dmi_datetime(start)}/{_format_dmi_datetime(end)}"


def _latest_expected_model_run(now: datetime) -> datetime:
    """Return the latest model run expected to be complete."""
    adjusted = now.astimezone(UTC) - _MODEL_AVAILABILITY_DELAY
    return adjusted.replace(
        hour=(adjusted.hour // 3) * 3,
        minute=0,
        second=0,
        microsecond=0,
    )


def _next_model_refresh(now: datetime, request_key: str) -> datetime:
    """Return the next expected model completion with stable jitter."""
    model_run = _latest_expected_model_run(now)
    refresh_at = model_run + _MODEL_CYCLE + _MODEL_AVAILABILITY_DELAY
    return refresh_at + timedelta(
        seconds=_stable_jitter_seconds(
            request_key,
            _MAX_REFRESH_JITTER_SECONDS,
        )
    )


def _http_cache_key(
    request_key: str,
    model_run: datetime,
    start: datetime,
    end: datetime,
) -> str:
    """Return a cache key representing the complete HTTP query variant."""
    return "_".join(
        (
            request_key,
            DMI_FORECAST_PARAMETERS,
            _format_dmi_datetime(model_run),
            _format_dmi_datetime(start),
            _format_dmi_datetime(end),
        )
    )


def _format_dmi_datetime(value: datetime) -> str:
    """Format UTC datetime for DMI EDR query parameters."""
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_samples(payload: dict[str, Any]) -> list[_DmiSample]:
    """Derive interval mean mm/h from accumulated total precipitation in mm.

    DMI documents total-precipitation as accumulated kg/m², equivalent to mm.
    Its rain-rate field has conflicting rate units and accumulation metadata,
    so it cannot be interpreted as an instantaneous intensity here.
    """
    features = payload.get("features")
    if not isinstance(features, list):
        return []

    raw_samples: list[tuple[datetime, float | None, str | None]] = []
    for feature in features:
        if not isinstance(feature, dict):
            continue
        properties = feature.get("properties")
        if not isinstance(properties, dict):
            continue
        time = _parse_datetime(properties.get("step") or properties.get("datetime"))
        if time is None:
            continue
        raw_samples.append(
            (
                time,
                _as_float(properties.get("total-precipitation")),
                _precipitation_type_label(properties.get("precipitation-type")),
            )
        )

    raw_samples.sort(key=lambda item: item[0])
    samples: list[_DmiSample] = []
    previous_time: datetime | None = None
    previous_total: float | None = None
    for time, grouped in groupby(raw_samples, key=lambda item: item[0]):
        boundaries = list(grouped)
        _, total_precipitation, precipitation_type = boundaries[0]
        # An ambiguous timestamp invalidates both adjoining intervals. Null
        # boundaries also break the chain rather than bridging missing steps.
        if (
            len(boundaries) != 1
            or total_precipitation is None
            or total_precipitation < 0
        ):
            total_precipitation = None
        if (
            previous_time is not None
            and previous_total is not None
            and total_precipitation is not None
            and total_precipitation >= previous_total
        ):
            interval_hours = (time - previous_time).total_seconds() / 3600
            if interval_hours > 0:
                amount = total_precipitation - previous_total
                rate = amount / interval_hours
                if math.isfinite(rate):
                    samples.append(
                        _DmiSample(
                            time=previous_time,
                            end_time=time,
                            precipitation_rate=rate,
                            precipitation_amount=amount,
                            precipitation_type=precipitation_type,
                        )
                    )
        previous_time = time
        previous_total = total_precipitation
    return samples


def _covers_period(samples: list[_DmiSample], start: datetime, end: datetime) -> bool:
    """Require contiguous known intervals before describing a period as dry."""
    covered_until = start
    for sample in samples:
        if sample.end_time <= covered_until:
            continue
        if sample.time > covered_until:
            return False
        covered_until = sample.end_time
        if covered_until >= end:
            return True
    return False


def _has_usable_samples(samples: list[_DmiSample], now: datetime) -> bool:
    """Require a real precipitation interval now or within the supported horizon."""
    return any(
        sample.end_time > now and sample.time < now + timedelta(hours=24)
        for sample in samples
    )


def _rain_risk_hours(
    samples: list[_DmiSample],
    horizon_hours: int,
    rain_threshold: float,
    now: datetime,
) -> list[RainRiskHour]:
    """Include each interval overlapping now through the requested horizon.

    Interval timestamps are their starts, like the other forecast providers.
    Partial current/final intervals count too, so a 12-hour window may overlap
    13 hourly intervals when the update occurs between hour boundaries.
    """
    end = now + timedelta(hours=horizon_hours)
    hourly: list[RainRiskHour] = []
    for sample in samples:
        if sample.end_time <= now:
            continue
        if sample.time >= end:
            break
        hourly.append(
            RainRiskHour(
                time=sample.time,
                interval_start=sample.time,
                interval_end=sample.end_time,
                probability=_threshold_probability(sample, rain_threshold),
                precipitation_amount=sample.precipitation_amount,
                symbol_code=sample.precipitation_type,
            )
        )
    return hourly


def _resolution_minutes(samples: list[_DmiSample]) -> int | None:
    """Report the coarsest returned interval without overstating precision."""
    return max(
        (
            round((sample.end_time - sample.time).total_seconds() / 60)
            for sample in samples
        ),
        default=None,
    )


def _threshold_probability(sample: _DmiSample, rain_threshold: float) -> int:
    """Compare like units: interval mean mm/h against the configured mm/h."""
    return 100 if sample.precipitation_rate >= rain_threshold else 0


def _current_precipitation(
    samples: list[_DmiSample],
    now: datetime,
) -> float | None:
    """Return the mean forecast intensity only for the interval containing now."""
    return next(
        (
            sample.precipitation_rate
            for sample in samples
            if sample.time <= now < sample.end_time
        ),
        None,
    )


def _arrival_minutes(
    samples: list[_DmiSample],
    rain_threshold: float,
    now: datetime,
) -> int | None:
    for sample in samples:
        if sample.time <= now:
            continue
        if sample.precipitation_rate < rain_threshold:
            continue
        return max(0, round((sample.time - now).total_seconds() / 60))
    return None


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    parsed = dt_util.parse_datetime(value)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        numeric = float(value)
    except TypeError, ValueError:
        return None
    return numeric if math.isfinite(numeric) and numeric >= 0 else None


def _precipitation_type_label(value: Any) -> str | None:
    numeric = _as_float(value)
    if numeric is None:
        return None
    match round(numeric):
        case 0:
            return "drizzle"
        case 1:
            return "rain"
        case 2:
            return "sleet"
        case 3:
            return "snow"
        case 4:
            return "freezing_drizzle"
        case 5:
            return "freezing_rain"
        case 6:
            return "graupel"
        case 7:
            return "hail"
        case _:
            return str(round(numeric))


def _is_outside_coverage_error(err: RainRadarApiError) -> bool:
    # A missing endpoint/model or invalid query is not evidence of map coverage.
    message = str(err).lower()
    return (
        err.status_code in (400, 404) or "http 400" in message or "http 404" in message
    ) and ("outside coverage" in message or "outside the coverage" in message)


def _is_stale(cache: CacheMetadata, now: datetime) -> bool:
    if cache.expires_at is not None:
        return cache.expires_at <= now
    return cache.from_cache


def _cache_age(cache: CacheMetadata, now: datetime) -> timedelta:
    """Return cache age, treating unknown fetch times as unusable."""
    if cache.fetched_at is None:
        return timedelta.max
    return max(timedelta(), now - cache.fetched_at)


def _cached_metadata(cache: CacheMetadata) -> CacheMetadata:
    """Mark provider data as served from the shared local cache."""
    return CacheMetadata(
        fetched_at=cache.fetched_at,
        expires_at=cache.expires_at,
        etag=cache.etag,
        last_modified=cache.last_modified,
        from_cache=True,
    )


def _request_manager_for(client: RainRadarApiClient) -> _DmiRequestManager:
    """Return a Home Assistant-wide DMI manager when runtime data is available."""
    hass = getattr(client, "hass", None)
    if hass is None:
        return _DmiRequestManager()
    manager = hass.data.get(_DATA_DMI_REQUEST_MANAGER)
    if isinstance(manager, _DmiRequestManager):
        return manager
    manager = _DmiRequestManager()
    hass.data[_DATA_DMI_REQUEST_MANAGER] = manager
    return manager


def _stable_jitter_seconds(value: str, maximum: int) -> int:
    """Return deterministic positive jitter to spread API requests."""
    if maximum <= 0:
        return 0
    digest = hashlib.sha256(value.encode()).digest()
    return int.from_bytes(digest[:4]) % (maximum + 1)


def _utcnow() -> datetime:
    """Return the current UTC time."""
    return datetime.now(UTC)


def _isoformat(value: datetime | None) -> str | None:
    """Return an ISO timestamp for diagnostics."""
    return value.isoformat() if value is not None else None
