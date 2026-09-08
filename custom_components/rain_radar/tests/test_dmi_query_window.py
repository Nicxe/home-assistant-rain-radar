"""DMI queries retain only the baseline and forecast horizon that are needed."""

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.rain_radar.providers.dmi import (
    DmiProvider,
    _cache_key,
    _next_model_refresh,
    _query_window,
)
from custom_components.rain_radar.providers.models import Location

from .test_dmi_provider import FakeClient, MutableClock, _options, _payload


@pytest.mark.parametrize(("hour", "minute"), [(2, 55), (5, 54)])
def test_query_keeps_current_interval_baseline(hour: int, minute: int) -> None:
    """The first and last fetch in a model cycle still include both boundaries."""
    now = datetime(2026, 9, 4, hour, minute, tzinfo=UTC)
    model, start, end = _query_window(now, _options())

    assert model == datetime(2026, 9, 4, tzinfo=UTC)
    assert start == model + timedelta(hours=2)
    assert start <= now.replace(minute=0)
    assert end >= now + timedelta(hours=_options().rain_risk_horizon_hours)


def test_query_stays_identical_until_next_model_cycle() -> None:
    """Coordinator ticks do not create new URLs or discard conditional cache keys."""
    first = datetime(2026, 9, 4, 2, 55, tzinfo=UTC)
    last = datetime(2026, 9, 4, 5, 54, tzinfo=UTC)
    assert _query_window(first, _options()) == _query_window(last, _options())


@pytest.mark.parametrize("hour", [2, 5, 23])
async def test_request_crossing_model_boundary_keeps_its_query_refresh_deadline(
    hour: int,
) -> None:
    """A slow response must not extend an older query into the next cache cycle."""
    started_at = datetime(2026, 9, 4, hour, 54, 59, tzinfo=UTC)
    clock = MutableClock(started_at)
    location = Location(55.715, 12.561)
    options = _options()
    planned_refresh = _next_model_refresh(started_at, _cache_key(location, options))
    _, _, query_end = _query_window(started_at, options)

    class CrossingBoundaryClient(FakeClient):
        """Complete the mocked network response just after the model boundary."""

        async def async_get_json(self, *args, **kwargs):
            response = await super().async_get_json(*args, **kwargs)
            clock.value += timedelta(seconds=2)
            return response

    client = CrossingBoundaryClient(_payload(started_at, forecast_hours=24))
    provider = DmiProvider(client, now_fn=clock)

    forecast = await provider.async_get_precipitation_forecast(location, options)

    assert provider.next_refresh_at == planned_refresh
    assert forecast.cache.expires_at == planned_refresh
    assert forecast.cache.fetched_at == started_at + timedelta(seconds=2)
    assert query_end >= planned_refresh + timedelta(
        hours=options.rain_risk_horizon_hours
    )
    assert provider.diagnostics["last_request_duration_seconds"] == 2
    assert len(client.calls) == 1
