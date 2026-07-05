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


# --- determine_pool_access --------------------------------------------------


def test_pool_access_owner_by_user_id_match() -> None:
    """The account owns the pool when its consumer id matches pool['owner']."""
    from custom_components.fluidra_pool.helpers import determine_pool_access

    pool = {"owner": "user-1", "contracts": [{"id": "user-1", "accessLevel": "viewer"}]}
    # Owner match wins even if a contract says viewer.
    assert determine_pool_access(pool, "user-1") == "owner"


def test_pool_access_viewer_when_all_contracts_viewer_and_not_owner() -> None:
    from custom_components.fluidra_pool.helpers import determine_pool_access

    pool = {
        "owner": "someone-else",
        "contracts": [{"id": "a", "accessLevel": "viewer"}, {"id": "b", "accessLevel": "viewer"}],
    }
    assert determine_pool_access(pool, "user-1") == "viewer"


def test_pool_access_reads_own_contract_level() -> None:
    """When our contract is identifiable, its exact level is returned."""
    from custom_components.fluidra_pool.helpers import determine_pool_access

    pool = {
        "owner": "owner-x",
        "contracts": [{"id": "user-1", "accessLevel": "editor"}, {"id": "b", "accessLevel": "viewer"}],
    }
    assert determine_pool_access(pool, "user-1") == "editor"


def test_pool_access_shared_when_mixed_and_unmatched() -> None:
    from custom_components.fluidra_pool.helpers import determine_pool_access

    pool = {"owner": "owner-x", "contracts": [{"accessLevel": "viewer"}, {"accessLevel": "owner"}]}
    assert determine_pool_access(pool, "user-1") == "shared"


def test_pool_access_unknown_without_contracts() -> None:
    from custom_components.fluidra_pool.helpers import determine_pool_access

    assert determine_pool_access({"owner": "x"}, None) == "unknown"
    assert determine_pool_access({}, "user-1") == "unknown"
