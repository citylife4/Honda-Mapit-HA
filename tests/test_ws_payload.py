"""Unit tests for websocket payload decoding and merging."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import struct
import types

import aiohttp
import pytest

COMPONENT_ROOT = (
    Path(__file__).resolve().parents[1] / "custom_components" / "mapit_tracker"
)

spec = importlib.util.spec_from_file_location(
    "mapit_ws_payload", COMPONENT_ROOT / "ws_payload.py"
)
ws = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ws)


def make_message(data, msg_type=aiohttp.WSMsgType.TEXT):
    """Build a stand-in for an aiohttp websocket message."""
    return types.SimpleNamespace(type=msg_type, data=data)


def ewkb_point(lon: float, lat: float) -> str:
    """Build a little-endian EWKB point hex string with an SRID."""
    return (
        struct.pack("<BI", 1, 1 | 0x20000000)
        + struct.pack("<I", 4326)
        + struct.pack("<dd", lon, lat)
    ).hex()


def snapshot_with_state(state: dict) -> dict:
    return {
        "vehicles": [
            {"id": "veh-1", "device": {"id": "dev-1", "state": dict(state)}},
            {"id": "veh-2", "device": {"id": "dev-2", "state": {"status": "AT_REST"}}},
        ]
    }


def state_of(snapshot: dict, vehicle_index: int = 0) -> dict:
    return snapshot["vehicles"][vehicle_index]["device"]["state"]


class TestDecodeWsMessage:
    def test_decodes_text_frames(self):
        assert ws.decode_ws_message(make_message('{"a": 1}')) == {"a": 1}

    def test_decodes_binary_frames(self):
        message = make_message(b'{"a": 1}', aiohttp.WSMsgType.BINARY)
        assert ws.decode_ws_message(message) == {"a": 1}

    def test_returns_none_for_malformed_json(self):
        assert ws.decode_ws_message(make_message("not json")) is None

    def test_ignores_other_frame_types(self):
        assert ws.decode_ws_message(make_message("{}", aiohttp.WSMsgType.PING)) is None


class TestExtractDeviceState:
    def test_reads_a_bare_state(self):
        state = ws.extract_device_state({"deviceId": "dev-1", "status": "MOVING"})
        assert state == {"deviceId": "dev-1", "status": "MOVING"}

    @pytest.mark.parametrize(
        "envelope", ["state", "data", "payload", "deviceState", "message"]
    )
    def test_unwraps_known_envelopes(self, envelope):
        payload = {envelope: {"deviceId": "dev-1", "speed": 12}}
        assert ws.extract_device_state(payload)["speed"] == 12

    def test_backfills_device_id_from_id(self):
        assert ws.extract_device_state({"id": "dev-1"})["deviceId"] == "dev-1"

    @pytest.mark.parametrize("payload", [None, "text", 42, {"unrelated": True}])
    def test_rejects_non_state_payloads(self, payload):
        assert ws.extract_device_state(payload) is None


class TestMergeDeviceState:
    BASE = {
        "lat": 40.4168,
        "lng": -3.7038,
        "status": "AT_REST",
        "speed": 0,
        "battery": 87,
        "odometer": 12345,
    }

    def test_merges_into_the_matching_device_only(self):
        merged = ws.merge_device_state(
            snapshot_with_state(self.BASE), "dev-1", {"status": "MOVING", "speed": 64}
        )
        assert state_of(merged)["status"] == "MOVING"
        assert state_of(merged, 1)["status"] == "AT_REST"

    def test_keeps_fields_the_frame_does_not_carry(self):
        merged = ws.merge_device_state(
            snapshot_with_state(self.BASE), "dev-1", {"speed": 64}
        )
        assert state_of(merged)["odometer"] == 12345

    def test_ignores_explicit_nulls(self):
        merged = ws.merge_device_state(
            snapshot_with_state(self.BASE), "dev-1", {"battery": None, "speed": 64}
        )
        assert state_of(merged)["battery"] == 87

    def test_encoded_location_replaces_stale_lat_lng(self):
        # A frame reporting only `location` must not leave the previous
        # lat/lng in place, or the tracker would stay pinned to it forever.
        merged = ws.merge_device_state(
            snapshot_with_state(self.BASE),
            "dev-1",
            {"location": ewkb_point(-3.71, 40.42)},
        )
        state = state_of(merged)
        assert "lat" not in state and "lng" not in state
        assert state["location"] == ewkb_point(-3.71, 40.42)

    def test_lat_lng_replaces_stale_encoded_location(self):
        base = {"location": ewkb_point(-3.7038, 40.4168), "status": "AT_REST"}
        merged = ws.merge_device_state(
            snapshot_with_state(base), "dev-1", {"lat": 40.42, "lng": -3.71}
        )
        state = state_of(merged)
        assert "location" not in state
        assert (state["lat"], state["lng"]) == (40.42, -3.71)

    def test_non_positional_update_leaves_position_untouched(self):
        merged = ws.merge_device_state(
            snapshot_with_state(self.BASE), "dev-1", {"battery": 80}
        )
        state = state_of(merged)
        assert (state["lat"], state["lng"]) == (40.4168, -3.7038)

    def test_does_not_mutate_the_input_snapshot(self):
        original = snapshot_with_state(self.BASE)
        ws.merge_device_state(original, "dev-1", {"speed": 64})
        assert state_of(original)["speed"] == 0

    def test_unknown_device_is_a_no_op(self):
        original = snapshot_with_state(self.BASE)
        merged = ws.merge_device_state(original, "dev-unknown", {"speed": 64})
        assert merged == original

    def test_handles_empty_snapshot(self):
        assert ws.merge_device_state(None, "dev-1", {"speed": 64}) == {}


class TestSummarizeDeviceState:
    def test_reports_coordinate_presence_without_the_payload(self):
        summary = ws.summarize_device_state(
            {"deviceId": "dev-1", "lat": 1.0, "lng": 2.0, "raw": "x" * 5000}
        )
        assert summary["has_coords"] is True
        assert summary["id"] == "dev-1"
        assert "raw" in summary["keys"]
        assert len(json.dumps(summary)) < 500

    def test_flags_missing_coordinates(self):
        assert ws.summarize_device_state({"status": "MOVING"})["has_coords"] is False
