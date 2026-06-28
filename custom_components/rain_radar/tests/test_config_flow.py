"""Tests for Rain Radar config flow."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.rain_radar.const import (
    CONF_CONTACT,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_NAME,
    CONF_PROVIDER,
    CONF_RAIN_RISK_HORIZON_HOURS,
    CONF_RAIN_SOON_WINDOW,
    CONF_RAIN_THRESHOLD,
    CONF_SAMPLE_RADIUS_M,
    DEFAULT_PROVIDER,
    DOMAIN,
)

VALID_INPUT = {
    CONF_NAME: "Home",
    CONF_LATITUDE: 59.3293,
    CONF_LONGITUDE: 18.0686,
    CONF_PROVIDER: DEFAULT_PROVIDER,
    CONF_CONTACT: "rain-radar@example.com",
    CONF_RAIN_THRESHOLD: 0.1,
    CONF_RAIN_SOON_WINDOW: 60,
    CONF_SAMPLE_RADIUS_M: 1000,
    CONF_RAIN_RISK_HORIZON_HOURS: 12,
}


async def test_user_step_shows_form(hass: HomeAssistant) -> None:
    """Test config flow shows user form."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_user_step_creates_entry_after_reload_notice(
    hass: HomeAssistant,
) -> None:
    """Test config flow creates entry with valid input."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        VALID_INPUT,
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reload_notice"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Home"
    assert result["data"][CONF_PROVIDER] == DEFAULT_PROVIDER
    assert result["data"][CONF_RAIN_RISK_HORIZON_HOURS] == 12


async def test_user_step_validates_invalid_latitude(hass: HomeAssistant) -> None:
    """Test invalid latitude is rejected."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    invalid = dict(VALID_INPUT)
    invalid[CONF_LATITUDE] = 91
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        invalid,
    )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"][CONF_LATITUDE] == "invalid_latitude"


async def test_user_step_validates_contact(hass: HomeAssistant) -> None:
    """Test contact validation."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    invalid = dict(VALID_INPUT)
    invalid[CONF_CONTACT] = "not-a-contact"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        invalid,
    )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"][CONF_CONTACT] == "invalid_contact"


async def test_options_flow_updates_options(
    hass: HomeAssistant,
    rain_radar_config_entry,
) -> None:
    """Test options flow updates runtime options."""
    rain_radar_config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(
        rain_radar_config_entry.entry_id
    )
    updated = dict(VALID_INPUT)
    updated[CONF_RAIN_THRESHOLD] = 0.5
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        updated,
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert rain_radar_config_entry.options[CONF_RAIN_THRESHOLD] == 0.5
