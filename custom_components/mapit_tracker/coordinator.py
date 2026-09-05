"""Data update coordinator for the Mapit Motorcycle Tracker integration.

The coordinator combines a slow REST poll with a per-device websocket feed:
the poll refreshes vehicle detail and routes, while the socket pushes live
position, speed and status as the vehicle moves.

The websocket handling is derived from https://github.com/d3vv3/hass-honda-mapit
(MIT, Copyright (c) 2026 db). See NOTICE for the retained notice.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import MapitApiClient, MapitAuthError, MapitConnectionError
from .const import (
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    WEBSOCKET_MAX_RECONNECT_DELAY,
    WEBSOCKET_RECONNECT_DELAY,
)
from .ws_payload import (
    decode_ws_message,
    extract_device_state,
    merge_device_state,
    summarize_device_state,
)

_LOGGER = logging.getLogger(__name__)


class MapitDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Manage Mapit data updates."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        client: MapitApiClient,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            logger=_LOGGER,
            name=f"{DOMAIN}_{config_entry.entry_id}",
            update_interval=DEFAULT_SCAN_INTERVAL,
            config_entry=config_entry,
        )
        self.client = client
        self._ws_tasks: dict[str, asyncio.Task[None]] = {}
        self._ws_stop_event = asyncio.Event()

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch the current snapshot from the API."""
        try:
            snapshot = await self.client.async_get_snapshot()
        except MapitAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except MapitConnectionError as err:
            raise UpdateFailed(f"Error communicating with API: {err}") from err

        await self._async_sync_ws_tasks(snapshot)
        return snapshot

    async def async_start_realtime(self) -> None:
        """Start websocket listeners for the known devices."""
        self._ws_stop_event.clear()
        if self.data:
            await self._async_sync_ws_tasks(self.data)

    async def async_stop_realtime(self) -> None:
        """Stop all websocket listeners."""
        self._ws_stop_event.set()
        tasks = list(self._ws_tasks.values())
        self._ws_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _async_sync_ws_tasks(self, snapshot: dict[str, Any]) -> None:
        """Start or stop listeners so they match the devices in the snapshot."""
        desired_devices = {
            device_id
            for vehicle in snapshot.get("vehicles", [])
            if (device_id := (vehicle.get("device") or {}).get("id"))
        }

        for device_id in list(self._ws_tasks):
            if device_id not in desired_devices:
                task = self._ws_tasks.pop(device_id)
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        for device_id in desired_devices:
            if device_id in self._ws_tasks or self._ws_stop_event.is_set():
                continue
            self._ws_tasks[device_id] = self.config_entry.async_create_background_task(
                self.hass,
                self._async_device_listener(device_id),
                name=f"{DOMAIN}_ws_{device_id}",
            )

    async def _async_device_listener(self, device_id: str) -> None:
        """Keep a websocket open for one device, reconnecting with backoff."""
        delay = WEBSOCKET_RECONNECT_DELAY.total_seconds()
        max_delay = WEBSOCKET_MAX_RECONNECT_DELAY.total_seconds()

        try:
            while not self._ws_stop_event.is_set():
                websocket: aiohttp.ClientWebSocketResponse | None = None
                try:
                    websocket = await self.client.async_ws_connect(device_id)
                    delay = WEBSOCKET_RECONNECT_DELAY.total_seconds()

                    async for message in websocket:
                        if self._ws_stop_event.is_set():
                            break

                        payload = decode_ws_message(message)
                        if payload is None:
                            continue

                        state = extract_device_state(payload)
                        if state is None:
                            _LOGGER.debug(
                                "Ignoring websocket payload for %s without state keys",
                                device_id,
                            )
                            continue

                        _LOGGER.debug(
                            "Mapit websocket update for %s: %s",
                            device_id,
                            summarize_device_state(state),
                        )
                        self.async_set_updated_data(
                            merge_device_state(self.data, device_id, state)
                        )
                except asyncio.CancelledError:
                    raise
                except MapitAuthError as err:
                    _LOGGER.warning(
                        "Mapit websocket auth failed for %s: %s", device_id, err
                    )
                    self.hass.async_create_task(self._async_recover_auth())
                    break
                except (MapitConnectionError, aiohttp.ClientError, TimeoutError) as err:
                    _LOGGER.debug("Mapit websocket for %s failed: %s", device_id, err)
                finally:
                    if websocket is not None and not websocket.closed:
                        await websocket.close()

                if self._ws_stop_event.is_set():
                    break

                await asyncio.sleep(delay)
                delay = min(delay * 2, max_delay)
        finally:
            if self._ws_tasks.get(device_id) is asyncio.current_task():
                self._ws_tasks.pop(device_id, None)

    async def _async_recover_auth(self) -> None:
        """Refresh after a websocket auth failure, escalating to reauth."""
        try:
            await self.async_request_refresh()
        except ConfigEntryAuthFailed:
            _LOGGER.warning("Automatic auth recovery failed; starting reauth")
            self.config_entry.async_start_reauth(self.hass)
