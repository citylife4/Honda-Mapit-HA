"""Unit tests for the pure helpers in the Mapit API client.

These deliberately avoid importing Home Assistant so they run on a bare
Python install in CI.
"""

from __future__ import annotations

from datetime import UTC, datetime
import importlib.util
from pathlib import Path
import struct
import sys
import types

import pytest

COMPONENT_ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "mapit_tracker"


def _load_module(name: str):
    """Load a component module without importing the package (needs HA)."""
    package = "mapit_tracker_test_pkg"
    if package not in sys.modules:
        pkg = types.ModuleType(package)
        pkg.__path__ = [str(COMPONENT_ROOT)]
        sys.modules[package] = pkg

    spec = importlib.util.spec_from_file_location(
        f"{package}.{name}", COMPONENT_ROOT / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"{package}.{name}"] = module
    spec.loader.exec_module(module)
    return module


# const.py imports homeassistant.const; stub the single symbol it needs.
if "homeassistant" not in sys.modules:
    ha = types.ModuleType("homeassistant")
    ha_const = types.ModuleType("homeassistant.const")

    class _Platform(str):
        SENSOR = "sensor"
        BINARY_SENSOR = "binary_sensor"
        DEVICE_TRACKER = "device_tracker"

    ha_const.Platform = _Platform
    sys.modules["homeassistant"] = ha
    sys.modules["homeassistant.const"] = ha_const

_load_module("const")
api = _load_module("api")


def _ewkb_point(lon: float, lat: float, srid: int | None = None) -> str:
    """Build a little-endian EWKB point hex string."""
    geom_type = 1 | (0x20000000 if srid is not None else 0)
    payload = struct.pack("<BI", 1, geom_type)
    if srid is not None:
        payload += struct.pack("<I", srid)
    payload += struct.pack("<dd", lon, lat)
    return payload.hex()


class TestParseMapitPoint:
    def test_parses_point_without_srid(self):
        assert api.parse_mapit_point(_ewkb_point(-3.7038, 40.4168)) == pytest.approx(
            (40.4168, -3.7038)
        )

    def test_parses_point_with_srid(self):
        assert api.parse_mapit_point(
            _ewkb_point(-3.7038, 40.4168, srid=4326)
        ) == pytest.approx((40.4168, -3.7038))

    @pytest.mark.parametrize("value", [None, "", "zzz", "0101"])
    def test_returns_none_for_unusable_input(self, value):
        assert api.parse_mapit_point(value) is None


class TestExtractDeviceCoordinates:
    def test_prefers_explicit_lat_lng(self):
        state = {"lat": 40.4, "lng": -3.7, "location": _ewkb_point(1.0, 2.0)}
        assert api.extract_device_coordinates(state) == (40.4, -3.7)

    def test_falls_back_to_encoded_location(self):
        state = {"location": _ewkb_point(-3.7038, 40.4168)}
        assert api.extract_device_coordinates(state) == pytest.approx(
            (40.4168, -3.7038)
        )

    def test_handles_missing_state(self):
        assert api.extract_device_coordinates(None) is None
        assert api.extract_device_coordinates({}) is None


class TestExtractSpeed:
    def test_zeroes_speed_while_parked(self):
        # The API keeps reporting residual speed after the vehicle stops.
        assert api.extract_speed({"status": "AT_REST", "speed": 3}) == 0

    def test_returns_reported_speed_while_moving(self):
        assert api.extract_speed({"status": "MOVING", "speed": 42.5}) == 42.5

    def test_coerces_numeric_strings(self):
        assert api.extract_speed({"status": "MOVING", "speed": "42"}) == 42

    def test_returns_none_without_speed(self):
        assert api.extract_speed({"status": "MOVING"}) is None
        assert api.extract_speed(None) is None


class TestExtractGpsAccuracy:
    def test_reads_direct_hdop(self):
        assert api.extract_gps_accuracy({"hdop": "1.4"}) == pytest.approx(1.4)

    def test_reads_alternate_key(self):
        assert api.extract_gps_accuracy({"horizontalAccuracy": 7}) == 7

    def test_reads_nested_payload(self):
        assert api.extract_gps_accuracy({"telemetry": {"gpsAccuracy": 5}}) == 5

    def test_reads_serialized_data_field(self):
        assert api.extract_gps_accuracy({"data": '{"hAcc": 3}'}) == 3

    def test_returns_none_when_absent(self):
        assert api.extract_gps_accuracy({"status": "MOVING"}) is None
        assert api.extract_gps_accuracy({"data": "not json"}) is None


class TestRuntimeConfigDiscovery:
    AMPLIFY_BUNDLE = (
        'x={userPoolId:"eu-west-1_ABC123",userPoolClientId:"clientid123",'
        'identityPoolId:"eu-west-1:1111-2222"};'
        'sendRequest({endpoint:"https://core.prod.mapit.me"});'
        'sendRequest({endpoint:"https://geo.prod.mapit.me"});'
    )
    VITE_BUNDLE = (
        'VITE_COGNITO_USER_POOL_ID:"eu-west-1_ABC123",'
        'VITE_COGNITO_CLIENT_ID:"clientid123",'
        'VITE_COGNITO_IDENTITY_POOL_ID:"eu-west-1:1111-2222",'
        'VITE_MAPIT_CORE_API:"https://core.prod.mapit.me",'
        'VITE_MAPIT_GEO_API:"https://geo.prod.mapit.me",'
    )

    @pytest.mark.parametrize("bundle", [AMPLIFY_BUNDLE, VITE_BUNDLE])
    def test_extracts_both_bundle_layouts(self, bundle):
        config = api.extract_runtime_config(bundle)
        assert config.user_pool_id == "eu-west-1_ABC123"
        assert config.app_client_id == "clientid123"
        assert config.identity_pool_id == "eu-west-1:1111-2222"
        assert config.core_api_url == "https://core.prod.mapit.me"
        assert config.source == "discovered"

    def test_derives_region_from_pool_id(self):
        assert api.extract_runtime_config(self.AMPLIFY_BUNDLE).region == "eu-west-1"

    def test_prefers_explicit_region(self):
        bundle = self.AMPLIFY_BUNDLE + 'region:"us-east-2"'
        assert api.extract_runtime_config(bundle).region == "us-east-2"

    def test_raises_when_fields_missing(self):
        with pytest.raises(api.MapitConnectionError):
            api.extract_runtime_config('{"unrelated": true}')

    def test_derives_websocket_url(self):
        config = api.extract_runtime_config(self.AMPLIFY_BUNDLE)
        assert config.devicestate_ws_url == "wss://dsw.prod.mapit.me/devicestate"


class TestBundleUrlExtraction:
    def test_orders_entry_bundle_first(self):
        html = (
            '<script src="/assets/lazy-Xyz.js"></script>'
            '<script src="/assets/index-Abc.js"></script>'
            '<script src="/assets/main-Def.js"></script>'
        )
        assert api.extract_bundle_urls(html) == [
            "https://app.mapit.me/assets/main-Def.js",
            "https://app.mapit.me/assets/index-Abc.js",
            "https://app.mapit.me/assets/lazy-Xyz.js",
        ]

    def test_deduplicates(self):
        html = '<script src="/assets/main-A.js"></script>' * 3
        assert api.extract_bundle_urls(html) == ["https://app.mapit.me/assets/main-A.js"]

    def test_returns_empty_without_bundles(self):
        assert api.extract_bundle_urls("<html></html>") == []


class TestOverrides:
    def test_overrides_win_over_builtin_defaults(self):
        config = api.default_runtime_config(
            {
                "user_pool_id": "us-east-1_LEGACY",
                "app_client_id": "legacyclient",
                "identity_pool_id": "us-east-1:legacy",
            }
        )
        assert config.user_pool_id == "us-east-1_LEGACY"
        assert config.region == "us-east-1"
        assert config.source == "overrides"

    def test_falls_back_when_no_overrides(self):
        config = api.default_runtime_config(None)
        assert config.region == "eu-west-1"
        assert config.source == "fallback"


class TestSigningPrimitives:
    def test_signature_key_matches_aws_reference(self):
        # Vector from the AWS SigV4 documentation.
        key = api.get_signature_key(
            "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
            "20150830",
            "us-east-1",
            "iam",
        )
        assert key.hex() == (
            "c4afb1cc5771d871763a393e44b703571b55cc28424d1a5e86da6ed3c154a4b9"
        )

    def test_canonical_query_is_sorted_and_encoded(self):
        assert api.canonical_query({"b": "2", "a": "x y"}) == "a=x%20y&b=2"

    def test_canonical_query_handles_empty(self):
        assert api.canonical_query({}) == ""

    def test_decode_jwt_exp(self):
        import base64
        import json

        payload = base64.urlsafe_b64encode(json.dumps({"exp": 1700000000}).encode())
        token = f"header.{payload.decode().rstrip('=')}.signature"
        assert api.decode_jwt_exp(token) == datetime.fromtimestamp(1700000000, tz=UTC)

    def test_decode_jwt_exp_rejects_garbage(self):
        assert api.decode_jwt_exp("not-a-jwt") is None


class TestTimestamps:
    def test_ms_to_datetime(self):
        assert api.ms_to_datetime(1700000000000) == datetime.fromtimestamp(
            1700000000, tz=UTC
        )

    @pytest.mark.parametrize("value", [None, "abc"])
    def test_ms_to_datetime_rejects_bad_input(self, value):
        assert api.ms_to_datetime(value) is None

    def test_parse_iso_datetime_handles_z_suffix(self):
        parsed = api.parse_iso_datetime("2026-05-13T21:46:18Z")
        assert parsed == datetime(2026, 5, 13, 21, 46, 18, tzinfo=UTC)

    @pytest.mark.parametrize("value", [None, "", "nope"])
    def test_parse_iso_datetime_rejects_bad_input(self, value):
        assert api.parse_iso_datetime(value) is None

    def test_parse_aws_timestamp_accepts_epoch_seconds(self):
        assert api.parse_aws_timestamp(1700000000) == datetime.fromtimestamp(
            1700000000, tz=UTC
        )


class TestGpxExport:
    ROUTE = {
        "id": "rt-1",
        "startedAt": "2026-05-13T10:00:00Z",
        "endedAt": "2026-05-13T11:00:00Z",
        "geoJSON": {
            "features": [
                {"geometry": {"type": "Point", "coordinates": [1.0, 2.0]}},
                {
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[-3.7, 40.4, 650], [-3.8, 40.5]],
                    }
                },
            ]
        },
    }

    def test_builds_track_points_in_order(self):
        gpx = api.build_gpx(self.ROUTE)
        assert '<trkpt lat="40.4" lon="-3.7">' in gpx
        assert "<ele>650</ele>" in gpx
        assert gpx.index('lat="40.4"') < gpx.index('lat="40.5"')

    def test_skips_non_linestring_features(self):
        assert api.build_gpx(self.ROUTE).count("<trkpt") == 2

    def test_handles_route_without_geometry(self):
        gpx = api.build_gpx({"id": "rt-empty"})
        assert "<trkseg>" in gpx and "<trkpt" not in gpx

    def test_escapes_xml(self):
        gpx = api.build_gpx({"id": "a&b<c>"})
        assert "a&amp;b&lt;c&gt;" in gpx
        assert "a&b<c>" not in gpx


class TestDeriveWsUrl:
    def test_swaps_core_host_for_devicestate_host(self):
        assert (
            api.derive_ws_url("https://core.staging.mapit.me")
            == "wss://dsw.staging.mapit.me/devicestate"
        )

    def test_leaves_unrecognised_host_alone(self):
        assert (
            api.derive_ws_url("https://api.example.com")
            == "wss://api.example.com/devicestate"
        )
