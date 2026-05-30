"""Tests for time/light.py — LumiPlus light schedule start/end time entities.

Also exercises edge cases of parse_schedule_time (time/base.py) not already
covered by tests/test_time.py and tests/test_time_schedule.py.
"""

from __future__ import annotations

from datetime import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.fluidra_pool.api_resilience import FluidraConnectionError
from custom_components.fluidra_pool.time.base import parse_schedule_time
from custom_components.fluidra_pool.time.light import (
    FluidraLightScheduleEndTimeEntity,
    FluidraLightScheduleStartTimeEntity,
)

POOL_ID = "pool-1"
LIGHT_ID = "LE24500883"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _light_device(schedules: list[dict] | None, *, online: bool = True) -> dict:
    device: dict[str, Any] = {
        "device_id": LIGHT_ID,
        "name": "Pool Light",
        "family": "",
        "model": "LumiPlus Connect",
        "type": "light",
        "online": online,
        "components": {},
    }
    if schedules is not None:
        device["schedule_data"] = schedules
    return device


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


SCHEDULE = {
    "id": 1,
    "enabled": True,
    "startTime": "30 08 * * 1,2,3,4,5",
    "endTime": "00 22 * * 1,2,3,4,5",
    "startActions": {"operationName": "11"},
}


# --------------------------------------------------------------------------
# parse_schedule_time — edge cases (focus on inputs not covered elsewhere)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("incoming", "expected"),
    [
        # None passthrough
        (None, None),
        # time object passthrough (identity)
        (time(13, 45), time(13, 45)),
        # int minutes from midnight
        (0, time(0, 0)),
        (75, time(1, 15)),
        # negative int — python floor-division semantics, still a valid time
        # -1 min // 60 == -1 -> hour=(-1)%24=23 ; -1 % 60 == 59 -> time(23, 59)
        (-1, time(23, 59)),
        # float minutes are truncated to int
        (90.9, time(1, 30)),
        # CRON "mm HH * * days"
        ("0 5 * * 1,2,3,4,5,6,7", time(5, 0)),
        ("30 08 * * 1,2,3,4,5", time(8, 30)),
        # leading/trailing whitespace is stripped
        ("  15 06 * * 1  ", time(6, 15)),
        # numeric string treated as minutes-from-midnight
        ("540", time(9, 0)),
        # numeric-string fallback when only one token & out-of-CRON-range
        ("1439", time(23, 59)),
    ],
)
def test_parse_schedule_time_valid(incoming, expected) -> None:
    """Valid time / int / float / CRON / numeric-string inputs decode correctly."""
    assert parse_schedule_time(incoming) == expected


@pytest.mark.parametrize(
    "incoming",
    [
        # CRON with out-of-range hour falls through; "24" is also too many minutes
        # for the numeric-string fallback (1440 -> 24h -> rejected) -> None
        "00 24 * * 1",
        # CRON with out-of-range minute, single token won't re-parse as int -> None
        "60 10 * * 1",
        # non-numeric garbage
        "not-a-time",
        "",
        # whitespace-only
        "   ",
        # single non-numeric token
        "abc",
        # CRON with non-int fields
        "xx yy * * 1",
        # unsupported type: list is not time/int/float/str -> None
        [1, 2],
        # unsupported type: dict -> None
        {"hour": 5},
    ],
)
def test_parse_schedule_time_invalid_returns_none(incoming) -> None:
    """Out-of-range, garbage and unsupported-type inputs return None."""
    assert parse_schedule_time(incoming) is None


def test_parse_schedule_time_numeric_string_over_one_day_rejected() -> None:
    """A numeric string mapping past 23h is rejected (guard `0 <= hours <= 23`)."""
    # 1440 minutes == 24:00, hours=24 fails the guard, returns None.
    assert parse_schedule_time("1440") is None


def test_parse_schedule_time_int_minutes_wrap_modulo_24() -> None:
    """Integer minutes beyond a day wrap with `% 24` (no guard on the int path)."""
    # 1500 min = 25h -> 25 % 24 = 1h, 0 min.
    assert parse_schedule_time(1500) == time(1, 0)


# --------------------------------------------------------------------------
# native_value
# --------------------------------------------------------------------------


def test_start_native_value_parses_cron() -> None:
    """Start time decoded from CRON minute/hour fields."""
    entity = FluidraLightScheduleStartTimeEntity(_coord(_light_device([SCHEDULE])), _api(), POOL_ID, LIGHT_ID, "1")
    _attach_ha(entity)
    assert entity.native_value == time(8, 30)


def test_end_native_value_parses_cron() -> None:
    """End time decoded from CRON minute/hour fields."""
    entity = FluidraLightScheduleEndTimeEntity(_coord(_light_device([SCHEDULE])), _api(), POOL_ID, LIGHT_ID, "1")
    _attach_ha(entity)
    assert entity.native_value == time(22, 0)


def test_start_native_value_parses_numeric_minutes() -> None:
    """Numeric minutes-from-midnight storage also decodes for the light start time."""
    schedule = {**SCHEDULE, "startTime": 555}  # 9:15
    entity = FluidraLightScheduleStartTimeEntity(_coord(_light_device([schedule])), _api(), POOL_ID, LIGHT_ID, "1")
    _attach_ha(entity)
    assert entity.native_value == time(9, 15)


def test_native_value_none_when_schedule_missing() -> None:
    """A schedule_id with no match yields None native_value (and unavailable)."""
    entity = FluidraLightScheduleStartTimeEntity(_coord(_light_device([SCHEDULE])), _api(), POOL_ID, LIGHT_ID, "99")
    _attach_ha(entity)
    assert entity.native_value is None


def test_end_native_value_none_when_no_schedule_data() -> None:
    """No schedule_data on the device → native_value None."""
    entity = FluidraLightScheduleEndTimeEntity(_coord(_light_device(None)), _api(), POOL_ID, LIGHT_ID, "1")
    _attach_ha(entity)
    assert entity.native_value is None


def test_native_value_none_for_empty_cron_string() -> None:
    """Missing startTime falls back to '' which parses to None."""
    schedule = {"id": 1, "enabled": True, "endTime": "00 22 * * 1"}
    entity = FluidraLightScheduleStartTimeEntity(_coord(_light_device([schedule])), _api(), POOL_ID, LIGHT_ID, "1")
    _attach_ha(entity)
    assert entity.native_value is None


# --------------------------------------------------------------------------
# icon
# --------------------------------------------------------------------------


def test_icons() -> None:
    """Start uses clock-start, end uses clock-end."""
    start = FluidraLightScheduleStartTimeEntity(_coord(_light_device([SCHEDULE])), _api(), POOL_ID, LIGHT_ID, "1")
    end = FluidraLightScheduleEndTimeEntity(_coord(_light_device([SCHEDULE])), _api(), POOL_ID, LIGHT_ID, "1")
    _attach_ha(start)
    _attach_ha(end)
    assert start.icon == "mdi:clock-start"
    assert end.icon == "mdi:clock-end"


# --------------------------------------------------------------------------
# available
# --------------------------------------------------------------------------


def test_available_true_when_online_and_schedule_exists() -> None:
    """Entity available when coordinator healthy, device online, schedule present."""
    entity = FluidraLightScheduleStartTimeEntity(_coord(_light_device([SCHEDULE])), _api(), POOL_ID, LIGHT_ID, "1")
    _attach_ha(entity)
    assert entity.available is True


def test_available_false_when_schedule_missing() -> None:
    """No matching schedule → unavailable even if online."""
    entity = FluidraLightScheduleStartTimeEntity(_coord(_light_device([SCHEDULE])), _api(), POOL_ID, LIGHT_ID, "99")
    _attach_ha(entity)
    assert entity.available is False


def test_available_false_when_device_offline() -> None:
    """Device offline → unavailable (super().available is False)."""
    entity = FluidraLightScheduleStartTimeEntity(
        _coord(_light_device([SCHEDULE], online=False)), _api(), POOL_ID, LIGHT_ID, "1"
    )
    _attach_ha(entity)
    assert entity.available is False


def test_available_false_when_coordinator_unhealthy() -> None:
    """Coordinator last_update_success False → unavailable."""
    coord = _coord(_light_device([SCHEDULE]))
    coord.last_update_success = False
    entity = FluidraLightScheduleEndTimeEntity(coord, _api(), POOL_ID, LIGHT_ID, "1")
    _attach_ha(entity)
    assert entity.available is False


# --------------------------------------------------------------------------
# device_info
# --------------------------------------------------------------------------


def test_device_info_uses_device_name_and_model() -> None:
    """device_info reflects the device name, manufacturer and model."""
    entity = FluidraLightScheduleStartTimeEntity(_coord(_light_device([SCHEDULE])), _api(), POOL_ID, LIGHT_ID, "1")
    _attach_ha(entity)
    info = entity.device_info
    assert info["identifiers"] == {("fluidra_pool", LIGHT_ID)}
    assert info["name"] == "Pool Light"
    assert info["manufacturer"] == "Fluidra"
    assert info["model"] == "LumiPlus Connect"
    assert info["via_device"] == ("fluidra_pool", POOL_ID)


def test_device_info_falls_back_to_default_name_and_model() -> None:
    """Without name/model, device_info uses the 'Pool Light <id>' / default model."""
    device = {
        "device_id": LIGHT_ID,
        "type": "light",
        "online": True,
        "components": {},
        "schedule_data": [SCHEDULE],
    }
    entity = FluidraLightScheduleEndTimeEntity(_coord(device), _api(), POOL_ID, LIGHT_ID, "1")
    _attach_ha(entity)
    info = entity.device_info
    assert info["name"] == f"Pool Light {LIGHT_ID}"
    assert info["model"] == "LumiPlus Connect"
    assert info["manufacturer"] == "Fluidra"


# --------------------------------------------------------------------------
# async_set_value — early returns (no-op)
# --------------------------------------------------------------------------


async def test_start_set_value_no_op_without_schedule_data_key() -> None:
    """No schedule_data key → API not called, returns silently."""
    api = _api()
    entity = FluidraLightScheduleStartTimeEntity(_coord(_light_device(None)), api, POOL_ID, LIGHT_ID, "1")
    _attach_ha(entity)
    await entity.async_set_value(time(7, 0))
    api.set_schedule.assert_not_called()


async def test_start_set_value_no_op_with_empty_schedule_list() -> None:
    """Empty schedule list short-circuits before calling the API."""
    api = _api()
    entity = FluidraLightScheduleStartTimeEntity(_coord(_light_device([])), api, POOL_ID, LIGHT_ID, "1")
    _attach_ha(entity)
    await entity.async_set_value(time(7, 0))
    api.set_schedule.assert_not_called()


async def test_end_set_value_no_op_without_schedule_data_key() -> None:
    """End entity early-returns when schedule_data is absent."""
    api = _api()
    entity = FluidraLightScheduleEndTimeEntity(_coord(_light_device(None)), api, POOL_ID, LIGHT_ID, "1")
    _attach_ha(entity)
    await entity.async_set_value(time(23, 0))
    api.set_schedule.assert_not_called()


async def test_end_set_value_no_op_with_empty_schedule_list() -> None:
    """End entity short-circuits on an empty schedule list (no API call)."""
    api = _api()
    entity = FluidraLightScheduleEndTimeEntity(_coord(_light_device([])), api, POOL_ID, LIGHT_ID, "1")
    _attach_ha(entity)
    await entity.async_set_value(time(23, 0))
    api.set_schedule.assert_not_called()


async def test_end_set_value_defaults_days_when_cron_days_non_int() -> None:
    """End entity falls back to all-days when CRON day tokens aren't integers."""
    schedule = {**SCHEDULE, "endTime": "0 22 * * x,y"}
    api = _api()
    entity = FluidraLightScheduleEndTimeEntity(_coord(_light_device([schedule])), api, POOL_ID, LIGHT_ID, "1")
    _attach_ha(entity)

    await entity.async_set_value(time(21, 0))

    sent = api.set_schedule.call_args.args[1]
    assert sent[0]["endTime"] == "0 21 * * 1,2,3,4,5,6,7"


# --------------------------------------------------------------------------
# async_set_value — success
# --------------------------------------------------------------------------


async def test_start_set_value_success_updates_target_and_refreshes() -> None:
    """Setting start time builds a component-40 payload and refreshes coordinator."""
    api = _api()
    coord = _coord(_light_device([SCHEDULE]))
    entity = FluidraLightScheduleStartTimeEntity(coord, api, POOL_ID, LIGHT_ID, "1")
    _attach_ha(entity)

    await entity.async_set_value(time(6, 45))

    api.set_schedule.assert_awaited_once()
    assert api.set_schedule.call_args.kwargs["component_id"] == 40
    sent = api.set_schedule.call_args.args[1]
    assert len(sent) == 1
    sched = sent[0]
    # Start re-serialised to "45 6 * * <days>", days preserved from existing CRON.
    assert sched["startTime"] == "45 6 * * 1,2,3,4,5"
    # End time left untouched.
    assert sched["endTime"] == "00 22 * * 1,2,3,4,5"
    assert sched["startActions"] == {"operationName": "11"}
    coord.async_request_refresh.assert_awaited_once()


async def test_end_set_value_success_updates_endtime_only() -> None:
    """Setting end time only changes endTime, keeping startTime intact."""
    api = _api()
    coord = _coord(_light_device([SCHEDULE]))
    entity = FluidraLightScheduleEndTimeEntity(coord, api, POOL_ID, LIGHT_ID, "1")
    _attach_ha(entity)

    await entity.async_set_value(time(23, 30))

    sent = api.set_schedule.call_args.args[1]
    sched = sent[0]
    assert sched["endTime"] == "30 23 * * 1,2,3,4,5"
    assert sched["startTime"] == "30 08 * * 1,2,3,4,5"
    coord.async_request_refresh.assert_awaited_once()


async def test_start_set_value_defaults_days_when_cron_malformed() -> None:
    """A startTime without 5 CRON fields falls back to all 7 days."""
    schedule = {**SCHEDULE, "startTime": "bad"}
    api = _api()
    entity = FluidraLightScheduleStartTimeEntity(_coord(_light_device([schedule])), api, POOL_ID, LIGHT_ID, "1")
    _attach_ha(entity)

    await entity.async_set_value(time(7, 0))

    sent = api.set_schedule.call_args.args[1]
    assert sent[0]["startTime"] == "0 7 * * 1,2,3,4,5,6,7"


async def test_start_set_value_defaults_days_when_cron_days_non_int() -> None:
    """Non-integer day tokens fall back to default all-days set."""
    schedule = {**SCHEDULE, "startTime": "0 8 * * a,b,c"}
    api = _api()
    entity = FluidraLightScheduleStartTimeEntity(_coord(_light_device([schedule])), api, POOL_ID, LIGHT_ID, "1")
    _attach_ha(entity)

    await entity.async_set_value(time(7, 0))

    sent = api.set_schedule.call_args.args[1]
    assert sent[0]["startTime"] == "0 7 * * 1,2,3,4,5,6,7"


async def test_set_value_leaves_other_schedules_untouched() -> None:
    """Only the target schedule's time is rewritten; others are rebuilt verbatim."""
    other = {
        "id": 2,
        "enabled": False,
        "startTime": "00 09 * * 1,2",
        "endTime": "00 21 * * 1,2",
        "startActions": {"operationName": "11"},
    }
    api = _api()
    entity = FluidraLightScheduleStartTimeEntity(_coord(_light_device([SCHEDULE, other])), api, POOL_ID, LIGHT_ID, "1")
    _attach_ha(entity)

    await entity.async_set_value(time(5, 0))

    sent = api.set_schedule.call_args.args[1]
    by_id = {s["id"]: s for s in sent}
    assert by_id[1]["startTime"] == "0 5 * * 1,2,3,4,5"
    # Untargeted schedule keeps its original times and enabled flag.
    assert by_id[2]["startTime"] == "00 09 * * 1,2"
    assert by_id[2]["endTime"] == "00 21 * * 1,2"
    assert by_id[2]["enabled"] is False


# --------------------------------------------------------------------------
# async_set_value — error paths
# --------------------------------------------------------------------------


async def test_start_set_value_raises_when_api_returns_false() -> None:
    """API returning False raises HomeAssistantError (light_schedule_set_failed)."""
    api = _api(success=False)
    coord = _coord(_light_device([SCHEDULE]))
    entity = FluidraLightScheduleStartTimeEntity(coord, api, POOL_ID, LIGHT_ID, "1")
    _attach_ha(entity)

    with pytest.raises(HomeAssistantError):
        await entity.async_set_value(time(7, 0))

    coord.async_request_refresh.assert_not_called()


async def test_end_set_value_raises_when_api_returns_false() -> None:
    """End entity also raises when set_schedule returns False."""
    api = _api(success=False)
    entity = FluidraLightScheduleEndTimeEntity(_coord(_light_device([SCHEDULE])), api, POOL_ID, LIGHT_ID, "1")
    _attach_ha(entity)

    with pytest.raises(HomeAssistantError):
        await entity.async_set_value(time(23, 0))


async def test_start_set_value_raises_on_api_exception() -> None:
    """A FluidraConnectionError from the API is wrapped into HomeAssistantError."""
    api = SimpleNamespace(set_schedule=AsyncMock(side_effect=FluidraConnectionError("boom")))
    coord = _coord(_light_device([SCHEDULE]))
    entity = FluidraLightScheduleStartTimeEntity(coord, api, POOL_ID, LIGHT_ID, "1")
    _attach_ha(entity)

    with pytest.raises(HomeAssistantError):
        await entity.async_set_value(time(7, 0))

    coord.async_request_refresh.assert_not_called()


async def test_end_set_value_raises_on_api_exception() -> None:
    """End entity wraps FluidraConnectionError into HomeAssistantError."""
    api = SimpleNamespace(set_schedule=AsyncMock(side_effect=FluidraConnectionError("boom")))
    entity = FluidraLightScheduleEndTimeEntity(_coord(_light_device([SCHEDULE])), api, POOL_ID, LIGHT_ID, "1")
    _attach_ha(entity)

    with pytest.raises(HomeAssistantError):
        await entity.async_set_value(time(23, 0))


# --------------------------------------------------------------------------
# unique_id / translation
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# base-class methods exercised through the light entity
# --------------------------------------------------------------------------


def test_get_schedule_data_returns_none_when_device_absent() -> None:
    """When the device id isn't in coordinator data, _get_schedule_data → None."""
    coord = _coord(_light_device([SCHEDULE]))
    entity = FluidraLightScheduleStartTimeEntity(coord, _api(), POOL_ID, "OTHER-ID", "1")
    _attach_ha(entity)
    assert entity._get_schedule_data() is None
    assert entity.native_value is None


def test_get_schedule_data_swallows_iteration_error() -> None:
    """A non-iterable schedule_data is caught and yields None (no exception)."""
    device = _light_device([SCHEDULE])
    device["schedule_data"] = 123  # not iterable -> TypeError caught internally
    entity = FluidraLightScheduleEndTimeEntity(_coord(device), _api(), POOL_ID, LIGHT_ID, "1")
    _attach_ha(entity)
    assert entity._get_schedule_data() is None


def test_handle_coordinator_update_writes_state() -> None:
    """_handle_coordinator_update pushes a state write."""
    entity = FluidraLightScheduleStartTimeEntity(_coord(_light_device([SCHEDULE])), _api(), POOL_ID, LIGHT_ID, "1")
    _attach_ha(entity)
    entity._handle_coordinator_update()
    entity.async_write_ha_state.assert_called_once()


def test_format_time_to_cron_defaults_to_all_days() -> None:
    """_format_time_to_cron with days=None uses the full mobile-app week."""
    entity = FluidraLightScheduleStartTimeEntity(_coord(_light_device([SCHEDULE])), _api(), POOL_ID, LIGHT_ID, "1")
    _attach_ha(entity)
    assert entity._format_time_to_cron(time(6, 5)) == "5 6 * * 1,2,3,4,5,6,7"


def test_parse_cron_time_delegates_to_parser() -> None:
    """_parse_cron_time delegates to parse_schedule_time."""
    entity = FluidraLightScheduleEndTimeEntity(_coord(_light_device([SCHEDULE])), _api(), POOL_ID, LIGHT_ID, "1")
    _attach_ha(entity)
    assert entity._parse_cron_time("0 5 * * 1") == time(5, 0)
    assert entity._parse_cron_time(None) is None


def test_unique_ids_distinct_start_end() -> None:
    """Start/end entities expose distinct unique ids carrying the schedule id."""
    start = FluidraLightScheduleStartTimeEntity(_coord(_light_device([SCHEDULE])), _api(), POOL_ID, LIGHT_ID, "1")
    end = FluidraLightScheduleEndTimeEntity(_coord(_light_device([SCHEDULE])), _api(), POOL_ID, LIGHT_ID, "1")
    assert start._attr_unique_id == f"fluidra_{LIGHT_ID}_light_1_start_time"
    assert end._attr_unique_id == f"fluidra_{LIGHT_ID}_light_1_end_time"
    assert start._attr_unique_id != end._attr_unique_id
    assert start._attr_translation_key == "light_schedule_start"
    assert end._attr_translation_key == "light_schedule_end"
    assert start._attr_translation_placeholders == {"schedule_id": "1"}
