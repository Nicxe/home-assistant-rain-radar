"""Tests for Rain Radar diagnostics."""

from __future__ import annotations

from types import SimpleNamespace

from homeassistant.core import HomeAssistant

from custom_components.rain_radar.const import (
    CONF_CONTACT,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    DEFAULT_RADAR_AREA,
)
from custom_components.rain_radar.diagnostics import async_get_config_entry_diagnostics


async def test_diagnostics_redact_contact_and_round_location(
    hass: HomeAssistant,
    rain_radar_config_entry,
) -> None:
    """Test diagnostics redaction."""
    rain_radar_config_entry.add_to_hass(hass)
    diagnostics = await async_get_config_entry_diagnostics(
        hass, rain_radar_config_entry
    )

    assert diagnostics["entry"]["data"][CONF_CONTACT] == "**REDACTED**"
    assert diagnostics["entry"]["data"][CONF_LATITUDE] == 59.33
    assert diagnostics["entry"]["data"][CONF_LONGITUDE] == 18.07
    assert diagnostics["provider"]["radar_area"] == DEFAULT_RADAR_AREA


async def test_diagnostics_include_forecast_request_state(
    hass: HomeAssistant,
    rain_radar_config_entry,
) -> None:
    """Test DMI cache and backoff state is available for troubleshooting."""
    rain_radar_config_entry.runtime_data = SimpleNamespace(
        coordinator=None,
        provider=SimpleNamespace(
            forecast_provider=SimpleNamespace(
                diagnostics={
                    "backoff_until": "2026-08-14T08:00:00+00:00",
                    "consecutive_failures": 2,
                }
            )
        ),
    )
    rain_radar_config_entry.add_to_hass(hass)

    diagnostics = await async_get_config_entry_diagnostics(
        hass, rain_radar_config_entry
    )

    assert diagnostics["provider"]["forecast_request"] == {
        "backoff_until": "2026-08-14T08:00:00+00:00",
        "consecutive_failures": 2,
    }
