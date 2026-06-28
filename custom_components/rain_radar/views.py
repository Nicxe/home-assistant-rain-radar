"""Authenticated HTTP endpoints for the Rain Radar card."""

from __future__ import annotations

from homeassistant.components.http import HomeAssistantView
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
        return self.json(
            {
                "entry_id": entry_id,
                "provider": data.provider_status.provider_name,
                "attribution": frame_set.attribution
                or data.provider_status.attribution,
                "coverage_status": frame_set.coverage_status.value,
                "is_stale": frame_set.is_stale,
                "latest_time": frame_set.latest_time.isoformat()
                if frame_set.latest_time
                else None,
                "animation_url": frame_set.animation_url,
                "frames": [
                    {
                        "time": frame.time.isoformat() if frame.time else None,
                        "url": frame.url,
                        "type": frame.frame_type,
                        "label": frame.label,
                    }
                    for frame in frame_set.frames
                ],
            }
        )


def async_register_http_views(hass: HomeAssistant) -> None:
    """Register HTTP views once."""
    key = f"{DOMAIN}_http_views_registered"
    if hass.data.get(key):
        return
    if hass.http is None:
        return
    hass.http.register_view(RainRadarFramesView())
    hass.data[key] = True
