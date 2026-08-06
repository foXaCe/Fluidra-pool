"""hvac_action from the live compressor state (c75) on Z250iQ/Z260iQ — Issue #139.

@Kal42 confirmed 0=idle, 16=heating, 24=cooling by driving the unit through
Boost/Silent and Heating/Cooling. A transient 8 appeared for a few seconds after
a mode change and was never explained, so it stays unmapped.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from homeassistant.components.climate import HVACAction
import pytest

from custom_components.fluidra_pool.climate_behaviors import Z260iqBehavior
from custom_components.fluidra_pool.device_registry import DEVICE_CONFIGS


def _infer_heat_cool() -> HVACAction:
    """Stand-in for the temperature-deadband fallback."""
    return HVACAction.HEATING


@pytest.mark.parametrize(
    ("compressor_state", "expected"),
    [(0, HVACAction.IDLE), (16, HVACAction.HEATING), (24, HVACAction.COOLING)],
)
def test_confirmed_states_win_over_the_mode(compressor_state: int, expected: HVACAction) -> None:
    """c14 says what was asked for; c75 says what the unit is doing."""
    device = {
        "heat_pump_reported": True,
        "z260iq_mode_value": 0,  # Smart Heat — would otherwise force HEATING.
        "compressor_state": compressor_state,
    }
    assert Z260iqBehavior().hvac_action(device, _infer_heat_cool) == expected


def test_idle_at_setpoint_is_the_original_complaint() -> None:
    """Issue #139: the unit stops at temperature but reported HEATING."""
    device = {"heat_pump_reported": True, "z260iq_mode_value": 0, "compressor_state": 0}
    assert Z260iqBehavior().hvac_action(device, _infer_heat_cool) == HVACAction.IDLE


def test_cooling_is_not_reported_as_heating() -> None:
    device = {"heat_pump_reported": True, "z260iq_mode_value": 1, "compressor_state": 24}
    assert Z260iqBehavior().hvac_action(device, _infer_heat_cool) == HVACAction.COOLING


@pytest.mark.parametrize("unknown", [8, 1, 32, 255])
def test_unknown_states_fall_back_to_the_mode(unknown: int) -> None:
    """Only confirmed values are trusted; the rest keep the previous behaviour."""
    device = {"heat_pump_reported": True, "z260iq_mode_value": 1, "compressor_state": unknown}
    assert Z260iqBehavior().hvac_action(device, _infer_heat_cool) == HVACAction.COOLING


def test_missing_register_keeps_the_mode_derived_action() -> None:
    """Units that never report c75 must behave exactly as before."""
    device: dict[str, Any] = {"heat_pump_reported": True, "z260iq_mode_value": 0}
    assert Z260iqBehavior().hvac_action(device, _infer_heat_cool) == HVACAction.HEATING


def test_off_and_no_flow_still_win_over_the_compressor_state() -> None:
    """A powered-off unit is OFF even if a stale c75 says heating."""
    off = {"heat_pump_reported": False, "compressor_state": 16}
    assert Z260iqBehavior().hvac_action(off, _infer_heat_cool) == HVACAction.OFF

    no_flow = {"heat_pump_reported": True, "no_flow_alarm": True, "compressor_state": 16}
    assert Z260iqBehavior().hvac_action(no_flow, _infer_heat_cool) == HVACAction.IDLE


@pytest.mark.parametrize("profile", ["z250iq_heat_pump", "z260iq_heat_pump"])
def test_lf_profiles_scan_c75(profile: str) -> None:
    """A mapped register that is never polled would leave the action unchanged."""
    assert 75 in DEVICE_CONFIGS[profile].features["specific_components"]


def test_coordinator_decodes_c75_only_for_the_z260iq_family() -> None:
    from custom_components.fluidra_pool.coordinator import FluidraDataUpdateCoordinator

    z250 = {
        "device_id": "LF25012345",
        "name": "Z250iQ",
        "family": "heat pump",
        "type": "heat_pump",
        "model": "",
        "components": {},
    }
    FluidraDataUpdateCoordinator._process_component_state(MagicMock(), z250, "pool-1", 75, {"reportedValue": 16})
    assert z250["compressor_state"] == 16

    other = {
        "device_id": "DM24008702.nn_1",
        "name": "Chlorinator",
        "family": "Chlorinators",
        "type": "chlorinator",
        "model": "Chlorinator",
        "components": {},
    }
    FluidraDataUpdateCoordinator._process_component_state(MagicMock(), other, "pool-1", 75, {"reportedValue": 16})
    assert "compressor_state" not in other
