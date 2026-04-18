"""
Fluidra Pool integration for Home Assistant.

This integration provides support for Fluidra Pool systems.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Final

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
import voluptuous as vol

from .api_resilience import FluidraError, FluidraMFARequired
from .const import CONF_EMAIL, CONF_PASSWORD, DOMAIN, FluidraPoolConfigEntry, FluidraPoolRuntimeData
from .utils import mask_email

if TYPE_CHECKING:
    from .coordinator import FluidraDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: Final = [
    Platform.SWITCH,
    Platform.SENSOR,
    Platform.SELECT,  # Pour modes d'opération pompe (OFF/ON/AUTO/TURBO)
    Platform.NUMBER,  # Pour contrôle vitesse pompe E30iQ (0-100%)
    Platform.TIME,  # Pour édition des heures de programmation
    Platform.CLIMATE,  # Pour contrôle température pompes à chaleur
    Platform.LIGHT,  # Pour LumiPlus Connect et autres éclairages
]

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


async def async_setup_entry(hass: HomeAssistant, entry: FluidraPoolConfigEntry) -> bool:
    """Set up Fluidra Pool from a config entry."""
    email = entry.data[CONF_EMAIL]
    password = entry.data[CONF_PASSWORD]

    # Initialize API client
    from .fluidra_api import FluidraPoolAPI

    # Pass any stored refresh token so the API can bypass MFA on reload/restart.
    stored_refresh_token = entry.data.get("refresh_token")

    def _persist_refresh_token(new_token: str) -> None:
        """Persist the latest refresh token back into the config entry."""
        hass.config_entries.async_update_entry(entry, data={**entry.data, "refresh_token": new_token})

    api = FluidraPoolAPI(
        email,
        password,
        hass,
        refresh_token=stored_refresh_token,
        on_token_persist=_persist_refresh_token,
    )

    try:
        # Test connection and authentication
        await api.authenticate()
        pools = await api.get_pools()
        # Continue setup even if no pools found - user may add equipment later

    except FluidraMFARequired as err:
        _LOGGER.warning("MFA required for %s, triggering reauth flow", mask_email(email))
        raise ConfigEntryAuthFailed("MFA required") from err
    except (FluidraError, TimeoutError, OSError) as err:
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
    from .coordinator import FluidraDataUpdateCoordinator

    coordinator = FluidraDataUpdateCoordinator(hass, api, entry)

    # 🏆 Utiliser runtime_data au lieu de hass.data (2024+)
    entry.runtime_data = FluidraPoolRuntimeData(coordinator=coordinator)

    # First refresh before platform setup so device_info has correct data
    await coordinator.async_config_entry_first_refresh()

    # Update device registry with correct names/models from coordinator data
    from .device_registry import DeviceIdentifier

    model_map = {
        "chlorinator": "Chlorinator",
        "pump": "Pump",
        "heat_pump": "Heat Pump",
        "light": "Light",
        "heater": "Heater",
    }
    if coordinator.data:
        for pool_id, pool_data in coordinator.data.items():
            for device in pool_data.get("devices", []):
                device_id = device.get("device_id")
                if not device_id:
                    continue
                config = DeviceIdentifier.identify_device(device)
                if config:
                    model = model_map.get(config.device_type, "Pool Equipment")
                    device_name = device.get("name", f"Device {device_id}")
                    device_registry.async_get_or_create(
                        config_entry_id=entry.entry_id,
                        identifiers={(DOMAIN, device_id)},
                        name=device_name,
                        manufacturer="Fluidra",
                        model=model,
                        via_device=(DOMAIN, pool_id),
                    )

    # Set up platforms after coordinator has data
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register services
    await _async_register_services(hass, coordinator)

    # 🥇 Gold: Recharger l'intégration quand les options changent
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    return True


async def _async_options_updated(hass: HomeAssistant, entry: FluidraPoolConfigEntry) -> None:
    """Handle options update - reload the integration.

    🥇 Gold: Recharger l'intégration pour appliquer les nouvelles options.
    """
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: FluidraPoolConfigEntry) -> bool:
    """Unload a config entry."""
    # runtime_data est nettoyé automatiquement
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old config entry to new version.

    🏆 Lifecycle: Migration idempotente pour compatibilité long terme.
    """
    _LOGGER.debug("Migrating config entry from version %s", entry.version)

    # Version 1 -> 2: Reserved for future migrations
    # Example:
    # if entry.version == 1:
    #     data = {**entry.data}
    #     data["new_key"] = data.pop("old_key", "default")
    #     hass.config_entries.async_update_entry(entry, data=data, version=2)
    #     _LOGGER.info("Migrated config entry to version 2")

    # Current version is 1, no migration needed yet
    if entry.version > 1:
        # Future-proof: if somehow version is higher than expected
        _LOGGER.error("Cannot migrate config entry from version %s", entry.version)
        return False

    return True


async def _async_register_services(hass: HomeAssistant, coordinator: FluidraDataUpdateCoordinator) -> None:
    """Register services for Fluidra Pool.

    🏆 Platinum: Services avec supports_response pour retourner des données.
    """

    async def _handle_set_schedule(call: ServiceCall) -> ServiceResponse:
        """Handle set_schedule service call.

        🏆 Platinum: Retourne le résultat de l'opération.
        """
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

        try:
            success = await coordinator.api.set_schedule(device_id, fluidra_schedules)
        except FluidraError:
            _LOGGER.exception("Service %s failed for device %s", SERVICE_SET_SCHEDULE, device_id)
            return {"success": False, "device_id": device_id, "error": "Fluidra API error"}

        if success:
            await coordinator.async_request_refresh()
            return {
                "success": True,
                "device_id": device_id,
                "schedules_count": len(fluidra_schedules),
            }
        return {"success": False, "device_id": device_id, "error": "API call failed"}

    async def _handle_clear_schedule(call: ServiceCall) -> ServiceResponse:
        """Handle clear_schedule service call.

        🏆 Platinum: Retourne le résultat de l'opération.
        """
        device_id = call.data["device_id"]

        try:
            success = await coordinator.api.clear_schedule(device_id)
        except FluidraError:
            _LOGGER.exception("Service %s failed for device %s", SERVICE_CLEAR_SCHEDULE, device_id)
            return {"success": False, "device_id": device_id, "error": "Fluidra API error"}

        if success:
            await coordinator.async_request_refresh()
            return {"success": True, "device_id": device_id}
        return {"success": False, "device_id": device_id, "error": "API call failed"}

    async def _handle_set_preset_schedule(call: ServiceCall) -> ServiceResponse:
        """Handle set_preset_schedule service call.

        🏆 Platinum: Retourne le résultat de l'opération.
        """
        device_id = call.data["device_id"]
        preset = call.data["preset"]

        # Define presets
        presets: dict[str, list[dict]] = {
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
            return {"success": False, "device_id": device_id, "error": f"Unknown preset: {preset}"}

        # Build schedules in Fluidra format
        fluidra_schedules = []
        for i, schedule in enumerate(presets[preset]):
            start_parts = schedule["start_time"].split(":")
            end_parts = schedule["end_time"].split(":")
            start_cron = f"{start_parts[1]} {start_parts[0]} * * {','.join(map(str, schedule['days']))}"
            end_cron = f"{end_parts[1]} {end_parts[0]} * * {','.join(map(str, schedule['days']))}"

            fluidra_schedules.append(
                {
                    "id": f"schedule_{i + 1}",
                    "enabled": schedule["enabled"],
                    "startTime": start_cron,
                    "endTime": end_cron,
                    "startActions": {"componentToChange": 11, "operationName": schedule["mode"]},
                    "endActions": {"componentToChange": 9, "operationName": "0"},
                    "state": "IDLE",
                }
            )

        try:
            success = await coordinator.api.set_schedule(device_id, fluidra_schedules)
        except FluidraError:
            _LOGGER.exception("Service %s failed for device %s", SERVICE_SET_PRESET_SCHEDULE, device_id)
            return {"success": False, "device_id": device_id, "error": "Fluidra API error"}

        if success:
            await coordinator.async_request_refresh()
            return {
                "success": True,
                "device_id": device_id,
                "preset": preset,
                "schedules_count": len(fluidra_schedules),
            }
        return {"success": False, "device_id": device_id, "error": "API call failed"}

    # 🏆 Platinum: Register services with supports_response
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_SCHEDULE,
        _handle_set_schedule,
        schema=SET_SCHEDULE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CLEAR_SCHEDULE,
        _handle_clear_schedule,
        schema=CLEAR_SCHEDULE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_PRESET_SCHEDULE,
        _handle_set_preset_schedule,
        schema=SET_PRESET_SCHEDULE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )


# FluidraDataUpdateCoordinator is now in coordinator.py
