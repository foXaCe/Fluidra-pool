"""
Fluidra Pool integration for Home Assistant.

This integration provides support for Fluidra Pool systems.
"""

import asyncio
from datetime import timedelta
import logging
from typing import Any, Dict, List

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
import voluptuous as vol

from .const import CONF_EMAIL, CONF_PASSWORD, DOMAIN
from .coordinator import FluidraDataUpdateCoordinator
from .fluidra_api import FluidraPoolAPI

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SWITCH,
    Platform.SENSOR,
    Platform.SELECT,  # Pour modes d'opération pompe (OFF/ON/AUTO/TURBO)
    Platform.NUMBER,  # Pour contrôle vitesse pompe E30iQ (0-100%)
    Platform.TIME,  # Pour édition des heures de programmation
    Platform.CLIMATE,  # Pour contrôle température pompes à chaleur
    Platform.LIGHT,  # Pour LumiPlus Connect et autres éclairages
]

UPDATE_INTERVAL = timedelta(seconds=45)  # Reduced frequency for better performance

# Service schemas
SERVICE_SET_SCHEDULE = "set_schedule"
SERVICE_CLEAR_SCHEDULE = "clear_schedule"
SERVICE_SET_PRESET_SCHEDULE = "set_preset_schedule"

SCHEDULE_SCHEMA = vol.Schema(
    {
        vol.Required("enabled"): cv.boolean,
        vol.Required("start_time"): cv.string,
        vol.Required("end_time"): cv.string,
        vol.Required("mode"): vol.In(["0", "1", "2"]),  # 0=Faible, 1=Moyenne, 2=Élevée
        vol.Optional("days", default=[0, 1, 2, 3, 4, 5, 6]): [vol.Range(min=0, max=6)],  # 0=Lundi, 6=Dimanche
    }
)

SET_SCHEDULE_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Required("schedules"): [SCHEDULE_SCHEMA],
    }
)

CLEAR_SCHEDULE_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
    }
)

SET_PRESET_SCHEDULE_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Required("preset"): vol.In(["standard", "intensive", "eco", "summer", "winter"]),
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Fluidra Pool from a config entry."""
    email = entry.data[CONF_EMAIL]
    password = entry.data[CONF_PASSWORD]

    # Initialize API client
    api = FluidraPoolAPI(email, password)

    try:
        # Test connection and authentication
        await api.authenticate()
        pools = await api.get_pools()

        if not pools:
            # Continue setup even without pools for now
            pass

    except Exception as err:
        _LOGGER.error("Unable to connect to Fluidra Pool API: %s", err)
        raise ConfigEntryNotReady from err

    # Create devices for each pool
    device_registry = dr.async_get(hass)
    for pool in pools:
        pool_id = pool.get("id")
        pool_name = pool.get("name", f"Pool {pool_id}")
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, pool_id)},
            name=pool_name,
            manufacturer="Fluidra",
            model="Pool",
        )

    # Create data update coordinator
    coordinator = FluidraDataUpdateCoordinator(hass, api, entry)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Set up platforms immediately (non-blocking)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Don't force first refresh - let it happen naturally on first update cycle
    # This prevents blocking during startup

    # Register services
    await _async_register_services(hass, coordinator)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


async def _async_register_services(hass: HomeAssistant, coordinator: FluidraDataUpdateCoordinator) -> None:
    """Register services for Fluidra Pool."""

    async def _handle_set_schedule(call: ServiceCall) -> None:
        """Handle set_schedule service call."""
        device_id = call.data["device_id"]
        schedules_data = call.data["schedules"]

        # Convert HA format to Fluidra API format
        fluidra_schedules = []
        for i, schedule in enumerate(schedules_data):
            # Convert time format "08:00" to cron format "0 8 * * 1,2,3,4,5"
            start_parts = schedule["start_time"].split(":")
            end_parts = schedule["end_time"].split(":")

            start_cron = f"{start_parts[1]} {start_parts[0]} * * {','.join(map(str, schedule['days']))}"
            end_cron = f"{end_parts[1]} {end_parts[0]} * * {','.join(map(str, schedule['days']))}"

            fluidra_schedule = {
                "id": f"schedule_{i + 1}",
                "enabled": schedule["enabled"],
                "startTime": start_cron,
                "endTime": end_cron,
                "startActions": {
                    "componentToChange": 11,  # Speed component
                    "operationName": schedule["mode"],
                },
                "endActions": {
                    "componentToChange": 9,  # Pump component
                    "operationName": "0",  # Turn off
                },
                "state": "IDLE",
            }
            fluidra_schedules.append(fluidra_schedule)

        # Send to API
        try:
            success = await coordinator.api.set_schedule(device_id, fluidra_schedules)
            if success:
                # Refresh coordinator data
                await coordinator.async_request_refresh()
        except Exception:
            pass

    async def _handle_clear_schedule(call: ServiceCall) -> None:
        """Handle clear_schedule service call."""
        device_id = call.data["device_id"]

        try:
            success = await coordinator.api.clear_schedule(device_id)
            if success:
                await coordinator.async_request_refresh()
        except Exception:
            pass

    async def _handle_set_preset_schedule(call: ServiceCall) -> None:
        """Handle set_preset_schedule service call."""
        device_id = call.data["device_id"]
        preset = call.data["preset"]

        # Define presets
        presets = {
            "standard": [
                {"enabled": True, "start_time": "08:00", "end_time": "12:00", "mode": "1", "days": [1, 2, 3, 4, 5]},
                {"enabled": True, "start_time": "18:00", "end_time": "20:00", "mode": "1", "days": [1, 2, 3, 4, 5]},
            ],
            "intensive": [
                {
                    "enabled": True,
                    "start_time": "08:00",
                    "end_time": "18:00",
                    "mode": "2",
                    "days": [0, 1, 2, 3, 4, 5, 6],
                }
            ],
            "eco": [
                {
                    "enabled": True,
                    "start_time": "10:00",
                    "end_time": "14:00",
                    "mode": "0",
                    "days": [0, 1, 2, 3, 4, 5, 6],
                }
            ],
            "summer": [
                {
                    "enabled": True,
                    "start_time": "06:00",
                    "end_time": "10:00",
                    "mode": "2",
                    "days": [0, 1, 2, 3, 4, 5, 6],
                },
                {
                    "enabled": True,
                    "start_time": "16:00",
                    "end_time": "22:00",
                    "mode": "2",
                    "days": [0, 1, 2, 3, 4, 5, 6],
                },
            ],
            "winter": [
                {
                    "enabled": True,
                    "start_time": "12:00",
                    "end_time": "16:00",
                    "mode": "0",
                    "days": [0, 1, 2, 3, 4, 5, 6],
                }
            ],
        }

        if preset not in presets:
            return

        # Use the set_schedule handler
        await _handle_set_schedule(
            ServiceCall(DOMAIN, SERVICE_SET_SCHEDULE, {"device_id": device_id, "schedules": presets[preset]})
        )

    # Register services
    hass.services.async_register(DOMAIN, SERVICE_SET_SCHEDULE, _handle_set_schedule, schema=SET_SCHEDULE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_CLEAR_SCHEDULE, _handle_clear_schedule, schema=CLEAR_SCHEDULE_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_SET_PRESET_SCHEDULE, _handle_set_preset_schedule, schema=SET_PRESET_SCHEDULE_SCHEMA
    )


# FluidraDataUpdateCoordinator is now in coordinator.py
