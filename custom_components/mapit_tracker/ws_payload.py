"""Pure helpers for decoding and merging Mapit device-state websocket frames.

Kept free of Home Assistant imports so the payload handling can be unit
tested without a Home Assistant install.

Derived from https://github.com/d3vv3/hass-honda-mapit (MIT, Copyright (c)
2026 db). See NOTICE for the retained notice.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import json
from typing import Any

import aiohttp

# Keys that identify a payload as a device state rather than an envelope.
DEVICE_STATE_KEYS = frozenset(
    {
        "id",
        "battery",
        "status",
        "speed",
        "lastTs",
        "lastCoordTs",
        "location",
        "lat",
        "lng",
        "deviceId",
    }
)

# Keys that carry the vehicle's position. A frame may report the position as
# a lat/lng pair or as an encoded point, so whichever arrives has to replace
# the whole group -- keeping the other half would pin the tracker to a stale
# location that the newer frame never overwrites.
POSITION_KEYS = frozenset({"lat", "lng", "location"})

# Envelope keys the backend has been seen to nest the device state under.
_ENVELOPE_KEYS = ("state", "data", "payload", "deviceState", "message")


def decode_ws_message(message: aiohttp.WSMessage) -> Any | None:
    """Decode a websocket message into a Python payload."""
    if message.type == aiohttp.WSMsgType.TEXT:
        try:
            return json.loads(message.data)
        except (TypeError, json.JSONDecodeError):
            return None

    if message.type == aiohttp.WSMsgType.BINARY:
        try:
            return json.loads(message.data.decode())
        except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
            return None

    return None


def extract_device_state(payload: Any) -> dict[str, Any] | None:
    """Extract a device-state mapping from a websocket payload."""
    if not isinstance(payload, Mapping):
        return None

    if DEVICE_STATE_KEYS.intersection(payload.keys()):
        state = dict(payload)
        if "deviceId" not in state and "id" in state:
            state["deviceId"] = state["id"]
        return state

    for key in _ENVELOPE_KEYS:
        nested = payload.get(key)
        if isinstance(nested, Mapping):
            state = extract_device_state(nested)
            if state is not None:
                return state

    return None


def merge_device_state(
    current: dict[str, Any] | None, device_id: str, state: Mapping[str, Any]
) -> dict[str, Any]:
    """Merge a websocket state into the current coordinator snapshot."""
    snapshot = deepcopy(current or {})

    for vehicle in snapshot.get("vehicles", []):
        device = vehicle.get("device") or {}
        if device.get("id") != device_id:
            continue

        merged_state = dict(device.get("state") or {})
        update = {key: value for key, value in state.items() if value is not None}

        if POSITION_KEYS.intersection(update):
            for key in POSITION_KEYS:
                merged_state.pop(key, None)

        merged_state.update(update)
        merged_state.setdefault("deviceId", device_id)
        device["state"] = merged_state
        vehicle["device"] = device
        return snapshot

    return snapshot


def summarize_device_state(state: Mapping[str, Any]) -> dict[str, Any]:
    """Build a debug summary that leaves out the bulky raw payload."""
    return {
        "id": state.get("id") or state.get("deviceId"),
        "status": state.get("status"),
        "speed": state.get("speed"),
        "battery": state.get("battery"),
        "lastCoordTs": state.get("lastCoordTs"),
        "has_coords": state.get("lat") is not None and state.get("lng") is not None,
        "keys": sorted(state.keys()),
    }
