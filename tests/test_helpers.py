"""Tests for the pure helper functions in helpers.py."""

from __future__ import annotations

from datetime import time

import pytest

from custom_components.fluidra_pool.helpers import (
    get_schedule_data,
    parse_cron_time,
    resolve_component_rw,
)

# --- get_schedule_data ----------------------------------------------------


def test_get_schedule_data_matches_mixed_id_types() -> None:
    """int/str ids are compared as strings (the API mixes both)."""
    device = {"schedule_data": [{"id": 1, "enabled": True}, {"id": "2", "enabled": False}]}
    assert get_schedule_data(device, "1") == {"id": 1, "enabled": True}
    assert get_schedule_data(device, 2) == {"id": "2", "enabled": False}


def test_get_schedule_data_returns_none_when_absent() -> None:
    assert get_schedule_data({}, 1) is None
    assert get_schedule_data({"schedule_data": []}, 1) is None
    assert get_schedule_data({"schedule_data": [{"id": 9}]}, 1) is None


# --- resolve_component_rw ---------------------------------------------------


@pytest.mark.parametrize(
    ("cfg", "expected"),
    [
        (10, (10, 10)),
        ({"read": 164, "write": 4}, (164, 4)),
        ({"write": 4}, (4, 4)),
        ({"read": 164}, (164, 164)),
    ],
)
def test_resolve_component_rw(cfg, expected) -> None:
    """Plain ints map to themselves; dicts fall back to the other side."""
    assert resolve_component_rw(cfg) == expected


# --- parse_cron_time --------------------------------------------------------


@pytest.mark.parametrize(
    ("cron", "expected"),
    [
        ("30 08 * * 1,2,3", time(8, 30)),
        ("0 0 * * *", time(0, 0)),
        ("59 23 * * 7", time(23, 59)),
    ],
)
def test_parse_cron_time_valid(cron, expected) -> None:
    assert parse_cron_time(cron) == expected


@pytest.mark.parametrize("invalid", ["", "5", "aa bb * * *", "99 99 * * *", None, 42])
def test_parse_cron_time_invalid_returns_none(invalid) -> None:
    """Short, non-numeric, out-of-range or non-string input → None."""
    assert parse_cron_time(invalid) is None  # type: ignore[arg-type]
