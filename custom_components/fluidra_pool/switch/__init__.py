"""Switch platform for Fluidra Pool integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchEntity

from ..const import (
    DEVICE_TYPE_CHLORINATOR,
    DEVICE_TYPE_HEAT_PUMP,
    DEVICE_TYPE_HEATER,
    DEVICE_TYPE_PUMP,
    FluidraPoolConfigEntry,
)
from ..device_registry import DeviceIdentifier
from ..platform_setup import async_setup_dynamic_platform
from .base import FluidraPoolSwitchEntity
from .chlorinator import FluidraChlorinatorBoostSwitch, FluidraChlorinatorSwitch
from .heater import FluidraHeaterSwitch, FluidraHeatPumpSwitch
from .pump import FluidraAutoModeSwitch, FluidraPumpSwitch
from .schedule import FluidraScheduleEnableSwitch

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

__all__ = [
    "FluidraAutoModeSwitch",
    "FluidraChlorinatorBoostSwitch",
    "FluidraChlorinatorSwitch",
    "FluidraHeatPumpSwitch",
    "FluidraHeaterSwitch",
    "FluidraPoolSwitchEntity",
    "FluidraPumpSwitch",
    "FluidraScheduleEnableSwitch",
    "async_setup_entry",
]

PARALLEL_UPDATES = 0  # Coordinator handles all updates


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: FluidraPoolConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Fluidra Pool switch entities, including devices added later."""
    coordinator = config_entry.runtime_data.coordinator

    def _build(pool_id: str, device: dict[str, Any]) -> list[SwitchEntity]:
        """Create switches for one device."""
        entities: list[SwitchEntity] = []
        device_id = device["device_id"]

        if DeviceIdentifier.should_create_entity(device, "switch"):
            device_config = DeviceIdentifier.identify_device(device)
            if device_config:
                device_type = device_config.device_type

                if device_type == DEVICE_TYPE_HEAT_PUMP:
                    entities.append(FluidraHeatPumpSwitch(coordinator, coordinator.api, pool_id, device_id))
                elif device_type == DEVICE_TYPE_PUMP:
                    entities.append(FluidraPumpSwitch(coordinator, coordinator.api, pool_id, device_id))
                elif device_type == DEVICE_TYPE_HEATER:
                    entities.append(FluidraHeaterSwitch(coordinator, coordinator.api, pool_id, device_id))
                elif device_type == DEVICE_TYPE_CHLORINATOR and DeviceIdentifier.has_feature(
                    device, "on_off_component"
                ):
                    entities.append(FluidraChlorinatorSwitch(coordinator, coordinator.api, pool_id, device_id))

        if DeviceIdentifier.should_create_entity(device, "switch_auto") and not DeviceIdentifier.has_feature(
            device, "skip_auto_mode"
        ):
            entities.append(FluidraAutoModeSwitch(coordinator, coordinator.api, pool_id, device_id))

        if DeviceIdentifier.has_feature(device, "schedules"):
            schedule_count = DeviceIdentifier.get_feature(device, "schedule_count", 8)
            for schedule_id in [str(i) for i in range(1, schedule_count + 1)]:
                entities.append(
                    FluidraScheduleEnableSwitch(coordinator, coordinator.api, pool_id, device_id, schedule_id)
                )

        if DeviceIdentifier.has_feature(device, "boost_mode"):
            entities.append(FluidraChlorinatorBoostSwitch(coordinator, coordinator.api, pool_id, device_id))

        return entities

    await async_setup_dynamic_platform(config_entry, async_add_entities, _build)
