"""Tests for the pure helper functions in helpers.py."""

from __future__ import annotations

from datetime import time

from homeassistant.helpers.device_registry import DeviceInfo
import pytest

from custom_components.fluidra_pool import helpers
from custom_components.fluidra_pool.const import DOMAIN
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


def test_pool_access_viewer_only_on_positive_match() -> None:
    """ "viewer" blocks writes, so it's only returned when the account's OWN contract
    says viewer — never inferred from "all contracts are viewer" (Issue #166)."""
    from custom_components.fluidra_pool.helpers import determine_pool_access

    # Account IS one of the viewer contracts → viewer (correct, still blocks).
    matched = {
        "owner": "someone-else",
        "contracts": [{"id": "user-1", "accessLevel": "viewer"}, {"id": "b", "accessLevel": "viewer"}],
    }
    assert determine_pool_access(matched, "user-1") == "viewer"

    # Account is NOT among the (all-viewer) contracts → must NOT be blocked. This is
    # the Issue #166 owner: its id ≠ pool.owner, and contracts[] lists only the
    # people it shared with. Inferring "viewer" here locked the real owner out.
    unmatched = {
        "owner": "someone-else",
        "contracts": [{"id": "a", "accessLevel": "viewer"}, {"id": "b", "accessLevel": "viewer"}],
    }
    assert determine_pool_access(unmatched, "user-1") == "unknown"


def test_pool_access_reads_own_contract_level() -> None:
    """When our contract is identifiable, its exact level is returned."""
    from custom_components.fluidra_pool.helpers import determine_pool_access

    pool = {
        "owner": "owner-x",
        "contracts": [{"id": "user-1", "accessLevel": "editor"}, {"id": "b", "accessLevel": "viewer"}],
    }
    assert determine_pool_access(pool, "user-1") == "editor"


def test_pool_access_unknown_when_unmatched() -> None:
    """An account not found among the contracts is "unknown" (non-blocking), never
    an inferred "viewer"/"shared" — it may well be the owner (Issue #166)."""
    from custom_components.fluidra_pool.helpers import determine_pool_access

    pool = {"owner": "owner-x", "contracts": [{"accessLevel": "viewer"}, {"accessLevel": "owner"}]}
    assert determine_pool_access(pool, "user-1") == "unknown"
    # user_id unresolved by the auth layer → also non-blocking, not a guessed viewer.
    all_viewer = {"owner": "owner-x", "contracts": [{"id": "a", "accessLevel": "viewer"}]}
    assert determine_pool_access(all_viewer, None) == "unknown"


def test_pool_access_unknown_without_contracts() -> None:
    from custom_components.fluidra_pool.helpers import determine_pool_access

    assert determine_pool_access({"owner": "x"}, None) == "unknown"
    assert determine_pool_access({}, "user-1") == "unknown"


# ---------------------------------------------------------------------------
# pool_link_kwargs — which key expresses the parent-pool link
# ---------------------------------------------------------------------------


def test_pool_link_kwargs_uses_the_registry_id_on_a_modern_core(monkeypatch: pytest.MonkeyPatch) -> None:
    """With via_device_id available and an id to hand, the id is what is written."""
    monkeypatch.setattr(helpers, "_VIA_DEVICE_ID_SUPPORTED", True)
    assert helpers.pool_link_kwargs("pool-1", "reg-42") == {"via_device_id": "reg-42"}


def test_pool_link_kwargs_falls_back_to_identifiers_without_an_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """No registry id means the link can only be expressed by identifiers."""
    monkeypatch.setattr(helpers, "_VIA_DEVICE_ID_SUPPORTED", True)
    assert helpers.pool_link_kwargs("pool-1", None) == {"via_device": (DOMAIN, "pool-1")}


def test_pool_link_kwargs_rejects_a_non_string_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-string id would make the registry raise; the deprecated key still links."""
    monkeypatch.setattr(helpers, "_VIA_DEVICE_ID_SUPPORTED", True)
    assert helpers.pool_link_kwargs("pool-1", object()) == {"via_device": (DOMAIN, "pool-1")}


def test_pool_link_kwargs_uses_identifiers_at_the_ha_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    """At the declared HA floor via_device_id does not exist, id or no id."""
    monkeypatch.setattr(helpers, "_VIA_DEVICE_ID_SUPPORTED", False)
    assert helpers.pool_link_kwargs("pool-1", "reg-42") == {"via_device": (DOMAIN, "pool-1")}


def test_link_to_pool_writes_the_link_onto_the_device_info(monkeypatch: pytest.MonkeyPatch) -> None:
    """link_to_pool returns the same mapping, with the link added."""
    monkeypatch.setattr(helpers, "_VIA_DEVICE_ID_SUPPORTED", True)
    info = DeviceInfo(identifiers={(DOMAIN, "dev-1")})
    assert helpers.link_to_pool(info, "pool-1", "reg-42") is info
    assert info["via_device_id"] == "reg-42"
