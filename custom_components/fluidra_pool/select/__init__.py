"""Select platform for Fluidra Pool integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.select import SelectEntity

from ..const import (
    DEVICE_TYPE_CHLORINATOR,
    DEVICE_TYPE_LIGHT,
    DEVICE_TYPE_PUMP,
    FluidraPoolConfigEntry,
)
from ..device_registry import DeviceIdentifier
from ..platform_setup import async_setup_dynamic_platform
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

    def _build(pool_id: str, device: dict[str, Any]) -> list[SelectEntity]:
        """Create select entities for one device."""
        entities: list[SelectEntity] = []
        device_id = device["device_id"]
        config = DeviceIdentifier.identify_device(device)
        device_type = config.device_type if config else device.get("type", "")

        # Chlorinator mode select (OFF/ON/AUTO) — skip for variants without mode select (e.g. CC24033907).
        if device_type == DEVICE_TYPE_CHLORINATOR:
            skip_mode = DeviceIdentifier.has_feature(device, "skip_mode_select")
            if not skip_mode:
                entities.append(FluidraChlorinatorModeSelect(coordinator, coordinator.api, pool_id, device_id))

        # Heat pumps don't expose speed or schedule controls.
        if DeviceIdentifier.has_feature(device, "skip_schedules"):
            return entities

        if (
            device_type == DEVICE_TYPE_PUMP
            and DeviceIdentifier.should_create_entity(device, "select")
            and device.get("variable_speed")
        ):
            entities.append(FluidraPumpSpeedSelect(coordinator, coordinator.api, pool_id, device_id))

        if (
            device_type == DEVICE_TYPE_PUMP
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

        if device_type == DEVICE_TYPE_LIGHT:
            effect_component = DeviceIdentifier.get_feature(device, "effect_select")
            if effect_component:
                entities.append(FluidraLightEffectSelect(coordinator, coordinator.api, pool_id, device_id))

        if device_type == DEVICE_TYPE_CHLORINATOR and DeviceIdentifier.has_feature(device, "schedule_component"):
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

        return entities

    await async_setup_dynamic_platform(config_entry, async_add_entities, _build)
