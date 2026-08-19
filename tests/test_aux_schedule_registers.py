"""The eXO iQ aux outputs have two schedule registers each (Issue #174).

@Inervo's capture of the official app shows Aux 1 writing to c22 when it drives
a plain on/off light and to c23 when it drives a colour LED, with Aux 2 using
c24/c25 the same way. Together with the pump registers c19-c21 that accounts
for all seven schedulers the unit declares.
"""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.fluidra_pool.helpers import describe_led_colour, resolve_aux_schedule_component

PLAIN_SLOT = {
    "id": 1,
    "groupId": 1,
    "enabled": True,
    "startTime": "09 08 * * 1,0",
    "endTime": "11 10 * * 1,0",
    "startActions": {"operationName": "1"},
}
COLOUR_SLOT = {
    "id": 1,
    "groupId": 1,
    "enabled": True,
    "startTime": "00 10 * * 5",
    "endTime": "00 11 * * 5",
    "startActions": {"operationName": "1", "componentActions": [{"id": 0, "reportedValue": 3}]},
}


def _exo(**components: Any) -> dict[str, Any]:
    return {
        "device_id": "NS25007212",
        "name": "Zodiac EXO iQ 35",
        "family": "Chlorinators",
        "type": "connected",
        "components": {str(k): {"reportedValue": v} for k, v in components.items()},
    }


@pytest.mark.parametrize(
    ("components", "aux", "expected"),
    [
        # Slots on the plain register only → that one.
        ({"22": [PLAIN_SLOT], "23": None}, "1", 22),
        ({"24": [PLAIN_SLOT], "25": None}, "2", 24),
        # Slots on the colour register only → the aux drives a colour LED.
        ({"22": None, "23": [COLOUR_SLOT]}, "1", 23),
        ({"24": [], "25": [COLOUR_SLOT]}, "2", 25),
        # Nothing configured anywhere → keep the plain register.
        ({"22": None, "23": None}, "1", 22),
        ({}, "2", 24),
    ],
)
def test_live_aux_register_follows_the_one_holding_slots(components: dict[str, Any], aux: str, expected: int) -> None:
    assert resolve_aux_schedule_component(_exo(**components), aux) == expected


def test_both_registers_populated_keeps_the_plain_one_and_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Ambiguous is not guessable; say so instead of picking silently."""
    device = _exo(**{"22": [PLAIN_SLOT], "23": [COLOUR_SLOT], "90": "Lighting"})

    with caplog.at_level("WARNING"):
        assert resolve_aux_schedule_component(device, "1") == 22

    assert "c22" in caplog.text
    assert "c23" in caplog.text
    assert "Lighting" in caplog.text


def test_profiles_without_a_colour_register_are_untouched() -> None:
    """A device declaring only the plain map keeps its single register."""
    device = {
        "device_id": "DM24049704",
        "family": "Chlorinators",
        "name": "Chlorinator",
        "components": {},
    }
    assert resolve_aux_schedule_component(device, "1", default=22) == 22


@pytest.mark.parametrize(
    ("index", "expected"),
    [
        # 0 and 1 only exist on the LumiPlus table (it starts at 0).
        (0, {"lumiplus": "white"}),
        (1, {"lumiplus": "red"}),
        # The overlap: the tables disagree, so both names are offered.
        (3, {"lumiplus": "green", "zodiac_nl": "sky_blue"}),
        (7, {"lumiplus": "sequence_1", "zodiac_nl": "emerald_green"}),
        # 14 and 15 only exist on the Zodiac NL table (it runs to 15).
        (14, {"zodiac_nl": "fat_tuesday"}),
        (15, {"zodiac_nl": "disco_tech"}),
        # Outside both tables, and non-numeric.
        (16, None),
        (-1, None),
        (None, None),
        ("green", None),
    ],
)
def test_colour_index_never_resolves_to_a_single_guessed_name(index: Any, expected: Any) -> None:
    """LumiPlus runs 0-13 and Zodiac NL runs 2-15; one table would be wrong."""
    assert describe_led_colour(index) == expected


# --- coordinator wiring ------------------------------------------------------


def _apply_aux(device: dict[str, Any]) -> dict[str, Any]:
    from unittest.mock import MagicMock

    from custom_components.fluidra_pool.coordinator import FluidraDataUpdateCoordinator

    FluidraDataUpdateCoordinator._apply_resolved_aux_schedules(MagicMock(), device)
    return device


def test_coordinator_surfaces_the_colour_register_for_a_led_aux() -> None:
    """Aux 1 on a LumiPlus keeps its slots on c23, which was never scanned."""
    device = _apply_aux(_exo(**{"22": None, "23": [COLOUR_SLOT], "24": [PLAIN_SLOT], "25": None}))

    assert device["aux_schedule_data"]["1"] == [COLOUR_SLOT]
    assert device["aux_schedule_data"]["2"] == [PLAIN_SLOT]
    assert device["aux_schedule_components_resolved"] == {"1": 23, "2": 24}


def test_coordinator_leaves_an_unconfigured_aux_empty() -> None:
    """No slots on either register is "no schedule", not a fallback hunt."""
    device = _apply_aux(_exo(**{"22": None, "23": None}))

    assert device["aux_schedule_data"]["1"] == []
    assert device["aux_schedule_components_resolved"]["1"] == 22


def test_devices_with_no_aux_registers_are_untouched() -> None:
    device = {"device_id": "DM24008702.nn_1", "family": "Chlorinators", "name": "Chlorinator", "components": {}}
    _apply_aux(device)
    assert "aux_schedule_data" not in device


def test_exo_profile_declares_and_scans_both_registers_of_each_pair() -> None:
    from custom_components.fluidra_pool.device_registry import DEVICE_CONFIGS

    features = DEVICE_CONFIGS["ns25_exo_chlorinator"].features
    assert features["aux_schedule_components"] == {"1": 22, "2": 24}
    assert features["aux_colour_schedule_components"] == {"1": 23, "2": 25}

    scanned = set(features["specific_components"])
    # A register that is mapped but never polled would resolve to nothing.
    for register in (22, 23, 24, 25):
        assert register in scanned, register

    # All seven schedulers the device declares (c19-c25) are accounted for.
    mapped = {features["schedule_component_map"][key] for key in ("none", "simple", "vs")}
    mapped |= {int(v) for v in features["aux_schedule_components"].values()}
    mapped |= {int(v) for v in features["aux_colour_schedule_components"].values()}
    assert mapped == {19, 20, 21, 22, 23, 24, 25}
