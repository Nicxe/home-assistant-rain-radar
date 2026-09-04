"""Integration lifecycle tests using the real DMI and radar providers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import logging
from unittest.mock import AsyncMock, Mock

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rain_radar.api import (
    RainRadarApiClient,
    RainRadarApiRateLimitedError,
)
from custom_components.rain_radar.const import (
    CONF_FORECAST_PROVIDER,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_NAME,
    CONF_RADAR_AREA,
    DMI_FORECAST_PARAMETERS,
    DMI_FORECAST_URL,
    DOMAIN,
    PROVIDER_DMI,
    REGNRADAR_RADAR_URL,
)
from custom_components.rain_radar.diagnostics import async_get_config_entry_diagnostics
from custom_components.rain_radar.providers.dmi import DmiProvider
from custom_components.rain_radar.providers.models import CacheMetadata
from custom_components.rain_radar.providers.regnradar import RegnradarProvider

from .test_dmi_provider import _payload as _dmi_payload
from .test_regnradar_provider import _payload as _radar_payload


def _entry() -> MockConfigEntry:
    """Select DMI through current-version config-entry options."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Home",
        version=2,
        data={
            CONF_NAME: "Home",
            CONF_LATITUDE: 55.715,
            CONF_LONGITUDE: 12.561,
            CONF_RADAR_AREA: "nordic",
        },
        options={CONF_FORECAST_PROVIDER: PROVIDER_DMI},
    )


def _mock_http_clients(monkeypatch, *, dmi_busy: bool = False) -> list[str]:
    """Mock network responses, leaving normalization and lifecycle untouched."""
    now = datetime.now(UTC).replace(microsecond=0)
    monkeypatch.setattr(
        "custom_components.rain_radar.providers.dmi._utcnow", lambda: now
    )
    monkeypatch.setattr(
        "custom_components.rain_radar.async_setup_frontend", AsyncMock()
    )
    monkeypatch.setattr(
        "custom_components.rain_radar.async_register_http_views", Mock()
    )
    monkeypatch.setattr(
        RainRadarApiClient,
        "async_get_bytes",
        AsyncMock(side_effect=AssertionError("Unexpected image network request")),
    )
    forecast = _dmi_payload(now, forecast_hours=24)
    radar = _radar_payload()
    for index, frame in enumerate(radar["nordic"]["images"]):
        frame["time_utc"] = (now - timedelta(minutes=10 - index * 5)).isoformat()
    calls: list[str] = []

    async def _get_json(self, cache_key, url, **kwargs):
        calls.append(url)
        if url == DMI_FORECAST_URL:
            assert kwargs["params"]["parameter-name"] == DMI_FORECAST_PARAMETERS
            assert kwargs["auth_required"] is False
            if dmi_busy:
                raise RainRadarApiRateLimitedError(
                    "Server is busy. Please try again later.",
                    status_code=429,
                    reason="server_busy",
                )
            payload = forecast
        elif url == REGNRADAR_RADAR_URL:
            payload = radar
        else:
            raise AssertionError(f"Unexpected API endpoint: {url}")
        return payload, CacheMetadata(
            fetched_at=now, expires_at=now + timedelta(hours=1)
        )

    monkeypatch.setattr(RainRadarApiClient, "async_get_json", _get_json)
    return calls


async def test_dmi_entry_setup_refresh_reload_and_unload_reuse_forecast(
    hass: HomeAssistant, monkeypatch
) -> None:
    """The real DMI pipeline shares one fetch across consumers and entry reload."""
    calls = _mock_http_clients(monkeypatch)
    entry = _entry()
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    first_runtime = entry.runtime_data
    assert isinstance(first_runtime.client, RainRadarApiClient)
    assert isinstance(first_runtime.provider, RegnradarProvider)
    assert isinstance(first_runtime.provider.forecast_provider, DmiProvider)
    assert float(hass.states.get("sensor.home_precipitation_now").state) == 0.0
    assert hass.states.get("binary_sensor.home_raining_now").state == "off"
    assert hass.states.get("binary_sensor.home_rain_soon").state == "on"
    assert hass.states.get("sensor.home_rain_risk_12h").state == "100"

    await first_runtime.coordinator.async_refresh()
    await first_runtime.coordinator.async_refresh()
    await hass.async_block_till_done()
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data is not first_runtime
    assert entry.runtime_data.client is not first_runtime.client
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    request = diagnostics["provider"]["forecast_request"]
    assert calls.count(DMI_FORECAST_URL) == 1
    assert request["request_count"] == 1
    assert request["cache_hits"] >= 4
    assert request["cache_usable"] is True
    assert request["last_error_reason"] is None
    assert diagnostics["provider"]["health"] == "ok"
    assert diagnostics["data"]["radar_frame_count"] == 2
    assert hass.states.get("binary_sensor.home_radar_coverage").state == "on"
    assert float(hass.states.get("sensor.home_precipitation_now").state) == 0.0

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_dmi_busy_entry_keeps_radar_and_backoff_across_reload(
    hass: HomeAssistant, monkeypatch, caplog
) -> None:
    """DMI failure keeps the entry and radar working without retries or log spam."""
    caplog.set_level(
        logging.WARNING, logger="custom_components.rain_radar.providers.dmi"
    )
    calls = _mock_http_clients(monkeypatch, dmi_busy=True)
    entry = _entry()
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    first_runtime = entry.runtime_data
    retry_at = first_runtime.provider.forecast_provider.backoff_until
    assert retry_at is not None
    assert entry.state is ConfigEntryState.LOADED
    assert first_runtime.coordinator.last_update_success is True
    assert hass.states.get("sensor.home_precipitation_now").state == "unknown"
    assert hass.states.get("binary_sensor.home_raining_now").state == "unknown"
    assert hass.states.get("sensor.home_rain_risk_12h").state == "unknown"
    assert hass.states.get("binary_sensor.home_radar_coverage").state == "on"
    assert hass.states.get("sensor.home_provider").attributes["status"] == "degraded"

    await first_runtime.coordinator.async_refresh()
    await first_runtime.coordinator.async_refresh()
    await hass.async_block_till_done()
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data is not first_runtime
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    request = diagnostics["provider"]["forecast_request"]
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.coordinator.last_update_success is True
    assert entry.runtime_data.provider.forecast_provider.backoff_until == retry_at
    assert calls.count(DMI_FORECAST_URL) == 1
    assert calls.count(REGNRADAR_RADAR_URL) >= 5
    assert request["request_count"] == 1
    assert request["status_code"] == 429
    assert request["last_error_reason"] == "server_busy"
    assert request["cache_usable"] is False
    assert diagnostics["provider"]["health"] == "degraded"
    assert diagnostics["provider"]["radar_coverage_status"] == "ok"
    assert diagnostics["data"]["radar_frame_count"] == 2
    assert hass.states.get("sensor.home_precipitation_now").state == "unknown"
    assert hass.states.get("binary_sensor.home_radar_coverage").state == "on"
    warnings = [
        record
        for record in caplog.records
        if record.name == "custom_components.rain_radar.providers.dmi"
        and record.levelno >= logging.WARNING
    ]
    assert len(warnings) == 1
    assert "server_busy" in warnings[0].getMessage()

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
