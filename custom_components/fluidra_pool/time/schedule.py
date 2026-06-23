"""Pump/chlorinator schedule start & end time entities."""

from __future__ import annotations

import asyncio
from datetime import time
import logging
from typing import TYPE_CHECKING

import aiohttp
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from ..api_resilience import FluidraError
from ..const import COMMAND_CONFIRMATION_DELAY, DOMAIN
from ..utils import convert_cron_days
from .base import FluidraScheduleTimeEntity

if TYPE_CHECKING:
    from ..coordinator import FluidraDataUpdateCoordinator
    from ..fluidra_api import FluidraPoolAPI

_LOGGER = logging.getLogger(__name__)


class FluidraScheduleStartTimeEntity(FluidraScheduleTimeEntity):
    """Time entity for schedule start time."""

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

        self._attr_translation_key = "schedule_start"
        self._attr_translation_placeholders = {"schedule_id": schedule_id}
        self._attr_unique_id = f"fluidra_{self._device_id}_{schedule_id}_start_time"
        self._attr_entity_category = EntityCategory.CONFIG

    @property
    def icon(self) -> str:
        """Return the icon for the entity."""
        return "mdi:clock-start"

    @property
    def native_value(self) -> time | None:
        """Return the current start time."""
        if self._optimistic_value is not None:
            return self._optimistic_value
        schedule = self._get_schedule_data()
        if schedule:
            start_time_str = schedule.get("startTime", "")
            return self._parse_cron_time(start_time_str)
        return None

    async def async_set_value(self, value: time) -> None:
        """Set the start time using exact mobile app format."""
        try:
            self._optimistic_value = value
            self.async_write_ha_state()

            device_data = self.device_data
            if "schedule_data" not in device_data:
                self._optimistic_value = None
                self.async_write_ha_state()
                return

            current_schedules = device_data["schedule_data"]
            if not current_schedules:
                self._optimistic_value = None
                self.async_write_ha_state()
                return

            current_schedule = self._get_schedule_data()
            if current_schedule:
                current_end_time = self._parse_cron_time(current_schedule.get("endTime", ""))
                # Only validate a forward, same-day window. An inverted pair
                # (start > end) mid-edit is an in-progress state, not a real
                # overnight range, so skip the overlap check (the user usually
                # fixes the other endpoint next; the device is the final arbiter).
                if current_end_time and value < current_end_time:
                    is_valid, error_msg = self._validate_schedule_overlap(value, current_end_time, self._schedule_id)
                    if not is_valid:
                        raise ServiceValidationError(
                            error_msg, translation_domain=DOMAIN, translation_key="schedule_overlap"
                        )

            updated_schedules = []
            for sched in current_schedules:
                start_time = convert_cron_days(sched.get("startTime", ""))
                end_time = convert_cron_days(sched.get("endTime", ""))

                if str(sched.get("id")) == str(self._schedule_id):
                    current_cron = sched.get("startTime", "")
                    days = [1, 2, 3, 4, 5, 6, 7]  # Default to every day (mobile format).
                    if current_cron:
                        parts = current_cron.split()
                        if len(parts) >= 5:
                            try:
                                old_days = [int(d) for d in parts[4].split(",")]
                                days = []
                                for day in old_days:
                                    if day == 0:  # CRON Sunday 0 → mobile Sunday 7.
                                        days.append(7)
                                    else:
                                        days.append(day)
                                days = sorted(days)
                            except (ValueError, TypeError):
                                pass

                    start_time = self._format_time_to_cron(value, days)

                component_id = self._get_schedule_component()

                if component_id == 258:
                    # DM24049704 chlorinator uses a flat groupId=1 + padded CRON.
                    scheduler = {
                        "id": sched.get("id"),
                        "groupId": 1,
                        "enabled": True,
                        "startTime": self._format_cron_time_chlorinator(start_time),
                        "endTime": self._format_cron_time_chlorinator(end_time),
                        "startActions": {"operationName": str(sched.get("startActions", {}).get("operationName", "1"))},
                    }
                else:
                    scheduler = {
                        "id": sched.get("id"),
                        "groupId": sched.get("id"),
                        "enabled": sched.get("enabled", False),
                        "startTime": start_time,
                        "endTime": end_time,
                        "startActions": {"operationName": str(sched.get("startActions", {}).get("operationName", "0"))},
                    }
                updated_schedules.append(scheduler)

            component_id = self._get_schedule_component()

            # Pumps expect exactly 8 schedulers — pad with safe defaults.
            if component_id == 20:
                while len(updated_schedules) < 8:
                    missing_id = len(updated_schedules) + 1
                    updated_schedules.append(
                        {
                            "id": missing_id,
                            "groupId": missing_id,
                            "enabled": False,
                            "startTime": "00 00 * * 1,2,3,4,5,6,7",
                            "endTime": "00 01 * * 1,2,3,4,5,6,7",
                            "startActions": {"operationName": "0"},
                        }
                    )

            success = await self._api.set_schedule(self._device_id, updated_schedules, component_id=component_id)
            if not success:
                self._optimistic_value = None
                self.async_write_ha_state()
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="schedule_set_rejected",
                    translation_placeholders={"device_id": self._device_id},
                )
            await asyncio.sleep(COMMAND_CONFIRMATION_DELAY)
            await self.coordinator.async_request_refresh()
            self._optimistic_value = None
            self.async_write_ha_state()

        except HomeAssistantError:
            self._optimistic_value = None
            self.async_write_ha_state()
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
            _LOGGER.error("Failed to set schedule start time for %s: %s", self._device_id, err)
            self._optimistic_value = None
            self.async_write_ha_state()
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="schedule_set_failed",
                translation_placeholders={"device_id": self._device_id},
            ) from err


class FluidraScheduleEndTimeEntity(FluidraScheduleTimeEntity):
    """Time entity for schedule end time."""

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

        self._attr_translation_key = "schedule_end"
        self._attr_translation_placeholders = {"schedule_id": schedule_id}
        self._attr_unique_id = f"fluidra_{self._device_id}_{schedule_id}_end_time"
        self._attr_entity_category = EntityCategory.CONFIG

    @property
    def icon(self) -> str:
        """Return the icon for the entity."""
        return "mdi:clock-end"

    @property
    def native_value(self) -> time | None:
        """Return the current end time."""
        if self._optimistic_value is not None:
            return self._optimistic_value
        schedule = self._get_schedule_data()
        if schedule:
            end_time_str = schedule.get("endTime", "")
            return self._parse_cron_time(end_time_str)
        return None

    async def async_set_value(self, value: time) -> None:
        """Set the end time using exact mobile app format."""
        try:
            self._optimistic_value = value
            self.async_write_ha_state()

            device_data = self.device_data
            if "schedule_data" not in device_data:
                self._optimistic_value = None
                self.async_write_ha_state()
                return

            current_schedules = device_data["schedule_data"]
            if not current_schedules:
                self._optimistic_value = None
                self.async_write_ha_state()
                return

            current_schedule = self._get_schedule_data()
            if current_schedule:
                current_start_time = self._parse_cron_time(current_schedule.get("startTime", ""))
                # Only validate a forward, same-day window (see start-time entity).
                if current_start_time and value > current_start_time:
                    is_valid, error_msg = self._validate_schedule_overlap(current_start_time, value, self._schedule_id)
                    if not is_valid:
                        raise ServiceValidationError(
                            error_msg, translation_domain=DOMAIN, translation_key="schedule_overlap"
                        )

            updated_schedules = []
            for sched in current_schedules:
                start_time = convert_cron_days(sched.get("startTime", ""))
                end_time = convert_cron_days(sched.get("endTime", ""))

                if str(sched.get("id")) == str(self._schedule_id):
                    current_cron = sched.get("endTime", "")
                    days = [1, 2, 3, 4, 5, 6, 7]
                    if current_cron:
                        parts = current_cron.split()
                        if len(parts) >= 5:
                            try:
                                old_days = [int(d) for d in parts[4].split(",")]
                                days = []
                                for day in old_days:
                                    if day == 0:
                                        days.append(7)
                                    else:
                                        days.append(day)
                                days = sorted(days)
                            except (ValueError, TypeError):
                                pass

                    end_time = self._format_time_to_cron(value, days)

                component_id = self._get_schedule_component()

                if component_id == 258:
                    scheduler = {
                        "id": sched.get("id"),
                        "groupId": 1,
                        "enabled": True,
                        "startTime": self._format_cron_time_chlorinator(start_time),
                        "endTime": self._format_cron_time_chlorinator(end_time),
                        "startActions": {"operationName": str(sched.get("startActions", {}).get("operationName", "1"))},
                    }
                else:
                    scheduler = {
                        "id": sched.get("id"),
                        "groupId": sched.get("id"),
                        "enabled": sched.get("enabled", False),
                        "startTime": start_time,
                        "endTime": end_time,
                        "startActions": {"operationName": str(sched.get("startActions", {}).get("operationName", "0"))},
                    }
                updated_schedules.append(scheduler)

            component_id = self._get_schedule_component()

            if component_id == 20:
                while len(updated_schedules) < 8:
                    missing_id = len(updated_schedules) + 1
                    updated_schedules.append(
                        {
                            "id": missing_id,
                            "groupId": missing_id,
                            "enabled": False,
                            "startTime": "00 00 * * 1,2,3,4,5,6,7",
                            "endTime": "00 01 * * 1,2,3,4,5,6,7",
                            "startActions": {"operationName": "0"},
                        }
                    )

            success = await self._api.set_schedule(self._device_id, updated_schedules, component_id=component_id)
            if not success:
                self._optimistic_value = None
                self.async_write_ha_state()
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="schedule_set_rejected",
                    translation_placeholders={"device_id": self._device_id},
                )
            await asyncio.sleep(COMMAND_CONFIRMATION_DELAY)
            await self.coordinator.async_request_refresh()
            self._optimistic_value = None
            self.async_write_ha_state()

        except HomeAssistantError:
            self._optimistic_value = None
            self.async_write_ha_state()
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
            _LOGGER.error("Failed to set schedule end time for %s: %s", self._device_id, err)
            self._optimistic_value = None
            self.async_write_ha_state()
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="schedule_set_failed",
                translation_placeholders={"device_id": self._device_id},
            ) from err
