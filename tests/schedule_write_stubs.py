"""Fake API surface for the schedule write path.

A schedule write serialises compose-and-PUT on a per-register lock and composes
from the list last PUT rather than the poll cache (Issue #210). Test doubles need
both, and need them typed: a bare mock hands back a truthy ``MagicMock`` that the
write path would treat as a slot list, and a ``MagicMock`` cannot be used as an
``async with`` target.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock


def install_schedule_write_stubs(api: Any) -> None:
    """Give ``api`` a working ``schedule_write_lock`` / ``pending_schedule_slots``."""
    locks: dict[tuple[str, int], asyncio.Lock] = {}

    def _lock(device_id: str, component_id: int) -> asyncio.Lock:
        return locks.setdefault((str(device_id), int(component_id)), asyncio.Lock())

    api.schedule_write_lock = MagicMock(side_effect=_lock)
    api.pending_schedule_slots = MagicMock(return_value=None)


def schedule_api(**attrs: Any) -> SimpleNamespace:
    """Return a fake API carrying ``attrs`` plus the schedule-write surface."""
    api = SimpleNamespace(**attrs)
    install_schedule_write_stubs(api)
    return api
