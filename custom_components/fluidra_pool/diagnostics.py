"""Diagnostics support for Fluidra Pool integration."""

from __future__ import annotations

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
    "device_id",
    "deviceId",
    "macAddress",
    "mac_address",
    "latitude",
    "longitude",
    "lat",
    "lng",
    "location",
    "address",
}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: FluidraPoolConfigEntry) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data.coordinator

    # Get coordinator data
    coordinator_data = coordinator.data if coordinator.data else {}

    # Build diagnostics data
    diagnostics_data = {
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
            "optimistic_entities_count": len(coordinator._optimistic_entities),
        },
        "pools": _redact_pools_data(coordinator_data),
    }

    return diagnostics_data


def _redact_pools_data(pools_data: dict) -> dict:
    """Redact sensitive information from pools data."""
    if not pools_data:
        return {}

    redacted = {}
    for pool_id, pool_data in pools_data.items():
        # Redact pool ID but keep structure
        redacted_pool_id = f"pool_{hash(pool_id) % 10000:04d}"
        redacted_pool = {}

        if isinstance(pool_data, dict):
            for key, value in pool_data.items():
                # Skip sensitive keys
                if key.lower() in {k.lower() for k in TO_REDACT}:
                    redacted_pool[key] = "**REDACTED**"
                elif key == "devices":
                    # Redact device data
                    redacted_pool["devices"] = _redact_devices_data(value)
                elif key == "water_quality":
                    # Keep water quality data (not sensitive)
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
            redacted_device = {}
            for key, value in device.items():
                if key.lower() in {k.lower() for k in TO_REDACT}:
                    redacted_device[key] = "**REDACTED**"
                elif key == "components":
                    # Keep component IDs but redact values
                    redacted_device["components"] = {
                        str(comp_id): _redact_component_data(comp_data) for comp_id, comp_data in (value or {}).items()
                    }
                elif isinstance(value, dict):
                    redacted_device[key] = async_redact_data(value, TO_REDACT)
                elif isinstance(value, list):
                    redacted_device[key] = value
                else:
                    redacted_device[key] = value

            # Use index instead of real device ID
            redacted_device["_device_index"] = i
            redacted_devices.append(redacted_device)
        else:
            redacted_devices.append(device)

    return redacted_devices


def _redact_component_data(component: dict) -> dict:
    """Redact component data, keeping structure but hiding sensitive values."""
    if not isinstance(component, dict):
        return component

    # Keep component structure for debugging
    return {
        "reportedValue": component.get("reportedValue"),
        "desiredValue": component.get("desiredValue"),
        "timestamp": component.get("timestamp"),
    }
