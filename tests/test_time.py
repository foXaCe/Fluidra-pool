"""Tests for the time platform (schedule start/end editors + parse_schedule_time)."""

from __future__ import annotations

from datetime import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.fluidra_pool.time import (
    FluidraScheduleEndTimeEntity,
    FluidraScheduleStartTimeEntity,
    async_setup_entry,
    parse_schedule_time,
)

POOL_ID = "pool-1"
DEVICE_ID = "TEST-PUMP-001"


# --- parse_schedule_time ---


@pytest.mark.parametrize(
    ("incoming", "expected"),
    [
        (None, None),
        # Already a time → returned as-is
        (time(7, 30), time(7, 30)),
        # Numeric minutes from midnight
        (0, time(0, 0)),
        (60, time(1, 0)),
        (540, time(9, 0)),
        # Floats also accepted
        (90.0, time(1, 30)),
        # CRON-style string "mm HH …"
        ("30 08 * * 1,2,3", time(8, 30)),
        ("00 23 * * *", time(23, 0)),
        # Numeric string (legacy storage)
        ("540", time(9, 0)),
        # Out-of-range CRON hours fall through and return None
        ("00 30 * * *", None),
        # Garbage returns None
        ("not-a-time", None),
        ([1, 2, 3], None),
    ],
)
def test_parse_schedule_time_accepts_multiple_formats(incoming, expected) -> None:
    """parse_schedule_time tolerates CRON, minutes-from-midnight and time objects."""
    assert parse_schedule_time(incoming) == expected


def test_parse_schedule_time_wraps_minutes_above_one_day() -> None:
    """Minutes ≥ 1440 wrap around (24h modulo) rather than blowing up."""
    # 25h after midnight is 1h.
    assert parse_schedule_time(1500) == time(1, 0)


# --- FluidraScheduleStartTimeEntity / EndTimeEntity ---


def _coord(schedules: list[dict]) -> Any:
    coordinator = MagicMock()
    coordinator.data = {
        POOL_ID: {
            "id": POOL_ID,
            "name": "Pool",
            "devices": [
                {
                    "device_id": DEVICE_ID,
                    "name": "Pump",
                    "type": "pump",
                    "online": True,
                    "schedule_data": schedules,
                    # Force identify_device to skip the global registry → no schedule_component
                    # feature pinned, so _get_schedule_component falls back to 20 (pump default).
                    "_identify_cache": {
                        "key": (DEVICE_ID, "", "", "", ""),
                        "config": SimpleNamespace(
                            device_type="pump",
                            features={},
                            components_range=25,
                            required_components=[0, 1, 2, 3],
                            entities=[],
                        ),
                    },
                    "family": "",
                    "model": "",
                }
            ],
        }
    }
    coordinator.async_request_refresh = AsyncMock()
    coordinator.last_update_success = True
    return coordinator


def _attach_ha(entity) -> None:
    entity.hass = MagicMock()
    entity.async_write_ha_state = MagicMock()


SCHEDULE = {
    "id": 1,
    "enabled": True,
    "startTime": "30 08 * * 1,2,3,4,5",
    "endTime": "00 10 * * 1,2,3,4,5",
    "startActions": {"operationName": "1"},
}


def test_schedule_start_native_value_parses_cron_string() -> None:
    """Start time is decoded from the CRON minute/hour fields."""
    entity = FluidraScheduleStartTimeEntity(
        _coord([SCHEDULE]),
        SimpleNamespace(set_schedule=AsyncMock(return_value=True)),
        POOL_ID,
        DEVICE_ID,
        schedule_id="1",
    )
    _attach_ha(entity)
    assert entity.native_value == time(8, 30)


def test_schedule_end_native_value_parses_cron_string() -> None:
    """End time is decoded from the CRON minute/hour fields."""
    entity = FluidraScheduleEndTimeEntity(
        _coord([SCHEDULE]),
        SimpleNamespace(set_schedule=AsyncMock(return_value=True)),
        POOL_ID,
        DEVICE_ID,
        schedule_id="1",
    )
    _attach_ha(entity)
    assert entity.native_value == time(10, 0)


def test_schedule_start_native_value_uses_optimistic_value_when_set() -> None:
    """Just after a user edit the optimistic value shadows the stale CRON."""
    entity = FluidraScheduleStartTimeEntity(
        _coord([SCHEDULE]),
        SimpleNamespace(set_schedule=AsyncMock(return_value=True)),
        POOL_ID,
        DEVICE_ID,
        schedule_id="1",
    )
    _attach_ha(entity)
    entity._optimistic_value = time(7, 0)
    assert entity.native_value == time(7, 0)


def test_schedule_start_native_value_none_when_no_match() -> None:
    """Schedule id without a matching entry returns None."""
    entity = FluidraScheduleStartTimeEntity(
        _coord([SCHEDULE]),
        SimpleNamespace(set_schedule=AsyncMock(return_value=True)),
        POOL_ID,
        DEVICE_ID,
        schedule_id="42",
    )
    _attach_ha(entity)
    assert entity.native_value is None


async def test_schedule_start_async_set_value_no_op_without_schedule_data() -> None:
    """When the coordinator hasn't populated schedule_data yet, set_value is a no-op."""
    coord = _coord([])
    coord.data[POOL_ID]["devices"][0].pop("schedule_data")
    api = SimpleNamespace(set_schedule=AsyncMock(return_value=True))
    entity = FluidraScheduleStartTimeEntity(coord, api, POOL_ID, DEVICE_ID, schedule_id="1")
    _attach_ha(entity)

    await entity.async_set_value(time(7, 0))

    api.set_schedule.assert_not_called()
    assert entity._optimistic_value is None


# --- async_setup_entry — dynamic-devices wiring ---


def _pinned_pump(device_id: str) -> dict:
    """Build a pump device with a pinned DeviceIdentifier cache exposing 'time'."""
    return {
        "device_id": device_id,
        "name": "Pump",
        "family": "",
        "type": "",
        "model": "",
        "online": True,
        "components": {},
        "schedule_data": [],
        "_identify_cache": {
            "key": (device_id, "", "", "", ""),
            "config": SimpleNamespace(
                device_type="pump",
                features={},
                components_range=25,
                required_components=[0, 1, 2, 3],
                entities=["time"],
            ),
        },
    }


async def test_setup_adds_new_device_dynamically() -> None:
    """dynamic-devices: a device appearing on a later poll is wired without a reload."""
    dev1 = _pinned_pump("dev1")
    pool = {"id": POOL_ID, "name": "Pool", "devices": [dev1]}
    coordinator = MagicMock()
    coordinator.data = {POOL_ID: pool}
    coordinator.last_update_success = True
    coordinator.api = SimpleNamespace(cached_pools=[pool], get_pools=AsyncMock(return_value=[pool]))
    coordinator.get_pools_from_data = lambda: [{"id": POOL_ID, **coordinator.data[POOL_ID]}]
    listeners: list[Any] = []
    coordinator.async_add_listener = lambda cb: listeners.append(cb) or (lambda: None)

    added: list[Any] = []
    entry = SimpleNamespace(
        runtime_data=SimpleNamespace(coordinator=coordinator),
        async_on_unload=lambda _unsub: None,
    )
    async_add = MagicMock(side_effect=lambda ents, *a, **k: added.extend(list(ents)))
    await async_setup_entry(MagicMock(), entry, async_add)

    uids_after_setup = {e.unique_id for e in added}
    assert any("dev1" in u for u in uids_after_setup)
    assert not any("dev2" in u for u in uids_after_setup)
    assert listeners, "a coordinator update listener must be registered for dynamic devices"

    # A new device shows up on a later poll; firing the listener must wire it.
    pool["devices"].append(_pinned_pump("dev2"))
    listeners[0]()

    new_uids = {e.unique_id for e in added} - uids_after_setup
    assert new_uids, "new device entities should be added without a reload"
    assert all("dev2" in u for u in new_uids), "only the newly-added device's entities are created"
