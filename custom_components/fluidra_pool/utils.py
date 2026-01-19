"""Utility functions for the Fluidra Pool integration."""

from __future__ import annotations

import logging

_LOGGER = logging.getLogger(__name__)

# Default CRON expression for all days
DEFAULT_CRON_ALL_DAYS = "00 00 * * 1,2,3,4,5,6,7"


def convert_cron_days(cron_time: str) -> str:
    """Convert cron time from HA format (0,1,2,3,4,5,6) to mobile format (1,2,3,4,5,6,7).

    In Home Assistant/standard cron format, Sunday is 0 and Saturday is 6.
    In Fluidra mobile app format, Monday is 1 and Sunday is 7.

    Args:
        cron_time: CRON expression string (e.g., "30 08 * * 0,1,2,3,4,5,6")

    Returns:
        Converted CRON expression with days in mobile format
    """
    if not cron_time:
        return DEFAULT_CRON_ALL_DAYS

    parts = cron_time.split()
    if len(parts) >= 5:
        try:
            # Convert day numbers: 0->7, 1->1, 2->2, etc.
            old_days = parts[4].split(",")
            new_days = []
            for day in old_days:
                day_num = int(day.strip())
                if day_num == 0:  # Sunday: 0 -> 7
                    new_days.append("7")
                else:  # Monday-Saturday: 1-6 -> 1-6
                    new_days.append(str(day_num))

            # Sort days to match mobile app format
            new_days_sorted = sorted(int(d) for d in new_days)
            parts[4] = ",".join(map(str, new_days_sorted))
            return " ".join(parts)
        except (ValueError, IndexError) as err:
            _LOGGER.debug("Failed to convert CRON days '%s': %s", cron_time, err)

    return cron_time


def parse_cron_time(cron_time: str) -> tuple[int, int] | None:
    """Extract hour and minute from a CRON expression.

    Args:
        cron_time: CRON expression string (e.g., "30 08 * * 1,2,3,4,5,6,7")

    Returns:
        Tuple of (hour, minute) or None if parsing fails
    """
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
    """Build a CRON expression from hour, minute and days.

    Args:
        hour: Hour (0-23)
        minute: Minute (0-59)
        days: Comma-separated day numbers in mobile format (1=Monday, 7=Sunday)

    Returns:
        CRON expression string
    """
    return f"{minute:02d} {hour:02d} * * {days}"
