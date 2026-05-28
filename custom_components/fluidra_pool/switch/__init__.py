"""Switch platform for Fluidra Pool integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.switch import SwitchEntity

from ..const import FluidraPoolConfigEntry
from ..device_registry import DeviceIdentifier
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
    """Set up Fluidra Pool switch entities."""
    coordinator = config_entry.runtime_data.coordinator

    entities: list[SwitchEntity] = []

    # Use cached pools data instead of API call for faster startup
    pools = coordinator.api.cached_pools or await coordinator.api.get_pools()
    for pool in pools:
        for device in pool["devices"]:
            device_id = device.get("device_id")

            if not device_id:
                continue

            if DeviceIdentifier.should_create_entity(device, "switch"):
                device_config = DeviceIdentifier.identify_device(device)
                if device_config:
                    device_type = device_config.device_type

                    if device_type == "heat_pump":
                        entities.append(FluidraHeatPumpSwitch(coordinator, coordinator.api, pool["id"], device_id))
                    elif device_type == "pump":
                        entities.append(FluidraPumpSwitch(coordinator, coordinator.api, pool["id"], device_id))
                    elif device_type == "heater":
                        entities.append(FluidraHeaterSwitch(coordinator, coordinator.api, pool["id"], device_id))
                    elif device_type == "chlorinator" and DeviceIdentifier.has_feature(device, "on_off_component"):
                        entities.append(FluidraChlorinatorSwitch(coordinator, coordinator.api, pool["id"], device_id))

            if DeviceIdentifier.should_create_entity(device, "switch_auto") and not DeviceIdentifier.has_feature(
                device, "skip_auto_mode"
            ):
                entities.append(FluidraAutoModeSwitch(coordinator, coordinator.api, pool["id"], device_id))

            if DeviceIdentifier.has_feature(device, "schedules"):
                schedule_count = DeviceIdentifier.get_feature(device, "schedule_count", 8)
                for schedule_id in [str(i) for i in range(1, schedule_count + 1)]:
                    entities.append(
                        FluidraScheduleEnableSwitch(coordinator, coordinator.api, pool["id"], device_id, schedule_id)
                    )

            if DeviceIdentifier.has_feature(device, "boost_mode"):
                entities.append(FluidraChlorinatorBoostSwitch(coordinator, coordinator.api, pool["id"], device_id))

    async_add_entities(entities)
