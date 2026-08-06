"""The eXO iQ moves its schedules by pump type (Issue #174).

@Inervo's six captures pin the discriminator: with no pump c82/c83 are both
false, a simple pump sets c82, a variable-speed pump sets c83. Fluidra honours
only the register matching the current configuration, and stale schedules
survive a configuration change — so "whichever register has entries" cannot
identify the live one.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from custom_components.fluidra_pool.device_registry import DEVICE_CONFIGS, DeviceIdentifier
from custom_components.fluidra_pool.helpers import resolve_schedule_component

SLOT = {"id": 1, "groupId": 1, "state": "IDLE", "enabled": True, "startTime": "07 10 * * 1,2"}


def _exo(**components: Any) -> dict[str, Any]:
    return {
        "device_id": "NS25007212",
        "name": "Zodiac EXO iQ 35",
        "family": "Chlorinators",
        "type": "connected",
        "components": {str(k): {"reportedValue": v} for k, v in components.items()},
    }


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        ({"82": False, "83": False}, 19),  # No pump → chlorination-only schedules.
        ({"82": True, "83": False}, 20),  # Simple pump.
        ({"82": False, "83": True}, 21),  # Variable-speed pump.
    ],
)
def test_register_follows_the_configured_pump_type(flags: dict[str, Any], expected: int) -> None:
    assert resolve_schedule_component(_exo(**flags)) == expected


def test_stale_entries_do_not_decide_the_register() -> None:
    """The whole reason for the flags: both registers can hold data at once."""
    device = _exo(**{"82": True, "83": False, "19": [SLOT], "20": [], "21": [SLOT]})
    # c19 and c21 both have content, but a simple pump is configured.
    assert resolve_schedule_component(device) == 20


def test_devices_without_the_map_keep_their_fixed_register() -> None:
    """Every other profile must be untouched by this."""
    device = {
        "device_id": "DM24008702.nn_1",
        "name": "Chlorinator",
        "family": "Chlorinators",
        "type": "chlorinator",
        "model": "Chlorinator",
        "components": {"172": {"reportedValue": 722}},
    }
    expected = DeviceIdentifier.get_feature(device, "schedule_component", 20)
    assert resolve_schedule_component(device) == expected


def test_unknown_device_falls_back_to_the_default() -> None:
    assert resolve_schedule_component({}, 258) == 258


# --- coordinator wiring ------------------------------------------------------


def _apply(device: dict[str, Any]) -> dict[str, Any]:
    from custom_components.fluidra_pool.coordinator import FluidraDataUpdateCoordinator

    coordinator = MagicMock()
    coordinator._track_schedule_count = MagicMock()
    FluidraDataUpdateCoordinator._apply_resolved_schedule(coordinator, device, "pool-1", device["device_id"])
    return device


def test_coordinator_reads_the_live_register_not_the_stale_one() -> None:
    """A VS pump must surface c21's schedules even though c19 still holds old ones."""
    stale = {"id": 9, "groupId": 9, "state": "IDLE", "enabled": True, "startTime": "00 08 * * 0"}
    device = _apply(_exo(**{"82": False, "83": True, "19": [stale], "21": [SLOT]}))

    assert device["schedule_data"] == [SLOT]
    assert device["schedule_component_resolved"] == 21


def test_coordinator_reports_no_schedules_when_the_live_register_is_empty() -> None:
    """An empty live register means no schedules — not "fall back to another one"."""
    stale = {"id": 9, "groupId": 9, "state": "IDLE", "enabled": True, "startTime": "00 08 * * 0"}
    device = _apply(_exo(**{"82": True, "83": False, "19": [stale], "20": []}))

    assert device["schedule_data"] == []
    assert device["schedule_component_resolved"] == 20


def test_exo_profile_declares_the_map_and_scans_every_register() -> None:
    config = DEVICE_CONFIGS["ns25_exo_chlorinator"]
    mapping = config.features["schedule_component_map"]
    assert mapping == {"none": 19, "simple": 20, "vs": 21, "simple_flag": 82, "vs_flag": 83}
    scanned = set(config.features["specific_components"])
    # A register that is mapped but never polled would resolve to nothing.
    for register in (19, 20, 21, 82, 83):
        assert register in scanned, register


def test_exo_exposes_six_schedule_slots() -> None:
    """Confirmed in the app: six slots per output (Issue #174)."""
    assert DEVICE_CONFIGS["ns25_exo_chlorinator"].features["schedule_count"] == 6
