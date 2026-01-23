"""
Fluidra Pool API wrapper for Home Assistant integration.

This module provides a simplified interface to the Fluidra Pool library
optimized for Home Assistant usage with real AWS Cognito authentication.

🏆 God Tier API client with:
- Circuit breaker pattern (5 failures → 5 min pause)
- Rate limiting with sliding window
- Retry with exponential backoff
- Structured error handling
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
import json
import logging
import time
from typing import TYPE_CHECKING, Any, Final

import aiohttp

from .const import PUMP_START_DELAY
from .device_registry import DeviceIdentifier

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

# API endpoints discovered through reverse engineering
FLUIDRA_EMEA_BASE: Final = "https://api.fluidra-emea.com"
COGNITO_ENDPOINT: Final = "https://cognito-idp.eu-west-1.amazonaws.com/"
COGNITO_CLIENT_ID: Final = "g3njunelkcbtefosqm9bdhhq1"

# Circuit breaker configuration
CIRCUIT_BREAKER_FAILURES: Final = 5  # Open after 5 failures
CIRCUIT_BREAKER_TIMEOUT: Final = 300  # 5 minutes recovery time

# Rate limiting configuration
RATE_LIMIT_REQUESTS: Final = 30  # Max requests
RATE_LIMIT_WINDOW: Final = 60  # Per 60 seconds

# Retry configuration
MAX_RETRIES: Final = 3
INITIAL_BACKOFF: Final = 1.0  # 1 second
MAX_BACKOFF: Final = 30.0  # 30 seconds
BACKOFF_MULTIPLIER: Final = 2.0

_LOGGER = logging.getLogger(__name__)


class FluidraError(Exception):
    """Base exception for Fluidra errors."""


class FluidraAuthError(FluidraError):
    """Exception for authentication errors."""


class FluidraConnectionError(FluidraError):
    """Exception for connection errors."""


class FluidraRateLimitError(FluidraError):
    """Exception for rate limit errors."""


class FluidraCircuitBreakerError(FluidraError):
    """Exception when circuit breaker is open."""


class CircuitState(Enum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Rejecting requests
    HALF_OPEN = "half_open"  # Testing recovery


@dataclass
class CircuitBreaker:
    """Circuit breaker implementation for API resilience.

    🏆 God Tier: Prevents cascade failures by stopping requests
    when the API is consistently failing.
    """

    failure_threshold: int = CIRCUIT_BREAKER_FAILURES
    recovery_timeout: float = CIRCUIT_BREAKER_TIMEOUT
    state: CircuitState = field(default=CircuitState.CLOSED)
    failure_count: int = field(default=0)
    last_failure_time: float = field(default=0.0)
    success_count: int = field(default=0)

    def record_success(self) -> None:
        """Record a successful request."""
        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= 2:  # 2 successes to fully close
                self.state = CircuitState.CLOSED
                self.failure_count = 0
                self.success_count = 0
                _LOGGER.info("Circuit breaker closed after recovery")
        elif self.state == CircuitState.CLOSED:
            self.failure_count = 0  # Reset on success

    def record_failure(self) -> None:
        """Record a failed request."""
        self.failure_count += 1
        self.last_failure_time = time.monotonic()
        self.success_count = 0

        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.OPEN
            _LOGGER.warning("Circuit breaker re-opened after failed recovery attempt")
        elif self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            _LOGGER.warning(
                "Circuit breaker opened after %d failures",
                self.failure_count,
            )

    def can_execute(self) -> bool:
        """Check if request can be executed."""
        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.OPEN:
            # Check if recovery timeout has elapsed
            if time.monotonic() - self.last_failure_time >= self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                _LOGGER.info("Circuit breaker half-open, testing recovery")
                return True
            return False

        # HALF_OPEN state allows requests
        return True


@dataclass
class RateLimiter:
    """Sliding window rate limiter.

    🏆 God Tier: Prevents API abuse and ensures fair usage.
    """

    max_requests: int = RATE_LIMIT_REQUESTS
    window_seconds: float = RATE_LIMIT_WINDOW
    _timestamps: deque = field(default_factory=deque)

    def can_execute(self) -> bool:
        """Check if request can be executed within rate limits."""
        now = time.monotonic()
        # Remove timestamps outside the window
        while self._timestamps and now - self._timestamps[0] > self.window_seconds:
            self._timestamps.popleft()

        return len(self._timestamps) < self.max_requests

    def record_request(self) -> None:
        """Record a request timestamp."""
        self._timestamps.append(time.monotonic())

    def wait_time(self) -> float:
        """Return time to wait before next request is allowed."""
        if self.can_execute():
            return 0.0
        now = time.monotonic()
        oldest = self._timestamps[0]
        return max(0.0, self.window_seconds - (now - oldest))


class FluidraPoolAPI:
    """Wrapper for Fluidra Pool API for Home Assistant.

    🏆 God Tier API client with:
    - Circuit breaker pattern
    - Rate limiting with sliding window
    - Retry with exponential backoff
    """

    __slots__ = (
        "email",
        "password",
        "_hass",
        "_session",
        "access_token",
        "refresh_token",
        "id_token",
        "token_expires_at",
        "user_pools",
        "devices",
        "_pools",
        "component_mappings",
        "pump_speed_levels",
        "speed_percentages",
        "_circuit_breaker",
        "_rate_limiter",
    )

    def __init__(self, email: str, password: str, hass: HomeAssistant | None = None) -> None:
        """Initialize the API wrapper."""
        self.email: str = email
        self.password: str = password
        self._hass: HomeAssistant | None = hass
        self._session: aiohttp.ClientSession | None = None

        # AWS Cognito tokens
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self.id_token: str | None = None
        self.token_expires_at: int | None = None  # Timestamp d'expiration

        # Account data
        self.user_pools: list[dict[str, Any]] = []
        self.devices: list[dict[str, Any]] = []
        self._pools: list[dict[str, Any]] = []

        # 🏆 God Tier: Circuit breaker and rate limiter
        self._circuit_breaker = CircuitBreaker()
        self._rate_limiter = RateLimiter()

        # Component control mappings discovered via reverse engineering
        self.component_mappings: Final[dict[str, int]] = {
            "pump_speed": 11,  # ComponentToChange: 11 = VITESSE POMPE (3 niveaux)
            "pump": 9,  # ComponentToChange: 9 = POMPE PRINCIPALE (on/off)
            "auto_mode": 10,  # ComponentToChange: 10 = MODE AUTO/AUTRE ÉQUIPEMENT
            "schedule": 20,  # ComponentToChange: 20 = PROGRAMMATION HORAIRE
        }

        # Speed levels discovered (Component 11 pump speed control - corrected)
        self.pump_speed_levels: Final[dict[str, int]] = {
            "low": 0,  # desiredValue: 0 = Faible (45%)
            "medium": 1,  # desiredValue: 1 = Moyenne (65%)
            "high": 2,  # desiredValue: 2 = Élevée (100%)
        }

        # Speed percentage mapping for display (corrected based on real testing)
        self.speed_percentages: Final[dict[int, int]] = {
            0: 45,  # Low speed (Faible)
            1: 65,  # Medium speed (Moyenne)
            2: 100,  # High speed (Élevée)
        }

    async def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json_data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        skip_circuit_breaker: bool = False,
    ) -> aiohttp.ClientResponse:
        """Execute HTTP request with circuit breaker, rate limiting, and retry.

        🏆 God Tier: Centralized request handling with full resilience patterns.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            url: Request URL
            headers: Optional headers
            json_data: Optional JSON body
            params: Optional query parameters
            skip_circuit_breaker: Skip circuit breaker (for auth requests)

        Returns:
            aiohttp.ClientResponse

        Raises:
            FluidraCircuitBreakerError: If circuit breaker is open
            FluidraRateLimitError: If rate limited
            FluidraConnectionError: If connection fails after retries
        """
        # Check circuit breaker
        if not skip_circuit_breaker and not self._circuit_breaker.can_execute():
            raise FluidraCircuitBreakerError(f"Circuit breaker open, retry after {CIRCUIT_BREAKER_TIMEOUT}s")

        # Check rate limiter
        if not self._rate_limiter.can_execute():
            wait_time = self._rate_limiter.wait_time()
            _LOGGER.debug("Rate limited, waiting %.1fs", wait_time)
            await asyncio.sleep(wait_time)

        # Record request for rate limiting
        self._rate_limiter.record_request()

        # Ensure session exists
        if self._session is None:
            if self._hass:
                from homeassistant.helpers.aiohttp_client import async_get_clientsession

                self._session = async_get_clientsession(self._hass)
            else:
                timeout = aiohttp.ClientTimeout(total=30)
                self._session = aiohttp.ClientSession(timeout=timeout)

        # Retry with exponential backoff
        last_error: Exception | None = None
        backoff = INITIAL_BACKOFF

        for attempt in range(MAX_RETRIES + 1):
            try:
                if method.upper() == "GET":
                    response = await self._session.get(url, headers=headers, params=params)
                elif method.upper() == "POST":
                    response = await self._session.post(url, headers=headers, json=json_data)
                elif method.upper() == "PUT":
                    response = await self._session.put(url, headers=headers, json=json_data)
                else:
                    raise ValueError(f"Unsupported HTTP method: {method}")

                # Record success for circuit breaker
                if not skip_circuit_breaker:
                    self._circuit_breaker.record_success()

                return response

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
        """Authentification réelle via AWS Cognito."""
        try:
            # Étape 1: Authentification initiale AWS Cognito
            await self._cognito_initial_auth()

            # Étape 2: Récupérer les informations du compte
            await self._get_user_profile()

            # Étape 3: Découvrir les piscines et équipements
            await self.async_update_data()

        except FluidraError:
            raise
        except Exception as e:
            raise FluidraAuthError(f"Authentication failed: {e}") from e

    async def _cognito_initial_auth(self) -> None:
        """Authentification initiale AWS Cognito."""
        auth_payload = {
            "AuthFlow": "USER_PASSWORD_AUTH",
            "ClientId": COGNITO_CLIENT_ID,
            "AuthParameters": {"USERNAME": self.email, "PASSWORD": self.password},
        }

        headers = {
            "Content-Type": "application/x-amz-json-1.1; charset=utf-8",
            "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
            "User-Agent": "com.fluidra.iaqualinkplus/1741857021 (Linux; U; Android 14; fr_FR; MI PAD 4; Build/UQ1A.240205.004; Cronet/140.0.7289.0)",
        }

        # Skip circuit breaker for auth (must always try)
        response = await self._request(
            "POST",
            COGNITO_ENDPOINT,
            headers=headers,
            json_data=auth_payload,
            skip_circuit_breaker=True,
        )

        if response.status != 200:
            error_text = await response.text()
            raise FluidraAuthError(f"Cognito auth failed: {response.status} - {error_text}")

        # AWS Cognito renvoie application/x-amz-json-1.1, il faut forcer le décodage
        response_text = await response.text()

        try:
            auth_data = json.loads(response_text)
        except json.JSONDecodeError as e:
            raise FluidraAuthError(f"Invalid JSON response: {e}") from e

        auth_result = auth_data.get("AuthenticationResult", {})

        self.access_token = auth_result.get("AccessToken")
        self.refresh_token = auth_result.get("RefreshToken")
        self.id_token = auth_result.get("IdToken")

        # Calculer l'expiration du token (AWS Cognito = 1 heure par défaut)
        expires_in = auth_result.get("ExpiresIn", 3600)  # 1 heure par défaut
        self.token_expires_at = int(time.time()) + expires_in - 300  # Renouveler 5 min avant expiration

        if not self.access_token:
            raise FluidraAuthError("Access token non reçu")

    async def _get_user_profile(self) -> dict[str, Any]:
        """Récupérer le profil utilisateur."""
        headers = self._build_auth_headers()
        profile_url = f"{FLUIDRA_EMEA_BASE}/mobile/consumers/me"

        try:
            response = await self._request("GET", profile_url, headers=headers)
            if response.status == 200:
                return await response.json()
        except FluidraError:
            _LOGGER.debug("Failed to get user profile, continuing anyway")
        return {}

    def _build_auth_headers(self) -> dict[str, str]:
        """Build standard authenticated headers."""
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "com.fluidra.iaqualinkplus/1741857021 (Linux; U; Android 14; fr_FR; MI PAD 4; Build/UQ1A.240205.004; Cronet/140.0.7289.0)",
        }

    async def async_update_data(self) -> None:
        """Discover pools and devices for the account and update their state."""
        self.devices = []  # Clear devices before updating
        headers = self._build_auth_headers()

        # Découvrir les piscines
        pools_url = f"{FLUIDRA_EMEA_BASE}/generic/users/me/pools"

        try:
            response = await self._request("GET", pools_url, headers=headers)
            if response.status == 200:
                pools_data = await response.json()

                # Handle both formats: direct list or dict with "pools" key
                if isinstance(pools_data, list):
                    self.user_pools = pools_data
                else:
                    self.user_pools = pools_data.get("pools", [])

                # Pour chaque piscine, découvrir les équipements
                for pool in self.user_pools:
                    pool_id = pool.get("id")
                    if pool_id:
                        await self._discover_devices_for_pool(pool_id, headers)
        except FluidraError as err:
            _LOGGER.warning("Failed to update data: %s", err)

    async def _discover_devices_for_pool(self, pool_id: str, headers: dict[str, str]) -> None:
        """Découvrir les équipements pour une piscine donnée."""
        devices_url = f"{FLUIDRA_EMEA_BASE}/generic/devices"
        params = {"poolId": pool_id, "format": "tree"}

        try:
            response = await self._request("GET", devices_url, headers=headers, params=params)
            if response.status != 200:
                return

            devices_data = await response.json()
        except FluidraError as err:
            _LOGGER.warning("Failed to discover devices for pool %s: %s", pool_id, err)
            return

        # Handle both formats: direct list or dict with "devices" key
        if isinstance(devices_data, list):
            pool_devices = devices_data
        else:
            pool_devices = devices_data.get("devices", [])

        for device in pool_devices:
            # Extract real device info from API structure
            device_id = device.get("id")
            info = device.get("info", {})
            device_name = info.get("name", f"Device {device_id}")
            family = info.get("family", "")
            connection_type = device.get("type", "unknown")

            # Determine device type from family - Enhanced for heat pumps
            family_lower = family.lower()
            device_name_lower = device_name.lower()

            if "pump" in family_lower and (
                "heat" in family_lower or "eco" in family_lower or "elyo" in family_lower or "thermal" in family_lower
            ):
                device_type = "heat_pump"
            elif "pump" in family_lower:
                device_type = "pump"
            elif any(keyword in family_lower for keyword in ["heat", "thermal", "eco elyo", "astralpool"]) or any(
                keyword in device_name_lower for keyword in ["heat", "thermal", "eco", "elyo"]
            ):
                device_type = "heat_pump"
            elif "heater" in family_lower:
                device_type = "heater"
            elif "light" in family_lower or "lumiplus" in device_name_lower:
                device_type = "light"
            else:
                device_type = "unknown"

            # Skip bridges - they are not controllable devices, only their children are
            is_bridge = "bridge" in family.lower() or "devices" in device

            if is_bridge:
                # Handle bridged devices (e.g., chlorinator under bridge)
                if "devices" in device and isinstance(device["devices"], list):
                    for child_device in device["devices"]:
                        child_device_id = child_device.get("id")
                        child_info = child_device.get("info", {})
                        child_device_name = child_info.get("name", f"Device {child_device_id}")
                        child_family = child_info.get("family", "")
                        child_connection_type = child_device.get("type", "unknown")

                        # Determine child device type
                        child_family_lower = child_family.lower()
                        if "chlorinator" in child_family_lower or "electrolyseur" in child_family_lower:
                            child_device_type = "chlorinator"
                        elif "pump" in child_family_lower:
                            child_device_type = "pump"
                        else:
                            child_device_type = "unknown"

                        child_device_info = {
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
                            "parent_id": device_id,  # Link to parent bridge
                        }
                        self.devices.append(child_device_info)
                continue  # Skip adding the bridge itself

            # Don't fetch initial states during discovery - let first polling do it
            # This speeds up Home Assistant startup significantly
            device_info = {
                "pool_id": pool_id,
                "device_id": device_id,
                "name": device_name,
                "type": device_type,
                "family": family,
                "connection_type": connection_type,
                "model": device_name,  # Use device name as model
                "manufacturer": "Fluidra",
                "online": connection_type == "connected",
                "is_running": False,
                "auto_mode_enabled": False,
                "operation_mode": 0,
                "speed_percent": 0,
                "variable_speed": True,
                "pump_type": "variable_speed",
            }
            self.devices.append(device_info)

    def is_token_expired(self) -> bool:
        """Vérifier si le token va expirer bientôt."""
        if not self.token_expires_at:
            return True  # Pas d'info d'expiration, considérer comme expiré
        return int(time.time()) >= self.token_expires_at

    async def ensure_valid_token(self) -> bool:
        """S'assurer que le token est valide, le renouveler si nécessaire."""
        if self.is_token_expired():
            return await self.refresh_access_token()
        return True

    async def refresh_access_token(self) -> bool:
        """Renouveler l'access token avec le refresh token."""
        if not self.refresh_token:
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
            # Skip circuit breaker for token refresh (must always try)
            response = await self._request(
                "POST",
                COGNITO_ENDPOINT,
                headers=headers,
                json_data=refresh_payload,
                skip_circuit_breaker=True,
            )

            if response.status == 200:
                # AWS Cognito renvoie application/x-amz-json-1.1, il faut forcer le décodage
                response_text = await response.text()
                auth_data = json.loads(response_text)
                auth_result = auth_data.get("AuthenticationResult", {})

                self.access_token = auth_result.get("AccessToken")
                new_refresh = auth_result.get("RefreshToken")
                if new_refresh:
                    self.refresh_token = new_refresh

                # Mettre à jour l'expiration
                expires_in = auth_result.get("ExpiresIn", 3600)
                self.token_expires_at = int(time.time()) + expires_in - 300

                return True
            return False
        except FluidraError as err:
            _LOGGER.warning("Token refresh failed: %s", err)
            return False

    async def get_pools(self) -> list[dict[str, Any]]:
        """Retourner les piscines découvertes lors de l'authentification."""
        if not self.access_token:
            raise FluidraAuthError("Not authenticated")

        # Convertir les données découvertes en format Home Assistant
        pools = []

        if self.user_pools:
            for pool in self.user_pools:
                pool_id = pool.get("id")
                pool_devices = [device for device in self.devices if device.get("pool_id") == pool_id]

                pool_data = {"id": pool_id, "name": pool.get("name", f"Pool {pool_id}"), "devices": pool_devices}
                pools.append(pool_data)

        elif self.devices:
            # Si pas de pools mais des devices, créer un pool par défaut
            default_pool = {"id": "default", "name": "Fluidra Pool", "devices": self.devices}
            pools.append(default_pool)

        if not pools:
            # Fallback: créer un pool de test si aucune donnée découverte
            test_pool = {
                "id": "test_pool",
                "name": "Test Pool",
                "devices": [
                    {
                        "device_id": "test_device",
                        "name": "E30iQ Pool Pump",
                        "type": "pump",
                        "model": "E30iQ",
                        "manufacturer": "Fluidra",
                        "online": True,
                        "is_running": False,
                        "auto_mode_enabled": False,
                        "operation_mode": 0,
                        "speed_percent": 50,
                        "variable_speed": True,
                        "pump_type": "variable_speed",
                    }
                ],
            }
            pools.append(test_pool)

        self._pools = pools
        return self._pools

    @property
    def cached_pools(self) -> list[dict[str, Any]]:
        """Get cached pools without API call (public accessor for _pools)."""
        return self._pools

    def get_pool_by_id(self, pool_id: str) -> dict[str, Any] | None:
        """Get a specific pool by ID."""
        for pool in self._pools:
            if pool["id"] == pool_id:
                return pool
        return None

    def get_device_by_id(self, device_id: str) -> dict[str, Any] | None:
        """Get a specific device by ID across all pools."""
        for pool in self._pools:
            for device in pool["devices"]:
                if device.get("device_id") == device_id:
                    return device
        return None

    async def poll_device_status(self, pool_id: str, device_id: str) -> dict[str, Any] | None:
        """Polling principal de l'état des équipements.

        🏆 God Tier: Uses circuit breaker, rate limiting, and retry.
        """
        if not self.access_token:
            raise FluidraAuthError("Not authenticated")

        # Vérification proactive du token
        if not await self.ensure_valid_token():
            raise FluidraAuthError("Token refresh failed")

        headers = self._build_auth_headers()
        url = f"{FLUIDRA_EMEA_BASE}/generic/devices"
        params = {"poolId": pool_id, "format": "tree"}

        try:
            response = await self._request("GET", url, headers=headers, params=params)

            if response.status == 200:
                devices = await response.json()

                # Recherche du device dans la réponse (y compris les périphériques bridgés)
                for device in devices:
                    if device.get("id") == device_id:
                        return device

                    # Check bridged devices (e.g., chlorinator under bridge)
                    if "devices" in device and isinstance(device["devices"], list):
                        for child_device in device["devices"]:
                            if child_device.get("id") == device_id:
                                return child_device

                return None

            if response.status == 403:
                # Token expiré, essayer de le rafraîchir
                if await self.refresh_access_token():
                    return await self.poll_device_status(pool_id, device_id)
                raise FluidraAuthError("Token refresh failed")
            return None

        except FluidraCircuitBreakerError:
            _LOGGER.debug("Circuit breaker open, skipping poll for device %s", device_id)
            return None
        except FluidraError as err:
            _LOGGER.debug("Poll device status failed: %s", err)
            return None

    async def poll_water_quality(self, pool_id: str) -> dict[str, Any] | None:
        """Polling télémétrie qualité de l'eau.

        🏆 God Tier: Uses circuit breaker, rate limiting, and retry.
        """
        if not self.access_token:
            raise FluidraAuthError("Not authenticated")

        headers = self._build_auth_headers()
        url = f"{FLUIDRA_EMEA_BASE}/generic/pools/{pool_id}/assistant/algorithms/telemetryWaterQuality/jobs"
        params = {"pageSize": 1}

        try:
            response = await self._request("GET", url, headers=headers, params=params)

            if response.status == 200:
                return await response.json()
            if response.status == 403:
                if await self.refresh_access_token():
                    return await self.poll_water_quality(pool_id)
                raise FluidraAuthError("Token refresh failed")
            return None

        except FluidraError as err:
            _LOGGER.debug("Poll water quality failed: %s", err)
            return None

    async def get_component_state(self, device_id: str, component_id: int) -> dict[str, Any] | None:
        """Récupère l'état d'un component spécifique.

        🏆 God Tier: Uses circuit breaker, rate limiting, and retry.
        """
        if not self.access_token:
            raise FluidraAuthError("Not authenticated")

        headers = self._build_auth_headers()
        url = f"{FLUIDRA_EMEA_BASE}/generic/devices/{device_id}/components/{component_id}"
        params = {"deviceType": "connected"}

        try:
            response = await self._request("GET", url, headers=headers, params=params)

            if response.status == 200:
                return await response.json()
            if response.status == 403:
                if await self.refresh_access_token():
                    return await self.get_component_state(device_id, component_id)
                raise FluidraAuthError("Token refresh failed")
            return None
        except FluidraError as err:
            _LOGGER.debug("Get component state failed: %s", err)
            return None

    async def get_device_component_state(self, device_id: str, component_id: int) -> dict[str, Any] | None:
        """Get the state of a device component.

        🏆 God Tier: Uses circuit breaker, rate limiting, and retry.
        """
        if not self.access_token:
            raise FluidraAuthError("Not authenticated")

        headers = self._build_auth_headers()
        url = f"{FLUIDRA_EMEA_BASE}/generic/devices/{device_id}/components/{component_id}"
        params = {"deviceType": "connected"}

        try:
            response = await self._request("GET", url, headers=headers, params=params)

            if response.status == 200:
                return await response.json()
            return None
        except FluidraError as err:
            _LOGGER.debug("Get device component state failed: %s", err)
            return None

    async def control_device_component(self, device_id: str, component_id: int, value: int) -> bool:
        """Control device component using real authentication.

        🏆 God Tier: Uses circuit breaker, rate limiting, and retry.
        """
        if not self.access_token:
            raise FluidraAuthError("Not authenticated")

        # Vérification proactive du token
        if not await self.ensure_valid_token():
            raise FluidraAuthError("Token refresh failed")

        headers = self._build_auth_headers()
        headers["content-type"] = "application/json; charset=utf-8"

        url = f"{FLUIDRA_EMEA_BASE}/generic/devices/{device_id}/components/{component_id}?deviceType=connected"
        payload = {"desiredValue": value}

        try:
            response = await self._request(
                "PUT",
                url,
                headers=headers,
                json_data=payload,
            )

            if response.status == 200:
                # Parse response for reportedValue/desiredValue
                try:
                    response_data = await response.json()
                    self._update_device_state_from_response(device_id, component_id, response_data, value)
                except json.JSONDecodeError:
                    # Fallback: mise à jour locale simple
                    self._update_device_state_fallback(device_id, component_id, value)

                return True

            if response.status == 401:
                # Token expiré, essayer de le renouveler et retry
                if await self.refresh_access_token():
                    return await self.control_device_component(device_id, component_id, value)
                return False

            return False

        except FluidraCircuitBreakerError:
            _LOGGER.warning("Circuit breaker open, cannot control device %s", device_id)
            return False
        except FluidraError as err:
            _LOGGER.warning("Control device component failed: %s", err)
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

        # Update components
        if "components" not in device:
            device["components"] = {}
        if str(component_id) not in device["components"]:
            device["components"][str(component_id)] = {}

        device["components"][str(component_id)]["desiredValue"] = desired_value
        device["components"][str(component_id)]["reportedValue"] = reported_value
        device["components"][str(component_id)]["ts"] = component_ts

        # Update legacy fields for backward compatibility
        if component_id == 9:  # Pump control
            device["is_running"] = bool(reported_value)
            device["operation_mode"] = reported_value or value
            device["desired_state"] = desired_value
            device["last_updated"] = component_ts
        elif component_id == 10:  # Auto mode
            device["auto_mode_enabled"] = bool(reported_value)
            device["auto_mode_desired"] = desired_value
            device["last_updated"] = component_ts

    def _update_device_state_fallback(self, device_id: str, component_id: int, value: int) -> None:
        """Fallback local state update when JSON parsing fails."""
        device = self.get_device_by_id(device_id)
        if not device:
            return

        if component_id == 9:  # Pump control
            device["is_running"] = bool(value)
            device["operation_mode"] = value
            if value > 1:
                device["speed_percent"] = value
            elif value == 1:
                device["speed_percent"] = device.get("speed_percent", 50)
            else:
                device["speed_percent"] = 0
        elif component_id == 10:  # Auto mode
            device["auto_mode_enabled"] = bool(value)

    async def set_heat_pump_temperature(self, device_id: str, temperature: float) -> bool:
        """Set heat pump target temperature using API control."""
        try:
            # Pour les pompes à chaleur, utiliser component 15 (température × 10)
            # Basé sur l'observation: Component 15 reporte 380 pour 38°C, 400 pour 40°C
            component_id = 15

            # Convertir la température en valeur × 10 pour l'API
            temperature_value = int(temperature * 10)

            success = await self.control_device_component(device_id, component_id, temperature_value)
            if success:
                # Mettre à jour l'état local
                device = self.get_device_by_id(device_id)
                if device:
                    device["target_temperature"] = temperature
                return True
            # Fallback: essayer d'autres composants possibles
            for fallback_component in [12, 13, 14, 16]:
                success = await self.control_device_component(device_id, fallback_component, temperature_value)
                if success:
                    device = self.get_device_by_id(device_id)
                    if device:
                        device["target_temperature"] = temperature
                    return True

            return False

        except Exception:
            return False

    def _is_heat_pump(self, device_id: str) -> bool:
        """Check if device is a heat pump (LG Eco Elyo or Z250iQ)."""
        device = self.get_device_by_id(device_id)
        if not device:
            return False

        device_config = DeviceIdentifier.identify_device(device)
        return device_config and device_config.device_type == "heat_pump"

    async def start_pump(self, device_id: str) -> bool:
        """Start pump using appropriate component based on device type."""
        # Heat pumps (LG Eco Elyo, Z250iQ) use component 13 for ON/OFF
        if self._is_heat_pump(device_id):
            return await self.control_device_component(device_id, 13, 1)

        # Standard pumps use component 9
        start_success = await self.control_device_component(device_id, 9, 1)

        if start_success:
            # Attendre un peu que la pompe démarre
            await asyncio.sleep(PUMP_START_DELAY)

            # Définir vitesse par défaut (Faible = niveau 0)
            await self.control_device_component(device_id, 11, 0)

            return True

        return False

    async def stop_pump(self, device_id: str) -> bool:
        """Stop pump using appropriate component based on device type."""
        # Heat pumps (LG Eco Elyo, Z250iQ) use component 13 for ON/OFF
        if self._is_heat_pump(device_id):
            return await self.control_device_component(device_id, 13, 0)

        # Standard pumps use component 9
        return await self.control_device_component(device_id, 9, 0)

    async def set_pump_speed(self, device_id: str, speed_percent: int) -> bool:
        """Set pump speed using the real component 11 speed control.

        Args:
            device_id: Device ID (ex: LE24500883)
            speed_percent: Speed percentage (0, 45, 65, or 100)
        """
        if not 0 <= speed_percent <= 100:
            return False

        # Map percentage to API speed level (component 11 - corrected mapping)
        if speed_percent == 0:
            # For stop, we might need to use component 9 or just return False
            return await self.control_device_component(device_id, 9, 0)  # Use component 9 for stop
        if speed_percent <= 45:
            speed_level = 0  # Low (45%)
        elif speed_percent <= 65:
            speed_level = 1  # Medium (65%)
        else:  # > 65%
            speed_level = 2  # High (100%)

        # Update local device state
        device = self.get_device_by_id(device_id)
        if device:
            device["speed_percent"] = self.speed_percentages.get(speed_level, speed_percent)
            device["is_running"] = bool(speed_level)
            device["operation_mode"] = speed_level

        # Use component 11 for speed control
        return await self.control_device_component(device_id, 11, speed_level)

    async def enable_auto_mode(self, device_id: str) -> bool:
        """Enable auto mode using discovered component ID 10."""
        return await self.control_device_component(device_id, 10, 1)

    async def disable_auto_mode(self, device_id: str) -> bool:
        """Disable auto mode using discovered component ID 10."""
        return await self.control_device_component(device_id, 10, 0)

    def _convert_schedules_to_dm24049704_format(self, schedules: list[dict[str, Any]]) -> dict:
        """Convert CRON-format schedules to DM24049704 programs/slots format.

        Input format (CRON):
        [{"id": 0, "startTime": "0 5 * * 1,2,3,4,5", "endTime": "0 6 * * 1,2,3,4,5",
          "startActions": {"operationName": "3"}, "enabled": True}]

        Output format (programs/slots):
        {
            "dayPrograms": {"monday": 1, "tuesday": 1, ..., "saturday": 0, "sunday": 0},
            "programs": [{"id": 1, "slots": [{"id": 0, "start": 1280, "end": 1536, "mode": 3}]}]
        }

        Time encoding: hours * 256 + minutes
        """
        # Map CRON day numbers to day names
        cron_day_to_name = {
            1: "monday",
            2: "tuesday",
            3: "wednesday",
            4: "thursday",
            5: "friday",
            6: "saturday",
            7: "sunday",
        }

        # Collect all days that have schedules
        all_scheduled_days = set()
        slots = []
        slot_id = 0

        for sched in schedules:
            if not sched.get("enabled", True):
                continue

            start_cron = sched.get("startTime", "")
            end_cron = sched.get("endTime", "")
            operation = sched.get("startActions", {}).get("operationName", "1")

            # Parse CRON times
            start_parts = start_cron.split() if start_cron else []
            end_parts = end_cron.split() if end_cron else []

            if len(start_parts) >= 5 and len(end_parts) >= 2:
                try:
                    start_minute = int(start_parts[0])
                    start_hour = int(start_parts[1])
                    end_minute = int(end_parts[0])
                    end_hour = int(end_parts[1])

                    # Encode times as hours * 256 + minutes
                    start_encoded = start_hour * 256 + start_minute
                    end_encoded = end_hour * 256 + end_minute

                    # Parse mode
                    mode = int(operation) if operation else 1

                    slots.append({"id": slot_id, "start": start_encoded, "end": end_encoded, "mode": mode})
                    slot_id += 1

                    # Collect days
                    days_str = start_parts[4]
                    if days_str != "*":
                        for day in days_str.split(","):
                            try:
                                all_scheduled_days.add(int(day.strip()))
                            except ValueError:
                                pass
                    else:
                        all_scheduled_days.update(range(1, 8))

                except (ValueError, IndexError) as e:
                    _LOGGER.warning("Failed to parse schedule: %s, error: %s", sched, e)
                    continue

        # Build dayPrograms: days with schedules -> program 1, others -> 0
        day_programs = {}
        for cron_day, day_name in cron_day_to_name.items():
            day_programs[day_name] = 1 if cron_day in all_scheduled_days else 0

        # Build the final format
        result = {"dayPrograms": day_programs, "programs": [{"id": 1, "slots": slots}] if slots else []}

        _LOGGER.debug("Converted schedules to DM24049704 format: %s -> %s", schedules, result)
        return result

    async def set_schedule(self, device_id: str, schedules: list[dict[str, Any]], component_id: int = 20) -> bool:
        """Set device schedule using exact format from mobile app.

        🏆 God Tier: Uses circuit breaker, rate limiting, and retry.

        Args:
            device_id: The device ID
            schedules: List of schedule dictionaries
            component_id: Component ID for schedules (20 for pumps, 40 for lights, 258 for DM24049704)
        """
        if not self.access_token:
            raise FluidraAuthError("Not authenticated")

        # Ensure valid token
        if not await self.ensure_valid_token():
            raise FluidraAuthError("Token refresh failed")

        headers = self._build_auth_headers()
        headers["content-type"] = "application/json; charset=utf-8"

        url = f"{FLUIDRA_EMEA_BASE}/generic/devices/{device_id}/components/{component_id}?deviceType=connected"
        payload = {"desiredValue": schedules}

        _LOGGER.debug("set_schedule: device=%s component=%s payload=%s", device_id, component_id, payload)

        try:
            response = await self._request("PUT", url, headers=headers, json_data=payload)
            response_text = await response.text()
            _LOGGER.debug(
                "set_schedule response: status=%s body=%s",
                response.status,
                response_text[:500] if response_text else "",
            )
            return response.status == 200

        except FluidraError as err:
            _LOGGER.error("set_schedule error: %s", err)
            return False

    async def get_default_schedule(self) -> list[dict[str, Any]]:
        """Get a default schedule template based on captured data."""
        return [
            {
                "id": 1,
                "groupId": 1,
                "enabled": True,
                "startTime": "08 30 * * 1,2,3,4,5,6,7",  # 8h30 tous les jours
                "endTime": "09 59 * * 1,2,3,4,5,6,7",  # 9h59 tous les jours
                "startActions": {"operationName": 1},  # Run mode
            },
        ]

    async def set_component_value(self, device_id: str, component_id: int, value: int) -> bool:
        """Set component value using exact format from mobile app.

        🏆 God Tier: Uses circuit breaker, rate limiting, and retry.
        """
        return await self._set_component_generic(device_id, component_id, value)

    async def set_component_string_value(self, device_id: str, component_id: int, value: str) -> bool:
        """Set component value as string (for LumiPlus ON/OFF: "1"/"0").

        🏆 God Tier: Uses circuit breaker, rate limiting, and retry.
        """
        return await self._set_component_generic(device_id, component_id, value)

    async def set_component_json_value(self, device_id: str, component_id: int, value: dict[str, Any]) -> bool:
        """Set component value as JSON object (for LumiPlus RGBW color).

        🏆 God Tier: Uses circuit breaker, rate limiting, and retry.
        """
        return await self._set_component_generic(device_id, component_id, value)

    async def _set_component_generic(
        self, device_id: str, component_id: int, value: int | str | dict[str, Any]
    ) -> bool:
        """Generic component value setter with full resilience patterns.

        🏆 God Tier: Centralized component control with circuit breaker,
        rate limiting, and retry.
        """
        if not self.access_token:
            raise FluidraAuthError("Not authenticated")

        if not await self.ensure_valid_token():
            raise FluidraAuthError("Token refresh failed")

        headers = self._build_auth_headers()
        headers["content-type"] = "application/json; charset=utf-8"

        url = f"{FLUIDRA_EMEA_BASE}/generic/devices/{device_id}/components/{component_id}?deviceType=connected"
        payload = {"desiredValue": value}

        try:
            response = await self._request("PUT", url, headers=headers, json_data=payload)
            return response.status == 200
        except FluidraError as err:
            _LOGGER.debug("Set component value failed: %s", err)
            return False

    async def clear_schedule(self, device_id: str) -> bool:
        """Clear all schedules for device."""
        return await self.set_schedule(device_id, [])

    async def get_pool_details(self, pool_id: str) -> dict[str, Any] | None:
        """Récupérer les détails spécifiques de la piscine.

        🏆 God Tier: Uses circuit breaker, rate limiting, and retry.
        """
        if not self.access_token:
            raise FluidraAuthError("Not authenticated")

        if not await self.ensure_valid_token():
            raise FluidraAuthError("Token refresh failed")

        headers = self._build_auth_headers()
        pool_data: dict[str, Any] = {}

        # Récupérer les détails généraux de la piscine
        url = f"{FLUIDRA_EMEA_BASE}/generic/pools/{pool_id}"
        try:
            response = await self._request("GET", url, headers=headers)
            if response.status == 200:
                pool_details = await response.json()
                pool_data.update(pool_details)
            elif response.status == 403:
                if await self.refresh_access_token():
                    return await self.get_pool_details(pool_id)
                raise FluidraAuthError("Token refresh failed")
        except FluidraError:
            pass

        # Récupérer les données de statut (météo, etc.)
        status_url = f"{FLUIDRA_EMEA_BASE}/generic/pools/{pool_id}/status"
        try:
            response = await self._request("GET", status_url, headers=headers)
            if response.status == 200:
                status_data = await response.json()
                pool_data["status_data"] = status_data
        except FluidraError:
            pass

        return pool_data if pool_data else None

    async def get_user_pools(self) -> list[dict[str, Any]] | None:
        """Récupérer la liste des piscines de l'utilisateur.

        🏆 God Tier: Uses circuit breaker, rate limiting, and retry.
        """
        if not self.access_token:
            raise FluidraAuthError("Not authenticated")

        if not await self.ensure_valid_token():
            raise FluidraAuthError("Token refresh failed")

        headers = self._build_auth_headers()
        url = f"{FLUIDRA_EMEA_BASE}/generic/users/me/pools"

        try:
            response = await self._request("GET", url, headers=headers)
            if response.status == 200:
                return await response.json()
            if response.status == 403:
                if await self.refresh_access_token():
                    return await self.get_user_pools()
                raise FluidraAuthError("Token refresh failed")
            return None
        except FluidraError as err:
            _LOGGER.debug("Get user pools failed: %s", err)
            return None

    async def close(self) -> None:
        """Close the API connection."""
        if self._session:
            try:
                await self._session.close()
            except Exception:
                pass
            finally:
                self._session = None
