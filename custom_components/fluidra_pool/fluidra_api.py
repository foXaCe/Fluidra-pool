"""
Fluidra Pool API wrapper for Home Assistant integration.

This module provides a simplified interface to the Fluidra Pool library
optimized for Home Assistant usage with real AWS Cognito authentication.
"""

import logging
import aiohttp
import asyncio
import json
from typing import Dict, List, Any, Optional

from .device_registry import DeviceIdentifier

# API endpoints discovered through reverse engineering
FLUIDRA_EMEA_BASE = "https://api.fluidra-emea.com"
COGNITO_ENDPOINT = "https://cognito-idp.eu-west-1.amazonaws.com/"
COGNITO_CLIENT_ID = "g3njunelkcbtefosqm9bdhhq1"

_LOGGER = logging.getLogger(__name__)


class FluidraError(Exception):
    """Base exception for Fluidra errors."""


class FluidraAuthError(FluidraError):
    """Exception for authentication errors."""


class FluidraConnectionError(FluidraError):
    """Exception for connection errors."""


class FluidraPoolAPI:
    """Wrapper for Fluidra Pool API for Home Assistant."""

    def __init__(self, email: str, password: str):
        """Initialize the API wrapper."""
        self.email = email
        self.password = password
        self._session: Optional[aiohttp.ClientSession] = None

        # AWS Cognito tokens
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.id_token: Optional[str] = None
        self.token_expires_at: Optional[int] = None  # Timestamp d'expiration

        # Account data
        self.user_pools: List[Dict[str, Any]] = []
        self.devices: List[Dict[str, Any]] = []
        self._pools: List[Dict[str, Any]] = []

        # Component control mappings discovered via reverse engineering
        self.component_mappings = {
            "pump_speed": 11, # ComponentToChange: 11 = VITESSE POMPE (3 niveaux)
            "pump": 9,        # ComponentToChange: 9 = POMPE PRINCIPALE (on/off)
            "auto_mode": 10,  # ComponentToChange: 10 = MODE AUTO/AUTRE ÉQUIPEMENT
            "schedule": 20    # ComponentToChange: 20 = PROGRAMMATION HORAIRE
        }

        # Speed levels discovered (Component 11 pump speed control - corrected)
        self.pump_speed_levels = {
            "low": 0,      # desiredValue: 0 = Faible (45%)
            "medium": 1,   # desiredValue: 1 = Moyenne (65%)
            "high": 2      # desiredValue: 2 = Élevée (100%)
        }

        # Speed percentage mapping for display (corrected based on real testing)
        self.speed_percentages = {
            0: 45,   # Low speed (Faible)
            1: 65,   # Medium speed (Moyenne)
            2: 100   # High speed (Élevée)
        }

    async def authenticate(self) -> None:
        """Authentification réelle via AWS Cognito."""
        if self._session is None:
            self._session = aiohttp.ClientSession()

        try:
            # Étape 1: Authentification initiale AWS Cognito
            await self._cognito_initial_auth()

            # Étape 2: Récupérer les informations du compte
            await self._get_user_profile()

            # Étape 3: Découvrir les piscines et équipements
            await self.async_update_data()

        except Exception as e:
            raise FluidraAuthError(f"Authentication failed: {e}")

    async def _cognito_initial_auth(self):
        """Authentification initiale AWS Cognito."""
        auth_payload = {
            "AuthFlow": "USER_PASSWORD_AUTH",
            "ClientId": COGNITO_CLIENT_ID,
            "AuthParameters": {
                "USERNAME": self.email,
                "PASSWORD": self.password
            }
        }

        headers = {
            "Content-Type": "application/x-amz-json-1.1; charset=utf-8",
            "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
            "User-Agent": "com.fluidra.iaqualinkplus/1741857021 (Linux; U; Android 14; fr_FR; MI PAD 4; Build/UQ1A.240205.004; Cronet/140.0.7289.0)"
        }

        async with self._session.post(
            COGNITO_ENDPOINT,
            json=auth_payload,
            headers=headers
        ) as response:
            if response.status != 200:
                error_text = await response.text()
                raise FluidraAuthError(f"Cognito auth failed: {response.status} - {error_text}")

            # AWS Cognito renvoie application/x-amz-json-1.1, il faut forcer le décodage
            response_text = await response.text()

            try:
                auth_data = json.loads(response_text)
            except json.JSONDecodeError as e:
                raise FluidraAuthError(f"Invalid JSON response: {e}")

            auth_result = auth_data.get("AuthenticationResult", {})

            self.access_token = auth_result.get("AccessToken")
            self.refresh_token = auth_result.get("RefreshToken")
            self.id_token = auth_result.get("IdToken")

            # Calculer l'expiration du token (AWS Cognito = 1 heure par défaut)
            expires_in = auth_result.get("ExpiresIn", 3600)  # 1 heure par défaut
            import time
            self.token_expires_at = int(time.time()) + expires_in - 300  # Renouveler 5 min avant expiration

            if not self.access_token:
                raise FluidraAuthError("Access token non reçu")

    async def _get_user_profile(self):
        """Récupérer le profil utilisateur."""
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "User-Agent": "com.fluidra.iaqualinkplus/1741857021 (Linux; U; Android 14; fr_FR; MI PAD 4; Build/UQ1A.240205.004; Cronet/140.0.7289.0)"
        }

        profile_url = f"{FLUIDRA_EMEA_BASE}/mobile/consumers/me"


        async with self._session.get(profile_url, headers=headers) as response:
            if response.status == 200:
                profile_data = await response.json()
                return profile_data
            else:
                return {}

    async def async_update_data(self):
        """Discover pools and devices for the account and update their state."""
        self.devices = [] # Clear devices before updating
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "com.fluidra.iaqualinkplus/1741857021 (Linux; U; Android 14; fr_FR; MI PAD 4; Build/UQ1A.240205.004; Cronet/140.0.7289.0)"
        }

        # Découvrir les piscines
        pools_url = f"{FLUIDRA_EMEA_BASE}/generic/users/me/pools"


        async with self._session.get(pools_url, headers=headers) as response:
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

    async def _discover_devices_for_pool(self, pool_id: str, headers: dict):
        """Découvrir les équipements pour une piscine donnée."""
        devices_url = f"{FLUIDRA_EMEA_BASE}/generic/devices"
        params = {"poolId": pool_id, "format": "tree"}


        async with self._session.get(devices_url, headers=headers, params=params) as response:
            if response.status == 200:
                devices_data = await response.json()

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

                    if "pump" in family_lower and ("heat" in family_lower or "eco" in family_lower or "elyo" in family_lower or "thermal" in family_lower):
                        device_type = "heat_pump"
                    elif "pump" in family_lower:
                        device_type = "pump"
                    elif any(keyword in family_lower for keyword in ["heat", "thermal", "eco elyo", "astralpool"]):
                        device_type = "heat_pump"
                    elif any(keyword in device_name_lower for keyword in ["heat", "thermal", "eco", "elyo"]):
                        device_type = "heat_pump"
                    elif "heater" in family_lower:
                        device_type = "heater"
                    elif "light" in family_lower:
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
                    is_running = False
                    operation_mode = 0
                    speed_percent = 0
                    auto_mode_enabled = False

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
                        "is_running": is_running,
                        "auto_mode_enabled": auto_mode_enabled,
                        "operation_mode": operation_mode,
                        "speed_percent": speed_percent,
                        "variable_speed": True,
                        "pump_type": "variable_speed"
                    }
                    self.devices.append(device_info)

    def is_token_expired(self) -> bool:
        """Vérifier si le token va expirer bientôt."""
        if not self.token_expires_at:
            return True  # Pas d'info d'expiration, considérer comme expiré

        import time
        current_time = int(time.time())
        return current_time >= self.token_expires_at

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
            "AuthParameters": {
                "REFRESH_TOKEN": self.refresh_token
            }
        }

        headers = {
            "Content-Type": "application/x-amz-json-1.1; charset=utf-8",
            "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth"
        }

        async with self._session.post(
            COGNITO_ENDPOINT,
            json=refresh_payload,
            headers=headers
        ) as response:
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
                import time
                self.token_expires_at = int(time.time()) + expires_in - 300

                return True
            else:
                return False

    async def get_pools(self) -> List[Dict[str, Any]]:
        """Retourner les piscines découvertes lors de l'authentification."""
        if not self.access_token:
            raise FluidraAuthError("Not authenticated")

        # Convertir les données découvertes en format Home Assistant
        pools = []

        if self.user_pools:
            for pool in self.user_pools:
                pool_id = pool.get("id")
                pool_devices = [device for device in self.devices if device.get("pool_id") == pool_id]

                pool_data = {
                    "id": pool_id,
                    "name": pool.get("name", f"Pool {pool_id}"),
                    "devices": pool_devices
                }
                pools.append(pool_data)

        elif self.devices:
            # Si pas de pools mais des devices, créer un pool par défaut
            default_pool = {
                "id": "default",
                "name": "Fluidra Pool",
                "devices": self.devices
            }
            pools.append(default_pool)

        if not pools:
            # Fallback: créer un pool de test si aucune donnée découverte
            test_pool = {
                "id": "test_pool",
                "name": "Test Pool",
                "devices": [{
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
                    "pump_type": "variable_speed"
                }]
            }
            pools.append(test_pool)

        self._pools = pools
        return self._pools

    def get_pool_by_id(self, pool_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific pool by ID."""
        for pool in self._pools:
            if pool["id"] == pool_id:
                return pool
        return None

    def get_device_by_id(self, device_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific device by ID across all pools."""
        for pool in self._pools:
            for device in pool["devices"]:
                if device.get("device_id") == device_id:
                    return device
        return None

    async def poll_device_status(self, pool_id: str, device_id: str) -> Optional[Dict[str, Any]]:
        """
        Polling principal de l'état des équipements (découvert via reverse engineering).
        Pattern: GET /generic/devices?poolId=...&format=tree toutes les 30s
        """
        if not self.access_token:
            raise FluidraAuthError("Not authenticated")

        # Vérification proactive du token
        if not await self.ensure_valid_token():
            raise FluidraAuthError("Token refresh failed")

        headers = {
            "content-type": "application/json",
            "accept": "application/json",
            "authorization": f"Bearer {self.access_token}",
            "user-agent": "com.fluidra.iaqualinkplus/1741857021 (Linux; U; Android 14; fr_FR; MI PAD 4; Build/UQ1A.240205.004; Cronet/140.0.7289.0)",
            "accept-encoding": "gzip, deflate",
            "priority": "u=1, i"
        }

        url = f"{FLUIDRA_EMEA_BASE}/generic/devices"
        params = {
            "poolId": pool_id,
            "format": "tree"
        }

        try:
            async with self._session.get(url, headers=headers, params=params) as response:
                if response.status == 200:
                    devices = await response.json()

                    # Recherche du device dans la réponse (y compris les périphériques bridgés)
                    for device in devices:
                        if device.get('id') == device_id:
                            return device

                        # Check bridged devices (e.g., chlorinator under bridge)
                        if "devices" in device and isinstance(device["devices"], list):
                            for child_device in device["devices"]:
                                if child_device.get('id') == device_id:
                                    return child_device

                    return None
                elif response.status == 403:
                    # Token expiré, essayer de le rafraîchir
                    if await self.refresh_access_token():
                        return await self.poll_device_status(pool_id, device_id)
                    else:
                        raise FluidraAuthError("Token refresh failed")
                else:
                    return None

        except Exception as e:
            return None

    async def poll_water_quality(self, pool_id: str) -> Optional[Dict[str, Any]]:
        """
        Polling télémétrie qualité de l'eau.
        Pattern: GET /generic/pools/.../assistant/algorithms/telemetryWaterQuality/jobs
        """
        if not self.access_token:
            raise FluidraAuthError("Not authenticated")

        headers = {
            "content-type": "application/json",
            "accept": "application/json",
            "authorization": f"Bearer {self.access_token}",
            "user-agent": "com.fluidra.iaqualinkplus/1741857021 (Linux; U; Android 14; fr_FR; MI PAD 4; Build/UQ1A.240205.004; Cronet/140.0.7289.0)",
        }

        url = f"{FLUIDRA_EMEA_BASE}/generic/pools/{pool_id}/assistant/algorithms/telemetryWaterQuality/jobs"
        params = {"pageSize": 1}

        try:
            async with self._session.get(url, headers=headers, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    return data
                elif response.status == 403:
                    if await self.refresh_access_token():
                        return await self.poll_water_quality(pool_id)
                    else:
                        raise FluidraAuthError("Token refresh failed")
                else:
                    return None

        except Exception as e:
            return None

    async def get_component_state(self, device_id: str, component_id: int) -> Optional[Dict[str, Any]]:
        """
        Récupère l'état d'un component spécifique (reportedValue/desiredValue).
        Cette méthode peut être appelée individuellement pour un component ou via PUT.
        """
        if not self.access_token:
            raise FluidraAuthError("Not authenticated")

        headers = {
            "content-type": "application/json",
            "accept": "application/json",
            "authorization": f"Bearer {self.access_token}",
            "user-agent": "com.fluidra.iaqualinkplus/1741857021 (Linux; U; Android 14; fr_FR; MI PAD 4; Build/UQ1A.240205.004; Cronet/140.0.7289.0)",
        }

        # Pour récupérer l'état, on peut utiliser GET sur le component
        url = f"{FLUIDRA_EMEA_BASE}/generic/devices/{device_id}/components/{component_id}"
        params = {"deviceType": "connected"}

        try:
            async with self._session.get(url, headers=headers, params=params) as response:
                if response.status == 200:
                    return await response.json()
                elif response.status == 403:
                    if await self.refresh_access_token():
                        return await self.get_component_state(device_id, component_id)
                    else:
                        raise FluidraAuthError("Token refresh failed")
                else:
                    return None
        except Exception as e:
            return None

    async def get_device_component_state(self, device_id: str, component_id: int) -> Optional[Dict[str, Any]]:
        """Get the state of a device component."""
        if not self.access_token:
            raise FluidraAuthError("Not authenticated")

        url = f"{FLUIDRA_EMEA_BASE}/generic/devices/{device_id}/components/{component_id}?deviceType=connected"

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "com.fluidra.iaqualinkplus/1741857021 (Linux; U; Android 14; fr_FR; MI PAD 4; Build/UQ1A.240205.004; Cronet/140.0.7289.0)"
        }


        if not self._session:
            self._session = aiohttp.ClientSession()

        try:
            async with self._session.get(url, headers=headers) as response:
                response_text = await response.text()

                if response.status == 200:
                    state_data = await response.json()
                    return state_data
                else:
                    return None
        except aiohttp.ClientError as e:
            return None

    async def control_device_component(self, device_id: str, component_id: int, value: int) -> bool:
        """Control device component using real authentication."""
        if not self.access_token:
            raise FluidraAuthError("Not authenticated")

        # Vérification proactive du token
        if not await self.ensure_valid_token():
            raise FluidraAuthError("Token refresh failed")

        # EXACT format captured from mitmproxy
        url = f"{FLUIDRA_EMEA_BASE}/generic/devices/{device_id}/components/{component_id}?deviceType=connected"

        headers = {
            "content-type": "application/json; charset=utf-8",
            "accept": "application/json",
            "authorization": f"Bearer {self.access_token}",
            "user-agent": "com.fluidra.iaqualinkplus/1741857021 (Linux; U; Android 14; fr_FR; MI PAD 4; Build/UQ1A.240205.004; Cronet/140.0.7289.0)"
        }

        # EXACT payload format captured: {"desiredValue": 1}
        payload = {"desiredValue": value}

        if not self._session:
            self._session = aiohttp.ClientSession()

        try:
            async with self._session.put(url, headers=headers, json=payload) as response:
                response_text = await response.text()

                if response.status == 200:
                    # Parse response for reportedValue/desiredValue (discovered structure)
                    try:
                        response_data = await response.json()
                        reported_value = response_data.get("reportedValue")
                        desired_value = response_data.get("desiredValue")
                        component_ts = response_data.get("ts")

                        # Update local device state with real API response
                        device = self.get_device_by_id(device_id)
                        if device:
                            # IMPORTANT: Update components desiredValue for optimistic UI update
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
                            elif component_id == 10:  # Auto mode (also chlorinator for some models)
                                device["auto_mode_enabled"] = bool(reported_value)
                                device["auto_mode_desired"] = desired_value
                                device["last_updated"] = component_ts

                    except json.JSONDecodeError:
                        # Fallback: mise à jour locale simple
                        device = self.get_device_by_id(device_id)
                        if device and component_id == 9:  # Pump control
                            device["is_running"] = bool(value)
                            device["operation_mode"] = value
                            if value > 1:  # Speed percentage
                                device["speed_percent"] = value
                            elif value == 1:  # Run mode
                                device["speed_percent"] = device.get("speed_percent", 50)
                            else:  # Stop
                                device["speed_percent"] = 0
                        elif device and component_id == 10:  # Auto mode
                            device["auto_mode_enabled"] = bool(value)

                    return True
                elif response.status == 401:
                    # Token expiré, essayer de le renouveler
                    if await self.refresh_access_token():
                        # Retry avec le nouveau token
                        headers["authorization"] = f"Bearer {self.access_token}"
                        async with self._session.put(url, headers=headers, json=payload) as retry_response:
                            if retry_response.status == 200:
                                return True
                            else:
                                return False
                    else:
                        return False
                else:
                    return False

        except aiohttp.ClientError as e:
            return False

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
            else:
                # Fallback: essayer d'autres composants possibles
                for fallback_component in [12, 13, 14, 16]:
                    success = await self.control_device_component(device_id, fallback_component, temperature_value)
                    if success:
                        device = self.get_device_by_id(device_id)
                        if device:
                            device["target_temperature"] = temperature
                        return True

                return False

        except Exception as e:
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
            import asyncio
            await asyncio.sleep(1)

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
        elif speed_percent <= 45:
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


    async def set_schedule(self, device_id: str, schedules: List[Dict[str, Any]]) -> bool:
        """Set pump schedule using exact format from mobile app."""
        if not self.access_token:
            raise FluidraAuthError("Not authenticated")

        # Ensure valid token
        if not await self.ensure_valid_token():
            raise FluidraAuthError("Token refresh failed")

        # EXACT URL format from captured traffic
        url = f"{FLUIDRA_EMEA_BASE}/generic/devices/{device_id}/components/20?deviceType=connected"

        headers = {
            "content-type": "application/json; charset=utf-8",
            "accept": "application/json",
            "authorization": f"Bearer {self.access_token}",
            "user-agent": "com.fluidra.iaqualinkplus/1741857021 (Linux; U; Android 14; fr_FR; MI PAD 4; Build/UQ1A.240205.004; Cronet/140.0.7289.0)"
        }

        # EXACT payload format from mobile app: {"desiredValue": [...]}
        payload = {"desiredValue": schedules}

        if not self._session:
            self._session = aiohttp.ClientSession()

        try:
            async with self._session.put(url, headers=headers, json=payload) as response:
                response_text = await response.text()

                if response.status == 200:
                    return True
                else:
                    return False

        except Exception as e:
            return False

    async def get_default_schedule(self) -> List[Dict[str, Any]]:
        """Get a default schedule template based on captured data."""
        return [
            {
                "id": 1,
                "groupId": 1,
                "enabled": True,
                "startTime": "08 30 * * 1,2,3,4,5,6,7",  # 8h30 tous les jours
                "endTime": "09 59 * * 1,2,3,4,5,6,7",   # 9h59 tous les jours
                "startActions": {"operationName": 1}      # Run mode
            },
        ]

    async def set_component_value(self, device_id: str, component_id: int, value: int) -> bool:
        """Set component value using exact format from mobile app."""
        if not self.access_token:
            raise FluidraAuthError("Not authenticated")

        # Ensure valid token
        if not await self.ensure_valid_token():
            raise FluidraAuthError("Token refresh failed")

        # EXACT URL format from captured traffic
        url = f"{FLUIDRA_EMEA_BASE}/generic/devices/{device_id}/components/{component_id}?deviceType=connected"

        headers = {
            "content-type": "application/json; charset=utf-8",
            "accept": "application/json",
            "authorization": f"Bearer {self.access_token}",
            "user-agent": "com.fluidra.iaqualinkplus/1741857021 (Linux; U; Android 14; fr_FR; MI PAD 4; Build/UQ1A.240205.004; Cronet/140.0.7289.0)"
        }

        # EXACT payload format from mobile app: {"desiredValue": value}
        payload = {"desiredValue": value}

        if not self._session:
            self._session = aiohttp.ClientSession()

        try:
            async with self._session.put(url, headers=headers, json=payload) as response:
                response_text = await response.text()

                if response.status == 200:
                    return True
                else:
                    return False

        except Exception as e:
            return False

    async def clear_schedule(self, device_id: str) -> bool:
        """Clear all schedules for device."""
        return await self.set_schedule(device_id, [])

    async def get_pool_details(self, pool_id: str) -> Optional[Dict[str, Any]]:
        """
        Récupérer les détails spécifiques de la piscine.
        Pattern: GET /generic/pools/{poolId}
        """
        if not self.access_token:
            raise FluidraAuthError("Not authenticated")

        # Vérification proactive du token
        if not await self.ensure_valid_token():
            raise FluidraAuthError("Token refresh failed")

        headers = {
            "content-type": "application/json",
            "accept": "application/json",
            "authorization": f"Bearer {self.access_token}",
            "user-agent": "com.fluidra.iaqualinkplus/1741857021 (Linux; U; Android 14; fr_FR; MI PAD 4; Build/UQ1A.240205.004; Cronet/140.0.7289.0)",
            "accept-encoding": "gzip, deflate",
            "priority": "u=1, i"
        }

        pool_data = {}

        # Récupérer les détails généraux de la piscine
        url = f"{FLUIDRA_EMEA_BASE}/generic/pools/{pool_id}"
        try:
            async with self._session.get(url, headers=headers) as response:
                if response.status == 200:
                    pool_details = await response.json()
                    pool_data.update(pool_details)
                elif response.status == 403:
                    if await self.refresh_access_token():
                        return await self.get_pool_details(pool_id)
                    else:
                        raise FluidraAuthError("Token refresh failed")
        except Exception:
            pass

        # Récupérer les données de statut (météo, etc.)
        status_url = f"{FLUIDRA_EMEA_BASE}/generic/pools/{pool_id}/status"
        try:
            async with self._session.get(status_url, headers=headers) as response:
                if response.status == 200:
                    status_data = await response.json()
                    pool_data["status_data"] = status_data
        except Exception:
            pass

        return pool_data if pool_data else None

    async def get_user_pools(self) -> Optional[List[Dict[str, Any]]]:
        """
        Récupérer la liste des piscines de l'utilisateur.
        Pattern: GET /generic/users/me/pools?
        """
        if not self.access_token:
            raise FluidraAuthError("Not authenticated")

        # Vérification proactive du token
        if not await self.ensure_valid_token():
            raise FluidraAuthError("Token refresh failed")

        headers = {
            "content-type": "application/json",
            "accept": "application/json",
            "authorization": f"Bearer {self.access_token}",
            "user-agent": "com.fluidra.iaqualinkplus/1741857021 (Linux; U; Android 14; fr_FR; MI PAD 4; Build/UQ1A.240205.004; Cronet/140.0.7289.0)",
            "accept-encoding": "gzip, deflate",
            "priority": "u=1, i"
        }

        url = f"{FLUIDRA_EMEA_BASE}/generic/users/me/pools"

        try:
            async with self._session.get(url, headers=headers) as response:
                if response.status == 200:
                    user_pools = await response.json()
                    return user_pools
                elif response.status == 403:
                    # Token expiré, essayer de le rafraîchir
                    if await self.refresh_access_token():
                        return await self.get_user_pools()
                    else:
                        raise FluidraAuthError("Token refresh failed")
                else:
                    return None

        except Exception:
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