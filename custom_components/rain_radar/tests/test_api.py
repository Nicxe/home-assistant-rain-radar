"""Tests for Rain Radar API helpers."""

from __future__ import annotations

import pytest

from custom_components.rain_radar import api as api_module
from custom_components.rain_radar.api import (
    RainRadarApiClient,
    RainRadarApiTemporaryError,
)
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


class _FailingRequest:
    """Async context manager that fails when the request starts."""

    async def __aenter__(self):
        raise TimeoutError

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _FailingSession:
    """Session returning a failing request context manager."""

    def get(self, *args, **kwargs):
        return _FailingRequest()


class _TemporaryFailureResponse:
    """Minimal response for temporary HTTP status tests."""

    status = 503

    async def text(self) -> str:
        return "Service Unavailable"


@pytest.mark.asyncio
async def test_api_timeout_is_temporary(hass, monkeypatch) -> None:
    """Test request timeouts are classified as temporary failures."""
    monkeypatch.setattr(
        api_module.aiohttp_client,
        "async_get_clientsession",
        lambda hass: _FailingSession(),
    )
    client = RainRadarApiClient(hass, DEFAULT_CONTACT)

    with pytest.raises(RainRadarApiTemporaryError, match="Timed out fetching"):
        await client.async_get_json("test", "https://example.com/data")


@pytest.mark.asyncio
async def test_api_server_error_is_temporary(hass) -> None:
    """Test provider server errors are classified as temporary failures."""
    client = RainRadarApiClient(hass, DEFAULT_CONTACT)

    with pytest.raises(RainRadarApiTemporaryError, match="HTTP 503"):
        await client._raise_for_status(
            _TemporaryFailureResponse(),
            "https://example.com/data",
        )
