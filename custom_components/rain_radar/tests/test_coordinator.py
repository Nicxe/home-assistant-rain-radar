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
        return RainRiskForecast(
            max_probability=None,
            coverage_status=CoverageStatus.TEMPORARILY_UNAVAILABLE,
        )


class RateLimitedProvider(TemporarilyUnavailableProvider):
    """Fake provider exposing a swallowed rate-limit error."""

    last_error = "Provider rate limited the request"
    last_error_type = "RainRadarApiRateLimitedError"
    last_success = datetime(2026, 8, 14, 5, tzinfo=UTC)


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


@pytest.mark.asyncio
async def test_coordinator_exposes_provider_rate_limit_status(
    hass: HomeAssistant,
    rain_radar_config_entry,
) -> None:
    """Test swallowed provider failures remain visible in coordinator status."""
    coordinator = RainRadarCoordinator(
        hass,
        config_entry=rain_radar_config_entry,
        provider=RateLimitedProvider(),
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

    assert coordinator.last_error_type == "RainRadarApiRateLimitedError"
    assert coordinator.data is not None
    assert coordinator.data.provider_status.message == (
        "Provider rate limited the request"
    )
    assert coordinator.data.provider_status.last_success == datetime(
        2026, 8, 14, 5, tzinfo=UTC
    )


@pytest.mark.parametrize("cold_start", [True, False])
async def test_forecast_outage_keeps_fresh_radar(
    hass, rain_radar_config_entry, cold_start
):
    """Forecast failures cannot discard a successfully fetched radar frame."""
    from custom_components.rain_radar.api import RainRadarApiTemporaryError
    from custom_components.rain_radar.providers.models import RadarFrame

    class OutageProvider(FakeProvider):
        async def async_get_precipitation_forecast(self, location, options):
            raise RainRadarApiTemporaryError("Forecast down", reason="server_error")

        async def async_get_rain_risk(self, location, options):
            raise RainRadarApiTemporaryError("Forecast down", reason="server_error")

        async def async_get_radar_frames(self, location, options):
            now = datetime.now(UTC)
            return RadarFrameSet(
                frames=[RadarFrame("new", now, "https://example.com/image", "image")],
                latest_time=now,
                updated_at=now,
                coverage_status=CoverageStatus.OK,
            )

    coordinator = RainRadarCoordinator(
        hass,
        config_entry=rain_radar_config_entry,
        provider=FakeProvider() if not cold_start else OutageProvider(),
        location=Location(59, 18),
        options=RainRadarOptions("", "met_no", "nordic", 0.1, 60, 1000, 12),
    )
    if not cold_start:
        await coordinator.async_refresh()
        coordinator.provider = OutageProvider()
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert coordinator.data.radar_frames.frames[0].frame_id == "new"
    assert coordinator.data.radar_status.status == "ok"
    assert coordinator.data.forecast_status.status == "temporarily_unavailable"
    assert coordinator.data.forecast_status.reason == "server_error"


@pytest.mark.parametrize("radar_case", ["empty", "old", "error"])
async def test_bad_radar_degrades_health_independently(
    hass, rain_radar_config_entry, radar_case
):
    """Healthy forecasts do not mask empty, old, or failed radar metadata."""
    from custom_components.rain_radar.api import RainRadarApiTemporaryError
    from custom_components.rain_radar.providers.models import RadarFrame

    class RadarFailureProvider(FakeProvider):
        async def async_get_radar_frames(self, location, options):
            if radar_case == "error":
                raise RainRadarApiTemporaryError("Radar down", reason="server_error")
            if radar_case == "old":
                old = datetime.now(UTC) - timedelta(hours=24)
                return RadarFrameSet(
                    frames=[RadarFrame("old", old, "https://example.com/image", "old")],
                    latest_time=old,
                    coverage_status=CoverageStatus.OK,
                )
            return RadarFrameSet(coverage_status=CoverageStatus.OK)

    coordinator = RainRadarCoordinator(
        hass,
        config_entry=rain_radar_config_entry,
        provider=RadarFailureProvider(),
        location=Location(59, 18),
        options=RainRadarOptions("", "met_no", "nordic", 0.1, 60, 1000, 12),
    )
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert coordinator.data.provider_status.health == ProviderHealth.DEGRADED
    assert coordinator.data.forecast_status.status == "ok"
    assert coordinator.data.radar_status.status == (
        "stale" if radar_case == "old" else "temporarily_unavailable"
    )
    assert coordinator.data.radar_frames.is_stale == (radar_case == "old")


@pytest.mark.parametrize("has_observation", [False, True])
async def test_dmi_fetch_time_is_not_forecast_data_age(
    hass, rain_radar_config_entry, has_observation, freezer
):
    """Legacy DMI updated_at is a fetch time, not weather production evidence."""
    now = datetime.now(UTC)
    freezer.move_to(now)

    class DmiMetadataProvider(FakeProvider):
        async def async_get_precipitation_forecast(self, location, options):
            return PrecipitationForecast(
                updated_at=now,
                observation_time=now - timedelta(minutes=10)
                if has_observation
                else None,
                current_precipitation=0,
                coverage_status=CoverageStatus.OK,
            )

    coordinator = RainRadarCoordinator(
        hass,
        config_entry=rain_radar_config_entry,
        provider=DmiMetadataProvider(),
        location=Location(59, 18),
        options=RainRadarOptions("", "dmi", "denmark", 0.1, 60, 1000, 12),
    )
    await coordinator.async_refresh()
    assert coordinator.data.precipitation.updated_at == now
    assert coordinator.data.forecast_status.data_age_seconds == (
        600 if has_observation else None
    )


async def test_stale_data_retains_successful_delivery_timestamp(
    hass, rain_radar_config_entry
):
    """Receiving old data is a successful delivery with independently stale data."""
    from custom_components.rain_radar.providers.models import CacheMetadata, RadarFrame

    now = datetime.now(UTC)
    old = now - timedelta(hours=2)

    class StaleProvider(FakeProvider):
        async def async_get_precipitation_forecast(self, location, options):
            return PrecipitationForecast(
                current_precipitation=None,
                is_stale=True,
                updated_at=old,
                coverage_status=CoverageStatus.OK,
                cache=CacheMetadata(fetched_at=now),
            )

        async def async_get_radar_frames(self, location, options):
            return RadarFrameSet(
                frames=[RadarFrame("old", old, "https://example.com/image", "old")],
                latest_time=old,
                coverage_status=CoverageStatus.OK,
                cache=CacheMetadata(fetched_at=now),
            )

    coordinator = RainRadarCoordinator(
        hass,
        config_entry=rain_radar_config_entry,
        provider=StaleProvider(),
        location=Location(59, 18),
        options=RainRadarOptions("", "met_no", "nordic", 0.1, 60, 1000, 12),
    )
    await coordinator.async_refresh()
    assert coordinator.data.radar_status.status == "stale"
    assert coordinator.data.radar_status.last_success == now
    assert coordinator.data.forecast_status.status == "stale"
    assert coordinator.data.forecast_status.last_success == now
