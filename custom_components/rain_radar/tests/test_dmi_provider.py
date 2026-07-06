"""Tests for DMI provider normalization."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from custom_components.rain_radar.api import (
    RainRadarApiError,
    RainRadarApiRateLimitedError,
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
    ) -> None:
        self.payload = payload
        self.error = error
        self.cache = cache
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
async def test_dmi_rate_limit_reuses_stale_cache() -> None:
    """Test DMI can reuse stale provider cache when rate limited."""
    expired_cache = CacheMetadata(
        fetched_at=datetime.now(UTC) - timedelta(minutes=20),
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    client = FakeClient(_payload(), cache=expired_cache)
    provider = DmiProvider(client)
    await provider.async_get_precipitation_forecast(
        Location(55.715, 12.561),
        _options(),
    )
    client.error = RainRadarApiRateLimitedError("Provider rate limited request")

    forecast = await provider.async_get_rain_risk(
        Location(55.715, 12.561),
        _options(),
    )

    assert forecast.max_probability == 100
    assert forecast.is_stale is True
    assert len(client.calls) == 2


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
