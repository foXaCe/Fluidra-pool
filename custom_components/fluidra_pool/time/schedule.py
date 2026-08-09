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
from .base import FluidraScheduleTimeEntity

if TYPE_CHECKING:
    from ..coordinator import FluidraDataUpdateCoordinator
    from ..fluidra_api import FluidraPoolAPI

_LOGGER = logging.getLogger(__name__)


def _replace_cron_time(cron_time: str, new_time: time) -> str:
    """Return ``cron_time`` with only its minute/hour replaced, days preserved.

    The day field is copied verbatim — it came straight from the API in the API's
    own numbering, so rewriting it (e.g. a 0→7 conversion) would shift the
    configured days on every write (Issue #175).
    """
    parts = cron_time.split()
    if len(parts) >= 5:
        parts[0] = str(new_time.minute)
        parts[1] = str(new_time.hour)
        return " ".join(parts)
    return f"{new_time.minute} {new_time.hour} * * 1,2,3,4,5,6,7"


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
        self._ensure_pool_writable()
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
                scheduler = dict(sched)
                if str(sched.get("id")) == str(self._schedule_id):
                    scheduler["startTime"] = _replace_cron_time(sched.get("startTime", ""), value)

                component_id = self._get_schedule_component()

                if component_id == 258:
                    # DM24049704 chlorinator uses a flat groupId=1 + padded CRON.
                    scheduler = {
                        "id": sched.get("id"),
                        "groupId": 1,
                        "enabled": True,
                        "startTime": self._format_cron_time_chlorinator(scheduler["startTime"]),
                        "endTime": self._format_cron_time_chlorinator(scheduler.get("endTime", "")),
                        "startActions": {"operationName": str(sched.get("startActions", {}).get("operationName", "1"))},
                    }
                else:
                    # Copy the entry verbatim and touch only the time being edited,
                    # so a schedule whose mode lives in componentActions (eXO iQ)
                    # keeps it instead of being rebuilt as operationName (Issue #175).
                    scheduler.setdefault("groupId", sched.get("id"))
                    scheduler["startTime"] = scheduler.get("startTime", "")
                    scheduler["endTime"] = scheduler.get("endTime", "")
                    # Read-only runtime fields the API rejects on write (Issue #89/#174).
                    scheduler.pop("state", None)
                    scheduler.pop("endActions", None)
                updated_schedules.append(scheduler)

            component_id = self._get_schedule_component()

            # Send only the configured schedules — no padding. Fluidra fills the
            # remaining device slots itself; padding to 8 with identical placeholder
            # windows is rejected as "OVERLAP in sched" (Issue #105), and a packet
            # capture of the official app confirms it sends only the real entries.
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
        self._ensure_pool_writable()
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
                scheduler = dict(sched)
                if str(sched.get("id")) == str(self._schedule_id):
                    scheduler["endTime"] = _replace_cron_time(sched.get("endTime", ""), value)

                component_id = self._get_schedule_component()

                if component_id == 258:
                    # DM24049704 chlorinator uses a flat groupId=1 + padded CRON.
                    scheduler = {
                        "id": sched.get("id"),
                        "groupId": 1,
                        "enabled": True,
                        "startTime": self._format_cron_time_chlorinator(scheduler.get("startTime", "")),
                        "endTime": self._format_cron_time_chlorinator(scheduler["endTime"]),
                        "startActions": {"operationName": str(sched.get("startActions", {}).get("operationName", "1"))},
                    }
                else:
                    # Copy the entry verbatim and touch only the time being edited
                    # (see the start-time entity; Issue #175).
                    scheduler.setdefault("groupId", sched.get("id"))
                    scheduler["startTime"] = scheduler.get("startTime", "")
                    scheduler["endTime"] = scheduler.get("endTime", "")
                    scheduler.pop("state", None)
                    scheduler.pop("endActions", None)
                updated_schedules.append(scheduler)

            component_id = self._get_schedule_component()

            # No padding — see the OVERLAP-in-sched note in the slot editor above (Issue #105).
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
