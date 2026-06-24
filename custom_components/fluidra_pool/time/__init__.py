"""Time platform for Fluidra Pool integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.time import TimeEntity
from homeassistant.core import callback

from ..const import FluidraPoolConfigEntry
from ..device_registry import DeviceIdentifier
from .base import (
    FluidraLightScheduleTimeEntity,
    FluidraScheduleTimeEntity,
    parse_schedule_time,
)
from .light import FluidraLightScheduleEndTimeEntity, FluidraLightScheduleStartTimeEntity
from .schedule import FluidraScheduleEndTimeEntity, FluidraScheduleStartTimeEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

__all__ = [
    "FluidraLightScheduleEndTimeEntity",
    "FluidraLightScheduleStartTimeEntity",
    "FluidraLightScheduleTimeEntity",
    "FluidraScheduleEndTimeEntity",
    "FluidraScheduleStartTimeEntity",
    "FluidraScheduleTimeEntity",
    "async_setup_entry",
    "parse_schedule_time",
]

PARALLEL_UPDATES = 0  # Coordinator handles all updates


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: FluidraPoolConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Fluidra Pool time entities, including devices added later."""
    coordinator = config_entry.runtime_data.coordinator
    known_devices: set[str] = set()

    @callback
    def _add_entities(pools: list[dict[str, Any]]) -> None:
        """Create entities for any device not seen yet (dynamic-devices)."""
        entities: list[TimeEntity] = []

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

                config = DeviceIdentifier.identify_device(device)
                device_type = config.device_type if config else device.get("type", "")

                # Heat pumps don't expose schedule controls.
                if DeviceIdentifier.has_feature(device, "skip_schedules"):
                    continue

                if device_type == "light":
                    # LumiPlus Connect lights — only create entities for existing schedules.
                    schedule_data = []
                    if coordinator.data:
                        pool_data = coordinator.data.get(pool_id, {})
                        for dev in pool_data.get("devices", []):
                            if dev.get("device_id") == device_id:
                                schedule_data = dev.get("schedule_data", [])
                                break

                    if schedule_data:
                        for schedule in schedule_data:
                            schedule_id = str(schedule.get("id", ""))
                            if schedule_id:
                                entities.append(
                                    FluidraLightScheduleStartTimeEntity(
                                        coordinator, coordinator.api, pool_id, device_id, schedule_id
                                    )
                                )
                                entities.append(
                                    FluidraLightScheduleEndTimeEntity(
                                        coordinator, coordinator.api, pool_id, device_id, schedule_id
                                    )
                                )
                elif device_type == "pump" and DeviceIdentifier.should_create_entity(device, "time"):
                    # Pumps: 8 schedulers on component 20.
                    for schedule_id in ["1", "2", "3", "4", "5", "6", "7", "8"]:
                        entities.append(
                            FluidraScheduleStartTimeEntity(
                                coordinator, coordinator.api, pool_id, device_id, schedule_id
                            )
                        )
                        entities.append(
                            FluidraScheduleEndTimeEntity(coordinator, coordinator.api, pool_id, device_id, schedule_id)
                        )
                elif device_type == "chlorinator" and DeviceIdentifier.has_feature(device, "schedules"):
                    # Chlorinators with schedules (e.g., DM24049704).
                    schedule_count = DeviceIdentifier.get_feature(device, "schedule_count", 3)
                    for i in range(1, schedule_count + 1):
                        schedule_id = str(i)
                        entities.append(
                            FluidraScheduleStartTimeEntity(
                                coordinator, coordinator.api, pool_id, device_id, schedule_id
                            )
                        )
                        entities.append(
                            FluidraScheduleEndTimeEntity(coordinator, coordinator.api, pool_id, device_id, schedule_id)
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
