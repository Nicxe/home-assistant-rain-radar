"""The Rain Radar integration."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
import homeassistant.helpers.config_validation as cv

from .api import RainRadarApiClient
from .const import (
    CONF_FORECAST_PROVIDER,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_PROVIDER,
    CONF_RADAR_AREA,
    CONF_RADAR_PROVIDER,
    CONF_RAIN_RISK_HORIZON_HOURS,
    CONF_RAIN_SOON_WINDOW,
    CONF_RAIN_THRESHOLD,
    CONF_SAMPLE_RADIUS_M,
    DEFAULT_CONTACT,
    DEFAULT_FORECAST_PROVIDER,
    DEFAULT_PROVIDER,
    DEFAULT_RADAR_AREA,
    DEFAULT_RADAR_PROVIDER,
    DEFAULT_RAIN_RISK_HORIZON_HOURS,
    DEFAULT_RAIN_SOON_WINDOW,
    DEFAULT_RAIN_THRESHOLD,
    DEFAULT_SAMPLE_RADIUS_M,
    DOMAIN,
    FORECAST_PROVIDER_OPTIONS,
    PLATFORMS,
    PROVIDER_DMI,
    PROVIDER_SMHI,
)
from .coordinator import RainRadarCoordinator
from .frontend import async_setup_frontend
from .providers.base import RainRadarProvider
from .providers.dmi import DmiProvider
from .providers.met_no import MetNoProvider
from .providers.models import Location, RainRadarOptions
from .providers.regnradar import RegnradarProvider
from .providers.smhi import SmhiProvider
from .views import async_register_http_views

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass(slots=True)
class RainRadarRuntimeData:
    """Runtime data for a Rain Radar config entry."""

    client: RainRadarApiClient
    provider: RegnradarProvider
    coordinator: RainRadarCoordinator
    options: RainRadarOptions


if TYPE_CHECKING:
    type RainRadarConfigEntry = ConfigEntry[RainRadarRuntimeData]
else:
    RainRadarConfigEntry = ConfigEntry


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up Rain Radar."""
    await async_setup_frontend(hass)
    async_register_http_views(hass)
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate legacy config entries."""
    if entry.version > 2:
        return False

    if entry.version < 2:
        hass.config_entries.async_update_entry(
            entry,
            data=_migrate_settings(entry.data),
            options=_migrate_settings(entry.options),
            version=2,
        )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: RainRadarConfigEntry) -> bool:
    """Set up Rain Radar from a config entry."""
    await async_setup_frontend(hass)

    options = _entry_options(hass, entry)
    location = _entry_location(hass, entry)
    client = RainRadarApiClient(hass, options.contact)
    provider = _entry_provider(client, entry)
    coordinator = RainRadarCoordinator(
        hass,
        config_entry=entry,
        provider=provider,
        location=location,
        options=options,
    )

    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception as err:
        _LOGGER.debug("First refresh failed for %s: %s", entry.title, err)
        raise ConfigEntryNotReady from err

    entry.runtime_data = RainRadarRuntimeData(
        client=client,
        provider=provider,
        coordinator=coordinator,
        options=options,
    )

    async def _options_updated(
        hass: HomeAssistant,
        updated_entry: ConfigEntry,
    ) -> None:
        await hass.config_entries.async_reload(updated_entry.entry_id)

    entry.async_on_unload(entry.add_update_listener(_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: RainRadarConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


def _entry_location(hass: HomeAssistant, entry: ConfigEntry) -> Location:
    return Location(
        latitude=float(
            entry.options.get(
                CONF_LATITUDE,
                entry.data.get(CONF_LATITUDE, hass.config.latitude),
            )
        ),
        longitude=float(
            entry.options.get(
                CONF_LONGITUDE,
                entry.data.get(CONF_LONGITUDE, hass.config.longitude),
            )
        ),
    )


def _entry_options(hass: HomeAssistant, entry: ConfigEntry) -> RainRadarOptions:
    return RainRadarOptions(
        contact=DEFAULT_CONTACT,
        forecast_provider=_entry_forecast_provider(entry),
        radar_area=str(
            entry.options.get(
                CONF_RADAR_AREA,
                entry.data.get(CONF_RADAR_AREA, DEFAULT_RADAR_AREA),
            )
        ),
        rain_threshold=float(
            entry.options.get(
                CONF_RAIN_THRESHOLD,
                entry.data.get(CONF_RAIN_THRESHOLD, DEFAULT_RAIN_THRESHOLD),
            )
        ),
        rain_soon_window_minutes=int(
            entry.options.get(
                CONF_RAIN_SOON_WINDOW,
                entry.data.get(CONF_RAIN_SOON_WINDOW, DEFAULT_RAIN_SOON_WINDOW),
            )
        ),
        sample_radius_m=int(
            entry.options.get(
                CONF_SAMPLE_RADIUS_M,
                entry.data.get(CONF_SAMPLE_RADIUS_M, DEFAULT_SAMPLE_RADIUS_M),
            )
        ),
        rain_risk_horizon_hours=int(
            entry.options.get(
                CONF_RAIN_RISK_HORIZON_HOURS,
                entry.data.get(
                    CONF_RAIN_RISK_HORIZON_HOURS,
                    DEFAULT_RAIN_RISK_HORIZON_HOURS,
                ),
            )
        ),
    )


def _entry_provider(
    client: RainRadarApiClient,
    entry: ConfigEntry,
) -> RegnradarProvider:
    return RegnradarProvider(
        client,
        forecast_provider=_forecast_provider_for(
            client, _entry_forecast_provider(entry)
        ),
    )


def _entry_forecast_provider(entry: ConfigEntry) -> str:
    value = str(
        entry.options.get(
            CONF_FORECAST_PROVIDER,
            entry.data.get(CONF_FORECAST_PROVIDER, DEFAULT_FORECAST_PROVIDER),
        )
    )
    if value in FORECAST_PROVIDER_OPTIONS:
        return value
    return DEFAULT_FORECAST_PROVIDER


def _forecast_provider_for(
    client: RainRadarApiClient,
    provider_id: str,
) -> RainRadarProvider:
    if provider_id == PROVIDER_DMI:
        return DmiProvider(client)
    if provider_id == PROVIDER_SMHI:
        return SmhiProvider(client)
    return MetNoProvider(client)


def _migrate_settings(settings) -> dict:
    migrated = dict(settings)
    legacy_provider = str(migrated.pop(CONF_PROVIDER, DEFAULT_PROVIDER))
    forecast_provider = str(
        migrated.get(CONF_FORECAST_PROVIDER, DEFAULT_FORECAST_PROVIDER)
    )
    if forecast_provider not in FORECAST_PROVIDER_OPTIONS:
        forecast_provider = DEFAULT_FORECAST_PROVIDER
    if legacy_provider not in FORECAST_PROVIDER_OPTIONS:
        legacy_provider = DEFAULT_FORECAST_PROVIDER

    migrated[CONF_FORECAST_PROVIDER] = forecast_provider or legacy_provider
    migrated[CONF_RADAR_PROVIDER] = DEFAULT_RADAR_PROVIDER
    migrated.setdefault(CONF_RADAR_AREA, DEFAULT_RADAR_AREA)
    return migrated
