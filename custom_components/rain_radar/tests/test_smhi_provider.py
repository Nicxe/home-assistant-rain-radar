"""Tests for SMHI provider normalization."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from custom_components.rain_radar.api import RainRadarApiError
from custom_components.rain_radar.const import PROVIDER_SMHI
from custom_components.rain_radar.providers.models import (
    CacheMetadata,
    CoverageStatus,
    Location,
    RainRadarOptions,
)
from custom_components.rain_radar.providers.smhi import SmhiProvider


class FakeClient:
    """Fake API client returning a SMHI payload."""

    def __init__(
        self,
        payload: dict[str, Any] | list[Any] | None,
        *,
        error: RainRadarApiError | None = None,
    ) -> None:
        self.payload = payload
        self.error = error
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def async_get_json(self, cache_key: str, url: str, **kwargs):
        """Return fake JSON payload."""
        self.calls.append((cache_key, url, kwargs.get("params", {})))
        await asyncio.sleep(0)
        if self.error is not None:
            raise self.error
        return self.payload, CacheMetadata(
            fetched_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )


def _options() -> RainRadarOptions:
    return RainRadarOptions(
        contact="rain-radar@example.com",
        forecast_provider=PROVIDER_SMHI,
        radar_area="sweden",
        rain_threshold=0.1,
        rain_soon_window_minutes=60,
        sample_radius_m=1000,
        rain_risk_horizon_hours=12,
    )


def _payload() -> dict[str, Any]:
    now = datetime.now(UTC).replace(microsecond=0)
    return {
        "createdTime": now.isoformat(),
        "referenceTime": (now - timedelta(minutes=15)).isoformat(),
        "geometry": {"type": "Point", "coordinates": [18.077207, 59.33036]},
        "timeSeries": [
            {
                "time": (now + timedelta(minutes=10)).isoformat(),
                "intervalParametersStartTime": (
                    now - timedelta(minutes=50)
                ).isoformat(),
                "data": {
                    "precipitation_amount_mean": 0.0,
                    "probability_of_precipitation": 7,
                    "predominant_precipitation_type_at_surface": 1,
                    "symbol_code": 3,
                },
            },
            {
                "time": (now + timedelta(minutes=70)).isoformat(),
                "intervalParametersStartTime": (
                    now + timedelta(minutes=10)
                ).isoformat(),
                "data": {
                    "precipitation_amount_mean": 0.4,
                    "probability_of_precipitation": 75,
                    "predominant_precipitation_type_at_surface": 2,
                    "symbol_code": 8,
                },
            },
            {
                "time": (now + timedelta(minutes=130)).isoformat(),
                "intervalParametersStartTime": (
                    now + timedelta(minutes=70)
                ).isoformat(),
                "data": {
                    "precipitation_amount_mean": 0.2,
                    "probability_of_precipitation": 35,
                    "predominant_precipitation_type_at_surface": 2,
                    "symbol_code": 7,
                },
            },
        ],
    }


@pytest.mark.asyncio
async def test_smhi_precipitation_forecast_calculates_arrival() -> None:
    """Test SMHI SNOW1G precipitation data is normalized."""
    client = FakeClient(_payload())
    provider = SmhiProvider(client)

    forecast = await provider.async_get_precipitation_forecast(
        Location(59.3293, 18.0686),
        _options(),
    )

    assert forecast.coverage_status == CoverageStatus.OK
    assert forecast.current_precipitation == 0.0
    assert forecast.rain_now is False
    assert forecast.rain_soon is True
    assert forecast.rain_arrival_minutes is not None
    assert forecast.rain_arrival_minutes <= 15
    assert len(forecast.samples) == 3
    assert client.calls[0][2]["timeseries"] == 13
    assert "precipitation_amount_mean" in client.calls[0][2]["parameters"]


@pytest.mark.asyncio
async def test_smhi_rain_risk_uses_probability_and_symbol() -> None:
    """Test SMHI precipitation probabilities are exposed as rain risk."""
    provider = SmhiProvider(FakeClient(_payload()))

    forecast = await provider.async_get_rain_risk(
        Location(59.3293, 18.0686),
        _options(),
    )

    assert forecast.max_probability == 75
    assert len(forecast.hourly) == 3
    assert forecast.hourly[1].precipitation_amount == 0.4
    assert forecast.hourly[1].symbol_code == "8"


@pytest.mark.asyncio
async def test_smhi_reuses_forecast_payload_for_concurrent_updates() -> None:
    """Test precipitation and risk sensors share one SMHI request."""
    client = FakeClient(_payload())
    provider = SmhiProvider(client)

    precipitation, rain_risk = await asyncio.gather(
        provider.async_get_precipitation_forecast(
            Location(59.3293, 18.0686), _options()
        ),
        provider.async_get_rain_risk(Location(59.3293, 18.0686), _options()),
    )

    assert precipitation.rain_soon is True
    assert rain_risk.max_probability == 75
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_smhi_outside_coverage_returns_empty_forecast() -> None:
    """Test SMHI outside-coverage errors do not fail the coordinator."""
    provider = SmhiProvider(
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
