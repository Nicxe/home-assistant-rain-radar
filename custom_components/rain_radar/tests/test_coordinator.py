"""Tests for Rain Radar coordinator."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from homeassistant.core import HomeAssistant
import pytest

from custom_components.rain_radar.coordinator import RainRadarCoordinator
from custom_components.rain_radar.providers.models import (
    CoverageStatus,
    Location,
    PrecipitationForecast,
    PrecipitationSample,
    ProviderHealth,
    RadarFrameSet,
    RainRadarOptions,
    RainRiskForecast,
    RainRiskHour,
)


class FakeProvider:
    """Fake provider for coordinator tests."""

    provider_id = "met_no"
    provider_name = "MET Norway"
    attribution = "Data from MET Norway"
    coverage_status = CoverageStatus.OK

    async def async_get_precipitation_forecast(self, location, options):
        now = datetime.now(UTC)
        return PrecipitationForecast(
            samples=[PrecipitationSample(time=now, precipitation_rate=0.2)],
            current_precipitation=0.2,
            rain_now=True,
            rain_soon=True,
            rain_arrival_minutes=0,
            updated_at=now,
            latest_time=now,
            coverage_status=CoverageStatus.OK,
        )

    async def async_get_rain_risk(self, location, options):
        now = datetime.now(UTC)
        return RainRiskForecast(
            max_probability=74,
            hourly=[
                RainRiskHour(
                    time=now + timedelta(hours=1),
                    probability=74,
                    precipitation_amount=1.2,
                    symbol_code="rain",
                )
            ],
            updated_at=now,
        )

    async def async_get_radar_frames(self, location, options):
        return RadarFrameSet(
            attribution=self.attribution, coverage_status=CoverageStatus.OK
        )


class TemporarilyUnavailableProvider(FakeProvider):
    """Fake provider with temporarily unavailable forecast data."""

    async def async_get_precipitation_forecast(self, location, options):
        return PrecipitationForecast(
            coverage_status=CoverageStatus.TEMPORARILY_UNAVAILABLE
        )

    async def async_get_rain_risk(self, location, options):
        return RainRiskForecast(max_probability=None)


@pytest.mark.asyncio
async def test_coordinator_exposes_normalized_data(
    hass: HomeAssistant,
    rain_radar_config_entry,
) -> None:
    """Test coordinator update."""
    coordinator = RainRadarCoordinator(
        hass,
        config_entry=rain_radar_config_entry,
        provider=FakeProvider(),
        location=Location(59.3293, 18.0686),
        options=RainRadarOptions(
            contact="rain-radar@example.com",
            forecast_provider="met_no",
            radar_area="nordic",
            rain_threshold=0.1,
            rain_soon_window_minutes=60,
            sample_radius_m=1000,
            rain_risk_horizon_hours=12,
        ),
    )

    await coordinator.async_refresh()

    assert coordinator.data is not None
    assert coordinator.data.precipitation.current_precipitation == 0.2
    assert coordinator.data.rain_risk.max_probability == 74
    assert coordinator.data.provider_status.coverage_status == CoverageStatus.OK


@pytest.mark.asyncio
async def test_coordinator_accepts_temporarily_unavailable_forecast(
    hass: HomeAssistant,
    rain_radar_config_entry,
) -> None:
    """Test temporary forecast outages keep the integration loaded."""
    coordinator = RainRadarCoordinator(
        hass,
        config_entry=rain_radar_config_entry,
        provider=TemporarilyUnavailableProvider(),
        location=Location(59.3293, 18.0686),
        options=RainRadarOptions(
            contact="rain-radar@example.com",
            forecast_provider="dmi",
            radar_area="nordic",
            rain_threshold=0.1,
            rain_soon_window_minutes=60,
            sample_radius_m=1000,
            rain_risk_horizon_hours=12,
        ),
    )

    await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert coordinator.data is not None
    assert coordinator.data.provider_status.health == ProviderHealth.DEGRADED
    assert (
        coordinator.data.provider_status.coverage_status
        == CoverageStatus.TEMPORARILY_UNAVAILABLE
    )
