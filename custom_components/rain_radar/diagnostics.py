"""Diagnostics support for Rain Radar."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import RainRadarConfigEntry
from .const import (
    CONF_CONTACT,
    CONF_FORECAST_PROVIDER,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_RADAR_PROVIDER,
    DEFAULT_FORECAST_PROVIDER,
    DEFAULT_RADAR_PROVIDER,
)

TO_REDACT = {CONF_CONTACT}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: RainRadarConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    data = dict(entry.data)
    options = dict(entry.options)

    for payload in (data, options):
        if CONF_LATITUDE in payload:
            payload[CONF_LATITUDE] = round(float(payload[CONF_LATITUDE]), 2)
        if CONF_LONGITUDE in payload:
            payload[CONF_LONGITUDE] = round(float(payload[CONF_LONGITUDE]), 2)

    runtime_data = getattr(entry, "runtime_data", None)
    coordinator = runtime_data.coordinator if runtime_data else None
    coordinator_data = coordinator.data if coordinator else None

    return async_redact_data(
        {
            "entry": {
                "title": entry.title,
                "data": data,
                "options": options,
            },
            "provider": {
                "id": coordinator_data.provider_status.provider_id
                if coordinator_data
                else None,
                "name": coordinator_data.provider_status.provider_name
                if coordinator_data
                else None,
                "coverage_status": coordinator_data.provider_status.coverage_status.value
                if coordinator_data
                else None,
                "health": coordinator_data.provider_status.health.value
                if coordinator_data
                else None,
                "radar_provider": DEFAULT_RADAR_PROVIDER
                if coordinator_data
                else data.get(CONF_RADAR_PROVIDER, DEFAULT_RADAR_PROVIDER),
                "forecast_provider": coordinator_data.options.forecast_provider
                if coordinator_data
                else data.get(CONF_FORECAST_PROVIDER, DEFAULT_FORECAST_PROVIDER),
                "last_error": coordinator.last_error_type if coordinator else None,
            },
            "data": {
                "last_update_success": coordinator.last_update_success
                if coordinator
                else None,
                "updated_at": coordinator_data.updated_at.isoformat()
                if coordinator_data
                else None,
                "precipitation_sample_count": len(
                    coordinator_data.precipitation.samples
                )
                if coordinator_data
                else 0,
                "rain_risk_hour_count": len(coordinator_data.rain_risk.hourly)
                if coordinator_data
                else 0,
                "radar_frame_count": len(coordinator_data.radar_frames.frames)
                if coordinator_data
                else 0,
            },
        },
        TO_REDACT,
    )
