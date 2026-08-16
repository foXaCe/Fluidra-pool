"""Tests for the sensor platform classes (device-level + pool-level + chlorinator)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from custom_components.fluidra_pool.sensor import (
    FluidraChlorinatorSensor,
    FluidraLightBrightnessSensor,
    FluidraPoolLocationSensor,
    FluidraPoolStatusSensor,
    FluidraPoolWeatherSensor,
    FluidraTemperatureSensor,
)

POOL_ID = "pool-1"
DEVICE_ID = "TEST-DEV-001"


def _coord(devices: list[dict], pool_extra: dict | None = None) -> Any:
    coordinator = MagicMock()
    pool = {"id": POOL_ID, "name": "Pool", "devices": devices}
    pool.update(pool_extra or {})
    coordinator.data = {POOL_ID: pool}
    coordinator.last_update_success = True
    return coordinator


def _pinned_device(device_id: str, components: dict | None = None, **extra: Any) -> dict:
    comp7 = ""
    components = components or {}
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
                device_type="generic",
                features={},
                components_range=25,
                required_components=[0, 1, 2, 3],
                entities=[],
            ),
        },
    }
    device.update(extra)
    return device


# --- FluidraTemperatureSensor (device-level) ----------------------------


@pytest.mark.parametrize(
    ("sensor_type", "field"),
    [
        ("current", "current_temperature"),
        ("target", "target_temperature"),
        ("water", "water_temperature"),
        ("air", "air_temperature"),
    ],
)
def test_temperature_sensor_reads_matching_device_field(sensor_type: str, field: str) -> None:
    """Each temperature variant reads the right key from the device payload."""
    device = _pinned_device(DEVICE_ID)
    device[field] = 23.4
    sensor = FluidraTemperatureSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID, sensor_type)
    assert sensor.native_value == 23.4


def test_temperature_sensor_unknown_type_returns_none() -> None:
    """An unknown sensor_type yields None instead of raising."""
    device = _pinned_device(DEVICE_ID, current_temperature=20.0)
    sensor = FluidraTemperatureSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID, "garbage")
    assert sensor.native_value is None


# --- FluidraPoolSensorEntity base (inherited from FluidraPoolEntity) ----


def test_sensor_unique_id_format_is_frozen() -> None:
    """unique_id format must stay `{DOMAIN}_{pool}_{device}_sensor[_type]` verbatim."""
    device = _pinned_device("d1")
    sensor = FluidraTemperatureSensor(_coord([device], None), SimpleNamespace(), "p1", "d1", "ph")
    assert sensor.unique_id == "fluidra_pool_p1_d1_sensor_ph"

    sensor_no_type = FluidraTemperatureSensor(_coord([device], None), SimpleNamespace(), "p1", "d1", "")
    assert sensor_no_type.unique_id == "fluidra_pool_p1_d1_sensor"


def test_sensor_device_info_exposes_firmware() -> None:
    """device_info now inherits sw_version from FluidraPoolEntity (was missing before)."""
    device = _pinned_device(DEVICE_ID, firmware_version_component="1.23")
    sensor = FluidraTemperatureSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID, "current")
    assert sensor.device_info["sw_version"] == "1.23"


# --- FluidraLightBrightnessSensor ----------------------------------------


def test_brightness_sensor_uses_device_field() -> None:
    """Brightness sensor reads the device's `brightness` field (already 0-100%)."""
    device = _pinned_device(DEVICE_ID, brightness=75)
    sensor = FluidraLightBrightnessSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID)
    assert sensor.native_value == 75


# --- FluidraPoolWeatherSensor (Kelvin → Celsius) -------------------------


def test_weather_sensor_converts_kelvin_to_celsius_rounded_to_one_decimal() -> None:
    """OpenWeather returns Kelvin; the sensor exposes Celsius rounded to 1dp."""
    coord = _coord(
        [],
        pool_extra={
            "status_data": {
                "weather": {
                    "status": "ok",
                    "value": {"current": {"main": {"temp": 295.65}}},
                }
            }
        },
    )
    sensor = FluidraPoolWeatherSensor(coord, SimpleNamespace(), POOL_ID)
    # 295.65 - 273.15 = 22.5
    assert sensor.native_value == 22.5


def test_weather_sensor_returns_none_when_status_not_ok() -> None:
    """A non-ok weather payload yields None (no fallback to stale data)."""
    coord = _coord(
        [],
        pool_extra={"status_data": {"weather": {"status": "stale", "value": {}}}},
    )
    sensor = FluidraPoolWeatherSensor(coord, SimpleNamespace(), POOL_ID)
    assert sensor.native_value is None


def test_weather_sensor_returns_none_when_payload_missing() -> None:
    """Missing weather block doesn't raise — just returns None."""
    coord = _coord([])
    sensor = FluidraPoolWeatherSensor(coord, SimpleNamespace(), POOL_ID)
    assert sensor.native_value is None


# --- FluidraPoolStatusSensor --------------------------------------------


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("using", "using"),
        ("maintenance", "maintenance"),
        ("offline", "offline"),
        ("winterized", "winterized"),
    ],
)
def test_pool_status_passes_through_known_states(state: str, expected: str) -> None:
    """Known Fluidra pool states are exposed as-is (HA translation key)."""
    coord = _coord([], pool_extra={"state": state})
    sensor = FluidraPoolStatusSensor(coord, SimpleNamespace(), POOL_ID)
    assert sensor.native_value == expected


def test_pool_status_falls_back_to_connected_when_unknown_but_named() -> None:
    """Unrecognised state with a pool name still surfaces something useful."""
    coord = _coord([], pool_extra={"state": "weird-future-state"})
    sensor = FluidraPoolStatusSensor(coord, SimpleNamespace(), POOL_ID)
    assert sensor.native_value == "connected"


def test_pool_status_unknown_when_no_data() -> None:
    """Without state or pool name the status is unknown_state."""
    coord = MagicMock()
    coord.data = {POOL_ID: {}}
    coord.last_update_success = True
    sensor = FluidraPoolStatusSensor(coord, SimpleNamespace(), POOL_ID)
    assert sensor.native_value == "unknown_state"


# --- FluidraPoolLocationSensor ------------------------------------------


def test_pool_location_joins_locality_and_country() -> None:
    """Location is rendered as `Locality, CC` when both are present."""
    coord = _coord(
        [],
        pool_extra={"geolocation": {"locality": "Sainte-Flaive-des-Loups", "countryCode": "FR"}},
    )
    sensor = FluidraPoolLocationSensor(coord, SimpleNamespace(), POOL_ID)
    assert sensor.native_value == "Sainte-Flaive-des-Loups, FR"


def test_pool_location_falls_back_to_partial_data() -> None:
    """Country-only or locality-only render without the comma."""
    coord = _coord([], pool_extra={"geolocation": {"countryCode": "FR"}})
    sensor = FluidraPoolLocationSensor(coord, SimpleNamespace(), POOL_ID)
    assert sensor.native_value == "FR"


def test_pool_location_unknown_when_missing() -> None:
    """No geolocation at all yields the literal "Unknown"."""
    coord = _coord([])
    sensor = FluidraPoolLocationSensor(coord, SimpleNamespace(), POOL_ID)
    assert sensor.native_value == "Unknown"


# --- FluidraChlorinatorSensor -------------------------------------------


@pytest.mark.parametrize(
    ("sensor_type", "component_id", "raw_value", "expected"),
    [
        ("ph", 165, 731, 7.31),
        ("orp", 170, 654, 654.0),
        ("temperature", 172, 260, 26.0),
        ("salinity", 174, 627, 6.27),
        ("free_chlorine", 178, 150, 1.5),
        ("chlorination_actual", 154, 80, 80.0),
        ("battery_voltage", 19, 4116, 4116.0),
    ],
)
def test_chlorinator_sensor_applies_per_type_divisor(
    sensor_type: str, component_id: int, raw_value: int, expected: float
) -> None:
    """Each chlorinator sensor type applies the right divisor to the raw component."""
    device = _pinned_device(DEVICE_ID, components={str(component_id): {"reportedValue": raw_value}})
    sensor = FluidraChlorinatorSensor(
        _coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID, sensor_type, component_id
    )
    assert sensor.native_value == pytest.approx(expected)


def test_chlorinator_battery_voltage_is_diagnostic_millivolt_sensor() -> None:
    """The Blue Connect battery voltage sensor is a diagnostic mV voltage sensor (Issue #138)."""
    from homeassistant.components.sensor import SensorDeviceClass
    from homeassistant.const import EntityCategory, UnitOfElectricPotential

    device = _pinned_device(DEVICE_ID, components={"19": {"reportedValue": 4104}})
    sensor = FluidraChlorinatorSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID, "battery_voltage", 19)
    assert sensor.native_value == pytest.approx(4104.0)
    assert sensor.native_unit_of_measurement == UnitOfElectricPotential.MILLIVOLT
    assert sensor.device_class is SensorDeviceClass.VOLTAGE
    assert sensor.entity_category is EntityCategory.DIAGNOSTIC
    assert sensor.unique_id == f"fluidra_{DEVICE_ID}_battery_voltage"


def test_chlorinator_sensor_returns_none_when_no_reading() -> None:
    """Missing reportedValue returns None rather than zero (HA shows unavailable)."""
    device = _pinned_device(DEVICE_ID, components={})
    sensor = FluidraChlorinatorSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID, "ph", 165)
    assert sensor.native_value is None


def test_chlorinator_sensor_returns_none_on_non_numeric_value() -> None:
    """Unparsable readings (e.g. unexpected string) degrade gracefully to None."""
    device = _pinned_device(DEVICE_ID, components={"165": {"reportedValue": "n/a"}})
    sensor = FluidraChlorinatorSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID, "ph", 165)
    assert sensor.native_value is None


def test_chlorinator_sensor_available_when_components_present() -> None:
    """Bridged chlorinators reporting online=False are still available if components arrive.

    Regression guard for Issue #63: gating on `online` left every sensor permanently
    unavailable on bridged `.nn_*` children.
    """
    device = _pinned_device(
        DEVICE_ID,
        components={"165": {"reportedValue": 720}},
        online=False,  # The bridged child mis-reports itself as offline.
    )
    sensor = FluidraChlorinatorSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID, "ph", 165)
    assert sensor.available is True


def test_chlorinator_sensor_unavailable_when_no_components() -> None:
    """Without component data the sensor is correctly unavailable."""
    device = _pinned_device(DEVICE_ID, components={})
    sensor = FluidraChlorinatorSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID, "ph", 165)
    assert sensor.available is False


def test_chlorinator_orp_zero_reads_unknown() -> None:
    """A flat 0 mV ORP means no probe is connected (eXO iQ LS) — Issue #111.

    An immersed redox probe never reads exactly 0 mV, so the sensor reports
    None ("unknown") instead of a misleading 0.
    """
    device = _pinned_device(DEVICE_ID, components={"63": {"reportedValue": 0}})
    sensor = FluidraChlorinatorSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID, "orp", 63)
    assert sensor.native_value is None


def test_chlorinator_orp_nonzero_reads_value() -> None:
    """A real ORP reading is still reported unchanged (regression guard for #111)."""
    device = _pinned_device(DEVICE_ID, components={"63": {"reportedValue": 738}})
    sensor = FluidraChlorinatorSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID, "orp", 63)
    assert sensor.native_value == pytest.approx(738.0)


def test_chlorinator_zero_non_orp_sensor_still_reads_zero() -> None:
    """The zero-suppression is ORP-only: other sensors keep a legitimate 0 reading."""
    device = _pinned_device(DEVICE_ID, components={"178": {"reportedValue": 0}})
    sensor = FluidraChlorinatorSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID, "free_chlorine", 178)
    assert sensor.native_value == pytest.approx(0.0)


def _device_with_sensors_feature(device_id: str, components: dict, sensors: dict) -> dict:
    """Device pinned to a profile whose `sensors` feature maps the given components."""
    device = _pinned_device(device_id, components=components)
    device["_identify_cache"]["config"] = SimpleNamespace(
        device_type="chlorinator",
        features={"sensors": sensors},
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=[],
    )
    return device


def test_chlorinator_sensor_follows_profile_reroute() -> None:
    """A pH sensor built on the generic mapping (c172) follows the tecnoLC2 re-route.

    Regression for Issue #156: an unknown tecnoLC2 chlorinator is first identified
    as the generic catch-all (pH on c172) so its pH sensor is created with
    component 172; once c8/c172 are scanned the device re-routes to the tecnoLC2
    signature profile (pH on c165), but the entity is never rebuilt. The sensor
    must resolve its component from the *current* profile and read c165 (7.41),
    not keep reading the water temperature on c172 (2.61) forever.
    """
    device = _device_with_sensors_feature(
        DEVICE_ID,
        components={
            "172": {"reportedValue": 261},  # water temperature, would read as pH 2.61
            "165": {"reportedValue": 741},  # the real pH
        },
        sensors={"ph": 165, "orp": 170, "temperature": 172, "salinity": 174},
    )
    # Entity created with the stale generic component 172.
    sensor = FluidraChlorinatorSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID, "ph", 172)
    assert sensor.native_value == pytest.approx(7.41)
    assert sensor.extra_state_attributes["component_id"] == 165


def test_chlorinator_sensor_stable_profile_uses_creation_component() -> None:
    """When the profile maps the sensor to its creation component, nothing changes."""
    device = _device_with_sensors_feature(
        DEVICE_ID,
        components={"165": {"reportedValue": 730}},
        sensors={"ph": 165},
    )
    sensor = FluidraChlorinatorSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID, "ph", 165)
    assert sensor.native_value == pytest.approx(7.30)


def test_chlorinator_sensor_falls_back_when_type_absent_from_profile() -> None:
    """A sensor type missing from the current profile keeps its creation component."""
    device = _device_with_sensors_feature(
        DEVICE_ID,
        components={"178": {"reportedValue": 150}},
        sensors={"ph": 165},  # no free_chlorine mapping
    )
    sensor = FluidraChlorinatorSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID, "free_chlorine", 178)
    assert sensor.native_value == pytest.approx(1.5)


def test_chlorinator_ph_zero_reads_unknown() -> None:
    """A pH of exactly 0 is impossible for real pool water (Issue #129).

    On some bridges the pH register only echoes the setpoint and clears to 0
    when the dosing pump is idle, so 0 means "no live reading", not pH 0.0.
    """
    device = _pinned_device(DEVICE_ID, components={"8": {"reportedValue": 0}})
    sensor = FluidraChlorinatorSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID, "ph", 8)
    assert sensor.native_value is None


def test_chlorinator_salinity_zero_reads_unknown() -> None:
    """A running salt cell never reads exactly 0 g/L — a frozen 0 means no reading (Issue #129).

    With no prior real reading captured (e.g. right after an HA restart),
    there is nothing to fall back to, so this stays "unknown" — see
    test_chlorinator_salinity_zero_falls_back_to_last_known_value for the
    case where a real reading was seen earlier in the same session.
    """
    device = _pinned_device(DEVICE_ID, components={"185": {"reportedValue": 0}})
    sensor = FluidraChlorinatorSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID, "salinity", 185)
    assert sensor.native_value is None


def test_chlorinator_salinity_zero_falls_back_to_last_known_value() -> None:
    """Salinity holds the last real reading while production is too low to measure.

    Unlike ORP/pH, a salinity probe reading 0 is a *temporary* condition
    (Fluidra documents it as chlorination production dropping below ~40%,
    not a hardware/echo issue), so a dashboard gauge is better served by the
    last real value than by going blank every time production dips.
    """
    device = _pinned_device(DEVICE_ID, components={"185": {"reportedValue": 467}})
    sensor = FluidraChlorinatorSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID, "salinity", 185)
    sensor._update_last_known_value()  # a trustworthy poll happened
    assert sensor.native_value == pytest.approx(4.67)

    # Production drops: the component now reports 0.
    device["components"] = {"185": {"reportedValue": 0}}
    sensor._update_last_known_value()  # no-op: 0 is not a real reading
    assert sensor.native_value == pytest.approx(4.67)


def test_chlorinator_salinity_extra_state_attributes_expose_last_known() -> None:
    """The salinity sensor exposes last_known_value/at and low_production; others don't."""
    device = _pinned_device(DEVICE_ID, components={"185": {"reportedValue": 467}})
    sensor = FluidraChlorinatorSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID, "salinity", 185)
    sensor._update_last_known_value()

    device["components"] = {"185": {"reportedValue": 0}}
    attrs = sensor.extra_state_attributes
    assert attrs["last_known_value"] == pytest.approx(4.67)
    assert attrs["last_known_at"] is not None
    assert attrs["low_production"] is True

    orp_device = _pinned_device(DEVICE_ID, components={"63": {"reportedValue": 738}})
    orp_sensor = FluidraChlorinatorSensor(_coord([orp_device]), SimpleNamespace(), POOL_ID, DEVICE_ID, "orp", 63)
    assert "last_known_value" not in orp_sensor.extra_state_attributes


def test_chlorinator_orp_zero_is_excluded_from_last_known_snapshot() -> None:
    """A raw 0 on ORP means "no probe", not a real reading (Issue #111) -- excluded
    from the snapshot just like salinity's low-production 0, via the shared
    _ZERO_IS_NOT_A_REAL_READING set. See test_last_known_at_tracked_for_non_guarded_sensor_types
    for the non-zero case, which ORP (and every other sensor_type) does track.
    """
    device = _pinned_device(DEVICE_ID, components={"63": {"reportedValue": 0}})
    sensor = FluidraChlorinatorSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID, "orp", 63)
    sensor._update_last_known_value()
    assert sensor._last_known_value is None
    assert sensor._last_known_at is None


# --- Chlorinator staleness guard: chlorination_actual + salinity offline (2026-08-11) ---


def test_chlorination_actual_falls_back_to_last_known_when_offline() -> None:
    """With the device offline, chlorination_actual must not echo a frozen cached
    reportedValue as if it were live -- fall back to the last confirmed reading,
    mirroring the offline guard already proven for the chlorinator alarm sensor
    (PR #170/#173). Confirmed live against real hardware 2026-08-11: with the
    chlorinator physically powered off, a direct cache-bypassing API call for
    component 154 returned the exact same timestamp as the coordinator's cached
    copy -- the cached reportedValue is stale, not a live reading.
    """
    device = _pinned_device(DEVICE_ID, components={"154": {"reportedValue": 60}}, online=True)
    sensor = FluidraChlorinatorSensor(
        _coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID, "chlorination_actual", 154
    )
    sensor._update_last_known_value()  # trustworthy poll while online
    assert sensor.native_value == pytest.approx(60.0)

    # Device goes offline; the cloud keeps echoing the last cached reportedValue.
    device["online"] = False
    assert sensor.native_value == pytest.approx(60.0)  # falls back to last known, not a live echo
    assert sensor.extra_state_attributes["device_offline"] is True
    assert sensor.extra_state_attributes["last_known_value"] == pytest.approx(60.0)


def test_chlorination_actual_zero_is_a_real_reading_not_excluded_from_snapshot() -> None:
    """Unlike salinity, chlorination_actual's 0 means the cell is genuinely idle
    (by ORP/CLI regulation), not a measurement gap -- it must still be snapshotted
    as the last known value, not skipped like salinity's 0.
    """
    device = _pinned_device(DEVICE_ID, components={"154": {"reportedValue": 0}}, online=True)
    sensor = FluidraChlorinatorSensor(
        _coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID, "chlorination_actual", 154
    )
    sensor._update_last_known_value()
    assert sensor._last_known_value == pytest.approx(0.0)

    device["online"] = False
    assert sensor.native_value == pytest.approx(0.0)


def test_chlorination_actual_unknown_when_never_polled_and_offline() -> None:
    """No prior trustworthy reading and offline right now -> unknown, not 0 or a guess."""
    device = _pinned_device(DEVICE_ID, components={"154": {"reportedValue": 60}}, online=False)
    sensor = FluidraChlorinatorSensor(
        _coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID, "chlorination_actual", 154
    )
    assert sensor.native_value is None


def test_chlorination_actual_last_update_success_false_also_falls_back() -> None:
    """A coordinator update that failed outright is just as untrustworthy as
    online=False -- same fallback applies (mirrors the alarm sensor's guard).
    """
    device = _pinned_device(DEVICE_ID, components={"154": {"reportedValue": 60}}, online=True)
    coordinator = _coord([device])
    sensor = FluidraChlorinatorSensor(coordinator, SimpleNamespace(), POOL_ID, DEVICE_ID, "chlorination_actual", 154)
    sensor._update_last_known_value()
    assert sensor.native_value == pytest.approx(60.0)

    coordinator.last_update_success = False
    assert sensor.native_value == pytest.approx(60.0)  # falls back, not an untrustworthy echo


def test_salinity_last_known_at_does_not_re_stamp_while_offline_with_cached_nonzero_value() -> None:
    """Regression for the staleness bug in the original PR #187 salinity guard:
    _update_last_known_value must not treat an untrustworthy poll as confirming
    freshness. Before this fix, a device gone offline with a frozen non-zero
    cached reportedValue (proven live 2026-08-11: salinity stayed at 4.69 g/L,
    unchanged by a direct API call, 12+ hours after power-off) would silently
    re-stamp last_known_at on every coordinator cycle, making stale data look
    freshly confirmed.
    """
    device = _pinned_device(DEVICE_ID, components={"185": {"reportedValue": 467}}, online=True)
    sensor = FluidraChlorinatorSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID, "salinity", 185)
    sensor._update_last_known_value()  # trustworthy poll while online
    assert sensor.native_value == pytest.approx(4.67)
    frozen_at = sensor._last_known_at
    assert frozen_at is not None

    # Device goes offline; cloud keeps echoing the same non-zero cached value --
    # this used to slip past the salinity guard entirely, since it only ever
    # checked `value == 0`.
    device["online"] = False
    sensor._update_last_known_value()  # must be a no-op: the poll is untrustworthy
    assert sensor._last_known_at == frozen_at
    assert sensor.native_value == pytest.approx(4.67)
    assert sensor.extra_state_attributes["device_offline"] is True


def test_salinity_offline_fallback_applies_even_without_a_zero_reading() -> None:
    """The offline guard must catch a frozen *non-zero* cached value too --
    the pre-fix code only ever checked `value == 0`, so a device that went
    offline while reporting a plausible non-zero salinity slipped through
    with no staleness signal at all (the exact scenario proven live
    2026-08-11 against real hardware).
    """
    device = _pinned_device(DEVICE_ID, components={"185": {"reportedValue": 467}}, online=False)
    sensor = FluidraChlorinatorSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID, "salinity", 185)
    # Never polled while trustworthy -> nothing to fall back to yet.
    assert sensor.native_value is None
    assert sensor.extra_state_attributes["device_offline"] is True


@pytest.mark.parametrize(
    ("sensor_type", "component_id", "reported_value"),
    [
        ("ph", 8, 720),
        ("orp", 63, 738),
        ("free_chlorine", 178, 150),
        ("temperature", 172, 261),
        ("salinity", 185, 467),
        ("chlorination_actual", 154, 60),
        ("conductivity", 15, 1362),
        ("battery_voltage", 4, 4116),
    ],
)
def test_device_offline_attribute_present_on_every_sensor_type(
    sensor_type: str, component_id: int, reported_value: int
) -> None:
    """device_offline is exposed on all eight sensor_type variants, not just
    the two with a last-known-value fallback, so a dashboard/automation can
    check connectivity uniformly across the whole device.
    """
    online_device = _pinned_device(
        DEVICE_ID, components={str(component_id): {"reportedValue": reported_value}}, online=True
    )
    online_sensor = FluidraChlorinatorSensor(
        _coord([online_device]), SimpleNamespace(), POOL_ID, DEVICE_ID, sensor_type, component_id
    )
    assert online_sensor.extra_state_attributes["device_offline"] is False

    offline_device = _pinned_device(
        DEVICE_ID, components={str(component_id): {"reportedValue": reported_value}}, online=False
    )
    offline_sensor = FluidraChlorinatorSensor(
        _coord([offline_device]), SimpleNamespace(), POOL_ID, DEVICE_ID, sensor_type, component_id
    )
    assert offline_sensor.extra_state_attributes["device_offline"] is True


# --- last_known_at tracked on all eight sensor_type (2026-08-12) -----------
#
# Extends the offline-staleness bookkeeping proven above for chlorination_actual
# and salinity to the remaining six sensor_type values, for dashboard consistency.
# Only last_known_at is tracked/exposed for these six -- native_value keeps
# reading device_data verbatim (unchanged), since the raw echo already freezes
# on its own once the device stops reporting (component data isn't refreshed
# while offline), so a value fallback here would just be a second code path to
# the same displayed number. See _STALENESS_GUARDED_TYPES's docstring.


@pytest.mark.parametrize(
    ("sensor_type", "component_id", "reported_value"),
    [
        ("ph", 165, 742),
        ("orp", 63, 738),
        ("free_chlorine", 178, 150),
        ("temperature", 172, 261),
        ("conductivity", 15, 1362),
        ("battery_voltage", 4, 4116),
    ],
)
def test_last_known_at_tracked_for_non_guarded_sensor_types(
    sensor_type: str, component_id: int, reported_value: int
) -> None:
    """A trustworthy poll snapshots last_known_at for every sensor_type, not just
    the two with a value fallback -- but last_known_value is exposed only for
    those two (_STALENESS_GUARDED_TYPES), since native_value never reads it
    for the other six.
    """
    device = _pinned_device(DEVICE_ID, components={str(component_id): {"reportedValue": reported_value}})
    sensor = FluidraChlorinatorSensor(
        _coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID, sensor_type, component_id
    )
    assert sensor._last_known_at is None  # nothing polled yet

    sensor._update_last_known_value()
    assert sensor._last_known_at is not None

    attrs = sensor.extra_state_attributes
    assert attrs["last_known_at"] is not None
    assert "last_known_value" not in attrs


def test_last_known_at_not_re_stamped_while_offline_for_a_non_guarded_type() -> None:
    """Same regression guard proven for salinity, checked here for a sensor_type
    outside _STALENESS_GUARDED_TYPES (temperature): an untrustworthy poll must
    never refresh last_known_at, even though native_value doesn't fall back
    to a stored value for this type -- the timestamp still shouldn't lie about
    freshness.
    """
    device = _pinned_device(DEVICE_ID, components={"172": {"reportedValue": 261}}, online=True)
    sensor = FluidraChlorinatorSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID, "temperature", 172)
    sensor._update_last_known_value()
    frozen_at = sensor._last_known_at
    assert frozen_at is not None

    device["online"] = False
    sensor._update_last_known_value()  # must be a no-op: the poll is untrustworthy
    assert sensor._last_known_at == frozen_at
    assert sensor.extra_state_attributes["last_known_at"] == frozen_at.isoformat()


def test_chlorinator_temperature_zero_is_snapshotted_unlike_orp_and_ph() -> None:
    """temperature has no "fake zero" artifact (unlike salinity/orp/ph), so a
    genuine 0 reading (0 degC) is snapshotted like any other real value.
    """
    device = _pinned_device(DEVICE_ID, components={"172": {"reportedValue": 0}})
    sensor = FluidraChlorinatorSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID, "temperature", 172)
    sensor._update_last_known_value()
    assert sensor._last_known_value == pytest.approx(0.0)
    assert sensor._last_known_at is not None


def test_chlorinator_ph_nonzero_reads_value() -> None:
    """A real pH reading is still reported unchanged (regression guard for #129)."""
    device = _pinned_device(DEVICE_ID, components={"165": {"reportedValue": 742}})
    sensor = FluidraChlorinatorSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID, "ph", 165)
    assert sensor.native_value == pytest.approx(7.42)


def test_chlorinator_temperature_zero_still_reads_zero() -> None:
    """Temperature is not guarded: 0 °C is a legitimate (if cold) reading."""
    device = _pinned_device(DEVICE_ID, components={"172": {"reportedValue": 0}})
    sensor = FluidraChlorinatorSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID, "temperature", 172)
    assert sensor.native_value == pytest.approx(0.0)


# --- Bridged .nn_ child reporting online=False with a live/frozen ts (2026-08-15) ---
#
# Reproduced by @foXaCe against PR #194's review (2026-08-13): a bridged
# chlorinator child that reports online=False on *every* poll, even while
# serving fresh data, was permanently locked out of _STALENESS_GUARDED_TYPES
# -- _poll_is_trustworthy() keyed on `online` alone, so _last_known_value
# never got a first value and native_value stayed unknown forever. The fix
# corroborates an online=False poll against the resolved component's `ts`:
# a value that has genuinely moved since the last poll is trusted even
# though `online` never flips to True; a `ts` that stays frozen (the actual
# disconnected case this guard exists for) is still treated as offline.


def test_bridged_child_offline_flag_is_untrusted_until_ts_is_seen_advancing() -> None:
    """online=False + an advancing component `ts` is trusted; online=False + a
    frozen `ts` (or no `ts` at all yet) is not -- exactly the corroboration
    @foXaCe asked for, checked across three consecutive polls of the same
    always-offline-flagged bridged device.
    """
    device = _pinned_device(DEVICE_ID, components={"185": {"reportedValue": 467, "ts": "t1"}}, online=False)
    sensor = FluidraChlorinatorSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID, "salinity", 185)

    # First sighting: a ts is present, but there is nothing yet to compare it
    # against, so it can't be proven fresh -- same as the pre-fix behaviour,
    # and the same conservative default as a device with no ts field at all.
    sensor._update_last_known_value()
    assert sensor.native_value is None
    assert sensor.extra_state_attributes["device_offline"] is True

    # ts advances on the next poll, together with a new reading: this is now
    # provably live data, not a frozen cached snapshot, even though `online`
    # never flips to True.
    device["components"]["185"] = {"reportedValue": 467, "ts": "t2"}
    sensor._update_last_known_value()
    assert sensor.native_value == pytest.approx(4.67)
    assert sensor.extra_state_attributes["device_offline"] is False
    assert sensor.extra_state_attributes["last_known_value"] == pytest.approx(4.67)

    # ts freezes again (the genuine disconnect this guard exists for): back
    # to untrusted, falling back to the last confirmed-good reading instead
    # of echoing the new but uncorroborated reportedValue.
    device["components"]["185"] = {"reportedValue": 999, "ts": "t2"}
    sensor._update_last_known_value()
    assert sensor.native_value == pytest.approx(4.67)
    assert sensor.extra_state_attributes["device_offline"] is True


def test_bridged_child_online_true_after_offline_resets_ts_corroboration() -> None:
    """Once the device reports online=True again, native_value trusts it
    immediately via the fast path -- the ts corroboration state from the
    offline period must not leak into a false negative once online recovers,
    nor into a false positive if it drops offline again with a stale ts.
    """
    device = _pinned_device(DEVICE_ID, components={"185": {"reportedValue": 300, "ts": "t1"}}, online=False)
    sensor = FluidraChlorinatorSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID, "salinity", 185)
    sensor._update_last_known_value()  # first sighting: untrusted
    assert sensor.native_value is None

    # Device genuinely reconnects.
    device["online"] = True
    device["components"]["185"] = {"reportedValue": 300, "ts": "t1"}  # same ts, now trusted via `online`
    sensor._update_last_known_value()
    assert sensor.native_value == pytest.approx(3.0)
    assert sensor.extra_state_attributes["device_offline"] is False

    # Drops back to online=False with the *same* ts as the last trustworthy
    # poll -- must not be mistaken for an advance just because the baseline
    # predates the reconnect.
    device["online"] = False
    sensor._update_last_known_value()
    assert sensor.native_value == pytest.approx(3.0)  # falls back to last known
    assert sensor.extra_state_attributes["device_offline"] is True
