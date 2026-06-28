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
                    time=now,
                    url="https://api.met.no/weatherapi/radar/2.0/?content=image",
                )
            ],
            latest_time=now,
            attribution="Data from MET Norway",
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
        "custom_components.rain_radar.providers.met_no.MetNoProvider.async_get_radar_frames",
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
