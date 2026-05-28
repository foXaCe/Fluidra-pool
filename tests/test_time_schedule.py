"""Tests for time/schedule.py — Start/End time entities + CRON serialisation."""

from __future__ import annotations

from datetime import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.fluidra_pool.time.schedule import (
    FluidraScheduleEndTimeEntity,
    FluidraScheduleStartTimeEntity,
)

POOL_ID = "pool-1"
PUMP_ID = "TEST-PUMP-001"
CHLOR_ID = "TEST-CHLOR-DM"


@pytest.fixture(autouse=True)
def _skip_sleep() -> Any:
    """Skip asyncio.sleep so optimistic delays don't slow down tests."""
    with patch("custom_components.fluidra_pool.time.schedule.asyncio.sleep", new=AsyncMock()):
        yield


def _coord(device: dict) -> Any:
    coordinator = MagicMock()
    coordinator.data = {POOL_ID: {"id": POOL_ID, "name": "Pool", "devices": [device]}}
    coordinator.async_request_refresh = AsyncMock()
    coordinator.last_update_success = True
    return coordinator


def _attach_ha(entity) -> None:
    entity.hass = MagicMock()
    entity.async_write_ha_state = MagicMock()


def _api(*, success: bool = True) -> SimpleNamespace:
    return SimpleNamespace(set_schedule=AsyncMock(return_value=success))


def _pump_device(schedules: list[dict] | None) -> dict:
    device: dict[str, Any] = {
        "device_id": PUMP_ID,
        "name": "Pump",
        "family": "",
        "model": "E30iQ",
        "type": "pump",
        "online": True,
        "components": {},
        "_identify_cache": {
            "key": (PUMP_ID, "", "", "pump", ""),
            "config": SimpleNamespace(
                device_type="pump",
                features={},  # No schedule_component override → default 20 (pump).
                components_range=25,
                required_components=[0, 1, 2, 3],
                entities=[],
            ),
        },
    }
    if schedules is not None:
        device["schedule_data"] = schedules
    return device


def _dm_chlor_device(schedules: list[dict] | None) -> dict:
    """DM24049704 chlorinator: schedule_component=258, uses padded CRON format."""
    device: dict[str, Any] = {
        "device_id": CHLOR_ID,
        "name": "Chlorinator",
        "family": "",
        "model": "",
        "type": "chlorinator",
        "online": True,
        "components": {},
        "_identify_cache": {
            "key": (CHLOR_ID, "", "", "chlorinator", ""),
            "config": SimpleNamespace(
                device_type="chlorinator",
                features={"schedule_component": 258},
                components_range=25,
                required_components=[0, 1, 2, 3],
                entities=[],
            ),
        },
    }
    if schedules is not None:
        device["schedule_data"] = schedules
    return device


SCHEDULE = {
    "id": 1,
    "enabled": True,
    "startTime": "30 8 * * 1,2,3,4,5",
    "endTime": "0 10 * * 1,2,3,4,5",
    "startActions": {"operationName": "1"},
}


# --- native_value behaviour ----------------------------------------------


def test_start_time_native_value_returns_optimistic_when_set() -> None:
    """An in-flight optimistic value shadows the coordinator value."""
    device = _pump_device([SCHEDULE])
    entity = FluidraScheduleStartTimeEntity(_coord(device), _api(), POOL_ID, PUMP_ID, schedule_id="1")
    _attach_ha(entity)
    entity._optimistic_value = time(7, 15)
    assert entity.native_value == time(7, 15)


def test_end_time_native_value_returns_optimistic_when_set() -> None:
    """Same optimistic-takes-precedence pattern on end time."""
    device = _pump_device([SCHEDULE])
    entity = FluidraScheduleEndTimeEntity(_coord(device), _api(), POOL_ID, PUMP_ID, schedule_id="1")
    _attach_ha(entity)
    entity._optimistic_value = time(11, 30)
    assert entity.native_value == time(11, 30)


def test_start_time_native_value_returns_none_for_missing_schedule() -> None:
    """A schedule_id with no match yields None (and the entity is unavailable)."""
    device = _pump_device([SCHEDULE])
    entity = FluidraScheduleStartTimeEntity(_coord(device), _api(), POOL_ID, PUMP_ID, schedule_id="42")
    _attach_ha(entity)
    assert entity.native_value is None
    assert entity.available is False


def test_end_time_native_value_decodes_cron() -> None:
    """`0 10 * * 1,2,3,4,5` decodes to 10:00."""
    device = _pump_device([SCHEDULE])
    entity = FluidraScheduleEndTimeEntity(_coord(device), _api(), POOL_ID, PUMP_ID, schedule_id="1")
    _attach_ha(entity)
    assert entity.native_value == time(10, 0)


def test_icon_for_start_and_end_time_entities() -> None:
    """Icons differentiate start (clock-start) vs end (clock-end)."""
    device = _pump_device([SCHEDULE])
    start = FluidraScheduleStartTimeEntity(_coord(device), _api(), POOL_ID, PUMP_ID, schedule_id="1")
    end = FluidraScheduleEndTimeEntity(_coord(device), _api(), POOL_ID, PUMP_ID, schedule_id="1")
    _attach_ha(start)
    _attach_ha(end)
    assert start.icon == "mdi:clock-start"
    assert end.icon == "mdi:clock-end"


# --- async_set_value — early returns ------------------------------------


async def test_start_time_set_value_no_op_without_schedule_data_key() -> None:
    """When schedule_data isn't even in device_data, the API isn't called."""
    device = _pump_device(None)
    api = _api()
    entity = FluidraScheduleStartTimeEntity(_coord(device), api, POOL_ID, PUMP_ID, schedule_id="1")
    _attach_ha(entity)

    await entity.async_set_value(time(7, 0))

    api.set_schedule.assert_not_called()
    assert entity._optimistic_value is None


async def test_start_time_set_value_no_op_with_empty_schedule_list() -> None:
    """Empty schedule list also short-circuits."""
    device = _pump_device([])
    api = _api()
    entity = FluidraScheduleStartTimeEntity(_coord(device), api, POOL_ID, PUMP_ID, schedule_id="1")
    _attach_ha(entity)

    await entity.async_set_value(time(7, 0))

    api.set_schedule.assert_not_called()


async def test_end_time_set_value_no_op_without_schedule_data() -> None:
    device = _pump_device(None)
    api = _api()
    entity = FluidraScheduleEndTimeEntity(_coord(device), api, POOL_ID, PUMP_ID, schedule_id="1")
    _attach_ha(entity)

    await entity.async_set_value(time(11, 0))

    api.set_schedule.assert_not_called()


# --- async_set_value — pump schedules (component 20) --------------------


async def test_start_time_set_value_updates_only_target_schedule() -> None:
    """Changing slot 2's start must not move slot 1 (non-overlapping setup)."""
    schedules = [
        {
            "id": 1,
            "enabled": True,
            "startTime": "30 8 * * 1,2,3,4,5",
            "endTime": "0 10 * * 1,2,3,4,5",
            "startActions": {"operationName": "1"},
        },
        {
            "id": 2,
            "enabled": True,
            "startTime": "0 14 * * 1,2,3,4,5",
            "endTime": "0 16 * * 1,2,3,4,5",
            "startActions": {"operationName": "1"},
        },
    ]
    device = _pump_device(schedules)
    api = _api()
    entity = FluidraScheduleStartTimeEntity(_coord(device), api, POOL_ID, PUMP_ID, schedule_id="2")
    _attach_ha(entity)

    # Move slot 2 to 13:00 — still doesn't overlap slot 1 (8:30-10:00).
    await entity.async_set_value(time(13, 0))

    api.set_schedule.assert_awaited_once()
    sent = api.set_schedule.call_args.args[1]
    assert len(sent) == 8  # Padded for pumps.
    start_by_id = {s["id"]: s["startTime"] for s in sent}
    # Slot 1 keeps its 8:30 start (re-formatted but same time).
    assert "30 8" in start_by_id[1] or "8 30" in start_by_id[1]
    # Slot 2 now starts at 13:00.
    assert start_by_id[2].startswith("0 13")


async def test_end_time_set_value_updates_only_target_schedule() -> None:
    schedules = [
        {
            "id": 1,
            "enabled": True,
            "startTime": "30 8 * * 1,2,3,4,5",
            "endTime": "0 10 * * 1,2,3,4,5",
            "startActions": {"operationName": "1"},
        },
        {
            "id": 2,
            "enabled": True,
            "startTime": "0 14 * * 1,2,3,4,5",
            "endTime": "0 16 * * 1,2,3,4,5",
            "startActions": {"operationName": "1"},
        },
    ]
    device = _pump_device(schedules)
    api = _api()
    entity = FluidraScheduleEndTimeEntity(_coord(device), api, POOL_ID, PUMP_ID, schedule_id="2")
    _attach_ha(entity)

    # Push slot 2's end to 15:45 — still inside its own window.
    await entity.async_set_value(time(15, 45))

    api.set_schedule.assert_awaited_once()
    sent = api.set_schedule.call_args.args[1]
    end_by_id = {s["id"]: s["endTime"] for s in sent}
    assert end_by_id[2].startswith("45 15")


async def test_start_time_set_value_preserves_existing_days() -> None:
    """The CRON days from the existing schedule are reused (not reset to all-days)."""
    schedule = {**SCHEDULE, "id": 1, "startTime": "0 8 * * 1,3,5"}
    device = _pump_device([schedule])
    api = _api()
    entity = FluidraScheduleStartTimeEntity(_coord(device), api, POOL_ID, PUMP_ID, schedule_id="1")
    _attach_ha(entity)

    await entity.async_set_value(time(7, 0))

    sent = api.set_schedule.call_args.args[1]
    # Slot 1 should keep days 1,3,5.
    assert sent[0]["startTime"].endswith("* * 1,3,5")


async def test_start_time_set_value_converts_cron_sunday_zero_to_seven() -> None:
    """CRON day 0 (Sunday) is rewritten as 7 in the mobile-app payload."""
    schedule = {**SCHEDULE, "id": 1, "startTime": "0 8 * * 0,1"}
    device = _pump_device([schedule])
    api = _api()
    entity = FluidraScheduleStartTimeEntity(_coord(device), api, POOL_ID, PUMP_ID, schedule_id="1")
    _attach_ha(entity)

    await entity.async_set_value(time(7, 0))

    sent = api.set_schedule.call_args.args[1]
    # The days are sorted and Sunday 0 becomes 7.
    assert sent[0]["startTime"].endswith("* * 1,7")


async def test_start_time_set_value_refreshes_coordinator_on_success() -> None:
    """Successful API call → coordinator refresh, optimistic cleared."""
    device = _pump_device([SCHEDULE])
    api = _api()
    coord = _coord(device)
    entity = FluidraScheduleStartTimeEntity(coord, api, POOL_ID, PUMP_ID, schedule_id="1")
    _attach_ha(entity)

    await entity.async_set_value(time(7, 0))

    assert entity._optimistic_value is None
    coord.async_request_refresh.assert_awaited_once()


async def test_start_time_set_value_clears_optimistic_when_api_fails() -> None:
    """API returning False → optimistic state reverted."""
    device = _pump_device([SCHEDULE])
    api = _api(success=False)
    entity = FluidraScheduleStartTimeEntity(_coord(device), api, POOL_ID, PUMP_ID, schedule_id="1")
    _attach_ha(entity)

    await entity.async_set_value(time(7, 0))

    assert entity._optimistic_value is None


# --- async_set_value — DM24049704 chlorinator (component 258) -----------


async def test_dm_start_time_set_value_uses_component_258() -> None:
    """DM chlorinator routes set_schedule with component_id=258."""
    schedule = {**SCHEDULE, "id": 1, "startTime": "0 8 * * 1,2,3,4,5"}
    device = _dm_chlor_device([schedule])
    api = _api()
    entity = FluidraScheduleStartTimeEntity(_coord(device), api, POOL_ID, CHLOR_ID, schedule_id="1")
    _attach_ha(entity)

    await entity.async_set_value(time(7, 0))

    assert api.set_schedule.call_args.kwargs["component_id"] == 258


async def test_dm_start_time_set_value_uses_chlorinator_payload_shape() -> None:
    """DM payload: groupId=1 (constant), padded CRON, single schedule (no 8-slot padding)."""
    schedule = {**SCHEDULE, "id": 1, "startTime": "0 8 * * 1,2,3,4,5"}
    device = _dm_chlor_device([schedule])
    api = _api()
    entity = FluidraScheduleStartTimeEntity(_coord(device), api, POOL_ID, CHLOR_ID, schedule_id="1")
    _attach_ha(entity)

    await entity.async_set_value(time(7, 0))

    sent = api.set_schedule.call_args.args[1]
    # DM doesn't pad to 8 slots.
    assert len(sent) == 1
    assert sent[0]["groupId"] == 1
    assert sent[0]["enabled"] is True


async def test_dm_end_time_set_value_pads_cron_minute_hour() -> None:
    """DM format zero-pads minute/hour: `0 8` → `00 08`."""
    schedule = {**SCHEDULE, "id": 1, "endTime": "5 9 * * 1,2,3,4,5"}
    device = _dm_chlor_device([schedule])
    api = _api()
    entity = FluidraScheduleEndTimeEntity(_coord(device), api, POOL_ID, CHLOR_ID, schedule_id="1")
    _attach_ha(entity)

    await entity.async_set_value(time(11, 5))

    sent = api.set_schedule.call_args.args[1]
    # The end time was 5:9 → padded to "05 09" but we overwrite with 11:05.
    assert sent[0]["endTime"].startswith("05 11")


# --- Schedule overlap validation -----------------------------------------


async def test_start_time_set_value_rejects_overlap_with_other_enabled_schedule() -> None:
    """Setting start=7:00 when another enabled schedule runs 6:00-10:00 raises ValueError."""
    schedules = [
        {  # Slot 1 runs 6:00-10:00 every weekday.
            "id": 1,
            "enabled": True,
            "startTime": "0 6 * * 1,2,3,4,5",
            "endTime": "0 10 * * 1,2,3,4,5",
            "startActions": {"operationName": "1"},
        },
        {  # Slot 2 currently runs 14:00-16:00 every weekday.
            "id": 2,
            "enabled": True,
            "startTime": "0 14 * * 1,2,3,4,5",
            "endTime": "0 16 * * 1,2,3,4,5",
            "startActions": {"operationName": "1"},
        },
    ]
    device = _pump_device(schedules)
    api = _api()
    entity = FluidraScheduleStartTimeEntity(_coord(device), api, POOL_ID, PUMP_ID, schedule_id="2")
    _attach_ha(entity)

    # Moving slot 2 to start at 7:00 — collides with slot 1's 6:00-10:00.
    await entity.async_set_value(time(7, 0))

    # Validation raises → optimistic cleared, API not called.
    assert entity._optimistic_value is None
    api.set_schedule.assert_not_called()


async def test_start_time_set_value_allows_non_overlapping_change() -> None:
    """Disabled schedules don't count against overlap detection."""
    schedules = [
        {  # Slot 1 disabled → ignored.
            "id": 1,
            "enabled": False,
            "startTime": "0 6 * * 1,2,3,4,5",
            "endTime": "0 10 * * 1,2,3,4,5",
            "startActions": {"operationName": "1"},
        },
        {
            "id": 2,
            "enabled": True,
            "startTime": "0 14 * * 1,2,3,4,5",
            "endTime": "0 16 * * 1,2,3,4,5",
            "startActions": {"operationName": "1"},
        },
    ]
    device = _pump_device(schedules)
    api = _api()
    entity = FluidraScheduleStartTimeEntity(_coord(device), api, POOL_ID, PUMP_ID, schedule_id="2")
    _attach_ha(entity)

    await entity.async_set_value(time(7, 0))

    api.set_schedule.assert_awaited_once()


# --- _times_overlap helper directly --------------------------------------


def test_times_overlap_detects_same_day_overlap() -> None:
    """Two overlapping ranges return True."""
    device = _pump_device([SCHEDULE])
    entity = FluidraScheduleStartTimeEntity(_coord(device), _api(), POOL_ID, PUMP_ID, schedule_id="1")
    _attach_ha(entity)
    assert entity._times_overlap(time(8, 0), time(10, 0), time(9, 0), time(11, 0)) is True


def test_times_overlap_returns_false_for_back_to_back_ranges() -> None:
    """Ranges touching at the boundary (8-10 vs 10-12) don't overlap."""
    device = _pump_device([SCHEDULE])
    entity = FluidraScheduleStartTimeEntity(_coord(device), _api(), POOL_ID, PUMP_ID, schedule_id="1")
    _attach_ha(entity)
    assert entity._times_overlap(time(8, 0), time(10, 0), time(10, 0), time(12, 0)) is False


def test_times_overlap_handles_overnight_range() -> None:
    """An overnight range (22:00-2:00) overlaps with morning 1:00-3:00."""
    device = _pump_device([SCHEDULE])
    entity = FluidraScheduleStartTimeEntity(_coord(device), _api(), POOL_ID, PUMP_ID, schedule_id="1")
    _attach_ha(entity)
    assert entity._times_overlap(time(22, 0), time(2, 0), time(1, 0), time(3, 0)) is True
