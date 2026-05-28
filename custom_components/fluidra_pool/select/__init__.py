"""Select platform for Fluidra Pool integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.select import SelectEntity

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
    """Set up the Fluidra Pool select entities."""
    coordinator = config_entry.runtime_data.coordinator

    entities: list[SelectEntity] = []

    # Use cached pools data instead of API call for faster startup
    pools = coordinator.api.cached_pools or await coordinator.api.get_pools()
    for pool in pools:
        for device in pool["devices"]:
            device_id = device.get("device_id")
            config = DeviceIdentifier.identify_device(device)
            device_type = config.device_type if config else device.get("type", "")

            if not device_id:
                continue

            # Chlorinator mode select (OFF/ON/AUTO) — skip for variants without mode select (e.g. CC24033907).
            if device_type == "chlorinator":
                skip_mode = DeviceIdentifier.has_feature(device, "skip_mode_select")
                if not skip_mode:
                    entities.append(FluidraChlorinatorModeSelect(coordinator, coordinator.api, pool["id"], device_id))

            # Heat pumps don't expose speed or schedule controls.
            if DeviceIdentifier.has_feature(device, "skip_schedules"):
                continue

            if (
                device_type == "pump"
                and DeviceIdentifier.should_create_entity(device, "select")
                and device.get("variable_speed")
            ):
                entities.append(FluidraPumpSpeedSelect(coordinator, coordinator.api, pool["id"], device_id))

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
                            pool["id"],
                            device_id,
                            schedule_id,
                        )
                    )

            if device_type == "light":
                effect_component = DeviceIdentifier.get_feature(device, "effect_select")
                if effect_component:
                    entities.append(FluidraLightEffectSelect(coordinator, coordinator.api, pool["id"], device_id))

            if device_type == "chlorinator" and DeviceIdentifier.has_feature(device, "schedule_component"):
                schedule_count = DeviceIdentifier.get_feature(device, "schedule_count", 3)
                for i in range(1, schedule_count + 1):
                    schedule_id = str(i)
                    entities.append(
                        FluidraChlorinatorScheduleSpeedSelect(
                            coordinator,
                            coordinator.api,
                            pool["id"],
                            device_id,
                            schedule_id,
                        )
                    )

    async_add_entities(entities)
