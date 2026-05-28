"""Per-component get/set operations and local state mirroring."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

from ..api_resilience import FluidraAuthError, FluidraCircuitBreakerError, FluidraError
from ..const import COMPONENT_AUTO_MODE, COMPONENT_PUMP_ONOFF
from ..utils import mask_device_id
from ._constants import FLUIDRA_EMEA_BASE

_LOGGER = logging.getLogger(__name__)


class ComponentsMixin:
    """Read/write a single device component (with local state mirroring)."""

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
