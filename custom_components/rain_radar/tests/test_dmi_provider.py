"""Tests for DMI provider normalization."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import logging
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


def _payload(now: datetime | None = None, *, forecast_hours: int = 0) -> dict[str, Any]:
    """Return a point forecast using the same clock as its provider."""
    now = (now or datetime.now(UTC)).replace(microsecond=0)
    payload = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [12.56, 55.72]},
                "properties": {
                    "step": (now - timedelta(minutes=10)).isoformat(),
                    "total-precipitation": 0.0,
                    "precipitation-type": 0,
                },
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [12.56, 55.72]},
                "properties": {
                    "step": (now + timedelta(minutes=20)).isoformat(),
                    "total-precipitation": 0.0,
                    "precipitation-type": 0,
                },
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [12.56, 55.72]},
                "properties": {
                    "step": (now + timedelta(minutes=80)).isoformat(),
                    "total-precipitation": 0.4,
                    "precipitation-type": 1,
                },
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [12.56, 55.72]},
                "properties": {
                    "step": (now + timedelta(minutes=140)).isoformat(),
                    "total-precipitation": 0.4,
                    "precipitation-type": 0,
                },
            },
        ],
    }
    payload["features"].extend(
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [12.56, 55.72]},
            "properties": {
                "step": (now + timedelta(hours=hour)).isoformat(),
                "total-precipitation": 0.4,
                "precipitation-type": 0,
            },
        }
        for hour in range(3, forecast_hours + 1)
    )
    return payload


def _cumulative_payload(
    start: datetime,
    amounts: list[float | None],
    *,
    interval: timedelta = timedelta(hours=1),
) -> dict[str, Any]:
    """Build realistic accumulated DMI precipitation at consecutive model steps."""
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "step": (start + index * interval).isoformat(),
                    "total-precipitation": amount,
                    "precipitation-type": 1,
                },
            }
            for index, amount in enumerate(amounts)
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
    assert set(params["parameter-name"].split(",")) == {
        "total-precipitation",
        "precipitation-type",
    }


@pytest.mark.asyncio
async def test_dmi_rain_risk_is_threshold_based() -> None:
    """Test DMI rain risk uses threshold-based values, not probability data."""
    provider = DmiProvider(FakeClient(_payload()))

    forecast = await provider.async_get_rain_risk(
        Location(55.715, 12.561),
        _options(),
    )

    assert forecast.max_probability == 100
    assert len(forecast.hourly) == 3
    assert forecast.hourly[0].probability == 0
    assert forecast.hourly[1].probability == 100
    assert forecast.hourly[1].precipitation_amount == 0.4
    assert forecast.hourly[1].symbol_code == "rain"
    assert forecast.hourly[2].probability == 0


@pytest.mark.asyncio
async def test_dmi_derives_hourly_rates_from_accumulated_precipitation() -> None:
    """Cumulative plateaus mean no new rain, not thousands of millimetres per hour."""
    start = datetime(2026, 8, 14, 6, tzinfo=UTC)
    clock = MutableClock(start + timedelta(minutes=30))
    payload = _cumulative_payload(start, [0.0, 0.6, 0.6, 0.9])
    for feature in payload["features"]:
        # This legacy field can itself be accumulated despite its name/metadata.
        feature["properties"]["rain-precipitation-rate"] = 2.529
    provider = DmiProvider(FakeClient(payload), now_fn=clock)

    precipitation, risk = await asyncio.gather(
        provider.async_get_precipitation_forecast(Location(55.715, 12.561), _options()),
        provider.async_get_rain_risk(Location(55.715, 12.561), _options()),
    )

    assert [
        sample.precipitation_rate for sample in precipitation.samples
    ] == pytest.approx([0.6, 0.0, 0.3])
    assert precipitation.current_precipitation == pytest.approx(0.6)
    assert [hour.precipitation_amount for hour in risk.hourly] == pytest.approx(
        [0.6, 0.0, 0.3]
    )
    assert [hour.probability for hour in risk.hourly] == [100, 0, 100]


@pytest.mark.asyncio
async def test_dmi_accumulated_precipitation_uses_actual_step_duration() -> None:
    """A two-hour accumulation increase must be divided by two for an hourly rate."""
    start = datetime(2026, 8, 14, 6, tzinfo=UTC)
    clock = MutableClock(start + timedelta(hours=1))
    provider = DmiProvider(
        FakeClient(_cumulative_payload(start, [0.0, 0.3], interval=timedelta(hours=2))),
        now_fn=clock,
    )

    forecast = await provider.async_get_precipitation_forecast(
        Location(55.715, 12.561), _options()
    )

    assert forecast.current_precipitation == pytest.approx(0.15)
    assert forecast.samples[-1].precipitation_rate == pytest.approx(0.15)


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_amount", [0.0, 1.5])
async def test_dmi_single_cumulative_boundary_does_not_define_rain_rate(
    initial_amount: float,
) -> None:
    """A single accumulated boundary, including zero, does not define an interval."""
    clock = MutableClock(datetime(2026, 8, 14, 6, tzinfo=UTC))
    provider = DmiProvider(
        FakeClient(_cumulative_payload(clock.value, [initial_amount])), now_fn=clock
    )

    forecast = await provider.async_get_precipitation_forecast(
        Location(55.715, 12.561), _options()
    )

    assert forecast.coverage_status == CoverageStatus.TEMPORARILY_UNAVAILABLE
    assert forecast.current_precipitation is None
    assert forecast.rain_now is None


@pytest.mark.asyncio
async def test_dmi_null_step_does_not_bridge_unknown_accumulation() -> None:
    """Missing totals break the accumulation baseline until another pair is known."""
    start = datetime(2026, 8, 14, 6, tzinfo=UTC)
    clock = MutableClock(start + timedelta(minutes=30))
    provider = DmiProvider(
        FakeClient(_cumulative_payload(start, [0.0, 0.6, None, 0.6, 0.9])),
        now_fn=clock,
    )

    precipitation, risk = await asyncio.gather(
        provider.async_get_precipitation_forecast(Location(55.715, 12.561), _options()),
        provider.async_get_rain_risk(Location(55.715, 12.561), _options()),
    )

    rates = {sample.time: sample.precipitation_rate for sample in precipitation.samples}
    assert rates.get(start + timedelta(hours=1)) is None
    assert rates.get(start + timedelta(hours=2)) is None
    assert rates[start + timedelta(hours=3)] == pytest.approx(0.3)
    assert [hour.time for hour in risk.hourly] == [start, start + timedelta(hours=3)]
    assert risk.hourly[1].precipitation_amount == pytest.approx(0.3)


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
    client = FakeClient(_payload(clock.value, forecast_hours=24))
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
    client.payload = _payload(clock.value, forecast_hours=24)
    await provider.async_get_precipitation_forecast(
        Location(55.715, 12.561), _options()
    )
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_dmi_daily_polling_is_bounded_by_model_cycles() -> None:
    """Test five-minute coordinator ticks produce about eight daily DMI requests."""
    start = datetime(2026, 8, 14, tzinfo=UTC)
    clock = MutableClock(start)
    client = FakeClient(_payload(clock.value, forecast_hours=24))
    provider = DmiProvider(client, now_fn=clock)

    for minutes in range(0, 24 * 60, 5):
        clock.value = start + timedelta(minutes=minutes)
        client.payload = _payload(clock.value, forecast_hours=24)
        await provider.async_get_precipitation_forecast(
            Location(55.715, 12.561), _options()
        )

    assert 8 <= len(client.calls) <= 9


@pytest.mark.asyncio
async def test_dmi_identical_entries_share_inflight_request(hass) -> None:
    """Test identical DMI entries share one request in a Home Assistant instance."""
    clock = MutableClock(datetime(2026, 8, 14, 6, 4, tzinfo=UTC))
    first_client = FakeClient(_payload(clock.value), hass=hass)
    second_client = FakeClient(_payload(clock.value), hass=hass)
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
    client = FakeClient(_payload(clock.value, forecast_hours=24), cache=expired_cache)
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
        _payload(clock.value, forecast_hours=24),
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
    client.payload = _payload(clock.value)
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
        _payload(clock.value, forecast_hours=24),
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
                    "step": (now - timedelta(hours=2)).isoformat(),
                    "total-precipitation": 0.0,
                }
            },
            {
                "properties": {
                    "step": (now - timedelta(hours=1)).isoformat(),
                    "total-precipitation": 0.6,
                }
            },
            {
                "properties": {
                    "step": (now - timedelta(minutes=10)).isoformat(),
                    "total-precipitation": 0.6,
                }
            },
            {
                "properties": {
                    "step": (now + timedelta(minutes=20)).isoformat(),
                    "total-precipitation": 0.6,
                }
            },
            {
                "properties": {
                    "step": (now + timedelta(minutes=80)).isoformat(),
                    "total-precipitation": 0.6,
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
async def test_dmi_short_dry_forecast_does_not_claim_full_soon_horizon() -> None:
    """Twenty dry forecast minutes cannot rule out rain over the next hour."""
    clock = MutableClock(datetime(2026, 8, 14, 6, 4, tzinfo=UTC))
    payload = _payload(clock.value)
    payload["features"] = payload["features"][:2]
    payload["features"][1]["properties"]["total-precipitation"] = 0.0
    provider = DmiProvider(FakeClient(payload), now_fn=clock)

    forecast = await provider.async_get_precipitation_forecast(
        Location(55.715, 12.561), _options()
    )

    assert forecast.coverage_status == CoverageStatus.OK
    assert forecast.rain_now is False
    assert forecast.rain_arrival_minutes is None
    assert forecast.rain_soon is None


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
    client = FakeClient(
        None,
        error=RainRadarApiError("Provider returned HTTP 404: outside coverage"),
    )
    provider = DmiProvider(client)

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
    assert len(client.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload_kind",
    [
        "object",
        "list",
        "empty_features",
        "null_values",
        "old_samples",
        "nonfinite",
        "nan",
    ],
)
async def test_dmi_invalid_payload_is_unavailable_and_rate_bounded(
    payload_kind: str,
) -> None:
    """Malformed or unusable forecasts must never become successful dry weather."""
    clock = MutableClock(datetime(2026, 8, 14, 6, 4, tzinfo=UTC))
    payload: dict[str, Any] | list[Any]
    if payload_kind == "object":
        payload = {"error": "No forecast available"}
    elif payload_kind == "list":
        payload = []
    elif payload_kind == "empty_features":
        payload = {"type": "FeatureCollection", "features": []}
    elif payload_kind == "old_samples":
        payload = _payload(clock.value - timedelta(days=1))
    else:
        payload = _payload(clock.value)
        for feature in payload["features"]:
            properties = feature["properties"]
            properties["total-precipitation"] = (
                float("inf")
                if payload_kind == "nonfinite"
                else float("nan")
                if payload_kind == "nan"
                else None
            )

    client = FakeClient(payload)
    provider = DmiProvider(client, now_fn=clock)

    precipitation, risk = await asyncio.gather(
        provider.async_get_precipitation_forecast(Location(55.715, 12.561), _options()),
        provider.async_get_rain_risk(Location(55.715, 12.561), _options()),
    )

    assert precipitation.coverage_status == CoverageStatus.TEMPORARILY_UNAVAILABLE
    assert precipitation.rain_now is None
    assert precipitation.current_precipitation is None
    assert risk.max_probability is None
    assert provider.last_success is None
    assert provider.last_error_type == "RainRadarApiTemporaryError"
    assert provider.diagnostics["last_error_reason"] == "invalid_response"
    assert provider.backoff_until is not None
    assert provider.backoff_until >= clock.value + timedelta(minutes=15)
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_dmi_invalid_refresh_keeps_previous_forecast() -> None:
    """An empty refresh must not replace usable cached weather or reset its age."""
    clock = MutableClock(datetime(2026, 8, 14, 6, 4, tzinfo=UTC))
    client = FakeClient(_payload(clock.value, forecast_hours=24))
    provider = DmiProvider(client, now_fn=clock)
    location = Location(55.715, 12.561)
    original = await provider.async_get_precipitation_forecast(location, _options())
    first_success = provider.last_success
    assert provider.next_refresh_at is not None

    clock.value = provider.next_refresh_at + timedelta(seconds=1)
    client.payload = {"type": "FeatureCollection", "features": []}
    precipitation, risk = await asyncio.gather(
        provider.async_get_precipitation_forecast(location, _options()),
        provider.async_get_rain_risk(location, _options()),
    )

    assert precipitation.samples == original.samples
    assert precipitation.is_stale is True
    assert precipitation.cache.from_cache is True
    assert precipitation.cache.fetched_at == original.cache.fetched_at
    assert risk.hourly
    assert provider.last_success == first_success
    assert provider.diagnostics["last_error_reason"] == "invalid_response"
    assert len(client.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("retry_after", [0, -1, 60])
async def test_dmi_short_retry_after_retains_minimum_backoff(
    retry_after: float,
) -> None:
    """Short Retry-After values must not bypass the integration's retry limit."""
    clock = MutableClock(datetime(2026, 8, 14, 6, 4, tzinfo=UTC))
    client = FakeClient(
        None,
        error=RainRadarApiRateLimitedError("Rate limited", retry_after=retry_after),
    )
    provider = DmiProvider(client, now_fn=clock)

    await provider.async_get_precipitation_forecast(
        Location(55.715, 12.561), _options()
    )

    assert provider.backoff_until is not None
    assert provider.backoff_until >= clock.value + timedelta(minutes=15)
    clock.value += timedelta(minutes=5)
    await provider.async_get_rain_risk(Location(55.715, 12.561), _options())
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_dmi_server_retry_after_is_respected() -> None:
    """Retry-After on a server outage must pause requests for the requested time."""
    clock = MutableClock(datetime(2026, 8, 14, 6, 4, tzinfo=UTC))
    client = FakeClient(
        None,
        error=RainRadarApiTemporaryError(
            "Service unavailable",
            status_code=503,
            reason="http_error",
            retry_after=7200,
        ),
    )
    provider = DmiProvider(client, now_fn=clock)

    await provider.async_get_precipitation_forecast(
        Location(55.715, 12.561), _options()
    )

    assert provider.backoff_until == clock.value + timedelta(hours=2)
    assert provider.diagnostics["status_code"] == 503


@pytest.mark.asyncio
async def test_dmi_retry_after_starts_when_request_finishes() -> None:
    """Time spent waiting on the network must not shorten the server's pause."""
    clock = MutableClock(datetime(2026, 8, 14, 6, 4, tzinfo=UTC))

    class SlowFailingClient(FakeClient):
        """Advance the test clock before reporting a failed response."""

        async def async_get_json(self, *args, **kwargs):
            try:
                return await super().async_get_json(*args, **kwargs)
            finally:
                clock.value += timedelta(seconds=25)

    client = SlowFailingClient(
        None, error=RainRadarApiRateLimitedError("Rate limited", retry_after=7200)
    )
    provider = DmiProvider(client, now_fn=clock)

    await provider.async_get_precipitation_forecast(
        Location(55.715, 12.561), _options()
    )

    assert provider.backoff_until == clock.value + timedelta(hours=2)


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [400, 404])
async def test_dmi_http_request_error_is_not_outside_coverage(status_code: int) -> None:
    """Query and resource errors must not be misreported as geographic coverage."""
    clock = MutableClock(datetime(2026, 8, 14, 6, 4, tzinfo=UTC))
    client = FakeClient(
        None,
        error=RainRadarApiError(
            f"Provider returned HTTP {status_code}: invalid datetime",
            status_code=status_code,
            reason="http_error",
        ),
    )
    provider = DmiProvider(client, now_fn=clock)

    precipitation, risk = await asyncio.gather(
        provider.async_get_precipitation_forecast(Location(55.715, 12.561), _options()),
        provider.async_get_rain_risk(Location(55.715, 12.561), _options()),
    )

    assert precipitation.coverage_status == CoverageStatus.TEMPORARILY_UNAVAILABLE
    assert risk.max_probability is None
    assert provider.backoff_until is not None
    assert provider.backoff_until >= clock.value + timedelta(minutes=15)
    assert provider.diagnostics["status_code"] == status_code
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_dmi_transient_failure_with_usable_cache_logs_once_at_info(
    caplog,
) -> None:
    """Short outages with usable weather data should not create warning spam."""
    caplog.set_level(logging.INFO, logger="custom_components.rain_radar.providers.dmi")
    clock = MutableClock(datetime(2026, 8, 14, 6, 4, tzinfo=UTC))
    client = FakeClient(_payload(clock.value, forecast_hours=24))
    provider = DmiProvider(client, now_fn=clock)
    location = Location(55.715, 12.561)
    await provider.async_get_precipitation_forecast(location, _options())
    assert provider.next_refresh_at is not None

    clock.value = provider.next_refresh_at + timedelta(seconds=1)
    client.error = RainRadarApiTemporaryError("Request timed out", reason="timeout")
    await provider.async_get_precipitation_forecast(location, _options())
    assert provider.backoff_until is not None
    clock.value = provider.backoff_until + timedelta(seconds=1)
    await provider.async_get_rain_risk(location, _options())

    records = [
        record
        for record in caplog.records
        if record.name == "custom_components.rain_radar.providers.dmi"
    ]
    assert [record.levelno for record in records] == [logging.INFO]
    assert "timeout" in records[0].getMessage().lower()


@pytest.mark.asyncio
async def test_dmi_outage_without_cache_warns_once_then_recovers_once(caplog) -> None:
    """An ongoing outage and its recovery should each be logged only once."""
    caplog.set_level(logging.INFO, logger="custom_components.rain_radar.providers.dmi")
    clock = MutableClock(datetime(2026, 8, 14, 6, 4, tzinfo=UTC))
    client = FakeClient(
        None, error=RainRadarApiTemporaryError("Request timed out", reason="timeout")
    )
    provider = DmiProvider(client, now_fn=clock)
    location = Location(55.715, 12.561)
    await asyncio.gather(
        provider.async_get_precipitation_forecast(location, _options()),
        provider.async_get_rain_risk(location, _options()),
    )
    assert provider.backoff_until is not None
    clock.value = provider.backoff_until + timedelta(seconds=1)
    await provider.async_get_precipitation_forecast(location, _options())
    assert provider.backoff_until is not None

    clock.value = provider.backoff_until + timedelta(seconds=1)
    client.error = None
    client.payload = _payload(clock.value)
    recovered = await provider.async_get_precipitation_forecast(location, _options())
    await provider.async_get_rain_risk(location, _options())

    records = [
        record
        for record in caplog.records
        if record.name == "custom_components.rain_radar.providers.dmi"
    ]
    assert [record.levelno for record in records] == [logging.WARNING, logging.INFO]
    assert recovered.coverage_status == CoverageStatus.OK
    assert provider.diagnostics["last_error_reason"] is None
    assert provider.diagnostics["status_code"] is None


@pytest.mark.asyncio
async def test_dmi_cache_expiring_during_backoff_warns_once(caplog) -> None:
    """Losing the usable stale fallback during a long pause must become visible."""
    caplog.set_level(logging.INFO, logger="custom_components.rain_radar.providers.dmi")
    start = datetime(2026, 8, 14, 6, 4, tzinfo=UTC)
    clock = MutableClock(start)
    client = FakeClient(_payload(clock.value, forecast_hours=24))
    provider = DmiProvider(client, now_fn=clock)
    location = Location(55.715, 12.561)
    await provider.async_get_precipitation_forecast(location, _options())
    assert provider.next_refresh_at is not None

    clock.value = provider.next_refresh_at + timedelta(seconds=1)
    client.error = RainRadarApiRateLimitedError("Rate limited", retry_after=43200)
    await provider.async_get_precipitation_forecast(location, _options())
    clock.value = start + timedelta(hours=6, seconds=1)
    unavailable = await provider.async_get_precipitation_forecast(location, _options())
    await provider.async_get_rain_risk(location, _options())

    records = [
        record
        for record in caplog.records
        if record.name == "custom_components.rain_radar.providers.dmi"
    ]
    assert [record.levelno for record in records] == [logging.INFO, logging.WARNING]
    assert unavailable.coverage_status == CoverageStatus.TEMPORARILY_UNAVAILABLE
    assert unavailable.rain_now is None
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_dmi_backoff_is_shared_between_different_entries(hass) -> None:
    """A rate limit must pause all DMI entries, including another location."""
    clock = MutableClock(datetime(2026, 8, 14, 6, 4, tzinfo=UTC))
    first_client = FakeClient(
        None,
        error=RainRadarApiRateLimitedError("Rate limited", retry_after=7200),
        hass=hass,
    )
    second_client = FakeClient(_payload(clock.value), hass=hass)
    first = DmiProvider(first_client, now_fn=clock)
    second = DmiProvider(second_client, now_fn=clock)

    await first.async_get_precipitation_forecast(Location(55.715, 12.561), _options())
    second_forecast = await second.async_get_precipitation_forecast(
        Location(57.7, 11.97), _options()
    )

    assert first.backoff_until == second.backoff_until
    assert second_forecast.coverage_status == CoverageStatus.TEMPORARILY_UNAVAILABLE
    assert len(first_client.calls) == 1
    assert not second_client.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("future_rain", [False, True])
async def test_dmi_partial_forecast_does_not_turn_missing_rain_into_dry_weather(
    future_rain: bool,
) -> None:
    """Partial weather stays usable without claiming unknown forecast hours are dry."""
    clock = MutableClock(datetime(2026, 8, 14, 6, 4, tzinfo=UTC))
    payload = _payload(clock.value)
    for feature in payload["features"][2:]:
        if future_rain and feature is payload["features"][2]:
            continue
        feature["properties"]["total-precipitation"] = None
    provider = DmiProvider(FakeClient(payload), now_fn=clock)

    precipitation, risk = await asyncio.gather(
        provider.async_get_precipitation_forecast(Location(55.715, 12.561), _options()),
        provider.async_get_rain_risk(Location(55.715, 12.561), _options()),
    )

    assert precipitation.coverage_status == CoverageStatus.OK
    assert precipitation.rain_now is False
    assert precipitation.rain_soon is (True if future_rain else None)
    assert risk.max_probability == (100 if future_rain else None)
    assert len(risk.hourly) == (2 if future_rain else 1)


@pytest.mark.asyncio
async def test_dmi_outside_coverage_cache_does_not_pause_another_location(hass) -> None:
    """A negative geographic result must remain local to its point query."""
    clock = MutableClock(datetime(2026, 8, 14, 6, 4, tzinfo=UTC))
    outside_client = FakeClient(
        None,
        error=RainRadarApiError("Provider returned HTTP 404: outside coverage"),
        hass=hass,
    )
    inside_client = FakeClient(_payload(clock.value), hass=hass)
    outside = DmiProvider(outside_client, now_fn=clock)
    inside = DmiProvider(inside_client, now_fn=clock)

    await outside.async_get_precipitation_forecast(
        Location(40.7128, -74.006), _options()
    )
    forecast = await inside.async_get_precipitation_forecast(
        Location(55.715, 12.561), _options()
    )
    clock.value += timedelta(minutes=5)
    outside_again = await outside.async_get_precipitation_forecast(
        Location(40.7128, -74.006), _options()
    )

    assert forecast.coverage_status == CoverageStatus.OK
    assert outside_again.coverage_status == CoverageStatus.OUTSIDE_COVERAGE
    assert len(outside_client.calls) == 1
    assert len(inside_client.calls) == 1


@pytest.mark.asyncio
async def test_dmi_cancelled_request_releases_single_flight_lock() -> None:
    """Cancellation during unload must not lock requests or count as an outage."""
    clock = MutableClock(datetime(2026, 8, 14, 6, 4, tzinfo=UTC))
    started = asyncio.Event()
    release = asyncio.Event()

    class WaitingClient(FakeClient):
        """Pause mocked network I/O until cancelled or released."""

        async def async_get_json(self, *args, **kwargs):
            started.set()
            await release.wait()
            return await super().async_get_json(*args, **kwargs)

    client = WaitingClient(_payload(clock.value))
    provider = DmiProvider(client, now_fn=clock)
    location = Location(55.715, 12.561)
    pending = asyncio.create_task(
        provider.async_get_precipitation_forecast(location, _options())
    )
    await started.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending

    release.set()
    async with asyncio.timeout(1):
        forecast = await provider.async_get_precipitation_forecast(location, _options())

    assert forecast.coverage_status == CoverageStatus.OK
    assert provider.backoff_until is None
    assert provider.diagnostics["consecutive_failures"] == 0


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
