"""
Fluidra Pool integration for Home Assistant.

This integration provides support for Fluidra Pool systems.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Final

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
import voluptuous as vol

from .api_resilience import FluidraError, FluidraMFARequired
from .const import (
    COMPONENT_SCHEDULE,
    CONF_EMAIL,
    CONF_PASSWORD,
    DOMAIN,
    FluidraPoolConfigEntry,
    FluidraPoolRuntimeData,
)
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

ALL_MOBILE_DAYS: Final = [1, 2, 3, 4, 5, 6, 7]

SCHEDULE_SCHEMA = vol.Schema(
    {
        vol.Required("enabled"): cv.boolean,
        vol.Required("start_time"): cv.string,
        vol.Required("end_time"): cv.string,
        vol.Required("mode"): vol.In(["0", "1", "2"]),  # 0=Faible, 1=Moyenne, 2=Élevée
        vol.Optional("days", default=ALL_MOBILE_DAYS): [vol.Range(min=1, max=7)],  # 1=Monday, 7=Sunday
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


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up Fluidra Pool integration-wide services."""
    await _async_register_services(hass)
    return True


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
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN,
            translation_key="mfa_required",
        ) from err
    except (FluidraError, TimeoutError, OSError) as err:
        _LOGGER.error("Unable to connect to Fluidra Pool API: %s", err)
        raise ConfigEntryNotReady from err

    # Create devices for each pool
    device_registry = dr.async_get(hass)
    for pool in pools:
        raw_pool_id = pool.get("id")
        if raw_pool_id is None:
            continue
        pool_id = str(raw_pool_id)
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


def _get_device_data(coordinator: FluidraDataUpdateCoordinator, device_id: str) -> dict[str, Any] | None:
    """Return device data from a coordinator for a Fluidra device ID."""
    if not coordinator.data:
        return None

    for pool_data in coordinator.data.values():
        for device in pool_data.get("devices", []):
            if device.get("device_id") == device_id:
                return device
    return None


def _coordinator_has_device(coordinator: FluidraDataUpdateCoordinator, device_id: str) -> bool:
    """Return True when a coordinator owns the requested Fluidra device."""
    return _get_device_data(coordinator, device_id) is not None


def _get_schedule_component(coordinator: FluidraDataUpdateCoordinator, device_id: str) -> int:
    """Return the schedule component for a device, defaulting to pump schedules."""
    from .device_registry import DeviceIdentifier

    device = _get_device_data(coordinator, device_id)
    if device is None:
        return COMPONENT_SCHEDULE
    return DeviceIdentifier.get_feature(device, "schedule_component", COMPONENT_SCHEDULE)


def _get_coordinator_for_device(hass: HomeAssistant, device_id: str) -> FluidraDataUpdateCoordinator:
    """Find the loaded entry coordinator that owns a service target device."""
    coordinators: list[FluidraDataUpdateCoordinator] = []
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        runtime_data = getattr(entry, "runtime_data", None)
        coordinator = getattr(runtime_data, "coordinator", None)
        if coordinator is None:
            continue

        coordinators.append(coordinator)
        if _coordinator_has_device(coordinator, device_id):
            return coordinator

    if len(coordinators) == 1:
        return coordinators[0]

    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="device_not_found",
        translation_placeholders={"device_id": device_id},
    )


def _parse_service_time(value: str) -> tuple[int, int]:
    """Parse service HH:MM input into hour/minute integers."""
    try:
        hour_text, minute_text = value.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
    except (AttributeError, ValueError) as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_time_format",
            translation_placeholders={"value": str(value)},
        ) from err

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_time_format",
            translation_placeholders={"value": str(value)},
        )
    return hour, minute


def _service_schedule_to_fluidra(schedule: dict[str, Any], schedule_id: int) -> dict[str, Any]:
    """Convert service schedule input to the Fluidra CRON schedule shape."""
    start_hour, start_minute = _parse_service_time(schedule["start_time"])
    end_hour, end_minute = _parse_service_time(schedule["end_time"])
    days = sorted({int(day) for day in schedule["days"]})
    if not days:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="empty_schedule_days",
        )
    days_str = ",".join(str(day) for day in days)

    return {
        "id": f"schedule_{schedule_id}",
        "enabled": schedule["enabled"],
        "startTime": f"{start_minute:02d} {start_hour:02d} * * {days_str}",
        "endTime": f"{end_minute:02d} {end_hour:02d} * * {days_str}",
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


async def _async_register_services(hass: HomeAssistant) -> None:
    """Register services for Fluidra Pool.

    🏆 Platinum: Services avec supports_response pour retourner des données.
    """
    if hass.services.has_service(DOMAIN, SERVICE_SET_SCHEDULE):
        return

    async def _handle_set_schedule(call: ServiceCall) -> ServiceResponse:
        """Handle set_schedule service call.

        🏆 Platinum: Retourne le résultat de l'opération.
        """
        device_id = call.data["device_id"]
        schedules_data = call.data["schedules"]
        coordinator = _get_coordinator_for_device(hass, device_id)

        # Convert HA format to Fluidra API format
        fluidra_schedules = [
            _service_schedule_to_fluidra(schedule, i) for i, schedule in enumerate(schedules_data, start=1)
        ]

        try:
            success = await coordinator.api.set_schedule(device_id, fluidra_schedules)
        except FluidraError as err:
            _LOGGER.exception("Service %s failed for device %s", SERVICE_SET_SCHEDULE, device_id)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="schedule_set_failed",
                translation_placeholders={"device_id": device_id},
            ) from err

        if success:
            await coordinator.async_request_refresh()
            return {
                "success": True,
                "device_id": device_id,
                "schedules_count": len(fluidra_schedules),
            }
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="schedule_set_rejected",
            translation_placeholders={"device_id": device_id},
        )

    async def _handle_clear_schedule(call: ServiceCall) -> ServiceResponse:
        """Handle clear_schedule service call.

        🏆 Platinum: Retourne le résultat de l'opération.
        """
        device_id = call.data["device_id"]
        coordinator = _get_coordinator_for_device(hass, device_id)

        try:
            success = await coordinator.api.clear_schedule(
                device_id, component_id=_get_schedule_component(coordinator, device_id)
            )
        except FluidraError as err:
            _LOGGER.exception("Service %s failed for device %s", SERVICE_CLEAR_SCHEDULE, device_id)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="schedule_clear_failed",
                translation_placeholders={"device_id": device_id},
            ) from err

        if success:
            await coordinator.async_request_refresh()
            return {"success": True, "device_id": device_id}
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="schedule_clear_rejected",
            translation_placeholders={"device_id": device_id},
        )

    async def _handle_set_preset_schedule(call: ServiceCall) -> ServiceResponse:
        """Handle set_preset_schedule service call.

        🏆 Platinum: Retourne le résultat de l'opération.
        """
        device_id = call.data["device_id"]
        preset = call.data["preset"]
        coordinator = _get_coordinator_for_device(hass, device_id)

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
                    "days": ALL_MOBILE_DAYS,
                }
            ],
            "eco": [
                {
                    "enabled": True,
                    "start_time": "10:00",
                    "end_time": "14:00",
                    "mode": "0",
                    "days": ALL_MOBILE_DAYS,
                }
            ],
            "summer": [
                {
                    "enabled": True,
                    "start_time": "06:00",
                    "end_time": "10:00",
                    "mode": "2",
                    "days": ALL_MOBILE_DAYS,
                },
                {
                    "enabled": True,
                    "start_time": "16:00",
                    "end_time": "22:00",
                    "mode": "2",
                    "days": ALL_MOBILE_DAYS,
                },
            ],
            "winter": [
                {
                    "enabled": True,
                    "start_time": "12:00",
                    "end_time": "16:00",
                    "mode": "0",
                    "days": ALL_MOBILE_DAYS,
                }
            ],
        }

        if preset not in presets:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_preset",
                translation_placeholders={"preset": preset},
            )

        # Build schedules in Fluidra format
        fluidra_schedules = [
            _service_schedule_to_fluidra(schedule, i) for i, schedule in enumerate(presets[preset], start=1)
        ]

        try:
            success = await coordinator.api.set_schedule(device_id, fluidra_schedules)
        except FluidraError as err:
            _LOGGER.exception("Service %s failed for device %s", SERVICE_SET_PRESET_SCHEDULE, device_id)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="preset_schedule_set_failed",
                translation_placeholders={"device_id": device_id},
            ) from err

        if success:
            await coordinator.async_request_refresh()
            return {
                "success": True,
                "device_id": device_id,
                "preset": preset,
                "schedules_count": len(fluidra_schedules),
            }
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="preset_schedule_set_rejected",
            translation_placeholders={"device_id": device_id},
        )

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
