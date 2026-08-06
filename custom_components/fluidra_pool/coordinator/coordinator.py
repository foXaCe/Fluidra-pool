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
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from ..api_resilience import FluidraConnectionError, FluidraError
from ..const import (
    BULK_FETCH_FAILURE_LIMIT,
    COMPONENT_HEAT_PUMP_SETPOINT,
    CONNECTION_ISSUE_THRESHOLD,
    DEFAULT_SCAN_INTERVAL,
    DEVICE_TYPE_CHLORINATOR,
    DEVICE_TYPE_HEAT_PUMP,
    DEVICE_TYPE_LIGHT,
    DOMAIN,
    EMPTY_COMPONENT_FETCH_THRESHOLD,
    OFFLINE_GRACE_POLLS,
    PUMP_SPEED_PERCENTAGES,
    STALE_DEVICE_THRESHOLD,
    VICTORIA_PRESET_FIRST,
    VICTORIA_PRESET_LAST,
    FluidraPoolConfigEntry,
)
from ..device_registry import DeviceIdentifier
from ..fluidra_api import FluidraPoolAPI
from ..helpers import determine_pool_access, resolve_schedule_component
from ..repairs import (
    async_create_connection_issue,
    async_create_unverified_profile_issue,
    async_delete_connection_issue,
    async_delete_unverified_profile_issue,
)
from ._parsers import calculate_auto_speed_from_schedules, parse_dm24049704_schedule_format

_LOGGER = logging.getLogger(__name__)


class FluidraDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Class to manage fetching data from the Fluidra Pool API."""

    def __init__(
        self, hass: HomeAssistant, api: FluidraPoolAPI, config_entry: FluidraPoolConfigEntry | None = None
    ) -> None:
        """Initialize."""
        self.api = api
        self.config_entry = config_entry  # Used for HA device cleanup.
        # Track scheduler entities per device for cleanup.
        self._previous_schedule_entities: dict[str, int] = {}
        # Consecutive polls each registry device has been missing (stale-devices).
        self._missing_device_counts: dict[str, int] = {}
        # Consecutive offline connectivity reports per device (Issue #140 debounce).
        self._offline_poll_counts: dict[str, int] = {}
        # Consecutive fully-empty component fetches per device — see
        # EMPTY_COMPONENT_FETCH_THRESHOLD for why this must not stay silent.
        self._empty_component_fetch_counts: dict[str, int] = {}
        # Skip heavy polling on first update for faster startup.
        self._first_update = True
        # Consecutive failed poll cycles; drives the connection_error repair issue.
        self._consecutive_update_failures = 0
        # Pools already warned about read-only (viewer) access — warn once, not every poll.
        self._read_only_pools_warned: set[str] = set()
        # Devices already flagged for an unverified profile — raise once, not every poll.
        self._unverified_profile_flagged: set[str] = set()
        # Consecutive bulk-fetch failures per device (Issue #144). Tracked per
        # device, not globally: some devices 404 the bulk endpoint while others on
        # the same account answer it fine (Issue #175), and a shared counter was
        # reset by their successes so the unsupported one was retried every poll.
        self._bulk_fetch_failures: dict[str, int] = {}
        # Devices whose unmapped registers have already been dumped at DEBUG — see
        # _log_unmapped_components; log once per session, not on every poll.
        self._unmapped_logged: set[str] = set()

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

    def get_pools_from_data(self) -> list[dict[str, Any]]:
        """Get pools list from coordinator data (no API call).

        Use this in platform setup instead of api.get_pools() for faster startup.
        """
        if not self.data:
            return []
        return [{"id": pool_id, **pool_data} for pool_id, pool_data in self.data.items()]

    async def _cleanup_removed_devices(self, current_device_ids: set[str]) -> None:
        """Purge devices/entities that have disappeared from the Fluidra API.

        A device is only removed once it has been absent from
        STALE_DEVICE_THRESHOLD consecutive successful polls, so a transient
        partial cloud response cannot wipe devices, entities and their history
        on a single hiccup.
        """
        if not self.config_entry:
            return

        try:
            device_registry = dr.async_get(self.hass)
            entity_registry = er.async_get(self.hass)

            devices_to_check = dr.async_entries_for_config_entry(device_registry, self.config_entry.entry_id)
            seen_ids: set[str] = set()

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

                seen_ids.add(device_id)

                if device_id in current_device_ids:
                    # Device present again — clear any pending strike.
                    self._missing_device_counts.pop(device_id, None)
                    continue

                # Absent this poll: count consecutive misses and purge only once
                # the device has been gone long enough to rule out a transient gap.
                misses = self._missing_device_counts.get(device_id, 0) + 1
                if misses < STALE_DEVICE_THRESHOLD:
                    self._missing_device_counts[device_id] = misses
                    continue

                self._missing_device_counts.pop(device_id, None)
                entities_to_remove = er.async_entries_for_device(
                    entity_registry, device_entry.id, include_disabled_entities=True
                )
                for entity_entry in entities_to_remove:
                    entity_registry.async_remove(entity_entry.entity_id)
                device_registry.async_remove_device(device_entry.id)

            # Drop strike counters for devices no longer in the registry.
            self._missing_device_counts = {
                did: count for did, count in self._missing_device_counts.items() if did in seen_ids
            }
            self._offline_poll_counts = {
                did: count for did, count in self._offline_poll_counts.items() if did in seen_ids
            }

        except Exception as err:  # best-effort cleanup must never fail the poll
            # async_remove_device can raise a bare KeyError when a device is
            # removed concurrently (the device registry has no guard, unlike the
            # entity registry). A best-effort cleanup must never propagate and
            # fail the whole poll, so catch broadly and only log.
            _LOGGER.debug("Failed to cleanup removed devices: %s", err)

    # Kept as a thin wrapper so existing callers (and tests) keep working.
    def _parse_dm24049704_schedule_format(self, reported_value: dict[str, Any]) -> list[dict[str, Any]]:
        """Parse DM24049704 chlorinator schedule format (programs/slots) to standard format."""
        return parse_dm24049704_schedule_format(reported_value)

    def _calculate_auto_speed_from_schedules(self, device: dict[str, Any]) -> int:
        """Calculate current speed based on active schedules in auto mode."""
        return calculate_auto_speed_from_schedules(device)

    async def _fetch_components(self, device_id: str, components_to_scan: list[int]) -> dict[int, dict[str, Any]]:
        """Fetch the requested components, preferring the single bulk request.

        The bulk endpoint returns every component in one call (Issue #144), which
        replaces N requests per device per poll and removes the rate-limit pressure
        that the fan-out created (Issue #63). It is *not* assumed to work: if it
        fails or returns an unusable payload we fall back to the per-component
        scan for this poll, and after BULK_FETCH_FAILURE_LIMIT consecutive failures
        we stop attempting it *for that device* for the rest of the session.

        The failure count is per device, not global: some devices answer the bulk
        endpoint with a 404 while others on the same account answer it fine (Issue
        #175 — a virtual ``standardTestStrip`` device does exactly this). A shared
        counter was reset by every other device's success, so the unsupported one
        was retried on every single poll forever.
        """
        if self._bulk_fetch_failures.get(device_id, 0) < BULK_FETCH_FAILURE_LIMIT:
            bulk = await self.api.get_all_components(device_id)
            # Only trust a real id-keyed mapping: anything else counts as a failure
            # and falls through to the per-component scan.
            if isinstance(bulk, dict) and bulk:
                self._bulk_fetch_failures.pop(device_id, None)
                # Keep only what this device's profile asks for: the bulk response
                # carries every register, and processing unrequested ones would feed
                # the per-family decoders components they don't expect.
                wanted = set(components_to_scan)
                self._log_unmapped_components(device_id, bulk, wanted)
                return {cid: state for cid, state in bulk.items() if cid in wanted}

            failures = self._bulk_fetch_failures.get(device_id, 0) + 1
            self._bulk_fetch_failures[device_id] = failures
            if failures == BULK_FETCH_FAILURE_LIMIT:
                _LOGGER.info(
                    "Bulk component fetch failed %d times in a row for device %s — "
                    "using per-component polling for it for the rest of this session",
                    failures,
                    device_id,
                )

        return await self._fetch_components_parallel(device_id, components_to_scan)

    def _log_unmapped_components(self, device_id: str, bulk: dict[int, dict[str, Any]], wanted: set[int]) -> None:
        """Log, once per device, the registers the bulk response carries but no profile maps.

        Adding support for a missing feature (a boost mode, an aux output, a
        schedule slot) means finding which register carries it, and the bulk
        endpoint already returns every one of them — they're just filtered out.
        Dumping the unmapped ones at DEBUG turns "enable debug logging and toggle
        the feature in the app" into a complete answer, instead of asking users
        for repeated diagnostics exports that only ever contain the registers we
        already know about (Issues #174/#175, where every requested register was
        unrelated to the missing boost/aux/schedule features).
        """
        if not _LOGGER.isEnabledFor(logging.DEBUG) or device_id in self._unmapped_logged:
            return
        self._unmapped_logged.add(device_id)
        unmapped = {cid: bulk[cid].get("reportedValue") for cid in sorted(bulk) if cid not in wanted}
        if unmapped:
            _LOGGER.debug(
                "Device %s reports %d component(s) no profile maps — toggle a missing "
                "feature in the Fluidra app and compare to find its register: %s",
                device_id,
                len(unmapped),
                unmapped,
            )

    async def _fetch_components_parallel(
        self, device_id: str, components_to_scan: list[int]
    ) -> dict[int, dict[str, Any]]:
        """Fetch multiple component states in parallel for a device.

        Returns a dict mapping component_id to component_state.
        """
        # Cap concurrency so we don't trip rate limits or exhaust the connector.
        semaphore = asyncio.Semaphore(10)

        async def fetch_one(component_id: int) -> tuple[int, dict[str, Any] | None]:
            async with semaphore:
                state = await self.api.get_component_state(device_id, component_id)
                return (component_id, state)

        tasks = [fetch_one(cid) for cid in components_to_scan]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        component_states: dict[int, dict[str, Any]] = {}
        for result in results:
            if isinstance(result, BaseException):
                # Keep a diagnostic trace instead of silently dropping the failure.
                _LOGGER.debug("Component fetch failed for device %s: %s", device_id, result)
                continue
            cid, state = result
            if state and isinstance(state, dict):
                component_states[cid] = state
        return component_states

    def _process_component_state(
        self, device: dict[str, Any], pool_id: str, component_id: int, component_state: dict[str, Any]
    ) -> None:
        """Process a single component state and update device data.

        Extracted from _async_update_data to reduce code duplication.
        """
        reported_value = component_state.get("reportedValue")
        raw_device_id = device.get("device_id")
        device_id = str(raw_device_id) if raw_device_id is not None else ""

        # Store ALL component data.
        device["components"][str(component_id)] = component_state

        # Victoria Smart Connect VS pumps use a string-based register layout on
        # c9-c24 that collides with the shared numeric mappings below (c9/c10 are
        # dead 0s that would stomp the state, c20 is a preset slot rather than
        # schedules, c21 is the live speed % rather than network status), so the
        # whole pump window is dispatched to its own decoder (Issue #144).
        if component_id >= 9 and DeviceIdentifier.has_feature(device, "victoria_vs_mode"):
            self._process_victoria_component(device, component_id, component_state)
            return

        # Info-component layout. Fluidra's standard slots are 0=device-id,
        # 1=part-numbers, 2=signal/RSSI, 3=firmware. The Blue Connect (BC3)
        # reorders them — 0=RSSI, 1=serial, 2=hardware-UID — which made the
        # device-info sensor show the signal as the device id and vice versa
        # (Issue #69). "info_layout" lets a family override the mapping while
        # everyone else keeps the default.
        bc_info_layout = DeviceIdentifier.get_feature(device, "info_layout") == "blue_connect"

        # The iQ heat pumps share a second layout — 0=running hours, 1=RSSI,
        # 2=IP address, 3=serial, 4=firmware. Confirmed on a Z650iQ (PR #180)
        # and on a live Z250iQ dump: "c1: Wi-Fi RSSI", "c2: IP address",
        # "c3: serial number", "c4: firmware version" (Issue #139, @Kal42).
        # Reading c2 as the RSSI there yields the IP string, which is why the
        # signal sensor reported unknown on a Z250iQ (Issue #183, @paradox37).
        iq_info_layout = DeviceIdentifier.has_feature(device, "z650iq_mode") or (
            DeviceIdentifier.get_feature(device, "info_layout") == "iq_heat_pump"
        )

        if component_id == 0:
            if bc_info_layout:
                if not DeviceIdentifier.has_feature(device, "skip_signal"):
                    device["signal_strength_component"] = reported_value
            elif not iq_info_layout:
                # Z650iQ uses c0 for running hours, not a device-id string.
                device["device_id_component"] = reported_value
            if DeviceIdentifier.has_feature(device, "z260iq_mode") and reported_value is not None:
                try:
                    device["running_hours"] = int(reported_value)
                except (ValueError, TypeError):
                    pass
        elif component_id == 1:
            if bc_info_layout:
                device["device_id_component"] = reported_value
            elif iq_info_layout:
                # Z650iQ uses c1 for WiFi RSSI (standard layout uses c2).
                if not DeviceIdentifier.has_feature(device, "skip_signal"):
                    device["signal_strength_component"] = reported_value
            else:
                device["part_numbers_component"] = reported_value
        elif component_id == 2:
            if bc_info_layout:
                # Hardware UID on Blue Connect — not an RSSI value.
                device["part_numbers_component"] = reported_value
            elif iq_info_layout:
                # Z650iQ c2 is empty — c1 already holds the RSSI, don't overwrite.
                pass
            elif not DeviceIdentifier.has_feature(device, "skip_signal"):
                device["signal_strength_component"] = reported_value
        elif component_id == 3:
            if iq_info_layout:
                # Z650iQ: c3 is the device serial number, not firmware (c4 = firmware).
                device["device_id_component"] = reported_value
            elif not DeviceIdentifier.has_feature(device, "skip_firmware"):
                device["firmware_version_component"] = reported_value
        elif component_id == 4:
            if iq_info_layout:
                # Z650iQ uses c4 for firmware version (standard layout uses c3).
                if not DeviceIdentifier.has_feature(device, "skip_firmware"):
                    device["firmware_version_component"] = reported_value
            elif bc_info_layout:
                # Blue Connect carries two firmwares — c3 is the nRF one, c4 the
                # ESP32 one, both shown on the app's Information page. Labelling
                # c4 as hardware errors surfaced a version string as a fault
                # (Issue #186, @Kal42).
                device["secondary_firmware_component"] = reported_value
            else:
                device["hardware_errors_component"] = reported_value
        elif component_id == 5:
            device["comm_errors_component"] = reported_value
        elif component_id == 9:
            device["pump_reported"] = reported_value
            device["pump_desired"] = component_state.get("desiredValue")
            device["is_running"] = bool(reported_value)
            # Not the heat-pump on/off flag on the Z650iQ — see that profile's
            # register map in device_registry/configs/heat_pumps.py.
        elif component_id == 10:
            device["auto_reported"] = reported_value
            device["auto_desired"] = component_state.get("desiredValue")
            device["auto_mode_enabled"] = bool(reported_value)
            # Z650iQ: c10 is the heat-pump on/off state (1=on, 0=off), not
            # c9 or c13. Kept in sync with the write path through the
            # profile's `on_off_component`.
            if (
                DeviceIdentifier.has_feature(device, "z650iq_mode")
                and device.get("type", "").lower() == DEVICE_TYPE_HEAT_PUMP
            ):
                device["heat_pump_reported"] = reported_value
                device["is_heating"] = bool(reported_value)
        elif component_id == 11:
            device["speed_level_reported"] = reported_value
            device["speed_level_desired"] = component_state.get("desiredValue")
            if not device.get("is_running", False):
                device["speed_percent"] = 0
            else:
                auto_mode = device.get("auto_mode_enabled", False)
                if auto_mode:
                    device["speed_percent"] = calculate_auto_speed_from_schedules(device)
                elif isinstance(reported_value, int):
                    device["speed_percent"] = PUMP_SPEED_PERCENTAGES.get(reported_value, 0)
                else:
                    device["speed_percent"] = 0
        elif component_id == 12:
            device["component_12_data"] = component_state
            # Only use c12 as the setpoint when the device config designates it
            # as such (e.g. Z650iQ with setpoint_component=12). Other heat pumps
            # use c15 — reading c12 as a temperature for them would be wrong.
            if (
                device.get("type", "").lower() == DEVICE_TYPE_HEAT_PUMP
                and DeviceIdentifier.get_feature(device, "setpoint_component", COMPONENT_HEAT_PUMP_SETPOINT) == 12
                and reported_value is not None
            ):
                try:
                    temp_val = float(reported_value) / 10.0
                    if 10.0 <= temp_val <= 50.0:
                        device["target_temperature"] = temp_val
                except (ValueError, TypeError):
                    pass
        elif component_id == 13:
            device["component_13_data"] = component_state
            if device.get("type", "").lower() == DEVICE_TYPE_HEAT_PUMP and not DeviceIdentifier.has_feature(
                device, "z550_mode"
            ):
                if DeviceIdentifier.has_feature(device, "z650iq_mode"):
                    # Z650iQ: c13 is the water temperature (decidegrees), not an
                    # on/off flag — writing 0/1 here would corrupt the reading.
                    if reported_value is not None:
                        try:
                            temp_val = float(reported_value) / 10.0
                            if 5.0 <= temp_val <= 50.0:
                                device["water_temperature"] = temp_val
                        except (ValueError, TypeError):
                            pass
                else:
                    # Z260iQ family: c13 is the on/off control (0=off, 1=on).
                    device["heat_pump_reported"] = reported_value
                    device["is_heating"] = bool(reported_value)
                    if reported_value is not None:
                        try:
                            temp_val = float(reported_value) / 10.0
                            if 5.0 <= temp_val <= 50.0:
                                device["water_temperature"] = temp_val
                        except (ValueError, TypeError):
                            pass
        elif component_id == 14:
            device["component_14_data"] = component_state
            if DeviceIdentifier.has_feature(device, "z260iq_mode") and reported_value is not None:
                try:
                    device["z260iq_mode_value"] = int(reported_value)
                except (ValueError, TypeError):
                    pass
        elif component_id == 15:
            desired_value = component_state.get("desiredValue")
            # Use `is not None` (not `or`) so a legitimate reported 0 is preserved
            # instead of silently falling back to desiredValue.
            raw_value = reported_value if reported_value is not None else desired_value
            device["component_15_speed"] = raw_value if raw_value is not None else 0
            temp_raw = raw_value
            # Skip if this device uses a different setpoint component (e.g. Z650iQ uses c12).
            if (
                device.get("type", "").lower() == DEVICE_TYPE_HEAT_PUMP
                and DeviceIdentifier.get_feature(device, "setpoint_component", COMPONENT_HEAT_PUMP_SETPOINT)
                == COMPONENT_HEAT_PUMP_SETPOINT
                and temp_raw is not None
            ):
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
            if device.get("type", "").lower() == DEVICE_TYPE_HEAT_PUMP and reported_value:
                try:
                    water_temp_value = float(reported_value) / 10.0
                    if 5.0 <= water_temp_value <= 50.0:
                        device["water_temperature"] = water_temp_value
                except (ValueError, TypeError):
                    pass
        elif component_id == 20:
            device_type = device.get("type", "")
            if device_type == DEVICE_TYPE_CHLORINATOR:
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
                # Z550iQ: c40 = outdoor air temperature.
                try:
                    air_temp = float(reported_value) / 10.0
                    if -20.0 <= air_temp <= 60.0:
                        device["air_temperature"] = air_temp
                except (ValueError, TypeError):
                    pass
            else:
                # Left undecoded for the Z650iQ: c40 is the evaporator
                # temperature there, and c44 carries its outdoor air reading.
                config = DeviceIdentifier.identify_device(device)
                device_type = config.device_type if config else device.get("type", "")
                if device_type == DEVICE_TYPE_LIGHT:
                    schedule_data = reported_value if isinstance(reported_value, list) else []
                    device["schedule_data"] = schedule_data
                    self._track_schedule_count(pool_id, device_id, schedule_data)
        elif component_id == 48 and DeviceIdentifier.has_feature(device, "z650iq_mode"):
            device["component_48_data"] = component_state
            # Instantaneous power draw in Watts. Not meter-verified — see the
            # profile's register map for the confidence note.
            if isinstance(reported_value, (int, float)) and not isinstance(reported_value, bool):
                device["pump_power"] = int(reported_value)
        elif component_id == 32 and DeviceIdentifier.has_feature(device, "z650iq_mode"):
            device["component_32_data"] = component_state
            # Compressor modulation level in percent — see the profile's
            # register map for the confidence note.
            if isinstance(reported_value, (int, float)) and not isinstance(reported_value, bool):
                device["compressor_modulation"] = int(reported_value)
        elif component_id == 44 and DeviceIdentifier.has_feature(device, "z650iq_mode"):
            device["component_44_data"] = component_state
            # Outdoor air on the Z650iQ — c40 is the evaporator temperature there.
            if reported_value is not None:
                try:
                    air_temp = float(reported_value) / 10.0
                    if -30.0 <= air_temp <= 60.0:
                        device["air_temperature"] = air_temp
                except (ValueError, TypeError):
                    pass
        elif component_id == 60:
            device["component_60_data"] = component_state
            # Z550iQ+ total running hours (raw integer h, matches
            # status.totalRunningHours) — surfaced as a sensor (Issue #88).
            if DeviceIdentifier.has_feature(device, "z550_mode") and reported_value is not None:
                try:
                    device["running_hours"] = int(reported_value)
                except (ValueError, TypeError):
                    pass
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
            if device.get("type", "").lower() == DEVICE_TYPE_HEAT_PUMP and reported_value:
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
        elif component_id == 75:
            device["component_75_data"] = component_state
            if DeviceIdentifier.has_feature(device, "z260iq_mode") and reported_value is not None:
                # Live compressor state, which the mode register (c14) cannot
                # give: c14 says what the unit was *told* to do, c75 says what
                # it is *doing*. Confirmed on a Z250iQ by driving Boost/Silent
                # and Heating/Cooling: 0=idle, 16=heating, 24=cooling. A value
                # of 8 was seen for a few seconds right after a mode change,
                # so unknown values fall through to the mode-derived action
                # rather than being guessed at (Issue #139, @Kal42).
                try:
                    device["compressor_state"] = int(reported_value)
                except (ValueError, TypeError):
                    pass
        elif component_id == 39:
            device["component_39_data"] = component_state
            if reported_value is not None:
                if DeviceIdentifier.has_feature(device, "z650iq_mode"):
                    # Compressor runtime while actively heating — not wall-clock
                    # hours since power-on (that is c0 / running_hours).
                    try:
                        device["compressor_running_hours"] = int(reported_value)
                    except (ValueError, TypeError):
                        pass
                elif DeviceIdentifier.has_feature(device, "z260iq_mode"):
                    # Z260iQ-family error E13: intake air above ~43 °C, which
                    # makes the unit refuse to run (0=OK, 1=error). Identified
                    # on a Z250iQ by @Kal42 (Issue #139).
                    try:
                        device["air_temperature_alarm"] = int(reported_value) != 0
                    except (ValueError, TypeError):
                        pass
        elif component_id == 67:
            device["component_67_data"] = component_state
            # Air temperature on the Z260iQ family (incl. the Z250iQ, which
            # shares the full register layout since Issue #139).
            air_temp_model = DeviceIdentifier.has_feature(device, "z260iq_mode")
            if air_temp_model and reported_value is not None:
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

    def _apply_resolved_schedule(self, device: dict[str, Any], pool_id: str, device_id: str) -> None:
        """Point schedule_data at the register the device is currently honouring.

        The eXO iQ keeps chlorination-only schedules on c19, simple-pump ones on
        c20 and variable-speed ones on c21, honouring only the one matching its
        configured pump. Stale entries survive a configuration change, so the
        live register is identified from the pump-type flags rather than from
        which register happens to hold data (Issue #174, @Inervo).
        """
        component_id = resolve_schedule_component(device)
        reported = device.get("components", {}).get(str(component_id), {}).get("reportedValue")

        if isinstance(reported, dict) and "programs" in reported:
            schedule_data = parse_dm24049704_schedule_format(reported)
        elif isinstance(reported, list):
            schedule_data = reported
        else:
            schedule_data = []

        device["schedule_data"] = schedule_data
        device["schedule_component_resolved"] = component_id
        self._track_schedule_count(pool_id, device_id, schedule_data)

    def _process_victoria_component(
        self, device: dict[str, Any], component_id: int, component_state: dict[str, Any]
    ) -> None:
        """Decode one Victoria Smart Connect VS pump register (Issue #144).

        Layout established from @renaatski's captures (running / stopped / flow /
        speed / 95 % / 100 %) and later a full traffic capture of the write path:
        the pump reports state and mode as strings (c14/c16/c18), the setpoint on
        c17, the live output % on c21, the flow rate on c25 (m³/h), power on c22
        (W) and head on c24 (cm — the HMI's "m" ×100), plus the hardware speed /
        flow limits on c42-c45. c13/c23 are still undeciphered; their raw values
        are kept in ``components``.
        """
        reported_value = component_state.get("reportedValue")
        device[f"component_{component_id}_data"] = component_state

        def _num(value: Any) -> int | float | None:
            """Return value if it's a real number (not a bool), else None."""
            return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None

        if component_id == 13:
            # c13 is the auto-schedule armed flag — the true state of the Auto
            # toggle, independent of the current running mode. A quick function
            # runs with c16="QUICK FUNCTION" while c13 stays 1, so Auto must be
            # derived from c13, not c16 (Issue #144, @renaatski: HA showed Auto
            # off while c13=1 during a quick function).
            auto = bool(reported_value)
            device["auto_reported"] = auto
            device["auto_mode_enabled"] = auto
        elif component_id == 14:
            if isinstance(reported_value, str):
                # PRIMING is the self-priming spin-up — the motor is turning, so it
                # counts as running (only NOT RUNNING / STOP mean stopped).
                is_running = reported_value.strip().upper() in ("RUNNING", "PRIMING")
                device["pump_reported"] = is_running
                device["is_running"] = is_running
        elif component_id == 16:
            if isinstance(reported_value, str):
                # c16 is the current running mode (AUTO / QUICK FUNCTION / STOP /
                # AUTO PRIMING / PUMP CALIBRATION) — info only. It does NOT drive the
                # Auto toggle any more; that's c13 above.
                device["pump_mode"] = reported_value
        elif component_id == 17:
            num = _num(reported_value)
            if num is not None:
                device["pump_setpoint"] = num
        elif component_id == 18:
            if isinstance(reported_value, str):
                device["pump_setpoint_type"] = reported_value
        elif component_id == 19:
            # Id/slug of the scheduler entry currently executing ("s0", a UUID, …),
            # "0" when idle. This is the join key onto /schedulers — the only place
            # a schedule-driven run's name and target exist (Issue #144, @renaatski).
            if reported_value not in (None, "", "0", 0):
                device["pump_active_schedule_id"] = str(reported_value)
            else:
                device.pop("pump_active_schedule_id", None)
        elif component_id == 20:
            if isinstance(reported_value, int) and not isinstance(reported_value, bool):
                device["pump_preset_slot"] = reported_value
        elif component_id == 21:
            num = _num(reported_value)
            if num is not None:
                device["speed_percent"] = max(0, min(100, int(num)))
        elif component_id == 22:
            num = _num(reported_value)
            if num is not None:
                device["pump_power"] = int(num)
        elif component_id == 24:
            num = _num(reported_value)
            if num is not None:
                device["pump_head"] = round(float(num) / 100.0, 2)
        elif component_id == 25:
            num = _num(reported_value)
            if num is not None:
                device["pump_flow"] = round(float(num), 1)
        elif component_id in (27, 28, 29):
            # Speed-preset dry-contact digital inputs, confirmed on-device by
            # @renaatski (Issue #144): c29 = Low (50 %), c28 = Medium (75 %),
            # c27 = High (100 %). Active (1) only when an external relay is wired
            # to that terminal (e.g. an ice-guard interlock), so surfaced as speed
            # attributes rather than always-off entities. NB these IDs mean other
            # things on other families (c28 is the no-flow alarm on the Z260iQ),
            # hence they're only decoded here under victoria_vs_mode.
            tier = {29: "low", 28: "medium", 27: "high"}[component_id]
            device[f"pump_speed_input_{tier}"] = bool(reported_value)
        elif component_id == 42:
            num = _num(reported_value)
            if num is not None:
                device["pump_speed_min"] = int(num)
        elif component_id == 43:
            num = _num(reported_value)
            if num is not None:
                device["pump_speed_max"] = int(num)
        elif component_id == 44:
            num = _num(reported_value)
            if num is not None:
                device["pump_flow_min"] = int(num)
        elif component_id == 45:
            num = _num(reported_value)
            if num is not None:
                device["pump_flow_max"] = int(num)
        elif VICTORIA_PRESET_FIRST <= component_id <= VICTORIA_PRESET_LAST:
            # Quick-function preset slots, stored as consolidated JSON objects
            # ({"name", "mode", "setpoint", "duration"}) — the same shape as c135.
            # @renaatski also decoded the equivalent scalar range (c89-c124) but
            # flagged a field-offset inconsistency there (c92 vs c126 disagree on
            # Clean's duration), so the objects are the trustworthy source.
            # An empty slot reports null, matching the app's "Not set up" tiles.
            presets = device.setdefault("pump_presets", {})
            index = component_id - VICTORIA_PRESET_FIRST
            if isinstance(reported_value, dict):
                presets[index] = reported_value
            else:
                presets.pop(index, None)
        elif component_id == 135:
            # Active quick-function / preset profile, e.g.
            # {"duration": "P0", "mode": "SPEED", "name": "MID SPEED", "setpoint": 75}.
            # Populated only for quick functions and numbered presets — a schedule-driven
            # AUTO run never touches it, so the value goes stale and must be ignored
            # unless the pump is actually in QUICK FUNCTION (Issue #144, @renaatski).
            if isinstance(reported_value, dict):
                device["pump_quick_function"] = reported_value
        elif component_id == 136:
            # Expiry timestamp (unix epoch) of a *timed* quick function; 0 when the
            # active function is untimed ("duration": "P0") or nothing is running.
            num = _num(reported_value)
            if num is not None:
                device["pump_quick_function_expiry"] = int(num) or None

    def _track_schedule_count(self, pool_id: str, device_id: str, schedule_data: list[dict[str, Any]]) -> None:
        """Track schedule count changes for cleanup."""
        device_key = f"{pool_id}_{device_id}"
        # Cleanup happens asynchronously in a separate task to avoid blocking.
        self._previous_schedule_entities[device_key] = len(schedule_data)

    def _note_update_failure(self) -> None:
        """Record a failed poll cycle and raise the connection repair issue when persistent."""
        self._consecutive_update_failures += 1
        if self._consecutive_update_failures == CONNECTION_ISSUE_THRESHOLD:
            _LOGGER.warning(
                "%d consecutive failed poll cycles — creating connection_error repair issue",
                self._consecutive_update_failures,
            )
            async_create_connection_issue(self.hass)

    def _handle_update_success(self) -> None:
        """Reset the failure streak and clear the connection repair issue if raised."""
        if self._consecutive_update_failures >= CONNECTION_ISSUE_THRESHOLD:
            async_delete_connection_issue(self.hass)
        self._consecutive_update_failures = 0

    def _sync_device_firmware(self, pools: list[dict[str, Any]]) -> None:
        """Mirror reported firmware versions into the HA device registry (no-op if unchanged)."""
        registry = dr.async_get(self.hass)
        for pool in pools:
            for device in pool.get("devices", []):
                device_id = device.get("device_id")
                firmware = device.get("firmware_version_component")
                if not device_id or firmware is None:
                    continue
                entry = registry.async_get_device(identifiers={(DOMAIN, str(device_id))})
                if entry is not None and entry.sw_version != str(firmware):
                    registry.async_update_device(entry.id, sw_version=str(firmware))

    async def _async_update_data(self) -> dict[str, Any]:
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

            # Defensively drop malformed pools without an id so a single bad entry
            # can't crash the whole update with a KeyError (it would otherwise send
            # the entry to SETUP_RETRY instead of degrading gracefully).
            valid_pools = [pool for pool in pools if pool.get("id") is not None]
            if len(valid_pools) != len(pools):
                _LOGGER.warning("Ignoring %d Fluidra pool(s) without an id", len(pools) - len(valid_pools))
            pools = valid_pools

            # Fast startup: minimal data on first update.
            if self._first_update:
                self._first_update = False
                self._handle_update_success()
                return {pool["id"]: pool for pool in pools}

            previous_data = self.data if isinstance(self.data, dict) else {}

            # Process each pool, isolating failures so one broken pool does not
            # make the whole integration unavailable.
            failed_pool_refreshes = 0
            for pool in pools:
                try:
                    await self._refresh_pool(pool, previous_data)
                except (aiohttp.ClientError, TimeoutError, FluidraError) as err:
                    _LOGGER.warning(
                        "Failed to refresh pool %s, keeping previous data: %s",
                        pool.get("id"),
                        err,
                    )
                    failed_pool_refreshes += 1
                    prev_pool = previous_data.get(pool["id"])
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

            # Only reconcile the registry when the fetch actually returned pools. A
            # transient empty response would otherwise purge every device+entity
            # (and their history) on a single cloud hiccup, until the next restart.
            if pools:
                await self._cleanup_removed_devices(current_device_ids)

            # Every pool failed to refresh: entities keep their previous data
            # (graceful degradation) but the outage still counts towards the
            # connection_error repair issue so the user learns about it.
            if pools and failed_pool_refreshes == len(pools):
                self._note_update_failure()
            else:
                self._handle_update_success()
                self._sync_device_firmware(pools)

            return {pool["id"]: pool for pool in pools}

        except ConfigEntryAuthFailed:
            raise
        except (aiohttp.ClientError, TimeoutError, FluidraError) as err:
            _LOGGER.exception("Error updating Fluidra Pool data")
            self._note_update_failure()
            raise UpdateFailed(f"Error communicating with API: {type(err).__name__}") from err

    def _apply_online_flag(self, device: dict[str, Any], device_id: str, connected: bool) -> None:
        """Debounce the cloud connectivity flag before it gates availability.

        The Fluidra heartbeat routinely misreports a healthy device as
        disconnected for a single poll (Issue #140 — same unreliable flag as
        Issue #63), which flipped every entity of the device unavailable for
        one scan interval. Mark a device offline only after
        OFFLINE_GRACE_POLLS consecutive offline reports; recover immediately
        on the first online report.
        """
        if connected:
            device["online"] = True
            self._offline_poll_counts.pop(device_id, None)
            return
        strikes = self._offline_poll_counts.get(device_id, 0) + 1
        self._offline_poll_counts[device_id] = strikes
        if strikes >= OFFLINE_GRACE_POLLS:
            device["online"] = False

    async def _refresh_pool(self, pool: dict[str, Any], previous_data: dict[str, Any]) -> None:
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

        # Determine whether the account can actually control this pool. A viewer
        # (read-only) contract makes the backend accept control writes with a
        # fake HTTP 200 that echoes the requested value but never persists it, so
        # commands silently have no effect (Issue #129). Surface it and warn once.
        access_level = determine_pool_access(pool, self.api.user_id)
        pool["access_level"] = access_level
        if access_level == "viewer" and pool_id not in self._read_only_pools_warned:
            self._read_only_pools_warned.add(pool_id)
            _LOGGER.warning(
                "Account has viewer (read-only) access to pool %s: control commands "
                "(setpoints, switches) are accepted by the Fluidra cloud but not applied. "
                "Owner-level access is required to change settings",
                pool.get("name", pool_id),
            )

        if isinstance(water_quality_result, dict):
            pool["water_quality"] = water_quality_result

        # Pool automations, needed to describe a schedule-driven run (Issue #144).
        # Only fetched when a device of this pool actually uses them, so pools
        # without such equipment don't pay an extra request per poll.
        if any(DeviceIdentifier.has_feature(device, "uses_pool_schedulers") for device in pool.get("devices", [])):
            schedulers = await self.api.get_pool_schedulers(pool_id)
            if isinstance(schedulers, list):
                pool["schedulers"] = schedulers

        # Preserve previous component and alarm data with deep copy to avoid
        # aliasing. Alarms are seeded from the last known state so that a
        # failed poll_pool_device_statuses call, or a status tree that omits
        # this device for a cycle, doesn't overwrite an active alarm with an
        # empty list -- it's overwritten below with fresh data whenever the
        # status poll actually returns something for this device.
        for device in pool.get("devices", []):
            device_id = device.get("device_id")
            if device_id and device_id in prev_devices_by_id:
                prev_device = prev_devices_by_id[device_id]
                if "components" in prev_device:
                    device["components"] = copy.deepcopy(prev_device["components"])
                if "alarms" in prev_device:
                    device["alarms"] = copy.deepcopy(prev_device["alarms"])

        devices_with_ids = [(d, d.get("device_id")) for d in pool.get("devices", []) if d.get("device_id")]
        if devices_with_ids:
            # One tree fetch per pool: the endpoint returns every device of the
            # pool, so per-device polling was N identical requests (Issue #140).
            try:
                statuses = await self.api.poll_pool_device_statuses(pool_id)
            except FluidraError as err:
                _LOGGER.debug("Device status poll failed for pool %s: %s", pool_id, err)
                statuses = None

            if statuses:
                for device, device_id in devices_with_ids:
                    status = statuses.get(device_id)
                    if isinstance(status, dict):
                        device["status"] = status
                        connectivity = status.get("connectivity", {})
                        device["connectivity"] = connectivity
                        if "connected" in connectivity:
                            self._apply_online_flag(device, device_id, bool(connectivity["connected"]))
                        # Device alarms (e.g. "PUMPSTOP PH") live only in this raw
                        # status tree, not in any numbered component — the
                        # specific_components scan never sees them (Piscina finding,
                        # 2026-07-10/11). Surface the raw list as-is for entities to
                        # interpret; an empty list means no active alarms.
                        device["alarms"] = status.get("alarms", [])

        chronically_stuck: list[str] = []
        any_device_got_data = False

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
            component_states = await self._fetch_components(device_id, components_to_scan)

            if components_to_scan and not component_states:
                strikes = self._empty_component_fetch_counts.get(device_id, 0) + 1
                self._empty_component_fetch_counts[device_id] = strikes
                if strikes >= EMPTY_COMPONENT_FETCH_THRESHOLD:
                    # Every individual component request failed again for this
                    # device. Don't raise here directly: some pools contain a
                    # mix of real hardware and virtual/manual-input pseudo
                    # devices (e.g. a photo-based test-strip entry) that never
                    # answer component polls by design — raising per-device
                    # would abort the whole pool's refresh and freeze healthy
                    # devices right along with the permanently-stuck one.
                    # Instead, collect it here and only fail the pool below if
                    # *every* device struck out this cycle, which is the actual
                    # "nothing is coming through" signal (e.g. the DNS outage
                    # this was built for) rather than one quirky device.
                    if strikes == EMPTY_COMPONENT_FETCH_THRESHOLD:
                        # Log once, right when it crosses the threshold — not on
                        # every subsequent poll, or a permanently-stuck device
                        # (like the test-strip one above) spams a warning forever.
                        _LOGGER.warning(
                            "No component data received for device %s after %d consecutive "
                            "polls — device may be unreachable or not support live polling",
                            device_id,
                            strikes,
                        )
                    chronically_stuck.append(device_id)
            else:
                self._empty_component_fetch_counts.pop(device_id, None)

            if component_states:
                any_device_got_data = True

            for component_id, component_state in component_states.items():
                self._process_component_state(device, pool_id, component_id, component_state)

            # Re-read the schedules from the register the device actually
            # honours, AFTER the whole scan: the pump-type flags (c82/c83) may
            # be processed after the schedule registers themselves, so the
            # inline branch above can resolve against flags that are not in
            # yet. Only profiles declaring schedule_component_map are affected;
            # everyone else keeps the single fixed register (Issue #174).
            if DeviceIdentifier.get_feature(device, "schedule_component_map", None):
                self._apply_resolved_schedule(device, pool_id, device_id)

            # Recompute auto-mode pump speed AFTER the whole component scan: the
            # speed (component 11) is processed before the schedule (component 20)
            # in scan order, so the inline calculation at component 11 would read a
            # stale/empty schedule_data. Redo it once schedule_data is populated.
            # Victoria VS pumps are excluded: they report the live output % on c21
            # even under a schedule, which beats deriving it from schedule slots.
            if device.get("auto_mode_enabled", False) and not DeviceIdentifier.has_feature(device, "victoria_vs_mode"):
                device["speed_percent"] = (
                    calculate_auto_speed_from_schedules(device) if device.get("is_running", False) else 0
                )

            # Flag devices resolved on a catch-all/legacy profile whose component
            # mapping is a best guess (Issue #73 and friends): the entities are
            # still created, but a repair issue tells the user their readings
            # may be wrong and how to get an exact profile added.
            config = DeviceIdentifier.identify_device(device)
            if config is not None and not config.verified:
                if device_id not in self._unverified_profile_flagged:
                    self._unverified_profile_flagged.add(device_id)
                    async_create_unverified_profile_issue(self.hass, device_id, str(device.get("name", device_id)))
            elif device_id in self._unverified_profile_flagged:
                # A code update added a verified profile for this device.
                self._unverified_profile_flagged.discard(device_id)
                async_delete_unverified_profile_issue(self.hass, device_id)

        if chronically_stuck and not any_device_got_data:
            # Every device in the pool struck out this cycle — that's the
            # "nothing is coming through" signal (transient DNS/API outage,
            # auth issue, etc.), as opposed to a single chronically-quirky
            # device. Propagate so it reaches _async_update_data's per-pool
            # handler and eventually the connection_error repair issue,
            # instead of freezing silently forever.
            raise FluidraConnectionError(
                f"No component data received for any device in pool {pool_id} (stuck: {', '.join(chronically_stuck)})"
            )
