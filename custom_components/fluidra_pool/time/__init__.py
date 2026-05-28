"""Time platform for Fluidra Pool integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.time import TimeEntity

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
    """Set up Fluidra Pool time entities."""
    coordinator = config_entry.runtime_data.coordinator

    entities: list[TimeEntity] = []

    # Use cached pools data instead of API call for faster startup.
    pools = coordinator.api.cached_pools or await coordinator.api.get_pools()
    for pool in pools:
        for device in pool["devices"]:
            device_id = device.get("device_id")
            config = DeviceIdentifier.identify_device(device)
            device_type = config.device_type if config else device.get("type", "")

            if not device_id:
                continue

            # Heat pumps don't expose schedule controls.
            if DeviceIdentifier.has_feature(device, "skip_schedules"):
                continue

            if device_type == "light":
                # LumiPlus Connect lights — only create entities for existing schedules.
                schedule_data = []
                if coordinator.data:
                    pool_data = coordinator.data.get(pool["id"], {})
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
                                    coordinator, coordinator.api, pool["id"], device_id, schedule_id
                                )
                            )
                            entities.append(
                                FluidraLightScheduleEndTimeEntity(
                                    coordinator, coordinator.api, pool["id"], device_id, schedule_id
                                )
                            )
            elif device_type == "pump" and DeviceIdentifier.should_create_entity(device, "time"):
                # Pumps: 8 schedulers on component 20.
                for schedule_id in ["1", "2", "3", "4", "5", "6", "7", "8"]:
                    entities.append(
                        FluidraScheduleStartTimeEntity(coordinator, coordinator.api, pool["id"], device_id, schedule_id)
                    )
                    entities.append(
                        FluidraScheduleEndTimeEntity(coordinator, coordinator.api, pool["id"], device_id, schedule_id)
                    )
            elif device_type == "chlorinator" and DeviceIdentifier.has_feature(device, "schedules"):
                # Chlorinators with schedules (e.g., DM24049704).
                schedule_count = DeviceIdentifier.get_feature(device, "schedule_count", 3)
                for i in range(1, schedule_count + 1):
                    schedule_id = str(i)
                    entities.append(
                        FluidraScheduleStartTimeEntity(coordinator, coordinator.api, pool["id"], device_id, schedule_id)
                    )
                    entities.append(
                        FluidraScheduleEndTimeEntity(coordinator, coordinator.api, pool["id"], device_id, schedule_id)
                    )

    async_add_entities(entities)
