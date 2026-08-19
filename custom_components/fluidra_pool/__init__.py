"""
Fluidra Pool integration for Home Assistant.

This integration provides support for Fluidra Pool systems.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Final

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
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
    CONF_REFRESH_TOKEN,
    DEVICE_MODEL_FALLBACK,
    DEVICE_MODEL_MAP,
    DOMAIN,
    FluidraPoolConfigEntry,
    FluidraPoolRuntimeData,
)
from .helpers import resolve_schedule_component
from .utils import mask_email

if TYPE_CHECKING:
    from .coordinator import FluidraDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

# Configured only via config entries (UI) and services — no YAML schema.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS: Final = [
    Platform.SWITCH,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,  # Pour l'état de production de la cellule de chloration
    Platform.BUTTON,  # Pour l'arrêt (Stop) de la pompe Victoria VS
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
        # Coerce to int so YAML floats (1.5) are rejected, keeping the 1=Monday..7=Sunday contract.
        vol.Optional("days", default=ALL_MOBILE_DAYS): [vol.All(vol.Coerce(int), vol.Range(min=1, max=7))],
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
    stored_refresh_token = entry.data.get(CONF_REFRESH_TOKEN)

    def _persist_refresh_token(new_token: str) -> None:
        """Persist the latest refresh token back into the config entry."""
        if entry.data.get(CONF_REFRESH_TOKEN) == new_token:
            return  # No-op write would needlessly touch the entry.
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_REFRESH_TOKEN: new_token})

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

    except FluidraMFARequired as err:
        _LOGGER.warning("MFA required for %s, triggering reauth flow", mask_email(email))
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN,
            translation_key="mfa_required",
        ) from err
    except (FluidraError, TimeoutError, OSError) as err:
        _LOGGER.error("Unable to connect to Fluidra Pool API: %s", err)
        raise ConfigEntryNotReady from err

    # The Fluidra cloud is frequently not ready right after a Home Assistant
    # restart and answers with an empty pool list. Entities are built once, from
    # this snapshot, so setting up now would leave the integration empty until the
    # user restarts again. Treat an empty result as "not ready" so HA retries the
    # setup automatically instead.
    if not pools:
        _LOGGER.debug("No pools returned yet (cloud not ready after restart); retrying setup")
        raise ConfigEntryNotReady("Fluidra returned no pools yet; Home Assistant will retry")

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
    entry.runtime_data = FluidraPoolRuntimeData(
        coordinator=coordinator,
        options_snapshot=dict(entry.options),
    )

    # First refresh before platform setup so device_info has correct data
    await coordinator.async_config_entry_first_refresh()

    # Update device registry with correct names/models from coordinator data
    from .device_registry import DeviceIdentifier

    if coordinator.data:
        for pool_id, pool_data in coordinator.data.items():
            for device in pool_data.get("devices", []):
                device_id = device.get("device_id")
                if not device_id:
                    continue
                config = DeviceIdentifier.identify_device(device)
                if config:
                    model = DEVICE_MODEL_MAP.get(config.device_type, DEVICE_MODEL_FALLBACK)
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
    """Handle entry updates — reload only when the *options* actually changed.

    🥇 Gold: Recharger l'intégration pour appliquer les nouvelles options.

    HA fires update listeners on any entry change, including the token-persist
    data write. Reloading on those would tear down the coordinator mid-operation
    (and could recurse: reload → re-auth → persist → reload), so reload only when
    the options differ from the snapshot captured at setup.
    """
    runtime = getattr(entry, "runtime_data", None)
    if runtime is not None and dict(entry.options) == runtime.options_snapshot:
        return  # Data-only change (e.g. refresh_token persist) — do not reload.
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: FluidraPoolConfigEntry) -> bool:
    """Unload a config entry.

    runtime_data is cleared automatically by HA. We still close the API client:
    under HA it uses the shared aiohttp session (``close()`` is a no-op), so this
    only matters for a privately-owned session (non-HA contexts / tests).
    """
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        runtime = getattr(entry, "runtime_data", None)
        coordinator = getattr(runtime, "coordinator", None)
        if coordinator is not None:
            await coordinator.api.close()
    return unload_ok


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
        devices: list[dict[str, Any]] = pool_data.get("devices", [])
        for device in devices:
            if device.get("device_id") == device_id:
                return device
    return None


def _coordinator_has_device(coordinator: FluidraDataUpdateCoordinator, device_id: str) -> bool:
    """Return True when a coordinator owns the requested Fluidra device."""
    return _get_device_data(coordinator, device_id) is not None


def _get_schedule_component(coordinator: FluidraDataUpdateCoordinator, device_id: str) -> int:
    """Return the schedule component for a device, defaulting to pump schedules."""
    device = _get_device_data(coordinator, device_id)
    if device is None:
        return COMPONENT_SCHEDULE
    return resolve_schedule_component(device, COMPONENT_SCHEDULE)


def _get_coordinator_for_device(hass: HomeAssistant, device_id: str) -> FluidraDataUpdateCoordinator:
    """Find the loaded entry coordinator that owns a service target device."""
    coordinators: list[FluidraDataUpdateCoordinator] = []
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        runtime_data = getattr(entry, "runtime_data", None)
        coordinator: FluidraDataUpdateCoordinator | None = getattr(runtime_data, "coordinator", None)
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


def _ensure_device_pool_writable(coordinator: FluidraDataUpdateCoordinator, device_id: str) -> None:
    """Fail fast when the device's pool is viewer (read-only) — Issue #133.

    Mirrors ``FluidraPoolControlEntity._ensure_pool_writable`` for domain
    services: a viewer write is accepted by the Fluidra cloud with a fake
    HTTP 200 that never persists, so raise a clear error instead.
    """
    for pool_id, pool in (coordinator.data or {}).items():
        for device in pool.get("devices", []):
            if device.get("device_id") == device_id:
                if pool.get("access_level") == "viewer":
                    raise ServiceValidationError(
                        translation_domain=DOMAIN,
                        translation_key="pool_read_only",
                        translation_placeholders={"pool_name": str(pool.get("name", pool_id))},
                    )
                return


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


def _device_uses_component_actions(coordinator: Any, device_id: str) -> bool:
    """Return True when this device carries a schedule's mode in ``componentActions``.

    Two payload shapes exist in the wild: ``startActions.operationName`` (a string
    mode) and ``startActions.componentActions`` (a list, used by the eXO family).
    Writing the wrong one doesn't fail loudly — the backend accepts it and the
    schedule ends up mangled (Issue #175) — so mirror whatever the device already
    reports rather than assuming.
    """
    device = coordinator.api.get_device_by_id(device_id) if coordinator else None
    for sched in (device or {}).get("schedule_data") or []:
        if isinstance(sched, dict) and isinstance(sched.get("startActions"), dict):
            return "componentActions" in sched["startActions"]
    return False


def _service_schedule_to_fluidra(
    schedule: dict[str, Any],
    schedule_id: int,
    *,
    use_component_actions: bool = False,
) -> dict[str, Any]:
    """Convert service schedule input to the Fluidra CRON schedule shape."""
    start_hour, start_minute = _parse_service_time(schedule["start_time"])
    end_hour, end_minute = _parse_service_time(schedule["end_time"])
    # A slot is two independent CRON expressions sharing one day set, so an
    # overnight window has no representation: 22:00 -> 06:00 would be stored as
    # "start Monday 22:00, end Monday 06:00", an end that precedes its own start.
    # None of the captured app slots crosses midnight either. Refuse it here,
    # where the whole window is specified at once and the intent is unambiguous;
    # split it into an evening slot and a morning slot instead. (The time
    # entities still tolerate a momentarily inverted pair, because that is a
    # half-finished edit rather than a stated overnight range.)
    if (end_hour, end_minute) <= (start_hour, start_minute):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="schedule_overnight_unsupported",
            translation_placeholders={
                "start_time": f"{start_hour:02d}:{start_minute:02d}",
                "end_time": f"{end_hour:02d}:{end_minute:02d}",
            },
        )
    days = sorted({int(day) for day in schedule["days"]})
    if not days:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="empty_schedule_days",
        )
    # The service takes mobile-app day numbers (1=Mon..7=Sun, see services.yaml)
    # but the device stores plain CRON, where Sunday is 0 — its own schedules read
    # "* * 0,1,2,3,4,5,6" for every day (Issue #174, @Inervo). Reading already
    # converts the other way (utils.convert_cron_days); writing never converted
    # back, so a Sunday schedule went out as day 7, which CRON does not define.
    days_str = ",".join(str(0 if day == 7 else day) for day in sorted({0 if d == 7 else d for d in days}))

    # Shape captured from the official Fluidra Connect app's PUT body (Issue #89):
    # an integer id/groupId per slot and a single startActions.operationName. The
    # previous payload (string "schedule_N" id, no groupId, a spurious
    # componentToChange, plus synthesised endActions and state) was rejected by the
    # server-side JSONata transform ("invalid scheduleUser").
    # No ``state``: a v2.78.5 attempt added ``"state": "IDLE"`` because the eXO's
    # own slots report one, but the capture of the app's PUT body settles it --
    # the app sends id/groupId/enabled/startTime/endTime/startActions and nothing
    # else. ``state`` is added by the device, not by the client (Issue #174).
    payload: dict[str, Any] = {"id": schedule_id, "groupId": schedule_id}
    payload["enabled"] = schedule["enabled"]
    payload["startTime"] = f"{start_minute:02d} {start_hour:02d} * * {days_str}"
    payload["endTime"] = f"{end_minute:02d} {end_hour:02d} * * {days_str}"
    payload["startActions"] = _schedule_start_actions(schedule["mode"], use_component_actions)
    return payload


def _schedule_start_actions(mode: Any, use_component_actions: bool) -> dict[str, Any]:
    """Build ``startActions`` in the shape the target device uses (Issue #175)."""
    if use_component_actions:
        try:
            value = int(mode)
        except (TypeError, ValueError):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_schedule_mode",
                translation_placeholders={"value": str(mode)},
            ) from None
        # ``desiredValue``, and ``operationName`` kept alongside: that is what the
        # official app PUTs. Reads echo the same action back as ``reportedValue``,
        # and sending that key back is a write the backend's transform does not
        # consume (Issue #174, @Inervo). ``schedule_slots_for_write`` enforces the
        # same rule on every other write path.
        return {"operationName": "1", "componentActions": [{"id": 0, "desiredValue": value}]}
    return {"operationName": mode}


def _ensure_schedule_write_supported(
    coordinator: FluidraDataUpdateCoordinator, device_id: str, component_id: int
) -> None:
    """Refuse schedule writes on devices where they are known to land wrong.

    On the eXO iQ the backend does not store what we send. Verified on hardware
    across four runs (Issue #174, @Inervo): a slot sent as 01:02-03:04 on a
    single day was stored as "03 02" / "00 04" on **four** days, and the stored
    days track the sent day deterministically -- sending day *n* yields
    ``{0, n+2, n+5, n+6}``.

    That was blamed on the payload matching the device's own *reported* slots
    field for field. @Inervo's later capture of the app's PUT body shows the two
    are not the same object: the app sends the action value under
    ``desiredValue`` with ``operationName`` alongside and no ``state`` at all,
    where the integration echoed back ``reportedValue`` and synthesised a
    ``state``. Both are corrected now, which is a plausible cause of the
    transform producing garbage rather than storing the slot -- but plausible is
    not verified, and nobody has re-run this on hardware.

    Two distinct failures, both worse than the feature being absent:

    * The VS register additionally needs a target RPM this service cannot set,
      and an RPM-less slot leaves the Fluidra app unable to load the device at
      all -- recoverable only by changing the pump type on the unit itself.
    * Every register here stores a schedule that differs from the one asked
      for, and the device *acts* on it: chlorination running at hours nobody
      chose is a worse outcome than an error message.

    Refusing is recoverable; writing is not, so the refusal stands until a
    hardware run confirms the corrected shape. The aux registers (c22-c25) are
    deliberately *not* covered: they take the same corrected payload and drive
    only an auxiliary output, so they are the safe place to confirm it.
    """
    from .device_registry import DeviceIdentifier

    device = _get_device_data(coordinator, device_id)
    if device is None:
        return
    mapping = DeviceIdentifier.get_feature(device, "schedule_component_map", None)
    if not isinstance(mapping, dict):
        return
    if component_id == mapping.get("vs"):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="schedule_vs_pump_unsupported",
            translation_placeholders={"device_id": device_id},
        )
    if component_id in {mapping.get("none"), mapping.get("simple")}:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="schedule_write_not_stored",
            translation_placeholders={"device_id": device_id},
        )


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
        _ensure_device_pool_writable(coordinator, device_id)

        # Convert HA format to Fluidra API format
        component_actions = _device_uses_component_actions(coordinator, device_id)
        fluidra_schedules = [
            _service_schedule_to_fluidra(schedule, i, use_component_actions=component_actions)
            for i, schedule in enumerate(schedules_data, start=1)
        ]

        schedule_component = _get_schedule_component(coordinator, device_id)
        _ensure_schedule_write_supported(coordinator, device_id, schedule_component)

        try:
            success = await coordinator.api.set_schedule(device_id, fluidra_schedules, component_id=schedule_component)
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
        _ensure_device_pool_writable(coordinator, device_id)

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
        _ensure_device_pool_writable(coordinator, device_id)

        # Define presets
        presets: dict[str, list[dict[str, Any]]] = {
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
            _service_schedule_to_fluidra(
                schedule, i, use_component_actions=_device_uses_component_actions(coordinator, device_id)
            )
            for i, schedule in enumerate(presets[preset], start=1)
        ]

        try:
            success = await coordinator.api.set_schedule(
                device_id, fluidra_schedules, component_id=_get_schedule_component(coordinator, device_id)
            )
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
