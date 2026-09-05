"""The Mapit Motorcycle Tracker integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import UpdateFailed

from .api import MapitApiClient
from .const import (
    ATTR_CONFIG_ENTRY_ID,
    ATTR_ROUTE_ID,
    CONF_COGNITO_OVERRIDES,
    CONF_EMAIL,
    DOMAIN,
    LEGACY_CONF_IDENTITY_POOL_ID,
    LEGACY_CONF_USER_POOL_CLIENT_ID,
    LEGACY_CONF_USER_POOL_ID,
    LEGACY_CONF_USERNAME,
    PLATFORMS,
    SERVICE_EXPORT_ROUTE_GPX,
    SERVICE_GET_ROUTE_DETAIL,
    STORAGE_KEY,
    STORAGE_VERSION,
)
from .coordinator import MapitDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

ROUTE_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ROUTE_ID): str,
        vol.Optional(ATTR_CONFIG_ENTRY_ID): str,
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the Mapit Motorcycle Tracker from a config entry."""
    store: Store[dict[str, Any]] = Store(
        hass, STORAGE_VERSION, f"{STORAGE_KEY}.{entry.entry_id}"
    )

    async def _async_save_tokens(tokens: dict[str, Any]) -> None:
        await store.async_save(tokens)

    client = MapitApiClient(
        async_get_clientsession(hass),
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
        overrides=entry.data.get(CONF_COGNITO_OVERRIDES),
        save_tokens=_async_save_tokens,
    )
    # Resuming from a stored refresh token avoids a full password login on
    # every Home Assistant restart.
    client.restore_tokens(await store.async_load())

    coordinator = MapitDataUpdateCoordinator(hass, entry, client)

    try:
        await coordinator.async_config_entry_first_refresh()
    except UpdateFailed as err:
        raise ConfigEntryNotReady(str(err)) from err

    await _async_migrate_unique_ids(hass, entry, coordinator)
    await coordinator.async_start_realtime()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "client": client,
        "coordinator": coordinator,
        "store": store,
    }

    _async_register_services(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if runtime is not None:
        await runtime["coordinator"].async_stop_realtime()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        entries = hass.data.get(DOMAIN, {})
        entries.pop(entry.entry_id, None)
        if not entries:
            hass.services.async_remove(DOMAIN, SERVICE_GET_ROUTE_DETAIL)
            hass.services.async_remove(DOMAIN, SERVICE_EXPORT_ROUTE_GPX)

    return unload_ok


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate an old config entry to the current schema.

    Version 1 entries asked the user for the Cognito pool identifiers. Those
    are now discovered from the Mapit frontend at runtime, so they are demoted
    to fallback overrides used only when discovery fails.
    """
    if entry.version >= 2:
        return True

    _LOGGER.debug("Migrating config entry %s from version 1", entry.entry_id)

    data = dict(entry.data)
    email = data.pop(LEGACY_CONF_USERNAME, None) or data.get(CONF_EMAIL)
    if not email:
        _LOGGER.error("Cannot migrate config entry without a username")
        return False

    overrides = {
        key: value
        for key, value in (
            ("identity_pool_id", data.pop(LEGACY_CONF_IDENTITY_POOL_ID, None)),
            ("user_pool_id", data.pop(LEGACY_CONF_USER_POOL_ID, None)),
            ("app_client_id", data.pop(LEGACY_CONF_USER_POOL_CLIENT_ID, None)),
        )
        if value
    }

    data[CONF_EMAIL] = email
    if overrides:
        data[CONF_COGNITO_OVERRIDES] = overrides

    hass.config_entries.async_update_entry(
        entry, data=data, unique_id=email.lower(), version=2
    )
    return True


async def _async_migrate_unique_ids(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: MapitDataUpdateCoordinator
) -> None:
    """Re-key pre-2.0 entities and devices from the entry id to the vehicle id.

    Before multi-vehicle support every entity was keyed on the config entry id,
    which meant one vehicle per entry. Re-keying preserves history and
    customisations for the account's first vehicle.
    """
    vehicles = (coordinator.data or {}).get("vehicles", [])
    if not vehicles:
        return

    vehicle_id = vehicles[0].get("id")
    if not vehicle_id:
        return

    old_prefix = f"{entry.entry_id}_"
    registry = er.async_get(hass)

    for registry_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if not registry_entry.unique_id.startswith(old_prefix):
            continue

        new_unique_id = f"{vehicle_id}_{registry_entry.unique_id[len(old_prefix):]}"
        if registry.async_get_entity_id(
            registry_entry.domain, DOMAIN, new_unique_id
        ):
            _LOGGER.debug(
                "Skipping unique id migration for %s: %s already exists",
                registry_entry.entity_id,
                new_unique_id,
            )
            continue

        _LOGGER.debug(
            "Migrating %s unique id %s -> %s",
            registry_entry.entity_id,
            registry_entry.unique_id,
            new_unique_id,
        )
        registry.async_update_entity(
            registry_entry.entity_id, new_unique_id=new_unique_id
        )

    device_registry = dr.async_get(hass)
    device = device_registry.async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    if device is not None and not device_registry.async_get_device(
        identifiers={(DOMAIN, vehicle_id)}
    ):
        device_registry.async_update_device(
            device.id,
            new_identifiers={(DOMAIN, vehicle_id)},
        )


def _async_register_services(hass: HomeAssistant) -> None:
    """Register the integration level services once."""
    if hass.services.has_service(DOMAIN, SERVICE_GET_ROUTE_DETAIL):
        return

    async def handle_get_route_detail(call: ServiceCall) -> dict[str, Any]:
        runtime = _select_runtime(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        return await runtime["client"].async_get_route_detail(call.data[ATTR_ROUTE_ID])

    async def handle_export_route_gpx(call: ServiceCall) -> dict[str, Any]:
        runtime = _select_runtime(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        return await runtime["client"].async_export_route_gpx(call.data[ATTR_ROUTE_ID])

    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_ROUTE_DETAIL,
        handle_get_route_detail,
        schema=ROUTE_SERVICE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_EXPORT_ROUTE_GPX,
        handle_export_route_gpx,
        schema=ROUTE_SERVICE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )


def _select_runtime(hass: HomeAssistant, config_entry_id: str | None) -> dict[str, Any]:
    """Return the runtime data for a service call."""
    entries: dict[str, dict[str, Any]] = hass.data.get(DOMAIN, {})

    if config_entry_id:
        if config_entry_id not in entries:
            raise HomeAssistantError(
                f"Unknown Mapit Tracker config entry: {config_entry_id}"
            )
        return entries[config_entry_id]

    if not entries:
        raise HomeAssistantError("Mapit Tracker is not configured")

    return next(iter(entries.values()))
