"""Device tracker platform for the Mapit Motorcycle Tracker integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import extract_device_coordinates, extract_gps_accuracy, extract_speed
from .const import DOMAIN
from .coordinator import MapitDataUpdateCoordinator
from .entity import MapitVehicleEntity


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Mapit device trackers from a config entry."""
    coordinator: MapitDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id][
        "coordinator"
    ]
    async_add_entities(
        MapitDeviceTracker(coordinator, vehicle["id"])
        for vehicle in (coordinator.data or {}).get("vehicles", [])
        if vehicle.get("id")
    )


class MapitDeviceTracker(MapitVehicleEntity, TrackerEntity):
    """Representation of a Mapit vehicle tracker."""

    _attr_name = None
    _attr_icon = "mdi:motorcycle"

    def __init__(
        self, coordinator: MapitDataUpdateCoordinator, vehicle_id: str
    ) -> None:
        """Initialize the tracker."""
        super().__init__(coordinator, vehicle_id)
        self._attr_unique_id = f"{vehicle_id}_tracker"

    @property
    def source_type(self) -> SourceType:
        """Return the source type of the device."""
        return SourceType.GPS

    @property
    def latitude(self) -> float | None:
        """Return the latitude of the device."""
        point = extract_device_coordinates(self.device_state)
        return point[0] if point else None

    @property
    def longitude(self) -> float | None:
        """Return the longitude of the device."""
        point = extract_device_coordinates(self.device_state)
        return point[1] if point else None

    @property
    def location_accuracy(self) -> int:
        """Return the GPS accuracy in metres."""
        accuracy = extract_gps_accuracy(self.device_state)
        try:
            return int(float(accuracy))
        except (TypeError, ValueError):
            return 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the tracker specific state attributes."""
        state = self.device_state
        attributes: dict[str, Any] = {
            "speed": extract_speed(state),
            "status": state.get("status"),
            "device_id": (self.vehicle_summary.get("device") or {}).get("id"),
            "odometer": state.get("odometer"),
            "last_coord_ts": state.get("lastCoordTs"),
            "battery": state.get("battery"),
        }

        accuracy = extract_gps_accuracy(state)
        if accuracy is not None:
            # Kept for backwards compatibility with automations written against
            # the pre-2.0 attribute names.
            attributes["gps_accuracy"] = accuracy
            attributes["hdop"] = accuracy

        return {key: value for key, value in attributes.items() if value is not None}
