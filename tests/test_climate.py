"""Tests for the climate platform (heat pump HVAC modes, target temperature, presets)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from homeassistant.components.climate import (
    ATTR_TEMPERATURE,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.exceptions import ServiceValidationError
import pytest

from custom_components.fluidra_pool.climate import FluidraHeatPumpClimate
from custom_components.fluidra_pool.const import (
    LG_PRESET_BOOST_HEATING,
    LG_PRESET_SMART_HEATING,
    Z550_PRESET_SMART,
)

POOL_ID = "pool-1"
DEVICE_ID = "TEST-HP-001"


def _coord(device: dict) -> Any:
    coordinator = MagicMock()
    coordinator.data = {POOL_ID: {"id": POOL_ID, "name": "Pool", "devices": [device]}}
    coordinator.async_request_refresh = AsyncMock()
    coordinator.last_update_success = True
    return coordinator


def _api() -> SimpleNamespace:
    return SimpleNamespace(
        set_heat_pump_temperature=AsyncMock(return_value=True),
        start_pump=AsyncMock(return_value=True),
        stop_pump=AsyncMock(return_value=True),
        control_device_component=AsyncMock(return_value=True),
    )


def _attach_ha(entity) -> None:
    entity.hass = MagicMock()
    entity.async_write_ha_state = MagicMock()


def _pin(device_id: str, features: dict | None = None, **device_extra: Any) -> dict:
    """Build a heat-pump-shaped device with pinned identify_device output."""
    components = device_extra.pop("components", {})
    comp7 = ""
    if "7" in components and isinstance(components["7"], dict):
        comp7 = str(components["7"].get("reportedValue", ""))
    device = {
        "device_id": device_id,
        "name": "Heat Pump",
        "family": "",
        "type": "",
        "model": "",
        "online": True,
        "components": components,
        "_identify_cache": {
            "key": (device_id, "", "", "", comp7),
            "config": SimpleNamespace(
                device_type="heat_pump",
                features=features or {},
                components_range=5,
                required_components=[0, 1, 2, 3],
                entities=[],
            ),
        },
    }
    device.update(device_extra)
    return device


# --- Temperatures ------------------------------------------------------


def test_current_temperature_uses_water_temperature_field() -> None:
    """current_temperature exposes the pool's water temperature, not the air."""
    device = _pin(DEVICE_ID, water_temperature=24.5, air_temperature=30.0)
    climate = FluidraHeatPumpClimate(_coord(device), _api(), POOL_ID, DEVICE_ID)
    _attach_ha(climate)
    assert climate.current_temperature == 24.5


def test_target_temperature_returns_pending_until_server_confirms() -> None:
    """Optimistic target stays visible while the API hasn't echoed it back."""
    device = _pin(DEVICE_ID, target_temperature=28.0)
    climate = FluidraHeatPumpClimate(_coord(device), _api(), POOL_ID, DEVICE_ID)
    _attach_ha(climate)
    climate._pending_temperature = 30.0
    assert climate.target_temperature == 30.0


def test_target_temperature_clears_optimistic_when_server_matches() -> None:
    """When the server confirms the optimistic value, it's dropped."""
    device = _pin(DEVICE_ID, target_temperature=30.0)
    climate = FluidraHeatPumpClimate(_coord(device), _api(), POOL_ID, DEVICE_ID)
    _attach_ha(climate)
    climate._pending_temperature = 30.0
    assert climate.target_temperature == 30.0
    assert climate._pending_temperature is None


# --- min/max/step depending on device flag -----------------------------


@pytest.mark.parametrize(
    ("features", "expected_min", "expected_max", "expected_step"),
    [
        ({}, 10.0, 40.0, 1.0),
        ({"z550_mode": True}, 15.0, 40.0, 1.0),
        ({"z260iq_mode": True}, 7.0, 40.0, 1.0),
    ],
)
def test_min_max_step_depend_on_device_family(
    features: dict, expected_min: float, expected_max: float, expected_step: float
) -> None:
    """Each heat-pump family gets its own temperature envelope."""
    device = _pin(DEVICE_ID, features=features)
    climate = FluidraHeatPumpClimate(_coord(device), _api(), POOL_ID, DEVICE_ID)
    _attach_ha(climate)
    assert climate.min_temp == expected_min
    assert climate.max_temp == expected_max
    assert climate.target_temperature_step == expected_step


# --- supported_features / hvac_modes / preset_modes ---------------------


def test_supported_features_adds_preset_when_supported() -> None:
    """preset_modes feature enables the PRESET_MODE bit on supported_features."""
    base = FluidraHeatPumpClimate(_coord(_pin(DEVICE_ID)), _api(), POOL_ID, DEVICE_ID)
    _attach_ha(base)
    assert ClimateEntityFeature.PRESET_MODE not in base.supported_features
    assert ClimateEntityFeature.TARGET_TEMPERATURE in base.supported_features

    presets = FluidraHeatPumpClimate(
        _coord(_pin(DEVICE_ID, features={"preset_modes": True})), _api(), POOL_ID, DEVICE_ID
    )
    _attach_ha(presets)
    assert ClimateEntityFeature.PRESET_MODE in presets.supported_features


def test_hvac_modes_for_z550_and_z260_include_cool_and_heat_cool() -> None:
    """Z550iQ+ and Z260iQ expose heat / cool / heat_cool on top of off/heat."""
    z550 = FluidraHeatPumpClimate(_coord(_pin(DEVICE_ID, features={"z550_mode": True})), _api(), POOL_ID, DEVICE_ID)
    _attach_ha(z550)
    assert HVACMode.COOL in z550.hvac_modes
    assert HVACMode.HEAT_COOL in z550.hvac_modes

    z260 = FluidraHeatPumpClimate(_coord(_pin(DEVICE_ID, features={"z260iq_mode": True})), _api(), POOL_ID, DEVICE_ID)
    _attach_ha(z260)
    assert HVACMode.COOL in z260.hvac_modes


def test_hvac_modes_default_to_off_and_heat() -> None:
    """A plain heat pump (no z550/z260 flag) only exposes off + heat."""
    climate = FluidraHeatPumpClimate(_coord(_pin(DEVICE_ID)), _api(), POOL_ID, DEVICE_ID)
    _attach_ha(climate)
    assert climate.hvac_modes == [HVACMode.OFF, HVACMode.HEAT]


def test_preset_modes_returns_lg_modes_when_preset_feature_set() -> None:
    """LG heat pumps surface the 7-value preset list."""
    device = _pin(DEVICE_ID, features={"preset_modes": True})
    climate = FluidraHeatPumpClimate(_coord(device), _api(), POOL_ID, DEVICE_ID)
    _attach_ha(climate)
    assert LG_PRESET_SMART_HEATING in climate.preset_modes
    assert LG_PRESET_BOOST_HEATING in climate.preset_modes


def test_preset_modes_empty_when_not_supported() -> None:
    """Pumps without preset support return an empty list (HA hides the dropdown)."""
    climate = FluidraHeatPumpClimate(_coord(_pin(DEVICE_ID)), _api(), POOL_ID, DEVICE_ID)
    _attach_ha(climate)
    assert climate.preset_modes == []


def test_preset_mode_z550_reads_component_17_when_present() -> None:
    """Z550iQ+ preset_mode falls back to component 17 when z550_preset_reported is missing."""
    device = _pin(
        DEVICE_ID,
        features={"z550_mode": True},
        components={"17": {"reportedValue": 0}},
    )
    climate = FluidraHeatPumpClimate(_coord(device), _api(), POOL_ID, DEVICE_ID)
    _attach_ha(climate)
    # 0 maps to 'silence' in Z550_VALUE_TO_PRESET.
    assert climate.preset_mode == "silence"


def test_preset_mode_z550_defaults_when_no_reading() -> None:
    """Without any component data the Z550 preset defaults to smart."""
    device = _pin(DEVICE_ID, features={"z550_mode": True})
    climate = FluidraHeatPumpClimate(_coord(device), _api(), POOL_ID, DEVICE_ID)
    _attach_ha(climate)
    assert climate.preset_mode == Z550_PRESET_SMART


def test_preset_mode_lg_reads_component_14() -> None:
    """LG heat-pump preset comes from component 14's reportedValue."""
    device = _pin(
        DEVICE_ID,
        features={"preset_modes": True},
        components={"14": {"reportedValue": 3}},  # LG_PRESET_BOOST_HEATING
    )
    climate = FluidraHeatPumpClimate(_coord(device), _api(), POOL_ID, DEVICE_ID)
    _attach_ha(climate)
    assert climate.preset_mode == LG_PRESET_BOOST_HEATING


# --- hvac_mode logic ---------------------------------------------------


def test_hvac_mode_off_when_heat_pump_not_running() -> None:
    """Without is_heating / heat_pump_reported the climate reports OFF."""
    device = _pin(DEVICE_ID, is_heating=False, is_running=False)
    climate = FluidraHeatPumpClimate(_coord(device), _api(), POOL_ID, DEVICE_ID)
    _attach_ha(climate)
    assert climate.hvac_mode == HVACMode.OFF


def test_hvac_mode_heat_when_heat_pump_reported_true() -> None:
    """heat_pump_reported truthy → HEAT mode."""
    device = _pin(DEVICE_ID, heat_pump_reported=1)
    climate = FluidraHeatPumpClimate(_coord(device), _api(), POOL_ID, DEVICE_ID)
    _attach_ha(climate)
    assert climate.hvac_mode == HVACMode.HEAT


@pytest.mark.parametrize(
    ("mode_value", "expected"),
    [
        (0, HVACMode.HEAT),
        (3, HVACMode.HEAT),
        (1, HVACMode.COOL),
        (5, HVACMode.COOL),
        (2, HVACMode.HEAT_COOL),
    ],
)
def test_hvac_mode_z260_decodes_mode_value_into_modes(mode_value: int, expected: HVACMode) -> None:
    """Z260iQ decodes component-14 preset into heat / cool / heat_cool."""
    device = _pin(
        DEVICE_ID,
        features={"z260iq_mode": True},
        heat_pump_reported=1,
        z260iq_mode_value=mode_value,
    )
    climate = FluidraHeatPumpClimate(_coord(device), _api(), POOL_ID, DEVICE_ID)
    _attach_ha(climate)
    assert climate.hvac_mode == expected


# --- async_set_temperature -------------------------------------------


async def test_async_set_temperature_rejects_out_of_range() -> None:
    """Setting a temperature outside [min, max] raises ServiceValidationError."""
    device = _pin(DEVICE_ID)
    api = _api()
    climate = FluidraHeatPumpClimate(_coord(device), api, POOL_ID, DEVICE_ID)
    _attach_ha(climate)

    with pytest.raises(ServiceValidationError):
        await climate.async_set_temperature(**{ATTR_TEMPERATURE: 5.0})

    api.set_heat_pump_temperature.assert_not_called()


async def test_async_set_temperature_writes_api_and_keeps_optimistic() -> None:
    """A valid setpoint goes through the API and leaves the optimistic value in place."""
    device = _pin(DEVICE_ID, target_temperature=28.0)
    api = _api()
    climate = FluidraHeatPumpClimate(_coord(device), api, POOL_ID, DEVICE_ID)
    _attach_ha(climate)

    await climate.async_set_temperature(**{ATTR_TEMPERATURE: 30.0})

    api.set_heat_pump_temperature.assert_awaited_once_with(DEVICE_ID, 30.0)
    assert climate._pending_temperature == 30.0
    climate.coordinator.async_request_refresh.assert_awaited_once()


async def test_async_set_temperature_no_op_when_no_temperature_kwarg() -> None:
    """If no `temperature` kwarg is provided the method returns silently."""
    device = _pin(DEVICE_ID)
    api = _api()
    climate = FluidraHeatPumpClimate(_coord(device), api, POOL_ID, DEVICE_ID)
    _attach_ha(climate)

    await climate.async_set_temperature()
    api.set_heat_pump_temperature.assert_not_called()


async def test_async_set_temperature_clears_optimistic_on_api_failure() -> None:
    """When the API rejects the setpoint, the optimistic state is rolled back."""
    device = _pin(DEVICE_ID, target_temperature=28.0)
    api = _api()
    api.set_heat_pump_temperature = AsyncMock(return_value=False)
    climate = FluidraHeatPumpClimate(_coord(device), api, POOL_ID, DEVICE_ID)
    _attach_ha(climate)

    await climate.async_set_temperature(**{ATTR_TEMPERATURE: 30.0})
    assert climate._pending_temperature is None
