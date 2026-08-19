"""Tests for fluidra_api/_schedules.py (SchedulesMixin).

Focus: SUCCESS paths, the DM24049704 format conversion branches, and the
set_schedule/clear_schedule methods. The request layer is
mocked so no network access happens.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.fluidra_pool.api_resilience import FluidraAuthError, FluidraError
from custom_components.fluidra_pool.const import (
    COMPONENT_DM24049704_SCHEDULE,
    COMPONENT_SCHEDULE,
)
from custom_components.fluidra_pool.fluidra_api import FluidraPoolAPI
from custom_components.fluidra_pool.fluidra_api._schedules import SchedulesMixin


def _make_api(status: int = 200, raw_text: str = "") -> FluidraPoolAPI:
    """Build a FluidraPoolAPI with the request/auth layer fully mocked."""
    api = FluidraPoolAPI("a@b.c", "pw")
    api.access_token = "tok"
    api.ensure_valid_token = AsyncMock(return_value=True)
    api._build_auth_headers = MagicMock(return_value={"Authorization": "Bearer tok"})
    api._request = AsyncMock(return_value=(status, {}, raw_text))
    return api


# --- _convert_schedules_to_dm24049704_format ----------------------------


def test_convert_normal_enabled_schedule_encodes_time_and_structure() -> None:
    """A single enabled schedule produces dayPrograms + one program.

    Time encoding is hour*256 + minute, with cron field order "minute hour".
    "0 5 ..." -> minute=0, hour=5 -> 5*256+0 = 1280.
    "0 6 ..." -> minute=0, hour=6 -> 6*256+0 = 1536.
    """
    api = _make_api()
    converted = api._convert_schedules_to_dm24049704_format(
        [
            {
                "id": 0,
                "enabled": True,
                "startTime": "0 5 * * 1,2,3,4,5",
                "endTime": "0 6 * * 1,2,3,4,5",
                "startActions": {"operationName": "3"},
            }
        ]
    )

    # Five weekdays share one program; weekend has no program (0).
    day_programs = converted["dayPrograms"]
    assert day_programs["monday"] == 1
    assert day_programs["friday"] == 1
    assert day_programs["saturday"] == 0
    assert day_programs["sunday"] == 0

    assert len(converted["programs"]) == 1
    program = converted["programs"][0]
    assert program["id"] == 1
    assert program["slots"] == [{"id": 0, "start": 1280, "end": 1536, "mode": 3}]


def test_convert_disabled_schedule_is_skipped() -> None:
    """A schedule with enabled=False contributes nothing."""
    api = _make_api()
    converted = api._convert_schedules_to_dm24049704_format(
        [
            {
                "id": 0,
                "enabled": False,
                "startTime": "0 5 * * 1,2,3,4,5",
                "endTime": "0 6 * * 1,2,3,4,5",
                "startActions": {"operationName": "1"},
            }
        ]
    )

    assert converted["programs"] == []
    assert all(value == 0 for value in converted["dayPrograms"].values())


def test_convert_malformed_times_are_skipped_via_parse_failure() -> None:
    """Non-numeric cron minute/hour hits the ValueError branch and is skipped."""
    api = _make_api()
    converted = api._convert_schedules_to_dm24049704_format(
        [
            {
                "id": 0,
                "enabled": True,
                # "xx" cannot be int()-parsed -> ValueError -> continue.
                "startTime": "xx 5 * * 1",
                "endTime": "0 6 * * 1",
                "startActions": {"operationName": "1"},
            }
        ]
    )

    assert converted["programs"] == []
    assert all(value == 0 for value in converted["dayPrograms"].values())


def test_convert_short_cron_fields_produce_no_slots() -> None:
    """startTime/endTime with fewer than 2 fields are ignored (len guard)."""
    api = _make_api()
    converted = api._convert_schedules_to_dm24049704_format(
        [
            {
                "id": 0,
                "enabled": True,
                "startTime": "5",  # only one field
                "endTime": "6",
                "startActions": {"operationName": "1"},
            }
        ]
    )

    assert converted["programs"] == []


def test_convert_multiple_days_sharing_one_program_dedups() -> None:
    """Two schedules with identical slots/days collapse to a single program id."""
    api = _make_api()
    converted = api._convert_schedules_to_dm24049704_format(
        [
            {
                "id": 0,
                "enabled": True,
                "startTime": "0 5 * * 1,2",
                "endTime": "0 6 * * 1,2",
                "startActions": {"operationName": "1"},
            },
            {
                "id": 1,
                "enabled": True,
                "startTime": "0 5 * * 3,4",
                "endTime": "0 6 * * 3,4",
                "startActions": {"operationName": "1"},
            },
        ]
    )

    day_programs = converted["dayPrograms"]
    # All four days have identical slots -> same program id, dedup to one program.
    assert day_programs["monday"] == day_programs["tuesday"] == day_programs["wednesday"] == day_programs["thursday"]
    assert len(converted["programs"]) == 1


def test_convert_default_operation_when_action_missing() -> None:
    """Missing startActions defaults operationName to '1' -> mode 1."""
    api = _make_api()
    converted = api._convert_schedules_to_dm24049704_format(
        [
            {
                "id": 0,
                "enabled": True,
                "startTime": "0 5 * * 1",
                "endTime": "0 6 * * 1",
            }
        ]
    )

    assert converted["programs"][0]["slots"][0]["mode"] == 1


# --- set_schedule -------------------------------------------------------


async def test_set_schedule_success_returns_true_and_puts_payload() -> None:
    """A 200 response yields True; the PUT carries desiredValue=schedules."""
    api = _make_api(status=200)
    schedules: list[dict[str, Any]] = [{"id": 1, "enabled": True}]

    result = await api.set_schedule("DEV-1", schedules)

    assert result is True
    api._request.assert_awaited_once()
    args, kwargs = api._request.await_args
    assert args[0] == "PUT"
    url = args[1]
    assert url.startswith("https://api.fluidra-emea.com/generic/devices/DEV-1/components/")
    assert url.endswith(f"/components/{COMPONENT_SCHEDULE}")
    assert kwargs.get("params") == {"deviceType": "connected"}
    # groupId is filled in from id on the way out (schedule_slots_for_write).
    assert kwargs["json_data"] == {"desiredValue": [{"id": 1, "enabled": True, "groupId": 1}]}
    # content-type header is set on the auth headers.
    assert kwargs["headers"]["content-type"] == "application/json; charset=utf-8"


async def test_set_schedule_not_authenticated_raises_auth_error() -> None:
    """No access_token -> FluidraAuthError before any request."""
    api = _make_api()
    api.access_token = None

    with pytest.raises(FluidraAuthError):
        await api.set_schedule("DEV-1", [])

    api._request.assert_not_awaited()


async def test_set_schedule_token_refresh_failure_raises_auth_error() -> None:
    """ensure_valid_token returning False -> FluidraAuthError, no request."""
    api = _make_api()
    api.ensure_valid_token = AsyncMock(return_value=False)

    with pytest.raises(FluidraAuthError):
        await api.set_schedule("DEV-1", [])

    api._request.assert_not_awaited()


async def test_set_schedule_dm24049704_component_converts_payload() -> None:
    """The DM24049704 component id triggers format conversion of the payload."""
    api = _make_api(status=200)
    schedules = [
        {
            "id": 0,
            "enabled": True,
            "startTime": "0 5 * * 1",
            "endTime": "0 6 * * 1",
            "startActions": {"operationName": "2"},
        }
    ]

    result = await api.set_schedule("DEV-9", schedules, component_id=COMPONENT_DM24049704_SCHEDULE)

    assert result is True
    args, kwargs = api._request.await_args
    url = args[1]
    assert url.endswith(f"/components/{COMPONENT_DM24049704_SCHEDULE}")
    assert kwargs.get("params") == {"deviceType": "connected"}
    desired = kwargs["json_data"]["desiredValue"]
    # Converted (programs/dayPrograms) shape, not the raw cron list.
    assert isinstance(desired, dict)
    assert "dayPrograms" in desired
    assert "programs" in desired
    assert desired["programs"][0]["slots"][0] == {"id": 0, "start": 1280, "end": 1536, "mode": 2}


async def test_set_schedule_non_200_warns_and_returns_false(caplog) -> None:
    """A non-200 status surfaces the rejection body at WARNING and returns False (Issue #89)."""
    api = _make_api(status=409, raw_text="invalid scheduleUser")

    with caplog.at_level("WARNING"):
        result = await api.set_schedule("DEV-1", [])

    assert result is False
    api._request.assert_awaited_once()
    assert "invalid scheduleUser" in caplog.text
    assert "409" in caplog.text


async def test_set_schedule_request_error_is_caught_returns_false() -> None:
    """A FluidraError from the request layer is swallowed -> False."""
    api = _make_api()
    api._request = AsyncMock(side_effect=FluidraError("boom"))

    result = await api.set_schedule("DEV-1", [])

    assert result is False


# --- clear_schedule -----------------------------------------------------


async def test_clear_schedule_delegates_to_set_schedule_with_empty_list() -> None:
    """clear_schedule sends an empty schedule list via set_schedule."""
    api = _make_api(status=200)

    result = await api.clear_schedule("DEV-1")

    assert result is True
    args, kwargs = api._request.await_args
    assert args[0] == "PUT"
    assert kwargs["json_data"] == {"desiredValue": []}


async def test_clear_schedule_passes_component_id_through() -> None:
    """A custom component_id is forwarded to set_schedule (and the URL)."""
    api = _make_api(status=200)

    result = await api.clear_schedule("DEV-1", component_id=COMPONENT_DM24049704_SCHEDULE)

    assert result is True
    args, kwargs = api._request.await_args
    url = args[1]
    assert url.endswith(f"/components/{COMPONENT_DM24049704_SCHEDULE}")
    assert kwargs.get("params") == {"deviceType": "connected"}
    # Empty list converted to empty programs/dayPrograms structure.
    desired = kwargs["json_data"]["desiredValue"]
    assert desired["programs"] == []


# --- get_pool_schedulers (Issue #144) ------------------------------------


class _FakeAPI(SchedulesMixin):
    """Stub exposing only what SchedulesMixin touches for the read path."""

    def __init__(self) -> None:
        self.access_token: str | None = "fake-token"
        self._request = AsyncMock()
        self._build_auth_headers = MagicMock(return_value={"Authorization": "Bearer fake-token"})
        self.ensure_valid_token = AsyncMock(return_value=True)


async def test_get_pool_schedulers_returns_list_on_200() -> None:
    api = _FakeAPI()
    api._request.return_value = (200, [{"id": "s0", "name": "Filtration"}, "junk"], "[]")
    entries = await api.get_pool_schedulers("pool-1")
    assert entries == [{"id": "s0", "name": "Filtration"}]  # non-dict entries dropped
    assert api._request.await_args.args[1].endswith("/pools/pool-1/schedulers")


async def test_get_pool_schedulers_accepts_wrapped_payload() -> None:
    api = _FakeAPI()
    api._request.return_value = (200, {"schedulers": [{"id": "s1"}]}, "{}")
    assert await api.get_pool_schedulers("pool-1") == [{"id": "s1"}]


@pytest.mark.parametrize("payload", [None, "nope", {"unexpected": 1}])
async def test_get_pool_schedulers_returns_none_on_unusable_payload(payload: Any) -> None:
    api = _FakeAPI()
    api._request.return_value = (200, payload, "")
    assert await api.get_pool_schedulers("pool-1") is None


async def test_get_pool_schedulers_returns_none_on_error_status() -> None:
    api = _FakeAPI()
    api._request.return_value = (404, None, "")
    assert await api.get_pool_schedulers("pool-1") is None


# --- Issue #174: the PUT body must match the official app's, byte for byte ----


async def test_put_body_matches_the_captured_app_body_for_a_vs_pump_slot() -> None:
    """@Inervo's capture: PUT /components/21, chlorination on, 05:06-07:08, 2332 rpm.

    Fed to us as the device reports the slot back — ``reportedValue``, a runtime
    ``state``, no ``groupId`` — which is what every write path hands over after
    an edit. What leaves must be the app's shape regardless.
    """
    api = _make_api(status=200)
    reported_slot: dict[str, Any] = {
        "id": 1,
        "state": "IDLE",
        "enabled": True,
        "startTime": "06 05 * * 1",
        "endTime": "08 07 * * 1",
        "endActions": {},
        "startActions": {"componentActions": [{"id": 0, "reportedValue": 1}, {"id": 1, "reportedValue": 2332}]},
    }

    assert await api.set_schedule("NS25007212", [reported_slot], component_id=21) is True

    _args, kwargs = api._request.await_args
    assert kwargs["json_data"] == {
        "desiredValue": [
            {
                "id": 1,
                "groupId": 1,
                "enabled": True,
                "startTime": "06 05 * * 1",
                "endTime": "08 07 * * 1",
                "startActions": {
                    "operationName": "1",
                    "componentActions": [{"id": 0, "desiredValue": 1}, {"id": 1, "desiredValue": 2332}],
                },
            }
        ]
    }


async def test_put_body_matches_the_captured_app_body_for_a_colour_led_slot() -> None:
    """@Inervo's capture: PUT /components/23, Friday 10:00-11:00, colour index 3."""
    api = _make_api(status=200)
    reported_slot: dict[str, Any] = {
        "id": 1,
        "groupId": 1,
        "enabled": True,
        "startTime": "00 10 * * 5",
        "endTime": "00 11 * * 5",
        "startActions": {"componentActions": [{"id": 0, "reportedValue": 3}]},
    }

    assert await api.set_schedule("NS25007212", [reported_slot], component_id=23) is True

    _args, kwargs = api._request.await_args
    assert kwargs["json_data"]["desiredValue"][0]["startActions"] == {
        "operationName": "1",
        "componentActions": [{"id": 0, "desiredValue": 3}],
    }


async def test_a_simple_on_off_aux_slot_is_sent_unchanged() -> None:
    """@Inervo's capture: PUT /components/22 carries operationName and nothing else."""
    api = _make_api(status=200)
    slot: dict[str, Any] = {
        "id": 1,
        "groupId": 1,
        "enabled": True,
        "startTime": "09 08 * * 1,0",
        "endTime": "11 10 * * 1,0",
        "startActions": {"operationName": "1"},
    }

    assert await api.set_schedule("NS25007212", [slot], component_id=22) is True

    _args, kwargs = api._request.await_args
    assert kwargs["json_data"] == {"desiredValue": [slot]}


async def test_clearing_a_register_still_sends_an_empty_list() -> None:
    api = _make_api(status=200)
    assert await api.clear_schedule("NS25007212", component_id=23) is True
    _args, kwargs = api._request.await_args
    assert kwargs["json_data"] == {"desiredValue": []}
