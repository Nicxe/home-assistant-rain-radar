"""DMI forecast provider implementation."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import logging
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
_MAX_CURRENT_SAMPLE_AGE = timedelta(minutes=90)
_BACKOFF_DELAYS = (
    timedelta(minutes=15),
    timedelta(minutes=30),
    timedelta(hours=1),
    timedelta(hours=3),
)
_MAX_SHARED_CACHE_ENTRIES = 32
_MAX_REFRESH_JITTER_SECONDS = 300
_RAIN_RATE_TO_MM_PER_HOUR = 3600


@dataclass(frozen=True, slots=True)
class _DmiForecastCache:
    """Cached DMI point forecast shared by precipitation and risk sensors."""

    request_key: str
    payload: dict[str, Any]
    cache: CacheMetadata
    refresh_at: datetime


class _DmiRequestManager:
    """Share DMI cache, single-flight requests, and backoff across entries."""

    def __init__(self) -> None:
        """Initialize shared DMI request state."""
        self.lock = asyncio.Lock()
        self._cache: OrderedDict[str, _DmiForecastCache] = OrderedDict()
        self.backoff_until: datetime | None = None
        self.consecutive_failures = 0
        self.last_error: str | None = None
        self.last_error_type: str | None = None
        self.last_success: datetime | None = None

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
        err: RainRadarApiTemporaryError,
        now: datetime,
        request_key: str,
    ) -> None:
        """Apply Retry-After or bounded exponential backoff."""
        first_failure = self.consecutive_failures == 0
        self.consecutive_failures += 1
        retry_after = (
            err.retry_after if isinstance(err, RainRadarApiRateLimitedError) else None
        )
        if retry_after is not None:
            delay = timedelta(seconds=max(0.0, retry_after))
        else:
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
        if self.backoff_until is None or candidate > self.backoff_until:
            self.backoff_until = candidate
        self.last_error = str(err)
        self.last_error_type = type(err).__name__
        if first_failure:
            _LOGGER.warning(
                "DMI forecast request failed; backing off until %s",
                self.backoff_until,
            )

    def record_success(self, now: datetime) -> None:
        """Reset backoff after a successful DMI response."""
        if self.consecutive_failures:
            _LOGGER.info("DMI forecast requests recovered")
        self.backoff_until = None
        self.consecutive_failures = 0
        self.last_error = None
        self.last_error_type = None
        self.last_success = now


@dataclass(frozen=True, slots=True)
class _DmiSample:
    """Normalized DMI forecast sample."""

    time: datetime
    precipitation_rate: float | None
    precipitation_amount: float | None
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
        """Return the active shared DMI backoff deadline."""
        return self._request_manager.backoff_until

    @property
    def last_error(self) -> str | None:
        """Return the most recent DMI request error."""
        return self._request_manager.last_error

    @property
    def last_error_type(self) -> str | None:
        """Return the most recent DMI request error type."""
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
        fetched_at = cached.cache.fetched_at if cached is not None else None
        return {
            "backoff_until": _isoformat(self.backoff_until),
            "cache_age_seconds": round((now - fetched_at).total_seconds())
            if fetched_at is not None
            else None,
            "cache_is_stale": cached.refresh_at <= now if cached is not None else None,
            "consecutive_failures": self._request_manager.consecutive_failures,
            "last_error": self.last_error_type,
            "last_success": _isoformat(self._request_manager.last_success),
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
        rain_soon = (
            rain_arrival is not None
            and rain_arrival <= options.rain_soon_window_minutes
        )
        latest_time = max((sample.time for sample in samples), default=None)

        return PrecipitationForecast(
            samples=[
                PrecipitationSample(
                    time=sample.time,
                    precipitation_rate=sample.precipitation_rate,
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
        )

    async def async_get_rain_risk(
        self,
        location: Location,
        options: RainRadarOptions,
    ) -> RainRiskForecast:
        """Fetch DMI threshold-based rain-risk forecast data."""
        payload, cache = await self._async_get_forecast(location, options)
        if payload is None:
            return RainRiskForecast(max_probability=None, cache=cache)

        samples = _parse_samples(payload)
        self._coverage_status = CoverageStatus.OK
        hourly = _rain_risk_hours(
            samples,
            options.rain_risk_horizon_hours,
            options.rain_threshold,
            self._now(),
        )
        max_probability = max((hour.probability for hour in hourly), default=None)

        return RainRiskForecast(
            max_probability=max_probability,
            hourly=hourly,
            updated_at=cache.fetched_at,
            is_stale=_is_stale(cache, self._now()),
            cache=cache,
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
        """Fetch a shared DMI forecast payload."""
        request_key = _cache_key(location, options)
        self._last_request_key = request_key
        now = self._now()
        if cached := self._fresh_cache(request_key, now):
            return cached.payload, _cached_metadata(cached.cache)
        if self._request_manager.is_backing_off(now):
            return self._temporary_result(request_key, now)

        async with self._request_manager.lock:
            now = self._now()
            if cached := self._fresh_cache(request_key, now):
                return cached.payload, _cached_metadata(cached.cache)
            if self._request_manager.is_backing_off(now):
                return self._temporary_result(request_key, now)

            model_run, start, end = _query_window(now, options)
            http_cache_key = _http_cache_key(
                request_key,
                model_run,
                start,
                end,
            )

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
                if _is_outside_coverage_error(err):
                    self._coverage_status = CoverageStatus.OUTSIDE_COVERAGE
                    return None, CacheMetadata()
                if isinstance(err, RainRadarApiTemporaryError):
                    self._request_manager.record_failure(err, now, request_key)
                    return self._temporary_result(request_key, now)
                raise

            if not isinstance(payload, dict):
                self._request_manager.record_success(now)
                self._coverage_status = CoverageStatus.UNKNOWN
                return None, cache

            refresh_at = _next_model_refresh(now, request_key)
            effective_cache = CacheMetadata(
                fetched_at=now,
                expires_at=refresh_at,
                etag=cache.etag,
                last_modified=cache.last_modified,
                from_cache=cache.from_cache,
            )
            self._request_manager.store(
                _DmiForecastCache(
                    request_key=request_key,
                    payload=payload,
                    cache=effective_cache,
                    refresh_at=refresh_at,
                )
            )
            self._request_manager.record_success(now)
            return payload, effective_cache

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
        if cached is None or cached.refresh_at <= now:
            return None
        return cached

    def _temporary_result(
        self,
        request_key: str,
        now: datetime,
    ) -> tuple[dict[str, Any] | None, CacheMetadata]:
        """Reuse bounded stale data during a temporary DMI backoff."""
        cached = self._request_manager.cached(request_key)
        if cached is not None and _cache_age(cached.cache, now) <= _MAX_STALE_AGE:
            return cached.payload, _cached_metadata(cached.cache)
        self._coverage_status = CoverageStatus.TEMPORARILY_UNAVAILABLE
        return None, CacheMetadata()


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
    next_refresh = model_run + _MODEL_CYCLE + _MODEL_AVAILABILITY_DELAY
    end_anchor = next_refresh.replace(minute=0, second=0, microsecond=0)
    if end_anchor < next_refresh:
        end_anchor += timedelta(hours=1)
    end = end_anchor + timedelta(hours=_forecast_hours(options))
    return model_run, model_run, end


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
            _format_dmi_datetime(model_run),
            _format_dmi_datetime(start),
            _format_dmi_datetime(end),
        )
    )


def _format_dmi_datetime(value: datetime) -> str:
    """Format UTC datetime for DMI EDR query parameters."""
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_samples(payload: dict[str, Any]) -> list[_DmiSample]:
    features = payload.get("features")
    if not isinstance(features, list):
        return []

    raw_samples: list[tuple[datetime, float | None, float | None, str | None]] = []
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
                _rain_rate_mm_per_hour(properties.get("rain-precipitation-rate")),
                _as_float(properties.get("total-precipitation")),
                _precipitation_type_label(properties.get("precipitation-type")),
            )
        )

    raw_samples.sort(key=lambda item: item[0])
    samples: list[_DmiSample] = []
    previous_total: float | None = None
    for time, rain_rate, total_precipitation, precipitation_type in raw_samples:
        precipitation_amount = _precipitation_amount(
            total_precipitation,
            previous_total,
            rain_rate,
        )
        if total_precipitation is not None:
            previous_total = total_precipitation
        samples.append(
            _DmiSample(
                time=time,
                precipitation_rate=rain_rate,
                precipitation_amount=precipitation_amount,
                precipitation_type=precipitation_type,
            )
        )
    return samples


def _precipitation_amount(
    total_precipitation: float | None,
    previous_total: float | None,
    rain_rate: float | None,
) -> float | None:
    if total_precipitation is not None and previous_total is not None:
        return max(0.0, total_precipitation - previous_total)
    return rain_rate


def _rain_risk_hours(
    samples: list[_DmiSample],
    horizon_hours: int,
    rain_threshold: float,
    now: datetime,
) -> list[RainRiskHour]:
    end = now + timedelta(hours=horizon_hours)
    hourly: list[RainRiskHour] = []
    for sample in samples:
        if sample.time <= now:
            continue
        if sample.time > end:
            break
        hourly.append(
            RainRiskHour(
                time=sample.time,
                probability=_threshold_probability(sample, rain_threshold),
                precipitation_amount=sample.precipitation_amount,
                symbol_code=sample.precipitation_type,
            )
        )
        if len(hourly) >= horizon_hours:
            break
    return hourly


def _threshold_probability(sample: _DmiSample, rain_threshold: float) -> int:
    values = [
        value
        for value in (sample.precipitation_rate, sample.precipitation_amount)
        if value is not None
    ]
    if not values:
        return 0
    return 100 if max(values) >= rain_threshold else 0


def _current_precipitation(
    samples: list[_DmiSample],
    now: datetime,
) -> float | None:
    if not samples:
        return None
    past_or_current = [sample for sample in samples if sample.time <= now]
    if past_or_current:
        current = past_or_current[-1]
        if now - current.time > _MAX_CURRENT_SAMPLE_AGE:
            return None
    else:
        current = samples[0]
        if current.time - now > _MAX_CURRENT_SAMPLE_AGE:
            return None
    return current.precipitation_rate


def _arrival_minutes(
    samples: list[_DmiSample],
    rain_threshold: float,
    now: datetime,
) -> int | None:
    for sample in samples:
        if sample.time <= now:
            continue
        if (
            sample.precipitation_rate is None
            or sample.precipitation_rate < rain_threshold
        ):
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


def _rain_rate_mm_per_hour(value: Any) -> float | None:
    numeric = _as_float(value)
    if numeric is None:
        return None
    return numeric * _RAIN_RATE_TO_MM_PER_HOUR


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
    message = str(err)
    return "HTTP 400" in message or "HTTP 404" in message


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
