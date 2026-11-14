"""Issue #266 — LG Eco Elyo exposes cool and heat_cool as HVAC modes.

The Eco Elyo's component-14 presets carry the direction (Smart/Boost/Silence
Heating or Cooling, Smart Heat/Cool), yet the climate entity only offered
off/heat and stayed on heat while cooling. The HVAC mode now follows the
preset, and setting a mode writes the matching preset.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call

from homeassistant.components.climate import HVACMode
from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.fluidra_pool.climate import FluidraHeatPumpClimate
from custom_components.fluidra_pool.const import (
    LG_MODE_TO_VALUE,
    LG_PRESET_BOOST_COOLING,
    LG_PRESET_BOOST_HEATING,
    LG_PRESET_SILENCE_COOLING,
    LG_PRESET_SMART_COOLING,
    LG_PRESET_SMART_HEAT_COOL,
    LG_PRESET_SMART_HEATING,
)
from custom_components.fluidra_pool.device_registry import DEVICE_CONFIGS, DeviceIdentifier

POOL_ID = "pool-1"
DEVICE_ID = "LG12345678"
ECO_ELYO = {"preset_modes": True, "lg_cooling": True}


def _device(features: dict[str, Any], c14: int | None = None, **extra: Any) -> dict[str, Any]:
    components = {} if c14 is None else {"14": {"reportedValue": c14}}
    device = {
        "device_id": DEVICE_ID,
        "name": "Heat Pump",
        "family": "",
        "type": "",
        "model": "",
        "online": True,
        "components": components,
        "_identify_cache": {
            "key": (DEVICE_ID, "", "", "", ""),
            "config": SimpleNamespace(
                device_type="heat_pump",
                features=features,
                components_range=5,
                required_components=[0, 1, 2, 3],
                entities=[],
            ),
        },
    }
    device.update(extra)
    return device


def _make(device: dict[str, Any]) -> tuple[FluidraHeatPumpClimate, SimpleNamespace]:
    api = SimpleNamespace(
        start_pump=AsyncMock(return_value=True),
        stop_pump=AsyncMock(return_value=True),
        control_device_component=AsyncMock(return_value=True),
    )
    coordinator = MagicMock()
    coordinator.data = {POOL_ID: {"id": POOL_ID, "name": "Pool", "devices": [device]}}
    coordinator.async_request_refresh = AsyncMock()
    coordinator.last_update_success = True
    climate = FluidraHeatPumpClimate(coordinator, api, POOL_ID, DEVICE_ID)
    climate.hass = MagicMock()
    climate.async_write_ha_state = MagicMock()
    return climate, api


def test_eco_elyo_profile_enables_cooling_and_hpgic_does_not() -> None:
    eco_elyo = DeviceIdentifier.identify_device(
        {"device_id": DEVICE_ID, "name": "Eco Elyo", "family": "eco elyo", "model": "astralpool", "type": "heat_pump"}
    )
    assert eco_elyo is DEVICE_CONFIGS["lg_heat_pump"]
    assert eco_elyo.features["lg_cooling"] is True
    assert "lg_cooling" not in DEVICE_CONFIGS["hpgic_gre_heat_pump"].features


def test_eco_elyo_hvac_modes_include_cool_and_heat_cool() -> None:
    climate, _ = _make(_device(ECO_ELYO))
    assert climate.hvac_modes == [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL, HVACMode.HEAT_COOL]


def test_lg_without_cooling_flag_keeps_off_and_heat() -> None:
    climate, _ = _make(_device({"preset_modes": True}, c14=1, heat_pump_reported=1))
    assert climate.hvac_modes == [HVACMode.OFF, HVACMode.HEAT]
    assert climate.hvac_mode == HVACMode.HEAT


@pytest.mark.parametrize(
    ("c14", "expected"),
    [
        (0, HVACMode.HEAT),
        (3, HVACMode.HEAT),
        (4, HVACMode.HEAT),
        (1, HVACMode.COOL),
        (5, HVACMode.COOL),
        (6, HVACMode.COOL),
        (2, HVACMode.HEAT_COOL),
        (None, HVACMode.HEAT),
        (99, HVACMode.HEAT),
    ],
)
def test_eco_elyo_hvac_mode_follows_preset(c14: int | None, expected: HVACMode) -> None:
    climate, _ = _make(_device(ECO_ELYO, c14=c14, heat_pump_reported=1))
    assert climate.hvac_mode == expected


def test_eco_elyo_off_wins_over_cooling_preset() -> None:
    climate, _ = _make(_device(ECO_ELYO, c14=1, heat_pump_reported=0))
    assert climate.hvac_mode == HVACMode.OFF


def test_eco_elyo_pending_cooling_preset_shows_cool() -> None:
    climate, _ = _make(_device(ECO_ELYO, c14=0, heat_pump_reported=1))
    climate._pending_preset_mode = LG_PRESET_BOOST_COOLING
    climate._last_preset_action_time = 10**12
    assert climate.hvac_mode == HVACMode.COOL


@pytest.mark.parametrize(
    ("current_c14", "hvac_mode", "written"),
    [
        (0, HVACMode.COOL, 1),  # smart_heating -> smart_cooling
        (3, HVACMode.COOL, 1),  # boost_heating -> smart_cooling
        (6, HVACMode.COOL, 6),  # silence_cooling kept
        (1, HVACMode.HEAT, 0),  # smart_cooling -> smart_heating
        (3, HVACMode.HEAT, 3),  # boost_heating kept
        (5, HVACMode.HEAT_COOL, 2),  # boost_cooling -> smart_heat_cool
        (2, HVACMode.HEAT_COOL, 2),
    ],
)
async def test_eco_elyo_set_hvac_mode_writes_matching_preset_then_turns_on(
    current_c14: int, hvac_mode: HVACMode, written: int
) -> None:
    climate, api = _make(_device(ECO_ELYO, c14=current_c14, heat_pump_reported=1))
    await climate.async_set_hvac_mode(hvac_mode)
    api.control_device_component.assert_awaited_once_with(DEVICE_ID, 14, written)
    api.start_pump.assert_awaited_once_with(DEVICE_ID)
    api.stop_pump.assert_not_called()


async def test_eco_elyo_set_hvac_mode_off_only_stops() -> None:
    climate, api = _make(_device(ECO_ELYO, c14=1, heat_pump_reported=1))
    await climate.async_set_hvac_mode(HVACMode.OFF)
    api.stop_pump.assert_awaited_once_with(DEVICE_ID)
    api.control_device_component.assert_not_called()
    api.start_pump.assert_not_called()


async def test_eco_elyo_preset_write_failure_does_not_turn_on() -> None:
    climate, api = _make(_device(ECO_ELYO, c14=0, heat_pump_reported=0))
    api.control_device_component.return_value = False
    with pytest.raises(HomeAssistantError):
        await climate.async_set_hvac_mode(HVACMode.COOL)
    assert api.control_device_component.await_args_list == [call(DEVICE_ID, 14, 1)]
    api.start_pump.assert_not_called()


def test_preset_constants_match_written_values() -> None:
    """Guard the values the write tests assert against."""
    assert LG_MODE_TO_VALUE[LG_PRESET_SMART_HEATING] == 0
    assert LG_MODE_TO_VALUE[LG_PRESET_SMART_COOLING] == 1
    assert LG_MODE_TO_VALUE[LG_PRESET_SMART_HEAT_COOL] == 2
    assert LG_MODE_TO_VALUE[LG_PRESET_BOOST_HEATING] == 3
    assert LG_MODE_TO_VALUE[LG_PRESET_SILENCE_COOLING] == 6
