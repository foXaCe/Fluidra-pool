"""Error-path tests for control entities.

Locks in the HomeAssistantError behaviour added across the light, number,
switch (heater + chlorinator), select (pump/light/chlorinator) and time
(schedule) platforms: every control method must raise HomeAssistantError when
the underlying API raises (FluidraConnectionError / aiohttp.ClientError) and
must clear any optimistic / pending state afterwards. The "API returns False"
revert path is also covered.
"""

from __future__ import annotations

from datetime import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
from homeassistant.components.light import ATTR_BRIGHTNESS, ATTR_RGBW_COLOR
from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.fluidra_pool.api_resilience import FluidraConnectionError
from custom_components.fluidra_pool.light import FluidraLight
from custom_components.fluidra_pool.number import (
    FluidraChlorinatorLevelNumber,
    FluidraChlorinatorOrpSetpoint,
    FluidraChlorinatorPhSetpoint,
    FluidraLightEffectSpeed,
)
from custom_components.fluidra_pool.select.chlorinator import FluidraChlorinatorModeSelect
from custom_components.fluidra_pool.select.light import FluidraLightEffectSelect
from custom_components.fluidra_pool.select.pump import FluidraPumpSpeedSelect
from custom_components.fluidra_pool.switch.chlorinator import (
    FluidraChlorinatorBoostSwitch,
    FluidraChlorinatorSwitch,
)
from custom_components.fluidra_pool.switch.heater import (
    FluidraHeaterSwitch,
    FluidraHeatPumpSwitch,
)
from custom_components.fluidra_pool.time.schedule import (
    FluidraScheduleEndTimeEntity,
    FluidraScheduleStartTimeEntity,
)

POOL_ID = "pool-1"
DEVICE_ID = "LE24500883"


# --- shared fixture builders ---------------------------------------------


def _coordinator(devices: list[dict]) -> Any:
    coordinator = MagicMock()
    coordinator.data = {POOL_ID: {"id": POOL_ID, "name": "Pool", "devices": devices}}
    coordinator.async_request_refresh = AsyncMock()
    coordinator.last_update_success = True
    return coordinator


def _api(**overrides: Any) -> SimpleNamespace:
    defaults = {
        "control_device_component": AsyncMock(return_value=True),
        "set_component_value": AsyncMock(return_value=True),
        "set_component_string_value": AsyncMock(return_value=True),
        "set_component_json_value": AsyncMock(return_value=True),
        "start_pump": AsyncMock(return_value=True),
        "stop_pump": AsyncMock(return_value=True),
        "set_schedule": AsyncMock(return_value=True),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _attach_ha(entity) -> None:
    entity.hass = MagicMock()
    entity.async_write_ha_state = MagicMock()


def _identify(
    device: dict,
    *,
    device_type: str,
    entities: list[str],
    features: dict | None = None,
) -> None:
    """Pin DeviceIdentifier.identify_device result on the device dict."""
    device_id = device.get("device_id", DEVICE_ID)
    family = device.get("family", "")
    model = device.get("model", "")
    type_ = device.get("type", "")
    device["_identify_cache"] = {
        "key": (device_id, family, model, type_, ""),
        "config": SimpleNamespace(
            device_type=device_type,
            features=features or {},
            entities=entities,
            components_range=25,
            required_components=[0, 1, 2, 3],
        ),
    }


@pytest.fixture(autouse=True)
def _skip_sleep() -> Any:
    """Skip optimistic / confirmation sleeps in every module under test."""
    with (
        patch("custom_components.fluidra_pool.select.pump.asyncio.sleep", new=AsyncMock()),
        patch("custom_components.fluidra_pool.select.light.asyncio.sleep", new=AsyncMock()),
        patch("custom_components.fluidra_pool.select.chlorinator.asyncio.sleep", new=AsyncMock()),
        patch("custom_components.fluidra_pool.switch.chlorinator.asyncio.sleep", new=AsyncMock()),
        patch("custom_components.fluidra_pool.time.schedule.asyncio.sleep", new=AsyncMock()),
    ):
        yield


# =========================================================================
# light.py
# =========================================================================


def _light(**api_overrides: Any) -> FluidraLight:
    device = {
        "device_id": DEVICE_ID,
        "name": "Pool Light",
        "type": "light",
        "online": True,
        "components": {},
    }
    _identify(device, device_type="light", entities=["light", "select", "number"])
    light = FluidraLight(_coordinator([device]), _api(**api_overrides), POOL_ID, DEVICE_ID)
    _attach_ha(light)
    return light


async def test_light_turn_on_api_raises_clears_optimistic() -> None:
    light = _light(set_component_string_value=AsyncMock(side_effect=FluidraConnectionError("boom")))
    with pytest.raises(HomeAssistantError):
        await light.async_turn_on()
    assert light._optimistic_is_on is None
    assert light._optimistic_brightness is None
    assert light._optimistic_rgbw is None


async def test_light_turn_on_returns_false_clears_optimistic() -> None:
    light = _light(set_component_string_value=AsyncMock(return_value=False))
    with pytest.raises(HomeAssistantError):
        await light.async_turn_on()
    assert light._optimistic_is_on is None


async def test_light_turn_on_brightness_subcommand_raises_clears_optimistic() -> None:
    light = _light(set_component_value=AsyncMock(side_effect=aiohttp.ClientError("net")))
    with pytest.raises(HomeAssistantError):
        await light.async_turn_on(**{ATTR_BRIGHTNESS: 200})
    # Brightness optimistic override must be rolled back too.
    assert light._optimistic_brightness is None
    assert light._optimistic_is_on is None


async def test_light_turn_on_color_subcommand_raises_clears_optimistic() -> None:
    light = _light(set_component_json_value=AsyncMock(side_effect=FluidraConnectionError("boom")))
    with pytest.raises(HomeAssistantError):
        await light.async_turn_on(**{ATTR_RGBW_COLOR: (10, 20, 30, 40)})
    assert light._optimistic_rgbw is None
    assert light._optimistic_is_on is None


async def test_light_turn_off_api_raises_clears_optimistic() -> None:
    light = _light(set_component_string_value=AsyncMock(side_effect=FluidraConnectionError("boom")))
    with pytest.raises(HomeAssistantError):
        await light.async_turn_off()
    assert light._optimistic_is_on is None


async def test_light_turn_off_returns_false_clears_optimistic() -> None:
    light = _light(set_component_string_value=AsyncMock(return_value=False))
    with pytest.raises(HomeAssistantError):
        await light.async_turn_off()
    assert light._optimistic_is_on is None


async def test_light_turn_on_success_keeps_optimistic_and_refreshes() -> None:
    light = _light()
    await light.async_turn_on(**{ATTR_BRIGHTNESS: 128, ATTR_RGBW_COLOR: (1, 2, 3, 4)})
    # On success optimistic values are kept (cleared later by coordinator update).
    assert light._optimistic_is_on is True
    light.coordinator.async_request_refresh.assert_awaited()


async def test_light_turn_off_success_refreshes() -> None:
    light = _light()
    await light.async_turn_off()
    assert light._optimistic_is_on is False
    light.coordinator.async_request_refresh.assert_awaited()


# =========================================================================
# number.py  (4 number classes)
# =========================================================================


def _number_device(features: dict | None = None) -> dict:
    device = {
        "device_id": DEVICE_ID,
        "name": "Chlorinator",
        "type": "chlorinator",
        "online": True,
        "components": {},
    }
    _identify(
        device,
        device_type="chlorinator",
        entities=["number", "select", "switch", "sensor"],
        features=features or {},
    )
    return device


async def test_number_level_set_value_api_raises() -> None:
    device = _number_device()
    api = _api(control_device_component=AsyncMock(side_effect=FluidraConnectionError("boom")))
    entity = FluidraChlorinatorLevelNumber(_coordinator([device]), api, POOL_ID, DEVICE_ID)
    _attach_ha(entity)
    with pytest.raises(HomeAssistantError):
        await entity.async_set_native_value(50)
    api.control_device_component.assert_awaited()


async def test_number_level_set_value_returns_false_no_refresh() -> None:
    device = _number_device()
    coordinator = _coordinator([device])
    api = _api(control_device_component=AsyncMock(return_value=False))
    entity = FluidraChlorinatorLevelNumber(coordinator, api, POOL_ID, DEVICE_ID)
    _attach_ha(entity)
    # Returns False -> code logs debug, raises HomeAssistantError and does NOT refresh.
    with pytest.raises(HomeAssistantError):
        await entity.async_set_native_value(50)
    coordinator.async_request_refresh.assert_not_awaited()


async def test_number_ph_setpoint_api_raises() -> None:
    device = _number_device({"ph_setpoint": {"write": 8, "read": 172}})
    api = _api(control_device_component=AsyncMock(side_effect=aiohttp.ClientError("net")))
    entity = FluidraChlorinatorPhSetpoint(_coordinator([device]), api, POOL_ID, DEVICE_ID)
    _attach_ha(entity)
    with pytest.raises(HomeAssistantError):
        await entity.async_set_native_value(7.2)


async def test_number_ph_setpoint_returns_false_no_refresh() -> None:
    device = _number_device({"ph_setpoint": 8})
    coordinator = _coordinator([device])
    api = _api(control_device_component=AsyncMock(return_value=False))
    entity = FluidraChlorinatorPhSetpoint(coordinator, api, POOL_ID, DEVICE_ID)
    _attach_ha(entity)
    with pytest.raises(HomeAssistantError):
        await entity.async_set_native_value(7.4)
    coordinator.async_request_refresh.assert_not_awaited()


async def test_number_orp_setpoint_api_raises() -> None:
    device = _number_device({"orp_setpoint": {"write": 11, "read": 177}})
    api = _api(control_device_component=AsyncMock(side_effect=FluidraConnectionError("boom")))
    entity = FluidraChlorinatorOrpSetpoint(_coordinator([device]), api, POOL_ID, DEVICE_ID)
    _attach_ha(entity)
    with pytest.raises(HomeAssistantError):
        await entity.async_set_native_value(700)


async def test_number_orp_setpoint_returns_false_no_refresh() -> None:
    device = _number_device({"orp_setpoint": 11})
    coordinator = _coordinator([device])
    api = _api(control_device_component=AsyncMock(return_value=False))
    entity = FluidraChlorinatorOrpSetpoint(coordinator, api, POOL_ID, DEVICE_ID)
    _attach_ha(entity)
    with pytest.raises(HomeAssistantError):
        await entity.async_set_native_value(720)
    coordinator.async_request_refresh.assert_not_awaited()


async def test_number_effect_speed_api_raises() -> None:
    device = {
        "device_id": DEVICE_ID,
        "name": "Pool Light",
        "type": "light",
        "online": True,
        "components": {},
    }
    _identify(device, device_type="light", entities=["number", "light"])
    api = _api(set_component_value=AsyncMock(side_effect=FluidraConnectionError("boom")))
    entity = FluidraLightEffectSpeed(_coordinator([device]), api, POOL_ID, DEVICE_ID)
    _attach_ha(entity)
    with pytest.raises(HomeAssistantError):
        await entity.async_set_native_value(4)


async def test_number_effect_speed_returns_false_no_refresh() -> None:
    device = {
        "device_id": DEVICE_ID,
        "name": "Pool Light",
        "type": "light",
        "online": True,
        "components": {},
    }
    _identify(device, device_type="light", entities=["number", "light"])
    coordinator = _coordinator([device])
    api = _api(set_component_value=AsyncMock(return_value=False))
    entity = FluidraLightEffectSpeed(coordinator, api, POOL_ID, DEVICE_ID)
    _attach_ha(entity)
    with pytest.raises(HomeAssistantError):
        await entity.async_set_native_value(4)
    coordinator.async_request_refresh.assert_not_awaited()


async def test_number_level_set_value_success_refreshes() -> None:
    device = _number_device()
    coordinator = _coordinator([device])
    api = _api(control_device_component=AsyncMock(return_value=True))
    entity = FluidraChlorinatorLevelNumber(coordinator, api, POOL_ID, DEVICE_ID)
    _attach_ha(entity)
    await entity.async_set_native_value(50)
    coordinator.async_request_refresh.assert_awaited()


# =========================================================================
# switch/heater.py  (FluidraHeaterSwitch + FluidraHeatPumpSwitch)
# =========================================================================


def _heater_device(features: dict | None = None) -> dict:
    device = {
        "device_id": DEVICE_ID,
        "name": "Heater",
        "type": "heater",
        "online": True,
        "components": {},
    }
    _identify(device, device_type="heater", entities=["switch"], features=features or {})
    return device


async def test_heater_turn_on_api_raises_clears_pending() -> None:
    device = _heater_device()
    api = _api(control_device_component=AsyncMock(side_effect=FluidraConnectionError("boom")))
    entity = FluidraHeaterSwitch(_coordinator([device]), api, POOL_ID, DEVICE_ID)
    _attach_ha(entity)
    with pytest.raises(HomeAssistantError):
        await entity.async_turn_on()
    assert entity._pending_state is None


async def test_heater_turn_on_returns_false_clears_pending() -> None:
    device = _heater_device()
    coordinator = _coordinator([device])
    api = _api(control_device_component=AsyncMock(return_value=False))
    entity = FluidraHeaterSwitch(coordinator, api, POOL_ID, DEVICE_ID)
    _attach_ha(entity)
    with pytest.raises(HomeAssistantError):
        await entity.async_turn_on()
    assert entity._pending_state is None
    coordinator.async_request_refresh.assert_not_awaited()


async def test_heater_turn_off_api_raises_clears_pending() -> None:
    device = _heater_device()
    api = _api(control_device_component=AsyncMock(side_effect=aiohttp.ClientError("net")))
    entity = FluidraHeaterSwitch(_coordinator([device]), api, POOL_ID, DEVICE_ID)
    _attach_ha(entity)
    with pytest.raises(HomeAssistantError):
        await entity.async_turn_off()
    assert entity._pending_state is None


async def test_heater_turn_off_returns_false_clears_pending() -> None:
    device = _heater_device()
    coordinator = _coordinator([device])
    api = _api(control_device_component=AsyncMock(return_value=False))
    entity = FluidraHeaterSwitch(coordinator, api, POOL_ID, DEVICE_ID)
    _attach_ha(entity)
    with pytest.raises(HomeAssistantError):
        await entity.async_turn_off()
    assert entity._pending_state is None
    coordinator.async_request_refresh.assert_not_awaited()


async def test_heat_pump_turn_on_api_raises_clears_pending() -> None:
    device = _heater_device()
    api = _api(start_pump=AsyncMock(side_effect=FluidraConnectionError("boom")))
    entity = FluidraHeatPumpSwitch(_coordinator([device]), api, POOL_ID, DEVICE_ID)
    _attach_ha(entity)
    with pytest.raises(HomeAssistantError):
        await entity.async_turn_on()
    assert entity._pending_state is None


async def test_heat_pump_turn_on_returns_false_clears_pending() -> None:
    device = _heater_device()
    coordinator = _coordinator([device])
    api = _api(start_pump=AsyncMock(return_value=False))
    entity = FluidraHeatPumpSwitch(coordinator, api, POOL_ID, DEVICE_ID)
    _attach_ha(entity)
    with pytest.raises(HomeAssistantError):
        await entity.async_turn_on()
    assert entity._pending_state is None
    coordinator.async_request_refresh.assert_not_awaited()


async def test_heat_pump_turn_off_api_raises_clears_pending() -> None:
    device = _heater_device()
    api = _api(stop_pump=AsyncMock(side_effect=FluidraConnectionError("boom")))
    entity = FluidraHeatPumpSwitch(_coordinator([device]), api, POOL_ID, DEVICE_ID)
    _attach_ha(entity)
    with pytest.raises(HomeAssistantError):
        await entity.async_turn_off()
    assert entity._pending_state is None


async def test_heat_pump_turn_off_returns_false_clears_pending() -> None:
    device = _heater_device()
    coordinator = _coordinator([device])
    api = _api(stop_pump=AsyncMock(return_value=False))
    entity = FluidraHeatPumpSwitch(coordinator, api, POOL_ID, DEVICE_ID)
    _attach_ha(entity)
    with pytest.raises(HomeAssistantError):
        await entity.async_turn_off()
    assert entity._pending_state is None
    coordinator.async_request_refresh.assert_not_awaited()


async def test_heat_pump_z550_turn_on_api_raises_clears_pending() -> None:
    """z550_mode routes via control_device_component(21) instead of start_pump."""
    device = _heater_device({"z550_mode": True})
    api = _api(control_device_component=AsyncMock(side_effect=FluidraConnectionError("boom")))
    entity = FluidraHeatPumpSwitch(_coordinator([device]), api, POOL_ID, DEVICE_ID)
    _attach_ha(entity)
    with pytest.raises(HomeAssistantError):
        await entity.async_turn_on()
    assert entity._pending_state is None
    api.control_device_component.assert_awaited_with(DEVICE_ID, 21, 1)


# =========================================================================
# switch/chlorinator.py  (FluidraChlorinatorSwitch + FluidraChlorinatorBoostSwitch)
# =========================================================================


def _chlorinator_device(features: dict | None = None) -> dict:
    device = {
        "device_id": DEVICE_ID,
        "name": "Chlorinator",
        "type": "chlorinator",
        "online": True,
        "components": {},
    }
    _identify(
        device,
        device_type="chlorinator",
        entities=["switch", "select", "number", "sensor"],
        features=features or {},
    )
    return device


async def test_chlorinator_turn_on_api_raises_clears_pending() -> None:
    device = _chlorinator_device()
    api = _api(control_device_component=AsyncMock(side_effect=FluidraConnectionError("boom")))
    entity = FluidraChlorinatorSwitch(_coordinator([device]), api, POOL_ID, DEVICE_ID)
    _attach_ha(entity)
    with pytest.raises(HomeAssistantError):
        await entity.async_turn_on()
    assert entity._pending_state is None


async def test_chlorinator_turn_on_returns_false_clears_pending() -> None:
    device = _chlorinator_device()
    coordinator = _coordinator([device])
    api = _api(control_device_component=AsyncMock(return_value=False))
    entity = FluidraChlorinatorSwitch(coordinator, api, POOL_ID, DEVICE_ID)
    _attach_ha(entity)
    with pytest.raises(HomeAssistantError):
        await entity.async_turn_on()
    assert entity._pending_state is None
    coordinator.async_request_refresh.assert_not_awaited()


async def test_chlorinator_turn_off_api_raises_clears_pending() -> None:
    device = _chlorinator_device()
    api = _api(control_device_component=AsyncMock(side_effect=aiohttp.ClientError("net")))
    entity = FluidraChlorinatorSwitch(_coordinator([device]), api, POOL_ID, DEVICE_ID)
    _attach_ha(entity)
    with pytest.raises(HomeAssistantError):
        await entity.async_turn_off()
    assert entity._pending_state is None


async def test_chlorinator_turn_off_returns_false_clears_pending() -> None:
    device = _chlorinator_device()
    coordinator = _coordinator([device])
    api = _api(control_device_component=AsyncMock(return_value=False))
    entity = FluidraChlorinatorSwitch(coordinator, api, POOL_ID, DEVICE_ID)
    _attach_ha(entity)
    with pytest.raises(HomeAssistantError):
        await entity.async_turn_off()
    assert entity._pending_state is None
    coordinator.async_request_refresh.assert_not_awaited()


async def test_chlorinator_boost_turn_on_api_raises_clears_pending() -> None:
    # skip_mode_select so turn_on goes straight to the boost component write.
    device = _chlorinator_device({"skip_mode_select": True})
    api = _api(control_device_component=AsyncMock(side_effect=FluidraConnectionError("boom")))
    entity = FluidraChlorinatorBoostSwitch(_coordinator([device]), api, POOL_ID, DEVICE_ID)
    _attach_ha(entity)
    with pytest.raises(HomeAssistantError):
        await entity.async_turn_on()
    assert entity._pending_state is None


async def test_chlorinator_boost_turn_on_returns_false_clears_pending() -> None:
    device = _chlorinator_device({"skip_mode_select": True})
    coordinator = _coordinator([device])
    api = _api(control_device_component=AsyncMock(return_value=False))
    entity = FluidraChlorinatorBoostSwitch(coordinator, api, POOL_ID, DEVICE_ID)
    _attach_ha(entity)
    with pytest.raises(HomeAssistantError):
        await entity.async_turn_on()
    assert entity._pending_state is None
    coordinator.async_request_refresh.assert_not_awaited()


async def test_chlorinator_boost_turn_off_api_raises_clears_pending() -> None:
    device = _chlorinator_device({"skip_mode_select": True})
    api = _api(control_device_component=AsyncMock(side_effect=FluidraConnectionError("boom")))
    entity = FluidraChlorinatorBoostSwitch(_coordinator([device]), api, POOL_ID, DEVICE_ID)
    _attach_ha(entity)
    with pytest.raises(HomeAssistantError):
        await entity.async_turn_off()
    assert entity._pending_state is None


async def test_chlorinator_boost_turn_off_returns_false_clears_pending() -> None:
    device = _chlorinator_device({"skip_mode_select": True})
    coordinator = _coordinator([device])
    api = _api(control_device_component=AsyncMock(return_value=False))
    entity = FluidraChlorinatorBoostSwitch(coordinator, api, POOL_ID, DEVICE_ID)
    _attach_ha(entity)
    with pytest.raises(HomeAssistantError):
        await entity.async_turn_off()
    assert entity._pending_state is None
    coordinator.async_request_refresh.assert_not_awaited()


async def test_chlorinator_boost_turn_on_mode_select_branch_raises() -> None:
    """Without skip_mode_select and mode != on, turn_on first writes the mode.

    The mode write here raises, which must still surface as HomeAssistantError
    and clear the pending state.
    """
    # No skip_mode_select feature, default mapping; component 20 reports 0 (off).
    device = _chlorinator_device()
    device["components"] = {"20": {"reportedValue": 0}}
    api = _api(control_device_component=AsyncMock(side_effect=FluidraConnectionError("boom")))
    entity = FluidraChlorinatorBoostSwitch(_coordinator([device]), api, POOL_ID, DEVICE_ID)
    _attach_ha(entity)
    with pytest.raises(HomeAssistantError):
        await entity.async_turn_on()
    assert entity._pending_state is None
    # The mode component (20) was the first write attempted.
    api.control_device_component.assert_awaited_with(DEVICE_ID, 20, 1)


# =========================================================================
# select/pump.py
# =========================================================================


def _pump_select(**api_overrides: Any) -> tuple[FluidraPumpSpeedSelect, Any]:
    device = {
        "device_id": DEVICE_ID,
        "name": "Pump",
        "type": "pump",
        "online": True,
        "is_running": True,
        "components": {},
    }
    coordinator = _coordinator([device])
    api = _api(**api_overrides)
    entity = FluidraPumpSpeedSelect(coordinator, api, POOL_ID, DEVICE_ID)
    _attach_ha(entity)
    return entity, coordinator


async def test_pump_select_api_raises_clears_optimistic() -> None:
    entity, _ = _pump_select(control_device_component=AsyncMock(side_effect=FluidraConnectionError("boom")))
    with pytest.raises(HomeAssistantError):
        await entity.async_select_option("high")
    # finally-block must always drop the optimistic option.
    assert entity._optimistic_option is None


async def test_pump_select_stopped_api_raises_clears_optimistic() -> None:
    entity, _ = _pump_select(control_device_component=AsyncMock(side_effect=aiohttp.ClientError("net")))
    with pytest.raises(HomeAssistantError):
        await entity.async_select_option("stopped")
    assert entity._optimistic_option is None


async def test_pump_select_unknown_option_is_noop() -> None:
    entity, coordinator = _pump_select()
    await entity.async_select_option("turbo")  # not in mapping -> early return
    assert entity._optimistic_option is None
    coordinator.async_request_refresh.assert_not_awaited()


async def test_pump_select_success_clears_optimistic_and_refreshes() -> None:
    entity, coordinator = _pump_select()
    await entity.async_select_option("high")
    assert entity._optimistic_option is None
    coordinator.async_request_refresh.assert_awaited()


async def test_pump_select_stopped_success_clears_optimistic() -> None:
    entity, coordinator = _pump_select()
    await entity.async_select_option("stopped")
    assert entity._optimistic_option is None
    coordinator.async_request_refresh.assert_awaited()


# =========================================================================
# select/light.py
# =========================================================================


def _light_select(**api_overrides: Any) -> tuple[FluidraLightEffectSelect, Any]:
    device = {
        "device_id": DEVICE_ID,
        "name": "Pool Light",
        "type": "light",
        "online": True,
        "components": {},
    }
    coordinator = _coordinator([device])
    api = _api(**api_overrides)
    entity = FluidraLightEffectSelect(coordinator, api, POOL_ID, DEVICE_ID)
    _attach_ha(entity)
    return entity, coordinator


async def test_light_select_api_raises_clears_optimistic() -> None:
    entity, _ = _light_select(control_device_component=AsyncMock(side_effect=FluidraConnectionError("boom")))
    with pytest.raises(HomeAssistantError):
        await entity.async_select_option("scene_3")
    assert entity._optimistic_option is None


async def test_light_select_unknown_option_is_noop() -> None:
    entity, coordinator = _light_select()
    await entity.async_select_option("rainbow")  # not in mapping
    assert entity._optimistic_option is None
    coordinator.async_request_refresh.assert_not_awaited()


async def test_light_select_success_keeps_optimistic_until_confirmed() -> None:
    entity, coordinator = _light_select()
    await entity.async_select_option("scene_5")
    # Confirm-or-expire: the optimistic value survives the write and is only
    # dropped when the coordinator reports it back (or after the timeout).
    assert entity._optimistic_option == "scene_5"
    coordinator.async_request_refresh.assert_awaited()


# =========================================================================
# select/chlorinator.py
# =========================================================================


def _mode_select(**api_overrides: Any) -> tuple[FluidraChlorinatorModeSelect, Any]:
    device = {
        "device_id": DEVICE_ID,
        "name": "Chlorinator",
        "type": "chlorinator",
        "online": True,
        "components": {},
    }
    _identify(device, device_type="chlorinator", entities=["select", "switch"])
    coordinator = _coordinator([device])
    api = _api(**api_overrides)
    entity = FluidraChlorinatorModeSelect(coordinator, api, POOL_ID, DEVICE_ID)
    _attach_ha(entity)
    return entity, coordinator


async def test_mode_select_api_raises_clears_optimistic() -> None:
    entity, _ = _mode_select(control_device_component=AsyncMock(side_effect=FluidraConnectionError("boom")))
    with pytest.raises(HomeAssistantError):
        await entity.async_select_option("on")
    assert entity._optimistic_option is None


async def test_mode_select_returns_false_clears_optimistic() -> None:
    entity, coordinator = _mode_select(control_device_component=AsyncMock(return_value=False))
    with pytest.raises(HomeAssistantError):
        await entity.async_select_option("auto")
    assert entity._optimistic_option is None
    coordinator.async_request_refresh.assert_not_awaited()


async def test_mode_select_unknown_option_is_noop() -> None:
    entity, coordinator = _mode_select()
    await entity.async_select_option("turbo")  # not in mapping
    assert entity._optimistic_option is None
    coordinator.async_request_refresh.assert_not_awaited()


async def test_mode_select_success_keeps_optimistic_until_confirmed() -> None:
    # On success the optimistic value is intentionally NOT cleared here; it is
    # dropped later in _handle_coordinator_update once the backend confirms.
    entity, coordinator = _mode_select()
    await entity.async_select_option("on")
    assert entity._optimistic_option == "on"
    coordinator.async_request_refresh.assert_awaited()


# =========================================================================
# time/schedule.py  (start + end)
# =========================================================================


def _schedule_device() -> dict:
    return {
        "device_id": DEVICE_ID,
        "name": "Pump",
        "type": "pump",
        "online": True,
        "components": {},
        "schedule_data": [
            {
                "id": 1,
                "enabled": True,
                "startTime": "0 8 * * 1,2,3,4,5,6,7",
                "endTime": "0 18 * * 1,2,3,4,5,6,7",
                "startActions": {"operationName": "0"},
            }
        ],
    }


def _start_time(**api_overrides: Any) -> tuple[FluidraScheduleStartTimeEntity, Any]:
    device = _schedule_device()
    coordinator = _coordinator([device])
    api = _api(**api_overrides)
    entity = FluidraScheduleStartTimeEntity(coordinator, api, POOL_ID, DEVICE_ID, "1")
    _attach_ha(entity)
    return entity, coordinator


def _end_time(**api_overrides: Any) -> tuple[FluidraScheduleEndTimeEntity, Any]:
    device = _schedule_device()
    coordinator = _coordinator([device])
    api = _api(**api_overrides)
    entity = FluidraScheduleEndTimeEntity(coordinator, api, POOL_ID, DEVICE_ID, "1")
    _attach_ha(entity)
    return entity, coordinator


async def test_schedule_start_api_raises_clears_optimistic() -> None:
    entity, _ = _start_time(set_schedule=AsyncMock(side_effect=FluidraConnectionError("boom")))
    with pytest.raises(HomeAssistantError):
        await entity.async_set_value(time(7, 30))
    assert entity._optimistic_value is None


async def test_schedule_start_returns_false_raises_clears_optimistic() -> None:
    entity, _ = _start_time(set_schedule=AsyncMock(return_value=False))
    # set_schedule returns False -> raises schedule_set_rejected (HomeAssistantError).
    with pytest.raises(HomeAssistantError):
        await entity.async_set_value(time(7, 30))
    assert entity._optimistic_value is None


async def test_schedule_end_api_raises_clears_optimistic() -> None:
    entity, _ = _end_time(set_schedule=AsyncMock(side_effect=aiohttp.ClientError("net")))
    with pytest.raises(HomeAssistantError):
        await entity.async_set_value(time(19, 0))
    assert entity._optimistic_value is None


async def test_schedule_end_returns_false_raises_clears_optimistic() -> None:
    entity, _ = _end_time(set_schedule=AsyncMock(return_value=False))
    with pytest.raises(HomeAssistantError):
        await entity.async_set_value(time(19, 0))
    assert entity._optimistic_value is None


async def test_schedule_start_success_clears_optimistic_and_refreshes() -> None:
    entity, coordinator = _start_time()
    await entity.async_set_value(time(7, 0))
    assert entity._optimistic_value is None
    coordinator.async_request_refresh.assert_awaited()


async def test_schedule_end_success_clears_optimistic_and_refreshes() -> None:
    entity, coordinator = _end_time()
    await entity.async_set_value(time(20, 0))
    assert entity._optimistic_value is None
    coordinator.async_request_refresh.assert_awaited()


async def test_schedule_start_no_schedule_data_early_return() -> None:
    # Device has no schedule_data key -> method returns early, clears optimistic.
    device = {
        "device_id": DEVICE_ID,
        "name": "Pump",
        "type": "pump",
        "online": True,
        "components": {},
    }
    coordinator = _coordinator([device])
    api = _api()
    entity = FluidraScheduleStartTimeEntity(coordinator, api, POOL_ID, DEVICE_ID, "1")
    _attach_ha(entity)
    await entity.async_set_value(time(7, 0))
    assert entity._optimistic_value is None
    api.set_schedule.assert_not_awaited()
