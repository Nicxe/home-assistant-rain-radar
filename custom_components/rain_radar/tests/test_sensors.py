"""Tests for Rain Radar entities."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from homeassistant.core import HomeAssistant

from custom_components.rain_radar.providers.models import (
    CoverageStatus,
    PrecipitationForecast,
    PrecipitationSample,
    RadarFrame,
    RadarFrameSet,
    RainRiskForecast,
    RainRiskHour,
)


async def test_entities_are_created_with_expected_states(
    hass: HomeAssistant,
    rain_radar_config_entry,
    monkeypatch,
) -> None:
    """Test required entities and rain-risk state."""
    now = datetime.now(UTC)

    async def _precipitation(self, location, options):
        return PrecipitationForecast(
            samples=[
                PrecipitationSample(time=now, precipitation_rate=0.0),
                PrecipitationSample(
                    time=now + timedelta(minutes=30), precipitation_rate=0.4
                ),
            ],
            current_precipitation=0.0,
            rain_now=False,
            rain_soon=True,
            rain_arrival_minutes=30,
            updated_at=now,
            latest_time=now + timedelta(minutes=30),
            coverage_status=CoverageStatus.OK,
        )

    async def _rain_risk(self, location, options):
        return RainRiskForecast(
            max_probability=85,
            hourly=[
                RainRiskHour(
                    time=now + timedelta(hours=1),
                    probability=85,
                    precipitation_amount=3.0,
                    symbol_code="rain",
                )
            ],
            updated_at=now,
        )

    async def _frames(self, location, options):
        return RadarFrameSet(
            frames=[
                RadarFrame(
                    frame_id="regnradar-nordic-obs-test",
                    time=now,
                    source_url="https://api.regnradar.se/radar/file/test.png",
                    image_cache_key="regnradar_radar_image_test",
                )
            ],
            latest_time=now,
            attribution="Radar imagery from Regnradar/Vackertväder",
            coverage_status=CoverageStatus.OK,
        )

    monkeypatch.setattr(
        "custom_components.rain_radar.providers.met_no.MetNoProvider.async_get_precipitation_forecast",
        _precipitation,
    )
    monkeypatch.setattr(
        "custom_components.rain_radar.providers.met_no.MetNoProvider.async_get_rain_risk",
        _rain_risk,
    )
    monkeypatch.setattr(
        "custom_components.rain_radar.providers.regnradar.RegnradarProvider.async_get_radar_frames",
        _frames,
    )

    rain_radar_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(rain_radar_config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.home_rain_risk_12h").state == "85"
    assert hass.states.get("sensor.home_rain_arrival").state == "30"
    assert hass.states.get("binary_sensor.home_rain_soon").state == "on"
    assert hass.states.get("binary_sensor.home_radar_coverage").state == "on"

    attrs = hass.states.get("sensor.home_rain_risk_12h").attributes
    assert "hourly" in attrs
    assert len(attrs["hourly"]) == 1
    assert attrs["rain_radar_entry_id"] == rain_radar_config_entry.entry_id

    precipitation_attrs = hass.states.get("sensor.home_precipitation_now").attributes
    assert precipitation_attrs["rain_threshold"] == 0.1
    assert precipitation_attrs["rain_soon_window_minutes"] == 60

    provider_attrs = hass.states.get("sensor.home_provider").attributes
    assert provider_attrs["radar_provider_id"] == "regnradar"
    assert provider_attrs["radar_area"] == "nordic"
    assert provider_attrs["forecast_provider_id"] == "met_no"
    assert provider_attrs["radar_coverage_status"] == "ok"
    assert provider_attrs["radar_frame_count"] == 1


async def test_radar_coverage_uses_radar_frame_status(
    hass: HomeAssistant,
    rain_radar_config_entry,
    monkeypatch,
) -> None:
    """Test radar coverage does not follow forecast coverage."""
    now = datetime.now(UTC)

    async def _precipitation(self, location, options):
        return PrecipitationForecast(
            current_precipitation=0.0,
            rain_now=False,
            rain_soon=False,
            coverage_status=CoverageStatus.OK,
        )

    async def _rain_risk(self, location, options):
        return RainRiskForecast(max_probability=0, updated_at=now)

    async def _frames(self, location, options):
        return RadarFrameSet(coverage_status=CoverageStatus.TEMPORARILY_UNAVAILABLE)

    monkeypatch.setattr(
        "custom_components.rain_radar.providers.met_no.MetNoProvider.async_get_precipitation_forecast",
        _precipitation,
    )
    monkeypatch.setattr(
        "custom_components.rain_radar.providers.met_no.MetNoProvider.async_get_rain_risk",
        _rain_risk,
    )
    monkeypatch.setattr(
        "custom_components.rain_radar.providers.regnradar.RegnradarProvider.async_get_radar_frames",
        _frames,
    )

    rain_radar_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(rain_radar_config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.home_radar_coverage").state == "off"
    assert hass.states.get("sensor.home_provider").attributes["coverage_status"] == "ok"
    assert (
        hass.states.get("sensor.home_provider").attributes["radar_coverage_status"]
        == "temporarily_unavailable"
    )
