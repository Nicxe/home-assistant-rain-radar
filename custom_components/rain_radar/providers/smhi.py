"""SMHI forecast provider implementation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import math
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
from .quality import precipitation_forecast, rain_risk_forecast

# SNOW1gv1 parameter.json declares missingValue=9999 for every requested field.
# https://opendata.smhi.se/metfcst/snow1gv1/examples
_MISSING_VALUE = 9999


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
    probability: int | None
    symbol_code: str | None


class SmhiProvider:
    """SMHI point forecast provider."""

    def __init__(self, client: RainRadarApiClient) -> None:
        """Initialize provider."""
        self.client = client
        self._coverage_status = CoverageStatus.UNKNOWN
        self._forecast_lock = asyncio.Lock()
        self._forecast_cache: _SmhiForecastCache | None = None
        self._forecast_error: tuple[str, datetime, RainRadarApiError] | None = None

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
        return precipitation_forecast(
            [
                PrecipitationSample(
                    time=sample.time,
                    precipitation_rate=sample.precipitation_rate,
                    interval_start=sample.interval_start,
                    interval_end=sample.time,
                )
                for sample in samples
            ],
            options,
            cache,
            updated_at,
            self._coverage_status,
            data_kind="model",
            resolution_minutes=60,
            maximum_age=timedelta(hours=12),
            source_reference=_parse_datetime(payload.get("referenceTime")),
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
        return rain_risk_forecast(
            hourly,
            options.rain_risk_horizon_hours,
            cache,
            updated_at,
            latest_time=max((sample.time for sample in samples), default=None),
            source_reference=_parse_datetime(payload.get("referenceTime")),
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

            if self._forecast_error is not None:
                failed_key, retry_at, error = self._forecast_error
                if failed_key == cache_key and datetime.now(UTC) < retry_at:
                    if _is_outside_coverage_error(error):
                        return None, CacheMetadata()
                    raise error
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
                self._forecast_error = (
                    cache_key,
                    err.next_retry or datetime.now(UTC) + timedelta(minutes=1),
                    err,
                )
                if _is_outside_coverage_error(err):
                    self._coverage_status = CoverageStatus.OUTSIDE_COVERAGE
                    return None, CacheMetadata()
                self._coverage_status = CoverageStatus.TEMPORARILY_UNAVAILABLE
                raise

            self._forecast_error = None
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
        if sample.interval_start is None or sample.interval_start >= end:
            continue
        hourly.append(
            RainRiskHour(
                time=sample.time,
                interval_start=sample.interval_start,
                interval_end=sample.time,
                probability=sample.probability,
                precipitation_amount=sample.precipitation_rate,
                symbol_code=sample.symbol_code,
            )
        )
    return hourly


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
        numeric = float(value)
        return (
            numeric
            if math.isfinite(numeric) and numeric >= 0 and numeric != _MISSING_VALUE
            else None
        )
    except TypeError, ValueError:
        return None


def _clamp_probability(value: Any) -> int | None:
    numeric = _as_float(value)
    if numeric is None:
        return None
    return round(max(0.0, min(100.0, numeric)))


def _symbol_code(value: Any) -> str | None:
    if _as_float(value) is None:
        return None
    return str(value)


def _is_outside_coverage_error(err: RainRadarApiError) -> bool:
    if err.reason == "outside_coverage":
        return True
    message = str(err).lower()
    return ("http 400" in message or "http 404" in message) and any(
        reason in message
        for reason in (
            "outside coverage",
            "outside the geographical",
            "outside the domain",
            "out of bounds",
        )
    )


def _cache_is_fresh(cache: CacheMetadata) -> bool:
    now = datetime.now(UTC)
    if cache.expires_at is not None:
        return cache.expires_at > now
    if cache.fetched_at is not None:
        return now - cache.fetched_at < timedelta(minutes=1)
    return False
