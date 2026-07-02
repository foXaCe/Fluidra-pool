"""Pure helper functions shared across Fluidra Pool platforms.

These functions take plain data in and return plain data out — no ``hass``,
no coordinator, no I/O — so they can be reused from any entity without
introducing coupling between platforms.
"""

from __future__ import annotations

from datetime import time
from typing import Any


def get_schedule_data(device_data: dict[str, Any], schedule_id: Any) -> dict[str, Any] | None:
    """Return the schedule dict matching ``schedule_id`` in ``device_data``.

    Looks up ``device_data["schedule_data"]`` for an entry whose ``id`` matches
    ``schedule_id`` (compared as strings, since the API mixes int/str ids).
    Returns ``None`` if ``device_data`` is empty, has no schedules, or no
    schedule matches.
    """
    if not device_data:
        return None

    schedules = device_data.get("schedule_data")
    if not schedules:
        return None

    for schedule in schedules:
        if str(schedule.get("id")) == str(schedule_id):
            result: dict[str, Any] = schedule
            return result

    return None


def resolve_component_rw(cfg: int | dict[str, Any]) -> tuple[Any, Any]:
    """Resolve a component config that may be a plain int or a read/write dict.

    Device-registry component features are expressed either as a single int
    (the same component is used for reading and writing) or as a
    ``{"read": x, "write": y}`` dict (separate components). Returns a
    ``(read, write)`` tuple; when ``cfg`` is a dict, a side missing from it
    falls back to the other side.
    """
    if isinstance(cfg, dict):
        write_component = cfg.get("write", cfg.get("read"))
        read_component = cfg.get("read", write_component)
        return read_component, write_component
    return cfg, cfg


def parse_cron_time(cron_time: str) -> time | None:
    """Parse a CRON expression (``"mm HH * * days"``) into a :class:`~datetime.time`.

    Returns ``None`` for anything that does not carry a valid minute/hour pair
    (short string, non-numeric fields, out-of-range values, non-string input).
    """
    try:
        parts = cron_time.split()
        if len(parts) >= 2:
            minute = int(parts[0])
            hour = int(parts[1])
            return time(hour, minute)
    except (ValueError, TypeError, IndexError, AttributeError):
        pass
    return None
