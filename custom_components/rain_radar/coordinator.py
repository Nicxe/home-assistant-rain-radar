"""Coordinator for Rain Radar."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
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
        try:
            precipitation_result, rain_risk_result, radar_result = await asyncio.gather(
                self.provider.async_get_precipitation_forecast(
                    self.location, self.options
                ),
                self.provider.async_get_rain_risk(self.location, self.options),
                self.provider.async_get_radar_frames(self.location, self.options),
                return_exceptions=True,
            )
        except RainRadarApiAuthError as err:
            self.last_error_type = type(err).__name__
            raise ConfigEntryAuthFailed from err

        errors: list[BaseException] = []
        if isinstance(precipitation_result, BaseException):
            if isinstance(precipitation_result, RainRadarApiAuthError):
                self.last_error_type = type(precipitation_result).__name__
                raise ConfigEntryAuthFailed from precipitation_result
            errors.append(precipitation_result)
            precipitation = PrecipitationForecast(
                coverage_status=CoverageStatus.UNKNOWN
            )
        else:
            precipitation = precipitation_result

        if isinstance(rain_risk_result, BaseException):
            if isinstance(rain_risk_result, RainRadarApiAuthError):
                self.last_error_type = type(rain_risk_result).__name__
                raise ConfigEntryAuthFailed from rain_risk_result
            errors.append(rain_risk_result)
            rain_risk = RainRiskForecast(max_probability=None)
        else:
            rain_risk = rain_risk_result

        if isinstance(radar_result, BaseException):
            if isinstance(radar_result, RainRadarApiAuthError):
                self.last_error_type = type(radar_result).__name__
                raise ConfigEntryAuthFailed from radar_result
            _LOGGER.debug("Radar frame metadata update failed: %s", radar_result)
            radar_frames = RadarFrameSet(
                attribution=self.provider.attribution,
                coverage_status=precipitation.coverage_status,
            )
        else:
            radar_frames = radar_result

        if (
            errors
            and precipitation.current_precipitation is None
            and not rain_risk.hourly
        ):
            err = errors[0]
            self.last_error_type = type(err).__name__
            raise UpdateFailed(str(err)) from err

        health = ProviderHealth.DEGRADED if errors else ProviderHealth.OK
        self.last_error_type = type(errors[0]).__name__ if errors else None
        now = datetime.now(UTC)
        provider_status = ProviderStatus(
            provider_id=self.provider.provider_id,
            provider_name=self.provider.provider_name,
            attribution=self.provider.attribution,
            coverage_status=precipitation.coverage_status,
            health=health,
            message=str(errors[0]) if errors else None,
            last_success=now if not errors else None,
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
        )
