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


# --- Issue #174: the schedule payload must match what the device reports ------


def _coordinator_with_schedules(schedules: list) -> Any:
    coordinator = MagicMock()
    coordinator.api.get_device_by_id.return_value = {"schedule_data": schedules}
    return coordinator


EXO_SLOT = {
    "id": 3,
    "groupId": 3,
    "state": "IDLE",
    "enabled": False,
    "startTime": "11 21 * * 1",
    "endTime": "19 21 * * 1",
    "startActions": {"componentActions": [{"id": 0, "reportedValue": 1}]},
}


def test_payload_matches_the_shape_the_exo_itself_reports() -> None:
    """Field for field, against a slot the Fluidra app created (Issue #174)."""
    from custom_components.fluidra_pool import _service_schedule_to_fluidra

    ours = _service_schedule_to_fluidra(
        {"enabled": False, "start_time": "21:11", "end_time": "21:19", "mode": "1", "days": [1]},
        3,
        use_component_actions=True,
        include_state=True,
    )
    assert ours == EXO_SLOT


def test_state_is_mirrored_from_the_device_not_assumed() -> None:
    """Devices that reject a synthesised state (#89) must not receive one."""
    from custom_components.fluidra_pool import _device_uses_schedule_state

    assert _device_uses_schedule_state(_coordinator_with_schedules([EXO_SLOT]), "NS25007212") is True

    without_state = {k: v for k, v in EXO_SLOT.items() if k != "state"}
    assert _device_uses_schedule_state(_coordinator_with_schedules([without_state]), "DM1") is False
    assert _device_uses_schedule_state(_coordinator_with_schedules([]), "DM1") is False
    assert _device_uses_schedule_state(_coordinator_with_schedules(["junk", None]), "DM1") is False


def test_state_is_omitted_unless_requested() -> None:
    from custom_components.fluidra_pool import _service_schedule_to_fluidra

    ours = _service_schedule_to_fluidra(
        {"enabled": True, "start_time": "05:45", "end_time": "10:15", "mode": "1", "days": [0, 1, 2, 3, 4, 5, 6]},
        1,
        use_component_actions=True,
    )
    assert "state" not in ours
    # Times and days still match the app's own encoding.
    assert ours["startTime"] == "45 05 * * 0,1,2,3,4,5,6"
    assert ours["endTime"] == "15 10 * * 0,1,2,3,4,5,6"


def test_sunday_is_written_as_cron_day_zero() -> None:
    """The service takes 1..7 (Sunday=7); CRON has no day 7 (Issue #174).

    Checked against a Sunday schedule the Fluidra app itself created, which
    reads "00 18 * * 0" for 18:00 on Sunday.
    """
    from custom_components.fluidra_pool import _service_schedule_to_fluidra

    sunday = _service_schedule_to_fluidra(
        {"enabled": True, "start_time": "18:00", "end_time": "19:00", "mode": "0", "days": [7]},
        2,
        use_component_actions=True,
    )
    assert sunday["startTime"] == "00 18 * * 0"
    assert sunday["endTime"] == "00 19 * * 0"


def test_every_day_matches_the_apps_own_encoding() -> None:
    """The app writes "45 05 * * 0,1,2,3,4,5,6" for an all-days 05:45 start."""
    from custom_components.fluidra_pool import _service_schedule_to_fluidra

    all_days = _service_schedule_to_fluidra(
        {"enabled": True, "start_time": "05:45", "end_time": "10:15", "mode": "1", "days": [1, 2, 3, 4, 5, 6, 7]},
        1,
        use_component_actions=True,
    )
    assert all_days["startTime"] == "45 05 * * 0,1,2,3,4,5,6"


def test_sunday_is_not_duplicated_when_sent_both_ways() -> None:
    """0 and 7 both mean Sunday; the slot must not list it twice."""
    from custom_components.fluidra_pool import _service_schedule_to_fluidra

    slot = _service_schedule_to_fluidra(
        {"enabled": True, "start_time": "01:00", "end_time": "02:00", "mode": "0", "days": [7, 1]},
        1,
        use_component_actions=True,
    )
    assert slot["startTime"] == "00 01 * * 0,1"


def test_key_order_matches_the_devices_own_slots() -> None:
    """Ordered as the eXO reports them, state third (Issue #174)."""
    from custom_components.fluidra_pool import _service_schedule_to_fluidra

    slot = _service_schedule_to_fluidra(
        {"enabled": False, "start_time": "21:11", "end_time": "21:19", "mode": "1", "days": [1]},
        3,
        use_component_actions=True,
        include_state=True,
    )
    assert list(slot) == list(EXO_SLOT)
