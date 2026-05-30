"""Data update coordinator for Fluidra Pool integration."""

from __future__ import annotations

import asyncio
import copy
from datetime import timedelta
import logging
from typing import Any

import aiohttp
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from ..api_resilience import FluidraError
from ..const import DEFAULT_SCAN_INTERVAL, DOMAIN, FluidraPoolConfigEntry
from ..device_registry import DeviceIdentifier
from ..fluidra_api import FluidraPoolAPI
from ._parsers import calculate_auto_speed_from_schedules, parse_dm24049704_schedule_format

_LOGGER = logging.getLogger(__name__)


class FluidraDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the Fluidra Pool API."""

    def __init__(
        self, hass: HomeAssistant, api: FluidraPoolAPI, config_entry: FluidraPoolConfigEntry | None = None
    ) -> None:
        """Initialize."""
        self.api = api
        self.config_entry = config_entry  # Used for HA device cleanup.
        # Track scheduler entities per device for cleanup.
        self._previous_schedule_entities: dict[str, int] = {}
        # Skip heavy polling on first update for faster startup.
        self._first_update = True

        # Honour the user-configured polling interval.
        scan_interval = DEFAULT_SCAN_INTERVAL
        if config_entry and config_entry.options:
            scan_interval = config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)

        super().__init__(
            hass,
            _LOGGER,
            name="fluidra_pool",
            update_interval=timedelta(seconds=scan_interval),
            # Debounce manual refresh requests so multiple toggles share a poll.
            request_refresh_debouncer=Debouncer(
                hass,
                _LOGGER,
                cooldown=1.5,
                immediate=False,
            ),
        )

    def get_pools_from_data(self) -> list[dict]:
        """Get pools list from coordinator data (no API call).

        Use this in platform setup instead of api.get_pools() for faster startup.
        """
        if not self.data:
            return []
        return [{"id": pool_id, **pool_data} for pool_id, pool_data in self.data.items()]

    async def _cleanup_removed_devices(self, current_device_ids: set):
        """Remove devices and entities that no longer exist in Fluidra API."""
        if not self.config_entry:
            return

        try:
            device_registry = dr.async_get(self.hass)
            entity_registry = er.async_get(self.hass)

            devices_to_check = dr.async_entries_for_config_entry(device_registry, self.config_entry.entry_id)

            for device_entry in devices_to_check:
                device_id = None
                for identifier in device_entry.identifiers:
                    if identifier[0] == DOMAIN:
                        device_id = identifier[1]
                        break

                if not device_id:
                    continue

                # Pool devices are parent placeholders, not actual equipment.
                if device_entry.model == "Pool":
                    continue

                if device_id not in current_device_ids:
                    entities_to_remove = er.async_entries_for_device(
                        entity_registry, device_entry.id, include_disabled_entities=True
                    )

                    for entity_entry in entities_to_remove:
                        entity_registry.async_remove(entity_entry.entity_id)

                    device_registry.async_remove_device(device_entry.id)

        except HomeAssistantError as err:
            _LOGGER.debug("Failed to cleanup removed devices: %s", err)

    async def _cleanup_schedule_sensor_if_empty(self, pool_id: str, device_id: str, schedule_data: list):
        """Clean up schedule sensor entity if no schedules remain."""
        try:
            if len(schedule_data) == 0:
                entity_registry = er.async_get(self.hass)

                # Unique-id format for the schedule sensor.
                expected_unique_id = f"fluidra_pool_{pool_id}_{device_id}_sensor_schedules"

                for entity_id, entry in entity_registry.entities.items():
                    if entry.platform == "fluidra_pool" and entry.unique_id == expected_unique_id:
                        entity_registry.async_remove(entity_id)
                        break

        except HomeAssistantError as err:
            _LOGGER.debug("Failed to cleanup schedule sensor: %s", err)

    # Kept as a thin wrapper so existing callers (and tests) keep working.
    def _parse_dm24049704_schedule_format(self, reported_value: dict) -> list:
        """Parse DM24049704 chlorinator schedule format (programs/slots) to standard format."""
        return parse_dm24049704_schedule_format(reported_value)

    def _calculate_auto_speed_from_schedules(self, device: dict) -> int:
        """Calculate current speed based on active schedules in auto mode."""
        return calculate_auto_speed_from_schedules(device)

    async def _fetch_components_parallel(self, device_id: str, components_to_scan: list[int]) -> dict[int, dict]:
        """Fetch multiple component states in parallel for a device.

        Returns a dict mapping component_id to component_state.
        """
        # Cap concurrency so we don't trip rate limits or exhaust the connector.
        semaphore = asyncio.Semaphore(10)

        async def fetch_one(component_id: int) -> tuple[int, dict | None]:
            async with semaphore:
                state = await self.api.get_component_state(device_id, component_id)
                return (component_id, state)

        tasks = [fetch_one(cid) for cid in components_to_scan]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        component_states: dict[int, dict] = {}
        for result in results:
            if isinstance(result, tuple):
                cid, state = result
                if state and isinstance(state, dict):
                    component_states[cid] = state
        return component_states

    def _process_component_state(self, device: dict, pool_id: str, component_id: int, component_state: dict) -> None:
        """Process a single component state and update device data.

        Extracted from _async_update_data to reduce code duplication.
        """
        reported_value = component_state.get("reportedValue")
        raw_device_id = device.get("device_id")
        device_id = str(raw_device_id) if raw_device_id is not None else ""

        # Store ALL component data.
        device["components"][str(component_id)] = component_state

        # Info-component layout. Fluidra's standard slots are 0=device-id,
        # 1=part-numbers, 2=signal/RSSI, 3=firmware. The Blue Connect (BC3)
        # reorders them — 0=RSSI, 1=serial, 2=hardware-UID — which made the
        # device-info sensor show the signal as the device id and vice versa
        # (Issue #69). "info_layout" lets a family override the mapping while
        # everyone else keeps the default.
        bc_info_layout = DeviceIdentifier.get_feature(device, "info_layout") == "blue_connect"

        if component_id == 0:
            if bc_info_layout:
                if not DeviceIdentifier.has_feature(device, "skip_signal"):
                    device["signal_strength_component"] = reported_value
            else:
                device["device_id_component"] = reported_value
            if DeviceIdentifier.has_feature(device, "z260iq_mode") and reported_value is not None:
                try:
                    device["running_hours"] = int(reported_value)
                except (ValueError, TypeError):
                    pass
        elif component_id == 1:
            if bc_info_layout:
                device["device_id_component"] = reported_value
            else:
                device["part_numbers_component"] = reported_value
        elif component_id == 2:
            if bc_info_layout:
                # Hardware UID on Blue Connect — not an RSSI value.
                device["part_numbers_component"] = reported_value
            elif not DeviceIdentifier.has_feature(device, "skip_signal"):
                device["signal_strength_component"] = reported_value
        elif component_id == 3:
            if not DeviceIdentifier.has_feature(device, "skip_firmware"):
                device["firmware_version_component"] = reported_value
        elif component_id == 4:
            device["hardware_errors_component"] = reported_value
        elif component_id == 5:
            device["comm_errors_component"] = reported_value
        elif component_id == 9:
            device["pump_reported"] = reported_value
            device["pump_desired"] = component_state.get("desiredValue")
            device["is_running"] = bool(reported_value)
        elif component_id == 10:
            device["auto_reported"] = reported_value
            device["auto_desired"] = component_state.get("desiredValue")
            device["auto_mode_enabled"] = bool(reported_value)
        elif component_id == 11:
            device["speed_level_reported"] = reported_value
            device["speed_level_desired"] = component_state.get("desiredValue")
            if not device.get("is_running", False):
                device["speed_percent"] = 0
            else:
                auto_mode = device.get("auto_mode_enabled", False)
                if auto_mode:
                    device["speed_percent"] = calculate_auto_speed_from_schedules(device)
                elif reported_value == 0:
                    device["speed_percent"] = 45
                elif reported_value == 1:
                    device["speed_percent"] = 65
                elif reported_value == 2:
                    device["speed_percent"] = 100
                else:
                    device["speed_percent"] = 0
        elif component_id == 13:
            device["component_13_data"] = component_state
            if device.get("type", "").lower() == "heat_pump" and not DeviceIdentifier.has_feature(device, "z550_mode"):
                device["heat_pump_reported"] = reported_value
                device["is_heating"] = bool(reported_value)
        elif component_id == 14:
            device["component_14_data"] = component_state
            if DeviceIdentifier.has_feature(device, "z260iq_mode") and reported_value is not None:
                try:
                    device["z260iq_mode_value"] = int(reported_value)
                except (ValueError, TypeError):
                    pass
        elif component_id == 15:
            device["component_15_speed"] = reported_value or component_state.get("desiredValue") or 0
            temp_raw = reported_value or component_state.get("desiredValue")
            if device.get("type", "").lower() == "heat_pump" and temp_raw:
                try:
                    temp_value = float(temp_raw) / 10.0
                    if 10.0 <= temp_value <= 50.0:
                        device["target_temperature"] = temp_value
                except (ValueError, TypeError):
                    pass
        elif component_id == 16:
            device["component_16_data"] = component_state
            if DeviceIdentifier.has_feature(device, "z550_mode"):
                device["z550_mode_reported"] = reported_value
        elif component_id == 17:
            device["component_17_data"] = component_state
            if DeviceIdentifier.has_feature(device, "z550_mode"):
                device["z550_preset_reported"] = reported_value
        elif component_id == 19:
            device["timezone_component"] = reported_value
            if device.get("type", "").lower() == "heat_pump" and reported_value:
                try:
                    water_temp_value = float(reported_value) / 10.0
                    if 5.0 <= water_temp_value <= 50.0:
                        device["water_temperature"] = water_temp_value
                except (ValueError, TypeError):
                    pass
        elif component_id == 20:
            device_type = device.get("type", "")
            if device_type == "chlorinator":
                # EXO chlorinators expose schedules (list) on component 20.
                if isinstance(reported_value, list):
                    device["schedule_data"] = reported_value
                    self._track_schedule_count(pool_id, device_id, reported_value)
                elif isinstance(reported_value, int):
                    device["mode_reported"] = reported_value
            else:
                schedule_data = reported_value if isinstance(reported_value, list) else []
                device["schedule_data"] = schedule_data
                self._track_schedule_count(pool_id, device_id, schedule_data)
        elif component_id == 21:
            device["network_status_component"] = reported_value
            if DeviceIdentifier.has_feature(device, "z550_mode"):
                device["heat_pump_reported"] = reported_value
                device["is_heating"] = bool(reported_value)
        elif component_id == 37:
            device["component_37_data"] = component_state
            if DeviceIdentifier.has_feature(device, "z550_mode") and reported_value:
                try:
                    water_temp = float(reported_value) / 10.0
                    if 0.0 <= water_temp <= 50.0:
                        device["water_temperature"] = water_temp
                except (ValueError, TypeError):
                    pass
        elif component_id == 40:
            device[f"component_{component_id}_data"] = component_state
            if DeviceIdentifier.has_feature(device, "z550_mode") and reported_value:
                try:
                    air_temp = float(reported_value) / 10.0
                    if -20.0 <= air_temp <= 60.0:
                        device["air_temperature"] = air_temp
                except (ValueError, TypeError):
                    pass
            else:
                config = DeviceIdentifier.identify_device(device)
                device_type = config.device_type if config else device.get("type", "")
                if device_type == "light":
                    schedule_data = reported_value if isinstance(reported_value, list) else []
                    device["schedule_data"] = schedule_data
                    self._track_schedule_count(pool_id, device_id, schedule_data)
        elif component_id == 61:
            device["component_61_data"] = component_state
            if DeviceIdentifier.has_feature(device, "z550_mode"):
                device["z550_state_reported"] = reported_value
                if reported_value == 2:
                    device["hvac_action"] = "heating"
                elif reported_value == 3:
                    device["hvac_action"] = "cooling"
                elif reported_value == 11:
                    device["hvac_action"] = "no_flow"
                else:
                    device["hvac_action"] = "idle"
        elif component_id in [62, 65]:
            if device.get("type", "").lower() == "heat_pump" and reported_value:
                try:
                    water_temp_value = float(reported_value) / 10.0
                    if 5.0 <= water_temp_value <= 50.0 and "water_temperature" not in device:
                        device["water_temperature"] = water_temp_value
                except (ValueError, TypeError):
                    pass
            device[f"component_{component_id}_data"] = component_state
        elif component_id == 28:
            device["component_28_data"] = component_state
            if DeviceIdentifier.has_feature(device, "z260iq_mode") and reported_value is not None:
                try:
                    device["no_flow_alarm"] = int(reported_value) != 0
                except (ValueError, TypeError):
                    pass
        elif component_id == 67:
            device["component_67_data"] = component_state
            if DeviceIdentifier.has_feature(device, "z260iq_mode") and reported_value is not None:
                try:
                    air_temp = float(reported_value) / 10.0
                    if -30.0 <= air_temp <= 60.0:
                        device["air_temperature"] = air_temp
                except (ValueError, TypeError):
                    pass
        else:
            schedule_comp = DeviceIdentifier.get_feature(device, "schedule_component")
            if schedule_comp and component_id == schedule_comp:
                if isinstance(reported_value, dict) and "programs" in reported_value:
                    schedule_data = parse_dm24049704_schedule_format(reported_value)
                elif isinstance(reported_value, list):
                    schedule_data = reported_value
                else:
                    schedule_data = []
                device["schedule_data"] = schedule_data
                self._track_schedule_count(pool_id, device_id, schedule_data)
            device[f"component_{component_id}_data"] = component_state

    def _track_schedule_count(self, pool_id: str, device_id: str, schedule_data: list) -> None:
        """Track schedule count changes for cleanup."""
        device_key = f"{pool_id}_{device_id}"
        # Cleanup happens asynchronously in a separate task to avoid blocking.
        self._previous_schedule_entities[device_key] = len(schedule_data)

    async def _async_update_data(self):
        """Update data via library using optimized parallel polling."""
        try:
            if not await self.api.ensure_valid_token():
                _LOGGER.error(
                    "Token validation failed — triggering reauth flow "
                    "(token_expires_at=%s, has_refresh=%s, has_access=%s)",
                    self.api.token_expires_at,
                    bool(self.api.refresh_token),
                    bool(self.api.access_token),
                )
                raise ConfigEntryAuthFailed(
                    translation_domain=DOMAIN,
                    translation_key="auth_failed",
                )

            pools = await self.api.get_pools()

            # Fast startup: minimal data on first update.
            if self._first_update:
                self._first_update = False
                return {pool["id"]: pool for pool in pools}

            previous_data = self.data if isinstance(self.data, dict) else {}

            # Process each pool, isolating failures so one broken pool does not
            # make the whole integration unavailable.
            for pool in pools:
                try:
                    await self._refresh_pool(pool, previous_data)
                except (aiohttp.ClientError, TimeoutError, FluidraError) as err:
                    _LOGGER.warning(
                        "Failed to refresh pool %s, keeping previous data: %s",
                        pool.get("id"),
                        err,
                    )
                    prev_pool = previous_data.get(pool.get("id"))
                    if prev_pool:
                        pool.update(prev_pool)

            # Collect current device IDs for cleanup.
            current_device_ids: set[str] = set()
            for pool in pools:
                current_device_ids.add(pool["id"])
                for device in pool.get("devices", []):
                    device_id = device.get("device_id")
                    if device_id:
                        current_device_ids.add(device_id)

            await self._cleanup_removed_devices(current_device_ids)

            return {pool["id"]: pool for pool in pools}

        except ConfigEntryAuthFailed:
            raise
        except (aiohttp.ClientError, TimeoutError, FluidraError) as err:
            _LOGGER.exception("Error updating Fluidra Pool data")
            raise UpdateFailed(f"Error communicating with API: {type(err).__name__}") from err

    async def _refresh_pool(self, pool: dict, previous_data: dict) -> None:
        """Refresh a single pool and its devices."""
        pool_id = pool["id"]
        prev_pool = previous_data.get(pool_id, {})
        prev_devices_by_id = {d.get("device_id"): d for d in prev_pool.get("devices", []) if d.get("device_id")}

        pool_details_task = self.api.get_pool_details(pool_id)
        water_quality_task = self.api.poll_water_quality(pool_id)
        refresh_results: tuple[Any, Any] = await asyncio.gather(
            pool_details_task, water_quality_task, return_exceptions=True
        )
        pool_details_result, water_quality_result = refresh_results

        if isinstance(pool_details_result, dict):
            api_devices = pool.get("devices", [])
            pool.update(pool_details_result)
            pool["devices"] = api_devices

        if isinstance(water_quality_result, dict):
            pool["water_quality"] = water_quality_result

        # Preserve previous component data with deep copy to avoid aliasing.
        for device in pool.get("devices", []):
            device_id = device.get("device_id")
            if device_id and device_id in prev_devices_by_id:
                prev_device = prev_devices_by_id[device_id]
                if "components" in prev_device:
                    device["components"] = copy.deepcopy(prev_device["components"])

        devices_with_ids = [(d, d.get("device_id")) for d in pool.get("devices", []) if d.get("device_id")]
        if devices_with_ids:
            status_tasks = [self.api.poll_device_status(pool_id, did) for _, did in devices_with_ids]
            status_results = await asyncio.gather(*status_tasks, return_exceptions=True)

            for (device, _), status in zip(devices_with_ids, status_results, strict=False):
                if isinstance(status, dict):
                    device["status"] = status
                    connectivity = status.get("connectivity", {})
                    device["connectivity"] = connectivity
                    if "connected" in connectivity:
                        device["online"] = connectivity["connected"]

        for device in pool.get("devices", []):
            device_id = device.get("device_id")
            if not device_id:
                continue
            device.setdefault("components", {})
            specific_components = DeviceIdentifier.get_feature(device, "specific_components", [])
            if specific_components:
                # When a device declares specific_components, that list is the
                # exhaustive set of useful components. Scan only the device-info
                # components (0-3) plus the specific ones, instead of the full
                # 0..range sweep — the sweep fired 25+ parallel requests per
                # device and triggered HTTP 429 rate limiting (Issue #63).
                components_to_scan = [0, 1, 2, 3]
                components_to_scan.extend(c for c in specific_components if c not in components_to_scan)
            else:
                component_range = DeviceIdentifier.get_components_range(device)
                components_to_scan = list(range(component_range))
            component_states = await self._fetch_components_parallel(device_id, components_to_scan)
            for component_id, component_state in component_states.items():
                self._process_component_state(device, pool_id, component_id, component_state)

            # Recompute auto-mode pump speed AFTER the whole component scan: the
            # speed (component 11) is processed before the schedule (component 20)
            # in scan order, so the inline calculation at component 11 would read a
            # stale/empty schedule_data. Redo it once schedule_data is populated.
            if device.get("auto_mode_enabled", False):
                device["speed_percent"] = (
                    calculate_auto_speed_from_schedules(device) if device.get("is_running", False) else 0
                )
