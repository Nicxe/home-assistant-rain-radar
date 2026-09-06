"""Tests for Rain Radar entities."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
import pytest

from custom_components.rain_radar.api import RainRadarApiTemporaryError
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
    assert attrs["rain_radar_entity_key"] == "rain_risk_12h"

    precipitation_attrs = hass.states.get("sensor.home_precipitation_now").attributes
    assert precipitation_attrs["rain_radar_entity_key"] == "precipitation_now"
    assert precipitation_attrs["rain_threshold"] == 0.1
    assert precipitation_attrs["rain_soon_window_minutes"] == 60

    rain_soon_attrs = hass.states.get("binary_sensor.home_rain_soon").attributes
    assert rain_soon_attrs["rain_radar_entity_key"] == "rain_soon"

    provider_attrs = hass.states.get("sensor.home_provider").attributes
    assert provider_attrs["rain_radar_entity_key"] == "provider"
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

    assert hass.states.get("binary_sensor.home_radar_coverage").state == "unavailable"
    assert hass.states.get("sensor.home_provider").attributes["coverage_status"] == "ok"
    assert (
        hass.states.get("sensor.home_provider").attributes["radar_coverage_status"]
        == "temporarily_unavailable"
    )


@pytest.fixture
def mocked_provider_updates(monkeypatch):
    """Provide successful source updates without accessing external services."""
    now = datetime.now(UTC)
    precipitation = AsyncMock(
        return_value=PrecipitationForecast(
            current_precipitation=0.2,
            rain_now=True,
            rain_soon=True,
            updated_at=now,
            coverage_status=CoverageStatus.OK,
        )
    )
    rain_risk = AsyncMock(
        return_value=RainRiskForecast(
            max_probability=100,
            hourly=[
                RainRiskHour(
                    time=now + timedelta(hours=1),
                    probability=100,
                    precipitation_amount=0.2,
                    symbol_code="rain",
                )
            ],
            updated_at=now,
        )
    )
    frames = AsyncMock(
        return_value=RadarFrameSet(
            latest_time=now,
            coverage_status=CoverageStatus.OK,
        )
    )
    monkeypatch.setattr(
        "custom_components.rain_radar.providers.met_no.MetNoProvider.async_get_precipitation_forecast",
        precipitation,
    )
    monkeypatch.setattr(
        "custom_components.rain_radar.providers.met_no.MetNoProvider.async_get_rain_risk",
        rain_risk,
    )
    monkeypatch.setattr(
        "custom_components.rain_radar.providers.regnradar.RegnradarProvider.async_get_radar_frames",
        frames,
    )
    return precipitation, rain_risk, frames


async def test_entities_follow_coordinator_failure_and_recovery(
    hass: HomeAssistant,
    rain_radar_config_entry,
    mocked_provider_updates,
) -> None:
    """Do not expose old entity values after a failed coordinator update."""
    precipitation, rain_risk, _ = mocked_provider_updates
    rain_radar_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(rain_radar_config_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = rain_radar_config_entry.runtime_data.coordinator

    assert hass.states.get("sensor.home_precipitation_now").state == "0.2"
    assert hass.states.get("binary_sensor.home_raining_now").state == "on"

    precipitation.side_effect = RainRadarApiTemporaryError("Forecast unavailable")
    rain_risk.side_effect = RainRadarApiTemporaryError("Forecast unavailable")
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert coordinator.last_update_success is False
    assert hass.states.get("sensor.home_precipitation_now").state == "unavailable"
    assert hass.states.get("binary_sensor.home_raining_now").state == "unavailable"

    precipitation.side_effect = None
    rain_risk.side_effect = None
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert coordinator.last_update_success is True
    assert hass.states.get("sensor.home_precipitation_now").state == "0.2"
    assert hass.states.get("binary_sensor.home_raining_now").state == "on"


async def test_risk_attributes_keep_the_final_partial_forecast_interval(
    hass: HomeAssistant,
    rain_radar_config_entry,
    mocked_provider_updates,
) -> None:
    """A horizon can overlap thirteen hourly intervals when starting mid-hour."""
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    _, rain_risk, _ = mocked_provider_updates
    rain_risk.return_value = RainRiskForecast(
        max_probability=100,
        hourly=[
            RainRiskHour(
                time=now + timedelta(hours=hour),
                probability=100 if hour == 12 else 0,
                precipitation_amount=0.4 if hour == 12 else 0,
                symbol_code="rain" if hour == 12 else None,
            )
            for hour in range(13)
        ],
        updated_at=now,
    )
    rain_radar_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(rain_radar_config_entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("sensor.home_rain_risk_12h")
    assert state.state == "100"
    assert len(state.attributes["hourly"]) == 13
    assert (
        state.attributes["hourly"][(now + timedelta(hours=12)).isoformat()][
            "probability"
        ]
        == 100
    )


async def test_partial_forecast_outage_keeps_radar_available(
    hass: HomeAssistant,
    rain_radar_config_entry,
    mocked_provider_updates,
) -> None:
    """A forecast backoff must not hide independently available radar data."""
    precipitation, rain_risk, frames = mocked_provider_updates
    now = datetime.now(UTC)
    frames.return_value = RadarFrameSet(
        frames=[
            RadarFrame(
                "fresh", now, "https://api.regnradar.se/radar/file/fresh.png", "fresh"
            )
        ],
        latest_time=now,
        coverage_status=CoverageStatus.OK,
    )
    precipitation.return_value = PrecipitationForecast(
        coverage_status=CoverageStatus.TEMPORARILY_UNAVAILABLE
    )
    rain_risk.return_value = RainRiskForecast(max_probability=None)
    rain_radar_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(rain_radar_config_entry.entry_id)
    await hass.async_block_till_done()

    assert rain_radar_config_entry.runtime_data.coordinator.last_update_success
    assert hass.states.get("sensor.home_precipitation_now").state == "unavailable"
    assert hass.states.get("binary_sensor.home_raining_now").state == "unavailable"
    assert hass.states.get("binary_sensor.home_radar_coverage").state == "on"
    assert hass.states.get("sensor.home_latest_radar_time").state != "unavailable"
    assert hass.states.get("sensor.home_provider").attributes["status"] == "degraded"


@pytest.mark.parametrize(
    ("forecast_age", "radar_age", "expected_age"),
    [(120, 30, "30"), (120, None, "120"), (None, 30, "30"), (None, None, "unknown")],
)
async def test_data_age_uses_source_timestamps_not_coordinator_tick(
    hass: HomeAssistant,
    rain_radar_config_entry,
    mocked_provider_updates,
    forecast_age,
    radar_age,
    expected_age,
) -> None:
    """Local refreshes must not make old or missing source data look new."""
    now = datetime.now(UTC)
    precipitation, rain_risk, frames = mocked_provider_updates
    forecast_time = (
        now - timedelta(minutes=forecast_age) if forecast_age is not None else None
    )
    precipitation.return_value = PrecipitationForecast(updated_at=forecast_time)
    rain_risk.return_value = RainRiskForecast(
        max_probability=None,
        updated_at=forecast_time,
    )
    frames.return_value = RadarFrameSet(
        latest_time=now - timedelta(minutes=radar_age)
        if radar_age is not None
        else None
    )
    rain_radar_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(rain_radar_config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.home_data_age").state == expected_age


async def test_forecast_failure_and_recovery_preserve_radar_and_source_times(
    hass, rain_radar_config_entry, mocked_provider_updates
):
    """An outage affects only its source and recovery clears its failure metadata."""
    precipitation, rain_risk, frames = mocked_provider_updates
    now = datetime.now(UTC)
    frames.return_value = RadarFrameSet(
        frames=[
            RadarFrame(
                "fresh", now, "https://api.regnradar.se/radar/file/fresh.png", "fresh"
            )
        ],
        latest_time=now,
        coverage_status=CoverageStatus.OK,
    )
    rain_radar_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(rain_radar_config_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = rain_radar_config_entry.runtime_data.coordinator
    last_success = coordinator.data.forecast_status.last_success
    precipitation.side_effect = RainRadarApiTemporaryError(
        "Forecast down", reason="server_error"
    )
    rain_risk.side_effect = RainRadarApiTemporaryError(
        "Forecast down", reason="server_error"
    )
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator.last_update_success
    assert hass.states.get("sensor.home_precipitation_now").state == "unavailable"
    assert hass.states.get("binary_sensor.home_radar_coverage").state == "on"
    assert coordinator.data.forecast_status.last_success == last_success
    assert coordinator.data.forecast_status.reason == "server_error"
    precipitation.side_effect = None
    rain_risk.side_effect = None
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("sensor.home_precipitation_now").state == "0.2"
    assert coordinator.data.forecast_status.reason is None
    assert coordinator.data.forecast_status.status == "ok"
    assert (
        hass.states.get("binary_sensor.home_radar_coverage").attributes["is_stale"]
        is False
    )


async def test_setup_with_failed_met_forecast_and_fresh_radar(
    hass, rain_radar_config_entry, mocked_provider_updates
):
    """First refresh loads a MET location even when only radar is delivered."""
    precipitation, rain_risk, frames = mocked_provider_updates
    now = datetime.now(UTC)
    frames.return_value = RadarFrameSet(
        frames=[
            RadarFrame(
                "fresh", now, "https://api.regnradar.se/radar/file/fresh.png", "fresh"
            )
        ],
        latest_time=now,
        coverage_status=CoverageStatus.OK,
    )
    precipitation.side_effect = RainRadarApiTemporaryError(
        "Forecast down", reason="server_error"
    )
    rain_risk.side_effect = RainRadarApiTemporaryError(
        "Forecast down", reason="server_error"
    )
    rain_radar_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(rain_radar_config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.home_radar_coverage").state == "on"
    assert hass.states.get("sensor.home_precipitation_now").state == "unavailable"
    assert (
        hass.states.get("sensor.home_provider").attributes["forecast_status"]["reason"]
        == "server_error"
    )


@pytest.mark.parametrize(("radar_age", "expected_age"), [(30, "30"), (None, "unknown")])
async def test_dmi_fetch_time_does_not_override_real_radar_data_age(
    hass,
    rain_radar_config_entry,
    mocked_provider_updates,
    monkeypatch,
    radar_age,
    expected_age,
):
    """A freshly fetched DMI forecast cannot make existing radar data look new."""
    now = datetime.now(UTC)
    precipitation, rain_risk, frames = mocked_provider_updates
    precipitation.return_value = PrecipitationForecast(updated_at=now)
    rain_risk.return_value = RainRiskForecast(max_probability=None, updated_at=now)
    frames.return_value = RadarFrameSet(
        latest_time=now - timedelta(minutes=radar_age)
        if radar_age is not None
        else None
    )
    monkeypatch.setattr(
        "custom_components.rain_radar.providers.dmi.DmiProvider.async_get_precipitation_forecast",
        precipitation,
    )
    monkeypatch.setattr(
        "custom_components.rain_radar.providers.dmi.DmiProvider.async_get_rain_risk",
        rain_risk,
    )
    rain_radar_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        rain_radar_config_entry, options={"forecast_provider": "dmi"}
    )
    assert await hass.config_entries.async_setup(rain_radar_config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.home_data_age").state == expected_age
    assert (
        rain_radar_config_entry.runtime_data.coordinator.data.forecast_status.data_age_seconds
        is None
    )


async def test_missing_met_probability_stays_unknown_when_nowcast_fails(
    hass, rain_radar_config_entry, mocked_provider_updates
):
    """A successful model with no probabilities is independent of nowcast failure."""
    precipitation, rain_risk, _frames = mocked_provider_updates
    precipitation.side_effect = RainRadarApiTemporaryError(
        "Nowcast down", reason="server_error"
    )
    now = datetime.now(UTC)
    rain_risk.return_value = RainRiskForecast(
        max_probability=None,
        hourly=[RainRiskHour(now, None, 0.0, "cloudy")],
        window_complete=False,
        reason="incomplete_window",
    )
    rain_radar_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(rain_radar_config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.home_precipitation_now").state == "unavailable"
    risk = hass.states.get("sensor.home_rain_risk_12h")
    assert risk.state == "unknown"
    assert risk.attributes["hourly"][now.isoformat()]["precipitation_amount"] == 0.0


async def test_each_forecast_entity_exposes_its_own_staleness(
    hass, rain_radar_config_entry, mocked_provider_updates
):
    """A stale short forecast must not mark a fresh probability model as stale."""
    precipitation, rain_risk, _frames = mocked_provider_updates
    precipitation.return_value = PrecipitationForecast(
        is_stale=True, reason="stale_data", window_complete=False
    )
    rain_risk.return_value = RainRiskForecast(max_probability=49, is_stale=False)
    rain_radar_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(rain_radar_config_entry.entry_id)
    await hass.async_block_till_done()

    for entity_id in ("sensor.home_rain_arrival", "binary_sensor.home_rain_soon"):
        attrs = hass.states.get(entity_id).attributes
        assert attrs["is_stale"] is True
        assert attrs["reason"] == "stale_data"
    assert hass.states.get("sensor.home_rain_risk_12h").attributes["is_stale"] is False
