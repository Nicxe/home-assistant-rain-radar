"""Tests for Rain Radar config flow."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rain_radar import async_migrate_entry
from custom_components.rain_radar.const import (
    CONF_CONTACT,
    CONF_FORECAST_PROVIDER,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_NAME,
    CONF_PROVIDER,
    CONF_RADAR_AREA,
    CONF_RADAR_PROVIDER,
    CONF_RAIN_RISK_HORIZON_HOURS,
    CONF_RAIN_SOON_WINDOW,
    CONF_RAIN_THRESHOLD,
    CONF_SAMPLE_RADIUS_M,
    DEFAULT_CONTACT,
    DEFAULT_FORECAST_PROVIDER,
    DEFAULT_RADAR_AREA,
    DEFAULT_RADAR_PROVIDER,
    DOMAIN,
    PROVIDER_DMI,
    PROVIDER_MET_NO,
    PROVIDER_SMHI,
)

VALID_INPUT = {
    CONF_NAME: "Home",
    CONF_LATITUDE: 59.3293,
    CONF_LONGITUDE: 18.0686,
    CONF_FORECAST_PROVIDER: DEFAULT_FORECAST_PROVIDER,
    CONF_RADAR_AREA: DEFAULT_RADAR_AREA,
    CONF_RAIN_THRESHOLD: 0.1,
    CONF_RAIN_SOON_WINDOW: 60,
    CONF_SAMPLE_RADIUS_M: 1000,
    CONF_RAIN_RISK_HORIZON_HOURS: 12,
}


def _schema_keys(result) -> set[str]:
    return {key.schema for key in result["data_schema"].schema}


async def test_user_step_shows_form(hass: HomeAssistant) -> None:
    """Test config flow shows user form."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert CONF_CONTACT not in _schema_keys(result)
    assert CONF_PROVIDER not in _schema_keys(result)
    assert CONF_FORECAST_PROVIDER in _schema_keys(result)
    assert CONF_RAIN_THRESHOLD in _schema_keys(result)


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
    assert result["data"][CONF_FORECAST_PROVIDER] == DEFAULT_FORECAST_PROVIDER
    assert result["data"][CONF_RADAR_PROVIDER] == DEFAULT_RADAR_PROVIDER
    assert result["data"][CONF_RADAR_AREA] == DEFAULT_RADAR_AREA
    assert result["data"][CONF_CONTACT] == DEFAULT_CONTACT
    assert result["data"][CONF_RAIN_RISK_HORIZON_HOURS] == 12


async def test_user_step_accepts_radar_area_with_forecast_provider(
    hass: HomeAssistant,
) -> None:
    """Test radar area can be selected while Regnradar stays the radar provider."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    user_input = dict(VALID_INPUT)
    user_input[CONF_FORECAST_PROVIDER] = PROVIDER_MET_NO
    user_input[CONF_RADAR_AREA] = "sweden"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input,
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_FORECAST_PROVIDER] == PROVIDER_MET_NO
    assert result["data"][CONF_RADAR_PROVIDER] == DEFAULT_RADAR_PROVIDER
    assert result["data"][CONF_RADAR_AREA] == "sweden"


async def test_user_step_accepts_smhi_forecast_provider(
    hass: HomeAssistant,
) -> None:
    """Test SMHI can be selected for forecast sensors while radar stays Regnradar."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    user_input = dict(VALID_INPUT)
    user_input[CONF_FORECAST_PROVIDER] = PROVIDER_SMHI
    user_input[CONF_RADAR_AREA] = "sweden"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input,
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_FORECAST_PROVIDER] == PROVIDER_SMHI
    assert result["data"][CONF_RADAR_PROVIDER] == DEFAULT_RADAR_PROVIDER
    assert result["data"][CONF_RADAR_AREA] == "sweden"


async def test_user_step_accepts_dmi_forecast_provider(
    hass: HomeAssistant,
) -> None:
    """Test DMI can be selected for forecast sensors while radar stays Regnradar."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    user_input = dict(VALID_INPUT)
    user_input[CONF_FORECAST_PROVIDER] = PROVIDER_DMI
    user_input[CONF_RADAR_AREA] = "denmark"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input,
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_FORECAST_PROVIDER] == PROVIDER_DMI
    assert result["data"][CONF_RADAR_PROVIDER] == DEFAULT_RADAR_PROVIDER
    assert result["data"][CONF_RADAR_AREA] == "denmark"


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


async def test_options_flow_updates_options(
    hass: HomeAssistant,
    rain_radar_config_entry,
) -> None:
    """Test options flow updates runtime options."""
    rain_radar_config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(
        rain_radar_config_entry.entry_id
    )
    assert CONF_CONTACT not in _schema_keys(result)
    updated = dict(VALID_INPUT)
    updated[CONF_RAIN_THRESHOLD] = 0.5
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        updated,
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert rain_radar_config_entry.options[CONF_RAIN_THRESHOLD] == 0.5


async def test_migrate_entry_splits_legacy_provider(
    hass: HomeAssistant,
) -> None:
    """Test legacy provider config is migrated into radar and forecast roles."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Home",
        data={
            CONF_NAME: "Home",
            CONF_LATITUDE: 59.3293,
            CONF_LONGITUDE: 18.0686,
            CONF_PROVIDER: "regnradar",
            CONF_RAIN_THRESHOLD: 0.1,
            CONF_RAIN_SOON_WINDOW: 60,
            CONF_SAMPLE_RADIUS_M: 1000,
            CONF_RAIN_RISK_HORIZON_HOURS: 12,
        },
        options={CONF_PROVIDER: "met_no", CONF_RADAR_AREA: "denmark"},
        version=1,
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry) is True

    assert entry.version == 2
    assert CONF_PROVIDER not in entry.data
    assert CONF_PROVIDER not in entry.options
    assert entry.data[CONF_RADAR_PROVIDER] == DEFAULT_RADAR_PROVIDER
    assert entry.data[CONF_FORECAST_PROVIDER] == DEFAULT_FORECAST_PROVIDER
    assert entry.options[CONF_RADAR_PROVIDER] == DEFAULT_RADAR_PROVIDER
    assert entry.options[CONF_FORECAST_PROVIDER] == DEFAULT_FORECAST_PROVIDER
    assert entry.options[CONF_RADAR_AREA] == "denmark"
