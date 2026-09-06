"""Tests for Rain Radar API helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import ClassVar

from aiohttp import ClientConnectionError, ContentTypeError
import pytest

from custom_components.rain_radar import api as api_module
from custom_components.rain_radar.api import (
    RainRadarApiAuthError,
    RainRadarApiClient,
    RainRadarApiError,
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

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error if error is not None else TimeoutError()

    async def __aenter__(self):
        raise self.error

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _FailingSession:
    """Session returning a failing request context manager."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    def get(self, *args, **kwargs):
        return _FailingRequest(self.error)


class _TemporaryFailureResponse:
    """Minimal response for temporary HTTP status tests."""

    status = 503
    headers: ClassVar[dict[str, str]] = {}

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
        json_error: Exception | None = None,
    ) -> None:
        self.status = status
        self.headers = headers or {}
        self._payload = payload
        self._body = body
        self._json_error = json_error

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._payload

    async def read(self):
        return self._payload

    async def text(self) -> str:
        return self._body


class _SequenceSession:
    """Session returning responses in order."""

    def __init__(self, *responses: _Response) -> None:
        self.responses = list(responses)
        self.request_headers: list[dict[str, str]] = []

    def get(self, *args, **kwargs):
        self.request_headers.append(kwargs.get("headers", {}))
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

    with pytest.raises(RainRadarApiTemporaryError, match="Timed out fetching") as error:
        await client.async_get_json("test", "https://example.com/data")

    assert error.value.reason == "timeout"
    assert error.value.status_code is None


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
    assert error.value.status_code == 429
    assert error.value.reason == "rate_limited"


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (
            '{"status":429,"message":"Server is busy. Please try again later."}',
            "server_busy",
        ),
        ('{"status":429,"message":"Rate limit exceeded"}', "rate_limited"),
        ("Service busy", "rate_limited"),
    ],
)
@pytest.mark.asyncio
async def test_api_distinguishes_dmi_server_busy_from_quota(hass, body, reason) -> None:
    """Recognize the documented live busy response without inventing a quota cause."""
    client = RainRadarApiClient(hass, DEFAULT_CONTACT)

    with pytest.raises(RainRadarApiRateLimitedError) as error:
        await client._raise_for_status(
            _Response(429, body=body), "https://example.com/data"
        )

    assert error.value.status_code == 429
    assert error.value.reason == reason


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


@pytest.mark.parametrize("status", [408, 425, 500, 502, 503, 504])
@pytest.mark.asyncio
async def test_api_temporary_http_errors_preserve_retry_and_status(
    hass, status
) -> None:
    """Respect server retry instructions for every temporary HTTP status."""
    client = RainRadarApiClient(hass, DEFAULT_CONTACT)
    response = _Response(status, headers={"Retry-After": "1200"})

    with pytest.raises(RainRadarApiTemporaryError) as error:
        await client._raise_for_status(response, "https://example.com/data")

    assert error.value.retry_after == 1200
    assert error.value.status_code == status
    assert error.value.reason == ("server_error" if status >= 500 else "http_error")


@pytest.mark.parametrize("status", [401, 403])
@pytest.mark.asyncio
async def test_api_keyless_rejection_has_distinct_safe_reason(hass, status) -> None:
    """Do not report an access rejection as a confirmed HTTP rate limit."""
    client = RainRadarApiClient(hass, DEFAULT_CONTACT)
    response = _Response(status, headers={"Retry-After": "180"})

    with pytest.raises(RainRadarApiRateLimitedError) as error:
        await client._raise_for_status(
            response, "https://example.com/data", auth_required=False
        )

    assert error.value.retry_after == 180
    assert error.value.status_code == status
    assert error.value.reason == "request_rejected"


@pytest.mark.asyncio
async def test_api_other_http_failure_preserves_status(hass) -> None:
    """Keep permanent request errors distinguishable from transport failures."""
    client = RainRadarApiClient(hass, DEFAULT_CONTACT)

    with pytest.raises(RainRadarApiError) as error:
        await client._raise_for_status(_Response(400), "https://example.com/data")

    assert error.value.status_code == 400
    assert error.value.reason == "http_error"
    assert not isinstance(error.value, RainRadarApiTemporaryError)


@pytest.mark.parametrize(
    "json_error",
    [
        ValueError("Invalid JSON"),
        ContentTypeError(None, (), status=200, message="Unexpected content type"),
    ],
)
@pytest.mark.asyncio
async def test_api_invalid_json_is_temporary(hass, monkeypatch, json_error) -> None:
    """Keep malformed JSON and content-type errors inside the retry boundary."""
    session = _SequenceSession(_Response(200, json_error=json_error))
    monkeypatch.setattr(
        api_module.aiohttp_client, "async_get_clientsession", lambda hass: session
    )
    client = RainRadarApiClient(hass, DEFAULT_CONTACT)

    with pytest.raises(RainRadarApiTemporaryError) as error:
        await client.async_get_json("forecast", "https://example.com/data")

    assert error.value.reason == "invalid_response"
    assert error.value.status_code == 200
    assert "forecast" not in client._cache


@pytest.mark.parametrize("method", ["async_get_json", "async_get_bytes"])
@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (TimeoutError(), "timeout"),
        (ClientConnectionError("Connection lost"), "network"),
    ],
)
@pytest.mark.asyncio
async def test_api_transport_errors_have_safe_reason(
    hass, monkeypatch, method, failure, reason
) -> None:
    """Expose stable transport failure reasons for JSON and binary requests."""
    monkeypatch.setattr(
        api_module.aiohttp_client,
        "async_get_clientsession",
        lambda hass: _FailingSession(failure),
    )
    client = RainRadarApiClient(hass, DEFAULT_CONTACT)

    with pytest.raises(RainRadarApiTemporaryError) as error:
        await getattr(client, method)("forecast", "https://example.com/data")

    assert error.value.reason == reason
    assert error.value.status_code is None
    assert error.value.retry_after is None


@pytest.mark.parametrize(
    "value",
    [
        "nan",
        "inf",
        "-inf",
        "1e999",
        "1e100",
        "invalid",
        "Fri, 31 Dec 99999 23:59:59 GMT",
    ],
)
def test_api_retry_after_ignores_invalid_or_unrepresentable_delays(value) -> None:
    """Untrusted retry headers must never overflow provider backoff arithmetic."""
    assert api_module._parse_retry_after(value) is None


@pytest.mark.parametrize("method", ["async_get_json", "async_get_bytes"])
@pytest.mark.asyncio
async def test_api_304_retains_request_cache_snapshot(
    hass, monkeypatch, method
) -> None:
    """A concurrent cache eviction must not invalidate an in-flight validator."""
    client = RainRadarApiClient(hass, DEFAULT_CONTACT)

    class _EvictingResponse(_Response):
        async def __aenter__(self):
            client._cache.clear()
            return self

    payload = {"value": 1} if method == "async_get_json" else b"image-data"
    session = _SequenceSession(
        _Response(
            200,
            payload=payload,
            headers={"ETag": '"forecast-1"', "Content-Type": "image/png"},
        ),
        _EvictingResponse(304),
    )
    monkeypatch.setattr(
        api_module.aiohttp_client, "async_get_clientsession", lambda hass: session
    )

    await getattr(client, method)("forecast", "https://example.com/data")
    result = await getattr(client, method)("forecast", "https://example.com/data")

    assert result[0] == payload
    assert result[1].from_cache is True
    assert result[1].etag == '"forecast-1"'
    assert session.request_headers[1]["If-None-Match"] == '"forecast-1"'
    if method == "async_get_bytes":
        assert result[2] == "image/png"


def test_api_error_constructors_remain_backward_compatible() -> None:
    """Existing provider errors can omit the new structured diagnostics."""
    error = RainRadarApiError("Request failed")
    temporary = RainRadarApiTemporaryError("Try later", retry_after=30)
    rate_limited = RainRadarApiRateLimitedError("Rate limited", retry_after=60)

    assert str(error) == "Request failed"
    assert error.reason is None
    assert error.status_code is None
    assert temporary.retry_after == 30
    assert rate_limited.retry_after == 60


@pytest.mark.parametrize("method", ["async_get_json", "async_get_bytes"])
async def test_fresh_http_cache_avoids_network(hass, monkeypatch, method):
    """Do not contact providers before advertised expiry."""
    payload = {"value": 1} if method == "async_get_json" else b"image"
    session = _SequenceSession(
        _Response(200, payload=payload, headers={"Cache-Control": "max-age=3600"})
    )
    monkeypatch.setattr(
        api_module.aiohttp_client, "async_get_clientsession", lambda hass: session
    )
    client = RainRadarApiClient(hass, DEFAULT_CONTACT)
    first = await getattr(client, method)("cache", "https://example.com/data")
    second = await getattr(client, method)("cache", "https://example.com/data")
    assert first[0] == second[0]
    assert second[1].from_cache
    assert len(session.request_headers) == 1


async def test_concurrent_failures_share_request_and_backoff(hass, monkeypatch):
    """Parallel and subsequent consumers respect the same failure retry time."""
    import asyncio

    session = _SequenceSession(_Response(503, headers={"Retry-After": "600"}))
    monkeypatch.setattr(
        api_module.aiohttp_client, "async_get_clientsession", lambda hass: session
    )
    client = RainRadarApiClient(hass, DEFAULT_CONTACT)
    results = await asyncio.gather(
        *(client.async_get_json("cache", "https://example.com/data") for _ in range(3)),
        return_exceptions=True,
    )
    assert all(isinstance(result, RainRadarApiTemporaryError) for result in results)
    with pytest.raises(RainRadarApiTemporaryError):
        await client.async_get_json("cache", "https://example.com/data")
    assert len(session.request_headers) == 1


@pytest.mark.parametrize(
    "headers",
    [
        {"Cache-Control": "no-cache, max-age=3600"},
        {"Cache-Control": "max-age=60", "Age": "120"},
    ],
)
async def test_expired_or_revalidation_required_cache_calls_provider(
    hass, monkeypatch, headers
):
    """Respect intermediaries' Age and mandatory revalidation."""
    session = _SequenceSession(
        _Response(200, payload={"value": 1}, headers=headers),
        _Response(200, payload={"value": 2}),
    )
    monkeypatch.setattr(
        api_module.aiohttp_client, "async_get_clientsession", lambda hass: session
    )
    client = RainRadarApiClient(hass, DEFAULT_CONTACT)
    await client.async_get_json("cache", "https://example.com/data")
    result = await client.async_get_json("cache", "https://example.com/data")
    assert result[0] == {"value": 2}
    assert len(session.request_headers) == 2


async def test_concurrent_successes_without_expiry_share_request(hass, monkeypatch):
    """Concurrent consumers of an uncacheable result still use one request."""
    import asyncio

    session = _SequenceSession(_Response(200, payload={"value": 1}))
    monkeypatch.setattr(
        api_module.aiohttp_client, "async_get_clientsession", lambda hass: session
    )
    client = RainRadarApiClient(hass, DEFAULT_CONTACT)
    results = await asyncio.gather(
        *(client.async_get_json("cache", "https://example.com/data") for _ in range(3))
    )
    assert all(result[0] == {"value": 1} for result in results)
    assert len(session.request_headers) == 1


async def test_no_store_does_not_retain_payload_or_validators(hass, monkeypatch):
    """A provider's no-store response is not retained after the request."""
    session = _SequenceSession(
        _Response(
            200,
            payload={"value": 1},
            headers={"Cache-Control": "no-store", "ETag": "secret"},
        )
    )
    monkeypatch.setattr(
        api_module.aiohttp_client, "async_get_clientsession", lambda hass: session
    )
    client = RainRadarApiClient(hass, DEFAULT_CONTACT)
    await client.async_get_json("cache", "https://example.com/data")
    assert "cache" not in client._cache
    assert "If-None-Match" not in client._headers("cache")
