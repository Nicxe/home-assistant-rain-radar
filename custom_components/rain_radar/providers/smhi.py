"""SMHI forecast provider implementation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.util import dt as dt_util

from ..api import RainRadarApiClient, RainRadarApiError
from ..const import (
    MAX_RAIN_RISK_HORIZON_HOURS,
    PROVIDER_SMHI,
    SMHI_ATTRIBUTION,
    SMHI_FORECAST_PARAMETERS,
    SMHI_FORECAST_URL_TEMPLATE,
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


@dataclass(frozen=True, slots=True)
class _SmhiForecastCache:
    """Cached SMHI point forecast shared by precipitation and risk sensors."""

    cache_key: str
    payload: dict[str, Any]
    cache: CacheMetadata


@dataclass(frozen=True, slots=True)
class _SmhiSample:
    """Normalized SMHI precipitation interval sample."""

    time: datetime
    interval_start: datetime | None
    precipitation_rate: float | None
    probability: int
    symbol_code: str | None


class SmhiProvider:
    """SMHI point forecast provider."""

    def __init__(self, client: RainRadarApiClient) -> None:
        """Initialize provider."""
        self.client = client
        self._coverage_status = CoverageStatus.UNKNOWN
        self._forecast_lock = asyncio.Lock()
        self._forecast_cache: _SmhiForecastCache | None = None

    @property
    def provider_id(self) -> str:
        """Return provider identifier."""
        return PROVIDER_SMHI

    @property
    def provider_name(self) -> str:
        """Return provider display name."""
        return "SMHI"

    @property
    def attribution(self) -> str:
        """Return provider attribution."""
        return SMHI_ATTRIBUTION

    @property
    def coverage_status(self) -> CoverageStatus:
        """Return latest known coverage status."""
        return self._coverage_status

    async def async_get_precipitation_forecast(
        self,
        location: Location,
        options: RainRadarOptions,
    ) -> PrecipitationForecast:
        """Fetch SMHI point precipitation forecast data."""
        payload, cache = await self._async_get_forecast(location, options)
        if payload is None:
            return PrecipitationForecast(
                coverage_status=self._coverage_status,
                cache=cache,
            )

        samples = _parse_samples(payload)
        self._coverage_status = CoverageStatus.OK
        updated_at = _parse_datetime(payload.get("createdTime")) or _parse_datetime(
            payload.get("referenceTime")
        )
        current = _current_precipitation(samples)
        rain_now = (
            current is not None and current >= options.rain_threshold
            if samples
            else None
        )
        rain_arrival = _arrival_minutes(samples, options.rain_threshold)
        rain_soon = (
            rain_arrival is not None
            and rain_arrival <= options.rain_soon_window_minutes
        )
        latest_time = max((sample.time for sample in samples), default=updated_at)

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
            updated_at=updated_at,
            latest_time=latest_time,
            coverage_status=self._coverage_status,
            is_stale=_is_stale(cache.expires_at),
            cache=cache,
        )

    async def async_get_rain_risk(
        self,
        location: Location,
        options: RainRadarOptions,
    ) -> RainRiskForecast:
        """Fetch SMHI precipitation probability data."""
        payload, cache = await self._async_get_forecast(location, options)
        if payload is None:
            return RainRiskForecast(max_probability=None, cache=cache)

        samples = _parse_samples(payload)
        self._coverage_status = CoverageStatus.OK
        updated_at = _parse_datetime(payload.get("createdTime")) or _parse_datetime(
            payload.get("referenceTime")
        )
        hourly = _rain_risk_hours(samples, options.rain_risk_horizon_hours)
        max_probability = max((hour.probability for hour in hourly), default=None)

        return RainRiskForecast(
            max_probability=max_probability,
            hourly=hourly,
            updated_at=updated_at,
            is_stale=_is_stale(cache.expires_at),
            cache=cache,
        )

    async def async_get_radar_frames(
        self,
        location: Location,
        options: RainRadarOptions,
    ) -> RadarFrameSet:
        """Return no radar frames for SMHI forecast-only usage."""
        return RadarFrameSet(
            attribution=SMHI_ATTRIBUTION,
            coverage_status=self._coverage_status,
        )

    async def _async_get_forecast(
        self,
        location: Location,
        options: RainRadarOptions,
    ) -> tuple[dict[str, Any] | None, CacheMetadata]:
        """Fetch a shared SMHI forecast payload."""
        cache_key = _cache_key(location, options)
        if cached := self._fresh_cache(cache_key):
            return cached.payload, cached.cache

        async with self._forecast_lock:
            if cached := self._fresh_cache(cache_key):
                return cached.payload, cached.cache

            try:
                payload, cache = await self.client.async_get_json(
                    cache_key,
                    SMHI_FORECAST_URL_TEMPLATE.format(
                        longitude=round(location.longitude, 6),
                        latitude=round(location.latitude, 6),
                    ),
                    params={
                        "timeseries": _timeseries_count(options),
                        "parameters": SMHI_FORECAST_PARAMETERS,
                    },
                )
            except RainRadarApiError as err:
                if _is_outside_coverage_error(err):
                    self._coverage_status = CoverageStatus.OUTSIDE_COVERAGE
                    return None, CacheMetadata()
                raise

            if not isinstance(payload, dict):
                self._coverage_status = CoverageStatus.UNKNOWN
                return None, cache

            self._forecast_cache = _SmhiForecastCache(cache_key, payload, cache)
            return payload, cache

    def _fresh_cache(self, cache_key: str) -> _SmhiForecastCache | None:
        """Return a usable in-provider forecast cache entry."""
        cached = self._forecast_cache
        if cached is None or cached.cache_key != cache_key:
            return None
        if _cache_is_fresh(cached.cache):
            return cached
        return None


def _cache_key(location: Location, options: RainRadarOptions) -> str:
    """Return a stable cache key for a SMHI point forecast request."""
    return (
        "smhi_snow1g_"
        f"{round(location.longitude, 6)}_"
        f"{round(location.latitude, 6)}_"
        f"{_timeseries_count(options)}"
    )


def _timeseries_count(options: RainRadarOptions) -> int:
    """Return enough hourly samples for configured soon/risk horizons."""
    soon_hours = (options.rain_soon_window_minutes + 59) // 60
    requested_hours = max(options.rain_risk_horizon_hours + 1, soon_hours + 1)
    return max(3, min(MAX_RAIN_RISK_HORIZON_HOURS + 1, requested_hours))


def _parse_samples(payload: dict[str, Any]) -> list[_SmhiSample]:
    timeseries = payload.get("timeSeries")
    if not isinstance(timeseries, list):
        return []

    samples: list[_SmhiSample] = []
    for item in timeseries:
        if not isinstance(item, dict):
            continue
        time = _parse_datetime(item.get("time"))
        if time is None:
            continue
        data = item.get("data")
        if not isinstance(data, dict):
            data = {}
        samples.append(
            _SmhiSample(
                time=time,
                interval_start=_parse_datetime(item.get("intervalParametersStartTime")),
                precipitation_rate=_as_float(data.get("precipitation_amount_mean")),
                probability=_clamp_probability(
                    data.get("probability_of_precipitation")
                ),
                symbol_code=_symbol_code(data.get("symbol_code")),
            )
        )
    samples.sort(key=lambda sample: sample.time)
    return samples


def _rain_risk_hours(
    samples: list[_SmhiSample],
    horizon_hours: int,
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
                probability=sample.probability,
                precipitation_amount=sample.precipitation_rate,
                symbol_code=sample.symbol_code,
            )
        )
        if len(hourly) >= horizon_hours:
            break
    return hourly


def _current_precipitation(samples: list[_SmhiSample]) -> float | None:
    if not samples:
        return None
    now = datetime.now(UTC)
    for sample in samples:
        if _sample_contains_time(sample, now):
            return sample.precipitation_rate
    past_or_current = [sample for sample in samples if sample.time <= now]
    current = past_or_current[-1] if past_or_current else samples[0]
    return current.precipitation_rate


def _arrival_minutes(
    samples: list[_SmhiSample],
    rain_threshold: float,
) -> int | None:
    now = datetime.now(UTC)
    for sample in samples:
        if (
            sample.precipitation_rate is None
            or sample.precipitation_rate < rain_threshold
        ):
            continue
        if _sample_contains_time(sample, now):
            return 0
        arrival_time = (
            sample.interval_start if sample.interval_start is not None else sample.time
        )
        if arrival_time < now:
            continue
        return max(0, round((arrival_time - now).total_seconds() / 60))
    return None


def _sample_contains_time(sample: _SmhiSample, value: datetime) -> bool:
    return (
        sample.interval_start is not None
        and sample.interval_start <= value <= sample.time
    )


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
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp_probability(value: Any) -> int:
    numeric = _as_float(value)
    if numeric is None:
        return 0
    return round(max(0.0, min(100.0, numeric)))


def _symbol_code(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _is_outside_coverage_error(err: RainRadarApiError) -> bool:
    message = str(err)
    return "HTTP 400" in message or "HTTP 404" in message


def _cache_is_fresh(cache: CacheMetadata) -> bool:
    now = datetime.now(UTC)
    if cache.expires_at is not None:
        return cache.expires_at > now
    if cache.fetched_at is not None:
        return now - cache.fetched_at < timedelta(minutes=1)
    return False


def _is_stale(expires_at: datetime | None) -> bool:
    return expires_at is not None and expires_at < datetime.now(UTC)
