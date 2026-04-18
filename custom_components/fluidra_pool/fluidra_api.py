"""Fluidra Pool API wrapper for Home Assistant integration.

This module provides a simplified interface to the Fluidra Pool library
optimized for Home Assistant usage with real AWS Cognito authentication.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import quote

import aiohttp

from .api_resilience import (
    BACKOFF_MULTIPLIER,
    CIRCUIT_BREAKER_TIMEOUT,
    INITIAL_BACKOFF,
    MAX_BACKOFF,
    MAX_RETRIES,
    CircuitBreaker,
    FluidraAuthError,
    FluidraCircuitBreakerError,
    FluidraConnectionError,
    FluidraError,
    FluidraMFARequired,
    RateLimiter,
)
from .const import (
    COMPONENT_AUTO_MODE,
    COMPONENT_HEAT_PUMP_ONOFF,
    COMPONENT_HEAT_PUMP_SETPOINT,
    COMPONENT_PUMP_ONOFF,
    COMPONENT_PUMP_SPEED,
    COMPONENT_SCHEDULE,
    DEFAULT_TIMEOUT,
    PUMP_START_DELAY,
)
from .device_registry import DeviceIdentifier
from .utils import mask_device_id, mask_email

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant

FLUIDRA_EMEA_BASE: Final = "https://api.fluidra-emea.com"
COGNITO_ENDPOINT: Final = "https://cognito-idp.eu-west-1.amazonaws.com/"
COGNITO_CLIENT_ID: Final = "g3njunelkcbtefosqm9bdhhq1"
FLUIDRA_USER_AGENT: Final = (
    "com.fluidra.iaqualinkplus/1741857021 "
    "(Linux; U; Android 14; fr_FR; MI PAD 4; Build/UQ1A.240205.004; Cronet/140.0.7289.0)"
)
_RETRYABLE_STATUSES: Final[frozenset[int]] = frozenset({429, 500, 502, 503, 504})
_MAX_REFRESH_ATTEMPTS: Final = 1

_LOGGER = logging.getLogger(__name__)


class FluidraPoolAPI:
    """Wrapper for Fluidra Pool API for Home Assistant."""

    __slots__ = (
        "email",
        "password",
        "_hass",
        "_session",
        "_owns_session",
        "_session_lock",
        "access_token",
        "refresh_token",
        "id_token",
        "token_expires_at",
        "user_pools",
        "devices",
        "_pools",
        "_circuit_breaker",
        "_rate_limiter",
        "_token_lock",
        "_on_token_persist",
    )

    def __init__(
        self,
        email: str,
        password: str | None,
        hass: HomeAssistant | None = None,
        refresh_token: str | None = None,
        on_token_persist: Callable[[str], None] | None = None,
    ) -> None:
        """Initialize the API wrapper."""
        self.email: str = email
        self.password: str | None = password
        self._hass: HomeAssistant | None = hass
        self._session: aiohttp.ClientSession | None = None
        self._owns_session: bool = False
        self._session_lock: asyncio.Lock = asyncio.Lock()

        self.access_token: str | None = None
        self.refresh_token: str | None = refresh_token
        self.id_token: str | None = None
        self.token_expires_at: int | None = None

        self._on_token_persist: Callable[[str], None] | None = on_token_persist

        self.user_pools: list[dict[str, Any]] = []
        self.devices: list[dict[str, Any]] = []
        self._pools: list[dict[str, Any]] = []

        self._circuit_breaker: CircuitBreaker = CircuitBreaker()
        self._rate_limiter: RateLimiter = RateLimiter()

        self._token_lock: asyncio.Lock = asyncio.Lock()

    async def _get_session(self) -> aiohttp.ClientSession:
        """Return the shared or owned aiohttp session, creating it safely once."""
        if self._session is not None:
            return self._session

        async with self._session_lock:
            if self._session is not None:
                return self._session

            if self._hass is not None:
                from homeassistant.helpers.aiohttp_client import (  # noqa: PLC0415
                    async_get_clientsession,
                )

                self._session = async_get_clientsession(self._hass)
                self._owns_session = False
            else:
                timeout = aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT)
                self._session = aiohttp.ClientSession(timeout=timeout)
                self._owns_session = True

            return self._session

    async def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json_data: Any = None,
        params: dict[str, Any] | None = None,
        skip_circuit_breaker: bool = False,
        skip_auth_refresh: bool = False,
    ) -> tuple[int, Any, str]:
        """Execute HTTP request with circuit breaker, rate limiting, and retry.

        Returns a tuple ``(status, parsed_body_or_None, raw_text)`` so callers
        never hold an unclosed ClientResponse. Body is consumed inside ``async
        with`` to guarantee release.

        Retries on transient network errors AND on HTTP 429/5xx responses.
        Centralises 401/403 token refresh so call-sites don't need to recurse.
        """
        if not skip_circuit_breaker and not self._circuit_breaker.can_execute():
            raise FluidraCircuitBreakerError(f"Circuit breaker open, retry after {CIRCUIT_BREAKER_TIMEOUT}s")

        if not self._rate_limiter.can_execute():
            wait_time = self._rate_limiter.wait_time()
            _LOGGER.debug("Rate limited, waiting %.1fs", wait_time)
            await asyncio.sleep(wait_time)

        self._rate_limiter.record_request()

        session = await self._get_session()
        request_headers = dict(headers) if headers else {}

        last_error: Exception | None = None
        backoff = INITIAL_BACKOFF
        refresh_attempts = 0
        request_timeout = aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT)

        for attempt in range(MAX_RETRIES + 1):
            try:
                async with session.request(
                    method.upper(),
                    url,
                    headers=request_headers,
                    json=json_data,
                    params=params,
                    timeout=request_timeout,
                ) as response:
                    status = response.status
                    raw_text = await response.text()

                if status in (401, 403) and not skip_auth_refresh and refresh_attempts < _MAX_REFRESH_ATTEMPTS:
                    refresh_attempts += 1
                    _LOGGER.debug("Got %d, refreshing token and retrying", status)
                    if await self.ensure_valid_token():
                        if "Authorization" in request_headers:
                            request_headers["Authorization"] = f"Bearer {self.access_token}"
                        continue
                    return status, _parse_json(raw_text), raw_text

                if status in _RETRYABLE_STATUSES and attempt < MAX_RETRIES:
                    if not skip_circuit_breaker:
                        self._circuit_breaker.record_failure()
                    retry_after = _parse_retry_after(response) if status == 429 else None
                    sleep_for = retry_after if retry_after is not None else backoff
                    _LOGGER.debug(
                        "HTTP %d on attempt %d/%d, sleeping %.1fs",
                        status,
                        attempt + 1,
                        MAX_RETRIES + 1,
                        sleep_for,
                    )
                    last_error = FluidraConnectionError(f"HTTP {status}")
                    await asyncio.sleep(sleep_for)
                    backoff = min(backoff * BACKOFF_MULTIPLIER, MAX_BACKOFF)
                    continue

                if 200 <= status < 300 and not skip_circuit_breaker:
                    self._circuit_breaker.record_success()

                return status, _parse_json(raw_text), raw_text

            except (aiohttp.ClientError, TimeoutError) as err:
                last_error = err
                if not skip_circuit_breaker:
                    self._circuit_breaker.record_failure()

                if attempt < MAX_RETRIES:
                    _LOGGER.debug(
                        "Request failed (attempt %d/%d): %s, retrying in %.1fs",
                        attempt + 1,
                        MAX_RETRIES + 1,
                        err,
                        backoff,
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * BACKOFF_MULTIPLIER, MAX_BACKOFF)
                else:
                    _LOGGER.warning(
                        "Request failed after %d attempts: %s",
                        MAX_RETRIES + 1,
                        err,
                    )

        raise FluidraConnectionError(f"Request failed after {MAX_RETRIES + 1} attempts: {last_error}")

    async def authenticate(self) -> None:
        """Authenticate via AWS Cognito.

        Tries the stored refresh token first to avoid MFA on every HA restart.
        Falls back to full credentials auth only when needed.
        """
        try:
            if self.refresh_token:
                if await self.refresh_access_token():
                    _LOGGER.info("Authenticated via stored refresh token (no MFA required)")
                    await self._get_user_profile()
                    await self.async_update_data()
                    return
                _LOGGER.warning("Stored refresh token expired or invalid, falling back to full auth")

            await self._cognito_initial_auth()
            await self._get_user_profile()
            await self.async_update_data()

        except FluidraError:
            raise
        except (aiohttp.ClientError, TimeoutError, json.JSONDecodeError, KeyError) as err:
            raise FluidraAuthError(f"Authentication failed: {type(err).__name__}") from err

    async def _cognito_initial_auth(self) -> None:
        """Perform initial AWS Cognito authentication."""
        if not self.password:
            raise FluidraAuthError("Password required for initial authentication")

        auth_payload = {
            "AuthFlow": "USER_PASSWORD_AUTH",
            "ClientId": COGNITO_CLIENT_ID,
            "AuthParameters": {"USERNAME": self.email, "PASSWORD": self.password},
        }

        headers = {
            "Content-Type": "application/x-amz-json-1.1; charset=utf-8",
            "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
            "User-Agent": FLUIDRA_USER_AGENT,
        }

        status, data, raw_text = await self._request(
            "POST",
            COGNITO_ENDPOINT,
            headers=headers,
            json_data=auth_payload,
            skip_circuit_breaker=True,
            skip_auth_refresh=True,
        )

        if status != 200 or data is None:
            _LOGGER.debug("Cognito auth failed body: %s", raw_text[:500])
            raise FluidraAuthError(f"Cognito auth failed with status {status}")

        auth_result = data.get("AuthenticationResult")
        if not auth_result:
            challenge_name = data.get("ChallengeName", "")
            session_token = data.get("Session", "")
            if challenge_name in ("SOFTWARE_TOKEN_MFA", "SMS_MFA"):
                raise FluidraMFARequired(challenge_name, session_token)
            raise FluidraAuthError(f"Unexpected Cognito challenge: {challenge_name or 'none'}")

        self._store_tokens(auth_result)

    async def _cognito_respond_to_mfa(
        self, code: str, session: str, challenge_name: str = "SOFTWARE_TOKEN_MFA"
    ) -> None:
        """Complete a Cognito MFA challenge with a TOTP or SMS code."""
        payload = {
            "ChallengeName": challenge_name,
            "ClientId": COGNITO_CLIENT_ID,
            "Session": session,
            "ChallengeResponses": {
                "USERNAME": self.email,
                f"{challenge_name}_CODE": code,
            },
        }

        headers = {
            "Content-Type": "application/x-amz-json-1.1; charset=utf-8",
            "X-Amz-Target": "AWSCognitoIdentityProviderService.RespondToAuthChallenge",
            "User-Agent": FLUIDRA_USER_AGENT,
        }

        status, data, raw_text = await self._request(
            "POST",
            COGNITO_ENDPOINT,
            headers=headers,
            json_data=payload,
            skip_circuit_breaker=True,
            skip_auth_refresh=True,
        )

        if status != 200 or data is None:
            _LOGGER.debug("MFA verification failed body: %s", raw_text[:500])
            raise FluidraAuthError(f"MFA verification failed with status {status}")

        auth_result = data.get("AuthenticationResult", {})
        self._store_tokens(auth_result)
        if not self.access_token:
            raise FluidraAuthError("Access token not received after MFA")

    def _store_tokens(self, auth_result: dict[str, Any]) -> None:
        """Persist freshly-minted tokens and notify the entry callback."""
        self.access_token = auth_result.get("AccessToken")
        new_refresh = auth_result.get("RefreshToken")
        if new_refresh:
            self.refresh_token = new_refresh
        self.id_token = auth_result.get("IdToken")

        expires_in = auth_result.get("ExpiresIn", 3600)
        margin = min(300, max(30, expires_in // 10))
        self.token_expires_at = int(time.time()) + expires_in - margin

        if not self.access_token:
            raise FluidraAuthError("Access token not received")

        if self.refresh_token and self._on_token_persist:
            self._on_token_persist(self.refresh_token)

    async def _get_user_profile(self) -> dict[str, Any]:
        """Fetch user profile."""
        headers = self._build_auth_headers()
        profile_url = f"{FLUIDRA_EMEA_BASE}/mobile/consumers/me"

        try:
            status, data, _ = await self._request("GET", profile_url, headers=headers)
            if status == 200 and isinstance(data, dict):
                return data
        except FluidraError:
            _LOGGER.debug("Failed to get user profile, continuing anyway")
        return {}

    def _build_auth_headers(self) -> dict[str, str]:
        """Build standard authenticated headers."""
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": FLUIDRA_USER_AGENT,
        }

    async def async_update_data(self) -> None:
        """Discover pools and devices for the account; atomic replacement at end."""
        headers = self._build_auth_headers()
        pools_url = f"{FLUIDRA_EMEA_BASE}/generic/users/me/pools"

        user_pools: list[dict[str, Any]] = []
        devices: list[dict[str, Any]] = []

        try:
            status, data, _ = await self._request("GET", pools_url, headers=headers)
            if status == 200:
                if isinstance(data, list):
                    user_pools = data
                elif isinstance(data, dict):
                    user_pools = data.get("pools", [])

                for pool in user_pools:
                    pool_id = pool.get("id")
                    if pool_id:
                        pool_devices = await self._discover_devices_for_pool(pool_id, headers)
                        devices.extend(pool_devices)
        except FluidraError as err:
            _LOGGER.warning("Failed to update data: %s", err)
            return

        self.user_pools = user_pools
        self.devices = devices

    async def _discover_devices_for_pool(self, pool_id: str, headers: dict[str, str]) -> list[dict[str, Any]]:
        """Discover devices for a single pool. Returns newly-discovered devices only."""
        devices_url = f"{FLUIDRA_EMEA_BASE}/generic/devices"
        params = {"poolId": pool_id, "format": "tree"}

        try:
            status, devices_data, _ = await self._request("GET", devices_url, headers=headers, params=params)
            if status != 200:
                return []
        except FluidraError as err:
            _LOGGER.warning("Failed to discover devices for pool %s: %s", pool_id, err)
            return []

        if isinstance(devices_data, list):
            pool_devices = devices_data
        elif isinstance(devices_data, dict):
            pool_devices = devices_data.get("devices", [])
        else:
            return []

        result: list[dict[str, Any]] = []
        for device in pool_devices:
            device_id = device.get("id")
            info = device.get("info", {})
            device_name = info.get("name", f"Device {device_id}")
            family = info.get("family", "")
            connection_type = device.get("type", "unknown")

            device_type = _classify_device_type(family, device_name)
            is_bridge = "bridge" in family.lower() or bool(device.get("devices"))

            if is_bridge:
                children = device.get("devices") or []
                if isinstance(children, list):
                    for child_device in children:
                        child_device_id = child_device.get("id")
                        child_info = child_device.get("info", {})
                        child_device_name = child_info.get("name", f"Device {child_device_id}")
                        child_family = child_info.get("family", "")
                        child_connection_type = child_device.get("type", "unknown")

                        child_device_type = _classify_device_type(child_family, child_device_name)

                        result.append(
                            {
                                "pool_id": pool_id,
                                "device_id": child_device_id,
                                "name": child_device_name,
                                "type": child_device_type,
                                "family": child_family,
                                "connection_type": child_connection_type,
                                "model": child_device_name,
                                "manufacturer": "Fluidra",
                                "online": child_connection_type == "connected",
                                "is_running": False,
                                "auto_mode_enabled": False,
                                "operation_mode": 0,
                                "speed_percent": 0,
                                "parent_id": device_id,
                            }
                        )
                continue

            result.append(
                {
                    "pool_id": pool_id,
                    "device_id": device_id,
                    "name": device_name,
                    "type": device_type,
                    "family": family,
                    "connection_type": connection_type,
                    "model": device_name,
                    "manufacturer": "Fluidra",
                    "online": connection_type == "connected",
                    "is_running": False,
                    "auto_mode_enabled": False,
                    "operation_mode": 0,
                    "speed_percent": 0,
                    "variable_speed": True,
                    "pump_type": "variable_speed",
                }
            )

        return result

    def is_token_expired(self) -> bool:
        """Return True if the token is near or past its expiration margin."""
        if not self.token_expires_at:
            return True
        return int(time.time()) >= self.token_expires_at

    async def ensure_valid_token(self) -> bool:
        """Ensure the access token is valid, refreshing if needed.

        Returns True if token is valid/renewed, False if credentials are definitively
        invalid (MFA required). Raises for transient network errors.
        """
        if not self.is_token_expired():
            return True

        async with self._token_lock:
            if not self.is_token_expired():
                return True

            if await self.refresh_access_token():
                return True

            _LOGGER.warning(
                "Token refresh failed, attempting full re-authentication (email=%s)",
                mask_email(self.email),
            )
            if not self.password:
                _LOGGER.warning("No password stored, cannot re-authenticate; reauth flow required")
                return False
            try:
                await self._cognito_initial_auth()
                _LOGGER.info("Full re-authentication successful")
                return True
            except FluidraMFARequired:
                _LOGGER.warning("MFA required during token re-authentication, triggering reauth flow")
                return False
            except (FluidraConnectionError, FluidraCircuitBreakerError):
                raise

    async def refresh_access_token(self) -> bool:
        """Renew the access token with the stored refresh token."""
        if not self.refresh_token:
            _LOGGER.debug("No refresh token available, cannot refresh")
            return False

        refresh_payload = {
            "AuthFlow": "REFRESH_TOKEN_AUTH",
            "ClientId": COGNITO_CLIENT_ID,
            "AuthParameters": {"REFRESH_TOKEN": self.refresh_token},
        }

        headers = {
            "Content-Type": "application/x-amz-json-1.1; charset=utf-8",
            "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
        }

        try:
            status, data, raw_text = await self._request(
                "POST",
                COGNITO_ENDPOINT,
                headers=headers,
                json_data=refresh_payload,
                skip_circuit_breaker=True,
                skip_auth_refresh=True,
            )
        except FluidraError as err:
            _LOGGER.warning("Token refresh failed (connection error): %s", err)
            return False

        if status == 200 and isinstance(data, dict):
            auth_result = data.get("AuthenticationResult", {})
            try:
                self._store_tokens(auth_result)
            except FluidraAuthError:
                return False
            return True

        _LOGGER.warning("Token refresh failed with status %d", status)
        _LOGGER.debug("Refresh body: %s", raw_text[:200])
        return False

    async def get_pools(self) -> list[dict[str, Any]]:
        """Return discovered pools with associated devices."""
        if not self.access_token:
            raise FluidraAuthError("Not authenticated")

        pools: list[dict[str, Any]] = []

        if self.user_pools:
            for pool in self.user_pools:
                pool_id = pool.get("id")
                pool_devices = [d for d in self.devices if d.get("pool_id") == pool_id]
                pools.append({"id": pool_id, "name": pool.get("name", f"Pool {pool_id}"), "devices": pool_devices})
        elif self.devices:
            pools.append({"id": "default", "name": "Fluidra Pool", "devices": self.devices})

        self._pools = pools
        return self._pools

    @property
    def cached_pools(self) -> list[dict[str, Any]]:
        """Return cached pools without an API call."""
        return self._pools

    def get_pool_by_id(self, pool_id: str) -> dict[str, Any] | None:
        """Return a specific pool by ID."""
        for pool in self._pools:
            if pool["id"] == pool_id:
                return pool
        return None

    def get_device_by_id(self, device_id: str) -> dict[str, Any] | None:
        """Return a specific device by ID across all pools."""
        for pool in self._pools:
            for device in pool["devices"]:
                if device.get("device_id") == device_id:
                    return device
        return None

    async def poll_device_status(self, pool_id: str, device_id: str) -> dict[str, Any] | None:
        """Poll device state from the Fluidra API."""
        if not self.access_token:
            raise FluidraAuthError("Not authenticated")

        if not await self.ensure_valid_token():
            raise FluidraAuthError("Token refresh failed")

        headers = self._build_auth_headers()
        url = f"{FLUIDRA_EMEA_BASE}/generic/devices"
        params = {"poolId": pool_id, "format": "tree"}

        try:
            status, data, _ = await self._request("GET", url, headers=headers, params=params)
        except FluidraCircuitBreakerError:
            _LOGGER.debug("Circuit breaker open, skipping poll for device %s", mask_device_id(device_id))
            return None
        except FluidraError as err:
            _LOGGER.debug("Poll device status failed: %s", err)
            return None

        if status != 200 or not isinstance(data, list):
            return None

        for device in data:
            if device.get("id") == device_id:
                return device
            children = device.get("devices")
            if isinstance(children, list):
                for child in children:
                    if child.get("id") == device_id:
                        return child
        return None

    async def poll_water_quality(self, pool_id: str) -> dict[str, Any] | None:
        """Poll water quality telemetry for a pool."""
        if not self.access_token:
            raise FluidraAuthError("Not authenticated")

        headers = self._build_auth_headers()
        url = (
            f"{FLUIDRA_EMEA_BASE}/generic/pools/{quote(str(pool_id), safe='')}"
            "/assistant/algorithms/telemetryWaterQuality/jobs"
        )
        params = {"pageSize": 1}

        try:
            status, data, _ = await self._request("GET", url, headers=headers, params=params)
        except FluidraError as err:
            _LOGGER.debug("Poll water quality failed: %s", err)
            return None

        if status == 200 and isinstance(data, dict):
            return data
        return None

    async def get_component_state(self, device_id: str, component_id: int) -> dict[str, Any] | None:
        """Retrieve the current state of a single component."""
        if not self.access_token:
            raise FluidraAuthError("Not authenticated")

        headers = self._build_auth_headers()
        url = f"{FLUIDRA_EMEA_BASE}/generic/devices/{quote(str(device_id), safe='')}/components/{int(component_id)}"
        params = {"deviceType": "connected"}

        try:
            status, data, _ = await self._request("GET", url, headers=headers, params=params)
        except FluidraError as err:
            _LOGGER.debug("Get component state failed: %s", err)
            return None

        if status == 200 and isinstance(data, dict):
            return data
        return None

    async def get_device_component_state(self, device_id: str, component_id: int) -> dict[str, Any] | None:
        """Return the state of a device component (backward-compatible alias)."""
        return await self.get_component_state(device_id, component_id)

    async def control_device_component(
        self, device_id: str, component_id: int, value: int | str | dict[str, Any]
    ) -> bool:
        """Control a device component through the real Fluidra API."""
        if not self.access_token:
            raise FluidraAuthError("Not authenticated")

        if not await self.ensure_valid_token():
            raise FluidraAuthError("Token refresh failed")

        headers = self._build_auth_headers()
        headers["content-type"] = "application/json; charset=utf-8"

        url = (
            f"{FLUIDRA_EMEA_BASE}/generic/devices/{quote(str(device_id), safe='')}"
            f"/components/{int(component_id)}?deviceType=connected"
        )
        payload = {"desiredValue": value}

        try:
            status, data, raw_text = await self._request("PUT", url, headers=headers, json_data=payload)
        except FluidraCircuitBreakerError:
            _LOGGER.warning("Circuit breaker open, cannot control device %s", mask_device_id(device_id))
            return False
        except FluidraError as err:
            _LOGGER.warning("Control device component failed: %s", err)
            return False

        if status == 200:
            if isinstance(data, dict) and isinstance(value, int):
                self._update_device_state_from_response(device_id, component_id, data, value)
            elif isinstance(value, int):
                self._update_device_state_fallback(device_id, component_id, value)
            return True

        _LOGGER.warning(
            "Control component %s on %s failed: HTTP %s",
            component_id,
            mask_device_id(device_id),
            status,
        )
        _LOGGER.debug("Control response body: %s", raw_text[:500])
        return False

    def _update_device_state_from_response(
        self, device_id: str, component_id: int, response_data: dict[str, Any], value: int
    ) -> None:
        """Update local device state from API response."""
        reported_value = response_data.get("reportedValue")
        desired_value = response_data.get("desiredValue")
        component_ts = response_data.get("ts")

        device = self.get_device_by_id(device_id)
        if not device:
            return

        components = device.setdefault("components", {})
        component_key = str(component_id)
        components.setdefault(component_key, {})
        components[component_key]["desiredValue"] = desired_value
        components[component_key]["reportedValue"] = reported_value
        components[component_key]["ts"] = component_ts

        if component_id == COMPONENT_PUMP_ONOFF:
            device["is_running"] = bool(reported_value)
            device["operation_mode"] = reported_value if reported_value is not None else value
            device["desired_state"] = desired_value
            device["last_updated"] = component_ts
        elif component_id == COMPONENT_AUTO_MODE:
            device["auto_mode_enabled"] = bool(reported_value)
            device["auto_mode_desired"] = desired_value
            device["last_updated"] = component_ts

    def _update_device_state_fallback(self, device_id: str, component_id: int, value: int) -> None:
        """Fallback local state update when JSON parsing fails."""
        device = self.get_device_by_id(device_id)
        if not device:
            return

        if component_id == COMPONENT_PUMP_ONOFF:
            device["is_running"] = bool(value)
            device["operation_mode"] = value
            if value > 1:
                device["speed_percent"] = value
            elif value == 1:
                device["speed_percent"] = device.get("speed_percent", 50)
            else:
                device["speed_percent"] = 0
        elif component_id == COMPONENT_AUTO_MODE:
            device["auto_mode_enabled"] = bool(value)

    async def set_heat_pump_temperature(self, device_id: str, temperature: float) -> bool:
        """Set heat pump target temperature on component 15 (setpoint × 10)."""
        temperature_value = int(temperature * 10)
        success = await self.control_device_component(device_id, COMPONENT_HEAT_PUMP_SETPOINT, temperature_value)
        if success:
            device = self.get_device_by_id(device_id)
            if device:
                device["target_temperature"] = temperature
        return success

    def _is_heat_pump(self, device_id: str) -> bool:
        """Return True if the device is a heat pump."""
        device = self.get_device_by_id(device_id)
        if not device:
            return False
        device_config = DeviceIdentifier.identify_device(device)
        return bool(device_config and device_config.device_type == "heat_pump")

    async def start_pump(self, device_id: str) -> bool:
        """Start pump using the correct component based on device type."""
        if self._is_heat_pump(device_id):
            return await self.control_device_component(device_id, COMPONENT_HEAT_PUMP_ONOFF, 1)

        start_success = await self.control_device_component(device_id, COMPONENT_PUMP_ONOFF, 1)

        if start_success:
            await asyncio.sleep(PUMP_START_DELAY)
            await self.control_device_component(device_id, COMPONENT_PUMP_SPEED, 0)
            return True

        return False

    async def stop_pump(self, device_id: str) -> bool:
        """Stop pump using the correct component based on device type."""
        if self._is_heat_pump(device_id):
            return await self.control_device_component(device_id, COMPONENT_HEAT_PUMP_ONOFF, 0)
        return await self.control_device_component(device_id, COMPONENT_PUMP_ONOFF, 0)

    async def set_pump_speed(self, device_id: str, speed_percent: int) -> bool:
        """Set pump speed. ``speed_percent`` snaps to the nearest API level."""
        if not 0 <= speed_percent <= 100:
            return False

        if speed_percent == 0:
            return await self.control_device_component(device_id, COMPONENT_PUMP_ONOFF, 0)

        if speed_percent <= 45:
            speed_level = 0
        elif speed_percent <= 65:
            speed_level = 1
        else:
            speed_level = 2

        return await self.control_device_component(device_id, COMPONENT_PUMP_SPEED, speed_level)

    async def enable_auto_mode(self, device_id: str) -> bool:
        """Enable auto mode."""
        return await self.control_device_component(device_id, COMPONENT_AUTO_MODE, 1)

    async def disable_auto_mode(self, device_id: str) -> bool:
        """Disable auto mode."""
        return await self.control_device_component(device_id, COMPONENT_AUTO_MODE, 0)

    def _convert_schedules_to_dm24049704_format(self, schedules: list[dict[str, Any]]) -> dict:
        """Convert CRON-format schedules to DM24049704 programs/slots format.

        Input format (CRON):
        [{"id": 0, "startTime": "0 5 * * 1,2,3,4,5", "endTime": "0 6 * * 1,2,3,4,5",
          "startActions": {"operationName": "3"}, "enabled": True}]

        Output format (programs/slots):
        {
            "dayPrograms": {"monday": 1, ...},
            "programs": [{"id": 1, "slots": [{"id": 0, "start": 1280, "end": 1536, "mode": 3}]}]
        }

        Time encoding: hours * 256 + minutes.
        """
        cron_day_to_name = {
            1: "monday",
            2: "tuesday",
            3: "wednesday",
            4: "thursday",
            5: "friday",
            6: "saturday",
            7: "sunday",
        }

        all_scheduled_days: set[int] = set()
        slots: list[dict[str, int]] = []
        slot_id = 0

        for sched in schedules:
            if not sched.get("enabled", True):
                continue

            start_cron = sched.get("startTime", "")
            end_cron = sched.get("endTime", "")
            operation = sched.get("startActions", {}).get("operationName", "1")

            start_parts = start_cron.split() if start_cron else []
            end_parts = end_cron.split() if end_cron else []

            if len(start_parts) >= 5 and len(end_parts) >= 2:
                try:
                    start_minute = int(start_parts[0])
                    start_hour = int(start_parts[1])
                    end_minute = int(end_parts[0])
                    end_hour = int(end_parts[1])

                    start_encoded = start_hour * 256 + start_minute
                    end_encoded = end_hour * 256 + end_minute

                    mode = int(operation) if operation else 1

                    slots.append({"id": slot_id, "start": start_encoded, "end": end_encoded, "mode": mode})
                    slot_id += 1

                    days_str = start_parts[4]
                    if days_str != "*":
                        for day in days_str.split(","):
                            try:
                                all_scheduled_days.add(int(day.strip()))
                            except ValueError:
                                continue
                    else:
                        all_scheduled_days.update(range(1, 8))

                except (ValueError, IndexError) as err:
                    _LOGGER.warning("Failed to parse schedule: %s, error: %s", sched, err)
                    continue

        day_programs = {
            day_name: 1 if cron_day in all_scheduled_days else 0 for cron_day, day_name in cron_day_to_name.items()
        }

        return {
            "dayPrograms": day_programs,
            "programs": [{"id": 1, "slots": slots}] if slots else [],
        }

    async def set_schedule(
        self, device_id: str, schedules: list[dict[str, Any]], component_id: int = COMPONENT_SCHEDULE
    ) -> bool:
        """Set device schedule using the mobile-app format."""
        if not self.access_token:
            raise FluidraAuthError("Not authenticated")

        if not await self.ensure_valid_token():
            raise FluidraAuthError("Token refresh failed")

        headers = self._build_auth_headers()
        headers["content-type"] = "application/json; charset=utf-8"

        url = (
            f"{FLUIDRA_EMEA_BASE}/generic/devices/{quote(str(device_id), safe='')}"
            f"/components/{int(component_id)}?deviceType=connected"
        )
        payload = {"desiredValue": schedules}

        try:
            status, _, raw_text = await self._request("PUT", url, headers=headers, json_data=payload)
        except FluidraError as err:
            _LOGGER.error("set_schedule error: %s", err)
            return False

        if status != 200:
            _LOGGER.debug("set_schedule body: %s", raw_text[:500])
        return status == 200

    async def get_default_schedule(self) -> list[dict[str, Any]]:
        """Return a default schedule template."""
        return [
            {
                "id": 1,
                "groupId": 1,
                "enabled": True,
                "startTime": "08 30 * * 1,2,3,4,5,6,7",
                "endTime": "09 59 * * 1,2,3,4,5,6,7",
                "startActions": {"operationName": 1},
            },
        ]

    async def set_component_value(self, device_id: str, component_id: int, value: int) -> bool:
        """Set component value as integer."""
        return await self._set_component_generic(device_id, component_id, value)

    async def set_component_string_value(self, device_id: str, component_id: int, value: str) -> bool:
        """Set component value as string (LumiPlus ON/OFF: "1"/"0")."""
        return await self._set_component_generic(device_id, component_id, value)

    async def set_component_json_value(self, device_id: str, component_id: int, value: dict[str, Any]) -> bool:
        """Set component value as JSON object (LumiPlus RGBW)."""
        return await self._set_component_generic(device_id, component_id, value)

    async def _set_component_generic(
        self, device_id: str, component_id: int, value: int | str | dict[str, Any]
    ) -> bool:
        """Generic component value setter."""
        if not self.access_token:
            raise FluidraAuthError("Not authenticated")

        if not await self.ensure_valid_token():
            raise FluidraAuthError("Token refresh failed")

        headers = self._build_auth_headers()
        headers["content-type"] = "application/json; charset=utf-8"

        url = (
            f"{FLUIDRA_EMEA_BASE}/generic/devices/{quote(str(device_id), safe='')}"
            f"/components/{int(component_id)}?deviceType=connected"
        )
        payload = {"desiredValue": value}

        try:
            status, _, _ = await self._request("PUT", url, headers=headers, json_data=payload)
        except FluidraError as err:
            _LOGGER.debug("Set component value failed: %s", err)
            return False
        return status == 200

    async def clear_schedule(self, device_id: str) -> bool:
        """Clear all schedules for a device."""
        return await self.set_schedule(device_id, [])

    async def get_pool_details(self, pool_id: str) -> dict[str, Any] | None:
        """Fetch pool details and status data."""
        if not self.access_token:
            raise FluidraAuthError("Not authenticated")

        if not await self.ensure_valid_token():
            raise FluidraAuthError("Token refresh failed")

        headers = self._build_auth_headers()
        pool_data: dict[str, Any] = {}

        url = f"{FLUIDRA_EMEA_BASE}/generic/pools/{quote(str(pool_id), safe='')}"
        try:
            status, data, _ = await self._request("GET", url, headers=headers)
            if status == 200 and isinstance(data, dict):
                pool_data.update(data)
        except FluidraError:
            pass

        status_url = f"{FLUIDRA_EMEA_BASE}/generic/pools/{quote(str(pool_id), safe='')}/status"
        try:
            status, data, _ = await self._request("GET", status_url, headers=headers)
            if status == 200 and isinstance(data, dict):
                pool_data["status_data"] = data
        except FluidraError:
            pass

        return pool_data if pool_data else None

    async def get_user_pools(self) -> list[dict[str, Any]] | None:
        """Return the list of pools for the user."""
        if not self.access_token:
            raise FluidraAuthError("Not authenticated")

        if not await self.ensure_valid_token():
            raise FluidraAuthError("Token refresh failed")

        headers = self._build_auth_headers()
        url = f"{FLUIDRA_EMEA_BASE}/generic/users/me/pools"

        try:
            status, data, _ = await self._request("GET", url, headers=headers)
        except FluidraError as err:
            _LOGGER.debug("Get user pools failed: %s", err)
            return None

        if status == 200 and isinstance(data, list):
            return data
        return None

    async def close(self) -> None:
        """Close the API connection, but only if we own the session."""
        if self._session and self._owns_session:
            try:
                await self._session.close()
            except (aiohttp.ClientError, OSError):
                _LOGGER.debug("Failed to close API session")
        self._session = None
        self._owns_session = False


def _parse_json(raw_text: str) -> Any:
    """Parse a response body as JSON; return None when it isn't JSON."""
    if not raw_text:
        return None
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        return None


def _parse_retry_after(response: aiohttp.ClientResponse) -> float | None:
    """Return Retry-After header in seconds, or None if absent/invalid."""
    header = response.headers.get("Retry-After")
    if not header:
        return None
    try:
        return float(header)
    except ValueError:
        return None


def _classify_device_type(family: str, device_name: str) -> str:
    """Classify a Fluidra device into a high-level type from its metadata."""
    family_lower = family.lower()
    device_name_lower = device_name.lower()

    if "pump" in family_lower and any(kw in family_lower for kw in ("heat", "eco", "elyo", "thermal")):
        return "heat_pump"
    if "pump" in family_lower:
        return "pump"
    if any(kw in family_lower for kw in ("heat", "thermal", "eco elyo", "astralpool")):
        return "heat_pump"
    if any(kw in device_name_lower for kw in ("heat", "thermal", "eco", "elyo")):
        return "heat_pump"
    if "chlorinator" in family_lower or "electrolyseur" in family_lower:
        return "chlorinator"
    if "heater" in family_lower:
        return "heater"
    if "light" in family_lower or "lumiplus" in device_name_lower:
        return "light"
    return "unknown"
