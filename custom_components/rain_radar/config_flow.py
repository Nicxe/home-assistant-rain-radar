"""Config flow for Rain Radar."""

from __future__ import annotations

from typing import Any

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.selector import selector
import voluptuous as vol

from .const import (
    CONF_CONTACT,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_NAME,
    CONF_PROVIDER,
    CONF_RAIN_RISK_HORIZON_HOURS,
    CONF_RAIN_SOON_WINDOW,
    CONF_RAIN_THRESHOLD,
    CONF_SAMPLE_RADIUS_M,
    DEFAULT_CONTACT,
    DEFAULT_NAME,
    DEFAULT_PROVIDER,
    DEFAULT_RAIN_RISK_HORIZON_HOURS,
    DEFAULT_RAIN_SOON_WINDOW,
    DEFAULT_RAIN_THRESHOLD,
    DEFAULT_SAMPLE_RADIUS_M,
    DOMAIN,
    MAX_RAIN_RISK_HORIZON_HOURS,
    MAX_RAIN_SOON_WINDOW,
    MAX_SAMPLE_RADIUS_M,
    MIN_RAIN_RISK_HORIZON_HOURS,
    MIN_RAIN_SOON_WINDOW,
    MIN_SAMPLE_RADIUS_M,
    PROVIDER_OPTIONS,
)


def _round_coord(value: float) -> float:
    return round(float(value), 4)


def _is_valid_contact(contact: str) -> bool:
    contact = contact.strip()
    if not contact:
        return False
    return ("@" in contact and "." in contact) or contact.startswith(
        ("http://", "https://")
    )


def _validate_input(user_input: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}

    try:
        latitude = float(user_input[CONF_LATITUDE])
    except (KeyError, TypeError, ValueError):
        errors[CONF_LATITUDE] = "invalid_latitude"
    else:
        if not -90 <= latitude <= 90:
            errors[CONF_LATITUDE] = "invalid_latitude"

    try:
        longitude = float(user_input[CONF_LONGITUDE])
    except (KeyError, TypeError, ValueError):
        errors[CONF_LONGITUDE] = "invalid_longitude"
    else:
        if not -180 <= longitude <= 180:
            errors[CONF_LONGITUDE] = "invalid_longitude"

    provider = str(user_input.get(CONF_PROVIDER, DEFAULT_PROVIDER))
    if provider not in PROVIDER_OPTIONS:
        errors[CONF_PROVIDER] = "unsupported_provider"

    contact = str(user_input.get(CONF_CONTACT, "")).strip()
    if not _is_valid_contact(contact):
        errors[CONF_CONTACT] = "invalid_contact"

    try:
        rain_threshold = float(user_input[CONF_RAIN_THRESHOLD])
    except (KeyError, TypeError, ValueError):
        errors[CONF_RAIN_THRESHOLD] = "invalid_rain_threshold"
    else:
        if rain_threshold < 0:
            errors[CONF_RAIN_THRESHOLD] = "invalid_rain_threshold"

    try:
        rain_soon_window = int(user_input[CONF_RAIN_SOON_WINDOW])
    except (KeyError, TypeError, ValueError):
        errors[CONF_RAIN_SOON_WINDOW] = "invalid_rain_soon_window"
    else:
        if not MIN_RAIN_SOON_WINDOW <= rain_soon_window <= MAX_RAIN_SOON_WINDOW:
            errors[CONF_RAIN_SOON_WINDOW] = "invalid_rain_soon_window"

    try:
        sample_radius = int(user_input[CONF_SAMPLE_RADIUS_M])
    except (KeyError, TypeError, ValueError):
        errors[CONF_SAMPLE_RADIUS_M] = "invalid_sample_radius"
    else:
        if not MIN_SAMPLE_RADIUS_M <= sample_radius <= MAX_SAMPLE_RADIUS_M:
            errors[CONF_SAMPLE_RADIUS_M] = "invalid_sample_radius"

    try:
        horizon = int(user_input[CONF_RAIN_RISK_HORIZON_HOURS])
    except (KeyError, TypeError, ValueError):
        errors[CONF_RAIN_RISK_HORIZON_HOURS] = "invalid_rain_risk_horizon"
    else:
        if not MIN_RAIN_RISK_HORIZON_HOURS <= horizon <= MAX_RAIN_RISK_HORIZON_HOURS:
            errors[CONF_RAIN_RISK_HORIZON_HOURS] = "invalid_rain_risk_horizon"

    return errors


def _normalized_data(user_input: dict[str, Any]) -> dict[str, Any]:
    return {
        CONF_NAME: str(user_input.get(CONF_NAME, DEFAULT_NAME)).strip() or DEFAULT_NAME,
        CONF_LATITUDE: float(user_input[CONF_LATITUDE]),
        CONF_LONGITUDE: float(user_input[CONF_LONGITUDE]),
        CONF_PROVIDER: str(user_input.get(CONF_PROVIDER, DEFAULT_PROVIDER)),
        CONF_CONTACT: str(user_input[CONF_CONTACT]).strip(),
        CONF_RAIN_THRESHOLD: float(user_input[CONF_RAIN_THRESHOLD]),
        CONF_RAIN_SOON_WINDOW: int(user_input[CONF_RAIN_SOON_WINDOW]),
        CONF_SAMPLE_RADIUS_M: int(user_input[CONF_SAMPLE_RADIUS_M]),
        CONF_RAIN_RISK_HORIZON_HOURS: int(user_input[CONF_RAIN_RISK_HORIZON_HOURS]),
    }


def _schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=defaults[CONF_NAME]): str,
            vol.Required(CONF_LATITUDE, default=defaults[CONF_LATITUDE]): vol.Coerce(
                float
            ),
            vol.Required(CONF_LONGITUDE, default=defaults[CONF_LONGITUDE]): vol.Coerce(
                float
            ),
            vol.Required(CONF_PROVIDER, default=defaults[CONF_PROVIDER]): selector(
                {
                    "select": {
                        "options": [{"value": DEFAULT_PROVIDER, "label": "MET Norway"}],
                        "mode": "dropdown",
                    }
                }
            ),
            vol.Required(CONF_CONTACT, default=defaults[CONF_CONTACT]): str,
            vol.Required(
                CONF_RAIN_THRESHOLD,
                default=defaults[CONF_RAIN_THRESHOLD],
            ): vol.Coerce(float),
            vol.Required(
                CONF_RAIN_SOON_WINDOW,
                default=defaults[CONF_RAIN_SOON_WINDOW],
            ): vol.Coerce(int),
            vol.Required(
                CONF_SAMPLE_RADIUS_M,
                default=defaults[CONF_SAMPLE_RADIUS_M],
            ): vol.Coerce(int),
            vol.Required(
                CONF_RAIN_RISK_HORIZON_HOURS,
                default=defaults[CONF_RAIN_RISK_HORIZON_HOURS],
            ): vol.Coerce(int),
        }
    )


class RainRadarConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Rain Radar."""

    VERSION = 1

    def __init__(self) -> None:
        self._pending_title: str | None = None
        self._pending_data: dict[str, Any] | None = None

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Return options flow."""
        return RainRadarOptionsFlow(config_entry)

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            errors = _validate_input(user_input)
            if not errors:
                data = _normalized_data(user_input)
                unique_id = (
                    f"{data[CONF_PROVIDER]}:"
                    f"{_round_coord(data[CONF_LATITUDE])},"
                    f"{_round_coord(data[CONF_LONGITUDE])}"
                )
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                self._pending_title = data[CONF_NAME]
                self._pending_data = data
                return self.async_show_form(
                    step_id="reload_notice",
                    data_schema=vol.Schema({}),
                )

        defaults = {
            CONF_NAME: DEFAULT_NAME,
            CONF_LATITUDE: self.hass.config.latitude,
            CONF_LONGITUDE: self.hass.config.longitude,
            CONF_PROVIDER: DEFAULT_PROVIDER,
            CONF_CONTACT: DEFAULT_CONTACT,
            CONF_RAIN_THRESHOLD: DEFAULT_RAIN_THRESHOLD,
            CONF_RAIN_SOON_WINDOW: DEFAULT_RAIN_SOON_WINDOW,
            CONF_SAMPLE_RADIUS_M: DEFAULT_SAMPLE_RADIUS_M,
            CONF_RAIN_RISK_HORIZON_HOURS: DEFAULT_RAIN_RISK_HORIZON_HOURS,
        }
        return self.async_show_form(
            step_id="user",
            data_schema=_schema(defaults),
            errors=errors,
        )

    async def async_step_reload_notice(self, user_input: dict[str, Any] | None = None):
        """Show final browser reload notice before entry creation."""
        if user_input is None:
            return self.async_show_form(
                step_id="reload_notice",
                data_schema=vol.Schema({}),
            )

        if self._pending_title is None or self._pending_data is None:
            return self.async_abort(reason="unknown")

        title = self._pending_title
        data = self._pending_data
        self._pending_title = None
        self._pending_data = None
        return self.async_create_entry(title=title, data=data)


class RainRadarOptionsFlow(config_entries.OptionsFlow):
    """Handle Rain Radar options."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        """Handle options step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_input(user_input)
            if not errors:
                return self.async_create_entry(
                    title="", data=_normalized_data(user_input)
                )

        defaults = {
            CONF_NAME: self._get(CONF_NAME, DEFAULT_NAME),
            CONF_LATITUDE: self._get(CONF_LATITUDE, self.hass.config.latitude),
            CONF_LONGITUDE: self._get(CONF_LONGITUDE, self.hass.config.longitude),
            CONF_PROVIDER: self._get(CONF_PROVIDER, DEFAULT_PROVIDER),
            CONF_CONTACT: self._get(CONF_CONTACT, DEFAULT_CONTACT),
            CONF_RAIN_THRESHOLD: self._get(
                CONF_RAIN_THRESHOLD,
                DEFAULT_RAIN_THRESHOLD,
            ),
            CONF_RAIN_SOON_WINDOW: self._get(
                CONF_RAIN_SOON_WINDOW,
                DEFAULT_RAIN_SOON_WINDOW,
            ),
            CONF_SAMPLE_RADIUS_M: self._get(
                CONF_SAMPLE_RADIUS_M,
                DEFAULT_SAMPLE_RADIUS_M,
            ),
            CONF_RAIN_RISK_HORIZON_HOURS: self._get(
                CONF_RAIN_RISK_HORIZON_HOURS,
                DEFAULT_RAIN_RISK_HORIZON_HOURS,
            ),
        }
        return self.async_show_form(
            step_id="init",
            data_schema=_schema(defaults),
            errors=errors,
        )

    def _get(self, key: str, default: Any) -> Any:
        return self._config_entry.options.get(
            key,
            self._config_entry.data.get(key, default),
        )
