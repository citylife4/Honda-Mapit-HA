"""Sensor platform for the Mapit Motorcycle Tracker integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfLength,
    UnitOfSpeed,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import extract_gps_accuracy, extract_speed, ms_to_datetime
from .const import DOMAIN
from .coordinator import MapitDataUpdateCoordinator
from .entity import MapitEntityDescriptionMixin, MapitVehicleEntity


@dataclass(frozen=True, kw_only=True)
class MapitSensorEntityDescription(
    SensorEntityDescription, MapitEntityDescriptionMixin
):
    """Describes a Mapit sensor entity."""


SENSORS: tuple[MapitSensorEntityDescription, ...] = (
    MapitSensorEntityDescription(
        key="speed",
        translation_key="speed",
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        device_class=SensorDeviceClass.SPEED,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:speedometer",
        value_fn=lambda entity: extract_speed(entity.device_state),
    ),
    MapitSensorEntityDescription(
        key="status",
        translation_key="status",
        icon="mdi:motorbike",
        value_fn=lambda entity: entity.device_state.get("status"),
    ),
    MapitSensorEntityDescription(
        key="battery",
        translation_key="battery",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda entity: entity.device_state.get("battery"),
    ),
    MapitSensorEntityDescription(
        key="gps_accuracy",
        translation_key="gps_accuracy",
        icon="mdi:crosshairs-gps",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda entity: extract_gps_accuracy(entity.device_state),
    ),
    MapitSensorEntityDescription(
        key="hdop",
        translation_key="hdop",
        icon="mdi:map-marker-radius",
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda entity: extract_gps_accuracy(entity.device_state),
    ),
    MapitSensorEntityDescription(
        key="odometer",
        translation_key="odometer",
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:counter",
        value_fn=lambda entity: entity.device_state.get("odometer"),
    ),
    MapitSensorEntityDescription(
        key="last_coord_ts",
        translation_key="last_coord_ts",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:clock-outline",
        value_fn=lambda entity: ms_to_datetime(
            entity.device_state.get("lastCoordTs")
        ),
    ),
    MapitSensorEntityDescription(
        key="last_seen",
        translation_key="last_seen",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:clock-check-outline",
        value_fn=lambda entity: ms_to_datetime(entity.device_state.get("lastTs")),
    ),
    MapitSensorEntityDescription(
        key="route_count",
        translation_key="route_count",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:map-marker-path",
        entity_registry_enabled_default=False,
        value_fn=lambda entity: len(entity.routes),
    ),
    MapitSensorEntityDescription(
        key="route_days",
        translation_key="route_days",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:calendar-range",
        entity_registry_enabled_default=False,
        value_fn=lambda entity: entity.route_days(),
    ),
    MapitSensorEntityDescription(
        key="last_route_started",
        translation_key="last_route_started",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:map-marker-right",
        entity_registry_enabled_default=False,
        value_fn=lambda entity: entity.route_started(entity.latest_route),
    ),
    MapitSensorEntityDescription(
        key="last_route_distance",
        translation_key="last_route_distance",
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:map-marker-distance",
        entity_registry_enabled_default=False,
        value_fn=lambda entity: entity.route_distance_km(entity.latest_route),
        attr_fn=lambda entity: {
            "avg_speed_kmh": (entity.latest_route or {}).get("avgSpeed"),
            "max_speed_kmh": (entity.latest_route or {}).get("maxSpeed"),
            "route_id": (entity.latest_route or {}).get("id"),
        },
    ),
    MapitSensorEntityDescription(
        key="last_route_duration",
        translation_key="last_route_duration",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:timer-outline",
        entity_registry_enabled_default=False,
        value_fn=lambda entity: entity.route_duration_minutes(entity.latest_route),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Mapit sensors from a config entry."""
    coordinator: MapitDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id][
        "coordinator"
    ]
    async_add_entities(
        MapitSensor(coordinator, vehicle["id"], description)
        for vehicle in (coordinator.data or {}).get("vehicles", [])
        if vehicle.get("id")
        for description in SENSORS
    )


class MapitSensor(MapitVehicleEntity, SensorEntity):
    """Representation of a Mapit sensor."""

    entity_description: MapitSensorEntityDescription

    def __init__(
        self,
        coordinator: MapitDataUpdateCoordinator,
        vehicle_id: str,
        description: MapitSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, vehicle_id)
        self.entity_description = description
        self._attr_unique_id = f"{vehicle_id}_{description.key}"

    @property
    def native_value(self) -> Any:
        """Return the state of the sensor."""
        return self.entity_description.value_fn(self)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the sensor specific state attributes."""
        if self.entity_description.attr_fn is None:
            return None
        attributes = self.entity_description.attr_fn(self) or {}
        return {key: value for key, value in attributes.items() if value is not None}
