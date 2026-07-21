"""Full-coverage tests for sensor/device.py, sensor/pool.py and sensor/__init__.py.

Focus: device-level and pool-level sensor classes plus the platform
``async_setup_entry``. Chlorinator measurement sensors and a handful of basic
temperature/weather/status/location cases are already exercised by
``tests/test_sensor.py`` — those are intentionally NOT duplicated here. This file
fills the gaps: brightness component paths, running hours, pump speed/schedule,
device-info diagnostics, the richer pool status/location/water-quality bodies,
availability, edge/None values and the setup wiring.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.fluidra_pool.const import DOMAIN
from custom_components.fluidra_pool.sensor import (
    FluidraChlorinatorSensor,
    FluidraDeviceInfoSensor,
    FluidraLightBrightnessSensor,
    FluidraPoolLocationSensor,
    FluidraPoolStatusSensor,
    FluidraPoolWaterQualitySensor,
    FluidraPoolWeatherSensor,
    FluidraPumpFlowSensor,
    FluidraPumpHeadSensor,
    FluidraPumpPowerSensor,
    FluidraPumpScheduleSensor,
    FluidraPumpSpeedSensor,
    FluidraRunningHoursSensor,
    FluidraTemperatureSensor,
    async_setup_entry,
)

POOL_ID = "pool-1"
DEVICE_ID = "TEST-DEV-001"


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


def _coord(devices: list[dict], pool_extra: dict | None = None) -> Any:
    """Build a MagicMock coordinator exposing .data shaped like the real one."""
    coordinator = MagicMock()
    pool = {"id": POOL_ID, "name": "Pool", "devices": devices}
    pool.update(pool_extra or {})
    coordinator.data = {POOL_ID: pool}
    coordinator.last_update_success = True
    return coordinator


def _pinned_device(
    device_id: str,
    *,
    components: dict | None = None,
    entities: list[str] | None = None,
    features: dict | None = None,
    device_type: str = "generic",
    **extra: Any,
) -> dict:
    """Build a device dict with a pinned DeviceIdentifier cache.

    The cache key matches the (device_id, family, model, type, comp7) tuple the
    identifier computes for a device with empty family/model/type and no comp 7.
    """
    components = components or {}
    comp7 = ""
    if "7" in components and isinstance(components["7"], dict):
        comp7 = str(components["7"].get("reportedValue", ""))
    device = {
        "device_id": device_id,
        "name": "Device",
        "family": "",
        "type": "",
        "model": "",
        "online": True,
        "components": components,
        "_identify_cache": {
            "key": (device_id, "", "", "", comp7),
            "config": SimpleNamespace(
                device_type=device_type,
                features=features or {},
                components_range=25,
                required_components=[0, 1, 2, 3],
                entities=entities or [],
            ),
        },
    }
    device.update(extra)
    return device


# --------------------------------------------------------------------------- #
# FluidraTemperatureSensor — translation_key mapping + availability            #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("sensor_type", "expected_key"),
    [
        ("current", "current_temperature"),
        ("target", "target_temperature"),
        ("water", "water_temperature"),
        ("air", "air_temperature"),
        ("nonsense", "current_temperature"),
    ],
)
def test_temperature_sensor_translation_key_map(sensor_type: str, expected_key: str) -> None:
    """Constructor maps the sensor_type onto the right translation key (default = current)."""
    device = _pinned_device(DEVICE_ID)
    sensor = FluidraTemperatureSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID, sensor_type)
    assert sensor._attr_translation_key == expected_key
    assert sensor.icon == "mdi:thermometer"


def test_temperature_sensor_value_none_when_field_absent() -> None:
    """A known type whose field is absent returns None (not 0)."""
    device = _pinned_device(DEVICE_ID)
    sensor = FluidraTemperatureSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID, "water")
    assert sensor.native_value is None


@pytest.mark.parametrize(
    ("sensor_type", "field", "value"),
    [
        ("current", "current_temperature", 19.5),
        ("target", "target_temperature", 27.0),
        ("water", "water_temperature", 24.0),
        ("air", "air_temperature", 31.2),
    ],
)
def test_temperature_sensor_reads_value(sensor_type: str, field: str, value: float) -> None:
    """current/target/water/air variants surface the matching device field value."""
    device = _pinned_device(DEVICE_ID, **{field: value})
    sensor = FluidraTemperatureSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID, sensor_type)
    assert sensor.native_value == value


def test_temperature_sensor_native_value_none_for_unknown_type() -> None:
    """An unrecognised sensor_type returns None from native_value (final fallthrough)."""
    device = _pinned_device(DEVICE_ID, current_temperature=20.0)
    sensor = FluidraTemperatureSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID, "garbage")
    assert sensor.native_value is None


def test_temperature_sensor_available_follows_online_flag() -> None:
    """Device-attached sensors are unavailable when the device reports offline."""
    device = _pinned_device(DEVICE_ID, online=False)
    sensor = FluidraTemperatureSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID, "current")
    assert sensor.available is False


def test_temperature_sensor_unavailable_when_coordinator_failed() -> None:
    """A failed coordinator update makes the entity unavailable even when online."""
    device = _pinned_device(DEVICE_ID, online=True)
    coord = _coord([device])
    coord.last_update_success = False
    sensor = FluidraTemperatureSensor(coord, SimpleNamespace(), POOL_ID, DEVICE_ID, "current")
    assert sensor.available is False


def test_device_sensor_device_data_empty_when_pool_missing() -> None:
    """device_data degrades to {} when the coordinator has no data for the pool."""
    coord = MagicMock()
    coord.data = None
    coord.last_update_success = True
    sensor = FluidraTemperatureSensor(coord, SimpleNamespace(), POOL_ID, DEVICE_ID, "current")
    assert sensor.device_data == {}
    assert sensor.native_value is None
    assert sensor.available is False


# --------------------------------------------------------------------------- #
# FluidraLightBrightnessSensor — legacy field vs LumiPlus component (17)        #
# --------------------------------------------------------------------------- #


def test_brightness_legacy_field_takes_priority() -> None:
    """The injected ``brightness`` field wins over the component."""
    device = _pinned_device(DEVICE_ID, brightness=42, components={"17": {"reportedValue": 99}})
    sensor = FluidraLightBrightnessSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID)
    assert sensor.native_value == 42
    assert sensor.icon == "mdi:brightness-percent"


def test_brightness_reads_component_17_rounded() -> None:
    """Without the legacy field it reads LumiPlus component 17 reportedValue and rounds it."""
    device = _pinned_device(DEVICE_ID, components={"17": {"reportedValue": 63.6}})
    sensor = FluidraLightBrightnessSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID)
    assert sensor.native_value == 64


def test_brightness_none_when_component_missing() -> None:
    """No brightness field and no component 17 yields None."""
    device = _pinned_device(DEVICE_ID, components={})
    sensor = FluidraLightBrightnessSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID)
    assert sensor.native_value is None


def test_brightness_none_when_component_not_a_dict() -> None:
    """A non-dict component 17 payload returns None instead of raising."""
    device = _pinned_device(DEVICE_ID, components={"17": "garbage"})
    sensor = FluidraLightBrightnessSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID)
    assert sensor.native_value is None


def test_brightness_none_when_reported_value_missing() -> None:
    """Component present but reportedValue is None -> None."""
    device = _pinned_device(DEVICE_ID, components={"17": {"reportedValue": None}})
    sensor = FluidraLightBrightnessSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID)
    assert sensor.native_value is None


def test_brightness_none_when_reported_value_not_numeric() -> None:
    """A non-numeric reportedValue degrades to None."""
    device = _pinned_device(DEVICE_ID, components={"17": {"reportedValue": "bright"}})
    sensor = FluidraLightBrightnessSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID)
    assert sensor.native_value is None


# --------------------------------------------------------------------------- #
# FluidraRunningHoursSensor                                                     #
# --------------------------------------------------------------------------- #


def test_running_hours_reads_field() -> None:
    """Running hours sensor surfaces the ``running_hours`` device field."""
    device = _pinned_device(DEVICE_ID, running_hours=1234)
    sensor = FluidraRunningHoursSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID)
    assert sensor.native_value == 1234
    assert sensor._attr_translation_key == "running_hours"


def test_running_hours_none_when_absent() -> None:
    """Absent running_hours -> None."""
    device = _pinned_device(DEVICE_ID)
    sensor = FluidraRunningHoursSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID)
    assert sensor.native_value is None


# --------------------------------------------------------------------------- #
# FluidraPumpSpeedSensor — mode classification, icon, attributes               #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("device_fields", "expected_mode"),
    [
        ({"is_running": False}, "stopped"),
        ({"is_running": True, "speed_percent": 0}, "not_running"),
        ({"is_running": True, "speed_percent": 30}, "low"),
        ({"is_running": True, "speed_percent": 50}, "low"),
        ({"is_running": True, "speed_percent": 65}, "medium"),
        ({"is_running": True, "speed_percent": 70}, "medium"),
        ({"is_running": True, "speed_percent": 100}, "high"),
        # pump_reported overrides is_running.
        ({"is_running": True, "pump_reported": 0, "speed_percent": 80}, "stopped"),
        ({"is_running": False, "pump_reported": 1, "speed_percent": 80}, "high"),
    ],
)
def test_pump_speed_native_value_classification(device_fields: dict, expected_mode: str) -> None:
    """Speed mode is derived from running flag + speed_percent thresholds."""
    device = _pinned_device(DEVICE_ID, **device_fields)
    sensor = FluidraPumpSpeedSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID)
    assert sensor.native_value == expected_mode


def test_pump_speed_victoria_running_without_percent_reads_running() -> None:
    """A Victoria VS pump running under a schedule reports c21=0 (no live %); the
    speed sensor must read "running", not the misleading "not_running" (Issue #144)."""
    device = _pinned_device(
        DEVICE_ID,
        is_running=True,
        speed_percent=0,
        features={"victoria_vs_mode": True},
        device_type="pump",
    )
    sensor = FluidraPumpSpeedSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID)
    assert sensor.native_value == "running"
    assert sensor.icon == "mdi:pump"


def test_pump_speed_non_victoria_running_without_percent_still_not_running() -> None:
    """Non-Victoria pumps keep the historical "not_running" for is_running + 0 %."""
    device = _pinned_device(DEVICE_ID, is_running=True, speed_percent=0, device_type="pump")
    sensor = FluidraPumpSpeedSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID)
    assert sensor.native_value == "not_running"


def test_pump_speed_victoria_with_percent_classifies_normally() -> None:
    """A Victoria reporting a live % (e.g. QUICK FUNCTION) still classifies low/medium/high."""
    device = _pinned_device(
        DEVICE_ID,
        is_running=True,
        speed_percent=95,
        features={"victoria_vs_mode": True},
        device_type="pump",
    )
    sensor = FluidraPumpSpeedSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID)
    assert sensor.native_value == "high"


def test_pump_speed_icon_off_when_stopped() -> None:
    """The icon switches to pump-off for the stopped/not-running states."""
    stopped = _pinned_device(DEVICE_ID, is_running=False)
    sensor = FluidraPumpSpeedSensor(_coord([stopped]), SimpleNamespace(), POOL_ID, DEVICE_ID)
    assert sensor.icon == "mdi:pump-off"

    not_running = _pinned_device(DEVICE_ID, is_running=True, speed_percent=0)
    sensor2 = FluidraPumpSpeedSensor(_coord([not_running]), SimpleNamespace(), POOL_ID, DEVICE_ID)
    assert sensor2.icon == "mdi:pump-off"


def test_pump_speed_icon_on_when_running() -> None:
    """A running pump shows the pump icon."""
    device = _pinned_device(DEVICE_ID, is_running=True, speed_percent=80)
    sensor = FluidraPumpSpeedSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID)
    assert sensor.icon == "mdi:pump"


def test_pump_speed_extra_attributes_reflect_reported_overrides() -> None:
    """extra_state_attributes mirrors raw + reported values and applies the overrides."""
    device = _pinned_device(
        DEVICE_ID,
        is_running=False,
        auto_mode_enabled=False,
        pump_reported=1,
        auto_reported=1,
        speed_percent=55,
        speed_level_reported=2,
    )
    sensor = FluidraPumpSpeedSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID)
    attrs = sensor.extra_state_attributes
    assert attrs["pump_running"] is True  # pump_reported override
    assert attrs["auto_mode"] is True  # auto_reported override
    assert attrs["speed_percent"] == 55
    assert attrs["speed_level"] == 2
    assert attrs["pump_reported"] == 1
    assert attrs["auto_reported"] == 1
    assert attrs["raw_data"] == {
        "is_running": False,
        "auto_mode_enabled": False,
        "speed_percent": 55,
    }


def test_pump_speed_extra_attributes_defaults_without_reported() -> None:
    """Without reported overrides the attributes fall back to the base fields."""
    device = _pinned_device(DEVICE_ID, is_running=True, auto_mode_enabled=True, speed_percent=40)
    sensor = FluidraPumpSpeedSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID)
    attrs = sensor.extra_state_attributes
    assert attrs["pump_running"] is True
    assert attrs["auto_mode"] is True
    assert attrs["speed_percent"] == 40
    assert attrs["speed_level"] is None
    assert attrs["pump_reported"] is None
    assert attrs["auto_reported"] is None


# --------------------------------------------------------------------------- #
# FluidraPumpScheduleSensor                                                     #
# --------------------------------------------------------------------------- #


def _schedule(
    *,
    enabled: bool = True,
    start: str = "0 8 * * 0,1,2,3,4,5,6",
    end: str = "0 18 * * 0,1,2,3,4,5,6",
    operation: str = "1",
    sched_id: str = "s1",
    state: str = "RUNNING",
) -> dict:
    return {
        "id": sched_id,
        "enabled": enabled,
        "startTime": start,
        "endTime": end,
        "startActions": {"operationName": operation},
        "state": state,
    }


def test_schedule_native_value_counts_enabled() -> None:
    """native_value counts only enabled schedules."""
    device = _pinned_device(
        DEVICE_ID,
        schedule_data=[_schedule(sched_id="a"), _schedule(enabled=False, sched_id="b"), _schedule(sched_id="c")],
    )
    sensor = FluidraPumpScheduleSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID)
    assert sensor.native_value == 2
    assert sensor.icon == "mdi:calendar-clock"


def test_schedule_native_value_zero_when_no_schedules() -> None:
    """Empty schedule_data -> 0 (not None)."""
    device = _pinned_device(DEVICE_ID, schedule_data=[])
    sensor = FluidraPumpScheduleSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID)
    assert sensor.native_value == 0


def test_schedule_native_value_zero_when_missing_block() -> None:
    """No schedule_data key at all -> 0 via the empty-list helper path."""
    device = _pinned_device(DEVICE_ID)
    sensor = FluidraPumpScheduleSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID)
    assert sensor.native_value == 0


def test_schedule_native_value_handles_bad_payload() -> None:
    """A malformed schedule entry triggers the guarded exception path -> None."""
    device = _pinned_device(DEVICE_ID, schedule_data=["not-a-dict"])
    sensor = FluidraPumpScheduleSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID)
    # iterating ``s.get`` on a str raises AttributeError -> caught -> None.
    assert sensor.native_value is None


def test_schedule_extra_attributes_formats_enabled_schedules() -> None:
    """extra_state_attributes formats time ranges, modes and totals."""
    device = _pinned_device(
        DEVICE_ID,
        schedule_data=[
            _schedule(sched_id="x", start="30 8 * * *", end="0 12 * * *", operation="2"),
            _schedule(enabled=False, sched_id="y"),
        ],
    )
    sensor = FluidraPumpScheduleSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID)
    attrs = sensor.extra_state_attributes
    assert attrs["total_schedules"] == 2
    assert attrs["enabled_schedules"] == 1
    formatted = attrs["schedules"]
    assert len(formatted) == 1
    assert formatted[0]["id"] == "x"
    assert formatted[0]["time"] == "08:30-12:00"
    assert formatted[0]["mode"] == "high (100%)"
    assert formatted[0]["state"] == "RUNNING"


def test_schedule_extra_attributes_invalid_cron_renders_na() -> None:
    """A cron string that can't be parsed yields an 'N/A' time range."""
    device = _pinned_device(
        DEVICE_ID,
        schedule_data=[_schedule(start="bad cron", end="also bad", operation="0")],
    )
    sensor = FluidraPumpScheduleSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID)
    attrs = sensor.extra_state_attributes
    assert attrs["schedules"][0]["time"] == "N/A"
    assert attrs["schedules"][0]["mode"] == "low (45%)"


def test_schedule_extra_attributes_empty_when_no_data() -> None:
    """Without schedules the attribute dict is empty."""
    device = _pinned_device(DEVICE_ID, schedule_data=[])
    sensor = FluidraPumpScheduleSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID)
    assert sensor.extra_state_attributes == {}


def test_schedule_extra_attributes_error_path() -> None:
    """A malformed schedule surfaces an 'error' key rather than raising."""
    device = _pinned_device(DEVICE_ID, schedule_data=["boom"])
    sensor = FluidraPumpScheduleSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID)
    attrs = sensor.extra_state_attributes
    assert "error" in attrs


def test_schedule_get_operation_name_unknown_defaults_low() -> None:
    """Unknown operation codes fall back to 'low (45%)'."""
    device = _pinned_device(DEVICE_ID)
    sensor = FluidraPumpScheduleSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID)
    assert sensor._get_operation_name("99") == "low (45%)"
    assert sensor._get_operation_name("0") == "low (45%)"
    assert sensor._get_operation_name("1") == "medium (65%)"


def test_schedule_parse_cron_time_handles_short_and_bad() -> None:
    """_parse_cron_time returns None for too-short or non-numeric cron strings."""
    device = _pinned_device(DEVICE_ID)
    sensor = FluidraPumpScheduleSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID)
    assert sensor._parse_cron_time("5") is None
    assert sensor._parse_cron_time("aa bb") is None
    parsed = sensor._parse_cron_time("15 9 * * *")
    assert parsed is not None
    assert parsed.hour == 9
    assert parsed.minute == 15


def test_schedule_get_current_schedule_active_window() -> None:
    """_get_current_schedule returns the schedule whose window contains 'now'."""
    from datetime import UTC, datetime

    fake_now = datetime(2026, 5, 30, 10, 0, 0, tzinfo=UTC)

    device = _pinned_device(DEVICE_ID)
    sensor = FluidraPumpScheduleSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID)
    active = _schedule(sched_id="active", start="0 8 * * *", end="0 18 * * *")
    inactive = _schedule(sched_id="inactive", start="0 20 * * *", end="0 22 * * *")
    disabled = _schedule(sched_id="disabled", enabled=False, start="0 9 * * *", end="0 11 * * *")
    with patch(
        "custom_components.fluidra_pool.sensor.device.dt_util.now",
        return_value=fake_now,
    ):
        result = sensor._get_current_schedule([disabled, inactive, active])
    assert result is not None
    assert result["id"] == "active"


def test_schedule_extra_attributes_includes_current_schedule() -> None:
    """When 'now' is inside an enabled schedule, current_* attrs are populated."""
    from datetime import UTC, datetime

    fake_now = datetime(2026, 5, 30, 10, 0, 0, tzinfo=UTC)

    device = _pinned_device(
        DEVICE_ID,
        schedule_data=[_schedule(sched_id="now", start="0 8 * * *", end="0 18 * * *", operation="1")],
    )
    sensor = FluidraPumpScheduleSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID)
    with patch(
        "custom_components.fluidra_pool.sensor.device.dt_util.now",
        return_value=fake_now,
    ):
        attrs = sensor.extra_state_attributes
    assert attrs["current_schedule_id"] == "now"
    assert attrs["current_time_range"] == "08:00-18:00"
    assert attrs["current_mode"] == "medium (65%)"


# --------------------------------------------------------------------------- #
# FluidraDeviceInfoSensor                                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("signal", "expected"),
    [
        (-45, "signal_excellent"),
        (-50, "signal_excellent"),
        (-55, "signal_very_good"),
        (-60, "signal_very_good"),
        (-65, "signal_good"),
        (-70, "signal_good"),
        (-75, "signal_low"),
        (-80, "signal_low"),
        (-90, "signal_very_low"),
    ],
)
def test_device_info_signal_buckets(signal: int, expected: str) -> None:
    """native_value maps RSSI dBm into the documented signal buckets."""
    device = _pinned_device(DEVICE_ID, signal_strength_component=signal)
    sensor = FluidraDeviceInfoSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID)
    assert sensor.native_value == expected
    assert sensor.icon == "mdi:information-outline"


def test_device_info_online_when_no_signal() -> None:
    """Absent or zero signal -> 'online' (the catch-all healthy state)."""
    device = _pinned_device(DEVICE_ID)
    sensor = FluidraDeviceInfoSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID)
    assert sensor.native_value == "online"

    device_zero = _pinned_device(DEVICE_ID, signal_strength_component=0)
    sensor_zero = FluidraDeviceInfoSensor(_coord([device_zero]), SimpleNamespace(), POOL_ID, DEVICE_ID)
    assert sensor_zero.native_value == "online"


def test_device_info_native_value_error_on_bad_signal_comparison() -> None:
    """A string signal triggers the guarded path; it is treated as 'online'.

    Note: the code's ``isinstance(signal, (int, float))`` guard means a string
    signal does not raise — it falls through to 'online'. This asserts the
    CURRENT behaviour.
    """
    device = _pinned_device(DEVICE_ID, signal_strength_component="strong")
    sensor = FluidraDeviceInfoSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID)
    assert sensor.native_value == "online"


def test_device_info_extra_attributes_full() -> None:
    """extra_state_attributes surfaces all diagnostic fields + signal quality."""
    device = _pinned_device(
        DEVICE_ID,
        device_id_component="abc123",
        part_numbers_component=["PN1", "PN2"],
        signal_strength_component=-55,
        firmware_version_component="1.2.3",
        hardware_errors_component=2,
        comm_errors_component=1,
        timezone_component="Europe/Paris",
        network_status_component=1,
        name="My Heat Pump",
        type="heat_pump",
        manufacturer="Fluidra",
        model="Z550",
    )
    sensor = FluidraDeviceInfoSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID)
    attrs = sensor.extra_state_attributes
    assert attrs["device_id"] == "abc123"
    assert attrs["part_numbers"] == ["PN1", "PN2"]
    assert attrs["signal_strength_dbm"] == -55
    assert attrs["signal_quality"] == "very_good"
    assert attrs["network_status"] == "connected"
    assert attrs["firmware_version"] == "1.2.3"
    assert attrs["hardware_error_count"] == 2
    assert attrs["communication_error_count"] == 1
    assert attrs["timezone_info"] == "Europe/Paris"
    assert attrs["device_name"] == "My Heat Pump"
    assert attrs["device_type"] == "heat_pump"
    assert attrs["manufacturer"] == "Fluidra"
    assert attrs["model"] == "Z550"
    assert attrs["online"] is True


@pytest.mark.parametrize(
    ("signal", "quality"),
    [
        (-45, "excellent"),
        (-55, "very_good"),
        (-65, "good"),
        (-75, "low"),
        (-90, "very_low"),
    ],
)
def test_device_info_attribute_signal_quality_buckets(signal: int, quality: str) -> None:
    """The attribute-level signal_quality classification covers all buckets."""
    device = _pinned_device(DEVICE_ID, signal_strength_component=signal)
    sensor = FluidraDeviceInfoSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID)
    assert sensor.extra_state_attributes["signal_quality"] == quality


def test_device_info_network_status_disconnected() -> None:
    """network_status != 1 renders as 'disconnected'."""
    device = _pinned_device(DEVICE_ID, network_status_component=0)
    sensor = FluidraDeviceInfoSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID)
    assert sensor.extra_state_attributes["network_status"] == "disconnected"


def test_device_info_native_value_error_path() -> None:
    """If reading device data raises a caught error, native_value degrades to 'error'."""
    coord = MagicMock()
    coord.last_update_success = True
    coord.data = MagicMock()
    coord.data.get.side_effect = TypeError("boom")
    sensor = FluidraDeviceInfoSensor(coord, SimpleNamespace(), POOL_ID, DEVICE_ID)
    assert sensor.native_value == "error"


def test_device_info_extra_attributes_error_path() -> None:
    """A raised error inside extra_state_attributes is captured under the 'error' key."""
    coord = MagicMock()
    coord.last_update_success = True
    coord.data = MagicMock()
    coord.data.get.side_effect = KeyError("boom")
    sensor = FluidraDeviceInfoSensor(coord, SimpleNamespace(), POOL_ID, DEVICE_ID)
    attrs = sensor.extra_state_attributes
    assert "error" in attrs


def test_device_info_attributes_defaults_when_empty() -> None:
    """With no diagnostic components, defaults (name/type/manufacturer/model) still appear."""
    coord = MagicMock()
    coord.data = {POOL_ID: {"id": POOL_ID, "devices": [{"device_id": DEVICE_ID}]}}
    coord.last_update_success = True
    sensor = FluidraDeviceInfoSensor(coord, SimpleNamespace(), POOL_ID, DEVICE_ID)
    attrs = sensor.extra_state_attributes
    assert attrs["device_name"] == "Unknown"
    assert attrs["device_type"] == "unknown"
    assert attrs["manufacturer"] == "Fluidra"
    assert attrs["model"] == "Unknown"
    assert attrs["online"] is False
    assert "signal_strength_dbm" not in attrs


# --------------------------------------------------------------------------- #
# Pool-level: weather extra coverage                                           #
# --------------------------------------------------------------------------- #


def test_weather_none_when_current_not_dict() -> None:
    """A non-dict 'current' block returns None."""
    coord = _coord(
        [],
        pool_extra={"status_data": {"weather": {"status": "ok", "value": {"current": "nope"}}}},
    )
    sensor = FluidraPoolWeatherSensor(coord, SimpleNamespace(), POOL_ID)
    assert sensor.native_value is None


def test_weather_none_when_main_missing_temp() -> None:
    """'main' without 'temp' returns None."""
    coord = _coord(
        [],
        pool_extra={"status_data": {"weather": {"status": "ok", "value": {"current": {"main": {}}}}}},
    )
    sensor = FluidraPoolWeatherSensor(coord, SimpleNamespace(), POOL_ID)
    assert sensor.native_value is None


def test_weather_native_value_converts_kelvin() -> None:
    """A well-formed ok weather payload converts Kelvin to rounded Celsius."""
    coord = _coord(
        [],
        pool_extra={"status_data": {"weather": {"status": "ok", "value": {"current": {"main": {"temp": 300.0}}}}}},
    )
    sensor = FluidraPoolWeatherSensor(coord, SimpleNamespace(), POOL_ID)
    assert sensor.native_value == 26.9


def test_weather_unit_and_classes() -> None:
    """Static unit/device/state class properties are exposed correctly."""
    sensor = FluidraPoolWeatherSensor(_coord([]), SimpleNamespace(), POOL_ID)
    assert sensor.native_unit_of_measurement == "°C"
    assert sensor.device_class.value == "temperature"
    assert sensor.state_class.value == "measurement"
    assert sensor.icon == "mdi:thermometer"


# --------------------------------------------------------------------------- #
# Pool-level: status sensor icon + extra attributes                            #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("state", "icon"),
    [
        ("using", "mdi:pool"),
        ("maintenance", "mdi:tools"),
        ("offline", "mdi:pool-off"),
        ("winterized", "mdi:snowflake"),
        ("anything-else", "mdi:help-circle"),
    ],
)
def test_pool_status_icon(state: str, icon: str) -> None:
    """Each known state maps to its icon, unknown falls back to help-circle."""
    coord = _coord([], pool_extra={"state": state})
    sensor = FluidraPoolStatusSensor(coord, SimpleNamespace(), POOL_ID)
    assert sensor.icon == icon


@pytest.mark.parametrize(
    ("state", "name", "expected"),
    [
        ("using", "P", "using"),
        ("maintenance", "P", "maintenance"),
        ("offline", "P", "offline"),
        ("winterized", "P", "winterized"),
        ("weird", "P", "connected"),
        ("weird", "", "unknown_state"),
    ],
)
def test_pool_status_native_value_branches(state: str, name: str, expected: str) -> None:
    """native_value covers each known state plus the connected/unknown fallbacks."""
    coord = MagicMock()
    coord.last_update_success = True
    coord.data = {POOL_ID: {"state": state, "name": name}}
    sensor = FluidraPoolStatusSensor(coord, SimpleNamespace(), POOL_ID)
    assert sensor.native_value == expected


def test_pool_status_extra_attributes_full() -> None:
    """extra_state_attributes aggregates characteristics, disinfection, devices and weather."""
    coord = _coord(
        [
            {"device_id": "d1", "type": "pump"},
            {"device_id": "d2", "type": "pump"},
            {"device_id": "d3", "type": "light"},
            {"device_id": "d4"},  # missing type -> 'unknown'
        ],
        pool_extra={
            "state": "using",
            "owner": "owner-7",
            "characteristics": {
                "shape": "rectangle",
                "constructionYear": 2010,
                "waterproof": "liner",
                "ground": "in",
                "place": "garden",
                "type": "private",
                "dimensions": {"volume": 50},
            },
            "disinfection": {
                "method": {"type": "salt", "name": "Salt electrolysis"},
                "automatic": True,
            },
            "status_data": {
                "weather": {
                    "status": "ok",
                    "value": {
                        "current": {
                            "main": {"temp": 295.65, "humidity": 60, "pressure": 1012},
                            "wind": {"speed": 3.4},
                        }
                    },
                }
            },
        },
    )
    sensor = FluidraPoolStatusSensor(coord, SimpleNamespace(), POOL_ID)
    attrs = sensor.extra_state_attributes
    assert attrs["pool_state"] == "using"
    assert attrs["owner_id"] == "owner-7"
    assert attrs["shape"] == "rectangle"
    assert attrs["construction_year"] == 2010
    assert attrs["waterproof"] == "liner"
    assert attrs["ground"] == "in"
    assert attrs["place"] == "garden"
    assert attrs["pool_type"] == "private"
    assert attrs["volume_m3"] == 50
    assert attrs["disinfection_type"] == "salt"
    assert attrs["disinfection_method"] == "Salt electrolysis"
    assert attrs["automatic_disinfection"] is True
    assert attrs["total_devices"] == 4
    assert attrs["device_types"] == {"pump": 2, "light": 1, "unknown": 1}
    assert attrs["weather_available"] is True
    assert attrs["air_temperature"] == 22.5
    assert attrs["humidity"] == 60
    assert attrs["pressure"] == 1012
    assert attrs["wind_speed"] == 3.4


def test_pool_status_extra_attributes_minimal() -> None:
    """With a bare pool, only the always-present keys are returned."""
    coord = MagicMock()
    coord.data = {POOL_ID: {}}
    coord.last_update_success = True
    sensor = FluidraPoolStatusSensor(coord, SimpleNamespace(), POOL_ID)
    attrs = sensor.extra_state_attributes
    assert attrs["pool_state"] == "unknown"
    assert attrs["total_devices"] == 0
    assert attrs["device_types"] == {}
    assert "owner_id" not in attrs
    assert "weather_available" not in attrs


def test_pool_status_weather_main_not_dict() -> None:
    """A weather 'current' present but 'main' not a dict still marks weather available."""
    coord = _coord(
        [],
        pool_extra={
            "state": "using",
            "status_data": {"weather": {"status": "ok", "value": {"current": {"main": "nope", "wind": "nope"}}}},
        },
    )
    sensor = FluidraPoolStatusSensor(coord, SimpleNamespace(), POOL_ID)
    attrs = sensor.extra_state_attributes
    assert attrs["weather_available"] is True
    assert "air_temperature" not in attrs
    assert "wind_speed" not in attrs


def test_pool_status_available_follows_coordinator() -> None:
    """Pool-level sensors are available iff the last coordinator update succeeded."""
    coord = _coord([], pool_extra={"state": "using"})
    sensor = FluidraPoolStatusSensor(coord, SimpleNamespace(), POOL_ID)
    assert sensor.available is True
    coord.last_update_success = False
    assert sensor.available is False


# --------------------------------------------------------------------------- #
# Pool-level: location extra attributes                                        #
# --------------------------------------------------------------------------- #


def test_pool_location_locality_only() -> None:
    """Locality-only geolocation renders just the locality."""
    coord = _coord([], pool_extra={"geolocation": {"locality": "Nantes"}})
    sensor = FluidraPoolLocationSensor(coord, SimpleNamespace(), POOL_ID)
    assert sensor.native_value == "Nantes"
    assert sensor.icon == "mdi:map-marker"


def test_pool_location_country_only() -> None:
    """Country-only geolocation renders just the country code."""
    coord = _coord([], pool_extra={"geolocation": {"countryCode": "ES"}})
    sensor = FluidraPoolLocationSensor(coord, SimpleNamespace(), POOL_ID)
    assert sensor.native_value == "ES"


def test_pool_location_full_join() -> None:
    """Both locality and country code render as 'Locality, CC'."""
    coord = _coord([], pool_extra={"geolocation": {"locality": "Lyon", "countryCode": "FR"}})
    sensor = FluidraPoolLocationSensor(coord, SimpleNamespace(), POOL_ID)
    assert sensor.native_value == "Lyon, FR"


def test_pool_location_unknown_when_no_geo() -> None:
    """No geolocation -> literal 'Unknown'."""
    coord = _coord([])
    sensor = FluidraPoolLocationSensor(coord, SimpleNamespace(), POOL_ID)
    assert sensor.native_value == "Unknown"


def test_pool_location_extra_attributes() -> None:
    """extra_state_attributes exposes lat/long/locality/country + weather extras."""
    coord = _coord(
        [],
        pool_extra={
            "geolocation": {
                "latitude": 46.7,
                "longitude": -1.6,
                "locality": "Nantes",
                "countryCode": "FR",
            },
            "status_data": {
                "weather": {
                    "status": "ok",
                    "value": {"current": {"sys": {"country": "FR"}, "timezone": 7200}},
                }
            },
        },
    )
    sensor = FluidraPoolLocationSensor(coord, SimpleNamespace(), POOL_ID)
    attrs = sensor.extra_state_attributes
    assert attrs["latitude"] == 46.7
    assert attrs["longitude"] == -1.6
    assert attrs["locality"] == "Nantes"
    assert attrs["country_code"] == "FR"
    assert attrs["weather_country"] == "FR"
    assert attrs["timezone"] == 7200


def test_pool_location_extra_attributes_empty() -> None:
    """No geolocation and no weather -> empty attribute dict."""
    coord = _coord([])
    sensor = FluidraPoolLocationSensor(coord, SimpleNamespace(), POOL_ID)
    assert sensor.extra_state_attributes == {}


# --------------------------------------------------------------------------- #
# Pool-level: water quality sensor                                             #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("disinfection", "expected"),
    [
        ({"automatic": True}, "auto"),
        ({"automatic": False}, "manual"),
        ({}, "not_configured"),
    ],
)
def test_water_quality_native_value(disinfection: dict, expected: str) -> None:
    """native_value maps the disinfection.automatic flag to the enum option."""
    coord = _coord([], pool_extra={"disinfection": disinfection} if disinfection else {})
    sensor = FluidraPoolWaterQualitySensor(coord, SimpleNamespace(), POOL_ID)
    assert sensor.native_value == expected
    assert sensor.icon == "mdi:water-check"


def test_water_quality_extra_attributes_full() -> None:
    """extra_state_attributes surfaces ranges, current quality and pool characteristics."""
    coord = _coord(
        [],
        pool_extra={
            "disinfection": {"method": {"type": "salt", "name": "Salt"}, "automatic": True},
            "waterQualitySensorRanges": {
                "ph": {"minValue": 7.0, "maxValue": 7.6, "unit": "pH"},
                "chlorine": {"minValue": 0.5, "maxValue": 2.0, "unit": "ppm"},
                "salinity": {"minValue": 3.0, "maxValue": 6.0, "unit": "g/L"},
                "orp": {"minValue": 650, "maxValue": 750, "unit": "mV"},
            },
            "water_quality": {"ph": 7.2},
            "characteristics": {"dimensions": {"volume": 50}, "type": "private", "waterproof": "liner"},
        },
    )
    sensor = FluidraPoolWaterQualitySensor(coord, SimpleNamespace(), POOL_ID)
    attrs = sensor.extra_state_attributes
    assert attrs["disinfection_type"] == "salt"
    assert attrs["disinfection_method"] == "Salt"
    assert attrs["automatic_disinfection"] is True
    assert attrs["ph_min"] == 7.0
    assert attrs["ph_max"] == 7.6
    assert attrs["ph_unit"] == "pH"
    assert attrs["chlorine_min"] == 0.5
    assert attrs["chlorine_max"] == 2.0
    assert attrs["salinity_min"] == 3.0
    assert attrs["salinity_unit"] == "g/L"
    assert attrs["orp_min"] == 650
    assert attrs["orp_max"] == 750
    assert attrs["orp_unit"] == "mV"
    assert attrs["current_water_quality"] == {"ph": 7.2}
    assert attrs["pool_volume_m3"] == 50
    assert attrs["pool_type"] == "private"
    assert attrs["waterproof"] == "liner"


def test_water_quality_extra_attributes_empty() -> None:
    """A bare pool yields an empty attribute dict."""
    coord = MagicMock()
    coord.data = {POOL_ID: {}}
    coord.last_update_success = True
    sensor = FluidraPoolWaterQualitySensor(coord, SimpleNamespace(), POOL_ID)
    assert sensor.extra_state_attributes == {}


# --------------------------------------------------------------------------- #
# async_setup_entry — entity wiring                                            #
# --------------------------------------------------------------------------- #


def _setup_coordinator(devices: list[dict]) -> Any:
    """Coordinator whose .data + .api.cached_pools are both populated for setup."""
    coordinator = MagicMock()
    pool = {"id": POOL_ID, "name": "Pool", "devices": devices}
    coordinator.data = {POOL_ID: pool}
    coordinator.last_update_success = True
    coordinator.api = SimpleNamespace(
        cached_pools=[pool],
        get_pools=AsyncMock(return_value=[pool]),
    )
    return coordinator


async def _run_setup(coordinator: Any) -> list[Any]:
    entry = SimpleNamespace(
        runtime_data=SimpleNamespace(coordinator=coordinator),
        async_on_unload=lambda _unsub: None,
    )
    added: list[Any] = []

    def _add(entities, *a, **k):
        added.extend(list(entities))

    async_add = MagicMock(side_effect=_add)
    await async_setup_entry(MagicMock(), entry, async_add)
    return added


async def test_setup_adds_new_device_dynamically() -> None:
    """dynamic-devices: a device appearing on a later poll is wired without a reload."""
    dev1 = _pinned_device("dev1", entities=["sensor_info"], device_type="pump")
    pool = {"id": POOL_ID, "name": "Pool", "devices": [dev1]}
    coordinator = MagicMock()
    coordinator.data = {POOL_ID: pool}
    coordinator.last_update_success = True
    coordinator.api = SimpleNamespace(cached_pools=[pool], get_pools=AsyncMock(return_value=[pool]))
    coordinator.get_pools_from_data = lambda: [{"id": POOL_ID, **coordinator.data[POOL_ID]}]
    listeners: list[Any] = []
    coordinator.async_add_listener = lambda cb: listeners.append(cb) or (lambda: None)

    added: list[Any] = []
    entry = SimpleNamespace(
        runtime_data=SimpleNamespace(coordinator=coordinator),
        async_on_unload=lambda _unsub: None,
    )
    async_add = MagicMock(side_effect=lambda ents, *a, **k: added.extend(list(ents)))
    await async_setup_entry(MagicMock(), entry, async_add)

    uids_after_setup = {e.unique_id for e in added}
    assert any("dev1" in u for u in uids_after_setup)
    assert not any("dev2" in u for u in uids_after_setup)
    assert listeners, "a coordinator update listener must be registered for dynamic devices"

    # A new device shows up on a later poll; firing the listener must wire it.
    pool["devices"].append(_pinned_device("dev2", entities=["sensor_info"], device_type="pump"))
    listeners[0]()

    new_uids = {e.unique_id for e in added} - uids_after_setup
    assert new_uids, "new device entities should be added without a reload"
    assert all("dev2" in u for u in new_uids), "only the newly-added device's entities are created"


async def test_setup_creates_pool_level_sensors_always() -> None:
    """Even with no devices, the four pool-level sensors are created per pool."""
    coordinator = _setup_coordinator([])
    added = await _run_setup(coordinator)
    assert any(isinstance(e, FluidraPoolWeatherSensor) for e in added)
    assert any(isinstance(e, FluidraPoolStatusSensor) for e in added)
    assert any(isinstance(e, FluidraPoolLocationSensor) for e in added)
    assert any(isinstance(e, FluidraPoolWaterQualitySensor) for e in added)


async def test_setup_skips_device_without_device_id() -> None:
    """A device lacking device_id is skipped (only pool-level sensors remain)."""
    device = _pinned_device("", entities=["sensor_info"])
    device.pop("device_id")
    coordinator = _setup_coordinator([device])
    added = await _run_setup(coordinator)
    assert not any(isinstance(e, FluidraDeviceInfoSensor) for e in added)
    # pool-level sensors still added
    assert any(isinstance(e, FluidraPoolStatusSensor) for e in added)


async def test_setup_creates_device_level_sensors_per_entities_flags() -> None:
    """Each sensor_* flag in config.entities spawns the matching device sensor."""
    device = _pinned_device(
        DEVICE_ID,
        entities=[
            "sensor_info",
            "sensor_schedule",
            "sensor_speed",
            "sensor_brightness",
            "sensor_running_hours",
        ],
    )
    coordinator = _setup_coordinator([device])
    added = await _run_setup(coordinator)
    classes = {type(e) for e in added}
    assert FluidraDeviceInfoSensor in classes
    assert FluidraPumpScheduleSensor in classes
    assert FluidraPumpSpeedSensor in classes
    assert FluidraLightBrightnessSensor in classes
    assert FluidraRunningHoursSensor in classes


async def test_setup_temperature_target_only() -> None:
    """sensor_temperature + target_temperature field -> a single 'target' temp sensor."""
    device = _pinned_device(DEVICE_ID, entities=["sensor_temperature"], target_temperature=28.0)
    coordinator = _setup_coordinator([device])
    added = await _run_setup(coordinator)
    temps = [e for e in added if isinstance(e, FluidraTemperatureSensor)]
    assert len(temps) == 1
    assert temps[0]._sensor_type == "target"


async def test_setup_temperature_z550_adds_water_and_air() -> None:
    """A z550_mode heat pump gets target + water + air temperature sensors."""
    device = _pinned_device(
        DEVICE_ID,
        entities=["sensor_temperature"],
        features={"z550_mode": True},
        target_temperature=28.0,
    )
    coordinator = _setup_coordinator([device])
    added = await _run_setup(coordinator)
    temp_types = sorted(e._sensor_type for e in added if isinstance(e, FluidraTemperatureSensor))
    assert temp_types == ["air", "target", "water"]


async def test_setup_temperature_z260iq_adds_water_and_air() -> None:
    """A z260iq_mode heat pump (no target field) gets water + air temperature sensors."""
    device = _pinned_device(
        DEVICE_ID,
        entities=["sensor_temperature"],
        features={"z260iq_mode": True},
    )
    coordinator = _setup_coordinator([device])
    added = await _run_setup(coordinator)
    temp_types = sorted(e._sensor_type for e in added if isinstance(e, FluidraTemperatureSensor))
    assert temp_types == ["air", "water"]


async def test_setup_chlorinator_creates_measurement_sensors() -> None:
    """A chlorinator device with a sensors feature spawns one sensor per configured type."""
    device = _pinned_device(
        DEVICE_ID,
        device_type="chlorinator",
        entities=[],
        features={"sensors": {"ph": 165, "orp": 170, "temperature": 172}},
    )
    coordinator = _setup_coordinator([device])
    added = await _run_setup(coordinator)
    chlor = [e for e in added if isinstance(e, FluidraChlorinatorSensor)]
    assert len(chlor) == 3


# --------------------------------------------------------------------------- #
# FluidraChlorinatorSensor — divisor override, device_data, device_info, attrs #
# --------------------------------------------------------------------------- #


def test_chlorinator_custom_divisor_overrides_default() -> None:
    """A `sensor_divisors` feature overrides the per-type default divisor (line 112)."""
    # Default ph divisor is 100; override it to 50 via the device registry feature.
    device = _pinned_device(
        DEVICE_ID,
        device_type="chlorinator",
        features={"sensor_divisors": {"ph": 50}},
        components={"165": {"reportedValue": 360}},
    )
    sensor = FluidraChlorinatorSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID, "ph", 165)
    assert sensor._divisor == 50
    # 360 / 50 = 7.2 (would be 3.6 with the default divisor of 100).
    assert sensor.native_value == pytest.approx(7.2)


def test_chlorinator_custom_divisor_ignored_for_other_types() -> None:
    """A `sensor_divisors` map that doesn't list this type leaves the default in place."""
    device = _pinned_device(
        DEVICE_ID,
        device_type="chlorinator",
        features={"sensor_divisors": {"orp": 2}},
        components={"165": {"reportedValue": 731}},
    )
    sensor = FluidraChlorinatorSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID, "ph", 165)
    # ph not in the override map -> keeps default divisor 100.
    assert sensor._divisor == 100
    assert sensor.native_value == pytest.approx(7.31)


def test_chlorinator_device_data_empty_when_coordinator_data_none() -> None:
    """device_data degrades to {} when coordinator.data is None (line 118)."""
    coord = MagicMock()
    coord.data = None
    coord.last_update_success = True
    sensor = FluidraChlorinatorSensor(coord, SimpleNamespace(), POOL_ID, DEVICE_ID, "ph", 165)
    assert sensor.device_data == {}
    assert sensor.native_value is None
    assert sensor.available is False


def test_chlorinator_device_data_empty_when_device_not_found() -> None:
    """device_data returns {} when the pool exists but the device_id isn't present (line 125)."""
    other = _pinned_device("OTHER-DEV", device_type="chlorinator", components={"165": {"reportedValue": 720}})
    sensor = FluidraChlorinatorSensor(_coord([other]), SimpleNamespace(), POOL_ID, DEVICE_ID, "ph", 165)
    assert sensor.device_data == {}
    assert sensor.native_value is None


def test_chlorinator_device_info_uses_device_name_and_manufacturer() -> None:
    """device_info pulls the device name + manufacturer from the payload (lines 130-131)."""
    device = _pinned_device(
        DEVICE_ID,
        device_type="chlorinator",
        components={"165": {"reportedValue": 720}},
        name="Salt Cell",
        manufacturer="Hayward",
    )
    sensor = FluidraChlorinatorSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID, "ph", 165)
    info = sensor.device_info
    assert info["name"] == "Salt Cell"
    assert info["manufacturer"] == "Hayward"
    assert info["model"] == "Chlorinator"
    assert (DOMAIN, DEVICE_ID) in info["identifiers"]
    assert info["via_device"] == (DOMAIN, POOL_ID)


def test_chlorinator_device_info_falls_back_to_generated_name() -> None:
    """A device without a name falls back to 'Chlorinator <id>' and the default manufacturer."""
    device = _pinned_device(DEVICE_ID, device_type="chlorinator", components={"165": {"reportedValue": 720}})
    device.pop("name", None)
    sensor = FluidraChlorinatorSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID, "ph", 165)
    info = sensor.device_info
    assert info["name"] == f"Chlorinator {DEVICE_ID}"
    assert info["manufacturer"] == "Fluidra"


def test_chlorinator_extra_state_attributes() -> None:
    """extra_state_attributes exposes component id, type, raw value and divisor (lines 171-174)."""
    device = _pinned_device(
        DEVICE_ID,
        device_type="chlorinator",
        components={"170": {"reportedValue": 654}},
    )
    sensor = FluidraChlorinatorSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID, "orp", 170)
    attrs = sensor.extra_state_attributes
    assert attrs["component_id"] == 170
    assert attrs["sensor_type"] == "orp"
    assert attrs["raw_value"] == 654
    assert attrs["divisor"] == 1
    assert attrs["device_id"] == DEVICE_ID


def test_chlorinator_extra_state_attributes_raw_value_none_when_missing() -> None:
    """When the component is absent, raw_value is None but the other keys still populate."""
    device = _pinned_device(DEVICE_ID, device_type="chlorinator", components={})
    sensor = FluidraChlorinatorSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID, "ph", 165)
    attrs = sensor.extra_state_attributes
    assert attrs["raw_value"] is None
    assert attrs["component_id"] == 165
    assert attrs["divisor"] == 100


async def test_setup_falls_back_to_get_pools_when_no_cache() -> None:
    """When cached_pools is empty, setup awaits api.get_pools()."""
    device = _pinned_device(DEVICE_ID, entities=["sensor_info"])
    pool = {"id": POOL_ID, "name": "Pool", "devices": [device]}
    coordinator = MagicMock()
    coordinator.data = {POOL_ID: pool}
    coordinator.last_update_success = True
    coordinator.api = SimpleNamespace(
        cached_pools=[],
        get_pools=AsyncMock(return_value=[pool]),
    )
    added = await _run_setup(coordinator)
    coordinator.api.get_pools.assert_awaited_once()
    assert any(isinstance(e, FluidraDeviceInfoSensor) for e in added)


# --------------------------------------------------------------------------- #
# Victoria VS read-side sensors (Issue #144) — power, head, speed attributes   #
# --------------------------------------------------------------------------- #


def test_pump_power_sensor_reports_watts() -> None:
    """pump_power (Victoria c22) is exposed as a W measurement."""
    device = _pinned_device(DEVICE_ID, pump_power=719)
    sensor = FluidraPumpPowerSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID)
    assert sensor.native_value == 719
    assert sensor.native_unit_of_measurement == "W"
    assert sensor.unique_id == f"{DOMAIN}_{POOL_ID}_{DEVICE_ID}_sensor_power"


def test_pump_power_sensor_none_until_reported() -> None:
    device = _pinned_device(DEVICE_ID)
    sensor = FluidraPumpPowerSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID)
    assert sensor.native_value is None


def test_pump_head_sensor_reports_metres() -> None:
    """pump_head (Victoria c24, converted cm → m by the coordinator)."""
    device = _pinned_device(DEVICE_ID, pump_head=11.97)
    sensor = FluidraPumpHeadSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID)
    assert sensor.native_value == 11.97
    assert sensor.native_unit_of_measurement == "m"
    assert sensor.unique_id == f"{DOMAIN}_{POOL_ID}_{DEVICE_ID}_sensor_head"


def test_pump_head_sensor_none_until_reported() -> None:
    device = _pinned_device(DEVICE_ID)
    sensor = FluidraPumpHeadSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID)
    assert sensor.native_value is None


def test_pump_flow_sensor_reports_cubic_metres_per_hour() -> None:
    """pump_flow (Victoria c25) is exposed as a m³/h volume-flow measurement."""
    device = _pinned_device(DEVICE_ID, pump_flow=7.2)
    sensor = FluidraPumpFlowSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID)
    assert sensor.native_value == 7.2
    assert sensor.native_unit_of_measurement == "m³/h"
    assert sensor.unique_id == f"{DOMAIN}_{POOL_ID}_{DEVICE_ID}_sensor_flow"


def test_pump_flow_sensor_none_until_reported() -> None:
    device = _pinned_device(DEVICE_ID)
    sensor = FluidraPumpFlowSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID)
    assert sensor.native_value is None


def test_pump_speed_attributes_include_victoria_setpoint() -> None:
    """Victoria pumps surface mode + setpoint (SPEED % or FLOW m³/h) as attributes."""
    device = _pinned_device(
        DEVICE_ID,
        is_running=True,
        speed_percent=62,
        pump_mode="QUICK FUNCTION",
        pump_setpoint=7,
        pump_setpoint_type="FLOW",
    )
    sensor = FluidraPumpSpeedSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID)
    attrs = sensor.extra_state_attributes
    assert attrs["pump_mode"] == "QUICK FUNCTION"
    assert attrs["setpoint"] == 7
    assert attrs["setpoint_type"] == "FLOW"


def test_pump_speed_attributes_omit_victoria_fields_for_other_pumps() -> None:
    """Non-Victoria pumps keep the historical attribute set untouched."""
    device = _pinned_device(DEVICE_ID, is_running=True, speed_percent=40)
    attrs = FluidraPumpSpeedSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID).extra_state_attributes
    assert "pump_mode" not in attrs
    assert "setpoint" not in attrs
    assert "setpoint_type" not in attrs


async def test_setup_creates_power_and_head_sensors_when_configured() -> None:
    """The sensor_power / sensor_head entity keys wire the Victoria telemetry sensors."""
    device = _pinned_device(
        DEVICE_ID,
        entities=["sensor_power", "sensor_head"],
        device_type="pump",
    )
    coordinator = _setup_coordinator([device])
    added = await _run_setup(coordinator)
    assert any(isinstance(e, FluidraPumpPowerSensor) for e in added)
    assert any(isinstance(e, FluidraPumpHeadSensor) for e in added)


async def test_setup_creates_flow_sensor_when_configured() -> None:
    """The sensor_flow entity key wires the Victoria flow-rate sensor."""
    device = _pinned_device(DEVICE_ID, entities=["sensor_flow"], device_type="pump")
    coordinator = _setup_coordinator([device])
    added = await _run_setup(coordinator)
    assert any(isinstance(e, FluidraPumpFlowSensor) for e in added)
