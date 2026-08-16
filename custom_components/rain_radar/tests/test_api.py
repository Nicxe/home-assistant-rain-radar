"""Tests for Rain Radar API helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import pytest

from custom_components.rain_radar import api as api_module
from custom_components.rain_radar.api import (
    RainRadarApiAuthError,
    RainRadarApiClient,
    RainRadarApiRateLimitedError,
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
    headers = {}

    async def text(self) -> str:
        return "Service Unavailable"


class _Response:
    """Minimal aiohttp response test double."""

    def __init__(
        self,
        status: int,
        *,
        headers: dict[str, str] | None = None,
        payload=None,
        body: str = "",
    ) -> None:
        self.status = status
        self.headers = headers or {}
        self._payload = payload
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def json(self):
        return self._payload

    async def text(self) -> str:
        return self._body


class _SequenceSession:
    """Session returning responses in order."""

    def __init__(self, *responses: _Response) -> None:
        self.responses = list(responses)

    def get(self, *args, **kwargs):
        return self.responses.pop(0)


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


@pytest.mark.asyncio
async def test_api_rate_limit_exposes_retry_after_seconds(hass) -> None:
    """Test Retry-After delta seconds are retained on rate-limit errors."""
    client = RainRadarApiClient(hass, DEFAULT_CONTACT)
    response = _Response(429, headers={"Retry-After": "900"})

    with pytest.raises(RainRadarApiRateLimitedError) as error:
        await client._raise_for_status(response, "https://example.com/data")

    assert error.value.retry_after == 900


@pytest.mark.asyncio
async def test_api_rate_limit_parses_retry_after_http_date(hass) -> None:
    """Test Retry-After HTTP dates are converted to a delay."""
    client = RainRadarApiClient(hass, DEFAULT_CONTACT)
    retry_at = datetime.now(UTC) + timedelta(minutes=10)
    response = _Response(429, headers={"Retry-After": format_datetime(retry_at)})

    with pytest.raises(RainRadarApiRateLimitedError) as error:
        await client._raise_for_status(response, "https://example.com/data")

    assert error.value.retry_after is not None
    assert 590 <= error.value.retry_after <= 600


@pytest.mark.asyncio
async def test_api_keyless_forbidden_is_treated_as_rate_limit(hass) -> None:
    """Test a keyless provider rejection does not trigger reauthentication."""
    client = RainRadarApiClient(hass, DEFAULT_CONTACT)
    response = _Response(403, body="Request blocked by fair-use policy")

    with pytest.raises(RainRadarApiRateLimitedError):
        await client._raise_for_status(
            response,
            "https://example.com/data",
            auth_required=False,
        )


@pytest.mark.asyncio
async def test_api_authenticated_forbidden_remains_auth_failure(hass) -> None:
    """Test authenticated providers retain their existing reauth behavior."""
    client = RainRadarApiClient(hass, DEFAULT_CONTACT)
    response = _Response(403, body="Invalid credentials")

    with pytest.raises(RainRadarApiAuthError):
        await client._raise_for_status(response, "https://example.com/data")


@pytest.mark.asyncio
async def test_api_304_does_not_reuse_an_expired_expiry(
    hass,
    monkeypatch,
) -> None:
    """Test a validated response gets a usable fallback freshness timestamp."""
    session = _SequenceSession(
        _Response(
            200,
            headers={"Cache-Control": "max-age=0", "ETag": '"forecast-1"'},
            payload={"value": 1},
        ),
        _Response(304),
    )
    monkeypatch.setattr(
        api_module.aiohttp_client,
        "async_get_clientsession",
        lambda hass: session,
    )
    client = RainRadarApiClient(hass, DEFAULT_CONTACT)

    await client.async_get_json("forecast", "https://example.com/data")
    payload, metadata = await client.async_get_json(
        "forecast", "https://example.com/data"
    )

    assert payload == {"value": 1}
    assert metadata.from_cache is True
    assert metadata.fetched_at is not None
    assert metadata.expires_at is None
    assert metadata.etag == '"forecast-1"'
