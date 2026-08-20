"""Per-component get/set operations and local state mirroring."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

from ..api_resilience import FluidraAuthError, FluidraCircuitBreakerError, FluidraError
from ..const import COMPONENT_AUTO_MODE, COMPONENT_PUMP_ONOFF
from ..utils import mask_device_id
from ._base import FluidraAPIBase
from ._constants import CONNECTED_PARAMS, FLUIDRA_EMEA_BASE

_LOGGER = logging.getLogger(__name__)


class ComponentsMixin(FluidraAPIBase):
    """Read/write a single device component (with local state mirroring)."""

    async def get_component_state(self, device_id: str, component_id: int) -> dict[str, Any] | None:
        """Retrieve the current state of a single component."""
        if not self.access_token:
            raise FluidraAuthError("Not authenticated")

        headers = self._build_auth_headers()
        url = f"{FLUIDRA_EMEA_BASE}/generic/devices/{quote(str(device_id), safe='')}/components/{int(component_id)}"
        params = dict(CONNECTED_PARAMS)

        try:
            status, data, _ = await self._request("GET", url, headers=headers, params=params)
        except FluidraError as err:
            _LOGGER.debug("Get component state failed: %s", err)
            return None

        if status == 200 and isinstance(data, dict):
            return data
        return None

    async def get_all_components(self, device_id: str) -> dict[int, dict[str, Any]] | None:
        """Fetch every component of a device in one request.

        This is the endpoint the official app uses (Issue #144, @renaatski):
        ``GET /generic/devices/{id}/components?deviceType=connected&details=true``.
        One call replaces the per-component fan-out, which cuts request volume
        (and the HTTP 429 rate-limiting it caused — Issue #63) and makes the wider
        register ranges affordable to poll.

        Returns ``{component_id: state}``, or ``None`` when the request failed or
        the payload wasn't in a shape we recognise, so callers can fall back to
        per-component reads.
        """
        if not self.access_token:
            raise FluidraAuthError("Not authenticated")

        headers = self._build_auth_headers()
        url = f"{FLUIDRA_EMEA_BASE}/generic/devices/{quote(str(device_id), safe='')}/components"
        params = dict(CONNECTED_PARAMS) | {"details": "true"}

        try:
            status, data, _ = await self._request("GET", url, headers=headers, params=params)
        except FluidraError as err:
            _LOGGER.debug("Bulk component fetch failed for %s: %s", mask_device_id(device_id), err)
            return None

        if status != 200:
            _LOGGER.debug(
                "Bulk component fetch for %s returned HTTP %s",
                mask_device_id(device_id),
                status,
            )
            return None

        return self._parse_bulk_components(data)

    @staticmethod
    def _parse_bulk_components(data: Any) -> dict[int, dict[str, Any]] | None:
        """Normalise a bulk-components payload into ``{component_id: state}``.

        The exact envelope isn't contractually documented, so accept the shapes it
        can plausibly take — a bare list of component objects, a ``{"components":
        [...]}`` wrapper, or an id-keyed mapping — and return ``None`` for anything
        unrecognised rather than silently yielding an empty scan (which the caller
        would mistake for "device has no components").
        """
        entries: Any = data
        if isinstance(data, dict):
            # Either a wrapper around the list, or already an id-keyed mapping.
            if isinstance(data.get("components"), list):
                entries = data["components"]
            else:
                mapping: dict[int, dict[str, Any]] = {}
                for key, value in data.items():
                    if not isinstance(value, dict):
                        continue
                    try:
                        mapping[int(key)] = value
                    except (TypeError, ValueError):
                        continue
                return mapping or None

        if not isinstance(entries, list):
            return None

        states: dict[int, dict[str, Any]] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            raw_id = entry.get("id")
            try:
                states[int(raw_id)] = entry  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
        return states or None

    def reported_component_value(self, device_id: str, component_id: int) -> Any:
        """Return the last value the device reported for a component, if known.

        Read from the local mirror rather than the network: this is called on
        the write path, where an extra round-trip would cost a request per
        command (the rate limiting of Issue #63 is a real constraint).
        """
        device = self.get_device_by_id(device_id)
        if not device:
            return None
        component = device.get("components", {}).get(str(int(component_id)))
        if not isinstance(component, dict):
            return None
        return component.get("reportedValue")

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

        url = f"{FLUIDRA_EMEA_BASE}/generic/devices/{quote(str(device_id), safe='')}/components/{int(component_id)}"
        payload = {"desiredValue": value}

        # Read the value the device reports *before* the write: the response
        # echoes back whatever we asked for, so it cannot serve as a baseline.
        baseline = self.reported_component_value(device_id, component_id)

        try:
            status, data, raw_text = await self._request(
                "PUT", url, headers=headers, json_data=payload, params=dict(CONNECTED_PARAMS)
            )
        except FluidraCircuitBreakerError:
            _LOGGER.warning("Circuit breaker open, cannot control device %s", mask_device_id(device_id))
            return False
        except FluidraError as err:
            _LOGGER.warning("Control device component failed: %s", err)
            return False

        if status == 200:
            # HTTP 200 is not proof the write landed (Issue #133) — arm a
            # read-back through the normal poll, a few cycles from now.
            self.write_verifier.record(device_id, component_id, value, baseline)
            if isinstance(data, dict) and isinstance(value, int):
                self._update_device_state_from_response(device_id, component_id, data, value)
            elif isinstance(value, int):
                self._update_device_state_fallback(device_id, component_id, value)
            return True

        # An outright rejection (the boost 404 of Issue #133) is already visible
        # here; a stale pending entry for the same component would later be
        # judged against a write that never happened.
        self.write_verifier.discard(device_id, component_id)
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

        url = f"{FLUIDRA_EMEA_BASE}/generic/devices/{quote(str(device_id), safe='')}/components/{int(component_id)}"
        payload = {"desiredValue": value}

        baseline = self.reported_component_value(device_id, component_id)

        try:
            status, _, _ = await self._request(
                "PUT", url, headers=headers, json_data=payload, params=dict(CONNECTED_PARAMS)
            )
        except FluidraError as err:
            _LOGGER.debug("Set component value failed: %s", err)
            return False

        if status != 200:
            self.write_verifier.discard(device_id, component_id)
            return False

        self.write_verifier.record(device_id, component_id, value, baseline)
        return True
