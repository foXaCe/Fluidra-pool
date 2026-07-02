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
    assert kwargs["json_data"] == {"desiredValue": schedules}
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
