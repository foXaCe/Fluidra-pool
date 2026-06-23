"""LumiPlus Connect light schedule start & end time entities."""

from __future__ import annotations

from datetime import time
import logging
from typing import TYPE_CHECKING

import aiohttp
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError

from ..api_resilience import FluidraError
from ..const import DOMAIN
from .base import FluidraLightScheduleTimeEntity

if TYPE_CHECKING:
    from ..coordinator import FluidraDataUpdateCoordinator
    from ..fluidra_api import FluidraPoolAPI

_LOGGER = logging.getLogger(__name__)


class FluidraLightScheduleStartTimeEntity(FluidraLightScheduleTimeEntity):
    """Time entity for LumiPlus Connect light schedule start time."""

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api: FluidraPoolAPI,
        pool_id: str,
        device_id: str,
        schedule_id: str,
    ) -> None:
        """Initialize the start time entity."""
        super().__init__(coordinator, api, pool_id, device_id, schedule_id, "start")

        self._attr_translation_key = "light_schedule_start"
        self._attr_translation_placeholders = {"schedule_id": schedule_id}
        self._attr_unique_id = f"fluidra_{self._device_id}_light_{schedule_id}_start_time"
        self._attr_entity_category = EntityCategory.CONFIG

    @property
    def icon(self) -> str:
        """Return the icon for the entity."""
        return "mdi:clock-start"

    @property
    def native_value(self) -> time | None:
        """Return the current start time."""
        schedule = self._get_schedule_data()
        if schedule:
            start_time_str = schedule.get("startTime", "")
            return self._parse_cron_time(start_time_str)
        return None

    async def async_set_value(self, value: time) -> None:
        """Set the start time."""
        try:
            device_data = self.device_data
            if "schedule_data" not in device_data:
                return

            current_schedules = device_data["schedule_data"]
            if not current_schedules:
                return

            updated_schedules = []
            for sched in current_schedules:
                start_time = sched.get("startTime", "00 00 * * 1,2,3,4,5,6,7")
                end_time = sched.get("endTime", "00 01 * * 1,2,3,4,5,6,7")

                if str(sched.get("id")) == str(self._schedule_id):
                    days = [1, 2, 3, 4, 5, 6, 7]
                    if start_time:
                        parts = start_time.split()
                        if len(parts) >= 5:
                            try:
                                days = [int(d) for d in parts[4].split(",")]
                            except (ValueError, TypeError):
                                pass
                    start_time = self._format_time_to_cron(value, days)

                scheduler = {
                    "id": sched.get("id"),
                    "groupId": sched.get("groupId", sched.get("id")),
                    "enabled": sched.get("enabled", False),
                    "startTime": start_time,
                    "endTime": end_time,
                    "startActions": sched.get("startActions", {"operationName": "11"}),
                }
                updated_schedules.append(scheduler)

            success = await self._api.set_schedule(self._device_id, updated_schedules, component_id=40)
            if not success:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="light_schedule_set_failed",
                    translation_placeholders={"schedule_id": str(self._schedule_id)},
                )
            await self.coordinator.async_request_refresh()

        except HomeAssistantError:
            raise
        except (
            aiohttp.ClientError,
            TimeoutError,
            FluidraError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
        ) as err:
            _LOGGER.error("Failed to set light schedule start time for %s: %s", self._device_id, err)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="light_schedule_set_failed",
                translation_placeholders={"schedule_id": str(self._schedule_id)},
            ) from err


class FluidraLightScheduleEndTimeEntity(FluidraLightScheduleTimeEntity):
    """Time entity for LumiPlus Connect light schedule end time."""

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api: FluidraPoolAPI,
        pool_id: str,
        device_id: str,
        schedule_id: str,
    ) -> None:
        """Initialize the end time entity."""
        super().__init__(coordinator, api, pool_id, device_id, schedule_id, "end")

        self._attr_translation_key = "light_schedule_end"
        self._attr_translation_placeholders = {"schedule_id": schedule_id}
        self._attr_unique_id = f"fluidra_{self._device_id}_light_{schedule_id}_end_time"
        self._attr_entity_category = EntityCategory.CONFIG

    @property
    def icon(self) -> str:
        """Return the icon for the entity."""
        return "mdi:clock-end"

    @property
    def native_value(self) -> time | None:
        """Return the current end time."""
        schedule = self._get_schedule_data()
        if schedule:
            end_time_str = schedule.get("endTime", "")
            return self._parse_cron_time(end_time_str)
        return None

    async def async_set_value(self, value: time) -> None:
        """Set the end time."""
        try:
            device_data = self.device_data
            if "schedule_data" not in device_data:
                return

            current_schedules = device_data["schedule_data"]
            if not current_schedules:
                return

            updated_schedules = []
            for sched in current_schedules:
                start_time = sched.get("startTime", "00 00 * * 1,2,3,4,5,6,7")
                end_time = sched.get("endTime", "00 01 * * 1,2,3,4,5,6,7")

                if str(sched.get("id")) == str(self._schedule_id):
                    days = [1, 2, 3, 4, 5, 6, 7]
                    if end_time:
                        parts = end_time.split()
                        if len(parts) >= 5:
                            try:
                                days = [int(d) for d in parts[4].split(",")]
                            except (ValueError, TypeError):
                                pass
                    end_time = self._format_time_to_cron(value, days)

                scheduler = {
                    "id": sched.get("id"),
                    "groupId": sched.get("groupId", sched.get("id")),
                    "enabled": sched.get("enabled", False),
                    "startTime": start_time,
                    "endTime": end_time,
                    "startActions": sched.get("startActions", {"operationName": "11"}),
                }
                updated_schedules.append(scheduler)

            success = await self._api.set_schedule(self._device_id, updated_schedules, component_id=40)
            if not success:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="light_schedule_set_failed",
                    translation_placeholders={"schedule_id": str(self._schedule_id)},
                )
            await self.coordinator.async_request_refresh()

        except HomeAssistantError:
            raise
        except (
            aiohttp.ClientError,
            TimeoutError,
            FluidraError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
        ) as err:
            _LOGGER.error("Failed to set light schedule end time for %s: %s", self._device_id, err)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="light_schedule_set_failed",
                translation_placeholders={"schedule_id": str(self._schedule_id)},
            ) from err
