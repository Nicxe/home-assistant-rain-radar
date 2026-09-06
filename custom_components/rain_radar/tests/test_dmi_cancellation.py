"""DMI single-flight requests survive consumer cancellation and entry reload."""

import asyncio
from datetime import UTC, datetime

import pytest

from custom_components.rain_radar import api as api_module
from custom_components.rain_radar.api import RainRadarApiClient
from custom_components.rain_radar.providers.dmi import DmiProvider
from custom_components.rain_radar.providers.models import CoverageStatus, Location

from .test_api import _Response
from .test_dmi_provider import MutableClock, _options, _payload


@pytest.mark.parametrize("status", [200, 429])
@pytest.mark.parametrize("same_location", [True, False])
async def test_cancelled_consumer_keeps_shared_fetch_and_global_limit(
    hass, monkeypatch, status, same_location
):
    """Reload neither duplicates the old request nor bypasses its global backoff."""
    now = datetime.now(UTC)
    clock = MutableClock(now)
    started = asyncio.Event()
    second_started = asyncio.Event()
    release = asyncio.Event()
    request_count = 0
    active = 0
    maximum_active = 0

    class DelayedResponse(_Response):
        async def __aenter__(self):
            nonlocal request_count, active, maximum_active
            request_count += 1
            active += 1
            maximum_active = max(maximum_active, active)
            started.set()
            if request_count > 1:
                second_started.set()
            await release.wait()
            return self

        async def __aexit__(self, *args):
            nonlocal active
            active -= 1
            return False

    class Session:
        def get(self, *args, **kwargs):
            return DelayedResponse(
                status,
                payload=_payload(now),
                headers={"Retry-After": "1800"} if status == 429 else {},
                body='{"message":"Server is busy. Please try again later."}',
            )

    monkeypatch.setattr(
        api_module.aiohttp_client, "async_get_clientsession", lambda hass: Session()
    )
    old = DmiProvider(RainRadarApiClient(hass, ""), now_fn=clock)
    first = asyncio.create_task(
        old.async_get_precipitation_forecast(Location(59, 18), _options())
    )
    await started.wait()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    reloaded = DmiProvider(RainRadarApiClient(hass, ""), now_fn=clock)
    location = Location(59, 18) if same_location else Location(60, 17)
    second = asyncio.create_task(reloaded.async_get_rain_risk(location, _options()))
    try:
        async with asyncio.timeout(0.01):
            await second_started.wait()
    except TimeoutError:
        pass
    release.set()
    result = await second
    await hass.async_block_till_done(wait_background_tasks=True)

    assert maximum_active == 1
    assert request_count == (2 if status == 200 and not same_location else 1)
    assert reloaded.diagnostics["request_count"] == request_count
    assert not reloaded._request_manager.inflight
    if status == 200:
        assert result.max_probability == 100
        assert result.coverage_status == CoverageStatus.OK
        assert reloaded.last_success == now
        assert reloaded.backoff_until is None
    else:
        assert result.max_probability is None
        assert result.coverage_status == CoverageStatus.TEMPORARILY_UNAVAILABLE
        assert reloaded.diagnostics["consecutive_failures"] == 1
        assert reloaded.diagnostics["last_error_reason"] == "server_busy"
        assert reloaded.backoff_until == old.backoff_until
