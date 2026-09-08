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


async def test_metadata_failure_does_not_reuse_healthy_delivery_status():
    """A coverage area remains geographical while a failed request is visible."""
    from unittest.mock import AsyncMock

    from custom_components.rain_radar.api import RainRadarApiTemporaryError

    client = FakeClient(_payload())
    provider = RegnradarProvider(client, forecast_provider=FakeForecastProvider())
    await provider.async_get_radar_frames(Location(59, 18), _options())
    client.async_get_json = AsyncMock(
        side_effect=RainRadarApiTemporaryError("Radar down", reason="server_error")
    )
    frames = await provider.async_get_radar_frames(Location(59, 18), _options())
    assert not frames.frames
    assert frames.coverage_status == CoverageStatus.TEMPORARILY_UNAVAILABLE
    assert provider.radar_error_reason == "server_error"


async def test_empty_images_with_coverage_polygons_are_not_healthy():
    """Coverage polygons cannot stand in for actual radar images."""
    payload = _payload()
    payload["sweden"]["images"] = []
    provider = RegnradarProvider(
        FakeClient(payload), forecast_provider=FakeForecastProvider()
    )
    frames = await provider.async_get_radar_frames(Location(59, 18), _options("sweden"))
    assert frames.coverage_status == CoverageStatus.TEMPORARILY_UNAVAILABLE


async def test_old_images_are_stale_despite_fresh_http_cache():
    """HTTP metadata cannot make an old radar observation current."""
    provider = RegnradarProvider(
        FakeClient(_payload()), forecast_provider=FakeForecastProvider()
    )
    frames = await provider.async_get_radar_frames(Location(59, 18), _options())
    assert frames.is_stale


async def test_multiple_locations_share_regnradar_metadata(hass, monkeypatch):
    """The common area listing is fetched only once across config entries."""
    from custom_components.rain_radar import api as api_module
    from custom_components.rain_radar.api import RainRadarApiClient

    from .test_api import _Response, _SequenceSession

    session = _SequenceSession(
        _Response(200, payload=_payload(), headers={"Cache-Control": "max-age=60"})
    )
    monkeypatch.setattr(
        api_module.aiohttp_client, "async_get_clientsession", lambda hass: session
    )
    providers = [
        RegnradarProvider(
            RainRadarApiClient(hass, "same-contact"),
            forecast_provider=FakeForecastProvider(),
        )
        for _ in range(2)
    ]
    await providers[0].async_get_radar_frames(Location(59, 18), _options("sweden"))
    await providers[1].async_get_radar_frames(Location(60, 17), _options("nordic"))
    assert len(session.request_headers) == 1


async def test_forecast_only_images_do_not_invent_recent_observation():
    """Future imagery must not hide the absence of observed radar data."""
    payload = _payload()
    payload["nordic"]["images"] = [payload["nordic"]["images"][1]]
    provider = RegnradarProvider(
        FakeClient(payload), forecast_provider=FakeForecastProvider()
    )
    frames = await provider.async_get_radar_frames(Location(59, 18), _options())
    assert frames.latest_time is None
    assert not frames.is_stale


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("time_utc", "2026-09-06 10:00:00 UTC"),
        ("time_js", "2026-09-06 10:00:00 Z"),
        ("created_at", "2026-09-06 10:00:00 UTC"),
    ],
)
async def test_live_regnradar_timestamp_format(field, value):
    """Normalize the UTC suffixes used by live Regnradar metadata."""
    payload = {
        "denmark": {
            "images": [
                {
                    "image_url": "//api.regnradar.se/radar/file/256035.png",
                    "type": "obs",
                    field: value,
                }
            ]
        }
    }
    provider = RegnradarProvider(
        FakeClient(payload), forecast_provider=FakeForecastProvider()
    )
    frames = await provider.async_get_radar_frames(
        Location(59, 18), _options("denmark")
    )
    assert frames.latest_time == datetime(2026, 9, 6, 10, tzinfo=UTC)


async def test_http_revalidation_deadline_does_not_age_fresh_observation():
    """An expired HTTP cache is not an expired radar observation."""
    from unittest.mock import AsyncMock

    now = datetime.now(UTC)
    client = FakeClient({})
    client.async_get_json = AsyncMock(
        return_value=(
            {
                "denmark": {
                    "images": [
                        {
                            "image_url": "//api.regnradar.se/radar/file/256035.png",
                            "time_utc": (now - timedelta(minutes=20)).isoformat(),
                            "type": "obs",
                        }
                    ]
                }
            },
            CacheMetadata(fetched_at=now, expires_at=now - timedelta(seconds=1)),
        )
    )
    provider = RegnradarProvider(client, forecast_provider=FakeForecastProvider())
    frames = await provider.async_get_radar_frames(
        Location(59, 18), _options("denmark")
    )
    assert not frames.is_stale
    assert provider.radar_last_success == now
