"""Pure parsing helpers used by :class:`FluidraDataUpdateCoordinator`.

These helpers are kept outside the coordinator class so they're easier to test
and don't pull HA infrastructure into unit tests.
"""

from __future__ import annotations

from datetime import time
import logging

from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

_DAY_NAME_TO_CRON: dict[str, int] = {
    "monday": 1,
    "tuesday": 2,
    "wednesday": 3,
    "thursday": 4,
    "friday": 5,
    "saturday": 6,
    "sunday": 7,
}

# operationName → effective percentage at the pump (from mitmproxy capture).
_OPERATION_TO_PERCENT: dict[str, int] = {
    "0": 45,  # Low.
    "1": 65,  # Medium.
    "2": 100,  # High.
}


def parse_dm24049704_schedule_format(reported_value: dict) -> list[dict]:
    """Parse DM24049704 chlorinator schedule format (programs/slots) to standard format.

    The API returns component 258 in this format::

        {
            "dayPrograms": {"monday": 1, "tuesday": 1, ...},
            "programs": [
                {"id": 1, "slots": [{"id": 0, "start": 1280, "end": 1536, "mode": 3}]}
            ]
        }

    Where time is encoded as ``hours * 256 + minutes`` and mode 1=S1, 2=S2, 3=S3.

    Returns standard schedule format::

        [{"id": 0, "startTime": "0 5 * * 1,2,3,4,5",
          "endTime": "0 6 * * 1,2,3,4,5",
          "startActions": {"operationName": "3"}, "enabled": True}]
    """
    try:
        if not isinstance(reported_value, dict):
            return []

        day_programs = reported_value.get("dayPrograms", {})
        programs = reported_value.get("programs", [])

        if not programs:
            return []

        # Group days by program ID.
        program_days: dict[int, list[int]] = {}
        for day_name, program_id in day_programs.items():
            if program_id not in program_days:
                program_days[program_id] = []
            cron_day = _DAY_NAME_TO_CRON.get(day_name.lower())
            if cron_day:
                program_days[program_id].append(cron_day)

        for days in program_days.values():
            days.sort()

        result: list[dict] = []
        schedule_id = 1  # DM24049704 uses IDs starting at 1.

        for program in programs:
            program_id = program.get("id")
            slots = program.get("slots", [])
            days = program_days.get(program_id, [])

            if not days:
                continue

            days_str = ",".join(str(d) for d in days)

            for slot in slots:
                start_raw = slot.get("start", 0)
                end_raw = slot.get("end", 0)
                mode = slot.get("mode", 0)

                # Skip empty slots (mode=0 with no time set).
                if mode == 0 and start_raw == 0 and end_raw == 0:
                    continue

                # Decode time: hours * 256 + minutes.
                start_hour = start_raw // 256
                start_minute = start_raw % 256
                end_hour = end_raw // 256
                end_minute = end_raw % 256

                start_cron = f"{start_minute} {start_hour} * * {days_str}"
                end_cron = f"{end_minute} {end_hour} * * {days_str}"

                result.append(
                    {
                        "id": schedule_id,
                        "groupId": schedule_id,  # groupId must match id for API.
                        "startTime": start_cron,
                        "endTime": end_cron,
                        "startActions": {"operationName": str(mode)},
                        "enabled": True,
                    }
                )
                schedule_id += 1

        _LOGGER.debug("Parsed DM24049704 schedule: %s -> %s", reported_value, result)
        return result

    except (ValueError, TypeError, KeyError) as err:
        _LOGGER.warning("Failed to parse DM24049704 schedule format: %s", err)
        return []


def _parse_cron_time(cron_time: str) -> time | None:
    """Parse cron ``mm HH …`` to a :class:`time` object."""
    try:
        parts = cron_time.split()
        if len(parts) >= 2:
            minute = int(parts[0])
            hour = int(parts[1])
            return time(hour, minute)
    except (ValueError, IndexError):
        pass
    return None


def _parse_cron_days(cron_time: str) -> list[int]:
    """Parse CRON day field into Python-style weekday indices (0=Monday)."""
    try:
        parts = cron_time.split()
        if len(parts) >= 5:
            days_str = parts[4]
            if days_str == "*":
                return list(range(7))
            days: list[int] = []
            for day in days_str.split(","):
                day_num = int(day.strip())
                # CRON 0=Sunday → Python 6, otherwise (1-6) → (0-5).
                if day_num == 0:
                    days.append(6)
                else:
                    days.append(day_num - 1)
            return days
    except (ValueError, IndexError):
        pass
    return []


def calculate_auto_speed_from_schedules(device: dict) -> int:
    """Calculate current speed based on active schedules in auto mode."""
    try:
        schedule_data = device.get("schedule_data", [])
        if not schedule_data:
            return 0

        now = dt_util.now()
        current_time = now.time()
        current_weekday = now.weekday()  # 0 = Monday, 6 = Sunday.

        for schedule in schedule_data:
            if not schedule.get("enabled", False):
                continue

            start_time_obj = _parse_cron_time(schedule.get("startTime", ""))
            end_time_obj = _parse_cron_time(schedule.get("endTime", ""))
            schedule_days = _parse_cron_days(schedule.get("startTime", ""))

            if not (start_time_obj and end_time_obj and current_weekday in schedule_days):
                continue

            # Wrap-aware window: an overnight schedule (start > end, e.g. 22:00→06:00)
            # is active when the current time is after the start OR before the end.
            # Mirrors the midnight-wrapping logic in time/base._minute_intervals.
            if start_time_obj <= end_time_obj:
                in_window = start_time_obj <= current_time <= end_time_obj
            else:
                in_window = current_time >= start_time_obj or current_time <= end_time_obj

            if in_window:
                operation = schedule.get("startActions", {}).get("operationName", "0")
                return _OPERATION_TO_PERCENT.get(operation, 0)

        return 0

    except (ValueError, TypeError):
        return 0
