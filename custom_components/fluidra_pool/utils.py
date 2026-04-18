"""Utility functions for the Fluidra Pool integration."""

from __future__ import annotations

import logging

_LOGGER = logging.getLogger(__name__)

# Default CRON expression for all days (Mon-Sun in mobile format)
DEFAULT_CRON_ALL_DAYS = "00 00 * * 1,2,3,4,5,6,7"

# CRON day number → weekday name (Fluidra mobile-app format: 1=Monday, 7=Sunday)
CRON_DAY_TO_NAME = {
    1: "monday",
    2: "tuesday",
    3: "wednesday",
    4: "thursday",
    5: "friday",
    6: "saturday",
    7: "sunday",
}

DAY_NAME_TO_CRON = {name: num for num, name in CRON_DAY_TO_NAME.items()}


def convert_cron_days(cron_time: str) -> str:
    """Convert cron day numbers from HA format (0=Sun..6=Sat) to mobile format (1=Mon..7=Sun)."""
    if not cron_time:
        return DEFAULT_CRON_ALL_DAYS

    parts = cron_time.split()
    if len(parts) >= 5:
        try:
            old_days = parts[4].split(",")
            new_days: list[int] = []
            for day in old_days:
                day_num = int(day.strip())
                new_days.append(7 if day_num == 0 else day_num)
            parts[4] = ",".join(str(d) for d in sorted(new_days))
            return " ".join(parts)
        except (ValueError, IndexError) as err:
            _LOGGER.debug("Failed to convert CRON days '%s': %s", cron_time, err)

    return cron_time


def parse_cron_time(cron_time: str) -> tuple[int, int] | None:
    """Extract ``(hour, minute)`` from a CRON expression."""
    if not cron_time:
        return None

    parts = cron_time.split()
    if len(parts) >= 2:
        try:
            minute = int(parts[0])
            hour = int(parts[1])
            return (hour, minute)
        except (ValueError, IndexError) as err:
            _LOGGER.debug("Failed to parse CRON time '%s': %s", cron_time, err)

    return None


def build_cron_expression(hour: int, minute: int, days: str = "1,2,3,4,5,6,7") -> str:
    """Build a CRON expression from ``hour``, ``minute`` and ``days``."""
    return f"{minute:02d} {hour:02d} * * {days}"


def mask_email(email: str | None) -> str:
    """Return a privacy-friendly representation of an email for logging."""
    if not email:
        return "***"
    if len(email) < 3:
        return "***"
    return f"{email[:3]}***"


def mask_device_id(device_id: str | None) -> str:
    """Return a privacy-friendly representation of a device id for logging."""
    if not device_id:
        return "***"
    if len(device_id) < 6:
        return "***"
    return f"{device_id[:3]}***{device_id[-3:]}"
