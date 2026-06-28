"""Binary sensor platform for Rain Radar."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ATTR_ENTRY_ID, ATTR_IS_STALE, ATTRIBUTION
from .coordinator import RainRadarCoordinator, RainRadarData
from .entity import RainRadarEntity


@dataclass(frozen=True, kw_only=True)
class RainRadarBinarySensorDescription(BinarySensorEntityDescription):
    """Binary sensor description."""

    value_fn: Callable[[RainRadarData], bool | None]


BINARY_SENSORS: tuple[RainRadarBinarySensorDescription, ...] = (
    RainRadarBinarySensorDescription(
        key="raining_now",
        translation_key="raining_now",
        device_class=BinarySensorDeviceClass.MOISTURE,
        icon="mdi:weather-rainy",
        value_fn=lambda data: data.precipitation.rain_now,
    ),
    RainRadarBinarySensorDescription(
        key="rain_soon",
        translation_key="rain_soon",
        device_class=BinarySensorDeviceClass.MOISTURE,
        icon="mdi:weather-pouring",
        value_fn=lambda data: data.precipitation.rain_soon,
    ),
    RainRadarBinarySensorDescription(
        key="radar_coverage",
        translation_key="radar_coverage",
        icon="mdi:radar",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.provider_status.coverage_status.value == "ok",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Rain Radar binary sensors."""
    coordinator: RainRadarCoordinator = entry.runtime_data.coordinator
    async_add_entities(
        [
            RainRadarBinarySensor(coordinator, entry.entry_id, description)
            for description in BINARY_SENSORS
        ]
    )


class RainRadarBinarySensor(RainRadarEntity, BinarySensorEntity):
    """Rain Radar binary sensor."""

    entity_description: RainRadarBinarySensorDescription

    def __init__(
        self,
        coordinator: RainRadarCoordinator,
        entry_id: str,
        description: RainRadarBinarySensorDescription,
    ) -> None:
        """Initialize binary sensor."""
        super().__init__(coordinator, entry_id, description.key)
        self.entity_description = description
        self._attr_translation_key = description.translation_key

    @property
    def is_on(self) -> bool | None:
        """Return binary sensor state."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def available(self) -> bool:
        """Return availability."""
        return self.coordinator.data is not None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Return extra attributes."""
        if self.coordinator.data is None:
            return {}
        return {
            ATTRIBUTION: self.coordinator.data.provider_status.attribution,
            ATTR_ENTRY_ID: self._entry_id,
            ATTR_IS_STALE: self.coordinator.data.precipitation.is_stale,
        }
