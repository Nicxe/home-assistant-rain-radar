"""Tests for Rain Radar API helpers."""

from __future__ import annotations

from custom_components.rain_radar.api import RainRadarApiClient
from custom_components.rain_radar.const import (
    DEFAULT_CONTACT,
    PROJECT_URL,
    VERSION,
    get_user_agent,
)


def test_met_no_user_agent_uses_integration_identity() -> None:
    """Test MET Norway requests use the integration identity first."""
    assert get_user_agent() == f"home-assistant-rain-radar/{VERSION} {PROJECT_URL}"


def test_api_headers_include_contact_without_generic_home_assistant_prefix(
    hass,
) -> None:
    """Test MET Norway headers include contact and a provider-friendly User-Agent."""
    client = RainRadarApiClient(hass, DEFAULT_CONTACT)

    headers = client._headers()

    assert headers["User-Agent"] == f"home-assistant-rain-radar/{VERSION} {PROJECT_URL}"
    assert not headers["User-Agent"].startswith("HomeAssistant/")
    assert headers["From"] == DEFAULT_CONTACT


def test_api_headers_support_image_accept_header(hass) -> None:
    """Test image requests keep MET identity headers."""
    client = RainRadarApiClient(hass, DEFAULT_CONTACT)

    headers = client._headers(accept="image/png")

    assert headers["Accept"] == "image/png"
    assert headers["User-Agent"] == f"home-assistant-rain-radar/{VERSION} {PROJECT_URL}"
    assert headers["From"] == DEFAULT_CONTACT
