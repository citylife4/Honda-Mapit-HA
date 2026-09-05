"""Binary sensor platform for the Mapit Motorcycle Tracker integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, STATUS_AT_REST
from .coordinator import MapitDataUpdateCoordinator
from .entity import MapitEntityDescriptionMixin, MapitVehicleEntity


@dataclass(frozen=True, kw_only=True)
class MapitBinarySensorEntityDescription(
    BinarySensorEntityDescription, MapitEntityDescriptionMixin
):
    """Describes a Mapit binary sensor entity."""


BINARY_SENSORS: tuple[MapitBinarySensorEntityDescription, ...] = (
    MapitBinarySensorEntityDescription(
        key="moving",
        translation_key="moving",
        device_class=BinarySensorDeviceClass.MOVING,
        value_fn=lambda entity: (
            entity.device_state.get("status") not in {None, STATUS_AT_REST}
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Mapit binary sensors from a config entry."""
    coordinator: MapitDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id][
        "coordinator"
    ]
    async_add_entities(
        MapitBinarySensor(coordinator, vehicle["id"], description)
        for vehicle in (coordinator.data or {}).get("vehicles", [])
        if vehicle.get("id")
        for description in BINARY_SENSORS
    )


class MapitBinarySensor(MapitVehicleEntity, BinarySensorEntity):
    """Representation of a Mapit binary sensor."""

    entity_description: MapitBinarySensorEntityDescription

    def __init__(
        self,
        coordinator: MapitDataUpdateCoordinator,
        vehicle_id: str,
        description: MapitBinarySensorEntityDescription,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, vehicle_id)
        self.entity_description = description
        self._attr_unique_id = f"{vehicle_id}_{description.key}"

    @property
    def is_on(self) -> bool:
        """Return whether the vehicle is moving."""
        return bool(self.entity_description.value_fn(self))
