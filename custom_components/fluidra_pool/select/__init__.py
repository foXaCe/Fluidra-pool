"""Select platform for Fluidra Pool integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.select import SelectEntity
from homeassistant.core import callback

from ..const import FluidraPoolConfigEntry
from ..device_registry import DeviceIdentifier
from .chlorinator import FluidraChlorinatorModeSelect
from .light import FluidraLightEffectSelect
from .pump import FluidraPumpSpeedSelect
from .schedule import FluidraChlorinatorScheduleSpeedSelect, FluidraScheduleModeSelect

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

__all__ = [
    "FluidraChlorinatorModeSelect",
    "FluidraChlorinatorScheduleSpeedSelect",
    "FluidraLightEffectSelect",
    "FluidraPumpSpeedSelect",
    "FluidraScheduleModeSelect",
    "async_setup_entry",
]

PARALLEL_UPDATES = 0  # Coordinator handles all updates


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: FluidraPoolConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Fluidra Pool select entities, including devices added later."""
    coordinator = config_entry.runtime_data.coordinator
    known_devices: set[str] = set()

    @callback
    def _add_entities(pools: list[dict[str, Any]]) -> None:
        """Create entities for any device not seen yet (dynamic-devices)."""
        entities: list[SelectEntity] = []

        for pool in pools:
            pool_id = pool["id"]

            for device in pool.get("devices", []):
                device_id = device.get("device_id")
                config = DeviceIdentifier.identify_device(device)
                device_type = config.device_type if config else device.get("type", "")

                if not device_id:
                    continue

                key = f"{pool_id}_{device_id}"
                if key in known_devices:
                    continue
                known_devices.add(key)

                # Chlorinator mode select (OFF/ON/AUTO) — skip for variants without mode select (e.g. CC24033907).
                if device_type == "chlorinator":
                    skip_mode = DeviceIdentifier.has_feature(device, "skip_mode_select")
                    if not skip_mode:
                        entities.append(FluidraChlorinatorModeSelect(coordinator, coordinator.api, pool_id, device_id))

                # Heat pumps don't expose speed or schedule controls.
                if DeviceIdentifier.has_feature(device, "skip_schedules"):
                    continue

                if (
                    device_type == "pump"
                    and DeviceIdentifier.should_create_entity(device, "select")
                    and device.get("variable_speed")
                ):
                    entities.append(FluidraPumpSpeedSelect(coordinator, coordinator.api, pool_id, device_id))

                if (
                    device_type == "pump"
                    and DeviceIdentifier.should_create_entity(device, "select")
                    and device.get("schedule_data")
                ):
                    # Pumps expose 8 schedule slots.
                    for schedule_id in ["1", "2", "3", "4", "5", "6", "7", "8"]:
                        entities.append(
                            FluidraScheduleModeSelect(
                                coordinator,
                                coordinator.api,
                                pool_id,
                                device_id,
                                schedule_id,
                            )
                        )

                if device_type == "light":
                    effect_component = DeviceIdentifier.get_feature(device, "effect_select")
                    if effect_component:
                        entities.append(FluidraLightEffectSelect(coordinator, coordinator.api, pool_id, device_id))

                if device_type == "chlorinator" and DeviceIdentifier.has_feature(device, "schedule_component"):
                    schedule_count = DeviceIdentifier.get_feature(device, "schedule_count", 3)
                    for i in range(1, schedule_count + 1):
                        schedule_id = str(i)
                        entities.append(
                            FluidraChlorinatorScheduleSpeedSelect(
                                coordinator,
                                coordinator.api,
                                pool_id,
                                device_id,
                                schedule_id,
                            )
                        )

        if entities:
            async_add_entities(entities)

    # Initial setup from the cached discovery (fast startup, unchanged behaviour).
    pools = coordinator.api.cached_pools or await coordinator.api.get_pools()
    _add_entities(pools)

    # Add entities for devices that appear on later polls, without a reload.
    @callback
    def _on_coordinator_update() -> None:
        _add_entities(coordinator.get_pools_from_data())

    config_entry.async_on_unload(coordinator.async_add_listener(_on_coordinator_update))
