"""Command Connect cabinet schedule start & end time entities (c35/c36).

The AstralPool Command Connect keeps two independent schedulers on fixed
registers — filtration pump on c35 (r1), pool lights on c36 (r2) — with one
slot each in the captures from @efgonzalez (Issue #210).

A schedule is an *armed window*, not a guaranteed stop: ending the window has
been observed not to stop an output started manually. Times are local
wall-clock despite the bridge reporting GMT0. Writes must omit the runtime
``state`` field; clearing is an empty list ``[]``. HTTP 200 alone is not proof
the write landed — ``set_schedule`` arms write verification.
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
from ..entity import FluidraPoolEntity
from ..helpers import (
    CABINET_SCHEDULE_ALL_DAYS,
    build_cabinet_schedule_slot,
    get_cabinet_schedule_data,
    resolve_cabinet_schedule_component,
)
from .base import FluidraScheduleTimeEntity
from .schedule import _replace_cron_time

if TYPE_CHECKING:
    from ..coordinator import FluidraDataUpdateCoordinator
    from ..fluidra_api import FluidraPoolAPI

_LOGGER = logging.getLogger(__name__)

_OUTPUT_LABELS = {"pump": "Filtration", "lights": "Lights"}


def _default_pair(edited: time, time_type: str) -> tuple[time, time]:
    """Pick a same-day partner when seeding a slot from a single endpoint."""
    if time_type == "start":
        end_minutes = (edited.hour * 60 + edited.minute + 60) % (24 * 60)
        return edited, time(end_minutes // 60, end_minutes % 60)
    start_minutes = edited.hour * 60 + edited.minute - 60
    if start_minutes < 0:
        start_minutes += 24 * 60
    return time(start_minutes // 60, start_minutes % 60), edited


class _FluidraCabinetScheduleTimeEntity(FluidraScheduleTimeEntity):
    """Shared start/end logic for one Command Connect cabinet schedule slot."""

    __slots__ = ("_cabinet_output",)

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api: FluidraPoolAPI,
        pool_id: str,
        device_id: str,
        cabinet_output: str,
        schedule_id: str,
        time_type: str,
    ) -> None:
        """Initialize the cabinet schedule time entity."""
        # Reuse the aux_number slot on the base class as a channel key so the
        # shared list/component helpers stay unused; cabinet paths override them.
        super().__init__(coordinator, api, pool_id, device_id, schedule_id, time_type, aux_number=None)
        self._cabinet_output = cabinet_output
        self._attr_translation_placeholders = {
            "output": _OUTPUT_LABELS.get(cabinet_output, cabinet_output),
            "schedule_id": schedule_id,
        }
        self._attr_entity_category = EntityCategory.CONFIG

    def _get_schedule_data(self) -> dict[str, Any] | None:
        """Return this output's schedule slot from cabinet_schedule_data."""
        try:
            return get_cabinet_schedule_data(self.device_data, self._cabinet_output, self._schedule_id)
        except (aiohttp.ClientError, TimeoutError, FluidraError, ValueError, TypeError, KeyError, AttributeError):
            _LOGGER.debug("Failed to get cabinet %s schedule data for %s", self._cabinet_output, self._device_id)
            return None

    def _get_schedule_list(self) -> list[dict[str, Any]]:
        """Return the schedule list for this cabinet output."""
        schedules: list[dict[str, Any]] = (self.device_data.get("cabinet_schedule_data") or {}).get(
            str(self._cabinet_output), []
        )
        return schedules

    def _get_schedule_component(self) -> int:
        """Return c35 (pump) or c36 (lights) for this output."""
        return resolve_cabinet_schedule_component(self.device_data, self._cabinet_output)

    @property
    def available(self) -> bool:
        """Stay available when the slot is empty so a clear can be rewritten."""
        return FluidraPoolEntity.available.fget(self)  # type: ignore[misc]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose armed-window semantics and the live register."""
        schedule = self._get_schedule_data()
        attrs: dict[str, Any] = {
            "cabinet_output": self._cabinet_output,
            "schedule_component": self._get_schedule_component(),
            # Verified behaviour (Issue #210): the schedule arms a window; it
            # does not guarantee the output stops when the window ends.
            "schedule_semantics": "armed_window",
            "schedule_local_time": True,
        }
        if schedule:
            attrs["schedule_state"] = schedule.get("state")
            attrs["enabled"] = schedule.get("enabled")
        return attrs

    async def _async_set_time(self, value: time) -> None:
        """Rewrite this slot's time (or seed a new slot) and PUT the register."""
        self._optimistic_value = value
        self.async_write_ha_state()

        current_schedules = list(self._get_schedule_list())
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

        if not current_schedules:
            start, end = _default_pair(value, self._time_type)
            updated_schedules = [build_cabinet_schedule_slot(int(self._schedule_id), start, end, enabled=True)]
        else:
            updated_schedules = []
            found = False
            for sched in current_schedules:
                entry = dict(sched)
                if str(entry.get("id")) == str(self._schedule_id):
                    found = True
                    existing = entry.get(field, "")
                    if existing:
                        entry[field] = _replace_cron_time(existing, value)
                    else:
                        entry[field] = f"{value.minute} {value.hour} * * {CABINET_SCHEDULE_ALL_DAYS}"
                entry.setdefault("groupId", entry.get("id"))
                entry.pop("state", None)
                entry.pop("endActions", None)
                if not isinstance(entry.get("startActions"), dict):
                    entry["startActions"] = {"operationName": str(entry.get("id", self._schedule_id))}
                updated_schedules.append(entry)
            if not found:
                start, end = _default_pair(value, self._time_type)
                updated_schedules.append(build_cabinet_schedule_slot(int(self._schedule_id), start, end, enabled=True))

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


class FluidraCabinetScheduleStartTimeEntity(_FluidraCabinetScheduleTimeEntity):
    """Start time for one Command Connect cabinet schedule slot."""

    __slots__ = ()

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api: FluidraPoolAPI,
        pool_id: str,
        device_id: str,
        cabinet_output: str,
        schedule_id: str,
    ) -> None:
        """Initialize the cabinet schedule start time entity."""
        super().__init__(coordinator, api, pool_id, device_id, cabinet_output, schedule_id, "start")
        self._attr_translation_key = "cabinet_schedule_start"
        self._attr_unique_id = f"fluidra_{self._device_id}_cabinet_{cabinet_output}_schedule_{schedule_id}_start_time"

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
            _LOGGER.error(
                "Failed to set cabinet %s schedule start time for %s: %s",
                self._cabinet_output,
                self._device_id,
                err,
            )
            self._optimistic_value = None
            self.async_write_ha_state()
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="schedule_set_failed",
                translation_placeholders={"device_id": self._device_id},
            ) from err


class FluidraCabinetScheduleEndTimeEntity(_FluidraCabinetScheduleTimeEntity):
    """End time for one Command Connect cabinet schedule slot."""

    __slots__ = ()

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api: FluidraPoolAPI,
        pool_id: str,
        device_id: str,
        cabinet_output: str,
        schedule_id: str,
    ) -> None:
        """Initialize the cabinet schedule end time entity."""
        super().__init__(coordinator, api, pool_id, device_id, cabinet_output, schedule_id, "end")
        self._attr_translation_key = "cabinet_schedule_end"
        self._attr_unique_id = f"fluidra_{self._device_id}_cabinet_{cabinet_output}_schedule_{schedule_id}_end_time"

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
            _LOGGER.error(
                "Failed to set cabinet %s schedule end time for %s: %s",
                self._cabinet_output,
                self._device_id,
                err,
            )
            self._optimistic_value = None
            self.async_write_ha_state()
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="schedule_set_failed",
                translation_placeholders={"device_id": self._device_id},
            ) from err
