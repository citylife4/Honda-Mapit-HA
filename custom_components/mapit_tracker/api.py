"""Asynchronous API client for the Mapit vehicle tracking backend.

The Mapit backend is an AWS API Gateway fronted by Cognito: a user pool login
yields an ID token, an identity pool exchanges that for temporary AWS
credentials, and every REST call is signed with AWS Signature V4.

Portions of the Cognito/SigV4 flow, the runtime config discovery and the GPX
export are derived from https://github.com/d3vv3/hass-honda-mapit
(MIT, Copyright (c) 2026 db). See LICENSE for the retained notice.
"""

from __future__ import annotations

import base64
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import html
import json
import logging
import re
import struct
from typing import Any
from urllib.parse import quote, urljoin, urlsplit

import aiohttp

from .const import (
    AUTH_REFRESH_MARGIN,
    DEFAULT_COGNITO_APP_CLIENT_ID,
    DEFAULT_COGNITO_IDENTITY_POOL_ID,
    DEFAULT_COGNITO_REGION,
    DEFAULT_COGNITO_USER_POOL_ID,
    DEFAULT_CORE_API_URL,
    DEFAULT_DEVICESTATE_WS_URL,
    DEFAULT_GEO_API_URL,
    MAPIT_APP_URL,
    ROUTE_CACHE_INTERVAL,
    STATUS_AT_REST,
    WEBSOCKET_HEARTBEAT,
)

_LOGGER = logging.getLogger(__name__)

# Any hashed JS chunk emitted by the frontend build (e.g. ``main-CeqmBK3A.js``).
_BUNDLE_PATH_RE = re.compile(r"/assets/[A-Za-z0-9._-]+\.js")
# Both the legacy ``VITE_*`` build-time constants and the current Amplify Gen 2
# config object are matched, so discovery survives either frontend layout.
_DISCOVERY_PATTERNS = {
    "identity_pool_id": re.compile(
        r'(?:VITE_COGNITO_IDENTITY_POOL_ID|identityPoolId)\s*:\s*"(?P<value>[^"]+)"'
    ),
    "user_pool_id": re.compile(
        r'(?:VITE_COGNITO_USER_POOL_ID|userPoolId)\s*:\s*"(?P<value>[^"]+)"'
    ),
    "app_client_id": re.compile(
        r'(?:VITE_COGNITO_CLIENT_ID|userPoolClientId)\s*:\s*"(?P<value>[^"]+)"'
    ),
    "core_api_url": re.compile(
        r'(?:VITE_MAPIT_CORE_API:"|endpoint:")(?P<value>https://core\.[^"]+)"'
    ),
    "geo_api_url": re.compile(
        r'(?:VITE_MAPIT_GEO_API:"|endpoint:")(?P<value>https://geo\.[^"]+)"'
    ),
}
# The region used to be an explicit ``region:"eu-west-1"`` field; it is no
# longer emitted, so it is derived from a Cognito identifier when absent.
_REGION_RE = re.compile(r'region\s*:\s*"(?P<value>[a-z]{2}-[a-z]+-\d)"')

# Keys that have carried GPS accuracy in the various device state payloads.
_GPS_ACCURACY_KEYS = (
    "hdop",
    "gpsAccuracy",
    "gps_accuracy",
    "accuracy",
    "horizontalAccuracy",
    "horizontal_accuracy",
    "hAcc",
)


class MapitError(Exception):
    """Base error for the Mapit integration."""


class MapitAuthError(MapitError):
    """Authentication failed."""


class MapitConnectionError(MapitError):
    """Connection to Mapit failed."""


@dataclass(slots=True)
class CognitoTokens:
    """Cognito user pool tokens."""

    access_token: str
    id_token: str
    refresh_token: str | None
    expires_at: datetime


@dataclass(slots=True)
class AwsCredentials:
    """Temporary AWS credentials from the identity pool."""

    access_key_id: str
    secret_key: str
    session_token: str
    expiration: datetime


@dataclass(slots=True)
class MapitRuntimeConfig:
    """Cognito and endpoint configuration used to talk to Mapit."""

    region: str
    user_pool_id: str
    app_client_id: str
    identity_pool_id: str
    core_api_url: str
    geo_api_url: str
    devicestate_ws_url: str
    source: str = "fallback"

    @property
    def cognito_logins_key(self) -> str:
        """Return the Cognito logins map key for this user pool."""
        return f"cognito-idp.{self.region}.amazonaws.com/{self.user_pool_id}"

    @property
    def cognito_idp_url(self) -> str:
        """Return the Cognito identity provider endpoint."""
        return f"https://cognito-idp.{self.region}.amazonaws.com/"

    @property
    def cognito_identity_url(self) -> str:
        """Return the Cognito identity pool endpoint."""
        return f"https://cognito-identity.{self.region}.amazonaws.com/"


def default_runtime_config(
    overrides: dict[str, str] | None = None,
) -> MapitRuntimeConfig:
    """Return the fallback runtime config, applying user-supplied overrides.

    Overrides come from config entries created before runtime discovery
    existed, where the pool identifiers were typed in by hand.
    """
    overrides = overrides or {}
    user_pool_id = overrides.get("user_pool_id") or DEFAULT_COGNITO_USER_POOL_ID
    identity_pool_id = (
        overrides.get("identity_pool_id") or DEFAULT_COGNITO_IDENTITY_POOL_ID
    )
    return MapitRuntimeConfig(
        region=_region_from_identifiers(user_pool_id, identity_pool_id),
        user_pool_id=user_pool_id,
        app_client_id=overrides.get("app_client_id") or DEFAULT_COGNITO_APP_CLIENT_ID,
        identity_pool_id=identity_pool_id,
        core_api_url=DEFAULT_CORE_API_URL,
        geo_api_url=DEFAULT_GEO_API_URL,
        devicestate_ws_url=DEFAULT_DEVICESTATE_WS_URL,
        source="overrides" if overrides else "fallback",
    )


class MapitApiClient:
    """Mapit HTTP API client running on Home Assistant's aiohttp session."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        email: str,
        password: str,
        *,
        overrides: dict[str, str] | None = None,
        save_tokens: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        """Initialise the client."""
        self._session = session
        self._email = email
        self._password = password
        self._overrides = overrides or {}
        self._save_tokens = save_tokens
        self._runtime = default_runtime_config(self._overrides)
        self._account: dict[str, Any] | None = None
        self._tokens: CognitoTokens | None = None
        self._aws_credentials: AwsCredentials | None = None
        self._identity_id: str | None = None
        self._account_id: str | None = None
        self._route_cache: dict[str, tuple[datetime, list[dict[str, Any]]]] = {}

    @property
    def runtime_config(self) -> MapitRuntimeConfig:
        """Return the runtime configuration currently in use."""
        return self._runtime

    @property
    def account_id(self) -> str | None:
        """Return the resolved account id, if known."""
        return self._account_id

    def restore_tokens(self, data: dict[str, Any] | None) -> None:
        """Restore a previously persisted refresh token and identity id.

        This lets a Home Assistant restart resume the session instead of
        replaying a full password login.
        """
        if not data:
            return

        self._identity_id = data.get("identity_id")
        self._account_id = data.get("account_id")
        refresh_token = data.get("refresh_token")
        if refresh_token:
            # An expired placeholder forces a refresh on the first request.
            self._tokens = CognitoTokens(
                access_token="",
                id_token="",
                refresh_token=refresh_token,
                expires_at=datetime.now(UTC) - timedelta(seconds=1),
            )

    async def _async_persist_tokens(self) -> None:
        """Persist the pieces of the session worth surviving a restart."""
        if self._save_tokens is None:
            return

        await self._save_tokens(
            {
                "refresh_token": self._tokens.refresh_token if self._tokens else None,
                "identity_id": self._identity_id,
                "account_id": self._account_id,
            }
        )

    async def async_validate_credentials(self) -> dict[str, Any]:
        """Validate credentials and return the account payload."""
        await self._ensure_authenticated(force_login=True)
        return await self.async_get_account()

    async def async_discover_runtime_config(
        self, *, force: bool = False
    ) -> MapitRuntimeConfig:
        """Discover the runtime configuration from the public Mapit frontend."""
        if self._runtime.source == "discovered" and not force:
            return self._runtime

        try:
            discovered, bundle_url = await self._discover_from_frontend()
        except MapitError as err:
            if force:
                raise
            _LOGGER.debug("Falling back to built-in Mapit config: %s", err)
            self._runtime = default_runtime_config(self._overrides)
        else:
            self._runtime = discovered
            _LOGGER.debug(
                "Discovered Mapit runtime config from frontend bundle %s", bundle_url
            )

        return self._runtime

    async def _discover_from_frontend(self) -> tuple[MapitRuntimeConfig, str]:
        """Locate a frontend bundle holding the runtime config and parse it."""
        html_text = await self._fetch_text(MAPIT_APP_URL)
        bundle_urls = extract_bundle_urls(html_text)
        if not bundle_urls:
            raise MapitConnectionError("Could not locate Mapit frontend bundle")

        last_error: MapitError | None = None
        for bundle_url in bundle_urls:
            bundle_text = await self._fetch_text(bundle_url)
            try:
                return extract_runtime_config(bundle_text), bundle_url
            except MapitConnectionError as err:
                last_error = err

        raise last_error or MapitConnectionError(
            "Could not extract Mapit runtime config"
        )

    async def _fetch_text(self, url: str) -> str:
        """Fetch text content from a URL."""
        try:
            async with self._session.get(url) as response:
                text = await response.text()
                status = response.status
        except aiohttp.ClientError as err:
            raise MapitConnectionError(str(err)) from err

        if status >= 400:
            raise MapitConnectionError(f"HTTP {status} while fetching {url}")

        return text

    async def async_get_snapshot(self) -> dict[str, Any]:
        """Fetch the full integration snapshot."""
        summary = await self.async_get_account_summary()
        vehicles: list[dict[str, Any]] = summary.get("vehicles", [])

        vehicle_details: dict[str, dict[str, Any]] = {}
        routes: dict[str, list[dict[str, Any]]] = {}

        for vehicle in vehicles:
            vehicle_id = vehicle.get("id")
            if not vehicle_id:
                continue
            vehicle_details[vehicle_id] = await self.async_get_vehicle_detail(vehicle_id)
            routes[vehicle_id] = await self.async_get_routes(vehicle_id)

        return {
            "account": summary.get("account", {}),
            "summary": summary,
            "vehicles": vehicles,
            "vehicle_details": vehicle_details,
            "routes": routes,
        }

    async def async_get_account(self) -> dict[str, Any]:
        """Return the account for the authenticated user."""
        if self._account is not None:
            return self._account

        summary = await self.async_get_account_summary()
        return summary["account"]

    async def async_get_account_summary(self) -> dict[str, Any]:
        """Fetch the summary for the authenticated account.

        The backend resolves the account from the request credentials, so no
        account id or email is needed. The old ``/v1/accounts?email=`` lookup
        is rejected with HTTP 403.
        """
        payload = await self._mapit_request(
            "GET", f"{self._runtime.core_api_url}/v1/account-summary"
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("account"), dict):
            raise MapitAuthError("No account returned for the authenticated user")

        account = payload["account"]
        self._account = account
        self._account_id = account.get("id")
        return payload

    async def async_get_vehicle_detail(self, vehicle_id: str) -> dict[str, Any]:
        """Fetch the vehicle detail payload."""
        return await self._mapit_request(
            "GET", f"{self._runtime.core_api_url}/v1/vehicles/{vehicle_id}"
        )

    async def async_get_routes(self, vehicle_id: str) -> list[dict[str, Any]]:
        """Fetch route summaries for a vehicle, cached to limit API calls."""
        now = datetime.now(UTC)
        cached = self._route_cache.get(vehicle_id)
        if cached is not None and now - cached[0] < ROUTE_CACHE_INTERVAL:
            return cached[1]

        payload = await self._mapit_request(
            "GET",
            f"{self._runtime.geo_api_url}/v1/routes",
            params={"vehicleId": vehicle_id},
        )
        routes = payload.get("data", []) if isinstance(payload, dict) else []
        routes.sort(key=lambda item: item.get("startedAt", ""), reverse=True)
        self._route_cache[vehicle_id] = (now, routes)
        return routes

    async def async_get_route_detail(self, route_id: str) -> dict[str, Any]:
        """Fetch a route detail payload."""
        return await self._mapit_request(
            "GET", f"{self._runtime.geo_api_url}/v1/routes/{route_id}"
        )

    async def async_export_route_gpx(self, route_id: str) -> dict[str, Any]:
        """Build GPX from a route detail payload."""
        route = await self.async_get_route_detail(route_id)
        return {
            "route_id": route_id,
            "started_at": route.get("startedAt"),
            "ended_at": route.get("endedAt"),
            "distance_m": route.get("distance"),
            "gpx": build_gpx(route),
        }

    async def async_ws_connect(self, device_id: str) -> aiohttp.ClientWebSocketResponse:
        """Open the realtime websocket for a device."""
        await self._ensure_authenticated()
        assert self._tokens is not None

        try:
            return await self._session.ws_connect(
                f"{self._runtime.devicestate_ws_url}/{device_id}",
                origin=MAPIT_APP_URL,
                protocols=(self._tokens.id_token,),
                heartbeat=WEBSOCKET_HEARTBEAT,
            )
        except aiohttp.ClientError as err:
            raise MapitConnectionError(str(err)) from err

    async def _ensure_authenticated(self, *, force_login: bool = False) -> None:
        """Make sure valid Cognito tokens and AWS credentials are available."""
        await self.async_discover_runtime_config()
        now = datetime.now(UTC)

        if force_login or self._tokens is None:
            await self._login()
        elif self._tokens.expires_at - AUTH_REFRESH_MARGIN <= now:
            try:
                await self._refresh_tokens()
            except MapitAuthError:
                _LOGGER.info("Token refresh failed; falling back to password login")
                await self._login()

        if self._aws_credentials is None or self._aws_credentials.expiration <= now:
            try:
                await self._refresh_aws_credentials()
            except MapitAuthError:
                _LOGGER.info(
                    "AWS credential refresh failed; retrying with a fresh login"
                )
                await self._login()

    async def _login(self) -> None:
        """Perform a full username/password login."""
        self._account = None
        self._account_id = None
        payload = await self._cognito_idp_request(
            target="AWSCognitoIdentityProviderService.InitiateAuth",
            body={
                "AuthFlow": "USER_PASSWORD_AUTH",
                "ClientId": self._runtime.app_client_id,
                "AuthParameters": {
                    "USERNAME": self._email,
                    "PASSWORD": self._password,
                },
                "ClientMetadata": {},
            },
        )
        auth_result = payload.get("AuthenticationResult", {})
        self._set_tokens(auth_result, auth_result.get("RefreshToken"))
        await self._refresh_aws_credentials(force_new_identity=True)
        await self._async_persist_tokens()

    async def _refresh_tokens(self) -> None:
        """Refresh the Cognito tokens using the stored refresh token."""
        if self._tokens is None or self._tokens.refresh_token is None:
            await self._login()
            return

        payload = await self._cognito_idp_request(
            target="AWSCognitoIdentityProviderService.InitiateAuth",
            body={
                "AuthFlow": "REFRESH_TOKEN_AUTH",
                "ClientId": self._runtime.app_client_id,
                "AuthParameters": {"REFRESH_TOKEN": self._tokens.refresh_token},
                "ClientMetadata": {},
            },
        )
        auth_result = payload.get("AuthenticationResult", {})
        self._set_tokens(auth_result, self._tokens.refresh_token)
        self._aws_credentials = None
        await self._async_persist_tokens()

    def _set_tokens(
        self, auth_result: dict[str, Any], refresh_token: str | None
    ) -> None:
        """Store the tokens from a Cognito auth result."""
        access_token = auth_result["AccessToken"]
        id_token = auth_result["IdToken"]
        expires_in = int(auth_result.get("ExpiresIn", 3600))
        expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)
        jwt_exp = decode_jwt_exp(id_token)
        if jwt_exp is not None:
            expires_at = jwt_exp

        self._tokens = CognitoTokens(
            access_token=access_token,
            id_token=id_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
        )

    async def _refresh_aws_credentials(
        self, *, force_new_identity: bool = False
    ) -> None:
        """Exchange the Cognito ID token for temporary AWS credentials."""
        if self._tokens is None or not self._tokens.id_token:
            raise MapitAuthError("Cannot refresh AWS credentials without tokens")

        if force_new_identity or self._identity_id is None:
            identity_payload = await self._cognito_identity_request(
                target="AWSCognitoIdentityService.GetId",
                body={
                    "IdentityPoolId": self._runtime.identity_pool_id,
                    "Logins": {self._runtime.cognito_logins_key: self._tokens.id_token},
                },
            )
            self._identity_id = identity_payload["IdentityId"]

        credentials_payload = await self._cognito_identity_request(
            target="AWSCognitoIdentityService.GetCredentialsForIdentity",
            body={
                "IdentityId": self._identity_id,
                "Logins": {self._runtime.cognito_logins_key: self._tokens.id_token},
            },
        )
        credentials = credentials_payload["Credentials"]
        self._aws_credentials = AwsCredentials(
            access_key_id=credentials["AccessKeyId"],
            secret_key=credentials["SecretKey"],
            session_token=credentials["SessionToken"],
            expiration=parse_aws_timestamp(credentials["Expiration"]),
        )

    async def _cognito_idp_request(
        self, target: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        """Call the Cognito identity provider API."""
        return await self._aws_json_request(
            url=self._runtime.cognito_idp_url, target=target, body=body
        )

    async def _cognito_identity_request(
        self, target: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        """Call the Cognito identity pool API."""
        return await self._aws_json_request(
            url=self._runtime.cognito_identity_url, target=target, body=body
        )

    async def _aws_json_request(
        self, *, url: str, target: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        """Send an AWS JSON 1.1 request."""
        headers = {
            "Content-Type": "application/x-amz-json-1.1",
            "X-Amz-Target": target,
            "X-Amz-User-Agent": "aws-amplify/5.0.4 js",
            "Accept": "*/*",
            "Origin": MAPIT_APP_URL,
            "Referer": f"{MAPIT_APP_URL}/",
        }

        try:
            async with self._session.post(
                url, data=json.dumps(body), headers=headers
            ) as response:
                text = await response.text()
                status = response.status
        except aiohttp.ClientError as err:
            raise MapitConnectionError(str(err)) from err

        if status >= 400:
            try:
                error_payload = json.loads(text)
            except json.JSONDecodeError:
                error_payload = {"message": text}
            error_name = error_payload.get("__type", "")
            message = (
                error_payload.get("message") or error_payload.get("Message") or text
            )
            if "NotAuthorized" in error_name or status in {400, 401, 403}:
                raise MapitAuthError(message)
            raise MapitConnectionError(message)

        return json.loads(text)

    async def _mapit_request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        retry_on_auth_error: bool = True,
    ) -> Any:
        """Send a SigV4-signed request to the Mapit API."""
        await self._ensure_authenticated()
        assert self._tokens is not None
        assert self._aws_credentials is not None

        headers = self._build_mapit_headers(method, url, params=params)

        try:
            async with self._session.request(
                method, url, params=params, headers=headers
            ) as response:
                text = await response.text()
                status = response.status
        except aiohttp.ClientError as err:
            raise MapitConnectionError(str(err)) from err

        if status in {401, 403} and retry_on_auth_error:
            _LOGGER.debug("Refreshing auth after %s from %s", status, url)
            await self._login()
            return await self._mapit_request(
                method, url, params=params, retry_on_auth_error=False
            )

        if status >= 400:
            message = text
            try:
                payload = json.loads(text)
                message = payload.get("message") or payload.get("Message") or text
            except json.JSONDecodeError:
                pass
            if status in {401, 403}:
                raise MapitAuthError(message)
            raise MapitConnectionError(message)

        if not text:
            return None

        return json.loads(text)

    def _build_mapit_headers(
        self, method: str, url: str, *, params: dict[str, Any] | None = None
    ) -> dict[str, str]:
        """Build the AWS Signature V4 headers for a request."""
        assert self._tokens is not None
        assert self._aws_credentials is not None

        now = datetime.now(UTC)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        parsed_url = urlsplit(url)
        canonical_uri = quote(parsed_url.path or "/", safe="/-_.~")
        canonical_querystring = canonical_query(params or {})

        canonical_headers = (
            "accept:application/json\n"
            f"host:{parsed_url.netloc}\n"
            f"x-amz-date:{amz_date}\n"
        )
        signed_headers = "accept;host;x-amz-date"
        canonical_request = "\n".join(
            [
                method.upper(),
                canonical_uri,
                canonical_querystring,
                canonical_headers,
                signed_headers,
                hashlib.sha256(b"").hexdigest(),
            ]
        )

        credential_scope = f"{date_stamp}/{self._runtime.region}/execute-api/aws4_request"
        string_to_sign = "\n".join(
            [
                "AWS4-HMAC-SHA256",
                amz_date,
                credential_scope,
                hashlib.sha256(canonical_request.encode()).hexdigest(),
            ]
        )

        signing_key = get_signature_key(
            self._aws_credentials.secret_key,
            date_stamp,
            self._runtime.region,
            "execute-api",
        )
        signature = hmac.new(
            signing_key, string_to_sign.encode(), hashlib.sha256
        ).hexdigest()

        return {
            "Accept": "application/json",
            "Authorization": (
                "AWS4-HMAC-SHA256 "
                f"Credential={self._aws_credentials.access_key_id}/{credential_scope}, "
                f"SignedHeaders={signed_headers}, Signature={signature}"
            ),
            "Origin": MAPIT_APP_URL,
            "Referer": f"{MAPIT_APP_URL}/",
            "X-Amz-Date": amz_date,
            "X-Amz-Security-Token": self._aws_credentials.session_token,
            "X-Id-Token": self._tokens.id_token,
        }


def canonical_query(params: dict[str, Any]) -> str:
    """Build a canonical AWS query string."""
    if not params:
        return ""

    items: list[tuple[str, str]] = []
    for key, value in params.items():
        if isinstance(value, (list, tuple)):
            items.extend((str(key), str(item)) for item in value)
        else:
            items.append((str(key), str(value)))

    items.sort()
    return "&".join(
        f"{quote(key, safe='-_.~')}={quote(value, safe='-_.~')}" for key, value in items
    )


def sign(key: bytes, msg: str) -> bytes:
    """Create an HMAC-SHA256 signature."""
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def get_signature_key(
    key: str, date_stamp: str, region_name: str, service_name: str
) -> bytes:
    """Build an AWS Signature V4 signing key."""
    k_date = sign(("AWS4" + key).encode(), date_stamp)
    k_region = sign(k_date, region_name)
    k_service = sign(k_region, service_name)
    return sign(k_service, "aws4_request")


def decode_jwt_exp(token: str) -> datetime | None:
    """Decode the exp claim from a JWT without verifying it."""
    try:
        payload_b64 = token.split(".")[1]
        padding = "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + padding))
        return datetime.fromtimestamp(int(payload["exp"]), tz=UTC)
    except (IndexError, KeyError, ValueError, json.JSONDecodeError):
        return None


def parse_aws_timestamp(value: str | int | float) -> datetime:
    """Parse an AWS timestamp payload value."""
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return datetime.fromtimestamp(float(value), tz=UTC)


def parse_iso_datetime(value: str | None) -> datetime | None:
    """Parse an ISO datetime string."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def ms_to_datetime(value: int | float | None) -> datetime | None:
    """Convert epoch milliseconds to an aware UTC datetime."""
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value) / 1000, tz=UTC)
    except (TypeError, ValueError, OSError):
        return None


def parse_mapit_point(hex_value: str | None) -> tuple[float, float] | None:
    """Parse an EWKB point string into a latitude/longitude pair."""
    if not hex_value:
        return None

    try:
        data = bytes.fromhex(hex_value)
        byte_order = "<" if data[0] == 1 else ">"
        geom_type = struct.unpack(f"{byte_order}I", data[1:5])[0]
        has_srid = bool(geom_type & 0x20000000)
        geom_type &= 0xFFFF
        if geom_type != 1:
            return None

        offset = 9 if has_srid else 5
        lon = struct.unpack(f"{byte_order}d", data[offset : offset + 8])[0]
        lat = struct.unpack(f"{byte_order}d", data[offset + 8 : offset + 16])[0]
    except (IndexError, ValueError, struct.error):
        return None

    return lat, lon


def extract_device_coordinates(
    state: dict[str, Any] | None,
) -> tuple[float, float] | None:
    """Extract latitude/longitude from a websocket or REST device state."""
    if not state:
        return None

    lat = state.get("lat")
    lng = state.get("lng")
    if lat is not None and lng is not None:
        try:
            return float(lat), float(lng)
        except (TypeError, ValueError):
            return None

    return parse_mapit_point(state.get("location"))


def coerce_number(value: Any) -> Any:
    """Convert numeric-like values to int/float when possible."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return float(stripped) if "." in stripped else int(stripped)
        except ValueError:
            return value
    return value


def find_first_key(payload: Any, keys: tuple[str, ...]) -> Any:
    """Find the first of ``keys`` present anywhere in a nested payload."""
    if isinstance(payload, dict):
        for key in keys:
            if payload.get(key) is not None:
                return coerce_number(payload[key])
        for nested in payload.values():
            found = find_first_key(nested, keys)
            if found is not None:
                return found
        return None

    if isinstance(payload, list):
        for item in payload:
            found = find_first_key(item, keys)
            if found is not None:
                return found

    return None


def extract_gps_accuracy(state: dict[str, Any] | None) -> Any:
    """Extract GPS accuracy/HDOP from the known device state variants."""
    if not state:
        return None

    direct = find_first_key(state, _GPS_ACCURACY_KEYS)
    if direct is not None:
        return direct

    # Some payloads put telemetry in a serialized `data` field.
    data_payload = state.get("data")
    if isinstance(data_payload, str):
        try:
            data_payload = json.loads(data_payload)
        except ValueError:
            return None

    return find_first_key(data_payload, _GPS_ACCURACY_KEYS)


def extract_speed(state: dict[str, Any] | None) -> float | int | None:
    """Return the reported speed, normalised to zero while parked.

    The API keeps reporting a residual speed after the vehicle stops, which
    would otherwise trigger speed automations on a parked bike.
    """
    if not state:
        return None

    if state.get("status") == STATUS_AT_REST:
        return 0

    speed = coerce_number(state.get("speed"))
    return speed if isinstance(speed, (int, float)) else None


def extract_bundle_urls(index_html: str) -> list[str]:
    """Return frontend bundle URLs, most likely to hold the config first.

    The Amplify runtime config lives in the entry bundle (``main-*.js``), so
    the entry/index chunks are tried before the lazily loaded ones.
    """
    seen: set[str] = set()
    paths: list[str] = []
    for match in _BUNDLE_PATH_RE.finditer(index_html):
        path = match.group(0)
        if path not in seen:
            seen.add(path)
            paths.append(path)

    def rank(path: str) -> int:
        name = path.rsplit("/", 1)[-1]
        if name.startswith("main"):
            return 0
        if name.startswith("index"):
            return 1
        return 2

    paths.sort(key=rank)
    return [urljoin(MAPIT_APP_URL, html.unescape(path)) for path in paths]


def extract_runtime_config(bundle_text: str) -> MapitRuntimeConfig:
    """Extract the runtime config values from a frontend bundle."""
    values: dict[str, str] = {}
    for key, pattern in _DISCOVERY_PATTERNS.items():
        match = pattern.search(bundle_text)
        if match is None:
            raise MapitConnectionError(f"Missing Mapit runtime field: {key}")
        values[key] = match.group("value")

    region_match = _REGION_RE.search(bundle_text)
    region = (
        region_match.group("value")
        if region_match
        else _region_from_identifiers(values["user_pool_id"], values["identity_pool_id"])
    )

    return MapitRuntimeConfig(
        region=region,
        user_pool_id=values["user_pool_id"],
        app_client_id=values["app_client_id"],
        identity_pool_id=values["identity_pool_id"],
        core_api_url=values["core_api_url"],
        geo_api_url=values["geo_api_url"],
        devicestate_ws_url=derive_ws_url(values["core_api_url"]),
        source="discovered",
    )


def _region_from_identifiers(*identifiers: str | None) -> str:
    """Derive the AWS region from a Cognito identifier.

    Cognito identifiers are prefixed with their region, e.g.
    ``eu-west-1_nHd6Er8N6`` or ``eu-west-1:<guid>``.
    """
    for identifier in identifiers:
        if not identifier:
            continue
        region = re.split(r"[_:]", identifier, maxsplit=1)[0]
        if re.fullmatch(r"[a-z]{2}-[a-z]+-\d", region):
            return region
    return DEFAULT_COGNITO_REGION


def derive_ws_url(core_api_url: str) -> str:
    """Derive the realtime websocket base URL from the core API URL."""
    host = urlsplit(core_api_url).netloc
    if host.startswith("core."):
        host = f"dsw.{host[5:]}"
    return f"wss://{host}/devicestate"


def build_gpx(route: dict[str, Any]) -> str:
    """Convert a route GeoJSON payload into a GPX document."""
    route_id = route.get("id", "route")
    started_at = route.get("startedAt")
    ended_at = route.get("endedAt")

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="Home Assistant Mapit Tracker"'
        ' xmlns="http://www.topografix.com/GPX/1/1">',
        "  <metadata>",
        f"    <name>{xml_escape(str(route_id))}</name>",
    ]
    if started_at:
        lines.append(f"    <time>{xml_escape(str(started_at))}</time>")
    lines.extend(
        [
            "  </metadata>",
            "  <trk>",
            f"    <name>{xml_escape(str(route_id))}</name>",
            "    <trkseg>",
        ]
    )
    for coordinate in extract_route_coordinates(route):
        lon, lat = coordinate[0], coordinate[1]
        ele = coordinate[2] if len(coordinate) > 2 else None
        lines.append(f'      <trkpt lat="{lat}" lon="{lon}">')
        if ele is not None:
            lines.append(f"        <ele>{ele}</ele>")
        lines.append("      </trkpt>")
    lines.extend(["    </trkseg>", "  </trk>"])
    if ended_at:
        lines.append(f"  <!-- endedAt: {xml_escape(str(ended_at))} -->")
    lines.append("</gpx>")
    return "\n".join(lines)


def extract_route_coordinates(route: dict[str, Any]) -> list[list[float]]:
    """Extract the first LineString coordinate list from a route payload."""
    geojson = route.get("geoJSON") or {}
    for feature in geojson.get("features", []):
        geometry = feature.get("geometry", {})
        if geometry.get("type") == "LineString":
            return geometry.get("coordinates", [])
    return []


def xml_escape(value: str) -> str:
    """Escape XML text."""
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )
