"""Shared entity base for the Mapit Motorcycle Tracker integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import parse_iso_datetime
from .const import DOMAIN
from .coordinator import MapitDataUpdateCoordinator


@dataclass(frozen=True, kw_only=True)
class MapitEntityDescriptionMixin:
    """Mixin adding value/attribute resolvers to an entity description."""

    value_fn: Callable[["MapitVehicleEntity"], Any]
    attr_fn: Callable[["MapitVehicleEntity"], dict[str, Any] | None] | None = None


class MapitVehicleEntity(CoordinatorEntity[MapitDataUpdateCoordinator]):
    """Base entity bound to a single Mapit vehicle."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: MapitDataUpdateCoordinator, vehicle_id: str
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self.vehicle_id = vehicle_id

    @property
    def vehicle_summary(self) -> dict[str, Any]:
        """Return the vehicle entry from the account summary."""
        for vehicle in (self.coordinator.data or {}).get("vehicles", []):
            if vehicle.get("id") == self.vehicle_id:
                return vehicle
        return {}

    @property
    def vehicle_detail(self) -> dict[str, Any]:
        """Return the detailed vehicle payload."""
        return (self.coordinator.data or {}).get("vehicle_details", {}).get(
            self.vehicle_id, {}
        )

    @property
    def device_state(self) -> dict[str, Any]:
        """Return the latest device state for this vehicle."""
        return (self.vehicle_summary.get("device") or {}).get("state") or {}

    @property
    def routes(self) -> list[dict[str, Any]]:
        """Return the cached routes for this vehicle, newest first."""
        return (self.coordinator.data or {}).get("routes", {}).get(self.vehicle_id, [])

    @property
    def latest_route(self) -> dict[str, Any] | None:
        """Return the most recent route, if any."""
        return self.routes[0] if self.routes else None

    @property
    def available(self) -> bool:
        """Return whether the vehicle is still present in the account."""
        return super().available and bool(self.vehicle_summary)

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device registry entry for this vehicle."""
        vehicle = self.vehicle_summary
        detail = self.vehicle_detail
        return DeviceInfo(
            identifiers={(DOMAIN, self.vehicle_id)},
            manufacturer=vehicle.get("product") or "Mapit",
            model=detail.get("model") or vehicle.get("model") or "Vehicle Tracker",
            name=vehicle.get("name") or detail.get("model") or "Motorcycle",
            serial_number=detail.get("vin"),
        )

    def route_days(self) -> int:
        """Return the number of distinct days with a recorded route."""
        return len(
            {
                route["startedAt"][:10]
                for route in self.routes
                if route.get("startedAt")
            }
        )

    @staticmethod
    def route_duration_minutes(route: dict[str, Any] | None) -> float | None:
        """Return a route's duration in minutes."""
        if not route:
            return None
        started = parse_iso_datetime(route.get("startedAt"))
        ended = parse_iso_datetime(route.get("endedAt"))
        if started is None or ended is None:
            return None
        return round((ended - started).total_seconds() / 60, 1)

    @staticmethod
    def route_distance_km(route: dict[str, Any] | None) -> float | None:
        """Return a route's distance in kilometres."""
        if not route:
            return None
        distance = route.get("distance")
        if distance is None:
            return None
        try:
            return round(float(distance) / 1000, 2)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def route_started(route: dict[str, Any] | None) -> datetime | None:
        """Return a route's start time."""
        if not route:
            return None
        return parse_iso_datetime(route.get("startedAt"))
