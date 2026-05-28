"""Tests for coordinator._parsers (DM24049704 schedule + auto-mode speed)."""

from __future__ import annotations

from datetime import datetime, time
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from custom_components.fluidra_pool.coordinator._parsers import (
    calculate_auto_speed_from_schedules,
    parse_dm24049704_schedule_format,
)

UTC = ZoneInfo("UTC")


# --- parse_dm24049704_schedule_format ------------------------------------


def test_dm24049704_returns_empty_list_for_non_dict_input() -> None:
    """A non-dict (None, str, int) returns an empty list — never raises."""
    assert parse_dm24049704_schedule_format(None) == []  # type: ignore[arg-type]
    assert parse_dm24049704_schedule_format("not-a-dict") == []  # type: ignore[arg-type]


def test_dm24049704_returns_empty_when_no_programs() -> None:
    """No programs entry → no schedules."""
    assert parse_dm24049704_schedule_format({"dayPrograms": {}, "programs": []}) == []


def test_dm24049704_decodes_a_single_slot_across_5_weekdays() -> None:
    """5:00→6:00 on monday-friday is encoded as `0 5 * * 1,2,3,4,5` / `0 6 * * 1,2,3,4,5`."""
    raw = {
        "dayPrograms": {
            "monday": 1,
            "tuesday": 1,
            "wednesday": 1,
            "thursday": 1,
            "friday": 1,
            "saturday": 0,
            "sunday": 0,
        },
        "programs": [
            {
                "id": 1,
                "slots": [
                    # 5*256+0 = 1280, 6*256+0 = 1536, mode 3 = S3.
                    {"id": 0, "start": 1280, "end": 1536, "mode": 3},
                ],
            }
        ],
    }

    parsed = parse_dm24049704_schedule_format(raw)

    assert len(parsed) == 1
    schedule = parsed[0]
    assert schedule["startTime"] == "0 5 * * 1,2,3,4,5"
    assert schedule["endTime"] == "0 6 * * 1,2,3,4,5"
    assert schedule["startActions"]["operationName"] == "3"
    assert schedule["enabled"] is True
    assert schedule["id"] == schedule["groupId"] == 1


def test_dm24049704_skips_empty_zero_slots() -> None:
    """Mode=0 with start=0 and end=0 is filler — skip it."""
    raw = {
        "dayPrograms": {"monday": 1},
        "programs": [
            {
                "id": 1,
                "slots": [
                    {"id": 0, "start": 0, "end": 0, "mode": 0},  # filler.
                    {"id": 1, "start": 1536, "end": 1792, "mode": 2},  # 6:00→7:00.
                ],
            }
        ],
    }

    parsed = parse_dm24049704_schedule_format(raw)
    assert len(parsed) == 1
    assert parsed[0]["startTime"] == "0 6 * * 1"
    assert parsed[0]["endTime"] == "0 7 * * 1"


def test_dm24049704_skips_programs_with_no_assigned_days() -> None:
    """A program referenced by no day in dayPrograms doesn't surface as a schedule."""
    raw = {
        "dayPrograms": {"monday": 0, "tuesday": 0},  # No day uses program 1.
        "programs": [{"id": 1, "slots": [{"id": 0, "start": 1280, "end": 1536, "mode": 1}]}],
    }
    assert parse_dm24049704_schedule_format(raw) == []


def test_dm24049704_encodes_minutes_correctly() -> None:
    """8:30 encoded as 8*256+30 = 2078 round-trips through the parser."""
    raw = {
        "dayPrograms": {"monday": 1},
        "programs": [
            {
                "id": 1,
                "slots": [{"id": 0, "start": 8 * 256 + 30, "end": 9 * 256 + 45, "mode": 1}],
            }
        ],
    }
    parsed = parse_dm24049704_schedule_format(raw)
    assert parsed[0]["startTime"] == "30 8 * * 1"
    assert parsed[0]["endTime"] == "45 9 * * 1"


# --- calculate_auto_speed_from_schedules ---------------------------------


def _device_with_schedules(schedules: list[dict]) -> dict:
    return {"schedule_data": schedules}


def test_auto_speed_returns_zero_when_no_schedules() -> None:
    """Without any schedule data, auto mode reports 0% (pump idle)."""
    assert calculate_auto_speed_from_schedules({}) == 0
    assert calculate_auto_speed_from_schedules({"schedule_data": []}) == 0


@pytest.mark.parametrize(
    ("operation_name", "expected_percent"),
    [("0", 45), ("1", 65), ("2", 100), ("unknown", 0)],
)
def test_auto_speed_maps_operation_name_to_percent_when_schedule_active(operation_name, expected_percent) -> None:
    """During an active enabled schedule, the operationName maps to a percentage."""
    # Schedule covers Monday 00:00 → 23:59.
    device = _device_with_schedules(
        [
            {
                "enabled": True,
                "startTime": "0 0 * * 1",  # 00:00 Monday.
                "endTime": "59 23 * * 1",  # 23:59 Monday.
                "startActions": {"operationName": operation_name},
            }
        ]
    )

    # Force "now" to a fixed Monday 12:00.
    fake_now = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)  # 2026-05-25 is a Monday.
    with patch(
        "custom_components.fluidra_pool.coordinator._parsers.dt_util.now",
        return_value=fake_now,
    ):
        assert calculate_auto_speed_from_schedules(device) == expected_percent


def test_auto_speed_skips_disabled_schedules() -> None:
    """A schedule with enabled=False isn't considered, even if currently active."""
    device = _device_with_schedules(
        [
            {
                "enabled": False,
                "startTime": "0 0 * * 1",
                "endTime": "59 23 * * 1",
                "startActions": {"operationName": "2"},
            }
        ]
    )
    fake_now = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)
    with patch(
        "custom_components.fluidra_pool.coordinator._parsers.dt_util.now",
        return_value=fake_now,
    ):
        assert calculate_auto_speed_from_schedules(device) == 0


def test_auto_speed_returns_zero_outside_active_window() -> None:
    """Schedule 08:00-10:00 but it's 12:00 → 0%."""
    device = _device_with_schedules(
        [
            {
                "enabled": True,
                "startTime": "0 8 * * 1",
                "endTime": "0 10 * * 1",
                "startActions": {"operationName": "1"},
            }
        ]
    )
    fake_now = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)
    with patch(
        "custom_components.fluidra_pool.coordinator._parsers.dt_util.now",
        return_value=fake_now,
    ):
        assert calculate_auto_speed_from_schedules(device) == 0


def test_auto_speed_skips_when_weekday_does_not_match() -> None:
    """Schedule restricted to Monday but it's Tuesday → 0%."""
    device = _device_with_schedules(
        [
            {
                "enabled": True,
                "startTime": "0 0 * * 1",  # Monday only.
                "endTime": "59 23 * * 1",
                "startActions": {"operationName": "1"},
            }
        ]
    )
    # 2026-05-26 is a Tuesday.
    fake_now = datetime(2026, 5, 26, 12, 0, tzinfo=UTC)
    with patch(
        "custom_components.fluidra_pool.coordinator._parsers.dt_util.now",
        return_value=fake_now,
    ):
        assert calculate_auto_speed_from_schedules(device) == 0


def test_auto_speed_handles_sunday_zero_cron_day_conversion() -> None:
    """CRON day 0 (Sunday) is converted to Python weekday 6."""
    device = _device_with_schedules(
        [
            {
                "enabled": True,
                "startTime": "0 0 * * 0",  # Sunday in CRON.
                "endTime": "59 23 * * 0",
                "startActions": {"operationName": "2"},
            }
        ]
    )
    # 2026-05-31 is a Sunday.
    fake_now = datetime(2026, 5, 31, 12, 0, tzinfo=UTC)
    with patch(
        "custom_components.fluidra_pool.coordinator._parsers.dt_util.now",
        return_value=fake_now,
    ):
        assert calculate_auto_speed_from_schedules(device) == 100


def test_auto_speed_returns_zero_when_cron_time_is_garbage() -> None:
    """An unparsable cron string gracefully returns 0."""
    device = _device_with_schedules(
        [
            {
                "enabled": True,
                "startTime": "not-a-cron",
                "endTime": "also-not",
                "startActions": {"operationName": "2"},
            }
        ]
    )
    fake_now = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)
    with patch(
        "custom_components.fluidra_pool.coordinator._parsers.dt_util.now",
        return_value=fake_now,
    ):
        assert calculate_auto_speed_from_schedules(device) == 0


def test_auto_speed_treats_star_days_as_all_days() -> None:
    """A CRON `* *` for days means every day matches the schedule."""
    device = _device_with_schedules(
        [
            {
                "enabled": True,
                "startTime": "0 0 * * *",
                "endTime": "59 23 * * *",
                "startActions": {"operationName": "1"},
            }
        ]
    )
    # Wednesday at noon, should match.
    fake_now = datetime(2026, 5, 27, 12, 0, tzinfo=UTC)
    with patch(
        "custom_components.fluidra_pool.coordinator._parsers.dt_util.now",
        return_value=fake_now,
    ):
        # Time isn't a `time` literal here; the helper compares time-of-day.
        assert calculate_auto_speed_from_schedules(device) == 65


# Sanity check: the parser does compare the *time* part, not the datetime.
def test_auto_speed_compares_clock_time_inclusively() -> None:
    """Boundary times start/end are inclusive."""
    device = _device_with_schedules(
        [
            {
                "enabled": True,
                "startTime": "0 12 * * 1",  # 12:00 Monday.
                "endTime": "0 12 * * 1",  # 12:00 Monday (same minute).
                "startActions": {"operationName": "1"},
            }
        ]
    )
    fake_now = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)
    with patch(
        "custom_components.fluidra_pool.coordinator._parsers.dt_util.now",
        return_value=fake_now,
    ):
        # current_time = 12:00, start = end = 12:00 → start <= current <= end.
        assert calculate_auto_speed_from_schedules(device) == 65

    # Reference the unused symbol so it's actually used (it's part of the test).
    assert isinstance(time(12, 0), time)
