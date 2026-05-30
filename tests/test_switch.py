"""Tests for the switch platform classes (pump, heater, auto mode, schedule enable…)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.fluidra_pool.switch import (
    FluidraAutoModeSwitch,
    FluidraHeaterSwitch,
    FluidraPumpSwitch,
    FluidraScheduleEnableSwitch,
)

POOL_ID = "pool-1"
DEVICE_ID = "LE24500883"


# --- fixture builders ----------------------------------------------------


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
        "control_device_component": AsyncMock(return_value=True),
        "set_schedule": AsyncMock(return_value=True),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _attach_ha(switch) -> None:
    """Attach the bits HA usually provides so async_write_ha_state doesn't blow up."""
    switch.hass = MagicMock()
    switch.async_write_ha_state = MagicMock()


@pytest.fixture(autouse=True)
def _skip_confirmation_delay() -> Any:
    """Don't actually sleep in turn_on/off paths during tests."""
    with (
        patch("custom_components.fluidra_pool.switch.pump.asyncio.sleep", new=AsyncMock()),
        patch("custom_components.fluidra_pool.switch.chlorinator.asyncio.sleep", new=AsyncMock()),
    ):
        yield


# --- FluidraPumpSwitch ---------------------------------------------------


def _pump_switch(device_extra: dict | None = None, **api_overrides: Any) -> FluidraPumpSwitch:
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


def test_pump_is_on_uses_real_time_reported_value_when_present() -> None:
    """Real-time pump_reported takes precedence over the legacy is_running flag."""
    pump = _pump_switch({"pump_reported": 1, "is_running": False})
    assert pump.is_on is True


def test_pump_is_on_falls_back_to_is_running() -> None:
    """Without pump_reported, the legacy is_running flag drives is_on."""
    pump = _pump_switch({"is_running": True})
    assert pump.is_on is True


def test_pump_pending_state_overrides_actual_until_expiry() -> None:
    """A fresh optimistic ON shows through even if the device still reports OFF."""
    pump = _pump_switch({"is_running": False})
    pump._set_pending_state(True)
    assert pump.is_on is True


async def test_pump_turn_on_invokes_start_pump_and_refresh() -> None:
    """Successful turn_on issues start_pump and asks the coordinator to refresh."""
    pump = _pump_switch()
    await pump.async_turn_on()
    pump._api.start_pump.assert_awaited_once_with(DEVICE_ID)
    pump.coordinator.async_request_refresh.assert_awaited_once()


async def test_pump_turn_on_clears_pending_state_on_api_failure() -> None:
    """When the API returns False, the optimistic state is rolled back."""
    pump = _pump_switch(api_failure=True) if False else _pump_switch()
    pump._api.start_pump = AsyncMock(return_value=False)
    await pump.async_turn_on()
    assert pump._pending_state is None


async def test_pump_turn_off_invokes_stop_pump() -> None:
    """turn_off goes through stop_pump (not start_pump with value=0)."""
    pump = _pump_switch({"is_running": True})
    await pump.async_turn_off()
    pump._api.stop_pump.assert_awaited_once_with(DEVICE_ID)


def test_pump_extra_state_attributes_expose_pending_action() -> None:
    """While a pending action is in flight the attribute reflects it."""
    pump = _pump_switch({"speed_percent": 65, "operation_mode": 1})
    assert pump.extra_state_attributes["pending_action"] is False
    pump._set_pending_state(True)
    assert pump.extra_state_attributes["pending_action"] is True


# --- FluidraHeaterSwitch -------------------------------------------------


def _heater(device_extra: dict | None = None, **api_overrides: Any) -> FluidraHeaterSwitch:
    device = {
        "device_id": DEVICE_ID,
        "name": "Heater",
        "type": "heater",
        "online": True,
    }
    device.update(device_extra or {})
    heater = FluidraHeaterSwitch(_coordinator([device]), _api(**api_overrides), POOL_ID, DEVICE_ID)
    _attach_ha(heater)
    return heater


async def test_heater_turn_on_writes_component_9_one() -> None:
    """Heater on issues control_device_component(component=9, value=1)."""
    heater = _heater()
    await heater.async_turn_on()
    heater._api.control_device_component.assert_awaited_once_with(DEVICE_ID, 9, 1)


async def test_heater_turn_off_writes_component_9_zero() -> None:
    """Heater off issues control_device_component(component=9, value=0)."""
    heater = _heater()
    await heater.async_turn_off()
    heater._api.control_device_component.assert_awaited_once_with(DEVICE_ID, 9, 0)


def test_heater_is_on_uses_is_heating_flag() -> None:
    """The heater reads is_heating (or fallback is_running) for its state."""
    on = _heater({"is_heating": True})
    off = _heater({"is_heating": False})
    assert on.is_on is True
    assert off.is_on is False


def test_heater_pending_state_clears_when_actual_matches() -> None:
    """Heater optimistic state clears once the poll confirms it, not only on timeout (sensor_switch-2)."""
    heater = _heater({"is_heating": True})
    heater._set_pending_state(True)
    assert heater.is_on is True
    assert heater._pending_state is None


# --- FluidraAutoModeSwitch -----------------------------------------------


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


def test_auto_mode_is_on_prefers_auto_reported_over_legacy_flag() -> None:
    """Real-time auto_reported wins over the legacy auto_mode_enabled."""
    auto = _auto({"auto_reported": 1, "auto_mode_enabled": False})
    assert auto.is_on is True


async def test_auto_mode_turn_on_calls_enable_auto_mode() -> None:
    """Switch on routes to api.enable_auto_mode for the right device."""
    auto = _auto()
    await auto.async_turn_on()
    auto._api.enable_auto_mode.assert_awaited_once_with(DEVICE_ID)


async def test_auto_mode_turn_off_calls_disable_auto_mode() -> None:
    """Switch off routes to api.disable_auto_mode."""
    auto = _auto({"auto_mode_enabled": True})
    await auto.async_turn_off()
    auto._api.disable_auto_mode.assert_awaited_once_with(DEVICE_ID)


# --- FluidraScheduleEnableSwitch (Issue #63 regression guard) ------------


SCHEDULE_4 = {
    "id": 4,
    "enabled": False,
    "startTime": "00 13 * * 0,1,2,3,4,5,6",
    "endTime": "30 14 * * 0,1,2,3,4,5,6",
    "startActions": {"operationName": "0"},
}


def _schedule_switch(schedules: list[dict], **api_overrides: Any) -> FluidraScheduleEnableSwitch:
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
        schedule_id="4",
    )
    _attach_ha(switch)
    return switch


def test_schedule_switch_available_only_when_schedule_present() -> None:
    """A schedule id that isn't in schedule_data marks the entity unavailable."""
    present = _schedule_switch([SCHEDULE_4])
    absent = _schedule_switch([])
    assert present.available is True
    assert absent.available is False


def test_schedule_switch_is_on_reflects_enabled_field() -> None:
    """is_on follows the schedule's `enabled` field when no pending state is set."""
    enabled = _schedule_switch([{**SCHEDULE_4, "enabled": True}])
    disabled = _schedule_switch([SCHEDULE_4])
    assert enabled.is_on is True
    assert disabled.is_on is False


def test_schedule_switch_pending_state_clears_once_server_catches_up() -> None:
    """Optimistic state drops to the real value as soon as it matches (Issue #63)."""
    switch = _schedule_switch([{**SCHEDULE_4, "enabled": True}])
    switch._set_pending_state(True)
    # First read sees the server confirming the optimistic value → cleared.
    assert switch.is_on is True
    assert switch._pending_state is None


async def test_schedule_switch_turn_on_sets_only_target_schedule_enabled() -> None:
    """Enabling schedule 4 must leave the other schedules untouched."""
    schedules = [{**SCHEDULE_4, "id": idx + 1, "enabled": idx == 0} for idx in range(3)]
    schedules.append(SCHEDULE_4)  # schedule_id=4 starts disabled
    switch = _schedule_switch(schedules)
    await switch.async_turn_on()

    # Single PUT to the schedule component with the same 8-slot shape.
    switch._api.set_schedule.assert_awaited_once()
    args, kwargs = switch._api.set_schedule.call_args
    assert args[0] == DEVICE_ID
    sent_schedules = args[1]
    assert kwargs["component_id"] == 20
    assert len(sent_schedules) == 8  # padded to 8 for pump component 20
    # Only id=4 should now be enabled.
    enabled_ids = {s["id"] for s in sent_schedules if s["enabled"]}
    assert enabled_ids == {1, 4}


async def test_schedule_switch_turn_on_no_op_when_schedule_data_missing() -> None:
    """If schedule_data isn't populated yet, no API call and pending state cleared."""
    switch = _schedule_switch([])
    # Remove the key entirely to hit the early-return branch.
    switch.coordinator.data[POOL_ID]["devices"][0].pop("schedule_data")
    await switch.async_turn_on()
    switch._api.set_schedule.assert_not_called()
    assert switch._pending_state is None


async def test_schedule_switch_turn_off_keeps_optimistic_until_server_confirms() -> None:
    """After a successful disable we keep the optimistic OFF until coordinator catches up."""
    schedules = [{**SCHEDULE_4, "enabled": True}]
    switch = _schedule_switch(schedules)
    await switch.async_turn_off()

    # The optimistic state must persist (Issue #63 fix): we no longer clear it eagerly.
    assert switch._pending_state is False
    switch._api.set_schedule.assert_awaited_once()
    switch.coordinator.async_request_refresh.assert_awaited_once()
