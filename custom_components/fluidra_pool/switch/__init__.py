"""Switch platform for Fluidra Pool integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import callback

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
    """Set up Fluidra Pool switch entities, including devices added later."""
    coordinator = config_entry.runtime_data.coordinator
    known_devices: set[str] = set()

    @callback
    def _add_entities(pools: list[dict[str, Any]]) -> None:
        """Create switches for any device not seen yet (dynamic-devices)."""
        entities: list[SwitchEntity] = []

        for pool in pools:
            pool_id = pool["id"]

            for device in pool.get("devices", []):
                device_id = device.get("device_id")
                if not device_id:
                    continue

                key = f"{pool_id}_{device_id}"
                if key in known_devices:
                    continue
                known_devices.add(key)

                if DeviceIdentifier.should_create_entity(device, "switch"):
                    device_config = DeviceIdentifier.identify_device(device)
                    if device_config:
                        device_type = device_config.device_type

                        if device_type == "heat_pump":
                            entities.append(FluidraHeatPumpSwitch(coordinator, coordinator.api, pool_id, device_id))
                        elif device_type == "pump":
                            entities.append(FluidraPumpSwitch(coordinator, coordinator.api, pool_id, device_id))
                        elif device_type == "heater":
                            entities.append(FluidraHeaterSwitch(coordinator, coordinator.api, pool_id, device_id))
                        elif device_type == "chlorinator" and DeviceIdentifier.has_feature(device, "on_off_component"):
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
