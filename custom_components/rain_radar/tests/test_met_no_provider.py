"""Tests for MET Norway provider normalization."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from custom_components.rain_radar.providers.met_no import MetNoProvider
from custom_components.rain_radar.providers.models import (
    CacheMetadata,
    Location,
    RainRadarOptions,
)

from .conftest import load_fixture


class FakeClient:
    """Fake API client returning fixture payloads."""

    def __init__(self, payloads: dict[str, dict[str, Any] | list[Any]]) -> None:
        self.payloads = payloads

    async def async_get_json(self, cache_key: str, url: str, **kwargs):
        return self.payloads[cache_key], CacheMetadata(fetched_at=datetime.now(UTC))


def _options() -> RainRadarOptions:
    return RainRadarOptions(
        contact="rain-radar@example.com",
        forecast_provider="met_no",
        radar_area="nordic",
        rain_threshold=0.1,
        rain_soon_window_minutes=60,
        sample_radius_m=1000,
        rain_risk_horizon_hours=12,
    )


def _shift_nowcast(payload: dict[str, Any]) -> dict[str, Any]:
    shifted = deepcopy(payload)
    now = datetime.now(UTC)
    shifted["properties"]["meta"]["updated_at"] = now.isoformat()
    for offset, item in zip(
        (-5, 20, 60), shifted["properties"]["timeseries"], strict=False
    ):
        item["time"] = (now + timedelta(minutes=offset)).isoformat()
    return shifted


def _shift_locationforecast(payload: dict[str, Any]) -> dict[str, Any]:
    shifted = deepcopy(payload)
    now = datetime.now(UTC)
    shifted["properties"]["meta"]["updated_at"] = now.isoformat()
    for index, item in enumerate(shifted["properties"]["timeseries"], start=1):
        item["time"] = (now + timedelta(hours=index)).isoformat()
    return shifted


@pytest.mark.asyncio
async def test_nowcast_fixture_calculates_rain_arrival() -> None:
    """Test nowcast fixture produces rain soon and arrival data."""
    provider = MetNoProvider(
        FakeClient(
            {"met_no_nowcast": _shift_nowcast(load_fixture("met_nowcast_rain.json"))}
        )
    )

    forecast = await provider.async_get_precipitation_forecast(
        Location(59.3293, 18.0686),
        _options(),
    )

    assert forecast.current_precipitation == 0.0
    assert forecast.rain_now is False
    assert forecast.rain_soon is True
    assert forecast.rain_arrival_minutes is not None
    assert forecast.rain_arrival_minutes <= 25


@pytest.mark.asyncio
async def test_nowcast_returns_zero_arrival_when_already_raining() -> None:
    """Test rain arrival is immediate when current precipitation is rain."""
    now = datetime.now(UTC)
    provider = MetNoProvider(
        FakeClient(
            {
                "met_no_nowcast": {
                    "properties": {
                        "meta": {"updated_at": now.isoformat()},
                        "timeseries": [
                            {
                                "time": (now - timedelta(minutes=5)).isoformat(),
                                "data": {
                                    "instant": {
                                        "details": {"precipitation_rate": 0.4}
                                    }
                                },
                            },
                            {
                                "time": (now + timedelta(minutes=20)).isoformat(),
                                "data": {
                                    "instant": {
                                        "details": {"precipitation_rate": 0.0}
                                    }
                                },
                            },
                        ],
                    }
                }
            }
        )
    )

    forecast = await provider.async_get_precipitation_forecast(
        Location(59.3293, 18.0686),
        _options(),
    )

    assert forecast.rain_now is True
    assert forecast.rain_soon is True
    assert forecast.rain_arrival_minutes == 0


@pytest.mark.asyncio
async def test_nowcast_fixture_handles_no_precipitation() -> None:
    """Test dry nowcast fixture."""
    provider = MetNoProvider(
        FakeClient(
            {"met_no_nowcast": _shift_nowcast(load_fixture("met_nowcast_dry.json"))}
        )
    )

    forecast = await provider.async_get_precipitation_forecast(
        Location(59.3293, 18.0686),
        _options(),
    )

    assert forecast.current_precipitation == 0.0
    assert forecast.rain_soon is False
    assert forecast.rain_arrival_minutes is None


@pytest.mark.asyncio
async def test_locationforecast_fixture_preserves_rain_risk_behavior() -> None:
    """Test migrated MET Rain Risk 12-hour behavior."""
    provider = MetNoProvider(
        FakeClient(
            {
                "met_no_locationforecast": _shift_locationforecast(
                    load_fixture("met_locationforecast_rain_risk.json")
                )
            }
        )
    )

    forecast = await provider.async_get_rain_risk(
        Location(59.3293, 18.0686),
        _options(),
    )

    assert forecast.max_probability == 85
    assert len(forecast.hourly) == 3
    assert forecast.hourly[1].probability == 85
    assert forecast.hourly[1].precipitation_amount == 3.0
    assert forecast.hourly[1].symbol_code == "heavyrain"


@pytest.mark.asyncio
async def test_radar_fixture_parses_frames() -> None:
    """Test radar frame fixture parsing."""
    provider = MetNoProvider(
        FakeClient({"met_no_radar_available": load_fixture("met_radar_frames.json")})
    )

    frames = await provider.async_get_radar_frames(
        Location(59.3293, 18.0686), _options()
    )

    assert len(frames.frames) == 2
    assert frames.animation_url is not None
    assert frames.latest_time is not None
    assert frames.bounds is not None
    assert frames.bounds.south == pytest.approx(52.295184)
    assert frames.product_id == "5level_reflectivity"
    assert (
        frames.frames[0].frame_id
        == "met-no-nordic-5level-reflectivity-20260628T080000Z"
    )
    assert frames.frames[0].source_url.startswith("https://api.met.no/")
    assert frames.frames[0].image_cache_key.startswith("met_no_radar_image_")


@pytest.mark.asyncio
async def test_radar_available_prefers_loadable_nordic_image_frames() -> None:
    """Test MET radar availability ignores animation URLs with time parameters."""
    provider = MetNoProvider(
        FakeClient(
            {
                "met_no_radar_available": [
                    {
                        "params": {
                            "area": "xband",
                            "content": "image",
                            "time": "2026-06-28T14:55:00Z",
                            "type": "5level_reflectivity",
                        },
                        "uri": "https://api.met.no/weatherapi/radar/2.0/?area=xband&content=image&time=2026-06-28T14%3A55%3A00Z&type=5level_reflectivity",
                    },
                    {
                        "params": {
                            "area": "nordic",
                            "content": "image",
                            "time": "2026-06-28T14:50:00Z",
                            "type": "5level_reflectivity",
                        },
                        "uri": "https://api.met.no/weatherapi/radar/2.0/?area=nordic&content=image&time=2026-06-28T14%3A50%3A00Z&type=5level_reflectivity",
                    },
                    {
                        "params": {
                            "area": "nordic",
                            "content": "animation",
                            "time": "2026-06-28T14:50:00Z",
                            "type": "5level_reflectivity",
                        },
                        "uri": "https://api.met.no/weatherapi/radar/2.0/?area=nordic&content=animation&time=2026-06-28T14%3A50%3A00Z&type=5level_reflectivity",
                    },
                    {
                        "params": {
                            "area": "nordic",
                            "content": "image",
                            "time": "2026-06-28T14:45:00Z",
                            "type": "reflectivity",
                        },
                        "uri": "https://api.met.no/weatherapi/radar/2.0/?area=nordic&content=image&time=2026-06-28T14%3A45%3A00Z&type=reflectivity",
                    },
                    {
                        "params": {
                            "area": "nordic",
                            "content": "image",
                            "time": "2026-06-28T14:40:00Z",
                            "type": "5level_reflectivity",
                        },
                        "uri": "https://example.com/not-a-radar-image.png",
                    },
                ]
            }
        )
    )

    frames = await provider.async_get_radar_frames(
        Location(59.3293, 18.0686), _options()
    )

    assert len(frames.frames) == 1
    assert frames.frames[0].source_url.endswith(
        "content=image&time=2026-06-28T14%3A50%3A00Z&type=5level_reflectivity"
    )
    assert frames.frames[0].time == datetime(2026, 6, 28, 14, 50, tzinfo=UTC)
    assert frames.latest_time == datetime(2026, 6, 28, 14, 50, tzinfo=UTC)


@pytest.mark.asyncio
async def test_radar_locations_json_overrides_fallback_bounds() -> None:
    """Test MET radar locations JSON is used for map bounds."""
    provider = MetNoProvider(
        FakeClient(
            {
                "met_no_radar_available": load_fixture("met_radar_frames.json"),
                "met_no_radar_locations": {
                    "features": [
                        {
                            "id": "nordic",
                            "geometry": {
                                "type": "Polygon",
                                "coordinates": [
                                    [
                                        [3.0, 52.0],
                                        [41.0, 52.0],
                                        [41.0, 72.0],
                                        [3.0, 72.0],
                                        [3.0, 52.0],
                                    ]
                                ],
                            },
                            "properties": {"id": "nordic"},
                        }
                    ]
                },
            }
        )
    )

    frames = await provider.async_get_radar_frames(
        Location(59.3293, 18.0686), _options()
    )

    assert frames.bounds is not None
    assert frames.bounds.south == 52.0
    assert frames.bounds.west == 3.0
    assert frames.bounds.north == 72.0
    assert frames.bounds.east == 41.0
