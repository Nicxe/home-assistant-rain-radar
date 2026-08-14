"""Tests for DMI provider normalization."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from custom_components.rain_radar.api import (
    RainRadarApiError,
    RainRadarApiRateLimitedError,
    RainRadarApiTemporaryError,
)
from custom_components.rain_radar.const import PROVIDER_DMI
from custom_components.rain_radar.providers.dmi import DmiProvider
from custom_components.rain_radar.providers.models import (
    CacheMetadata,
    CoverageStatus,
    Location,
    RainRadarOptions,
)


class FakeClient:
    """Fake API client returning a DMI payload."""

    def __init__(
        self,
        payload: dict[str, Any] | list[Any] | None,
        *,
        error: RainRadarApiError | None = None,
        cache: CacheMetadata | None = None,
        hass=None,
    ) -> None:
        self.payload = payload
        self.error = error
        self.cache = cache
        self.hass = hass
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def async_get_json(self, cache_key: str, url: str, **kwargs):
        """Return fake JSON payload."""
        self.calls.append((cache_key, url, kwargs.get("params", {})))
        await asyncio.sleep(0)
        if self.error is not None:
            raise self.error
        return self.payload, self.cache or CacheMetadata(
            fetched_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )


class MutableClock:
    """Mutable UTC clock for cadence and backoff tests."""

    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


def _options() -> RainRadarOptions:
    return RainRadarOptions(
        contact="rain-radar@example.com",
        forecast_provider=PROVIDER_DMI,
        radar_area="denmark",
        rain_threshold=0.1,
        rain_soon_window_minutes=60,
        sample_radius_m=1000,
        rain_risk_horizon_hours=12,
    )


def _payload() -> dict[str, Any]:
    now = datetime.now(UTC).replace(microsecond=0)
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [12.56, 55.72]},
                "properties": {
                    "step": (now - timedelta(minutes=10)).isoformat(),
                    "rain-precipitation-rate": 0.0,
                    "total-precipitation": 0.0,
                    "precipitation-type": 0,
                },
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [12.56, 55.72]},
                "properties": {
                    "step": (now + timedelta(minutes=20)).isoformat(),
                    "rain-precipitation-rate": 0.0002,
                    "total-precipitation": 0.4,
                    "precipitation-type": 1,
                },
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [12.56, 55.72]},
                "properties": {
                    "step": (now + timedelta(minutes=80)).isoformat(),
                    "rain-precipitation-rate": 0.0,
                    "total-precipitation": 0.4,
                    "precipitation-type": 0,
                },
            },
        ],
    }


@pytest.mark.asyncio
async def test_dmi_precipitation_forecast_calculates_arrival() -> None:
    """Test DMI HARMONIE precipitation data is normalized."""
    client = FakeClient(_payload())
    provider = DmiProvider(client)

    forecast = await provider.async_get_precipitation_forecast(
        Location(55.715, 12.561),
        _options(),
    )

    assert forecast.coverage_status == CoverageStatus.OK
    assert forecast.current_precipitation == 0.0
    assert forecast.rain_now is False
    assert forecast.rain_soon is True
    assert forecast.rain_arrival_minutes is not None
    assert forecast.rain_arrival_minutes <= 20
    assert len(forecast.samples) == 3
    params = client.calls[0][2]
    assert params["coords"] == "POINT(12.561 55.715)"
    assert params["crs"] == "crs84"
    assert params["f"] == "GeoJSON"
    assert "rain-precipitation-rate" in params["parameter-name"]


@pytest.mark.asyncio
async def test_dmi_rain_risk_is_threshold_based() -> None:
    """Test DMI rain risk uses threshold-based values, not probability data."""
    provider = DmiProvider(FakeClient(_payload()))

    forecast = await provider.async_get_rain_risk(
        Location(55.715, 12.561),
        _options(),
    )

    assert forecast.max_probability == 100
    assert len(forecast.hourly) == 2
    assert forecast.hourly[0].probability == 100
    assert forecast.hourly[0].precipitation_amount == 0.4
    assert forecast.hourly[0].symbol_code == "rain"
    assert forecast.hourly[1].probability == 0


@pytest.mark.asyncio
async def test_dmi_reuses_forecast_payload_for_concurrent_updates() -> None:
    """Test precipitation and risk sensors share one DMI request."""
    client = FakeClient(_payload())
    provider = DmiProvider(client)

    precipitation, rain_risk = await asyncio.gather(
        provider.async_get_precipitation_forecast(Location(55.715, 12.561), _options()),
        provider.async_get_rain_risk(Location(55.715, 12.561), _options()),
    )

    assert precipitation.rain_soon is True
    assert rain_risk.max_probability == 100
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_dmi_cache_follows_model_refresh_cycle() -> None:
    """Test coordinator updates do not poll DMI between model cycles."""
    clock = MutableClock(datetime(2026, 8, 14, 6, 4, tzinfo=UTC))
    client = FakeClient(_payload())
    provider = DmiProvider(client, now_fn=clock)

    await provider.async_get_precipitation_forecast(
        Location(55.715, 12.561), _options()
    )
    next_refresh = provider.next_refresh_at
    assert next_refresh is not None

    clock.value = next_refresh - timedelta(seconds=1)
    await provider.async_get_rain_risk(Location(55.715, 12.561), _options())
    assert len(client.calls) == 1

    clock.value = next_refresh + timedelta(seconds=1)
    await provider.async_get_precipitation_forecast(
        Location(55.715, 12.561), _options()
    )
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_dmi_daily_polling_is_bounded_by_model_cycles() -> None:
    """Test five-minute coordinator ticks produce about eight daily DMI requests."""
    start = datetime(2026, 8, 14, tzinfo=UTC)
    clock = MutableClock(start)
    client = FakeClient(_payload())
    provider = DmiProvider(client, now_fn=clock)

    for minutes in range(0, 24 * 60, 5):
        clock.value = start + timedelta(minutes=minutes)
        await provider.async_get_precipitation_forecast(
            Location(55.715, 12.561), _options()
        )

    assert 8 <= len(client.calls) <= 9


@pytest.mark.asyncio
async def test_dmi_identical_entries_share_inflight_request(hass) -> None:
    """Test identical DMI entries share one request in a Home Assistant instance."""
    clock = MutableClock(datetime(2026, 8, 14, 6, 4, tzinfo=UTC))
    first_client = FakeClient(_payload(), hass=hass)
    second_client = FakeClient(_payload(), hass=hass)
    first_provider = DmiProvider(first_client, now_fn=clock)
    second_provider = DmiProvider(second_client, now_fn=clock)

    first, second = await asyncio.gather(
        first_provider.async_get_precipitation_forecast(
            Location(55.715, 12.561), _options()
        ),
        second_provider.async_get_rain_risk(Location(55.715, 12.561), _options()),
    )

    assert first.rain_soon is True
    assert second.max_probability == 100
    assert len(first_client.calls) + len(second_client.calls) == 1


@pytest.mark.asyncio
async def test_dmi_rate_limit_reuses_stale_cache() -> None:
    """Test DMI can reuse stale provider cache when rate limited."""
    now = datetime(2026, 8, 14, 6, 4, tzinfo=UTC)
    clock = MutableClock(now)
    expired_cache = CacheMetadata(
        fetched_at=now - timedelta(minutes=20),
        expires_at=now - timedelta(minutes=1),
    )
    client = FakeClient(_payload(), cache=expired_cache)
    provider = DmiProvider(client, now_fn=clock)
    await provider.async_get_precipitation_forecast(
        Location(55.715, 12.561),
        _options(),
    )
    assert provider.next_refresh_at is not None
    clock.value = provider.next_refresh_at + timedelta(seconds=1)
    client.error = RainRadarApiRateLimitedError("Provider rate limited request")

    forecast = await provider.async_get_rain_risk(
        Location(55.715, 12.561),
        _options(),
    )

    assert forecast.is_stale is True
    assert forecast.cache.from_cache is True
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_dmi_rate_limit_uses_exponential_backoff() -> None:
    """Test repeated rate limits do not cause five-minute retry loops."""
    now = datetime(2026, 8, 14, 6, 4, tzinfo=UTC)
    clock = MutableClock(now)
    client = FakeClient(
        _payload(),
        cache=CacheMetadata(fetched_at=now, expires_at=now + timedelta(minutes=1)),
    )
    provider = DmiProvider(client, now_fn=clock)
    await provider.async_get_precipitation_forecast(
        Location(55.715, 12.561), _options()
    )

    assert provider.next_refresh_at is not None
    clock.value = provider.next_refresh_at + timedelta(seconds=1)
    client.error = RainRadarApiRateLimitedError("Provider rate limited request")
    forecast = await provider.async_get_rain_risk(Location(55.715, 12.561), _options())
    first_backoff = provider.backoff_until

    assert forecast.is_stale is True
    assert first_backoff is not None
    assert first_backoff - clock.value >= timedelta(minutes=15)
    assert len(client.calls) == 2

    clock.value = first_backoff - timedelta(seconds=1)
    await provider.async_get_rain_risk(Location(55.715, 12.561), _options())
    assert len(client.calls) == 2

    clock.value = first_backoff + timedelta(seconds=1)
    await provider.async_get_rain_risk(Location(55.715, 12.561), _options())
    second_backoff = provider.backoff_until
    assert second_backoff is not None
    assert second_backoff - clock.value >= timedelta(minutes=30)
    assert len(client.calls) == 3


@pytest.mark.asyncio
async def test_dmi_rate_limit_respects_retry_after() -> None:
    """Test a provider Retry-After delay takes precedence over fallback backoff."""
    clock = MutableClock(datetime(2026, 8, 14, 6, 4, tzinfo=UTC))
    client = FakeClient(
        None,
        error=RainRadarApiRateLimitedError(
            "Provider rate limited request", retry_after=7200
        ),
    )
    provider = DmiProvider(client, now_fn=clock)

    await provider.async_get_precipitation_forecast(
        Location(55.715, 12.561), _options()
    )

    assert provider.backoff_until == clock.value + timedelta(hours=2)


@pytest.mark.asyncio
async def test_dmi_success_resets_rate_limit_backoff() -> None:
    """Test a successful retry restores normal model scheduling."""
    clock = MutableClock(datetime(2026, 8, 14, 6, 4, tzinfo=UTC))
    client = FakeClient(
        None,
        error=RainRadarApiRateLimitedError("Provider rate limited request"),
    )
    provider = DmiProvider(client, now_fn=clock)
    await provider.async_get_precipitation_forecast(
        Location(55.715, 12.561), _options()
    )
    assert provider.backoff_until is not None

    clock.value = provider.backoff_until + timedelta(seconds=1)
    client.error = None
    client.payload = _payload()
    await provider.async_get_precipitation_forecast(
        Location(55.715, 12.561), _options()
    )

    assert provider.backoff_until is None
    assert provider.last_error_type is None
    assert provider.last_success == clock.value


@pytest.mark.asyncio
async def test_dmi_does_not_publish_expired_stale_current_values() -> None:
    """Test stale forecasts eventually become unavailable instead of misleading."""
    now = datetime(2026, 8, 14, 6, 4, tzinfo=UTC)
    clock = MutableClock(now)
    client = FakeClient(
        _payload(),
        cache=CacheMetadata(fetched_at=now, expires_at=now + timedelta(minutes=1)),
    )
    provider = DmiProvider(client, now_fn=clock)
    await provider.async_get_precipitation_forecast(
        Location(55.715, 12.561), _options()
    )

    clock.value = now + timedelta(hours=7)
    client.error = RainRadarApiRateLimitedError("Provider rate limited request")
    forecast = await provider.async_get_precipitation_forecast(
        Location(55.715, 12.561), _options()
    )

    assert forecast.coverage_status == CoverageStatus.TEMPORARILY_UNAVAILABLE
    assert forecast.current_precipitation is None
    assert forecast.rain_now is None


@pytest.mark.asyncio
async def test_dmi_ignores_past_rain_when_calculating_arrival() -> None:
    """Test historical rain is not reported as arriving now."""
    now = datetime.now(UTC).replace(microsecond=0)
    payload = {
        "type": "FeatureCollection",
        "features": [
            {
                "properties": {
                    "step": (now - timedelta(hours=1)).isoformat(),
                    "rain-precipitation-rate": 0.0002,
                }
            },
            {
                "properties": {
                    "step": (now - timedelta(minutes=10)).isoformat(),
                    "rain-precipitation-rate": 0.0,
                }
            },
            {
                "properties": {
                    "step": (now + timedelta(minutes=20)).isoformat(),
                    "rain-precipitation-rate": 0.0,
                }
            },
        ],
    }
    provider = DmiProvider(FakeClient(payload))

    forecast = await provider.async_get_precipitation_forecast(
        Location(55.715, 12.561), _options()
    )

    assert forecast.rain_now is False
    assert forecast.rain_arrival_minutes is None
    assert forecast.rain_soon is False


@pytest.mark.asyncio
async def test_dmi_rate_limit_without_cache_is_temporarily_unavailable() -> None:
    """Test a busy DMI service does not prevent integration setup."""
    client = FakeClient(
        None,
        error=RainRadarApiRateLimitedError("Provider rate limited request"),
    )
    provider = DmiProvider(client)

    precipitation, rain_risk = await asyncio.gather(
        provider.async_get_precipitation_forecast(Location(55.715, 12.561), _options()),
        provider.async_get_rain_risk(Location(55.715, 12.561), _options()),
    )

    assert precipitation.coverage_status == CoverageStatus.TEMPORARILY_UNAVAILABLE
    assert precipitation.samples == []
    assert rain_risk.max_probability is None
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_dmi_timeout_without_cache_is_temporarily_unavailable() -> None:
    """Test a DMI timeout does not fail the coordinator update."""
    client = FakeClient(
        None,
        error=RainRadarApiTemporaryError("Timed out fetching DMI forecast"),
    )
    provider = DmiProvider(client)

    precipitation, rain_risk = await asyncio.gather(
        provider.async_get_precipitation_forecast(Location(55.715, 12.561), _options()),
        provider.async_get_rain_risk(Location(55.715, 12.561), _options()),
    )

    assert precipitation.coverage_status == CoverageStatus.TEMPORARILY_UNAVAILABLE
    assert precipitation.samples == []
    assert rain_risk.max_probability is None
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_dmi_outside_coverage_returns_empty_forecast() -> None:
    """Test DMI outside-coverage errors do not fail the coordinator."""
    provider = DmiProvider(
        FakeClient(
            None,
            error=RainRadarApiError("Provider returned HTTP 404: outside coverage"),
        )
    )

    forecast = await provider.async_get_precipitation_forecast(
        Location(40.7128, -74.006),
        _options(),
    )
    risk = await provider.async_get_rain_risk(
        Location(40.7128, -74.006),
        _options(),
    )

    assert forecast.coverage_status == CoverageStatus.OUTSIDE_COVERAGE
    assert forecast.samples == []
    assert risk.max_probability is None


@pytest.mark.asyncio
async def test_dmi_is_forecast_only() -> None:
    """Test DMI forecast provider does not expose radar frames directly."""
    provider = DmiProvider(FakeClient(_payload()))

    frames = await provider.async_get_radar_frames(
        Location(55.715, 12.561),
        _options(),
    )

    assert frames.frames == []
    assert frames.attribution == "Data from DMI"
