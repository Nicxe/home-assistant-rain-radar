"""Shared Rain Radar entity base."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_NAME, DOMAIN
from .coordinator import RainRadarCoordinator


class RainRadarEntity(CoordinatorEntity[RainRadarCoordinator]):
    """Base entity for Rain Radar."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: RainRadarCoordinator,
        entry_id: str,
        unique_suffix: str,
    ) -> None:
        """Initialize entity."""
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._attr_unique_id = f"{entry_id}_{unique_suffix}"

    @property
    def source_attributes(self) -> dict[str, object]:
        """Expose source-specific health consistently on every entity."""
        data = self.coordinator.data
        if data is None:
            return {}
        return {
            "radar_status": data.radar_status.as_dict(),
            "forecast_status": data.forecast_status.as_dict(),
            "radar_latest_time": data.radar_frames.latest_time.isoformat()
            if data.radar_frames.latest_time
            else None,
            "radar_updated_at": data.radar_frames.updated_at.isoformat()
            if data.radar_frames.updated_at
            else None,
            "forecast_provider": data.options.forecast_provider,
            "radar_provider": data.provider_status.provider_id,
            "resolution_minutes": data.precipitation.resolution_minutes,
            "data_kind": data.precipitation.data_kind,
            "window_complete": data.precipitation.window_complete,
            "is_stale": data.precipitation.is_stale,
            "reason": data.precipitation.reason,
        }

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        provider_name = "Rain Radar"
        if self.coordinator.data:
            provider_name = self.coordinator.data.provider_status.provider_name
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name=self.coordinator.config_entry.options.get(
                CONF_NAME, self.coordinator.config_entry.title
            )
            if self.coordinator.config_entry
            else "Rain Radar",
            manufacturer=provider_name,
            model="Rain radar integration",
        )
