"""DMI forecast provider implementation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.util import dt as dt_util

from ..api import (
    RainRadarApiClient,
    RainRadarApiError,
    RainRadarApiRateLimitedError,
)
from ..const import (
    DMI_ATTRIBUTION,
    DMI_FORECAST_PARAMETERS,
    DMI_FORECAST_URL,
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

_FORECAST_CACHE_TTL = timedelta(minutes=15)
_RAIN_RATE_TO_MM_PER_HOUR = 3600


@dataclass(frozen=True, slots=True)
class _DmiForecastCache:
    """Cached DMI point forecast shared by precipitation and risk sensors."""

    cache_key: str
    payload: dict[str, Any]
    cache: CacheMetadata


@dataclass(frozen=True, slots=True)
class _DmiSample:
    """Normalized DMI forecast sample."""

    time: datetime
    precipitation_rate: float | None
    precipitation_amount: float | None
    precipitation_type: str | None


class DmiProvider:
    """DMI point forecast provider."""

    def __init__(self, client: RainRadarApiClient) -> None:
        """Initialize provider."""
        self.client = client
        self._coverage_status = CoverageStatus.UNKNOWN
        self._forecast_lock = asyncio.Lock()
        self._forecast_cache: _DmiForecastCache | None = None

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
        current = _current_precipitation(samples)
        rain_now = (
            current is not None and current >= options.rain_threshold
            if samples
            else None
        )
        rain_arrival = (
            0 if rain_now is True else _arrival_minutes(samples, options.rain_threshold)
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
            is_stale=_is_stale(cache),
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
        )
        max_probability = max((hour.probability for hour in hourly), default=None)

        return RainRiskForecast(
            max_probability=max_probability,
            hourly=hourly,
            updated_at=cache.fetched_at,
            is_stale=_is_stale(cache),
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
        cache_key = _cache_key(location, options)
        if cached := self._fresh_cache(cache_key):
            return cached.payload, cached.cache

        async with self._forecast_lock:
            if cached := self._fresh_cache(cache_key):
                return cached.payload, cached.cache

            try:
                payload, cache = await self.client.async_get_json(
                    cache_key,
                    DMI_FORECAST_URL,
                    params={
                        "coords": (
                            f"POINT({round(location.longitude, 6)} "
                            f"{round(location.latitude, 6)})"
                        ),
                        "crs": "crs84",
                        "parameter-name": DMI_FORECAST_PARAMETERS,
                        "datetime": _datetime_range(options),
                        "f": "GeoJSON",
                    },
                )
            except RainRadarApiError as err:
                if _is_outside_coverage_error(err):
                    self._coverage_status = CoverageStatus.OUTSIDE_COVERAGE
                    return None, CacheMetadata()
                if isinstance(err, RainRadarApiRateLimitedError) and (
                    stale := self._stale_cache(cache_key)
                ):
                    return stale.payload, stale.cache
                raise

            if not isinstance(payload, dict):
                self._coverage_status = CoverageStatus.UNKNOWN
                return None, cache

            self._forecast_cache = _DmiForecastCache(cache_key, payload, cache)
            return payload, cache

    def _fresh_cache(self, cache_key: str) -> _DmiForecastCache | None:
        """Return a usable in-provider forecast cache entry."""
        cached = self._forecast_cache
        if cached is None or cached.cache_key != cache_key:
            return None
        if _cache_is_fresh(cached.cache):
            return cached
        return None

    def _stale_cache(self, cache_key: str) -> _DmiForecastCache | None:
        """Return stale cache when DMI is temporarily unavailable."""
        cached = self._forecast_cache
        if cached is None or cached.cache_key != cache_key:
            return None
        return cached


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


def _datetime_range(options: RainRadarOptions) -> str:
    """Return the DMI EDR datetime range for the small point query."""
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    start = now - timedelta(hours=1)
    end = now + timedelta(hours=_forecast_hours(options))
    return f"{_format_dmi_datetime(start)}/{_format_dmi_datetime(end)}"


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
) -> list[RainRiskHour]:
    now = datetime.now(UTC)
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


def _current_precipitation(samples: list[_DmiSample]) -> float | None:
    if not samples:
        return None
    now = datetime.now(UTC)
    past_or_current = [sample for sample in samples if sample.time <= now]
    current = past_or_current[-1] if past_or_current else samples[0]
    return current.precipitation_rate


def _arrival_minutes(
    samples: list[_DmiSample],
    rain_threshold: float,
) -> int | None:
    now = datetime.now(UTC)
    for sample in samples:
        if (
            sample.precipitation_rate is None
            or sample.precipitation_rate < rain_threshold
        ):
            continue
        if sample.time <= now:
            return 0
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


def _cache_is_fresh(cache: CacheMetadata) -> bool:
    now = datetime.now(UTC)
    if cache.expires_at is not None:
        return cache.expires_at > now
    if cache.fetched_at is not None:
        return now - cache.fetched_at < _FORECAST_CACHE_TTL
    return False


def _is_stale(cache: CacheMetadata) -> bool:
    now = datetime.now(UTC)
    if cache.expires_at is not None:
        return cache.expires_at < now
    if cache.fetched_at is not None:
        return now - cache.fetched_at >= _FORECAST_CACHE_TTL
    return False
