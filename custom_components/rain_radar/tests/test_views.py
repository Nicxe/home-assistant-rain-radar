"""Tests for Rain Radar HTTP views."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from homeassistant.components.http.auth import STORAGE_KEY

from custom_components.rain_radar.const import DOMAIN
from custom_components.rain_radar.providers.models import (
    CoverageStatus,
    Location,
    ProviderStatus,
    RadarBounds,
    RadarFrame,
    RadarFrameSet,
)
from custom_components.rain_radar.views import (
    RainRadarFrameImageView,
    RainRadarFramesView,
)


class FakeRequest:
    """Minimal request object for direct view calls."""

    def __init__(self, hass) -> None:
        self.app = {"hass": hass}


class FakeImageClient:
    """Fake image client."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def async_get_bytes(self, cache_key, url, **kwargs):
        """Return a fake PNG payload."""
        self.calls.append((cache_key, url))
        return b"png", SimpleNamespace(expires_at=None), "image/png"


def _runtime_data():
    frame = RadarFrame(
        frame_id="regnradar-nordic-obs-test",
        time=datetime(2026, 6, 28, 8, tzinfo=UTC),
        source_url="https://api.regnradar.se/radar/file/test.png",
        image_cache_key="regnradar_radar_image_test",
    )
    data = SimpleNamespace(
        provider_status=ProviderStatus(
            provider_id="regnradar",
            provider_name="Regnradar",
            attribution=(
                "Radar imagery from Regnradar/Vackertväder; forecast data from "
                "MET Norway"
            ),
        ),
        location=Location(latitude=59.3293, longitude=18.0686),
        options=SimpleNamespace(forecast_provider="met_no"),
        radar_frames=RadarFrameSet(
            frames=[frame],
            bounds=RadarBounds(
                south=52.295184,
                west=3.448806,
                north=71.520959,
                east=40.837085,
            ),
            latest_time=frame.time,
            attribution="Radar imagery from Regnradar/Vackertväder",
            coverage_status=CoverageStatus.OK,
        ),
    )
    image_client = FakeImageClient()
    return SimpleNamespace(
        client=image_client,
        coordinator=SimpleNamespace(data=data),
    )


async def test_frames_view_returns_signed_image_url_without_source_url(
    hass,
    rain_radar_config_entry,
) -> None:
    """Test frame metadata only exposes signed HA image URLs."""
    hass.data[STORAGE_KEY] = "test-refresh-token"
    rain_radar_config_entry.runtime_data = _runtime_data()
    rain_radar_config_entry.add_to_hass(hass)

    response = await RainRadarFramesView().get(
        FakeRequest(hass),
        rain_radar_config_entry.entry_id,
    )
    payload = response.text

    assert response.status == 200
    assert f"/api/{DOMAIN}/{rain_radar_config_entry.entry_id}/frames/" in payload
    assert "image_url" in payload
    assert "source_url" not in payload
    assert "https://api.met.no" not in payload
    assert "https://api.regnradar.se" not in payload
    assert "bounds" in payload
    assert "overlay_mode" in payload
    assert '"radar_provider":"regnradar"' in payload
    assert '"forecast_provider":"met_no"' in payload
    assert "regnradar" in payload


async def test_frame_image_view_rejects_unknown_frame_id(
    hass,
    rain_radar_config_entry,
) -> None:
    """Test image view does not proxy arbitrary frame IDs."""
    rain_radar_config_entry.runtime_data = _runtime_data()
    rain_radar_config_entry.add_to_hass(hass)

    response = await RainRadarFrameImageView().get(
        FakeRequest(hass),
        rain_radar_config_entry.entry_id,
        "unknown-frame",
    )

    assert response.status == 404
    assert rain_radar_config_entry.runtime_data.client.calls == []


async def test_frame_image_view_fetches_known_frame(
    hass,
    rain_radar_config_entry,
) -> None:
    """Test image view fetches only the validated provider frame URL."""
    rain_radar_config_entry.runtime_data = _runtime_data()
    rain_radar_config_entry.add_to_hass(hass)

    response = await RainRadarFrameImageView().get(
        FakeRequest(hass),
        rain_radar_config_entry.entry_id,
        "regnradar-nordic-obs-test",
    )

    assert response.status == 200
    assert response.content_type == "image/png"
    assert rain_radar_config_entry.runtime_data.client.calls == [
        (
            "regnradar_radar_image_test",
            "https://api.regnradar.se/radar/file/test.png",
        )
    ]
