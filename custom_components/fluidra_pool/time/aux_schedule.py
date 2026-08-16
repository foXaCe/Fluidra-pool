"""Auxiliary-output schedule start & end time entities (eXO iQ c22/c24).

The eXO iQ keeps aux-output schedules on fixed registers independent of the
pump type — Aux 1 on c22, Aux 2 on c24 — with up to two slots each (Issue
#174). They behave exactly like the pump/chlorination schedule time entities
but read from / write to the aux-specific register.
"""

from __future__ import annotations

import asyncio
from datetime import time
import logging
from typing import TYPE_CHECKING, Any

import aiohttp
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from ..api_resilience import FluidraError
from ..const import COMMAND_CONFIRMATION_DELAY, DOMAIN
from .base import FluidraScheduleTimeEntity
from .schedule import _replace_cron_time

if TYPE_CHECKING:
    from ..coordinator import FluidraDataUpdateCoordinator
    from ..fluidra_api import FluidraPoolAPI

_LOGGER = logging.getLogger(__name__)


class _FluidraAuxScheduleTimeEntity(FluidraScheduleTimeEntity):
    """Shared start/end logic for one auxiliary-output schedule slot."""

    __slots__ = ("_aux_number",)

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api: FluidraPoolAPI,
        pool_id: str,
        device_id: str,
        aux_number: str,
        schedule_id: str,
        time_type: str,
    ) -> None:
        """Initialize the aux schedule time entity."""
        super().__init__(coordinator, api, pool_id, device_id, schedule_id, time_type, aux_number=aux_number)
        self._aux_number = aux_number

        self._attr_translation_placeholders = {"aux_number": aux_number, "schedule_id": schedule_id}
        self._attr_entity_category = EntityCategory.CONFIG

    async def _async_set_time(self, value: time) -> None:
        """Rewrite only the target slot's time field and PUT the aux register."""
        self._optimistic_value = value
        self.async_write_ha_state()

        current_schedules = self._get_schedule_list()
        if not current_schedules:
            self._optimistic_value = None
            self.async_write_ha_state()
            return

        field = "startTime" if self._time_type == "start" else "endTime"
        current_schedule = self._get_schedule_data()
        if current_schedule and self._time_type == "start":
            current_end_time = self._parse_cron_time(current_schedule.get("endTime", ""))
            if current_end_time and value < current_end_time:
                is_valid, error_msg = self._validate_schedule_overlap(value, current_end_time, self._schedule_id)
                if not is_valid:
                    raise ServiceValidationError(
                        error_msg, translation_domain=DOMAIN, translation_key="schedule_overlap"
                    )
        elif current_schedule and self._time_type == "end":
            current_start_time = self._parse_cron_time(current_schedule.get("startTime", ""))
            if current_start_time and value > current_start_time:
                is_valid, error_msg = self._validate_schedule_overlap(current_start_time, value, self._schedule_id)
                if not is_valid:
                    raise ServiceValidationError(
                        error_msg, translation_domain=DOMAIN, translation_key="schedule_overlap"
                    )

        updated_schedules: list[dict[str, Any]] = []
        for sched in current_schedules:
            entry = dict(sched)
            if str(entry.get("id")) == str(self._schedule_id):
                entry[field] = _replace_cron_time(entry.get(field, ""), value)
            entry.setdefault("groupId", entry.get("id"))
            entry.pop("state", None)
            entry.pop("endActions", None)
            updated_schedules.append(entry)

        component_id = self._get_schedule_component()
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


class FluidraAuxScheduleStartTimeEntity(_FluidraAuxScheduleTimeEntity):
    """Start time for one auxiliary-output schedule slot."""

    __slots__ = ()

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api: FluidraPoolAPI,
        pool_id: str,
        device_id: str,
        aux_number: str,
        schedule_id: str,
    ) -> None:
        """Initialize the aux schedule start time entity."""
        super().__init__(coordinator, api, pool_id, device_id, aux_number, schedule_id, "start")
        self._attr_translation_key = "aux_schedule_start"
        self._attr_unique_id = f"fluidra_{self._device_id}_aux{aux_number}_schedule_{schedule_id}_start_time"

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
            return self._parse_cron_time(schedule.get("startTime", ""))
        return None

    async def async_set_value(self, value: time) -> None:
        """Set the start time."""
        self._ensure_pool_writable()
        try:
            await self._async_set_time(value)
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
            _LOGGER.error("Failed to set aux %s schedule start time for %s: %s", self._aux_number, self._device_id, err)
            self._optimistic_value = None
            self.async_write_ha_state()
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="schedule_set_failed",
                translation_placeholders={"device_id": self._device_id},
            ) from err


class FluidraAuxScheduleEndTimeEntity(_FluidraAuxScheduleTimeEntity):
    """End time for one auxiliary-output schedule slot."""

    __slots__ = ()

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api: FluidraPoolAPI,
        pool_id: str,
        device_id: str,
        aux_number: str,
        schedule_id: str,
    ) -> None:
        """Initialize the aux schedule end time entity."""
        super().__init__(coordinator, api, pool_id, device_id, aux_number, schedule_id, "end")
        self._attr_translation_key = "aux_schedule_end"
        self._attr_unique_id = f"fluidra_{self._device_id}_aux{aux_number}_schedule_{schedule_id}_end_time"

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
            return self._parse_cron_time(schedule.get("endTime", ""))
        return None

    async def async_set_value(self, value: time) -> None:
        """Set the end time."""
        self._ensure_pool_writable()
        try:
            await self._async_set_time(value)
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
            _LOGGER.error("Failed to set aux %s schedule end time for %s: %s", self._aux_number, self._device_id, err)
            self._optimistic_value = None
            self.async_write_ha_state()
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="schedule_set_failed",
                translation_placeholders={"device_id": self._device_id},
            ) from err
