"""Sensor platform for Rain Radar."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ATTR_ENTRY_ID,
    ATTR_FORECAST_PROVIDER_ID,
    ATTR_FORECAST_SAMPLES,
    ATTR_HOURLY,
    ATTR_IS_STALE,
    ATTR_LAST_UPDATED,
    ATTR_PROVIDER_ID,
    ATTR_RADAR_AREA,
    ATTR_RADAR_PROVIDER_ID,
    ATTR_STATUS,
    ATTRIBUTION,
    DEFAULT_RADAR_PROVIDER,
)
from .coordinator import RainRadarCoordinator, RainRadarData
from .entity import RainRadarEntity


@dataclass(frozen=True, kw_only=True)
class RainRadarSensorDescription(SensorEntityDescription):
    """Sensor description."""

    value_fn: Callable[[RainRadarData], Any]
    attrs_fn: Callable[[RainRadarData], dict[str, Any]] | None = None


def _data_age_minutes(data: RainRadarData) -> int | None:
    timestamps = [
        data.precipitation.updated_at,
        data.rain_risk.updated_at,
        data.radar_frames.latest_time,
        data.updated_at,
    ]
    latest = max((value for value in timestamps if value is not None), default=None)
    if latest is None:
        return None
    return max(0, round((datetime.now(UTC) - latest).total_seconds() / 60))


def _rain_risk_attrs(data: RainRadarData) -> dict[str, Any]:
    return {
        ATTRIBUTION: data.provider_status.attribution,
        ATTR_HOURLY: {
            hour.time.isoformat(): {
                "probability": hour.probability,
                "precipitation_amount": hour.precipitation_amount,
                "symbol_code": hour.symbol_code,
            }
            for hour in data.rain_risk.hourly[: data.options.rain_risk_horizon_hours]
        },
        ATTR_IS_STALE: data.rain_risk.is_stale,
        ATTR_LAST_UPDATED: data.rain_risk.updated_at.isoformat()
        if data.rain_risk.updated_at
        else None,
    }


def _precipitation_attrs(data: RainRadarData) -> dict[str, Any]:
    return {
        ATTRIBUTION: data.provider_status.attribution,
        ATTR_FORECAST_SAMPLES: [
            {
                "time": sample.time.isoformat(),
                "precipitation_rate": sample.precipitation_rate,
            }
            for sample in data.precipitation.samples[:24]
        ],
        ATTR_IS_STALE: data.precipitation.is_stale,
        ATTR_ENTRY_ID: data.provider_status.provider_id,
        "rain_threshold": data.options.rain_threshold,
        "rain_soon_window_minutes": data.options.rain_soon_window_minutes,
    }


def _provider_attrs(data: RainRadarData) -> dict[str, Any]:
    return {
        ATTR_PROVIDER_ID: data.provider_status.provider_id,
        ATTR_RADAR_PROVIDER_ID: DEFAULT_RADAR_PROVIDER,
        ATTR_RADAR_AREA: data.options.radar_area,
        ATTR_FORECAST_PROVIDER_ID: data.options.forecast_provider,
        ATTR_STATUS: data.provider_status.health.value,
        "coverage_status": data.provider_status.coverage_status.value,
        "radar_coverage_status": data.radar_frames.coverage_status.value,
        "radar_frame_count": len(data.radar_frames.frames),
        ATTRIBUTION: data.provider_status.attribution,
    }


SENSORS: tuple[RainRadarSensorDescription, ...] = (
    RainRadarSensorDescription(
        key="precipitation_now",
        translation_key="precipitation_now",
        native_unit_of_measurement="mm/h",
        icon="mdi:weather-pouring",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.precipitation.current_precipitation,
        attrs_fn=_precipitation_attrs,
    ),
    RainRadarSensorDescription(
        key="rain_risk_12h",
        translation_key="rain_risk_12h",
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:weather-rainy",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.rain_risk.max_probability,
        attrs_fn=_rain_risk_attrs,
    ),
    RainRadarSensorDescription(
        key="rain_arrival",
        translation_key="rain_arrival",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        icon="mdi:timer-outline",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.precipitation.rain_arrival_minutes,
    ),
    RainRadarSensorDescription(
        key="data_age",
        translation_key="data_age",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        icon="mdi:clock-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_data_age_minutes,
    ),
    RainRadarSensorDescription(
        key="provider",
        translation_key="provider",
        icon="mdi:database-eye-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.provider_status.provider_name,
        attrs_fn=_provider_attrs,
    ),
    RainRadarSensorDescription(
        key="latest_radar_time",
        translation_key="latest_radar_time",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:radar",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.radar_frames.latest_time,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Rain Radar sensors."""
    coordinator: RainRadarCoordinator = entry.runtime_data.coordinator
    async_add_entities(
        [
            RainRadarSensor(coordinator, entry.entry_id, description)
            for description in SENSORS
        ]
    )


class RainRadarSensor(RainRadarEntity, SensorEntity):
    """Rain Radar sensor."""

    entity_description: RainRadarSensorDescription

    def __init__(
        self,
        coordinator: RainRadarCoordinator,
        entry_id: str,
        description: RainRadarSensorDescription,
    ) -> None:
        """Initialize sensor."""
        super().__init__(coordinator, entry_id, description.key)
        self.entity_description = description
        self._attr_translation_key = description.translation_key

    @property
    def native_value(self) -> Any:
        """Return native value."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def available(self) -> bool:
        """Return availability."""
        return self.coordinator.data is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes."""
        if self.coordinator.data is None:
            return {}
        attrs = {
            ATTRIBUTION: self.coordinator.data.provider_status.attribution,
            ATTR_ENTRY_ID: self._entry_id,
        }
        if self.entity_description.attrs_fn:
            attrs.update(self.entity_description.attrs_fn(self.coordinator.data))
        attrs[ATTR_ENTRY_ID] = self._entry_id
        return attrs
