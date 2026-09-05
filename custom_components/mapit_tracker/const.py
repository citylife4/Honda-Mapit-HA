"""Constants for the Mapit Motorcycle Tracker integration."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "mapit_tracker"

PLATFORMS: list[Platform] = [
    Platform.DEVICE_TRACKER,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
]

CONF_EMAIL = "email"
CONF_ACCOUNT_ID = "account_id"
CONF_COGNITO_OVERRIDES = "cognito_overrides"

# Legacy config entry keys (version 1) kept for migration.
LEGACY_CONF_USERNAME = "username"
LEGACY_CONF_IDENTITY_POOL_ID = "identity_pool_id"
LEGACY_CONF_USER_POOL_ID = "user_pool_id"
LEGACY_CONF_USER_POOL_CLIENT_ID = "user_pool_client_id"

# The websocket carries live device state, so the REST poll only needs to catch
# what the socket misses (routes, vehicle detail, reconnect gaps).
DEFAULT_SCAN_INTERVAL = timedelta(minutes=10)
ROUTE_CACHE_INTERVAL = timedelta(hours=6)
AUTH_REFRESH_MARGIN = timedelta(minutes=5)

WEBSOCKET_HEARTBEAT = 60
WEBSOCKET_RECONNECT_DELAY = timedelta(seconds=10)
WEBSOCKET_MAX_RECONNECT_DELAY = timedelta(minutes=5)

STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}.tokens"

ATTR_ROUTE_ID = "route_id"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"

SERVICE_GET_ROUTE_DETAIL = "get_route_detail"
SERVICE_EXPORT_ROUTE_GPX = "export_route_gpx"

MAPIT_APP_URL = "https://app.mapit.me"

# Built-in fallbacks, used only when runtime discovery fails and the config
# entry carries no user-supplied overrides.
DEFAULT_COGNITO_REGION = "eu-west-1"
DEFAULT_COGNITO_USER_POOL_ID = "eu-west-1_nHd6Er8N6"
DEFAULT_COGNITO_APP_CLIENT_ID = "7fo1dt507lf6riggmprmql2mpb"
DEFAULT_COGNITO_IDENTITY_POOL_ID = "eu-west-1:a25d1457-542f-43d3-8b47-c3c60ed3675d"
DEFAULT_CORE_API_URL = "https://core.prod.mapit.me"
DEFAULT_GEO_API_URL = "https://geo.prod.mapit.me"
DEFAULT_DEVICESTATE_WS_URL = "wss://dsw.prod.mapit.me/devicestate"

# Status reported by the device when the vehicle is parked. The API keeps
# reporting a residual speed in this state, so it is normalised to zero.
STATUS_AT_REST = "AT_REST"
