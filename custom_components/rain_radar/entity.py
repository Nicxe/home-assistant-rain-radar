"""Shared Rain Radar entity base."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
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
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        provider_name = "Rain Radar"
        if self.coordinator.data:
            provider_name = self.coordinator.data.provider_status.provider_name
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name=self.coordinator.config_entry.title
            if self.coordinator.config_entry
            else "Rain Radar",
            manufacturer=provider_name,
            model="Rain radar integration",
        )
