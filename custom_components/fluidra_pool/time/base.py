"""Base time-entity classes and the schedule-time parser."""

from __future__ import annotations

from datetime import time
import logging
from typing import TYPE_CHECKING, Any

import aiohttp
from homeassistant.components.time import TimeEntity
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo

from ..api_resilience import FluidraError
from ..const import DEVICE_MODEL_FALLBACK, DEVICE_MODEL_MAP, DOMAIN
from ..device_registry import DeviceIdentifier
from ..entity import FluidraPoolControlEntity
from ..helpers import get_schedule_data
from ..utils import extract_cron_days

if TYPE_CHECKING:
    from ..coordinator import FluidraDataUpdateCoordinator
    from ..fluidra_api import FluidraPoolAPI

_LOGGER = logging.getLogger(__name__)


def parse_schedule_time(time_value: time | int | float | str | None) -> time | None:
    """Parse schedule time - handles both numeric (minutes) and CRON format.

    Formats supported:
    - Numeric minutes from midnight: 540 -> 09:00
    - CRON format: "0 5 * * 1,2,3,4,5,6,7" -> 05:00
    """
    if time_value is None:
        return None

    if isinstance(time_value, time):
        return time_value

    if isinstance(time_value, (int, float)):
        minutes = int(time_value)
        hours = minutes // 60
        mins = minutes % 60
        return time(hours % 24, mins)

    if isinstance(time_value, str):
        time_value = time_value.strip()

        # Try CRON format "mm HH * * days".
        parts = time_value.split()
        if len(parts) >= 2:
            try:
                minute = int(parts[0])
                hour = int(parts[1])
                if 0 <= hour <= 23 and 0 <= minute <= 59:
                    return time(hour, minute)
            except (ValueError, TypeError):
                pass

        # Try numeric string (minutes from midnight).
        try:
            minutes = int(time_value)
            hours = minutes // 60
            mins = minutes % 60
            if 0 <= hours <= 23:
                return time(hours, mins)
        except (ValueError, TypeError):
            pass

    return None


class _FluidraTimeEntityBase(FluidraPoolControlEntity, TimeEntity):
    """Private base sharing schedule lookup/parsing logic for time entities.

    Holds everything that is identical between the pump/chlorinator and
    LumiPlus light schedule time entities. Device-specific behaviour
    (``device_info`` naming/model, optimistic values, overlap validation)
    stays in the public subclasses below.
    """

    __slots__ = ("_schedule_id", "_time_type")

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api: FluidraPoolAPI,
        pool_id: str,
        device_id: str,
        schedule_id: str,
        time_type: str,
    ) -> None:
        """Initialize the time entity."""
        super().__init__(coordinator, api, pool_id, device_id)
        self._schedule_id = schedule_id
        self._time_type = time_type  # "start" or "end"

    def _get_schedule_data(self) -> dict[str, Any] | None:
        """Get schedule data from coordinator."""
        try:
            return get_schedule_data(self.device_data, self._schedule_id)
        except (aiohttp.ClientError, TimeoutError, FluidraError, ValueError, TypeError, KeyError, AttributeError):
            _LOGGER.debug("Failed to get schedule data for %s", self._device_id)
            return None

    @property
    def available(self) -> bool:
        """Return True if the device/coordinator are healthy and the schedule exists."""
        return super().available and self._get_schedule_data() is not None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()

    def _parse_cron_time(self, cron_time: time | int | float | str | None) -> time | None:
        """Parse cron time format or numeric minutes to time object."""
        return parse_schedule_time(cron_time)

    def _format_time_to_cron(self, time_obj: time, days: list[int] | None = None) -> str:
        """Format time object to cron format."""
        if days is None:
            days = [1, 2, 3, 4, 5, 6, 7]  # All days in mobile-app format.
        days_str = ",".join(map(str, days))
        return f"{time_obj.minute} {time_obj.hour} * * {days_str}"


class FluidraScheduleTimeEntity(_FluidraTimeEntityBase):
    """Base class for Fluidra pump/chlorinator schedule time entities."""

    __slots__ = ("_optimistic_value",)

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api: FluidraPoolAPI,
        pool_id: str,
        device_id: str,
        schedule_id: str,
        time_type: str,
    ) -> None:
        """Initialize the time entity."""
        super().__init__(coordinator, api, pool_id, device_id, schedule_id, time_type)
        self._optimistic_value: time | None = None

    def _get_schedule_component(self) -> int:
        """Get the correct schedule component ID for this device."""
        device_data = self.device_data
        # Per-device override (e.g. 258 for DM24049704 chlorinator).
        schedule_comp: int = DeviceIdentifier.get_feature(device_data, "schedule_component")
        if schedule_comp:
            return schedule_comp
        # Default to component 20 (pumps).
        return 20

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info using device registry for consistent naming."""
        config = DeviceIdentifier.identify_device(self.device_data)
        default_model = (
            DEVICE_MODEL_MAP.get(config.device_type, DEVICE_MODEL_FALLBACK) if config else DEVICE_MODEL_FALLBACK
        )

        device_name = self.device_data.get("name") or f"Device {self._device_id}"
        firmware = self.device_data.get("firmware_version_component")
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=device_name,
            manufacturer=self.device_data.get("manufacturer", "Fluidra"),
            model=default_model,
            sw_version=str(firmware) if firmware is not None else None,
            via_device=(DOMAIN, self._pool_id),
        )

    def _get_schedule_days(self, schedule: dict[str, Any] | None) -> set[int]:
        """Return the active days for a schedule."""
        if not schedule:
            return set(range(1, 8))
        return extract_cron_days(schedule.get("startTime") or schedule.get("endTime"))

    def _validate_schedule_overlap(
        self, new_start_time: time, new_end_time: time, schedule_id_to_update: str
    ) -> tuple[bool, str]:
        """Validate that the new schedule doesn't overlap with existing enabled schedules."""
        try:
            device_data = self.device_data
            if "schedule_data" not in device_data:
                return True, ""

            current_schedules = device_data["schedule_data"]
            current_schedule = next(
                (schedule for schedule in current_schedules if str(schedule.get("id")) == str(schedule_id_to_update)),
                None,
            )
            new_days = self._get_schedule_days(current_schedule)

            for schedule in current_schedules:
                if str(schedule.get("id")) == str(schedule_id_to_update) or not schedule.get("enabled", False):
                    continue

                if new_days.isdisjoint(self._get_schedule_days(schedule)):
                    continue

                existing_start = self._parse_cron_time(schedule.get("startTime", ""))
                existing_end = self._parse_cron_time(schedule.get("endTime", ""))

                if not existing_start or not existing_end:
                    continue

                if self._times_overlap(new_start_time, new_end_time, existing_start, existing_end):
                    schedule_name = f"Programme {schedule.get('id')}"
                    return (
                        False,
                        f"Conflit détecté avec {schedule_name} ({existing_start.strftime('%H:%M')} - {existing_end.strftime('%H:%M')})",
                    )

            return True, ""
        except (aiohttp.ClientError, TimeoutError, FluidraError, ValueError, TypeError, KeyError, AttributeError):
            _LOGGER.debug("Failed to validate schedule overlap for %s", self._device_id)
            return True, ""

    def _times_overlap(self, start1: time, end1: time, start2: time, end2: time) -> bool:
        """Check if two time ranges overlap."""
        start1_min = start1.hour * 60 + start1.minute
        end1_min = end1.hour * 60 + end1.minute
        start2_min = start2.hour * 60 + start2.minute
        end2_min = end2.hour * 60 + end2.minute

        return any(
            interval1_start < interval2_end and interval2_start < interval1_end
            for interval1_start, interval1_end in self._minute_intervals(start1_min, end1_min)
            for interval2_start, interval2_end in self._minute_intervals(start2_min, end2_min)
        )

    def _minute_intervals(self, start_min: int, end_min: int) -> list[tuple[int, int]]:
        """Split a possibly overnight time range into same-day minute intervals."""
        if start_min == end_min:
            return [(0, 24 * 60)]
        if end_min < start_min:
            return [(start_min, 24 * 60), (0, end_min)]
        return [(start_min, end_min)]

    def _format_cron_time_chlorinator(self, cron_time: str) -> str:
        """Format CRON time for DM24049704 chlorinator (00 05 * * 1,2,3,4,5,6,7)."""
        if not cron_time:
            return "00 00 * * 1,2,3,4,5,6,7"
        parts = cron_time.split()
        if len(parts) >= 5:
            minute = parts[0].zfill(2)
            hour = parts[1].zfill(2)
            days = parts[4] if parts[4] != "*" else "1,2,3,4,5,6,7"
            return f"{minute} {hour} * * {days}"
        return cron_time


class FluidraLightScheduleTimeEntity(_FluidraTimeEntityBase):
    """Base class for LumiPlus Connect light schedule time entities."""

    __slots__ = ()

    SCHEDULE_COMPONENT = 40  # LumiPlus light schedules live on component 40.

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        device_name = self.device_data.get("name") or f"Pool Light {self._device_id}"
        firmware = self.device_data.get("firmware_version_component")
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=device_name,
            manufacturer=self.device_data.get("manufacturer", "Fluidra"),
            model=self.device_data.get("model", "LumiPlus Connect"),
            sw_version=str(firmware) if firmware is not None else None,
            via_device=(DOMAIN, self._pool_id),
        )
