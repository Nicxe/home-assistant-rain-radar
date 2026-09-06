"""Coordinator for Rain Radar."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import RainRadarApiAuthError
from .const import DEFAULT_UPDATE_INTERVAL, DOMAIN
from .providers.base import RainRadarProvider
from .providers.models import (
    CoverageStatus,
    Location,
    PrecipitationForecast,
    ProviderHealth,
    ProviderStatus,
    RadarFrameSet,
    RainRadarOptions,
    RainRiskForecast,
    SourceStatus,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RainRadarData:
    """Coordinator data exposed to entities and endpoints."""

    location: Location
    options: RainRadarOptions
    provider_status: ProviderStatus
    precipitation: PrecipitationForecast
    rain_risk: RainRiskForecast
    radar_frames: RadarFrameSet
    updated_at: datetime
    forecast_status: SourceStatus = field(default_factory=SourceStatus)
    radar_status: SourceStatus = field(default_factory=SourceStatus)
    precipitation_available: bool = True
    rain_risk_available: bool = True


class RainRadarCoordinator(DataUpdateCoordinator[RainRadarData]):
    """Fetch and normalize data through a provider."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        config_entry: ConfigEntry,
        provider: RainRadarProvider,
        location: Location,
        options: RainRadarOptions,
    ) -> None:
        """Initialize coordinator."""
        super().__init__(
            hass,
            logger=_LOGGER,
            name=DOMAIN,
            update_interval=DEFAULT_UPDATE_INTERVAL,
            config_entry=config_entry,
        )
        self.provider = provider
        self.location = location
        self.options = options
        self.last_error_type: str | None = None

    def update_runtime_options(
        self,
        *,
        location: Location,
        options: RainRadarOptions,
    ) -> None:
        """Update location and options after an options-flow change."""
        self.location = location
        self.options = options

    async def _async_update_data(self) -> RainRadarData:
        now = datetime.now(UTC)
        precipitation_result, rain_risk_result, radar_result = await asyncio.gather(
            self.provider.async_get_precipitation_forecast(self.location, self.options),
            self.provider.async_get_rain_risk(self.location, self.options),
            self.provider.async_get_radar_frames(self.location, self.options),
            return_exceptions=True,
        )
        for result in (precipitation_result, rain_risk_result, radar_result):
            if isinstance(result, RainRadarApiAuthError):
                self.last_error_type = type(result).__name__
                raise ConfigEntryAuthFailed from result
        errors = [
            result
            for result in (precipitation_result, rain_risk_result)
            if isinstance(result, Exception)
        ]
        precipitation = (
            precipitation_result
            if not isinstance(precipitation_result, Exception)
            else PrecipitationForecast(
                coverage_status=CoverageStatus.TEMPORARILY_UNAVAILABLE
            )
        )
        rain_risk = (
            rain_risk_result
            if not isinstance(rain_risk_result, Exception)
            else RainRiskForecast(max_probability=None)
        )
        radar_error = radar_result if isinstance(radar_result, Exception) else None
        radar_frames = (
            radar_result
            if radar_error is None
            else RadarFrameSet(
                attribution=self.provider.attribution,
                coverage_status=CoverageStatus.TEMPORARILY_UNAVAILABLE,
            )
        )
        if (
            errors
            and precipitation.current_precipitation is None
            and not rain_risk.hourly
            and not radar_frames.frames
        ):
            self.last_error_type = type(errors[0]).__name__
            raise UpdateFailed(str(errors[0])) from errors[0]

        forecast_provider = getattr(self.provider, "forecast_provider", self.provider)
        diagnostics = getattr(forecast_provider, "diagnostics", {})
        forecast_error = errors[0] if errors else None
        forecast_unavailable = (
            precipitation.coverage_status == CoverageStatus.TEMPORARILY_UNAVAILABLE
        )
        forecast_stale = precipitation.is_stale or rain_risk.is_stale
        forecast_state = (
            "temporarily_unavailable"
            if forecast_error or forecast_unavailable
            else "stale"
            if forecast_stale
            else "unknown"
            if precipitation.window_complete is False
            or rain_risk.window_complete is False
            or (
                precipitation.coverage_status == CoverageStatus.OK
                and precipitation.current_precipitation is None
                and rain_risk.max_probability is None
            )
            else precipitation.coverage_status.value
        )
        forecast_reason = (
            getattr(forecast_error, "reason", None)
            or diagnostics.get("last_error_reason")
            or precipitation.reason
            or rain_risk.reason
        )
        if forecast_state == "stale" and not forecast_reason:
            forecast_reason = "stale_data"
        forecast_last_success = max(
            (
                value
                for value in (
                    getattr(forecast_provider, "last_success", None),
                    precipitation.cache.fetched_at,
                    rain_risk.cache.fetched_at,
                )
                if value is not None
            ),
            default=None,
        )
        if forecast_last_success is None:
            if forecast_state == "ok":
                forecast_last_success = now
            elif self.data:
                forecast_last_success = self.data.forecast_status.last_success
        forecast_status = SourceStatus(
            status=forecast_state,
            reason=forecast_reason,
            last_success=forecast_last_success,
            last_attempt=_datetime(diagnostics.get("last_attempt"))
            or getattr(forecast_error, "last_attempt", None)
            or precipitation.cache.fetched_at
            or rain_risk.cache.fetched_at
            or now,
            next_retry=getattr(forecast_error, "next_retry", None)
            or getattr(forecast_provider, "backoff_until", None),
            data_age_seconds=_age(
                precipitation.observation_time
                or rain_risk.observation_time
                or (
                    precipitation.updated_at or rain_risk.updated_at
                    if self.options.forecast_provider != "dmi"
                    else None
                ),
                now,
            ),
        )
        radar_age = _age(radar_frames.latest_time, now)
        radar_stale = radar_frames.is_stale or bool(
            radar_frames.frames and radar_age is not None and radar_age > 30 * 60
        )
        if radar_stale != radar_frames.is_stale:
            radar_frames = replace(radar_frames, is_stale=radar_stale)
        radar_state = (
            "temporarily_unavailable"
            if radar_error
            else "outside_coverage"
            if radar_frames.coverage_status == CoverageStatus.OUTSIDE_COVERAGE
            else "temporarily_unavailable"
            if not radar_frames.frames
            else "stale"
            if radar_stale
            else "unknown"
            if radar_age is None
            else radar_frames.coverage_status.value
        )
        radar_reason = getattr(radar_error, "reason", None) or getattr(
            self.provider, "radar_error_reason", None
        )
        if radar_state == "stale":
            radar_reason = radar_reason or "stale_data"
        elif radar_state == "temporarily_unavailable":
            radar_reason = radar_reason or "no_frames"
        elif radar_state == "unknown" and radar_age is None:
            radar_reason = radar_reason or "missing_timestamp"
        radar_status = SourceStatus(
            status=radar_state,
            reason=radar_reason,
            last_success=(
                radar_frames.cache.fetched_at
                or getattr(self.provider, "radar_last_success", None)
                or (self.data.radar_status.last_success if self.data else None)
                or (now if radar_state == "ok" else None)
            ),
            last_attempt=getattr(self.provider, "radar_last_attempt", None) or now,
            next_retry=getattr(radar_error, "next_retry", None)
            or getattr(self.provider, "radar_next_retry", None),
            data_age_seconds=radar_age,
        )
        degraded = forecast_state != "ok" or radar_state != "ok"
        self.last_error_type = (
            type(forecast_error).__name__
            if forecast_error
            else getattr(forecast_provider, "last_error_type", None)
            if forecast_state != "ok"
            else type(radar_error).__name__
            if radar_error
            else getattr(self.provider, "radar_last_error", None)
            if radar_state != "ok"
            else None
        )
        provider_status = ProviderStatus(
            provider_id=self.provider.provider_id,
            provider_name=self.provider.provider_name,
            attribution=self.provider.attribution,
            coverage_status=precipitation.coverage_status,
            health=ProviderHealth.DEGRADED if degraded else ProviderHealth.OK,
            message=str(forecast_error or radar_error)
            if forecast_error or radar_error
            else getattr(forecast_provider, "last_error", None),
            last_success=forecast_last_success,
            last_error=self.last_error_type,
        )
        return RainRadarData(
            location=self.location,
            options=self.options,
            provider_status=provider_status,
            precipitation=precipitation,
            rain_risk=rain_risk,
            radar_frames=radar_frames,
            updated_at=now,
            forecast_status=forecast_status,
            radar_status=radar_status,
            precipitation_available=not isinstance(precipitation_result, Exception)
            and not forecast_unavailable,
            rain_risk_available=not isinstance(rain_risk_result, Exception)
            and rain_risk.coverage_status != CoverageStatus.TEMPORARILY_UNAVAILABLE,
        )


def _age(value: datetime | None, now: datetime) -> int | None:
    return max(0, round((now - value).total_seconds())) if value else None


def _datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None
