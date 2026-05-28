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
