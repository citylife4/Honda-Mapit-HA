"""Mapit API wrapper for Home Assistant integration."""
from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path

import requests

_LOGGER = logging.getLogger(__name__)


class MapitAPI:
    """API wrapper for Mapit vehicle tracking service."""

    def __init__(
        self,
        username: str,
        password: str,
        identity_pool_id: str,
        user_pool_id: str,
        user_pool_client_id: str,
        hass=None,
    ):
        """Initialize the API."""
        self.username = username
        self.password = password
        self.identity_pool_id = identity_pool_id
        self.user_pool_id = user_pool_id
        self.user_pool_client_id = user_pool_client_id
        self.hass = hass

        # API configuration
        self.auth_flow_type = "USER_PASSWORD_AUTH"
        self.service = "execute-api"
        self.host = "core.prod.mapit.me"
        self.region = "eu-west-1"
        self.url_idp = f"cognito-idp.{self.region}.amazonaws.com"
        self.url_identity = f"cognito-identity.{self.region}.amazonaws.com"

        # Tokens (will be populated during authentication)
        self.id_token = None
        self.access_token = None
        self.access_key = None
        self.secret_key = None
        self.session_token = None
        self.identity_id = None
        self.account_id = None

        # Token cache file path
        if hass:
            self.token_cache_file = Path(hass.config.config_dir) / ".mapit_tokens.json"
        else:
            self.token_cache_file = Path("tokens.json")

        # Try to load cached tokens
        self._load_cached_tokens()

    def _load_cached_tokens(self):
        """Load tokens from cache file."""
        try:
            if self.token_cache_file.exists():
                with open(self.token_cache_file, "r") as f:
                    tokens = json.load(f)
                self.access_key = tokens.get("access_key")
                self.secret_key = tokens.get("secret_key")
                self.session_token = tokens.get("session_token")
                self.identity_id = tokens.get("identity_id")
                self.id_token = tokens.get("id_token")
                self.access_token = tokens.get("access_token")
                self.account_id = tokens.get("id")
                _LOGGER.debug("Loaded cached tokens")
        except Exception as e:
            _LOGGER.debug("Could not load cached tokens: %s", e)

    def _save_tokens_to_cache(self):
        """Save tokens to cache file."""
        try:
            tokens = {
                "access_key": self.access_key,
                "secret_key": self.secret_key,
                "session_token": self.session_token,
                "identity_id": self.identity_id,
                "id_token": self.id_token,
                "access_token": self.access_token,
                "id": self.account_id,
            }
            with open(self.token_cache_file, "w") as f:
                json.dump(tokens, f)
            _LOGGER.debug("Saved tokens to cache")
        except Exception as e:
            _LOGGER.error("Could not save tokens to cache: %s", e)

    def _reset_auth_state(self, clear_cache: bool = False, keep_account_id: bool = False):
        """Reset in-memory auth state and optionally clear cached tokens."""
        self.id_token = None
        self.access_token = None
        self.access_key = None
        self.secret_key = None
        self.session_token = None
        self.identity_id = None
        if not keep_account_id:
            self.account_id = None

        if clear_cache:
            try:
                if self.token_cache_file.exists():
                    self.token_cache_file.unlink()
                    _LOGGER.debug("Cleared token cache file")
            except Exception as e:
                _LOGGER.warning("Could not clear token cache file: %s", e)

    @staticmethod
    def _extract_error_detail(response: requests.Response) -> str:
        """Extract a useful error message from an API response."""
        try:
            payload = response.json()
            if isinstance(payload, dict):
                for key in ("message", "Message", "error", "Error", "__type"):
                    value = payload.get(key)
                    if value:
                        return str(value)
                return json.dumps(payload)
            return str(payload)
        except ValueError:
            return response.text.strip()

    @staticmethod
    def _is_expired_token_error(detail: str) -> bool:
        """Check if an error detail indicates an expired token."""
        normalized = detail.lower()
        return (
            "expiredtokenexception" in normalized
            or "security token included in the request is expired" in normalized
            or "token expired" in normalized
            or ("token" in normalized and "expired" in normalized)
        )

    def _send_request(self, url, headers, payload=None, method="POST", url_prefix="https://"):
        """Send HTTP request to API."""
        headers["content-type"] = "application/x-amz-json-1.1"
        response = requests.request(method, url_prefix + url, headers=headers, json=payload, timeout=30)

        detail = self._extract_error_detail(response)

        if response.status_code in (401, 403):
            if self._is_expired_token_error(detail):
                _LOGGER.warning("Token expired, need to re-authenticate: %s", detail)
                raise TokenExpiredError(detail or "Token expired")

            _LOGGER.error("Authentication/authorization failed: %s - %s", response.status_code, detail)
            raise RequestForbiddenError(f"Request failed with status {response.status_code}: {detail}")

        if response.status_code != 200:
            _LOGGER.error("Request failed: %s - %s", response.status_code, detail)
            raise RequestFailedError(f"Request failed with status {response.status_code}: {detail}")

        return response.json()

    def authenticate(self):
        """Authenticate with Mapit API and get all required tokens."""
        _LOGGER.debug("Starting authentication")

        # Step 1: Get ID and Access tokens
        payload = {
            "AuthFlow": self.auth_flow_type,
            "ClientId": self.user_pool_client_id,
            "AuthParameters": {"USERNAME": self.username, "PASSWORD": self.password},
            "ClientMetadata": {},
        }
        headers = {"x-amz-target": "AWSCognitoIdentityProviderService.InitiateAuth"}

        response = self._send_request(self.url_idp, headers, payload=payload)
        self.id_token = response["AuthenticationResult"]["IdToken"]
        self.access_token = response["AuthenticationResult"]["AccessToken"]

        # Step 2: Get Identity ID
        payload = {
            "IdentityPoolId": self.identity_pool_id,
            "Logins": {f"{self.url_idp}/{self.user_pool_id}": self.id_token},
        }
        headers = {"x-amz-target": "AWSCognitoIdentityService.GetId"}

        response = self._send_request(self.url_identity, headers, payload=payload)
        self.identity_id = response["IdentityId"]

        # Step 3: Get AWS Credentials
        payload = {
            "IdentityId": self.identity_id,
            "Logins": {f"{self.url_idp}/{self.user_pool_id}": self.id_token},
        }
        headers = {"x-amz-target": "AWSCognitoIdentityService.GetCredentialsForIdentity"}

        response = self._send_request(self.url_identity, headers, payload=payload)
        self.access_key = response["Credentials"]["AccessKeyId"]
        self.secret_key = response["Credentials"]["SecretKey"]
        self.session_token = response["Credentials"]["SessionToken"]

        # Step 4: Get Account ID
        if self.account_id:
            _LOGGER.debug("Using cached account ID")
        else:
            try:
                self.account_id = self._resolve_account_id()
            except (RequestForbiddenError, RequestFailedError) as err:
                _LOGGER.error("Failed to resolve account ID: %s", err)
                raise

        # Save tokens to cache
        self._save_tokens_to_cache()

        _LOGGER.info("Authentication successful")

    def _sign(self, key, msg):
        """Sign a message with HMAC-SHA256."""
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    def _get_signature_key(self, key, date_stamp):
        """Generate AWS signature key."""
        k_date = self._sign(("AWS4" + key).encode("utf-8"), date_stamp)
        k_region = self._sign(k_date, self.region)
        k_service = self._sign(k_region, self.service)
        k_signing = self._sign(k_service, "aws4_request")
        return k_signing

    def _create_auth_header(self, method, spacename, canonical_querystring=""):
        """Create AWS Signature Version 4 authorization header."""
        t = datetime.datetime.utcnow()
        amz_date = t.strftime("%Y%m%dT%H%M%SZ")
        datestamp = t.strftime("%Y%m%d")

        canonical_headers = (
            f"accept:application/json\nhost:{self.host}\nx-amz-date:{amz_date}\n"
        )
        signed_headers = "accept;host;x-amz-date"
        algorithm = "AWS4-HMAC-SHA256"
        credential_scope = f"{datestamp}/{self.region}/{self.service}/aws4_request"
        payload_hash = hashlib.sha256(b"").hexdigest()

        canonical_request = (
            f"{method}\n{spacename}\n{canonical_querystring}\n"
            f"{canonical_headers}\n{signed_headers}\n{payload_hash}"
        )

        string_to_sign = (
            f"{algorithm}\n{amz_date}\n{credential_scope}\n"
            f"{hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()}"
        )

        signing_key = self._get_signature_key(self.secret_key, datestamp)
        signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

        auth_value = (
            f"{algorithm} Credential={self.access_key}/{datestamp}/{self.region}/"
            f"{self.service}/aws4_request, SignedHeaders={signed_headers} Signature={signature}"
        )

        return auth_value, amz_date

    def _authorized_request(self, spacename, canonical_querystring="", method="GET"):
        """Make an authorized request to the Mapit API."""
        auth_value, amz_date = self._create_auth_header(method, spacename, canonical_querystring)

        url = f"{self.host}{spacename}?{canonical_querystring}"
        headers = {
            "host": self.host,
            "Accept": "application/json",
            "X-Amz-Security-Token": self.session_token,
            "X-Id-Token": self.id_token,
            "x-amz-date": amz_date,
            "Authorization": auth_value,
        }

        return self._send_request(url, headers, method=method)

    def _resolve_account_id(self) -> str:
        """Resolve account ID from API."""
        try:
            response = self._authorized_request("/v1/vehicles")
            vehicles = response.get("data", [])
            if vehicles and isinstance(vehicles[0], dict):
                account = vehicles[0].get("account", {})
                if isinstance(account, dict) and account.get("id"):
                    return account["id"]
            _LOGGER.warning("Could not resolve account ID from /v1/vehicles response")
        except RequestFailedError as err:
            _LOGGER.warning("Account ID lookup via /v1/vehicles failed: %s", err)

        # Legacy fallback path used by older API behavior
        response = self._authorized_request(
            "/v1/accounts", f"email={self.username.replace('@', '%40')}"
        )
        return response[0]["id"]

    def get_current_status(self):
        """Get current vehicle status."""
        response = None
        for attempt in range(2):
            had_auth_context = all(
                (
                    self.id_token,
                    self.access_key,
                    self.secret_key,
                    self.session_token,
                    self.account_id,
                )
            )
            try:
                if not had_auth_context:
                    _LOGGER.debug("Missing auth context, authenticating")
                    self.authenticate()

                spacename = f"/v1/accounts/{self.account_id}/summary"
                response = self._authorized_request(spacename)
                break
            except TokenExpiredError as err:
                if attempt == 1:
                    _LOGGER.error("Token expired after refresh retry: %s", err)
                    raise

                _LOGGER.info("Token expired, clearing cached tokens and retrying auth")
                self._reset_auth_state(clear_cache=True, keep_account_id=bool(self.account_id))
            except RequestForbiddenError as err:
                if attempt == 0:
                    err_lower = str(err).lower()
                    drop_account_id = "not authorized to access this account summary" in err_lower
                    keep_account_id = bool(self.account_id) and not drop_account_id
                    if drop_account_id:
                        _LOGGER.info("Current account ID is not authorized, retrying with account re-resolution")
                    else:
                        _LOGGER.info("Request forbidden, retrying with fresh auth")
                    self._reset_auth_state(clear_cache=True, keep_account_id=keep_account_id)
                    continue
                raise

        if response is None:
            raise RequestFailedError("Could not retrieve vehicle status")

        # Extract vehicle data
        vehicle = response["vehicles"][0]
        state = vehicle["device"]["state"]
        
        # Get speed and status
        speed = state["speed"]
        status = state["status"]
        
        # Normalize speed: set to 0 when vehicle is at rest
        # API sometimes reports residual speed values when stopped
        if status == "AT_REST":
            speed = 0

        return {
            "latitude": state["lat"],
            "longitude": state["lng"],
            "speed": speed,
            "status": status,
            "battery": state.get("battery", 0),
            "hdop": state.get("hdop"),
            "odometer": state.get("odometer"),
            "last_coord_ts": state.get("lastCoordTs"),
            "raw_data": response,
        }


class TokenExpiredError(Exception):
    """Exception raised when API token has expired."""


class RequestFailedError(Exception):
    """Exception raised when API request fails."""


class RequestForbiddenError(RequestFailedError):
    """Exception raised when API returns 401/403 for non-expiry reasons."""
