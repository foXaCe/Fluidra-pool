"""Utility functions for the Fluidra Pool integration."""

from __future__ import annotations

import logging

_LOGGER = logging.getLogger(__name__)

# Default CRON expression for all days (Mon-Sun in mobile format)
DEFAULT_CRON_ALL_DAYS = "00 00 * * 1,2,3,4,5,6,7"
MOBILE_CRON_DAYS = (1, 2, 3, 4, 5, 6, 7)

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


def normalize_mobile_days(days: list[int] | set[int] | tuple[int, ...] | None) -> list[int]:
    """Normalize cron days to Fluidra mobile-app format (1=Monday, 7=Sunday)."""
    if not days:
        return list(MOBILE_CRON_DAYS)

    normalized: set[int] = set()
    for day in days:
        day_num = int(day)
        if day_num == 0:
            day_num = 7
        if 1 <= day_num <= 7:
            normalized.add(day_num)
    return sorted(normalized) or list(MOBILE_CRON_DAYS)


def extract_cron_days(cron_time: str | None) -> set[int]:
    """Extract Fluidra mobile-app days from a CRON expression."""
    if not cron_time:
        return set(MOBILE_CRON_DAYS)

    parts = cron_time.split()
    if len(parts) < 5 or parts[4] == "*":
        return set(MOBILE_CRON_DAYS)

    days: set[int] = set()
    for raw_day in parts[4].split(","):
        raw_day = raw_day.strip()
        if not raw_day:
            continue
        try:
            if "-" in raw_day:
                start_text, end_text = raw_day.split("-", 1)
                start_day = int(start_text)
                end_day = int(end_text)
                days.update(normalize_mobile_days(tuple(range(start_day, end_day + 1))))
            else:
                days.update(normalize_mobile_days((int(raw_day),)))
        except ValueError as err:
            _LOGGER.debug("Failed to parse CRON day '%s' from '%s': %s", raw_day, cron_time, err)

    return days or set(MOBILE_CRON_DAYS)


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
