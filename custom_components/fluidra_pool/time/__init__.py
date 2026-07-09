"""Time platform for Fluidra Pool integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.time import TimeEntity

from ..const import (
    DEVICE_TYPE_CHLORINATOR,
    DEVICE_TYPE_LIGHT,
    DEVICE_TYPE_PUMP,
    FluidraPoolConfigEntry,
)
from ..device_registry import DeviceIdentifier
from ..platform_setup import async_setup_dynamic_platform
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

    def _build(pool_id: str, device: dict[str, Any]) -> list[TimeEntity]:
        """Create time entities for one device."""
        entities: list[TimeEntity] = []
        device_id = device["device_id"]

        config = DeviceIdentifier.identify_device(device)
        device_type = config.device_type if config else device.get("type", "")

        # Heat pumps don't expose schedule controls.
        if DeviceIdentifier.has_feature(device, "skip_schedules"):
            return entities

        if device_type == DEVICE_TYPE_LIGHT:
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
        elif device_type == DEVICE_TYPE_PUMP and DeviceIdentifier.should_create_entity(device, "time"):
            # Pumps: 8 schedulers on component 20.
            for schedule_id in ["1", "2", "3", "4", "5", "6", "7", "8"]:
                entities.append(
                    FluidraScheduleStartTimeEntity(coordinator, coordinator.api, pool_id, device_id, schedule_id)
                )
                entities.append(
                    FluidraScheduleEndTimeEntity(coordinator, coordinator.api, pool_id, device_id, schedule_id)
                )
        elif device_type == DEVICE_TYPE_CHLORINATOR and DeviceIdentifier.has_feature(device, "schedules"):
            # Chlorinators with schedules (e.g., DM24049704).
            schedule_count = DeviceIdentifier.get_feature(device, "schedule_count", 3)
            for i in range(1, schedule_count + 1):
                schedule_id = str(i)
                entities.append(
                    FluidraScheduleStartTimeEntity(coordinator, coordinator.api, pool_id, device_id, schedule_id)
                )
                entities.append(
                    FluidraScheduleEndTimeEntity(coordinator, coordinator.api, pool_id, device_id, schedule_id)
                )

        return entities

    await async_setup_dynamic_platform(config_entry, async_add_entities, _build)
