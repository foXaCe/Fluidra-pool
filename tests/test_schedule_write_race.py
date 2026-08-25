"""Regression tests for the schedule write race reported on Issue #210.

@efgonzalez edited a Command Connect lights start time on real hardware, watched
the field snap back to the old value, retried a few times, and ended up with
``endTime`` holding the *old* ``startTime`` — an inverted window on the wire.

Two defects produce that, and both are covered here:

* the edited value was dropped ``COMMAND_CONFIRMATION_DELAY`` (3 s) after the
  PUT, while the cabinet takes 5-10 s to report the change back, so the UI
  necessarily showed the pre-write value again and invited a retry;
* every write recomposed the register's whole slot list from the poll cache,
  which still holds the pre-write list during that window, so the retry re-sent
  a stale value for the field it was not editing.
"""

from __future__ import annotations

import asyncio
import copy
from datetime import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.fluidra_pool.const import SCHEDULE_WRITE_HOLD_SECONDS
from custom_components.fluidra_pool.fluidra_api import FluidraPoolAPI
from custom_components.fluidra_pool.time.cabinet_schedule import (
    FluidraCabinetScheduleEndTimeEntity,
    FluidraCabinetScheduleStartTimeEntity,
)

POOL_ID = "pool-1"
DEVICE_ID = "QR24xxxx.ndsr_1"

# The lights slot exactly as captured before the failing test (c36, one slot).
LIGHTS_SLOT: dict[str, Any] = {
    "id": 1,
    "groupId": 1,
    "state": "IDLE",
    "enabled": True,
    "startTime": "15 19 * * 0,1,2,3,4,5,6",
    "endTime": "30 22 * * 0,1,2,3,4,5,6",
    "startActions": {"operationName": "1"},
}


def _make_api(status: int = 200) -> FluidraPoolAPI:
    """A real API object with only the request/auth layer mocked."""
    api = FluidraPoolAPI("a@b.c", "pw")
    api.access_token = "tok"
    api.ensure_valid_token = AsyncMock(return_value=True)
    api._build_auth_headers = MagicMock(return_value={"Authorization": "Bearer tok"})
    api._request = AsyncMock(return_value=(status, {}, ""))
    return api


def _identify_cache() -> dict[str, Any]:
    return {
        "_identify_cache": {
            "key": (DEVICE_ID, "Cabinets", "Command Connect", "cabinet", ""),
            "config": SimpleNamespace(
                device_type="cabinet",
                features={"cabinet_schedule_components": {"pump": 35, "lights": 36}},
            ),
        }
    }


def _device(slots: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "device_id": DEVICE_ID,
        "name": "Command Connect",
        "family": "Cabinets",
        "model": "Command Connect",
        "type": "cabinet",
        "online": True,
        "components": {"36": {"reportedValue": copy.deepcopy(slots)}},
        "cabinet_schedule_data": {"lights": copy.deepcopy(slots)},
        **_identify_cache(),
    }


def _coord(device: dict[str, Any]) -> Any:
    coordinator = MagicMock()
    coordinator.data = {POOL_ID: {"id": POOL_ID, "name": "Pool", "devices": [device]}}
    coordinator.async_request_refresh = AsyncMock()
    coordinator.last_update_success = True
    return coordinator


def _attach_ha(entity: Any) -> None:
    entity.hass = MagicMock()
    entity.async_write_ha_state = MagicMock()


@pytest.fixture(autouse=True)
def _skip_sleep() -> Any:
    """Skip the post-write confirmation delay."""
    with patch("custom_components.fluidra_pool.time.cabinet_schedule.asyncio.sleep", new=AsyncMock()):
        yield


# --- API layer: the list we last PUT is the base for the next write --------


@pytest.mark.asyncio
async def test_pending_schedule_slots_holds_the_last_write() -> None:
    """After a successful PUT, the next write composes on what we sent."""
    api = _make_api()

    assert api.pending_schedule_slots(DEVICE_ID, 36) is None
    assert await api.set_schedule(DEVICE_ID, [LIGHTS_SLOT], component_id=36) is True

    pending = api.pending_schedule_slots(DEVICE_ID, 36)
    assert pending is not None
    # Stored in write shape: the runtime "state" field never goes back out.
    assert "state" not in pending[0]
    assert pending[0]["startTime"] == LIGHTS_SLOT["startTime"]


@pytest.mark.asyncio
async def test_pending_schedule_slots_released_once_the_device_echoes() -> None:
    """The poll cache becomes authoritative again as soon as it agrees."""
    api = _make_api()
    await api.set_schedule(DEVICE_ID, [LIGHTS_SLOT], component_id=36)

    echoed = [{k: v for k, v in LIGHTS_SLOT.items() if k != "state"}]
    api.get_device_by_id = MagicMock(return_value={"components": {"36": {"reportedValue": echoed}}})

    assert api.pending_schedule_slots(DEVICE_ID, 36) is None


@pytest.mark.asyncio
async def test_pending_schedule_slots_expires_so_the_app_is_never_masked() -> None:
    """A change made in the Fluidra app wins once the hold elapses."""
    api = _make_api()
    await api.set_schedule(DEVICE_ID, [LIGHTS_SLOT], component_id=36)

    pending = api._pending_schedule_writes[(DEVICE_ID, 36)]
    pending.expires_at -= SCHEDULE_WRITE_HOLD_SECONDS + 1

    assert api.pending_schedule_slots(DEVICE_ID, 36) is None


@pytest.mark.asyncio
async def test_rejected_write_leaves_no_composition_base() -> None:
    """A refused PUT must not become the base for the next edit."""
    api = _make_api(status=422)
    assert await api.set_schedule(DEVICE_ID, [LIGHTS_SLOT], component_id=36) is False
    assert api.pending_schedule_slots(DEVICE_ID, 36) is None


# --- entity layer: two edits in a row must not undo each other ------------


class _Suspend:
    """A real suspension point.

    ``asyncio.sleep`` is patched out for these tests (it is the same module
    object the production code imports), so a fake that awaited it would never
    actually yield and the interleaving under test could not happen.
    """

    def __await__(self) -> Any:
        yield


class _FrozenCloud:
    """A cloud whose poll cache does not move until the device confirms.

    That is the real behaviour measured on Issue #210 (5-10 s), and it is the
    condition under which the corruption happened: composing from that cache
    re-sends the pre-write value for every field the current edit does not touch.
    """

    def __init__(self) -> None:
        self.writes: list[tuple[int, list[dict[str, Any]]]] = []
        self.order: list[str] = []
        self._locks: dict[tuple[str, int], asyncio.Lock] = {}
        self._pending: dict[tuple[str, int], list[dict[str, Any]]] = {}

    def schedule_write_lock(self, device_id: str, component_id: int) -> asyncio.Lock:
        return self._locks.setdefault((str(device_id), int(component_id)), asyncio.Lock())

    def pending_schedule_slots(self, device_id: str, component_id: int) -> list[dict[str, Any]] | None:
        return copy.deepcopy(self._pending.get((str(device_id), int(component_id))))

    def discard_pending_schedule(self, device_id: str, component_id: int) -> None:
        self._pending.pop((str(device_id), int(component_id)), None)

    async def set_schedule(self, device_id: str, schedules: list[dict[str, Any]], component_id: int) -> bool:
        self.order.append(f"put-{len(self.writes)}-in")
        await _Suspend()  # let a concurrent writer interleave if it can
        self.writes.append((int(component_id), copy.deepcopy(schedules)))
        self._pending[(str(device_id), int(component_id))] = copy.deepcopy(schedules)
        self.order.append(f"put-{len(self.writes) - 1}-out")
        return True


@pytest.mark.asyncio
async def test_second_edit_does_not_revert_the_first() -> None:
    """Editing start then end must not send the pre-write start back."""
    device = _device([LIGHTS_SLOT])
    api = _FrozenCloud()
    coord = _coord(device)

    start = FluidraCabinetScheduleStartTimeEntity(coord, api, POOL_ID, DEVICE_ID, "lights", "1")
    end = FluidraCabinetScheduleEndTimeEntity(coord, api, POOL_ID, DEVICE_ID, "lights", "1")
    _attach_ha(start)
    _attach_ha(end)

    await start.async_set_value(time(19, 30))
    await end.async_set_value(time(23, 0))

    assert len(api.writes) == 2
    last_component, last_slots = api.writes[-1]
    assert last_component == 36
    # The end edit carries the start time the first write set — not "15 19".
    assert last_slots[0]["startTime"].startswith("30 19")
    assert last_slots[0]["endTime"].startswith("0 23")


@pytest.mark.asyncio
async def test_repeated_edits_never_reinstate_the_old_value() -> None:
    """The revert-retry loop that corrupted the wire, replayed."""
    device = _device([LIGHTS_SLOT])
    api = _FrozenCloud()
    entity = FluidraCabinetScheduleStartTimeEntity(_coord(device), api, POOL_ID, DEVICE_ID, "lights", "1")
    _attach_ha(entity)

    for value in (time(19, 30), time(19, 30), time(19, 45)):
        await entity.async_set_value(value)

    for _, slots in api.writes:
        # The end time is the only field these edits must never touch.
        assert slots[0]["endTime"] == LIGHTS_SLOT["endTime"]
    assert api.writes[-1][1][0]["startTime"].startswith("45 19")


@pytest.mark.asyncio
async def test_concurrent_edits_on_one_scheduler_are_serialised() -> None:
    """Two writes fired at once must follow each other, not interleave."""
    device = _device([LIGHTS_SLOT])
    api = _FrozenCloud()
    coord = _coord(device)

    start = FluidraCabinetScheduleStartTimeEntity(coord, api, POOL_ID, DEVICE_ID, "lights", "1")
    end = FluidraCabinetScheduleEndTimeEntity(coord, api, POOL_ID, DEVICE_ID, "lights", "1")
    _attach_ha(start)
    _attach_ha(end)

    await asyncio.gather(
        start.async_set_value(time(19, 30)),
        end.async_set_value(time(23, 0)),
    )

    assert api.order == ["put-0-in", "put-0-out", "put-1-in", "put-1-out"]
    final = api.writes[-1][1][0]
    assert final["startTime"].startswith("30 19")
    assert final["endTime"].startswith("0 23")


# --- entity layer: the edit must stay on screen until the device agrees ---


@pytest.mark.asyncio
async def test_edited_time_survives_the_post_write_refresh() -> None:
    """The refresh 3 s after the PUT reads a device that has not confirmed."""
    device = _device([LIGHTS_SLOT])
    api = _FrozenCloud()
    entity = FluidraCabinetScheduleStartTimeEntity(_coord(device), api, POOL_ID, DEVICE_ID, "lights", "1")
    _attach_ha(entity)

    await entity.async_set_value(time(19, 30))

    # The poll cache still holds 19:15 — the field must not snap back to it.
    assert device["cabinet_schedule_data"]["lights"][0]["startTime"] == "15 19 * * 0,1,2,3,4,5,6"
    assert entity.native_value == time(19, 30)


@pytest.mark.asyncio
async def test_edited_time_released_when_the_device_reports_it() -> None:
    """Once the poll agrees, the device is authoritative again."""
    device = _device([LIGHTS_SLOT])
    api = _FrozenCloud()
    entity = FluidraCabinetScheduleStartTimeEntity(_coord(device), api, POOL_ID, DEVICE_ID, "lights", "1")
    _attach_ha(entity)

    await entity.async_set_value(time(19, 30))
    device["cabinet_schedule_data"]["lights"][0]["startTime"] = "30 19 * * 0,1,2,3,4,5,6"

    assert entity.native_value == time(19, 30)
    assert entity._optimistic_value is None


@pytest.mark.asyncio
async def test_edited_time_released_after_the_hold_expires() -> None:
    """A write the device never takes must stop lying to the user."""
    device = _device([LIGHTS_SLOT])
    api = _FrozenCloud()
    entity = FluidraCabinetScheduleStartTimeEntity(_coord(device), api, POOL_ID, DEVICE_ID, "lights", "1")
    _attach_ha(entity)

    await entity.async_set_value(time(19, 30))
    entity._optimistic_until -= SCHEDULE_WRITE_HOLD_SECONDS + 1

    assert entity.native_value == time(19, 15)
    assert entity._optimistic_value is None
