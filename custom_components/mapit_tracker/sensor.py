"""Support for Mapit Motorcycle sensors."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfSpeed
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN, MapitDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


def _convert_timestamp(ts):
    """Convert epoch milliseconds to UTC datetime.
    
    Args:
        ts: Epoch timestamp in milliseconds
        
    Returns:
        datetime object in UTC timezone, or None if conversion fails
    """
    if ts is None:
        return None
    try:
        from datetime import timezone
        return datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
    except (ValueError, TypeError):
        return None



@dataclass(frozen=True)
class MapitSensorEntityDescription(SensorEntityDescription):
    """Describes Mapit sensor entity."""

    value_fn: Callable[[dict], StateType] = lambda data: None


SENSORS: tuple[MapitSensorEntityDescription, ...] = (
    MapitSensorEntityDescription(
        key="speed",
        name="Speed",
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        device_class=SensorDeviceClass.SPEED,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:speedometer",
        value_fn=lambda data: data.get("speed"),
    ),
    MapitSensorEntityDescription(
        key="status",
        name="Status",
        icon="mdi:motorbike",
        value_fn=lambda data: data.get("status"),
    ),
    MapitSensorEntityDescription(
        key="battery",
        name="Battery",
        native_unit_of_measurement="%",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.get("battery"),
    ),
    MapitSensorEntityDescription(
        key="gps_accuracy",
        name="GPS Accuracy",
        icon="mdi:crosshairs-gps",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.get("gps_accuracy", data.get("hdop")),
    ),
    MapitSensorEntityDescription(
        key="hdop",
        name="HDOP",
        icon="mdi:map-marker-radius",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.get("hdop", data.get("gps_accuracy")),
    ),
    MapitSensorEntityDescription(
        key="odometer",
        name="Odometer",
        native_unit_of_measurement="km",
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:counter",
        value_fn=lambda data: data.get("odometer"),
    ),
    MapitSensorEntityDescription(
        key="last_coord_ts",
        name="Last Coordinate Update",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:clock-outline",
        value_fn=lambda data: _convert_timestamp(data.get("last_coord_ts")),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Mapit sensors from config entry."""
    coordinator: MapitDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    async_add_entities(
        MapitSensor(coordinator, config_entry, description)
        for description in SENSORS
    )


class MapitSensor(CoordinatorEntity, SensorEntity):
    """Representation of a Mapit sensor."""

    entity_description: MapitSensorEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: MapitDataUpdateCoordinator,
        config_entry: ConfigEntry,
        description: MapitSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_{description.key}"

        # Device info
        self._attr_device_info = {
            "identifiers": {(DOMAIN, config_entry.entry_id)},
            "name": "Motorcycle",
            "manufacturer": "Mapit",
            "model": "Vehicle Tracker",
        }

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        if self.coordinator.data:
            return self.entity_description.value_fn(self.coordinator.data)
        return None
