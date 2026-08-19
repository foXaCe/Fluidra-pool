"""Regression tests for schedule corruption on write (Issue #175).

Enabling a schedule slot from Home Assistant rewrote every field of every slot
from scratch, which mangled schedules on devices whose payload doesn't match the
shape that code assumed — @Inervo saw times shift and modes reset on an eXO iQ,
with the damage mirrored back into the official Fluidra app.
"""

from __future__ import annotations

from typing import Any

from custom_components.fluidra_pool.switch.schedule import _with_enabled

# An eXO iQ slot as the API actually returns it: the mode lives in
# componentActions, and the cron days are already in the API's own numbering.
EXO_SLOT: dict[str, Any] = {
    "id": 1,
    "groupId": 1,
    "state": "IDLE",
    "enabled": True,
    "startTime": "00 08 * * 0,1,2,3,4,5,6",
    "endTime": "00 12 * * 0,1,2,3,4,5,6",
    "startActions": {"componentActions": [{"id": 0, "reportedValue": 1}]},
}

# The other shape in the wild, carrying the mode as a string.
LEGACY_SLOT: dict[str, Any] = {
    "id": 2,
    "groupId": 2,
    "enabled": False,
    "startTime": "30 09 * * 1,2,3",
    "endTime": "30 18 * * 1,2,3",
    "startActions": {"operationName": "2"},
}


def test_enabling_preserves_component_actions_mode() -> None:
    """The eXO mode must survive being enabled.

    Previously `startActions` was rebuilt as `operationName` with a "0" default,
    so the real mode was silently replaced by 0 on every toggle.
    """
    [updated] = _with_enabled([EXO_SLOT], 1, True)
    assert updated["startActions"] == {"componentActions": [{"id": 0, "reportedValue": 1}]}


def test_enabling_preserves_cron_days_verbatim() -> None:
    """Times and days must go back exactly as they were read.

    They used to be run through a 0→7 day conversion on every write, even though
    the values came straight from the API in its own format — shifting the days.
    """
    [updated] = _with_enabled([EXO_SLOT], 1, True)
    assert updated["startTime"] == "00 08 * * 0,1,2,3,4,5,6"
    assert updated["endTime"] == "00 12 * * 0,1,2,3,4,5,6"


def test_only_the_targeted_slot_changes() -> None:
    """Toggling one slot must leave the others byte-identical."""
    updated = _with_enabled([EXO_SLOT, LEGACY_SLOT], 2, True)
    expected_untouched = {k: v for k, v in EXO_SLOT.items() if k not in ("state", "endActions")}
    assert updated[0] == expected_untouched  # untouched except read-only fields stripped
    assert updated[1]["enabled"] is True
    assert updated[1]["startActions"] == {"operationName": "2"}  # mode preserved


def test_disabling_only_flips_the_flag() -> None:
    updated = _with_enabled([EXO_SLOT], 1, False)
    assert updated[0]["enabled"] is False
    assert {k: v for k, v in updated[0].items() if k not in ("enabled", "state", "endActions")} == {
        k: v for k, v in EXO_SLOT.items() if k not in ("enabled", "state", "endActions")
    }


def test_unknown_fields_are_carried_through() -> None:
    """Fields we don't know about must not be dropped — that's what broke this."""
    slot = {**EXO_SLOT, "someFutureField": {"nested": True}}
    [updated] = _with_enabled([slot], 1, True)
    assert updated["someFutureField"] == {"nested": True}


def test_state_and_end_actions_are_stripped_before_write() -> None:
    """Runtime fields the API reports but rejects on write must not be echoed.

    A capture of the official app's PUT body carries only id/groupId/startActions
    (Issue #89); `state`/`endActions` in the payload made the backend reject the
    whole list as "invalid scheduleUser". Since toggling echoes every slot back,
    they have to be dropped or the toggle bounces with an API error (Issue #174).
    """
    [updated] = _with_enabled([EXO_SLOT], 1, True)
    assert "state" not in updated
    assert "endActions" not in updated
    # startActions — the actual configuration — survives untouched.
    assert updated["startActions"] == {"componentActions": [{"id": 0, "reportedValue": 1}]}
    assert updated["startTime"] == "00 08 * * 0,1,2,3,4,5,6"


def test_group_id_defaults_to_id_when_absent() -> None:
    """The API rejects a payload without groupId, so mirror id when missing."""
    [updated] = _with_enabled([{"id": 3, "enabled": False}], 3, True)
    assert updated["groupId"] == 3


def test_source_schedules_are_not_mutated() -> None:
    """The coordinator's cached data must not be edited in place."""
    original = dict(EXO_SLOT)
    _with_enabled([EXO_SLOT], 1, False)
    assert EXO_SLOT == original


# --- the set_schedule service writes the shape the device uses ------------

from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402

from custom_components.fluidra_pool import (  # noqa: E402
    _device_uses_component_actions,
    _service_schedule_to_fluidra,
)


def _coordinator(schedule_data: list[dict[str, Any]] | None) -> Any:
    device = {"device_id": "D1"}
    if schedule_data is not None:
        device["schedule_data"] = schedule_data
    return SimpleNamespace(api=SimpleNamespace(get_device_by_id=lambda _d: device))


def test_component_actions_detected_from_the_device() -> None:
    assert _device_uses_component_actions(_coordinator([EXO_SLOT]), "D1") is True
    assert _device_uses_component_actions(_coordinator([LEGACY_SLOT]), "D1") is False


def test_detection_defaults_to_legacy_without_data() -> None:
    """No schedules to learn from → keep the long-standing shape."""
    assert _device_uses_component_actions(_coordinator(None), "D1") is False
    assert _device_uses_component_actions(_coordinator([]), "D1") is False
    assert _device_uses_component_actions(None, "D1") is False


def test_service_builds_component_actions_for_exo_devices() -> None:
    """Writing operationName to an eXO is what mangled the schedule (Issue #175)."""
    built = _service_schedule_to_fluidra(
        {"enabled": True, "start_time": "08:00", "end_time": "12:00", "mode": "1", "days": [1, 2, 3]},
        1,
        use_component_actions=True,
    )
    assert built["startActions"] == {"operationName": "1", "componentActions": [{"id": 0, "desiredValue": 1}]}
    assert built["startTime"] == "00 08 * * 1,2,3"


def test_service_keeps_operation_name_for_other_devices() -> None:
    built = _service_schedule_to_fluidra(
        {"enabled": True, "start_time": "08:00", "end_time": "12:00", "mode": "1", "days": [1]},
        1,
    )
    assert built["startActions"] == {"operationName": "1"}


def test_service_rejects_non_numeric_mode_for_component_actions() -> None:
    """componentActions carries an int; refuse rather than send something bogus."""
    from homeassistant.exceptions import ServiceValidationError

    with pytest.raises(ServiceValidationError):
        _service_schedule_to_fluidra(
            {"enabled": True, "start_time": "08:00", "end_time": "12:00", "mode": "turbo", "days": [1]},
            1,
            use_component_actions=True,
        )
