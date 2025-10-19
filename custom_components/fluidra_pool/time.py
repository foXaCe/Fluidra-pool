"""Time platform for Fluidra Pool integration."""
import logging
from datetime import time
from typing import Optional, Dict, Any

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import EntityCategory

from .const import DOMAIN
from .coordinator import FluidraDataUpdateCoordinator
from .device_registry import DeviceIdentifier

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Fluidra Pool time entities."""
    coordinator: FluidraDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    entities = []

    pools = await coordinator.api.get_pools()
    for pool in pools:
        for device in pool["devices"]:
            device_id = device.get("device_id")

            if not device_id:
                continue

            # Skip devices that don't support schedules (heat pumps)
            if DeviceIdentifier.has_feature(device, "skip_schedules"):
                continue

            # Only create time entities for devices that should have them
            if DeviceIdentifier.should_create_entity(device, "time"):
                # Create time entities for the actual 8 schedulers found
                for schedule_id in ["1", "2", "3", "4", "5", "6", "7", "8"]:
                    # Create start time entity
                    entities.append(FluidraScheduleStartTimeEntity(
                        coordinator,
                        coordinator.api,
                        pool["id"],
                        device_id,
                        schedule_id
                    ))

                    # Create end time entity
                    entities.append(FluidraScheduleEndTimeEntity(
                        coordinator,
                        coordinator.api,
                        pool["id"],
                        device_id,
                        schedule_id
                    ))

    async_add_entities(entities)


class FluidraScheduleTimeEntity(CoordinatorEntity, TimeEntity):
    """Base class for Fluidra schedule time entities."""

    def __init__(self, coordinator, api, pool_id: str, device_id: str, schedule_id: str, time_type: str):
        """Initialize the time entity."""
        super().__init__(coordinator)
        self._api = api
        self._pool_id = pool_id
        self._device_id = device_id
        self._schedule_id = schedule_id
        self._time_type = time_type  # "start" or "end"

    @property
    def device_data(self) -> dict:
        """Get device data from coordinator."""
        if self.coordinator.data is None:
            return {}
        pool = self.coordinator.data.get(self._pool_id)
        if pool:
            for device in pool.get("devices", []):
                if device.get("device_id") == self._device_id:
                    return device
        return {}

    @property
    def device_info(self) -> dict:
        """Return device info."""
        device_name = self.device_data.get("name") or f"E30iQ Pump {self._device_id}"
        return {
            "identifiers": {(DOMAIN, self._device_id)},
            "name": device_name,
            "manufacturer": self.device_data.get("manufacturer", "Fluidra"),
            "model": self.device_data.get("model", "E30iQ"),
            "via_device": (DOMAIN, self._pool_id),
        }

    def _get_schedule_data(self) -> Optional[dict]:
        """Get schedule data from coordinator."""
        try:
            # Get schedules from device data like the sensor does
            device_data = self.device_data

            # Si aucune donnée n'est disponible, retourner None
            if not device_data:
                return None

            if "schedule_data" in device_data:
                schedules = device_data["schedule_data"]

                for schedule in schedules:
                    schedule_id = schedule.get("id")
                    # Compare both as string and int to handle type mismatch
                    if str(schedule_id) == str(self._schedule_id):
                        return schedule

        except Exception as e:
            _LOGGER.error(f"[TIME {self._schedule_id}] Error getting schedule data: {e}")
        return None

    @property
    def available(self) -> bool:
        """Return True if the schedule exists."""
        result = self._get_schedule_data() is not None
        return result

    def _parse_cron_time(self, cron_time: str) -> Optional[time]:
        """Parse cron time format 'mm HH * * 0,1,2,3,4,5,6' to time object."""
        try:
            parts = cron_time.split()
            if len(parts) >= 2:
                minute = int(parts[0])
                hour = int(parts[1])
                return time(hour, minute)
        except (ValueError, IndexError):
            pass
        return None

    def _format_time_to_cron(self, time_obj: time, days: list = None) -> str:
        """Format time object to cron format."""
        if days is None:
            days = [1, 2, 3, 4, 5, 6, 7]  # All days in mobile format
        days_str = ','.join(map(str, days))
        return f"{time_obj.minute} {time_obj.hour} * * {days_str}"

    def _convert_cron_days(self, cron_time: str) -> str:
        """Convert cron time from HA format (0,1,2,3,4,5,6) to mobile format (1,2,3,4,5,6,7)."""
        if not cron_time:
            return "00 00 * * 1,2,3,4,5,6,7"

        parts = cron_time.split()
        if len(parts) >= 5:
            try:
                # Convert day numbers: 0->7, 1->1, 2->2, etc.
                old_days = parts[4].split(',')
                new_days = []
                for day in old_days:
                    day_num = int(day.strip())
                    if day_num == 0:  # Sunday: 0 -> 7
                        new_days.append("7")
                    else:  # Monday-Saturday: 1-6 -> 1-6
                        new_days.append(str(day_num))

                # Sort days to match mobile app format
                new_days_sorted = sorted([int(d) for d in new_days])
                parts[4] = ','.join(map(str, new_days_sorted))
                return ' '.join(parts)
            except (ValueError, IndexError):
                pass

        return cron_time

    def _validate_schedule_overlap(self, new_start_time: time, new_end_time: time, schedule_id_to_update: str) -> tuple[bool, str]:
        """Validate that the new schedule doesn't overlap with existing enabled schedules."""
        try:
            device_data = self.device_data
            if "schedule_data" not in device_data:
                return True, ""

            current_schedules = device_data["schedule_data"]

            for schedule in current_schedules:
                # Skip the schedule we're updating and disabled schedules
                if (str(schedule.get("id")) == str(schedule_id_to_update) or
                    not schedule.get("enabled", False)):
                    continue

                # Parse existing schedule times
                existing_start = self._parse_cron_time(schedule.get("startTime", ""))
                existing_end = self._parse_cron_time(schedule.get("endTime", ""))

                if not existing_start or not existing_end:
                    continue

                # Check for overlap (simple time overlap check)
                if self._times_overlap(new_start_time, new_end_time, existing_start, existing_end):
                    schedule_name = f"Programme {schedule.get('id')}"
                    return False, f"Conflit détecté avec {schedule_name} ({existing_start.strftime('%H:%M')} - {existing_end.strftime('%H:%M')})"

            return True, ""
        except Exception as e:
            _LOGGER.warning(f"Error validating schedule overlap: {e}")
            return True, ""  # Allow if validation fails

    def _times_overlap(self, start1: time, end1: time, start2: time, end2: time) -> bool:
        """Check if two time ranges overlap."""
        # Convert times to minutes for easier comparison
        start1_min = start1.hour * 60 + start1.minute
        end1_min = end1.hour * 60 + end1.minute
        start2_min = start2.hour * 60 + start2.minute
        end2_min = end2.hour * 60 + end2.minute

        # Handle overnight schedules (end time < start time)
        if end1_min < start1_min:
            end1_min += 24 * 60
        if end2_min < start2_min:
            end2_min += 24 * 60

        # Check for overlap
        return not (end1_min <= start2_min or start1_min >= end2_min)


class FluidraScheduleStartTimeEntity(FluidraScheduleTimeEntity):
    """Time entity for schedule start time."""

    def __init__(self, coordinator, api, pool_id: str, device_id: str, schedule_id: str):
        """Initialize the start time entity."""
        super().__init__(coordinator, api, pool_id, device_id, schedule_id, "start")

        device_name = self.device_data.get("name") or f"E30iQ Pump {self._device_id}"
        self._attr_translation_key = "schedule_start"
        self._attr_translation_placeholders = {"schedule_id": schedule_id}
        self._attr_unique_id = f"fluidra_{self._device_id}_{schedule_id}_start_time"
        self._attr_entity_category = EntityCategory.CONFIG

    @property
    def icon(self) -> str:
        """Return the icon for the entity."""
        return "mdi:clock-start"

    @property
    def native_value(self) -> Optional[time]:
        """Return the current start time."""
        schedule = self._get_schedule_data()
        if schedule:
            start_time_str = schedule.get("startTime", "")
            return self._parse_cron_time(start_time_str)
        return None

    async def async_set_value(self, value: time) -> None:
        """Set the start time using exact mobile app format."""
        try:
            # Get all current schedule data
            device_data = self.device_data
            if "schedule_data" not in device_data:
                _LOGGER.error(f"No schedule data found for device {self._device_id}")
                return

            current_schedules = device_data["schedule_data"]
            if not current_schedules:
                return

            # Get current end time for this schedule to validate overlap
            current_schedule = self._get_schedule_data()
            if current_schedule:
                current_end_time = self._parse_cron_time(current_schedule.get("endTime", ""))
                if current_end_time:
                    # Validate no overlap with other schedules
                    is_valid, error_msg = self._validate_schedule_overlap(value, current_end_time, self._schedule_id)
                    if not is_valid:
                        _LOGGER.error(f"❌ {error_msg}")
                        raise ValueError(error_msg)

            # Create complete schedule list with EXACT format from mobile app
            updated_schedules = []
            for sched in current_schedules:
                # Convert cron format 0,1,2,3,4,5,6 to 1,2,3,4,5,6,7 for mobile app
                start_time = self._convert_cron_days(sched.get("startTime", ""))
                end_time = self._convert_cron_days(sched.get("endTime", ""))

                # If this is the schedule we're updating, use the new time
                if str(sched.get("id")) == str(self._schedule_id):
                    # Parse current days from the existing cron time
                    current_cron = sched.get("startTime", "")
                    days = [1, 2, 3, 4, 5, 6, 7]  # Default to all days in mobile format
                    if current_cron:
                        parts = current_cron.split()
                        if len(parts) >= 5:
                            try:
                                # Convert existing days to mobile format
                                old_days = [int(d) for d in parts[4].split(',')]
                                days = []
                                for day in old_days:
                                    if day == 0:  # Sunday: 0 -> 7
                                        days.append(7)
                                    else:  # Monday-Saturday: 1-6 -> 1-6
                                        days.append(day)
                                days = sorted(days)
                            except (ValueError, TypeError, AttributeError):
                                pass

                    start_time = self._format_time_to_cron(value, days)

                scheduler = {
                    "id": sched.get("id"),
                    "groupId": sched.get("id"),  # Mobile app always uses id as groupId
                    "enabled": sched.get("enabled", False),
                    "startTime": start_time,
                    "endTime": end_time,
                    "startActions": {
                        "operationName": str(sched.get("startActions", {}).get("operationName", "0"))
                    }
                }
                updated_schedules.append(scheduler)

            # Ensure we have exactly 8 schedulers (add missing ones)
            while len(updated_schedules) < 8:
                missing_id = len(updated_schedules) + 1
                updated_schedules.append({
                    "id": missing_id,
                    "groupId": missing_id,
                    "enabled": False,
                    "startTime": "00 00 * * 1,2,3,4,5,6,7",
                    "endTime": "00 01 * * 1,2,3,4,5,6,7",
                    "startActions": {"operationName": "0"}
                })

            # Send update to API
            success = await self._api.set_schedule(self._device_id, updated_schedules)
            if success:
                await self.coordinator.async_request_refresh()
            else:
                _LOGGER.error(f"❌ Failed to update start time for schedule {self._schedule_id}")

        except Exception as e:
            _LOGGER.error(f"❌ Error setting start time for schedule {self._schedule_id}: {e}")


class FluidraScheduleEndTimeEntity(FluidraScheduleTimeEntity):
    """Time entity for schedule end time."""

    def __init__(self, coordinator, api, pool_id: str, device_id: str, schedule_id: str):
        """Initialize the end time entity."""
        super().__init__(coordinator, api, pool_id, device_id, schedule_id, "end")

        device_name = self.device_data.get("name") or f"E30iQ Pump {self._device_id}"
        self._attr_translation_key = "schedule_end"
        self._attr_translation_placeholders = {"schedule_id": schedule_id}
        self._attr_unique_id = f"fluidra_{self._device_id}_{schedule_id}_end_time"
        self._attr_entity_category = EntityCategory.CONFIG

    @property
    def icon(self) -> str:
        """Return the icon for the entity."""
        return "mdi:clock-end"

    @property
    def native_value(self) -> Optional[time]:
        """Return the current end time."""
        schedule = self._get_schedule_data()
        if schedule:
            end_time_str = schedule.get("endTime", "")
            return self._parse_cron_time(end_time_str)
        return None

    async def async_set_value(self, value: time) -> None:
        """Set the end time using exact mobile app format."""
        try:
            # Get all current schedule data
            device_data = self.device_data
            if "schedule_data" not in device_data:
                _LOGGER.error(f"No schedule data found for device {self._device_id}")
                return

            current_schedules = device_data["schedule_data"]
            if not current_schedules:
                return

            # Get current start time for this schedule to validate overlap
            current_schedule = self._get_schedule_data()
            if current_schedule:
                current_start_time = self._parse_cron_time(current_schedule.get("startTime", ""))
                if current_start_time:
                    # Validate no overlap with other schedules
                    is_valid, error_msg = self._validate_schedule_overlap(current_start_time, value, self._schedule_id)
                    if not is_valid:
                        _LOGGER.error(f"❌ {error_msg}")
                        raise ValueError(error_msg)

            # Create complete schedule list with EXACT format from mobile app
            updated_schedules = []
            for sched in current_schedules:
                # Convert cron format 0,1,2,3,4,5,6 to 1,2,3,4,5,6,7 for mobile app
                start_time = self._convert_cron_days(sched.get("startTime", ""))
                end_time = self._convert_cron_days(sched.get("endTime", ""))

                # If this is the schedule we're updating, use the new time
                if str(sched.get("id")) == str(self._schedule_id):
                    # Parse current days from the existing cron time
                    current_cron = sched.get("endTime", "")
                    days = [1, 2, 3, 4, 5, 6, 7]  # Default to all days in mobile format
                    if current_cron:
                        parts = current_cron.split()
                        if len(parts) >= 5:
                            try:
                                # Convert existing days to mobile format
                                old_days = [int(d) for d in parts[4].split(',')]
                                days = []
                                for day in old_days:
                                    if day == 0:  # Sunday: 0 -> 7
                                        days.append(7)
                                    else:  # Monday-Saturday: 1-6 -> 1-6
                                        days.append(day)
                                days = sorted(days)
                            except (ValueError, TypeError, AttributeError):
                                pass

                    end_time = self._format_time_to_cron(value, days)

                scheduler = {
                    "id": sched.get("id"),
                    "groupId": sched.get("id"),  # Mobile app always uses id as groupId
                    "enabled": sched.get("enabled", False),
                    "startTime": start_time,
                    "endTime": end_time,
                    "startActions": {
                        "operationName": str(sched.get("startActions", {}).get("operationName", "0"))
                    }
                }
                updated_schedules.append(scheduler)

            # Ensure we have exactly 8 schedulers (add missing ones)
            while len(updated_schedules) < 8:
                missing_id = len(updated_schedules) + 1
                updated_schedules.append({
                    "id": missing_id,
                    "groupId": missing_id,
                    "enabled": False,
                    "startTime": "00 00 * * 1,2,3,4,5,6,7",
                    "endTime": "00 01 * * 1,2,3,4,5,6,7",
                    "startActions": {"operationName": "0"}
                })

            # Send update to API
            success = await self._api.set_schedule(self._device_id, updated_schedules)
            if success:
                await self.coordinator.async_request_refresh()
            else:
                _LOGGER.error(f"❌ Failed to update end time for schedule {self._schedule_id}")

        except Exception as e:
            _LOGGER.error(f"❌ Error setting end time for schedule {self._schedule_id}: {e}")