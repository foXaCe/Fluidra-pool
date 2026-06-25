"""Coverage-gap tests for switch/pump.py and switch/schedule.py.

These modules differ from heater/chlorinator: their async_turn_on/off methods
*swallow* API errors (debug log + clear pending state) instead of re-raising
HomeAssistantError. The tests here exercise the branches not already covered by
test_switch.py / test_entity_error_paths.py:

pump.py
* icon OFF branch (both pump + auto-mode)
* is_on optimistic clear on match AND on expiry (pump + auto-mode)
* async_turn_on/off: API-returns-False revert, and the exception handler
  (no raise, pending cleared, no refresh) for both pump + auto-mode

schedule.py
* icon OFF branch
* _get_schedule_data: empty device_data -> None, and the exception handler
* is_on: pending state surfaces while server hasn't caught up (return path)
* async_turn_on: empty current_schedules early-return
* async_turn_off: missing schedule_data key + empty list early-returns
* async_turn_on/off: API-returns-False revert + exception handler (no raise)
* extra_state_attributes with a schedule present (start/end/state/actions)
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from custom_components.fluidra_pool.api_resilience import FluidraConnectionError
from custom_components.fluidra_pool.switch import (
    FluidraAutoModeSwitch,
    FluidraPumpSwitch,
    FluidraScheduleEnableSwitch,
)

POOL_ID = "pool-1"
DEVICE_ID = "LE24500883"


# --- shared fixture builders ---------------------------------------------


def _coordinator(devices: list[dict]) -> Any:
    coordinator = MagicMock()
    coordinator.data = {POOL_ID: {"id": POOL_ID, "name": "Pool", "devices": devices}}
    coordinator.async_request_refresh = AsyncMock()
    coordinator.last_update_success = True
    return coordinator


def _api(**overrides: Any) -> SimpleNamespace:
    defaults = {
        "start_pump": AsyncMock(return_value=True),
        "stop_pump": AsyncMock(return_value=True),
        "enable_auto_mode": AsyncMock(return_value=True),
        "disable_auto_mode": AsyncMock(return_value=True),
        "set_schedule": AsyncMock(return_value=True),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _attach_ha(entity) -> None:
    entity.hass = MagicMock()
    entity.async_write_ha_state = MagicMock()


@pytest.fixture(autouse=True)
def _skip_sleep() -> Any:
    """Never actually sleep in the optimistic confirmation delays."""
    with patch("custom_components.fluidra_pool.switch.pump.asyncio.sleep", new=AsyncMock()):
        yield


# =========================================================================
# FluidraPumpSwitch
# =========================================================================


def _pump(device_extra: dict | None = None, **api_overrides: Any) -> FluidraPumpSwitch:
    device = {
        "device_id": DEVICE_ID,
        "name": "Pump",
        "model": "E30iQ",
        "type": "pump",
        "online": True,
    }
    device.update(device_extra or {})
    pump = FluidraPumpSwitch(_coordinator([device]), _api(**api_overrides), POOL_ID, DEVICE_ID)
    _attach_ha(pump)
    return pump


def test_pump_icon_off_branch() -> None:
    """The icon falls back to pump-off when the pump is not running."""
    off = _pump({"is_running": False})
    on = _pump({"is_running": True})
    assert off.icon == "mdi:pump-off"
    assert on.icon == "mdi:pump"


def test_pump_is_on_clears_pending_when_actual_matches() -> None:
    """Optimistic ON is dropped as soon as the poll reports the same value."""
    pump = _pump({"pump_reported": 1})
    pump._set_pending_state(True)
    assert pump.is_on is True
    assert pump._pending_state is None  # reconciled with the real value


def test_pump_is_on_clears_pending_on_expiry() -> None:
    """An expired optimistic ON yields the real (mismatched) reported state."""
    pump = _pump({"pump_reported": 0})
    pump._set_pending_state(True)
    pump._last_action_time = 0.0  # far in the past -> expired
    assert pump.is_on is False
    assert pump._pending_state is None


async def test_pump_turn_on_returns_false_reverts_no_refresh() -> None:
    """start_pump False rolls back the optimistic state and skips refresh."""
    pump = _pump(start_pump=AsyncMock(return_value=False))
    await pump.async_turn_on()
    assert pump._pending_state is None
    pump.coordinator.async_request_refresh.assert_not_awaited()


async def test_pump_turn_on_api_raises_is_swallowed_and_clears_pending() -> None:
    """pump turn_on swallows API errors (no raise) and clears pending state."""
    pump = _pump(start_pump=AsyncMock(side_effect=FluidraConnectionError("boom")))
    await pump.async_turn_on()  # must NOT raise
    assert pump._pending_state is None
    pump.coordinator.async_request_refresh.assert_not_awaited()


async def test_pump_turn_off_returns_false_reverts_no_refresh() -> None:
    """stop_pump False rolls back the optimistic state and skips refresh."""
    pump = _pump({"is_running": True}, stop_pump=AsyncMock(return_value=False))
    await pump.async_turn_off()
    assert pump._pending_state is None
    pump.coordinator.async_request_refresh.assert_not_awaited()


async def test_pump_turn_off_api_raises_is_swallowed_and_clears_pending() -> None:
    """pump turn_off swallows API errors (no raise) and clears pending state."""
    pump = _pump({"is_running": True}, stop_pump=AsyncMock(side_effect=aiohttp.ClientError("net")))
    await pump.async_turn_off()  # must NOT raise
    assert pump._pending_state is None


# =========================================================================
# FluidraAutoModeSwitch
# =========================================================================


def _auto(device_extra: dict | None = None, **api_overrides: Any) -> FluidraAutoModeSwitch:
    device = {
        "device_id": DEVICE_ID,
        "name": "Pump",
        "type": "pump",
        "online": True,
        "auto_mode_enabled": False,
    }
    device.update(device_extra or {})
    auto = FluidraAutoModeSwitch(_coordinator([device]), _api(**api_overrides), POOL_ID, DEVICE_ID)
    _attach_ha(auto)
    return auto


def test_auto_mode_icon_off_branch() -> None:
    """The icon falls back to autorenew-off when auto mode is off."""
    off = _auto({"auto_mode_enabled": False})
    on = _auto({"auto_reported": 1})
    assert off.icon == "mdi:autorenew-off"
    assert on.icon == "mdi:auto-mode"


def test_auto_mode_is_on_falls_back_to_legacy_flag() -> None:
    """Without auto_reported, the legacy auto_mode_enabled drives is_on."""
    assert _auto({"auto_mode_enabled": True}).is_on is True
    assert _auto({"auto_mode_enabled": False}).is_on is False


def test_auto_mode_is_on_clears_pending_when_actual_matches() -> None:
    """Optimistic ON clears once the reported value confirms it."""
    auto = _auto({"auto_reported": 1})
    auto._set_pending_state(True)
    assert auto.is_on is True
    assert auto._pending_state is None


def test_auto_mode_is_on_clears_pending_on_expiry() -> None:
    """An expired optimistic ON yields the real (mismatched) state."""
    auto = _auto({"auto_reported": 0})
    auto._set_pending_state(True)
    auto._last_action_time = 0.0
    assert auto.is_on is False
    assert auto._pending_state is None


def test_auto_mode_is_on_pending_shows_through_until_match() -> None:
    """A fresh optimistic ON is reported even while auto still says off."""
    auto = _auto({"auto_reported": 0})
    auto._set_pending_state(True)
    assert auto.is_on is True
    assert auto._pending_state is True


async def test_auto_mode_turn_on_returns_false_reverts_no_refresh() -> None:
    """enable_auto_mode False rolls back the optimistic state, no refresh."""
    auto = _auto(enable_auto_mode=AsyncMock(return_value=False))
    await auto.async_turn_on()
    assert auto._pending_state is None
    auto.coordinator.async_request_refresh.assert_not_awaited()


async def test_auto_mode_turn_on_api_raises_is_swallowed() -> None:
    """auto-mode turn_on swallows API errors and clears pending state."""
    auto = _auto(enable_auto_mode=AsyncMock(side_effect=FluidraConnectionError("boom")))
    await auto.async_turn_on()  # must NOT raise
    assert auto._pending_state is None
    auto.coordinator.async_request_refresh.assert_not_awaited()


async def test_auto_mode_turn_off_returns_false_reverts_no_refresh() -> None:
    """disable_auto_mode False rolls back the optimistic state, no refresh."""
    auto = _auto({"auto_mode_enabled": True}, disable_auto_mode=AsyncMock(return_value=False))
    await auto.async_turn_off()
    assert auto._pending_state is None
    auto.coordinator.async_request_refresh.assert_not_awaited()


async def test_auto_mode_turn_off_api_raises_is_swallowed() -> None:
    """auto-mode turn_off swallows API errors and clears pending state."""
    auto = _auto({"auto_mode_enabled": True}, disable_auto_mode=AsyncMock(side_effect=aiohttp.ClientError("net")))
    await auto.async_turn_off()  # must NOT raise
    assert auto._pending_state is None


# =========================================================================
# FluidraScheduleEnableSwitch
# =========================================================================


SCHEDULE_4 = {
    "id": 4,
    "enabled": False,
    "startTime": "00 13 * * 0,1,2,3,4,5,6",
    "endTime": "30 14 * * 0,1,2,3,4,5,6",
    "startActions": {"operationName": "0"},
    "endActions": {"operationName": "1"},
    "state": "IDLE",
}


def _schedule(schedules: list[dict], schedule_id: str = "4", **api_overrides: Any) -> FluidraScheduleEnableSwitch:
    device = {
        "device_id": DEVICE_ID,
        "name": "Pump",
        "type": "pump",
        "model": "E30iQ",
        "online": True,
        "schedule_data": schedules,
    }
    switch = FluidraScheduleEnableSwitch(
        _coordinator([device]),
        _api(**api_overrides),
        POOL_ID,
        DEVICE_ID,
        schedule_id=schedule_id,
    )
    _attach_ha(switch)
    return switch


def test_schedule_icon_off_branch() -> None:
    """Icon falls back to calendar-outline when the schedule is disabled."""
    off = _schedule([SCHEDULE_4])
    on = _schedule([{**SCHEDULE_4, "enabled": True}])
    assert off.icon == "mdi:calendar-outline"
    assert on.icon == "mdi:calendar-clock"


def test_schedule_get_schedule_data_returns_none_when_device_empty() -> None:
    """With no device_data in the coordinator, _get_schedule_data returns None."""
    switch = _schedule([SCHEDULE_4])
    # Empty the coordinator so device_data resolves to {} (falsy).
    switch.coordinator.data = {}
    assert switch._get_schedule_data() is None


def test_schedule_get_schedule_data_swallows_exception() -> None:
    """A broken schedule payload is caught and yields None (debug-logged)."""
    switch = _schedule([SCHEDULE_4])
    # device_data raises when iterated/accessed -> exception handler -> None.
    bad = MagicMock()
    bad.__contains__.side_effect = TypeError("broken schedule payload")
    with patch.object(type(switch), "device_data", new_callable=lambda: property(lambda self: bad)):
        assert switch._get_schedule_data() is None


def test_schedule_is_on_pending_shows_through_until_server_catches_up() -> None:
    """While the server still reports the old value, the optimistic state shows."""
    # Pending ON but the schedule still reports disabled -> return pending (not cleared).
    switch = _schedule([SCHEDULE_4])  # enabled == False
    switch._set_pending_state(True)
    assert switch.is_on is True
    assert switch._pending_state is True  # not yet reconciled


async def test_schedule_turn_on_empty_schedules_early_return() -> None:
    """schedule_data present but empty -> no API call, pending cleared."""
    switch = _schedule([])  # key present, list empty
    await switch.async_turn_on()
    switch._api.set_schedule.assert_not_awaited()
    assert switch._pending_state is None


async def test_schedule_turn_on_returns_false_reverts() -> None:
    """set_schedule False rolls back the optimistic ON state."""
    switch = _schedule([SCHEDULE_4], set_schedule=AsyncMock(return_value=False))
    await switch.async_turn_on()
    assert switch._pending_state is None


async def test_schedule_turn_on_api_raises_is_swallowed() -> None:
    """Enable swallows API errors (no raise) and clears pending state."""
    switch = _schedule([SCHEDULE_4], set_schedule=AsyncMock(side_effect=FluidraConnectionError("boom")))
    await switch.async_turn_on()  # must NOT raise
    assert switch._pending_state is None


async def test_schedule_turn_off_missing_schedule_data_key_early_return() -> None:
    """No schedule_data key at all -> no API call, pending cleared."""
    switch = _schedule([])
    switch.coordinator.data[POOL_ID]["devices"][0].pop("schedule_data")
    await switch.async_turn_off()
    switch._api.set_schedule.assert_not_awaited()
    assert switch._pending_state is None


async def test_schedule_turn_off_empty_schedules_early_return() -> None:
    """schedule_data present but empty -> no API call on turn_off, pending cleared."""
    switch = _schedule([])
    await switch.async_turn_off()
    switch._api.set_schedule.assert_not_awaited()
    assert switch._pending_state is None


async def test_schedule_turn_off_sets_only_target_disabled() -> None:
    """Disabling schedule 4 leaves the other schedules' enabled state intact."""
    schedules = [{**SCHEDULE_4, "id": idx + 1, "enabled": True} for idx in range(3)]
    schedules.append({**SCHEDULE_4, "enabled": True})  # id=4 starts enabled
    switch = _schedule(schedules)
    await switch.async_turn_off()
    switch._api.set_schedule.assert_awaited_once()
    args, kwargs = switch._api.set_schedule.call_args
    assert args[0] == DEVICE_ID
    sent = args[1]
    assert kwargs["component_id"] == 20
    assert len(sent) == 4  # only the configured slots, no padding (Issue #105)
    # id=4 disabled, ids 1-3 still enabled.
    by_id = {s["id"]: s["enabled"] for s in sent}
    assert by_id[4] is False
    assert by_id[1] is True
    assert by_id[2] is True
    assert by_id[3] is True


async def test_schedule_turn_off_returns_false_reverts() -> None:
    """set_schedule False rolls back the optimistic OFF state."""
    switch = _schedule([{**SCHEDULE_4, "enabled": True}], set_schedule=AsyncMock(return_value=False))
    await switch.async_turn_off()
    assert switch._pending_state is None


async def test_schedule_turn_off_api_raises_is_swallowed() -> None:
    """Disable swallows API errors (no raise) and clears pending state."""
    switch = _schedule(
        [{**SCHEDULE_4, "enabled": True}],
        set_schedule=AsyncMock(side_effect=aiohttp.ClientError("net")),
    )
    await switch.async_turn_off()  # must NOT raise
    assert switch._pending_state is None


def test_schedule_extra_state_attributes_with_schedule_present() -> None:
    """Attributes expose the schedule's times, state and start/end actions."""
    switch = _schedule([SCHEDULE_4])
    attrs = switch.extra_state_attributes
    assert attrs["schedule_id"] == "4"
    assert attrs["device_id"] == DEVICE_ID
    assert attrs["start_time"] == SCHEDULE_4["startTime"]
    assert attrs["end_time"] == SCHEDULE_4["endTime"]
    assert attrs["state"] == "IDLE"
    assert attrs["start_action"] == {"operationName": "0"}
    assert attrs["end_action"] == {"operationName": "1"}
    assert attrs["pending_action"] is False


def test_schedule_extra_state_attributes_without_schedule() -> None:
    """With no matching schedule, only the base attributes are present."""
    switch = _schedule([])
    attrs = switch.extra_state_attributes
    assert attrs["schedule_id"] == "4"
    assert "start_time" not in attrs
    assert attrs["pending_action"] is False
