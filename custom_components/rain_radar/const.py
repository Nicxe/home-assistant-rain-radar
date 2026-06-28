"""Constants for Rain Radar."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.const import Platform

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

DOMAIN = "rain_radar"
NAME = "Rain Radar"
VERSION = "0.0.0"
PROJECT_URL = "https://github.com/Nicxe/home-assistant-rain-radar"

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]

CONF_CONTACT = "contact"
CONF_LATITUDE = "latitude"
CONF_LONGITUDE = "longitude"
CONF_NAME = "name"
CONF_PROVIDER = "provider"
CONF_RAIN_RISK_HORIZON_HOURS = "rain_risk_horizon_hours"
CONF_RAIN_SOON_WINDOW = "rain_soon_window"
CONF_RAIN_THRESHOLD = "rain_threshold"
CONF_SAMPLE_RADIUS_M = "sample_radius_m"

PROVIDER_MET_NO = "met_no"
PROVIDER_OPTIONS = [PROVIDER_MET_NO]

DEFAULT_NAME = "Home"
DEFAULT_CONTACT = ""
DEFAULT_PROVIDER = PROVIDER_MET_NO
DEFAULT_RAIN_THRESHOLD = 0.1
DEFAULT_RAIN_SOON_WINDOW = 60
DEFAULT_SAMPLE_RADIUS_M = 1000
DEFAULT_RAIN_RISK_HORIZON_HOURS = 12
DEFAULT_UPDATE_INTERVAL = timedelta(minutes=5)

MIN_RAIN_SOON_WINDOW = 5
MAX_RAIN_SOON_WINDOW = 120
MIN_SAMPLE_RADIUS_M = 0
MAX_SAMPLE_RADIUS_M = 10000
MIN_RAIN_RISK_HORIZON_HOURS = 1
MAX_RAIN_RISK_HORIZON_HOURS = 24

MET_NO_ATTRIBUTION = "Data from MET Norway"
MET_NO_LOCATIONFORECAST_COMPLETE_URL = (
    "https://api.met.no/weatherapi/locationforecast/2.0/complete"
)
MET_NO_NOWCAST_COMPLETE_URL = "https://api.met.no/weatherapi/nowcast/2.0/complete"
MET_NO_RADAR_AVAILABLE_URL = "https://api.met.no/weatherapi/radar/2.0/available.json"
MET_NO_RADAR_ANIMATION_URL = (
    "https://api.met.no/weatherapi/radar/2.0/"
    "?type=reflectivity&area=nordic&content=animation"
)

CARD_FILENAME = "rain-radar-card.js"
CARD_WWW_DIR = "www"
CARD_STATIC_BASE_PATH = f"/{DOMAIN}-static"
CARD_CANONICAL_BASE_URL = f"{CARD_STATIC_BASE_PATH}/{CARD_FILENAME}"
CARD_LEGACY_BASE_URL = f"/local/{CARD_FILENAME}"
FRONTEND_DATA_COMPONENT_LISTENER = f"{DOMAIN}_component_listener"
FRONTEND_DATA_KEY = f"{DOMAIN}_frontend"

ATTRIBUTION = "attribution"
ATTR_ENTRY_ID = "rain_radar_entry_id"
ATTR_FORECAST_SAMPLES = "forecast_samples"
ATTR_HOURLY = "hourly"
ATTR_IS_STALE = "is_stale"
ATTR_LAST_UPDATED = "last_updated"
ATTR_PROVIDER = "provider"
ATTR_PROVIDER_ID = "provider_id"
ATTR_STATUS = "status"


def get_user_agent(hass: HomeAssistant | None = None) -> str:
    """Generate the MET Norway User-Agent header."""
    return f"home-assistant-rain-radar/{VERSION} {PROJECT_URL}"
