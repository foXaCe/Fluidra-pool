"""Z650 activity must describe observed compressor operation, not its setpoint."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import HVACAction
import pytest

from custom_components.fluidra_pool.sensor.device import FluidraHeatPumpActivitySensor

from .test_climate_full import DEVICE_ID, POOL_ID, _api, _coord, _z650_device


@pytest.mark.parametrize(
    ("power", "preset", "modulation", "no_flow", "expected"),
    [
        (0, 0, 80, False, HVACAction.OFF),
        (0, 2, 80, True, HVACAction.OFF),
        (1, 0, 0, False, HVACAction.IDLE),
        (1, 2, 0, False, HVACAction.IDLE),
        (1, 1, 35, False, HVACAction.HEATING),
        (1, 2, 35, False, HVACAction.HEATING),
        (1, 3, 35, False, HVACAction.HEATING),
        (1, 0, 35, False, None),
        (1, 99, 35, False, None),
        (1, 2, None, False, None),
        (1, 2, "50", False, None),
        (1, 2, float("nan"), False, None),
        (1, 2, float("inf"), False, None),
        (1, 2, -1, False, None),
        (1, 2, 101, False, None),
        (1, 2, True, False, None),
        (1, 0, None, True, HVACAction.IDLE),
        (None, 2, 35, False, None),
    ],
)
def test_activity_uses_power_modulation_and_confirmed_direction(
    power: Any, preset: Any, modulation: Any, no_flow: bool, expected: HVACAction | None
) -> None:
    """The separate Activity entity follows the same evidence as the climate card."""
    device = _z650_device(
        preset,
        heat_pump_reported=power,
        compressor_modulation=modulation,
        no_flow_alarm=no_flow,
        pump_power=7 if modulation == 0 else 500,
    )
    sensor = FluidraHeatPumpActivitySensor(_coord([device]), _api(), POOL_ID, DEVICE_ID)
    assert sensor.native_value == (expected.value if expected is not None else None)


@pytest.mark.parametrize(("water", "target"), [(20, 32), (35, 25), (30, 30)])
def test_smart_plus_activity_stays_unknown_regardless_of_temperature(water: float, target: float) -> None:
    device = _z650_device(0, water_temperature=water, target_temperature=target)
    sensor = FluidraHeatPumpActivitySensor(_coord([device]), _api(), POOL_ID, DEVICE_ID)
    assert sensor.native_value is None


def test_activity_changes_from_idle_to_unknown_to_off_with_reported_state() -> None:
    """A new poll clears the old action; Smart+ does not retain a guessed direction."""
    device = _z650_device(2, compressor_modulation=0, pump_power=7)
    coordinator = _coord([device])
    sensor = FluidraHeatPumpActivitySensor(coordinator, _api(), POOL_ID, DEVICE_ID)
    assert sensor.native_value == "idle"

    device["compressor_modulation"] = 40
    assert sensor.native_value == "heating"

    device["z260iq_mode_value"] = 0
    device["components"]["14"]["reportedValue"] = 0
    assert sensor.native_value is None

    device["heat_pump_reported"] = 0
    assert sensor.native_value == "off"
