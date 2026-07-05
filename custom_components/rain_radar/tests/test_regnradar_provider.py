"""Tests for Regnradar provider normalization."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from custom_components.rain_radar.providers.models import (
    CacheMetadata,
    CoverageStatus,
    Location,
    PrecipitationForecast,
    RainRadarOptions,
    RainRiskForecast,
)
from custom_components.rain_radar.providers.regnradar import RegnradarProvider


class FakeClient:
    """Fake API client returning fixture payloads."""

    def __init__(self, payload: dict[str, Any] | list[Any]) -> None:
        self.payload = payload
        self.calls: list[tuple[str, str]] = []

    async def async_get_json(self, cache_key: str, url: str, **kwargs):
        """Return fake JSON payload."""
        self.calls.append((cache_key, url))
        return self.payload, CacheMetadata(
            fetched_at=datetime(2026, 6, 29, 4, 15, tzinfo=UTC),
            expires_at=datetime.now(UTC) + timedelta(hours=4),
        )


class FakeForecastProvider:
    """Fake point forecast provider delegated to by Regnradar."""

    provider_id = "met_no"
    provider_name = "MET Norway"
    attribution = "Data from MET Norway"
    coverage_status = CoverageStatus.OK

    async def async_get_precipitation_forecast(self, location, options):
        """Return fake precipitation forecast."""
        return PrecipitationForecast(coverage_status=CoverageStatus.OK)

    async def async_get_rain_risk(self, location, options):
        """Return fake rain risk."""
        return RainRiskForecast(max_probability=42)

    async def async_get_radar_frames(self, location, options):
        """Not used by Regnradar."""
        raise AssertionError("Regnradar should fetch radar frames itself")


def _options(area: str = "nordic") -> RainRadarOptions:
    return RainRadarOptions(
        contact="rain-radar@example.com",
        forecast_provider="met_no",
        radar_area=area,
        rain_threshold=0.1,
        rain_soon_window_minutes=60,
        sample_radius_m=1000,
        rain_risk_horizon_hours=12,
    )


def _payload() -> dict[str, Any]:
    return {
        "primary": "nordic",
        "nordic": {
            "images": [
                {
                    "image_url": "//api.regnradar.se/radar/file/a.png",
                    "time_utc": "2026-06-29T04:00:00Z",
                    "time_local": "06:00",
                    "type": "obs",
                    "source": "MET",
                },
                {
                    "image_url": "//api.regnradar.se/radar/file/b.png",
                    "time_utc": "2026-06-29T04:05:00Z",
                    "time_local": "06:05",
                    "type": "fcst",
                    "source": "MET",
                },
                {
                    "image_url": "https://example.com/not-proxied.png",
                    "time_utc": "2026-06-29T04:10:00Z",
                    "type": "obs",
                },
            ],
            "no_coverage": [],
        },
        "sweden": {
            "images": [
                {
                    "image_url": "//api.regnradar.se/radar/file/s.png",
                    "time_utc": "2026-06-29T04:00:00Z",
                    "time_local": "06:00",
                    "type": "obs",
                    "source": "SMHI",
                }
            ],
            "no_coverage": [
                {"lat": 63.641, "lng": 18.406, "location": "Ornskoldsvik radar"}
            ],
        },
        "denmark": {
            "images": [
                {
                    "image_url": "//api.regnradar.se/radar/file/d.png",
                    "time_utc": "2026-06-29T04:00:00Z",
                    "time_local": "06:00",
                    "type": "obs",
                    "source": "DMI",
                }
            ],
            "no_coverage": [],
        },
    }


@pytest.mark.asyncio
async def test_regnradar_parses_nordic_coverage_frames() -> None:
    """Test Regnradar Nordic frames are normalized for Leaflet overlay rendering."""
    provider = RegnradarProvider(
        FakeClient(_payload()),
        forecast_provider=FakeForecastProvider(),
    )

    frames = await provider.async_get_radar_frames(
        Location(59.3293, 18.0686),
        _options(),
    )

    assert len(frames.frames) == 2
    assert frames.overlay_mode == "regnradar_coverage"
    assert frames.product_id == "regnradar_nordic"
    assert frames.projection_id == "epsg3857_leaflet_image_overlay"
    assert frames.bounds is not None
    assert frames.bounds.south == pytest.approx(53.0841628421789)
    assert frames.bounds.west == pytest.approx(-8.03410177882381)
    assert frames.image_size.width == 2392
    assert frames.image_size.height == 2265
    assert frames.frames[0].frame_id == "regnradar-nordic-obs-20260629T040000Z"
    assert frames.frames[0].source_url == "https://api.regnradar.se/radar/file/a.png"
    assert frames.frames[1].frame_type == "fcst"
    assert frames.frames[1].label == "06:05"
    assert frames.latest_time == datetime(2026, 6, 29, 4, 0, tzinfo=UTC)
    assert provider.client.calls == [
        ("regnradar_radar", "https://api.regnradar.se/radar")
    ]


@pytest.mark.asyncio
async def test_regnradar_selects_configured_area() -> None:
    """Test Regnradar area selection changes bounds and product metadata."""
    provider = RegnradarProvider(
        FakeClient(_payload()),
        forecast_provider=FakeForecastProvider(),
    )

    frames = await provider.async_get_radar_frames(
        Location(59.3293, 18.0686),
        _options("sweden"),
    )

    assert len(frames.frames) == 1
    assert frames.product_id == "regnradar_sweden"
    assert frames.bounds is not None
    assert frames.bounds.south == pytest.approx(53.6813981284917)
    assert frames.bounds.east == pytest.approx(29.7811924432583)
    assert frames.frames[0].frame_id == "regnradar-sweden-obs-20260629T040000Z"


@pytest.mark.asyncio
async def test_regnradar_rejects_arbitrary_image_urls() -> None:
    """Test Regnradar parser never produces arbitrary proxy URLs."""
    provider = RegnradarProvider(
        FakeClient(
            {
                "nordic": {
                    "images": [
                        {
                            "image_url": "https://api.regnradar.se/not-radar/file.png",
                            "time_utc": "2026-06-29T04:00:00Z",
                        },
                        {
                            "image_url": "https://example.com/radar/file/a.png",
                            "time_utc": "2026-06-29T04:05:00Z",
                        },
                    ]
                }
            }
        ),
        forecast_provider=FakeForecastProvider(),
    )

    frames = await provider.async_get_radar_frames(
        Location(59.3293, 18.0686),
        _options(),
    )

    assert frames.frames == []
    assert frames.coverage_status == CoverageStatus.TEMPORARILY_UNAVAILABLE


@pytest.mark.asyncio
async def test_regnradar_delegates_point_forecasts_to_fallback_provider() -> None:
    """Test point forecast sensors keep using the fallback provider."""
    provider = RegnradarProvider(
        FakeClient(_payload()),
        forecast_provider=FakeForecastProvider(),
    )

    precipitation = await provider.async_get_precipitation_forecast(
        Location(59.3293, 18.0686),
        _options(),
    )
    rain_risk = await provider.async_get_rain_risk(
        Location(59.3293, 18.0686),
        _options(),
    )

    assert precipitation.coverage_status == CoverageStatus.OK
    assert rain_risk.max_probability == 42
    assert provider.coverage_status == CoverageStatus.UNKNOWN
