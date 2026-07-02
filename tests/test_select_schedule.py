"""Tests for select/schedule.py — schedule mode (pump) and schedule speed (chlorinator)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.fluidra_pool.api_resilience import FluidraError
from custom_components.fluidra_pool.select.schedule import (
    FluidraChlorinatorScheduleSpeedSelect,
    FluidraScheduleModeSelect,
)

POOL_ID = "pool-1"
PUMP_ID = "TEST-PUMP-001"
CHLOR_ID = "TEST-CHLOR-002"


@pytest.fixture(autouse=True)
def _skip_sleep() -> Any:
    """Skip asyncio.sleep so optimistic delays don't slow down tests."""
    with patch("custom_components.fluidra_pool.select.schedule.asyncio.sleep", new=AsyncMock()):
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


def _api() -> SimpleNamespace:
    return SimpleNamespace(set_schedule=AsyncMock(return_value=True))


def _pump_device(schedules: list[dict] | None) -> dict:
    """Build a pump device with optional schedule data."""
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
                features={},
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
    "startTime": "0 8 * * 1,2,3,4,5",
    "endTime": "0 10 * * 1,2,3,4,5",
    "startActions": {"operationName": "1"},
}


# --- FluidraScheduleModeSelect -------------------------------------------


def test_schedule_mode_options_are_three_speed_levels() -> None:
    """Pump schedule modes: 0 (low), 1 (medium), 2 (high)."""
    device = _pump_device([SCHEDULE])
    select = FluidraScheduleModeSelect(_coord(device), _api(), POOL_ID, PUMP_ID, schedule_id="1")
    _attach_ha(select)
    assert select.options == ["0", "1", "2"]


def test_schedule_mode_current_option_reads_operation_name() -> None:
    """current_option mirrors the schedule's startActions.operationName."""
    device = _pump_device([{**SCHEDULE, "startActions": {"operationName": "2"}}])
    select = FluidraScheduleModeSelect(_coord(device), _api(), POOL_ID, PUMP_ID, schedule_id="1")
    _attach_ha(select)
    assert select.current_option == "2"


def test_schedule_mode_current_option_defaults_to_zero_when_schedule_missing() -> None:
    """Without a matching schedule, default to mode '0' (low)."""
    device = _pump_device([SCHEDULE])
    select = FluidraScheduleModeSelect(_coord(device), _api(), POOL_ID, PUMP_ID, schedule_id="99")
    _attach_ha(select)
    assert select.current_option == "0"


def test_schedule_mode_available_only_when_schedule_exists() -> None:
    """An entity for a non-existent schedule slot is unavailable."""
    device = _pump_device([SCHEDULE])
    present = FluidraScheduleModeSelect(_coord(device), _api(), POOL_ID, PUMP_ID, schedule_id="1")
    absent = FluidraScheduleModeSelect(_coord(device), _api(), POOL_ID, PUMP_ID, schedule_id="99")
    _attach_ha(present)
    _attach_ha(absent)
    assert present.available is True
    assert absent.available is False


def test_schedule_mode_icon_changes_with_current_speed() -> None:
    """Each speed has a different speedometer icon."""
    for op_name, expected_icon in (
        ("0", "mdi:speedometer-slow"),
        ("1", "mdi:speedometer-medium"),
        ("2", "mdi:speedometer"),
    ):
        device = _pump_device([{**SCHEDULE, "startActions": {"operationName": op_name}}])
        select = FluidraScheduleModeSelect(_coord(device), _api(), POOL_ID, PUMP_ID, schedule_id="1")
        _attach_ha(select)
        assert select.icon == expected_icon


async def test_schedule_mode_select_invalid_option_is_no_op() -> None:
    """An option outside {0, 1, 2} is rejected before any API call."""
    device = _pump_device([SCHEDULE])
    api = _api()
    select = FluidraScheduleModeSelect(_coord(device), api, POOL_ID, PUMP_ID, schedule_id="1")
    _attach_ha(select)
    await select.async_select_option("garbage")
    api.set_schedule.assert_not_called()


async def test_schedule_mode_select_no_op_without_schedule_data() -> None:
    """Without schedule_data in the device dict, set_schedule isn't called."""
    device = _pump_device(None)  # No schedule_data.
    api = _api()
    select = FluidraScheduleModeSelect(_coord(device), api, POOL_ID, PUMP_ID, schedule_id="1")
    _attach_ha(select)
    await select.async_select_option("2")
    api.set_schedule.assert_not_called()


async def test_schedule_mode_select_no_op_with_empty_schedules() -> None:
    """An empty schedule list also short-circuits."""
    device = _pump_device([])
    api = _api()
    select = FluidraScheduleModeSelect(_coord(device), api, POOL_ID, PUMP_ID, schedule_id="1")
    _attach_ha(select)
    await select.async_select_option("2")
    api.set_schedule.assert_not_called()


async def test_schedule_mode_select_updates_only_target_schedule() -> None:
    """Changing mode for schedule 2 must leave schedule 1 untouched."""
    schedules = [
        {**SCHEDULE, "id": 1, "startActions": {"operationName": "0"}},
        {**SCHEDULE, "id": 2, "startActions": {"operationName": "0"}},
    ]
    device = _pump_device(schedules)
    api = _api()
    select = FluidraScheduleModeSelect(_coord(device), api, POOL_ID, PUMP_ID, schedule_id="2")
    _attach_ha(select)

    await select.async_select_option("2")

    api.set_schedule.assert_awaited_once()
    sent_schedules = api.set_schedule.call_args.args[1]
    # No padding — only the configured schedules are sent (Issue #105).
    assert len(sent_schedules) == 2
    op_by_id = {s["id"]: s["startActions"]["operationName"] for s in sent_schedules}
    assert op_by_id[1] == "0"  # Untouched.
    assert op_by_id[2] == "2"  # New mode.


async def test_schedule_mode_select_sends_only_configured_slots() -> None:
    """Only the configured schedules are sent — no padding to 8 (Issue #105)."""
    device = _pump_device([{**SCHEDULE, "id": 1}])
    api = _api()
    select = FluidraScheduleModeSelect(_coord(device), api, POOL_ID, PUMP_ID, schedule_id="1")
    _attach_ha(select)

    await select.async_select_option("1")

    sent = api.set_schedule.call_args.args[1]
    assert len(sent) == 1
    assert {s["id"] for s in sent} == {1}


async def test_schedule_mode_select_refreshes_coordinator_on_success() -> None:
    """A successful API call triggers a coordinator refresh."""
    device = _pump_device([SCHEDULE])
    api = _api()
    coord = _coord(device)
    select = FluidraScheduleModeSelect(coord, api, POOL_ID, PUMP_ID, schedule_id="1")
    _attach_ha(select)

    await select.async_select_option("2")

    coord.async_request_refresh.assert_awaited_once()


async def test_schedule_mode_select_raises_when_api_rejects() -> None:
    """A False set_schedule surfaces as HomeAssistantError instead of being swallowed (select_time-3)."""
    device = _pump_device([SCHEDULE])
    api = _api()
    api.set_schedule = AsyncMock(return_value=False)
    select = FluidraScheduleModeSelect(_coord(device), api, POOL_ID, PUMP_ID, schedule_id="1")
    _attach_ha(select)

    with pytest.raises(HomeAssistantError):
        await select.async_select_option("2")


async def test_schedule_mode_select_wraps_api_error_as_home_assistant_error() -> None:
    """A FluidraError during set_schedule is wrapped as HomeAssistantError (select_time-3)."""
    device = _pump_device([SCHEDULE])
    api = _api()
    api.set_schedule = AsyncMock(side_effect=FluidraError("boom"))
    select = FluidraScheduleModeSelect(_coord(device), api, POOL_ID, PUMP_ID, schedule_id="1")
    _attach_ha(select)

    with pytest.raises(HomeAssistantError):
        await select.async_select_option("2")


# --- FluidraChlorinatorScheduleSpeedSelect -------------------------------


def _chlor_device(*, schedule_data: list[dict], output_type: str = "speed") -> dict:
    """Build a chlorinator device with schedule_output_type set."""
    return {
        "device_id": CHLOR_ID,
        "name": "Chlorinator",
        "family": "",
        "model": "",
        "type": "chlorinator",
        "online": True,
        "components": {},
        "schedule_data": schedule_data,
        "_identify_cache": {
            "key": (CHLOR_ID, "", "", "chlorinator", ""),
            "config": SimpleNamespace(
                device_type="chlorinator",
                features={"schedule_output_type": output_type, "schedule_component": 258},
                components_range=25,
                required_components=[0, 1, 2, 3],
                entities=[],
            ),
        },
    }


def test_chlor_schedule_speed_uses_s_options_by_default() -> None:
    """Default schedule_output_type 'speed' exposes s1/s2/s3."""
    device = _chlor_device(schedule_data=[{**SCHEDULE, "startActions": {"operationName": "2"}}])
    select = FluidraChlorinatorScheduleSpeedSelect(_coord(device), _api(), POOL_ID, CHLOR_ID, schedule_id="1")
    _attach_ha(select)
    assert select.options == ["s1", "s2", "s3"]


def test_chlor_schedule_speed_uses_output_options_for_exo() -> None:
    """schedule_output_type 'output' (EXO iQ35) exposes pump/aux1/aux2."""
    device = _chlor_device(schedule_data=[{**SCHEDULE, "startActions": {"operationName": "1"}}], output_type="output")
    select = FluidraChlorinatorScheduleSpeedSelect(_coord(device), _api(), POOL_ID, CHLOR_ID, schedule_id="1")
    _attach_ha(select)
    assert select.options == ["pump", "aux1", "aux2"]


async def test_chlor_schedule_speed_resets_optimistic_on_early_return() -> None:
    """An early return (no schedule to update) clears the optimistic option (select_time-1).

    Without the reset, current_option short-circuits on the stale optimistic value
    forever because this class has no _handle_coordinator_update to clear it.
    """
    device = _chlor_device(schedule_data=[])  # empty → early return after the optimistic write
    api = _api()
    select = FluidraChlorinatorScheduleSpeedSelect(_coord(device), api, POOL_ID, CHLOR_ID, schedule_id="1")
    _attach_ha(select)

    await select.async_select_option("s1")

    assert select._optimistic_option is None
    api.set_schedule.assert_not_called()


def test_chlor_schedule_speed_current_option_reads_operation_name() -> None:
    """For DM-style chlorinators, current_option reads operationName."""
    schedule = {**SCHEDULE, "startActions": {"operationName": "2"}}
    device = _chlor_device(schedule_data=[schedule])
    select = FluidraChlorinatorScheduleSpeedSelect(_coord(device), _api(), POOL_ID, CHLOR_ID, schedule_id="1")
    _attach_ha(select)
    assert select.current_option == "s2"


def test_chlor_schedule_speed_current_option_reads_component_actions_for_exo() -> None:
    """For EXO, current_option reads componentActions[0].reportedValue."""
    schedule = {**SCHEDULE, "startActions": {"componentActions": [{"id": 0, "reportedValue": 3}]}}
    device = _chlor_device(schedule_data=[schedule], output_type="output")
    select = FluidraChlorinatorScheduleSpeedSelect(_coord(device), _api(), POOL_ID, CHLOR_ID, schedule_id="1")
    _attach_ha(select)
    assert select.current_option == "aux2"


def test_chlor_schedule_speed_optimistic_value_takes_precedence() -> None:
    """A pending optimistic option is returned during the action window."""
    device = _chlor_device(schedule_data=[SCHEDULE])
    select = FluidraChlorinatorScheduleSpeedSelect(_coord(device), _api(), POOL_ID, CHLOR_ID, schedule_id="1")
    _attach_ha(select)
    select._optimistic_option = "s3"
    assert select.current_option == "s3"


def test_chlor_schedule_speed_available_only_when_schedule_exists() -> None:
    device = _chlor_device(schedule_data=[SCHEDULE])
    present = FluidraChlorinatorScheduleSpeedSelect(_coord(device), _api(), POOL_ID, CHLOR_ID, schedule_id="1")
    absent = FluidraChlorinatorScheduleSpeedSelect(_coord(device), _api(), POOL_ID, CHLOR_ID, schedule_id="42")
    _attach_ha(present)
    _attach_ha(absent)
    assert present.available is True
    assert absent.available is False


async def test_chlor_schedule_speed_select_invalid_option_is_no_op() -> None:
    """Unknown options don't trigger API calls."""
    device = _chlor_device(schedule_data=[SCHEDULE])
    api = _api()
    select = FluidraChlorinatorScheduleSpeedSelect(_coord(device), api, POOL_ID, CHLOR_ID, schedule_id="1")
    _attach_ha(select)
    await select.async_select_option("unknown")
    api.set_schedule.assert_not_called()


async def test_chlor_schedule_speed_select_writes_operation_name_for_dm() -> None:
    """DM-style: sets startActions.operationName = the speed number."""
    schedules = [
        {**SCHEDULE, "id": 1, "startActions": {"operationName": "1"}},
        {**SCHEDULE, "id": 2, "startActions": {"operationName": "1"}},
    ]
    device = _chlor_device(schedule_data=schedules)
    api = _api()
    select = FluidraChlorinatorScheduleSpeedSelect(_coord(device), api, POOL_ID, CHLOR_ID, schedule_id="2")
    _attach_ha(select)

    await select.async_select_option("s3")

    api.set_schedule.assert_awaited_once()
    sent = api.set_schedule.call_args.args[1]
    kwargs = api.set_schedule.call_args.kwargs
    assert kwargs["component_id"] == 258
    sent_by_id = {s["id"]: s["startActions"]["operationName"] for s in sent}
    assert sent_by_id[2] == "3"
    assert sent_by_id[1] == "1"  # Untouched.


async def test_chlor_schedule_speed_select_writes_component_actions_for_exo() -> None:
    """EXO-style writes componentActions[0].reportedValue."""
    schedule = {**SCHEDULE, "id": 1, "startActions": {"componentActions": [{"id": 0, "reportedValue": 1}]}}
    device = _chlor_device(schedule_data=[schedule], output_type="output")
    api = _api()
    select = FluidraChlorinatorScheduleSpeedSelect(_coord(device), api, POOL_ID, CHLOR_ID, schedule_id="1")
    _attach_ha(select)

    await select.async_select_option("aux1")

    sent = api.set_schedule.call_args.args[1]
    assert sent[0]["startActions"]["componentActions"][0]["reportedValue"] == 2


async def test_chlor_schedule_speed_clears_optimistic_after_success() -> None:
    """After a successful API call, the optimistic option is cleared."""
    device = _chlor_device(schedule_data=[SCHEDULE])
    api = _api()
    coord = _coord(device)
    select = FluidraChlorinatorScheduleSpeedSelect(coord, api, POOL_ID, CHLOR_ID, schedule_id="1")
    _attach_ha(select)

    await select.async_select_option("s2")

    assert select._optimistic_option is None
    coord.async_request_refresh.assert_awaited_once()


async def test_chlor_schedule_speed_clears_optimistic_after_api_failure() -> None:
    """If the API returns False, the optimistic state is cleared and HomeAssistantError raised."""
    device = _chlor_device(schedule_data=[SCHEDULE])
    api = SimpleNamespace(set_schedule=AsyncMock(return_value=False))
    select = FluidraChlorinatorScheduleSpeedSelect(_coord(device), api, POOL_ID, CHLOR_ID, schedule_id="1")
    _attach_ha(select)

    with pytest.raises(HomeAssistantError):
        await select.async_select_option("s2")

    assert select._optimistic_option is None


def test_format_cron_time_pads_minute_and_hour() -> None:
    """Underlying _format_cron_time zero-pads h:m and normalises days '*' → full week."""
    device = _chlor_device(schedule_data=[SCHEDULE])
    select = FluidraChlorinatorScheduleSpeedSelect(_coord(device), _api(), POOL_ID, CHLOR_ID, schedule_id="1")
    _attach_ha(select)
    # Minute 5, hour 8 → "05 08".
    assert select._format_cron_time("5 8 * * *") == "05 08 * * 1,2,3,4,5,6,7"


def test_format_cron_time_returns_default_on_empty_input() -> None:
    """An empty CRON string defaults to midnight Mon-Sun."""
    device = _chlor_device(schedule_data=[SCHEDULE])
    select = FluidraChlorinatorScheduleSpeedSelect(_coord(device), _api(), POOL_ID, CHLOR_ID, schedule_id="1")
    _attach_ha(select)
    assert select._format_cron_time("") == "00 00 * * 1,2,3,4,5,6,7"


def test_format_cron_time_keeps_explicit_days() -> None:
    """Explicit day lists are preserved verbatim."""
    device = _chlor_device(schedule_data=[SCHEDULE])
    select = FluidraChlorinatorScheduleSpeedSelect(_coord(device), _api(), POOL_ID, CHLOR_ID, schedule_id="1")
    _attach_ha(select)
    assert select._format_cron_time("30 14 * * 1,3,5") == "30 14 * * 1,3,5"


def test_format_cron_time_returns_input_when_too_few_parts() -> None:
    """A malformed CRON string with fewer than 5 fields is returned unchanged."""
    device = _chlor_device(schedule_data=[SCHEDULE])
    select = FluidraChlorinatorScheduleSpeedSelect(_coord(device), _api(), POOL_ID, CHLOR_ID, schedule_id="1")
    _attach_ha(select)
    assert select._format_cron_time("5 8") == "5 8"


# --- coverage gaps: schedule mode (pump) -------------------------------


def test_schedule_mode_get_schedule_data_swallows_malformed_schedule() -> None:
    """A non-dict entry in schedule_data triggers the guarded except → None."""
    device = _pump_device(["not-a-dict"])  # schedule.get(...) raises AttributeError
    select = FluidraScheduleModeSelect(_coord(device), _api(), POOL_ID, PUMP_ID, schedule_id="1")
    _attach_ha(select)
    assert select._get_schedule_data() is None
    # available combines super().available with the (now None) schedule lookup.
    assert select.available is False


def test_schedule_mode_extra_state_attributes_with_schedule() -> None:
    """When the schedule exists, attrs expose start/end time, enabled and state."""
    schedule = {
        **SCHEDULE,
        "startTime": "0 8 * * 1,2,3,4,5",
        "endTime": "0 10 * * 1,2,3,4,5",
        "enabled": True,
        "state": "RUNNING",
    }
    device = _pump_device([schedule])
    select = FluidraScheduleModeSelect(_coord(device), _api(), POOL_ID, PUMP_ID, schedule_id="1")
    _attach_ha(select)
    attrs = select.extra_state_attributes
    assert attrs["schedule_id"] == "1"
    assert attrs["device_id"] == PUMP_ID
    assert attrs["available_modes"] == ["0", "1", "2"]
    assert attrs["start_time"] == "0 8 * * 1,2,3,4,5"
    assert attrs["end_time"] == "0 10 * * 1,2,3,4,5"
    assert attrs["enabled"] is True
    assert attrs["state"] == "RUNNING"


def test_schedule_mode_extra_state_attributes_without_schedule() -> None:
    """Without a matching schedule, only the static keys are present."""
    device = _pump_device([SCHEDULE])
    select = FluidraScheduleModeSelect(_coord(device), _api(), POOL_ID, PUMP_ID, schedule_id="99")
    _attach_ha(select)
    attrs = select.extra_state_attributes
    assert attrs == {
        "schedule_id": "99",
        "device_id": PUMP_ID,
        "available_modes": ["0", "1", "2"],
    }
    assert "start_time" not in attrs


# --- coverage gaps: chlorinator schedule speed -------------------------


def test_chlor_schedule_speed_get_schedule_data_swallows_malformed_schedule() -> None:
    """A non-dict schedule entry hits the guarded except → None (so unavailable)."""
    device = _chlor_device(schedule_data=[None])  # schedule.get(...) raises AttributeError
    select = FluidraChlorinatorScheduleSpeedSelect(_coord(device), _api(), POOL_ID, CHLOR_ID, schedule_id="1")
    _attach_ha(select)
    assert select._get_schedule_data() is None
    assert select.available is False


def test_chlor_schedule_speed_current_option_defaults_when_no_schedule() -> None:
    """No matching schedule and no optimistic value → first option (s1)."""
    device = _chlor_device(schedule_data=[SCHEDULE])
    select = FluidraChlorinatorScheduleSpeedSelect(_coord(device), _api(), POOL_ID, CHLOR_ID, schedule_id="404")
    _attach_ha(select)
    assert select._optimistic_option is None
    assert select.current_option == "s1"


async def test_chlor_schedule_speed_select_no_op_without_schedule_data() -> None:
    """No schedule_data key → optimistic write is rolled back and no API call."""
    device = _chlor_device(schedule_data=[SCHEDULE])
    # Remove schedule_data so async_select_option hits the early-return branch.
    del device["schedule_data"]
    api = _api()
    select = FluidraChlorinatorScheduleSpeedSelect(_coord(device), api, POOL_ID, CHLOR_ID, schedule_id="1")
    _attach_ha(select)

    await select.async_select_option("s1")

    assert select._optimistic_option is None
    api.set_schedule.assert_not_called()


async def test_chlor_schedule_speed_exo_preserves_other_component_actions() -> None:
    """EXO update reads componentActions of the untouched schedule (loop else-branch)."""
    schedules = [
        {**SCHEDULE, "id": 1, "startActions": {"componentActions": [{"id": 0, "reportedValue": 3}]}},
        {**SCHEDULE, "id": 2, "startActions": {"componentActions": [{"id": 0, "reportedValue": 1}]}},
    ]
    device = _chlor_device(schedule_data=schedules, output_type="output")
    api = _api()
    select = FluidraChlorinatorScheduleSpeedSelect(_coord(device), api, POOL_ID, CHLOR_ID, schedule_id="2")
    _attach_ha(select)

    await select.async_select_option("pump")  # → reportedValue 1 for schedule 2

    sent = api.set_schedule.call_args.args[1]
    by_id = {s["id"]: s["startActions"]["componentActions"][0]["reportedValue"] for s in sent}
    assert by_id[2] == 1  # New value for the target.
    assert by_id[1] == 3  # Untouched schedule keeps its existing reportedValue.


def _chlor_device_generic_component(*, schedule_data: list[dict], schedule_component: int) -> dict:
    """Speed-mode chlorinator with a non-258 schedule_component (generic else-branch)."""
    return {
        "device_id": CHLOR_ID,
        "name": "Chlorinator",
        "family": "",
        "model": "",
        "type": "chlorinator",
        "online": True,
        "components": {},
        "schedule_data": schedule_data,
        "_identify_cache": {
            "key": (CHLOR_ID, "", "", "chlorinator", ""),
            "config": SimpleNamespace(
                device_type="chlorinator",
                features={"schedule_output_type": "speed", "schedule_component": schedule_component},
                components_range=25,
                required_components=[0, 1, 2, 3],
                entities=[],
            ),
        },
    }


async def test_chlor_schedule_speed_generic_branch_for_non_258_component() -> None:
    """speed output + schedule_component != 258 takes the generic scheduler branch (line 335)."""
    schedules = [{**SCHEDULE, "id": 1, "enabled": True, "startActions": {"operationName": "1"}}]
    device = _chlor_device_generic_component(schedule_data=schedules, schedule_component=259)
    api = _api()
    select = FluidraChlorinatorScheduleSpeedSelect(_coord(device), api, POOL_ID, CHLOR_ID, schedule_id="1")
    _attach_ha(select)

    await select.async_select_option("s3")

    api.set_schedule.assert_awaited_once()
    assert api.set_schedule.call_args.kwargs["component_id"] == 259
    sent = api.set_schedule.call_args.args[1]
    # Generic branch keeps the original groupId == id and raw start/end times (no CRON padding).
    assert sent[0]["groupId"] == sent[0]["id"]
    assert sent[0]["startActions"]["operationName"] == "3"
    assert sent[0]["startTime"] == "0 8 * * 1,2,3,4,5"


async def test_chlor_schedule_speed_wraps_api_error_and_clears_optimistic() -> None:
    """A FluidraError raised by set_schedule is wrapped as HomeAssistantError (355-366)."""
    device = _chlor_device(schedule_data=[SCHEDULE])
    api = SimpleNamespace(set_schedule=AsyncMock(side_effect=FluidraError("boom")))
    select = FluidraChlorinatorScheduleSpeedSelect(_coord(device), api, POOL_ID, CHLOR_ID, schedule_id="1")
    _attach_ha(select)

    with pytest.raises(HomeAssistantError):
        await select.async_select_option("s2")

    assert select._optimistic_option is None


def test_chlor_schedule_speed_icon_per_option() -> None:
    """Icon reflects the current speed: s1 slow, s2 medium, else default."""
    for op_name, expected in (("1", "mdi:speedometer-slow"), ("2", "mdi:speedometer-medium"), ("3", "mdi:speedometer")):
        device = _chlor_device(schedule_data=[{**SCHEDULE, "startActions": {"operationName": op_name}}])
        select = FluidraChlorinatorScheduleSpeedSelect(_coord(device), _api(), POOL_ID, CHLOR_ID, schedule_id="1")
        _attach_ha(select)
        assert select.icon == expected


def test_chlor_schedule_speed_extra_state_attributes_with_schedule() -> None:
    """With a schedule, attrs expose times, enabled and state."""
    schedule = {
        **SCHEDULE,
        "startTime": "0 8 * * 1,2,3,4,5",
        "endTime": "0 10 * * 1,2,3,4,5",
        "enabled": True,
        "state": "RUNNING",
    }
    device = _chlor_device(schedule_data=[schedule])
    select = FluidraChlorinatorScheduleSpeedSelect(_coord(device), _api(), POOL_ID, CHLOR_ID, schedule_id="1")
    _attach_ha(select)
    attrs = select.extra_state_attributes
    assert attrs["schedule_id"] == "1"
    assert attrs["device_id"] == CHLOR_ID
    assert attrs["available_speeds"] == ["s1", "s2", "s3"]
    assert attrs["start_time"] == "0 8 * * 1,2,3,4,5"
    assert attrs["end_time"] == "0 10 * * 1,2,3,4,5"
    assert attrs["enabled"] is True
    assert attrs["state"] == "RUNNING"


def test_chlor_schedule_speed_extra_state_attributes_without_schedule() -> None:
    """Without a matching schedule, only static keys are present."""
    device = _chlor_device(schedule_data=[SCHEDULE])
    select = FluidraChlorinatorScheduleSpeedSelect(_coord(device), _api(), POOL_ID, CHLOR_ID, schedule_id="404")
    _attach_ha(select)
    attrs = select.extra_state_attributes
    assert attrs == {
        "schedule_id": "404",
        "device_id": CHLOR_ID,
        "available_speeds": ["s1", "s2", "s3"],
    }
    assert "start_time" not in attrs
