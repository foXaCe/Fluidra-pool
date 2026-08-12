"""Two regressions reported against v2.78.x.

Issue #193 (@FoxP): the last-known salinity fallback from #187 kept showing a
reading while the pump was off, i.e. while the cell held no flowing water.

Issue #174 (@Inervo): writing a schedule to a variable-speed pump produced a
slot with no target RPM, after which the Fluidra app could no longer load the
device at all until the pump type was changed on the unit itself.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from homeassistant.exceptions import ServiceValidationError
import pytest

from custom_components.fluidra_pool.sensor.chlorinator import FluidraChlorinatorSensor

POOL_ID = "pool-1"
DEVICE_ID = "LC26033146.nn_1"

FLOW_ALARM = {
    "errorCode": "FLOW",
    "type": "error",
    "value": True,
    "default": {"title": "No flow", "text": "Check valves position."},
}


def _coord(device: dict) -> Any:
    coordinator = MagicMock()
    coordinator.data = {POOL_ID: {"id": POOL_ID, "name": "Pool", "devices": [device]}}
    coordinator.last_update_success = True
    return coordinator


def _device(salinity: Any, alarms: list | None = None) -> dict[str, Any]:
    device: dict[str, Any] = {
        "device_id": DEVICE_ID,
        "name": "Chlorinator",
        "family": "Chlorinators",
        "type": "chlorinator",
        "model": "Chlorinator",
        "online": True,
        "components": {"174": {"reportedValue": salinity}},
    }
    if alarms is not None:
        device["alarms"] = alarms
    return device


def _sensor(device: dict) -> FluidraChlorinatorSensor:
    return FluidraChlorinatorSensor(_coord(device), MagicMock(), POOL_ID, DEVICE_ID, "salinity", 174)


def _poll(sensor: FluidraChlorinatorSensor, salinity: Any, alarms: list | None = None) -> None:
    """Apply one coordinator refresh, driving the last-known bookkeeping.

    The snapshot happens in _handle_coordinator_update, once per refresh —
    calling _update_last_known_salinity directly is what HA's update does,
    without needing a real hass to write state.
    """
    device = sensor.coordinator.data[POOL_ID]["devices"][0]
    device["components"]["174"]["reportedValue"] = salinity
    if alarms is not None:
        device["alarms"] = alarms
    sensor._update_last_known_salinity()


def test_last_known_is_held_while_production_is_low() -> None:
    """The #187 behaviour, unchanged: no flow alarm, so the water is still there."""
    sensor = _sensor(_device(498))
    _poll(sensor, 498)
    assert sensor.native_value == 4.98

    _poll(sensor, 0)
    assert sensor.native_value == 4.98


def test_last_known_is_dropped_while_no_flow_is_reported() -> None:
    """Issue #193: the held reading describes water no longer passing the probe."""
    sensor = _sensor(_device(498))
    _poll(sensor, 498)
    assert sensor.native_value == 4.98

    _poll(sensor, 0, alarms=[FLOW_ALARM])
    assert sensor.native_value is None


def test_a_real_reading_wins_over_a_flow_alarm() -> None:
    """A live non-zero value is reported whatever the alarms say."""
    sensor = _sensor(_device(512, alarms=[FLOW_ALARM]))
    assert sensor.native_value == 5.12


def test_inactive_or_unrelated_alarms_do_not_suppress_the_fallback() -> None:
    sensor = _sensor(_device(498))
    _poll(sensor, 498)
    assert sensor.native_value == 4.98

    _poll(
        sensor,
        0,
        alarms=[
            {**FLOW_ALARM, "value": False},  # cleared
            {"errorCode": "PH_HIGH", "value": True},  # unrelated
            "junk",
        ],
    )
    assert sensor.native_value == 4.98


def test_no_flow_is_exposed_as_an_attribute() -> None:
    sensor = _sensor(_device(0, alarms=[FLOW_ALARM]))
    attributes = sensor.extra_state_attributes
    assert attributes["no_flow"] is True
    assert attributes["low_production"] is True


# --- Issue #174: schedule write guard ----------------------------------------


def _exo_coordinator(vs: bool) -> Any:
    device = {
        "device_id": "NS25007212",
        "name": "Zodiac EXO iQ 35",
        "family": "Chlorinators",
        "type": "connected",
        "components": {"82": {"reportedValue": not vs}, "83": {"reportedValue": vs}},
    }
    coordinator = MagicMock()
    coordinator.data = {POOL_ID: {"id": POOL_ID, "name": "Pool", "devices": [device]}}
    return coordinator


def test_writing_to_a_variable_speed_schedule_is_refused() -> None:
    """Refusing is recoverable; writing a slot with no RPM is not."""
    from custom_components.fluidra_pool import _ensure_schedule_write_supported

    coordinator = _exo_coordinator(vs=True)
    with pytest.raises(ServiceValidationError):
        _ensure_schedule_write_supported(coordinator, "NS25007212", 21)


def test_simple_pump_and_chlorination_schedules_are_still_writable() -> None:
    from custom_components.fluidra_pool import _ensure_schedule_write_supported

    coordinator = _exo_coordinator(vs=False)
    _ensure_schedule_write_supported(coordinator, "NS25007212", 20)
    _ensure_schedule_write_supported(coordinator, "NS25007212", 19)


def test_devices_without_a_schedule_map_are_unaffected() -> None:
    """Every other profile keeps writing exactly as before."""
    from custom_components.fluidra_pool import _ensure_schedule_write_supported

    device = {
        "device_id": "DM24008702.nn_1",
        "name": "Chlorinator",
        "family": "Chlorinators",
        "type": "chlorinator",
        "model": "Chlorinator",
        "components": {"172": {"reportedValue": 722}},
    }
    coordinator = MagicMock()
    coordinator.data = {POOL_ID: {"id": POOL_ID, "name": "Pool", "devices": [device]}}
    _ensure_schedule_write_supported(coordinator, "DM24008702.nn_1", 20)
