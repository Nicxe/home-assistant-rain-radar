"""Tests for point-local DMI request failures."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import logging

import pytest

from custom_components.rain_radar.api import RainRadarApiError
from custom_components.rain_radar.providers.dmi import DmiProvider
from custom_components.rain_radar.providers.models import CoverageStatus, Location

from .test_dmi_provider import FakeClient, MutableClock, _options, _payload


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [400, 404])
async def test_request_failure_does_not_pause_other_locations(
    hass, status_code
) -> None:
    """A bad point query must not prevent another point from fetching weather."""
    clock = MutableClock(datetime(2026, 8, 14, 6, 4, tzinfo=UTC))
    bad_client = FakeClient(
        None,
        error=RainRadarApiError(
            "Invalid point query", status_code=status_code, reason="http_error"
        ),
        hass=hass,
    )
    good_client = FakeClient(_payload(clock.value), hass=hass)
    bad = DmiProvider(bad_client, now_fn=clock)
    good = DmiProvider(good_client, now_fn=clock)
    bad_location = Location(55.715, 12.561)

    failed = await bad.async_get_precipitation_forecast(bad_location, _options())
    good_forecast = await good.async_get_precipitation_forecast(
        Location(57.7, 11.97), _options()
    )
    repeated = await bad.async_get_precipitation_forecast(bad_location, _options())

    assert failed.coverage_status == CoverageStatus.TEMPORARILY_UNAVAILABLE
    assert repeated.coverage_status == CoverageStatus.TEMPORARILY_UNAVAILABLE
    assert good_forecast.coverage_status == CoverageStatus.OK
    assert len(bad_client.calls) == 1
    assert len(good_client.calls) == 1
    assert bad.backoff_until >= clock.value + timedelta(minutes=15)
    assert bad.diagnostics["last_error_reason"] == "http_error"
    assert bad.diagnostics["status_code"] == status_code
    assert good.backoff_until is None
    assert good.last_error is None


@pytest.mark.asyncio
async def test_request_failure_keeps_usable_stale_forecast(hass) -> None:
    """A local request failure must preserve weather and its real cache age."""
    clock = MutableClock(datetime(2026, 8, 14, 6, 4, tzinfo=UTC))
    client = FakeClient(_payload(clock.value, forecast_hours=24), hass=hass)
    provider = DmiProvider(client, now_fn=clock)
    location = Location(55.715, 12.561)
    original = await provider.async_get_precipitation_forecast(location, _options())
    clock.value = provider.next_refresh_at + timedelta(seconds=1)
    client.error = RainRadarApiError(
        "Invalid query", status_code=400, reason="http_error"
    )

    failed_refresh = await provider.async_get_precipitation_forecast(
        location, _options()
    )
    repeated = await provider.async_get_precipitation_forecast(location, _options())

    assert failed_refresh.coverage_status == CoverageStatus.OK
    assert repeated.samples == original.samples
    assert repeated.cache.fetched_at == original.cache.fetched_at
    assert repeated.is_stale is True
    assert repeated.cache.from_cache is True
    assert provider.diagnostics["last_error_reason"] == "http_error"
    assert provider.diagnostics["status_code"] == 400
    assert provider.diagnostics["cache_usable"] is True
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_request_failure_cannot_extend_stale_cache_lifetime() -> None:
    """Local cooldown must not make a six-hour-old forecast usable again."""
    start = datetime(2026, 8, 14, 6, 4, tzinfo=UTC)
    clock = MutableClock(start)
    client = FakeClient(_payload(clock.value, forecast_hours=24))
    provider = DmiProvider(client, now_fn=clock)
    location = Location(55.715, 12.561)
    await provider.async_get_precipitation_forecast(location, _options())
    clock.value = start + timedelta(hours=5, minutes=55)
    client.error = RainRadarApiError(
        "Invalid query", status_code=400, reason="http_error"
    )
    await provider.async_get_precipitation_forecast(location, _options())
    clock.value = start + timedelta(hours=6, seconds=1)

    forecast = await provider.async_get_precipitation_forecast(location, _options())

    assert forecast.coverage_status == CoverageStatus.TEMPORARILY_UNAVAILABLE
    assert forecast.current_precipitation is None
    assert provider.diagnostics["cache_usable"] is False
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_request_failure_recovery_resets_only_its_local_diagnostics() -> None:
    """A successful retry clears the point's request error and cooldown."""
    clock = MutableClock(datetime(2026, 8, 14, 6, 4, tzinfo=UTC))
    client = FakeClient(
        None,
        error=RainRadarApiError("Invalid query", status_code=400, reason="http_error"),
    )
    provider = DmiProvider(client, now_fn=clock)
    location = Location(55.715, 12.561)
    await provider.async_get_precipitation_forecast(location, _options())
    clock.value = provider.backoff_until + timedelta(seconds=1)
    client.error = None
    client.payload = _payload(clock.value)

    recovered = await provider.async_get_precipitation_forecast(location, _options())

    assert recovered.coverage_status == CoverageStatus.OK
    assert provider.backoff_until is None
    assert provider.diagnostics["status_code"] is None
    assert provider.diagnostics["last_error_reason"] is None
    assert provider.diagnostics["consecutive_failures"] == 0
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_request_failure_is_shared_only_by_identical_point_keys(hass) -> None:
    """Identical entries share one query failure without duplicate requests."""
    clock = MutableClock(datetime(2026, 8, 14, 6, 4, tzinfo=UTC))
    error = RainRadarApiError("Invalid query", status_code=400, reason="http_error")
    first_client = FakeClient(None, error=error, hass=hass)
    second_client = FakeClient(None, error=error, hass=hass)
    first = DmiProvider(first_client, now_fn=clock)
    second = DmiProvider(second_client, now_fn=clock)
    location = Location(55.715, 12.561)

    await asyncio.gather(
        first.async_get_precipitation_forecast(location, _options()),
        second.async_get_rain_risk(location, _options()),
    )

    assert len(first_client.calls) + len(second_client.calls) == 1
    assert first.backoff_until == second.backoff_until
    assert first.diagnostics["status_code"] == second.diagnostics["status_code"] == 400


@pytest.mark.asyncio
async def test_request_failure_logs_one_episode_and_its_recovery(caplog) -> None:
    """A persistent point query failure must retain bounded logging and retries."""
    caplog.set_level(logging.INFO, logger="custom_components.rain_radar.providers.dmi")
    clock = MutableClock(datetime(2026, 8, 14, 6, 4, tzinfo=UTC))
    client = FakeClient(
        None,
        error=RainRadarApiError("Invalid query", status_code=400, reason="http_error"),
    )
    provider = DmiProvider(client, now_fn=clock)
    location = Location(55.715, 12.561)
    await provider.async_get_precipitation_forecast(location, _options())
    clock.value = provider.backoff_until + timedelta(seconds=1)
    await provider.async_get_rain_risk(location, _options())
    assert provider.backoff_until >= clock.value + timedelta(minutes=30)
    assert provider.diagnostics["consecutive_failures"] == 2
    clock.value = provider.backoff_until + timedelta(seconds=1)
    client.error = None
    client.payload = _payload(clock.value)

    await provider.async_get_precipitation_forecast(location, _options())

    records = [
        record
        for record in caplog.records
        if record.name == "custom_components.rain_radar.providers.dmi"
    ]
    assert [record.levelno for record in records] == [logging.WARNING, logging.INFO]
    assert len(client.calls) == 3
