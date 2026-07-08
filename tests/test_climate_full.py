"""Thorough tests for the climate platform (FluidraHeatPumpClimate).

Covers temperatures, min/max/step, hvac_modes/preset_modes/preset_mode,
hvac_mode/hvac_action across the Z550iQ+, LG and Z260iQ families, plus the
async service methods (set_temperature / set_hvac_mode / set_preset_mode)
including their optimistic, unknown-value and API-failure paths, and
async_setup_entry.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.climate import (
    ATTR_TEMPERATURE,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
import pytest

from custom_components.fluidra_pool.api_resilience import FluidraConnectionError
from custom_components.fluidra_pool.climate import (
    FluidraHeatPumpClimate,
    async_setup_entry,
)
from custom_components.fluidra_pool.const import (
    LG_PRESET_BOOST_COOLING,
    LG_PRESET_SMART_COOLING,
    LG_PRESET_SMART_HEAT_COOL,
    LG_PRESET_SMART_HEATING,
    Z260_MAX_TEMP,
    Z260_MIN_TEMP,
    Z550_MAX_TEMP,
    Z550_MIN_TEMP,
    Z550_MODE_AUTO,
    Z550_MODE_COOLING,
    Z550_MODE_HEATING,
    Z550_STATE_COOLING,
    Z550_STATE_HEATING,
    Z550_STATE_IDLE,
    Z550_STATE_NO_FLOW,
)

POOL_ID = "pool-1"
DEVICE_ID = "TEST-HP-FULL"

TIME_MOD = "custom_components.fluidra_pool.climate.time"


# --- helpers -----------------------------------------------------------


def _coord(devices: list[dict]) -> Any:
    coordinator = MagicMock()
    coordinator.data = {POOL_ID: {"id": POOL_ID, "name": "Pool", "devices": devices}}
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


def _pin(
    device_id: str = DEVICE_ID, features: dict | None = None, *, entities: list | None = None, **extra: Any
) -> dict:
    """Build a heat-pump-shaped device with pinned identify_device output."""
    components = extra.pop("components", {})
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
                entities=entities if entities is not None else [],
            ),
        },
    }
    device.update(extra)
    return device


def _make(device: dict, api: SimpleNamespace | None = None) -> FluidraHeatPumpClimate:
    api = api or _api()
    climate = FluidraHeatPumpClimate(_coord([device]), api, POOL_ID, DEVICE_ID)
    _attach_ha(climate)
    return climate


# --- basic identity / unit ---------------------------------------------


def test_unique_id_and_temperature_unit() -> None:
    climate = _make(_pin())
    assert climate.unique_id == f"fluidra_pool_{POOL_ID}_{DEVICE_ID}_climate"
    assert climate.temperature_unit == "°C"


# --- current / target temperature --------------------------------------


def test_current_temperature_none_when_missing() -> None:
    climate = _make(_pin())
    assert climate.current_temperature is None


def test_current_temperature_returns_water_temperature() -> None:
    climate = _make(_pin(water_temperature=26.0))
    assert climate.current_temperature == 26.0


def test_target_temperature_none_without_data() -> None:
    climate = _make(_pin())
    assert climate.target_temperature is None


def test_target_temperature_returns_actual_without_pending() -> None:
    climate = _make(_pin(target_temperature=28.0))
    assert climate.target_temperature == 28.0


def test_target_temperature_optimistic_then_confirmed() -> None:
    climate = _make(_pin(target_temperature=29.0))
    climate._pending_temperature = 31.0
    # Server still reports 29 -> optimistic value shown.
    assert climate.target_temperature == 31.0
    # Now make the server agree -> optimistic cleared on next read.
    climate.coordinator.data[POOL_ID]["devices"][0]["target_temperature"] = 31.0
    assert climate.target_temperature == 31.0
    assert climate._pending_temperature is None


def test_target_temperature_clears_within_tolerance() -> None:
    """A reported value within the 0.05 tolerance confirms the pending value (climate_light_number-4)."""
    climate = _make(_pin(target_temperature=28.0))
    climate._pending_temperature = 28.04  # decidegree quantization noise vs the requested 28.0
    climate._last_action_time = None
    assert climate.target_temperature == 28.0  # confirmed within tolerance → actual shown
    assert climate._pending_temperature is None


async def test_target_temperature_optimistic_expires_after_timeout() -> None:
    """A never-confirmed optimistic temperature clears after the 5s fallback (climate_light_number-4)."""
    api = _api()
    climate = _make(_pin(target_temperature=29.0), api)
    with patch(TIME_MOD) as mock_time:
        mock_time.time.return_value = 1000.0
        await climate.async_set_temperature(**{ATTR_TEMPERATURE: 31.0})
        # Device keeps reporting 29 (clamped / changed elsewhere) → optimistic value shown.
        assert climate.target_temperature == 31.0
        # 6 seconds later the fallback expiry releases the stale optimistic value.
        mock_time.time.return_value = 1006.0
        assert climate.target_temperature == 29.0
        assert climate._pending_temperature is None


# --- min/max/step ------------------------------------------------------


@pytest.mark.parametrize(
    ("features", "lo", "hi", "step"),
    [
        ({}, 10.0, 40.0, 1.0),
        ({"z550_mode": True}, Z550_MIN_TEMP, Z550_MAX_TEMP, 1.0),
        ({"z260iq_mode": True}, Z260_MIN_TEMP, Z260_MAX_TEMP, 1.0),
    ],
)
def test_temperature_envelope(features: dict, lo: float, hi: float, step: float) -> None:
    climate = _make(_pin(features=features))
    assert climate.min_temp == lo
    assert climate.max_temp == hi
    assert climate.target_temperature_step == step


# --- supported_features ------------------------------------------------


def test_supported_features_base_has_target_and_onoff_no_preset() -> None:
    climate = _make(_pin())
    feats = climate.supported_features
    assert ClimateEntityFeature.TARGET_TEMPERATURE in feats
    assert ClimateEntityFeature.TURN_ON in feats
    assert ClimateEntityFeature.TURN_OFF in feats
    assert ClimateEntityFeature.PRESET_MODE not in feats


def test_supported_features_with_preset() -> None:
    climate = _make(_pin(features={"preset_modes": True}))
    assert ClimateEntityFeature.PRESET_MODE in climate.supported_features


# --- hvac_modes / preset_modes -----------------------------------------


def test_hvac_modes_default() -> None:
    assert _make(_pin()).hvac_modes == [HVACMode.OFF, HVACMode.HEAT]


def test_hvac_modes_z550() -> None:
    modes = _make(_pin(features={"z550_mode": True})).hvac_modes
    assert modes == [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL, HVACMode.HEAT_COOL]


def test_hvac_modes_z260() -> None:
    modes = _make(_pin(features={"z260iq_mode": True})).hvac_modes
    assert modes == [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL, HVACMode.HEAT_COOL]


def test_preset_modes_z550_empty() -> None:
    # Z550iQ+ exposes no controllable preset: component 17 is read-only (Issue #88).
    assert _make(_pin(features={"z550_mode": True})).preset_modes == []


def test_preset_modes_lg() -> None:
    presets = _make(_pin(features={"preset_modes": True})).preset_modes
    assert LG_PRESET_SMART_HEATING in presets
    assert LG_PRESET_BOOST_COOLING in presets


def test_preset_modes_empty_default() -> None:
    assert _make(_pin()).preset_modes == []


# --- preset_mode -------------------------------------------------------


def test_preset_mode_z550_none() -> None:
    # Z550iQ+ has no controllable preset (Issue #88): always None regardless of c17.
    climate = _make(_pin(features={"z550_mode": True}, z550_preset_reported=2, components={"17": {"reportedValue": 0}}))
    assert climate.preset_mode is None


def test_preset_mode_lg_from_component_14() -> None:
    climate = _make(_pin(features={"preset_modes": True}, components={"14": {"reportedValue": 5}}))
    assert climate.preset_mode == LG_PRESET_BOOST_COOLING


def test_preset_mode_lg_unknown_reported_falls_back() -> None:
    climate = _make(_pin(features={"preset_modes": True}, components={"14": {"reportedValue": 77}}))
    assert climate.preset_mode == LG_PRESET_SMART_HEATING


def test_preset_mode_lg_fallback_no_component() -> None:
    climate = _make(_pin(features={"preset_modes": True}))
    assert climate.preset_mode == LG_PRESET_SMART_HEATING


def test_preset_mode_none_when_unsupported() -> None:
    assert _make(_pin()).preset_mode is None


def test_preset_mode_pending_optimistic_within_window() -> None:
    climate = _make(_pin(features={"z550_mode": True}, z550_preset_reported=0))
    climate._pending_preset_mode = "boost"
    with patch(TIME_MOD) as mock_time:
        # action time = 1000, now = 1003 (< 5s) -> keep optimistic
        climate._last_preset_action_time = 1000.0
        mock_time.time.return_value = 1003.0
        assert climate.preset_mode == "boost"


def test_preset_mode_pending_optimistic_expires() -> None:
    climate = _make(_pin(features={"z550_mode": True}, z550_preset_reported=0))
    climate._pending_preset_mode = "boost"
    with patch(TIME_MOD) as mock_time:
        climate._last_preset_action_time = 1000.0
        mock_time.time.return_value = 1010.0  # > 5s -> expire, fall through
        assert climate.preset_mode is None  # Z550 has no controllable preset (Issue #88)
    assert climate._pending_preset_mode is None
    assert climate._last_preset_action_time is None


# --- hvac_mode ---------------------------------------------------------


def test_hvac_mode_off_default() -> None:
    climate = _make(_pin(is_heating=False, is_running=False))
    assert climate.hvac_mode == HVACMode.OFF


def test_hvac_mode_heat_pump_reported_true() -> None:
    assert _make(_pin(heat_pump_reported=1)).hvac_mode == HVACMode.HEAT


def test_hvac_mode_heat_pump_reported_false() -> None:
    assert _make(_pin(heat_pump_reported=0)).hvac_mode == HVACMode.OFF


def test_hvac_mode_fallback_is_heating() -> None:
    assert _make(_pin(is_heating=True)).hvac_mode == HVACMode.HEAT


def test_hvac_mode_fallback_is_running() -> None:
    assert _make(_pin(is_running=True)).hvac_mode == HVACMode.HEAT


def test_hvac_mode_pending_optimistic_window() -> None:
    climate = _make(_pin(heat_pump_reported=0))
    climate._pending_hvac_mode = HVACMode.COOL
    with patch(TIME_MOD) as mock_time:
        climate._last_hvac_action_time = 1000.0
        mock_time.time.return_value = 1002.0
        assert climate.hvac_mode == HVACMode.COOL


def test_hvac_mode_pending_optimistic_expires() -> None:
    climate = _make(_pin(heat_pump_reported=1))
    climate._pending_hvac_mode = HVACMode.COOL
    with patch(TIME_MOD) as mock_time:
        climate._last_hvac_action_time = 1000.0
        mock_time.time.return_value = 1010.0
        assert climate.hvac_mode == HVACMode.HEAT  # falls through to reported true
    assert climate._pending_hvac_mode is None


def test_hvac_mode_pending_preset_forces_heat() -> None:
    climate = _make(_pin(heat_pump_reported=0))
    climate._pending_preset_mode = LG_PRESET_SMART_HEATING
    assert climate.hvac_mode == HVACMode.HEAT


def test_hvac_mode_z550_off_when_pump_off() -> None:
    climate = _make(_pin(features={"z550_mode": True}, heat_pump_reported=0))
    assert climate.hvac_mode == HVACMode.OFF


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (Z550_MODE_HEATING, HVACMode.HEAT),
        (Z550_MODE_COOLING, HVACMode.COOL),
        (Z550_MODE_AUTO, HVACMode.HEAT_COOL),
        (None, HVACMode.HEAT),  # unknown mode but pump on -> default HEAT
    ],
)
def test_hvac_mode_z550_decodes_mode(mode, expected) -> None:
    climate = _make(_pin(features={"z550_mode": True}, heat_pump_reported=1, z550_mode_reported=mode))
    assert climate.hvac_mode == expected


def test_hvac_mode_z260_off_when_reported_false() -> None:
    climate = _make(_pin(features={"z260iq_mode": True}, heat_pump_reported=0))
    assert climate.hvac_mode == HVACMode.OFF


@pytest.mark.parametrize(
    ("mode_value", "expected"),
    [
        (0, HVACMode.HEAT),
        (3, HVACMode.HEAT),
        (4, HVACMode.HEAT),
        (1, HVACMode.COOL),
        (5, HVACMode.COOL),
        (6, HVACMode.COOL),
        (2, HVACMode.HEAT_COOL),
    ],
)
def test_hvac_mode_z260_decodes_mode_value(mode_value, expected) -> None:
    climate = _make(_pin(features={"z260iq_mode": True}, heat_pump_reported=1, z260iq_mode_value=mode_value))
    assert climate.hvac_mode == expected


def test_hvac_mode_z260_fallback_heat_when_on_unknown_mode() -> None:
    climate = _make(_pin(features={"z260iq_mode": True}, heat_pump_reported=1))
    assert climate.hvac_mode == HVACMode.HEAT


def test_hvac_mode_lg_from_heat_pump_reported() -> None:
    climate = _make(_pin(features={"preset_modes": True}, heat_pump_reported=1))
    assert climate.hvac_mode == HVACMode.HEAT


def test_hvac_mode_lg_heat_pump_reported_false() -> None:
    climate = _make(_pin(features={"preset_modes": True}, heat_pump_reported=0))
    assert climate.hvac_mode == HVACMode.OFF


def test_hvac_mode_lg_from_pump_reported() -> None:
    climate = _make(_pin(features={"preset_modes": True}, pump_reported=1))
    assert climate.hvac_mode == HVACMode.HEAT


def test_hvac_mode_lg_from_is_running() -> None:
    climate = _make(_pin(features={"preset_modes": True}, is_running=True))
    assert climate.hvac_mode == HVACMode.HEAT


def test_hvac_mode_lg_fallback_off() -> None:
    climate = _make(_pin(features={"preset_modes": True}))
    assert climate.hvac_mode == HVACMode.OFF


# --- hvac_action -------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (Z550_STATE_HEATING, HVACAction.HEATING),
        (Z550_STATE_COOLING, HVACAction.COOLING),
        (Z550_STATE_IDLE, HVACAction.IDLE),
        (Z550_STATE_NO_FLOW, HVACAction.IDLE),  # no flow -> idle (on but blocked, Issue #88)
    ],
)
def test_hvac_action_z550(state, expected) -> None:
    climate = _make(_pin(features={"z550_mode": True}, z550_state_reported=state))
    assert climate.hvac_action == expected


def test_hvac_action_z260_off() -> None:
    climate = _make(_pin(features={"z260iq_mode": True}, heat_pump_reported=0))
    assert climate.hvac_action == HVACAction.OFF


def test_hvac_action_z260_no_flow_idle() -> None:
    climate = _make(_pin(features={"z260iq_mode": True}, heat_pump_reported=1, no_flow_alarm=True))
    assert climate.hvac_action == HVACAction.IDLE


@pytest.mark.parametrize(
    ("mode_value", "expected"),
    [
        (1, HVACAction.COOLING),
        (5, HVACAction.COOLING),
        (0, HVACAction.HEATING),
        (3, HVACAction.HEATING),
        (2, HVACAction.IDLE),  # Smart H+C without temperatures: deadband fallback
    ],
)
def test_hvac_action_z260_by_mode(mode_value, expected) -> None:
    climate = _make(_pin(features={"z260iq_mode": True}, heat_pump_reported=1, z260iq_mode_value=mode_value))
    assert climate.hvac_action == expected


@pytest.mark.parametrize(
    ("water", "target", "expected"),
    [
        (24.0, 28.0, HVACAction.HEATING),  # water 4°C below setpoint
        (30.0, 26.0, HVACAction.COOLING),  # water 4°C above setpoint
        (27.5, 28.0, HVACAction.IDLE),  # inside the ±1.0°C deadband: satisfied
        (28.9, 28.0, HVACAction.IDLE),  # just under the deadband edge
    ],
)
def test_hvac_action_z260_heat_cool_inferred_from_temp_delta(water, target, expected) -> None:
    """Smart H+C (c14=2) infers the running direction from the water-vs-setpoint delta (Issue #139)."""
    climate = _make(
        _pin(
            features={"z260iq_mode": True},
            heat_pump_reported=1,
            z260iq_mode_value=2,
            water_temperature=water,
            target_temperature=target,
        )
    )
    assert climate.hvac_action == expected


def test_hvac_action_z260_default_heating_when_on_no_mode() -> None:
    climate = _make(_pin(features={"z260iq_mode": True}, heat_pump_reported=1))
    assert climate.hvac_action == HVACAction.HEATING


def test_hvac_action_lg_off_when_not_running() -> None:
    climate = _make(_pin(features={"preset_modes": True}, heat_pump_reported=0))
    assert climate.hvac_action == HVACAction.OFF


def test_hvac_action_lg_cooling() -> None:
    climate = _make(
        _pin(features={"preset_modes": True}, heat_pump_reported=1, components={"14": {"reportedValue": 1}})
    )
    assert climate.hvac_action == HVACAction.COOLING


def test_hvac_action_lg_heat_cool_idle() -> None:
    """Without temperatures, LG heat_cool falls back to IDLE (deadband path)."""
    climate = _make(
        _pin(features={"preset_modes": True}, heat_pump_reported=1, components={"14": {"reportedValue": 2}})
    )
    assert climate.hvac_action == HVACAction.IDLE


def test_hvac_action_lg_heat_cool_inferred_from_temp_delta() -> None:
    """LG heat_cool shares the Smart H+C temperature-delta inference (Issue #139)."""
    climate = _make(
        _pin(
            features={"preset_modes": True},
            heat_pump_reported=1,
            components={"14": {"reportedValue": 2}},
            water_temperature=24.0,
            target_temperature=28.0,
        )
    )
    assert climate.hvac_action == HVACAction.HEATING


def test_hvac_action_lg_heating_default() -> None:
    climate = _make(
        _pin(features={"preset_modes": True}, heat_pump_reported=1, components={"14": {"reportedValue": 0}})
    )
    assert climate.hvac_action == HVACAction.HEATING


def test_hvac_action_lg_falls_back_to_pump_reported_and_is_running() -> None:
    climate = _make(_pin(features={"preset_modes": True}, pump_reported=1, components={"14": {"reportedValue": 0}}))
    assert climate.hvac_action == HVACAction.HEATING


def test_hvac_action_lg_falls_back_to_is_running() -> None:
    # heat_pump_reported and pump_reported both absent -> on derived from is_running.
    climate = _make(_pin(features={"preset_modes": True}, is_running=True, components={"14": {"reportedValue": 0}}))
    assert climate.hvac_action == HVACAction.HEATING


def test_hvac_action_standard_heating() -> None:
    climate = _make(_pin(is_heating=True))
    assert climate.hvac_action == HVACAction.HEATING


def test_hvac_action_standard_off() -> None:
    climate = _make(_pin(is_heating=False))
    assert climate.hvac_action == HVACAction.OFF


# --- icon --------------------------------------------------------------


def test_icon_heat_vs_off() -> None:
    on = _make(_pin(heat_pump_reported=1))
    assert on.icon == "mdi:heat-pump"
    off = _make(_pin(heat_pump_reported=0))
    assert off.icon == "mdi:heat-pump-outline"


# --- async_set_temperature ---------------------------------------------


async def test_set_temperature_no_kwarg_noop() -> None:
    api = _api()
    climate = _make(_pin(), api)
    await climate.async_set_temperature()
    api.set_heat_pump_temperature.assert_not_called()


async def test_set_temperature_out_of_range_too_low() -> None:
    api = _api()
    climate = _make(_pin(), api)
    with pytest.raises(ServiceValidationError):
        await climate.async_set_temperature(**{ATTR_TEMPERATURE: 5.0})
    api.set_heat_pump_temperature.assert_not_called()


async def test_set_temperature_out_of_range_too_high() -> None:
    api = _api()
    climate = _make(_pin(), api)
    with pytest.raises(ServiceValidationError):
        await climate.async_set_temperature(**{ATTR_TEMPERATURE: 99.0})
    api.set_heat_pump_temperature.assert_not_called()


async def test_set_temperature_valid_calls_api_and_refreshes() -> None:
    api = _api()
    climate = _make(_pin(target_temperature=28.0), api)
    await climate.async_set_temperature(**{ATTR_TEMPERATURE: 30.0})
    api.set_heat_pump_temperature.assert_awaited_once_with(DEVICE_ID, 30.0)
    assert climate._pending_temperature == 30.0
    climate.coordinator.async_request_refresh.assert_awaited_once()


async def test_set_temperature_api_returns_false_raises_and_reverts() -> None:
    api = _api()
    api.set_heat_pump_temperature = AsyncMock(return_value=False)
    climate = _make(_pin(target_temperature=28.0), api)
    with pytest.raises(HomeAssistantError):
        await climate.async_set_temperature(**{ATTR_TEMPERATURE: 30.0})
    assert climate._pending_temperature is None
    assert climate._last_action_time is None


async def test_set_temperature_api_exception_wrapped() -> None:
    api = _api()
    api.set_heat_pump_temperature = AsyncMock(side_effect=FluidraConnectionError("boom"))
    climate = _make(_pin(target_temperature=28.0), api)
    with pytest.raises(HomeAssistantError):
        await climate.async_set_temperature(**{ATTR_TEMPERATURE: 30.0})
    assert climate._pending_temperature is None


# --- async_set_hvac_mode -----------------------------------------------


async def test_set_hvac_mode_standard_heat_starts_pump() -> None:
    api = _api()
    climate = _make(_pin(), api)
    await climate.async_set_hvac_mode(HVACMode.HEAT)
    api.start_pump.assert_awaited_once_with(DEVICE_ID)
    climate.coordinator.async_request_refresh.assert_awaited_once()


async def test_set_hvac_mode_standard_off_stops_pump() -> None:
    api = _api()
    climate = _make(_pin(), api)
    await climate.async_set_hvac_mode(HVACMode.OFF)
    api.stop_pump.assert_awaited_once_with(DEVICE_ID)


async def test_set_hvac_mode_standard_unsupported_mode_noop() -> None:
    api = _api()
    climate = _make(_pin(), api)
    await climate.async_set_hvac_mode(HVACMode.COOL)
    api.start_pump.assert_not_called()
    api.stop_pump.assert_not_called()
    assert climate._pending_hvac_mode is None


async def test_set_hvac_mode_z550_off_uses_component_21() -> None:
    api = _api()
    climate = _make(_pin(features={"z550_mode": True}), api)
    await climate.async_set_hvac_mode(HVACMode.OFF)
    api.control_device_component.assert_awaited_once_with(DEVICE_ID, 21, 0)


async def test_set_hvac_mode_z550_heat_powers_on_then_sets_mode() -> None:
    api = _api()
    climate = _make(_pin(features={"z550_mode": True}), api)
    await climate.async_set_hvac_mode(HVACMode.HEAT)
    # First call powers on component 21, second sets component 16 to heating.
    assert api.control_device_component.await_args_list[0].args == (DEVICE_ID, 21, 1)
    assert api.control_device_component.await_args_list[1].args == (DEVICE_ID, 16, Z550_MODE_HEATING)


async def test_set_hvac_mode_z550_cool_sets_cooling_mode() -> None:
    api = _api()
    climate = _make(_pin(features={"z550_mode": True}), api)
    await climate.async_set_hvac_mode(HVACMode.COOL)
    assert api.control_device_component.await_args_list[1].args == (DEVICE_ID, 16, Z550_MODE_COOLING)


async def test_set_hvac_mode_z550_heat_cool_sets_auto_mode() -> None:
    api = _api()
    climate = _make(_pin(features={"z550_mode": True}), api)
    await climate.async_set_hvac_mode(HVACMode.HEAT_COOL)
    assert api.control_device_component.await_args_list[1].args == (DEVICE_ID, 16, Z550_MODE_AUTO)


async def test_set_hvac_mode_z550_rolls_back_power_when_mode_write_fails() -> None:
    """If power-on succeeds but the mode write fails, power is rolled back off (climate_light_number-5)."""
    api = _api()
    # power-on (21,1) -> True, mode write (16) -> False, rollback (21,0) -> True
    api.control_device_component = AsyncMock(side_effect=[True, False, True])
    climate = _make(_pin(features={"z550_mode": True}), api)

    with pytest.raises(HomeAssistantError):
        await climate.async_set_hvac_mode(HVACMode.HEAT)

    calls = [c.args for c in api.control_device_component.await_args_list]
    assert calls[0] == (DEVICE_ID, 21, 1)  # power on
    assert calls[1] == (DEVICE_ID, 16, Z550_MODE_HEATING)  # mode write (rejected)
    assert calls[2] == (DEVICE_ID, 21, 0)  # rollback: power back off
    assert climate._pending_hvac_mode is None  # optimistic state reverted


async def test_set_hvac_mode_z260_off_uses_component_13() -> None:
    api = _api()
    climate = _make(_pin(features={"z260iq_mode": True}), api)
    await climate.async_set_hvac_mode(HVACMode.OFF)
    api.control_device_component.assert_awaited_once_with(DEVICE_ID, 13, 0)


async def test_set_hvac_mode_z260_heat_sets_mode_then_powers_on() -> None:
    api = _api()
    climate = _make(_pin(features={"z260iq_mode": True}), api)
    await climate.async_set_hvac_mode(HVACMode.HEAT)
    # component 14 set to a HEAT value, then component 13 powered on.
    first = api.control_device_component.await_args_list[0].args
    second = api.control_device_component.await_args_list[1].args
    assert first[0] == DEVICE_ID
    assert first[1] == 14
    assert first[2] in (0, 3, 4)
    assert second == (DEVICE_ID, 13, 1)


async def test_set_hvac_mode_z260_cool_uses_cool_value() -> None:
    api = _api()
    climate = _make(_pin(features={"z260iq_mode": True}), api)
    await climate.async_set_hvac_mode(HVACMode.COOL)
    first = api.control_device_component.await_args_list[0].args
    assert first[1] == 14
    assert first[2] in (1, 5, 6)


async def test_set_hvac_mode_z260_heat_clamps_cool_preset_to_smart_heat() -> None:
    # current preset (via component 14) is a COOL preset; asking for HEAT must
    # clamp the mode_value back to Smart Heating (component-14 value 0).
    api = _api()
    climate = _make(
        _pin(
            features={"z260iq_mode": True, "preset_modes": True},
            components={"14": {"reportedValue": 1}},  # smart_cooling
        ),
        api,
    )
    await climate.async_set_hvac_mode(HVACMode.HEAT)
    first = api.control_device_component.await_args_list[0].args
    assert first[1] == 14
    assert first[2] == 0


async def test_set_hvac_mode_z260_cool_clamps_heat_preset_to_smart_cool() -> None:
    api = _api()
    climate = _make(
        _pin(
            features={"z260iq_mode": True, "preset_modes": True},
            components={"14": {"reportedValue": 0}},  # smart_heating
        ),
        api,
    )
    await climate.async_set_hvac_mode(HVACMode.COOL)
    first = api.control_device_component.await_args_list[0].args
    assert first[1] == 14
    assert first[2] == 1


async def test_set_hvac_mode_z260_heat_cool_uses_value_2() -> None:
    api = _api()
    climate = _make(_pin(features={"z260iq_mode": True}), api)
    await climate.async_set_hvac_mode(HVACMode.HEAT_COOL)
    first = api.control_device_component.await_args_list[0].args
    assert first[1] == 14
    assert first[2] == 2


async def test_set_hvac_mode_z260_unsupported_noop() -> None:
    api = _api()
    climate = _make(_pin(features={"z260iq_mode": True}), api)
    await climate.async_set_hvac_mode(HVACMode.DRY)
    api.control_device_component.assert_not_called()
    assert climate._pending_hvac_mode is None


async def test_set_hvac_mode_api_returns_false_raises() -> None:
    api = _api()
    api.start_pump = AsyncMock(return_value=False)
    climate = _make(_pin(), api)
    with pytest.raises(HomeAssistantError):
        await climate.async_set_hvac_mode(HVACMode.HEAT)
    assert climate._pending_hvac_mode is None


async def test_set_hvac_mode_api_exception_wrapped() -> None:
    api = _api()
    api.start_pump = AsyncMock(side_effect=FluidraConnectionError("boom"))
    climate = _make(_pin(), api)
    with pytest.raises(HomeAssistantError):
        await climate.async_set_hvac_mode(HVACMode.HEAT)
    assert climate._pending_hvac_mode is None


# --- async_set_preset_mode ---------------------------------------------


async def test_set_preset_mode_z550_noop() -> None:
    # Z550iQ+ has no controllable preset — set_preset_mode must not write (Issue #88).
    api = _api()
    climate = _make(_pin(features={"z550_mode": True}), api)
    await climate.async_set_preset_mode("boost")
    api.control_device_component.assert_not_called()
    assert climate._pending_preset_mode is None


async def test_set_preset_mode_z550_unknown_noop() -> None:
    api = _api()
    climate = _make(_pin(features={"z550_mode": True}), api)
    await climate.async_set_preset_mode("nope")
    api.control_device_component.assert_not_called()
    assert climate._pending_preset_mode is None


async def test_set_preset_mode_lg_valid() -> None:
    api = _api()
    climate = _make(_pin(features={"preset_modes": True}), api)
    await climate.async_set_preset_mode(LG_PRESET_SMART_HEAT_COOL)
    api.control_device_component.assert_awaited_once_with(DEVICE_ID, 14, 2)


async def test_set_preset_mode_lg_unknown_noop() -> None:
    api = _api()
    climate = _make(_pin(features={"preset_modes": True}), api)
    await climate.async_set_preset_mode("unknown_preset")
    api.control_device_component.assert_not_called()
    assert climate._pending_preset_mode is None


async def test_set_preset_mode_unsupported_device_noop() -> None:
    api = _api()
    climate = _make(_pin(), api)
    await climate.async_set_preset_mode(LG_PRESET_SMART_COOLING)
    api.control_device_component.assert_not_called()
    assert climate._pending_preset_mode is None


async def test_set_preset_mode_api_returns_false_raises() -> None:
    api = _api()
    api.control_device_component = AsyncMock(return_value=False)
    climate = _make(_pin(features={"preset_modes": True}), api)
    with pytest.raises(HomeAssistantError):
        await climate.async_set_preset_mode(LG_PRESET_SMART_HEATING)
    assert climate._pending_preset_mode is None


async def test_set_preset_mode_api_exception_wrapped() -> None:
    api = _api()
    api.control_device_component = AsyncMock(side_effect=FluidraConnectionError("boom"))
    climate = _make(_pin(features={"preset_modes": True}), api)
    with pytest.raises(HomeAssistantError):
        await climate.async_set_preset_mode(LG_PRESET_SMART_HEATING)
    assert climate._pending_preset_mode is None


# --- extra_state_attributes --------------------------------------------


def test_extra_state_attributes_z550_branch() -> None:
    climate = _make(
        _pin(
            features={"z550_mode": True},
            water_temperature=25.0,
            air_temperature=30.0,
            z550_mode_reported=1,
            z550_state_reported=11,  # no flow
            running_hours=1234,
            components={
                "21": {"reportedValue": 1},
                "18": {"reportedValue": 1},
                "60": {"reportedValue": 1234},
            },
        )
    )
    attrs = climate.extra_state_attributes
    assert attrs["device_type"] == "heat_pump"
    assert attrs["water_temperature"] == 25.0
    assert attrs["air_temperature"] == 30.0
    assert attrs["z550_mode"] == "cooling"
    assert attrs["z550_state"] == "no_flow"
    assert attrs["no_flow"] is True  # Issue #88
    assert attrs["running_hours"] == 1234  # component 60
    assert attrs["component_18_raw"] == 1
    assert attrs["component_60_raw"] == 1234
    assert "z550_preset" not in attrs  # presets removed (Issue #88)
    assert attrs["component_21_raw"] == 1


def test_extra_state_attributes_z260_branch_and_errors() -> None:
    climate = _make(
        _pin(
            features={"z260iq_mode": True},
            air_temperature=18.0,
            no_flow_alarm=False,
            running_hours=120,
            z260iq_mode_value=2,
            permission_error=True,
            last_control_error="oops",
            firmware_version="1.2.3",
        )
    )
    attrs = climate.extra_state_attributes
    assert attrs["air_temperature"] == 18.0
    assert attrs["no_flow_alarm"] is False
    assert attrs["running_hours"] == 120
    assert attrs["z260iq_mode_raw"] == 2
    assert attrs["permission_error"] is True
    assert attrs["last_control_error"] == "oops"
    assert attrs["firmware_version"] == "1.2.3"


def test_extra_state_attributes_includes_ip_address() -> None:
    climate = _make(_pin(ip_address="10.0.0.5"))
    assert climate.extra_state_attributes["ip_address"] == "10.0.0.5"


# --- availability ------------------------------------------------------


def test_available_true_when_online_and_update_ok() -> None:
    climate = _make(_pin(online=True))
    assert climate.available is True


def test_available_false_when_offline() -> None:
    climate = _make(_pin(online=False))
    assert climate.available is False


# --- async_setup_entry -------------------------------------------------


async def test_async_setup_entry_creates_climate_for_heat_pump() -> None:
    hp = _pin("HP-1", entities=["climate"])
    other = _pin("PUMP-1", entities=["switch"])
    coordinator = MagicMock()
    coordinator.api = SimpleNamespace(
        cached_pools=[{"id": POOL_ID, "devices": [hp, other]}],
        get_pools=AsyncMock(return_value=[]),
    )
    entry = SimpleNamespace(
        runtime_data=SimpleNamespace(coordinator=coordinator),
        async_on_unload=lambda _unsub: None,
    )

    added: list = []

    def _add(entities, *a, **k):
        added.extend(list(entities))

    async_add = MagicMock(side_effect=_add)
    await async_setup_entry(MagicMock(), entry, async_add)

    assert len(added) == 1
    assert isinstance(added[0], FluidraHeatPumpClimate)
    assert added[0]._device_id == "HP-1"


async def test_async_setup_entry_falls_back_to_get_pools() -> None:
    hp = _pin("HP-2", entities=["climate"])
    coordinator = MagicMock()
    coordinator.api = SimpleNamespace(
        cached_pools=[],
        get_pools=AsyncMock(return_value=[{"id": POOL_ID, "devices": [hp]}]),
    )
    entry = SimpleNamespace(
        runtime_data=SimpleNamespace(coordinator=coordinator),
        async_on_unload=lambda _unsub: None,
    )

    added: list = []
    async_add = MagicMock(side_effect=lambda e, *a, **k: added.extend(list(e)))
    await async_setup_entry(MagicMock(), entry, async_add)

    coordinator.api.get_pools.assert_awaited_once()
    assert len(added) == 1
    assert isinstance(added[0], FluidraHeatPumpClimate)


async def test_async_setup_entry_no_climate_entities() -> None:
    pump = _pin("PUMP-2", entities=["switch"])
    coordinator = MagicMock()
    coordinator.api = SimpleNamespace(
        cached_pools=[{"id": POOL_ID, "devices": [pump]}],
        get_pools=AsyncMock(return_value=[]),
    )
    entry = SimpleNamespace(
        runtime_data=SimpleNamespace(coordinator=coordinator),
        async_on_unload=lambda _unsub: None,
    )

    added: list = []
    async_add = MagicMock(side_effect=lambda e, *a, **k: added.extend(list(e)))
    await async_setup_entry(MagicMock(), entry, async_add)
    assert added == []


async def test_setup_adds_new_device_dynamically() -> None:
    """dynamic-devices: a heat pump appearing on a later poll is wired without a reload."""
    hp1 = _pin("HP-DYN-1", entities=["climate"])
    pool = {"id": POOL_ID, "devices": [hp1]}
    coordinator = MagicMock()
    coordinator.api = SimpleNamespace(cached_pools=[pool], get_pools=AsyncMock(return_value=[pool]))
    coordinator.get_pools_from_data = lambda: [{"id": POOL_ID, "devices": pool["devices"]}]
    listeners: list[Any] = []
    coordinator.async_add_listener = lambda cb: listeners.append(cb) or (lambda: None)

    added: list = []
    entry = SimpleNamespace(
        runtime_data=SimpleNamespace(coordinator=coordinator),
        async_on_unload=lambda _unsub: None,
    )
    async_add = MagicMock(side_effect=lambda e, *a, **k: added.extend(list(e)))
    await async_setup_entry(MagicMock(), entry, async_add)

    uids_after_setup = {e.unique_id for e in added}
    assert any("HP-DYN-1" in u for u in uids_after_setup)
    assert not any("HP-DYN-2" in u for u in uids_after_setup)
    assert listeners, "a coordinator update listener must be registered for dynamic devices"

    # A new heat pump shows up on a later poll; firing the listener must wire it.
    pool["devices"].append(_pin("HP-DYN-2", entities=["climate"]))
    listeners[0]()

    new_uids = {e.unique_id for e in added} - uids_after_setup
    assert new_uids, "new device entities should be added without a reload"
    assert all("HP-DYN-2" in u for u in new_uids), "only the newly-added device's entities are created"
