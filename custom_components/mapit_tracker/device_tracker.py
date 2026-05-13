"""Support for Mapit Motorcycle device tracker."""
from __future__ import annotations

import logging

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN, MapitDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Mapit device tracker from config entry."""
    coordinator: MapitDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    async_add_entities(
        [MapitDeviceTracker(coordinator, config_entry)],
        True,
    )


class MapitDeviceTracker(CoordinatorEntity, TrackerEntity):
    """Representation of a Mapit motorcycle tracker."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_icon = "mdi:motorcycle"

    def __init__(
        self,
        coordinator: MapitDataUpdateCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the tracker."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_tracker"

        # Device info
        self._attr_device_info = {
            "identifiers": {(DOMAIN, config_entry.entry_id)},
            "name": "Motorcycle",
            "manufacturer": "Mapit",
            "model": "Vehicle Tracker",
        }

    @property
    def latitude(self) -> float | None:
        """Return latitude value of the device."""
        if self.coordinator.data:
            return self.coordinator.data.get("latitude")
        return None

    @property
    def longitude(self) -> float | None:
        """Return longitude value of the device."""
        if self.coordinator.data:
            return self.coordinator.data.get("longitude")
        return None

    @property
    def source_type(self) -> SourceType:
        """Return the source type, eg gps or router, of the device."""
        return SourceType.GPS

    @property
    def extra_state_attributes(self):
        """Return entity specific state attributes."""
        if not self.coordinator.data:
            return {}

        attrs = {
            "speed": self.coordinator.data.get("speed"),
            "status": self.coordinator.data.get("status"),
        }

        gps_accuracy = self.coordinator.data.get("gps_accuracy")
        hdop = self.coordinator.data.get("hdop")
        if hdop is None:
            hdop = gps_accuracy

        # Add optional fields if available
        if gps_accuracy is not None:
            attrs["gps_accuracy"] = gps_accuracy
        if hdop is not None:
            attrs["hdop"] = hdop
        if self.coordinator.data.get("odometer") is not None:
            attrs["odometer"] = self.coordinator.data.get("odometer")
        if self.coordinator.data.get("last_coord_ts") is not None:
            attrs["last_coord_ts"] = self.coordinator.data.get("last_coord_ts")

        return attrs
