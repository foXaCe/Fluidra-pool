"""Diagnostics support for Fluidra Pool integration."""

from __future__ import annotations

import re
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from .api_resilience import FluidraAuthError
from .const import FluidraPoolConfigEntry
from .device_registry import DEVICE_CONFIGS, DeviceIdentifier
from .helpers import resolve_schedule_component
from .utils import mask_device_id

TO_REDACT = {
    CONF_EMAIL,
    CONF_PASSWORD,
    "email",
    "password",
    "access_token",
    "refresh_token",
    "id_token",
    "token",
    "serial_number",
    "serialNumber",
    "sn",
    "device_id",
    "deviceId",
    "macAddress",
    "mac_address",
    "latitude",
    "longitude",
    "lat",
    "lng",
    "lon",  # OpenWeather coord block uses "lon"/"lat".
    "location",
    "address",
    "alias",
    "bleAccessCode",
    "ble_access_code",
    "sessionIdentifier",
    "session_identifier",
    "ip",
    "ipAddress",
    "ip_address",
}
TO_REDACT_LOWER = {key.lower() for key in TO_REDACT}
REDACTED = "**REDACTED**"

# The raw device tree entry stored under device["status"] carries the device
# serial in its "id" field (and in nested bridge children under "devices[].id").
# "id" must NOT go into the global TO_REDACT: schedule/job ids would be lost.
TO_REDACT_STATUS = TO_REDACT | {"id"}

# Components that typically carry device identifiers (serial, MAC, IP, SKU).
# Their `reportedValue`/`desiredValue` strings are redacted regardless of the device
# family — these slots are reserved for telemetry-metadata on the Fluidra cloud:
#  0: signal strength (numeric, kept) OR running-hours (Z260iQ)
#  1: serial / part numbers (string identifier)
#  2: MAC / hardware UID
#  6: SKU / model identifier
#  7: thingType signature (BC3, BXWAA, …) — kept since useful for debugging
#  8: IP address (or masked by the cloud on some devices)
_IDENTIFIER_COMPONENT_IDS: frozenset[int | str] = frozenset({1, 2, 6, 8, "1", "2", "6", "8"})

# Device-extracted fields that mirror identifier components (set by the coordinator
# via `_process_component_state`). Their values must be redacted too.
_IDENTIFIER_DEVICE_FIELDS: frozenset[str] = frozenset(
    {
        "part_numbers_component",
        "signal_strength_component",
        "comm_errors_component",
        "device_id_component",
    }
)

# Patterns matching common identifier formats. Used when a value is a string but
# the surrounding key doesn't appear in TO_REDACT (defence in depth).
_IDENTIFIER_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$"),  # IPv4.
    re.compile(r"^[0-9A-Fa-f:]{17}$"),  # MAC (xx:xx:xx:xx:xx:xx).
    re.compile(r"^[A-Z]{2}\d{6,}[A-Z0-9]*$"),  # Fluidra serial (QX25002362, LC25000122, …).
    re.compile(r"^[A-Z]{3,}\d{10,}$"),  # Hardware UID (AXR080700451258659).
)


def _looks_like_identifier(value: Any) -> bool:
    """Return True when a string value matches a known identifier shape."""
    if not isinstance(value, str) or len(value) < 7:
        return False
    return any(pattern.match(value) for pattern in _IDENTIFIER_VALUE_PATTERNS)


def _redact_if_identifier(value: Any) -> Any:
    """Redact a value when it looks like an identifier, otherwise return as-is."""
    return REDACTED if _looks_like_identifier(value) else value


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: FluidraPoolConfigEntry) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data.coordinator

    coordinator_data = coordinator.data if coordinator.data else {}

    return {
        "config_entry": {
            "entry_id": entry.entry_id,
            "version": entry.version,
            "domain": entry.domain,
            "title": entry.title,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "update_interval": str(coordinator.update_interval),
        },
        # Control writes the cloud accepted and then discarded (Issue #133).
        # This is what turns "my setpoint keeps reverting" into a measurement:
        # the component, the value asked for, the value the device kept. Device
        # ids are already masked by the verifier and the values are setpoints
        # and modes, so there is nothing identifying left to redact.
        "lost_writes": list(getattr(coordinator, "lost_writes", [])),
        # What the cloud itself says about each device's schedule register,
        # next to what the profile resolved. Fetched here and nowhere else:
        # on the poll path it would cost one request per device and buy
        # nothing, but in a bug report it settles "my schedule writes land on
        # the wrong slot" outright (Issue #174).
        "cloud_schedulers": await _collect_scheduler_capabilities(coordinator, coordinator_data),
        # Every register of the devices whose profile is a guess. Without this
        # block a report about an unreported model can only ever contain the
        # registers the guess already reads (Issue #221).
        "unverified_devices": await _collect_unverified_device_registers(coordinator, coordinator_data),
        "pools": _redact_pools_data(coordinator_data),
    }


def _profile_name(config: Any) -> str:
    """Return the registry key of a resolved profile (for a bug report to quote)."""
    for name, candidate in DEVICE_CONFIGS.items():
        if candidate is config:
            return name
    return "unknown"


def _register_sort_key(item: tuple[Any, Any]) -> tuple[int, int, str]:
    """Sort registers numerically, tolerating a payload that keyed them as strings."""
    key = item[0]
    text = str(key)
    return (0, int(text), "") if text.lstrip("-").isdigit() else (1, 0, text)


def _device_thing_type(device: dict[str, Any]) -> str:
    """Read Fluidra's own family id the way the identifier does."""
    thing_type = str(device.get("thing_type", ""))
    if thing_type:
        return thing_type
    status = device.get("status")
    return str(status.get("thingType", "")) if isinstance(status, dict) else ""


async def _collect_unverified_device_registers(coordinator: Any, pools_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Dump every register of the devices resolved on an unverified profile.

    A catch-all profile scans only the handful of registers it guesses at, so
    ``device["components"]`` — and therefore the rest of this download — can
    never contain the register that actually carries the state. Issue #221 is
    the case in point: a Z350iQ reads OFF in Home Assistant because component 13
    is not its ON/OFF flag, and no diagnostics export could show which register
    is, because that register was never polled. The bulk endpoint returns all of
    them, so ask for it here — once per download, and only for the devices whose
    mapping is a guess. On the poll path it would cost a request per device per
    cycle and buy nothing (same reasoning as ``cloud_schedulers``).

    Never fails the diagnostics download: an endpoint that refuses, a device the
    cloud says nothing about, or an expired token all come back as a shorter list.
    """
    api = getattr(coordinator, "api", None)
    if api is None or not hasattr(api, "get_all_components"):
        return []

    collected: list[dict[str, Any]] = []
    for pool in pools_data.values():
        if not isinstance(pool, dict):
            continue
        for device in pool.get("devices", []):
            if not isinstance(device, dict):
                continue
            device_id = device.get("device_id")
            if not device_id:
                continue
            config = DeviceIdentifier.identify_device(device)
            if config is None or config.verified:
                continue
            try:
                bulk = await api.get_all_components(device_id)
            except (FluidraAuthError, OSError, TimeoutError):
                continue
            if not isinstance(bulk, dict):
                continue
            scanned = sorted(int(cid) for cid in device.get("components", {}) if str(cid).isdigit())
            collected.append(
                {
                    "device_id": mask_device_id(device_id),
                    # What the cloud calls this family — the single field that
                    # tells a maintainer which register map the unit should read.
                    "thing_type": _device_thing_type(device) or "unknown",
                    "profile": _profile_name(config),
                    "profile_verified": False,
                    # The gap between the two lists is the point of this block.
                    "scanned_components": scanned,
                    "all_registers": {
                        str(cid): _redact_component_data(cid, state)
                        for cid, state in sorted(bulk.items(), key=_register_sort_key)
                    },
                }
            )
    return collected


async def _collect_scheduler_capabilities(coordinator: Any, pools_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Compare the cloud-declared schedule register with the resolved one, per device.

    Never fails the diagnostics download: an endpoint that refuses, an account
    whose token expired mid-download, or a device the cloud says nothing about
    all come back as an empty or partial list.
    """
    api = getattr(coordinator, "api", None)
    if api is None or not hasattr(api, "get_device_capabilities"):
        return []

    collected: list[dict[str, Any]] = []
    for pool in pools_data.values():
        if not isinstance(pool, dict):
            continue
        for device in pool.get("devices", []):
            device_id = device.get("device_id")
            if not device_id:
                continue
            try:
                capabilities = await api.get_device_capabilities(device_id)
            except (FluidraAuthError, OSError, TimeoutError):
                continue

            resolved = resolve_schedule_component(device)
            for capability in capabilities:
                collected.append(
                    {
                        "device_id": mask_device_id(device_id),
                        "scheduler_id": capability.scheduler_id,
                        "type": capability.scheduler_type,
                        "enabled": capability.enabled,
                        "cloud_component_read": capability.component_read,
                        "cloud_component_write": capability.component_write,
                        "profile_resolved_component": resolved,
                        "matches_profile": capability.component_write == resolved,
                    }
                )
    return collected


def _redact_pools_data(pools_data: dict[str, Any]) -> dict[str, Any]:
    """Redact sensitive information from pools data."""
    if not pools_data:
        return {}

    redacted: dict[str, Any] = {}
    for pool_id, pool_data in pools_data.items():
        # Redact pool ID but keep structure.
        redacted_pool_id = f"pool_{hash(pool_id) % 10000:04d}"
        redacted_pool: Any = {}

        if isinstance(pool_data, dict):
            for key, value in pool_data.items():
                if key.lower() in TO_REDACT_LOWER:
                    redacted_pool[key] = REDACTED
                elif key == "devices":
                    redacted_pool["devices"] = _redact_devices_data(value)
                elif key == "water_quality":
                    # Water-quality telemetry is useful for debugging algorithms.
                    redacted_pool[key] = value
                elif key == "id":
                    # The pool id is already anonymised in the dict key
                    # (pool_XXXX) — keeping it in the value defeats that.
                    redacted_pool[key] = REDACTED
                elif isinstance(value, dict):
                    redacted_pool[key] = async_redact_data(value, TO_REDACT)
                else:
                    redacted_pool[key] = value
        else:
            redacted_pool = pool_data

        redacted[redacted_pool_id] = redacted_pool

    return redacted


def _redact_devices_data(devices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Redact sensitive information from devices data."""
    if not devices:
        return []

    redacted_devices: list[dict[str, Any]] = []
    for i, device in enumerate(devices):
        if isinstance(device, dict):
            redacted_device: dict[str, Any] = {}
            for key, value in device.items():
                if key.lower() in TO_REDACT_LOWER:
                    redacted_device[key] = REDACTED
                elif key == "components":
                    redacted_device["components"] = {
                        str(comp_id): _redact_component_data(comp_id, comp_data)
                        for comp_id, comp_data in (value or {}).items()
                    }
                elif key in _IDENTIFIER_DEVICE_FIELDS:
                    # Mirror of an identifier-bearing component — always redact strings.
                    redacted_device[key] = REDACTED if isinstance(value, str) else value
                elif key == "status" and isinstance(value, dict):
                    # Raw tree entry: its "id" (and children "devices[].id") is
                    # the device serial — redact it (async_redact_data recurses
                    # into nested lists/dicts).
                    redacted_device[key] = async_redact_data(value, TO_REDACT_STATUS)
                elif isinstance(value, dict):
                    redacted_device[key] = async_redact_data(value, TO_REDACT)
                elif isinstance(value, list):
                    redacted_device[key] = value
                else:
                    # Best-effort pattern-based redaction (IP/MAC/serial).
                    redacted_device[key] = _redact_if_identifier(value)

            redacted_device["_device_index"] = i
            redacted_devices.append(redacted_device)
        else:
            redacted_devices.append(device)

    return redacted_devices


def _redact_component_data(component_id: Any, component: dict[str, Any]) -> dict[str, Any]:
    """Redact a component dict.

    Telemetry components (pH, ORP, temperature, speed, schedules, …) are NOT
    sensitive and stay in clear so diagnostics remain useful for debugging
    device mappings.

    Components 0-8 (the "device info" slots) often carry serial numbers, MAC
    addresses, IPs and SKUs — those strings are redacted by component id.
    A defensive pattern check handles unexpected identifier shapes elsewhere.
    """
    if not isinstance(component, dict):
        return component

    is_identifier_slot = component_id in _IDENTIFIER_COMPONENT_IDS

    redacted: dict[str, Any] = {}
    for key, value in component.items():
        if key.lower() in TO_REDACT_LOWER:
            redacted[key] = REDACTED
        elif isinstance(value, dict):
            redacted[key] = async_redact_data(value, TO_REDACT)
        elif key in ("reportedValue", "desiredValue") and is_identifier_slot and isinstance(value, str):
            redacted[key] = REDACTED
        elif key in ("reportedValue", "desiredValue"):
            redacted[key] = _redact_if_identifier(value)
        else:
            redacted[key] = value
    return redacted
