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
    calling _update_last_known_value directly is what HA's update does,
    without needing a real hass to write state.
    """
    device = sensor.coordinator.data[POOL_ID]["devices"][0]
    device["components"]["174"]["reportedValue"] = salinity
    if alarms is not None:
        device["alarms"] = alarms
    sensor._update_last_known_value()


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


def test_last_known_wins_over_no_flow_while_device_is_offline() -> None:
    """The device_offline guard takes priority over the no-flow guard.

    An offline chlorinator cannot report a trustworthy alarm state either --
    a stale FLOW alarm sitting in a frozen components/alarms snapshot is no
    more trustworthy than a stale reportedValue. native_value must return
    early on the offline check, never reaching _no_flow_reported(), so a
    disconnected device with an active (necessarily stale) FLOW alarm still
    shows the last confirmed-good salinity rather than unknown.
    """
    sensor = _sensor(_device(498))
    _poll(sensor, 498)
    assert sensor.native_value == 4.98

    device = sensor.coordinator.data[POOL_ID]["devices"][0]
    device["online"] = False
    device["components"]["174"]["reportedValue"] = 0
    device["alarms"] = [FLOW_ALARM]
    assert sensor.native_value == 4.98


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


@pytest.mark.parametrize("component", [19, 20, 21])
def test_every_exo_schedule_register_is_refused(component: int) -> None:
    """The eXO stores a schedule different from the one sent, and acts on it.

    Verified across four hardware runs: 01:02-03:04 on one day came back as
    "03 02" / "00 04" on four days, the stored days tracking the sent day as
    {0, n+2, n+5, n+6}. A wrong-but-applied schedule runs the equipment at
    hours nobody chose, which is worse than refusing (Issue #174).
    """
    from custom_components.fluidra_pool import _ensure_schedule_write_supported

    coordinator = _exo_coordinator(vs=component == 21)
    with pytest.raises(ServiceValidationError):
        _ensure_schedule_write_supported(coordinator, "NS25007212", component)


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


# The PUT body @Inervo captured from the official app, for the same device
# family and endpoint the integration writes to. Its full key set is
# id/groupId/enabled/startTime/endTime/startActions -- no ``state``, and the
# componentActions value under ``desiredValue`` with ``operationName``
# alongside, not instead.
APP_PUT_SLOT = {
    "id": 3,
    "groupId": 3,
    "enabled": False,
    "startTime": "11 21 * * 1",
    "endTime": "19 21 * * 1",
    "startActions": {"operationName": "1", "componentActions": [{"id": 0, "desiredValue": 1}]},
}

# The same slot as the device *reports* it back: it adds ``state`` itself and
# echoes the action under ``reportedValue``.
EXO_REPORTED_SLOT = {
    "id": 3,
    "groupId": 3,
    "state": "IDLE",
    "enabled": False,
    "startTime": "11 21 * * 1",
    "endTime": "19 21 * * 1",
    "startActions": {"componentActions": [{"id": 0, "reportedValue": 1}]},
}


def test_payload_matches_the_apps_own_put_body() -> None:
    """Field for field, against the app's captured PUT body (Issue #174)."""
    from custom_components.fluidra_pool import _service_schedule_to_fluidra

    ours = _service_schedule_to_fluidra(
        {"enabled": False, "start_time": "21:11", "end_time": "21:19", "mode": "1", "days": [1]},
        3,
        use_component_actions=True,
    )
    assert ours == APP_PUT_SLOT


def test_a_reported_slot_is_converted_to_the_write_shape() -> None:
    """Echoing a read slot back verbatim is what landed mangled (Issue #174)."""
    from custom_components.fluidra_pool.helpers import schedule_slots_for_write

    assert schedule_slots_for_write([EXO_REPORTED_SLOT]) == [APP_PUT_SLOT]


def test_write_shape_leaves_operation_name_devices_alone() -> None:
    """Units carrying the mode as operationName keep their payload untouched."""
    from custom_components.fluidra_pool.helpers import schedule_slots_for_write

    slot = {
        "id": 1,
        "groupId": 1,
        "enabled": True,
        "startTime": "09 08 * * 1,0",
        "endTime": "11 10 * * 1,0",
        "startActions": {"operationName": "1"},
    }
    assert schedule_slots_for_write([slot]) == [slot]


def test_state_is_never_sent() -> None:
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


def test_key_order_matches_the_apps_own_put_body() -> None:
    """Ordered as the app sends them (Issue #174)."""
    from custom_components.fluidra_pool import _service_schedule_to_fluidra

    slot = _service_schedule_to_fluidra(
        {"enabled": False, "start_time": "21:11", "end_time": "21:19", "mode": "1", "days": [1]},
        3,
        use_component_actions=True,
    )
    assert list(slot) == list(APP_PUT_SLOT)


# --- Issue #174: a slot cannot cross midnight --------------------------------


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("22:00", "06:00"),  # The overnight case named on the issue.
        ("08:00", "08:00"),  # Zero-length.
        ("10:30", "10:29"),  # One minute backwards.
        ("23:59", "00:00"),  # Straddling midnight by a minute.
    ],
)
def test_a_slot_that_does_not_end_after_it_starts_is_refused(start: str, end: str) -> None:
    """start and end are two CRONs on one day set, so overnight has no shape."""
    from homeassistant.exceptions import ServiceValidationError

    from custom_components.fluidra_pool import _service_schedule_to_fluidra

    with pytest.raises(ServiceValidationError) as err:
        _service_schedule_to_fluidra(
            {"enabled": True, "start_time": start, "end_time": end, "mode": "1", "days": [1]},
            1,
        )
    assert err.value.translation_key == "schedule_overnight_unsupported"


def test_the_two_halves_of_an_overnight_window_are_both_accepted() -> None:
    """The documented workaround: an evening slot and a morning slot."""
    from custom_components.fluidra_pool import _service_schedule_to_fluidra

    evening = _service_schedule_to_fluidra(
        {"enabled": True, "start_time": "22:00", "end_time": "23:59", "mode": "1", "days": [1]}, 1
    )
    morning = _service_schedule_to_fluidra(
        {"enabled": True, "start_time": "00:00", "end_time": "06:00", "mode": "1", "days": [2]}, 2
    )
    assert evening["startTime"] == "00 22 * * 1"
    assert morning["endTime"] == "00 06 * * 2"
