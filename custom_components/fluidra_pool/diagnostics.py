"""Diagnostics support for Fluidra Pool integration."""

from __future__ import annotations

import re
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from .const import FluidraPoolConfigEntry

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
        "pools": _redact_pools_data(coordinator_data),
    }


def _redact_pools_data(pools_data: dict) -> dict:
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
                elif isinstance(value, dict):
                    redacted_pool[key] = async_redact_data(value, TO_REDACT)
                else:
                    redacted_pool[key] = value
        else:
            redacted_pool = pool_data

        redacted[redacted_pool_id] = redacted_pool

    return redacted


def _redact_devices_data(devices: list) -> list:
    """Redact sensitive information from devices data."""
    if not devices:
        return []

    redacted_devices = []
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


def _redact_component_data(component_id: Any, component: dict) -> dict:
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
