"""Pool/device discovery and polling."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

from ..api_resilience import FluidraAuthError, FluidraCircuitBreakerError, FluidraError
from ..utils import mask_device_id
from ._base import FluidraAPIBase
from ._constants import DEVICES_ENDPOINT, FLUIDRA_EMEA_BASE, USER_POOLS_ENDPOINT
from ._helpers import classify_device_type

_LOGGER = logging.getLogger(__name__)


class DevicesMixin(FluidraAPIBase):
    """Pool & device discovery, caching, and polling."""

    async def async_update_data(self) -> None:
        """Discover pools and devices for the account; atomic replacement at end."""
        headers = self._build_auth_headers()

        user_pools: list[dict[str, Any]] = []
        devices: list[dict[str, Any]] = []

        try:
            status, data, _ = await self._request("GET", USER_POOLS_ENDPOINT, headers=headers)
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
        params = {"poolId": pool_id, "format": "tree"}

        try:
            status, devices_data, _ = await self._request("GET", DEVICES_ENDPOINT, headers=headers, params=params)
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

        # De-duplicate by device_id: the Fluidra tree endpoint can return the same
        # physical device twice — an ``offline`` cloud-shadow entry and a live
        # ``connected`` entry sharing one ``id`` (seen on WiFi/BLE units like the
        # Blue Connect Silver, issue #69). Keeping both produced duplicate-unique_id
        # warnings, double polling, and value lookups binding to the stale shadow.
        result: dict[str, dict[str, Any]] = {}

        def _add(raw_device_id: Any, built: dict[str, Any]) -> None:
            """Insert a built device, preferring the ``connected`` entry on collision."""
            # Devices without an id can't be de-duplicated; keep each under a
            # synthetic key so none are dropped.
            key = str(raw_device_id) if raw_device_id else f"__noid_{len(result)}"
            existing = result.get(key)
            if existing is None or (
                built["connection_type"] == "connected" and existing["connection_type"] != "connected"
            ):
                result[key] = built

        for device in pool_devices:
            device_id = device.get("id")
            info = device.get("info", {})
            device_name = info.get("name", f"Device {device_id}")
            family = info.get("family", "")
            connection_type = device.get("type", "unknown")

            device_type = classify_device_type(family, device_name)
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

                        child_device_type = classify_device_type(child_family, child_device_name)
                        child_is_pump = child_device_type == "pump"

                        _add(
                            child_device_id,
                            {
                                "pool_id": pool_id,
                                "device_id": child_device_id,
                                "name": child_device_name,
                                "type": child_device_type,
                                "family": child_family,
                                "connection_type": child_connection_type,
                                "model": child_device_name,
                                "manufacturer": "Fluidra",
                                # Connection type is only trustworthy when explicit; anything else
                                # is unknown (None) so it does not read as offline.
                                "online": {"connected": True, "disconnected": False}.get(child_connection_type),
                                "is_running": False,
                                "auto_mode_enabled": False,
                                "operation_mode": 0,
                                "speed_percent": 0,
                                "parent_id": device_id,
                                # Pumps behind a bridge must still expose the speed-select
                                # entity, gated on these keys by the select platform.
                                "variable_speed": child_is_pump,
                                "pump_type": "variable_speed" if child_is_pump else child_device_type,
                            },
                        )
                continue

            _add(
                device_id,
                {
                    "pool_id": pool_id,
                    "device_id": device_id,
                    "name": device_name,
                    "type": device_type,
                    "family": family,
                    "connection_type": connection_type,
                    "model": device_name,
                    "manufacturer": "Fluidra",
                    # See above: explicit values only, unknown stays None.
                    "online": {"connected": True, "disconnected": False}.get(connection_type),
                    "is_running": False,
                    "auto_mode_enabled": False,
                    "operation_mode": 0,
                    "speed_percent": 0,
                    "variable_speed": True,
                    "pump_type": "variable_speed",
                },
            )

        return list(result.values())

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

    def get_device_by_id(self, device_id: str) -> dict[str, Any] | None:
        """Return a specific device by ID across all pools."""
        for pool in self._pools:
            devices: list[dict[str, Any]] = pool["devices"]
            for device in devices:
                if device.get("device_id") == device_id:
                    return device
        return None

    async def poll_pool_device_statuses(self, pool_id: str) -> dict[str, dict[str, Any]] | None:
        """Fetch the pool device tree once and return statuses keyed by device id.

        The tree endpoint returns every device (and bridged children) of the
        pool in a single response, so polling it once per pool replaces one
        identical GET per device (Issue #140). Returns ``None`` when the fetch
        failed — callers keep the previous per-device data.
        """
        if not self.access_token:
            raise FluidraAuthError("Not authenticated")

        if not await self.ensure_valid_token():
            raise FluidraAuthError("Token refresh failed")

        headers = self._build_auth_headers()
        params = {"poolId": pool_id, "format": "tree"}

        try:
            status, data, _ = await self._request("GET", DEVICES_ENDPOINT, headers=headers, params=params)
        except FluidraCircuitBreakerError:
            _LOGGER.debug("Circuit breaker open, skipping status poll for pool %s", pool_id)
            return None
        except FluidraError as err:
            _LOGGER.debug("Poll pool device statuses failed: %s", err)
            return None

        if status != 200:
            return None
        # The tree endpoint may return a bare list or a {"devices": [...]} envelope —
        # accept both, exactly as _discover_devices_for_pool does for the same request.
        if isinstance(data, dict):
            data = data.get("devices", [])
        if not isinstance(data, list):
            return None

        statuses: dict[str, dict[str, Any]] = {}
        device_list: list[dict[str, Any]] = data
        for device in device_list:
            if not isinstance(device, dict):
                continue
            if device.get("id") is not None:
                statuses[device["id"]] = device
            children = device.get("devices")
            if isinstance(children, list):
                child_list: list[dict[str, Any]] = children
                for child in child_list:
                    if isinstance(child, dict) and child.get("id") is not None:
                        statuses[child["id"]] = child
        return statuses

    async def poll_device_status(self, pool_id: str, device_id: str) -> dict[str, Any] | None:
        """Poll a single device's state from the Fluidra API.

        Fetches the whole pool tree — prefer :meth:`poll_pool_device_statuses`
        when several devices of the same pool are needed.
        """
        statuses = await self.poll_pool_device_statuses(pool_id)
        if statuses is None:
            _LOGGER.debug("No status tree for device %s", mask_device_id(device_id))
            return None
        return statuses.get(device_id)

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
