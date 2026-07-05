"""Authenticated HTTP endpoints for the Rain Radar card."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.components.http.auth import async_sign_path
from homeassistant.core import HomeAssistant

from .const import DOMAIN


class RainRadarFramesView(HomeAssistantView):
    """Return normalized radar frame metadata for one config entry."""

    url = f"/api/{DOMAIN}/{{entry_id}}/frames"
    name = f"api:{DOMAIN}:frames"
    requires_auth = True

    async def get(self, request, entry_id: str):
        """Handle frame metadata requests."""
        hass: HomeAssistant = request.app["hass"]
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN or not entry.runtime_data:
            return self.json({"error": "Entry not found"}, status_code=404)

        data = entry.runtime_data.coordinator.data
        if data is None:
            return self.json({"frames": [], "attribution": None})

        frame_set = data.radar_frames
        frame_types = sorted({frame.frame_type for frame in frame_set.frames})
        return self.json(
            {
                "entry_id": entry_id,
                "provider": data.provider_status.provider_name,
                "radar_provider": "regnradar",
                "radar_area": data.options.radar_area,
                "forecast_provider": data.options.forecast_provider,
                "frame_types": frame_types,
                "has_forecast_frames": any(
                    frame_type.lower() in {"fcst", "forecast"}
                    for frame_type in frame_types
                ),
                "location": {
                    "latitude": data.location.latitude,
                    "longitude": data.location.longitude,
                },
                "attribution": frame_set.attribution
                or data.provider_status.attribution,
                "coverage_status": frame_set.coverage_status.value,
                "is_stale": frame_set.is_stale,
                "latest_time": frame_set.latest_time.isoformat()
                if frame_set.latest_time
                else None,
                "expires_at": frame_set.cache.expires_at.isoformat()
                if frame_set.cache.expires_at
                else None,
                "image_size": {
                    "width": frame_set.image_size.width,
                    "height": frame_set.image_size.height,
                },
                "bounds": _bounds_json(frame_set.bounds),
                "projection_id": frame_set.projection_id,
                "product_id": frame_set.product_id,
                "overlay_mode": frame_set.overlay_mode,
                "color_scale": [
                    {"label": step.label, "color": step.color}
                    for step in frame_set.color_scale
                ],
                "frames": [
                    {
                        "id": frame.frame_id,
                        "time": frame.time.isoformat() if frame.time else None,
                        "image_url": async_sign_path(
                            hass,
                            f"/api/{DOMAIN}/{entry_id}/frames/{frame.frame_id}/image",
                            timedelta(minutes=10),
                        ),
                        "type": frame.frame_type,
                        "label": frame.label,
                        "content_type": frame.content_type,
                    }
                    for frame in frame_set.frames
                ],
            }
        )


class RainRadarFrameImageView(HomeAssistantView):
    """Return a signed radar frame image for one config entry."""

    url = f"/api/{DOMAIN}/{{entry_id}}/frames/{{frame_id}}/image"
    name = f"api:{DOMAIN}:frame_image"
    requires_auth = True

    async def get(self, request, entry_id: str, frame_id: str):
        """Handle a radar image request."""
        hass: HomeAssistant = request.app["hass"]
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN or not entry.runtime_data:
            return self.json({"error": "Entry not found"}, status_code=404)

        data = entry.runtime_data.coordinator.data
        if data is None:
            return self.json({"error": "Radar data not available"}, status_code=404)

        frame = next(
            (
                candidate
                for candidate in data.radar_frames.frames
                if candidate.frame_id == frame_id
            ),
            None,
        )
        if frame is None:
            return self.json({"error": "Frame not found"}, status_code=404)

        image, cache, content_type = await entry.runtime_data.client.async_get_bytes(
            frame.image_cache_key,
            frame.source_url,
            request_timeout=15,
            accept=frame.content_type,
        )
        return web.Response(
            body=image,
            content_type=content_type,
            headers=_image_cache_headers(cache.expires_at),
        )


def _bounds_json(bounds):
    """Return JSON-safe Leaflet bounds."""
    if bounds is None:
        return None
    return {
        "south": bounds.south,
        "west": bounds.west,
        "north": bounds.north,
        "east": bounds.east,
        "leaflet": [[bounds.south, bounds.west], [bounds.north, bounds.east]],
    }


def _image_cache_headers(expires_at: datetime | None) -> dict[str, str]:
    """Build conservative cache headers for proxied radar images."""
    if expires_at is None:
        return {"Cache-Control": "private, max-age=60"}
    max_age = max(0, round((expires_at - datetime.now(UTC)).total_seconds()))
    return {"Cache-Control": f"private, max-age={max_age}"}


def async_register_http_views(hass: HomeAssistant) -> None:
    """Register HTTP views once."""
    key = f"{DOMAIN}_http_views_registered"
    if hass.data.get(key):
        return
    if hass.http is None:
        return
    hass.http.register_view(RainRadarFramesView())
    hass.http.register_view(RainRadarFrameImageView())
    hass.data[key] = True
