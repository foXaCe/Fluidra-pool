"""Coverage-gap tests for switch/chlorinator.py and switch/heater.py.

Focus on the SUCCESS paths, PROPERTIES and branches not already exercised by
test_switch.py / test_entity_error_paths.py (which own the error/raise paths and
the returns-False revert assertions). Here we cover:

* state-property fallback chains (component → pump_reported → is_running …)
* optimistic pending-state handling, including expiry-driven auto clear
* icon / unique_id / extra_state_attributes
* async_turn_on / async_turn_off SUCCESS (api True → request_refresh)
* boost mode-select branch and z550_mode heat-pump branch
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.fluidra_pool.switch.chlorinator import (
    FluidraChlorinatorBoostSwitch,
    FluidraChlorinatorSwitch,
)
from custom_components.fluidra_pool.switch.heater import (
    FluidraHeaterSwitch,
    FluidraHeatPumpSwitch,
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
        "start_pump": AsyncMock(return_value=True),
        "stop_pump": AsyncMock(return_value=True),
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
    """Pin DeviceIdentifier.identify_device result on the device dict.

    The cache key must match what identify_device recomputes, so family/model/
    type are read straight back off the device dict (same as the source).
    """
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
    """Never actually sleep in the optimistic confirmation delays."""
    with patch(
        "custom_components.fluidra_pool.switch.chlorinator.asyncio.sleep",
        new=AsyncMock(),
    ):
        yield


# =========================================================================
# FluidraChlorinatorSwitch
# =========================================================================


def _chlorinator_device(features: dict | None = None, **extra: Any) -> dict:
    device = {
        "device_id": DEVICE_ID,
        "name": "Chlorinator",
        "type": "chlorinator",
        "online": True,
        "components": {},
    }
    device.update(extra)
    _identify(
        device,
        device_type="chlorinator",
        entities=["switch", "select", "number", "sensor"],
        features=features or {},
    )
    return device


def _chlorinator(device: dict, **api_overrides: Any) -> FluidraChlorinatorSwitch:
    entity = FluidraChlorinatorSwitch(_coordinator([device]), _api(**api_overrides), POOL_ID, DEVICE_ID)
    _attach_ha(entity)
    return entity


def test_chlorinator_is_on_reads_component_reported_value() -> None:
    """The on_off_component reportedValue is the primary source of truth."""
    device = _chlorinator_device(components={"9": {"reportedValue": 1}})
    assert _chlorinator(device).is_on is True
    device_off = _chlorinator_device(components={"9": {"reportedValue": 0}})
    assert _chlorinator(device_off).is_on is False


def test_chlorinator_is_on_falls_back_to_pump_reported() -> None:
    """With no component reportedValue, pump_reported drives the state."""
    device = _chlorinator_device(pump_reported=1, is_running=False)
    assert _chlorinator(device).is_on is True


def test_chlorinator_is_on_falls_back_to_is_running() -> None:
    """With neither component nor pump_reported, is_running is the last resort."""
    device = _chlorinator_device(is_running=True)
    assert _chlorinator(device).is_on is True
    device_default = _chlorinator_device()
    assert _chlorinator(device_default).is_on is False


def test_chlorinator_custom_on_off_component_feature() -> None:
    """A configured on_off_component feature overrides the default of 9."""
    device = _chlorinator_device(features={"on_off_component": 12}, components={"12": {"reportedValue": 1}})
    entity = _chlorinator(device)
    assert entity.is_on is True
    assert entity.extra_state_attributes["component_id"] == 12


def test_chlorinator_pending_state_shows_through_until_match() -> None:
    """A fresh optimistic ON is reported even while the device still says OFF."""
    device = _chlorinator_device(components={"9": {"reportedValue": 0}})
    entity = _chlorinator(device)
    entity._set_pending_state(True)
    assert entity.is_on is True
    assert entity._pending_state is True


def test_chlorinator_pending_state_clears_when_actual_matches() -> None:
    """Once the device confirms the pending value the optimistic flag is dropped."""
    device = _chlorinator_device(components={"9": {"reportedValue": 1}})
    entity = _chlorinator(device)
    entity._set_pending_state(True)
    assert entity.is_on is True
    assert entity._pending_state is None  # matched → cleared


def test_chlorinator_pending_state_clears_on_expiry() -> None:
    """An old optimistic value times out and yields the real (mismatched) state."""
    device = _chlorinator_device(components={"9": {"reportedValue": 0}})
    entity = _chlorinator(device)
    entity._set_pending_state(True)
    entity._last_action_time = 0.0  # far in the past → expired
    assert entity.is_on is False
    assert entity._pending_state is None


def test_chlorinator_actual_state_direct() -> None:
    """_actual_state mirrors the component reportedValue regardless of pending."""
    device = _chlorinator_device(components={"9": {"reportedValue": 1}})
    entity = _chlorinator(device)
    assert entity._actual_state() is True


def test_chlorinator_icon_reflects_state() -> None:
    """Icon switches between flask (on) and flask-outline (off)."""
    on = _chlorinator(_chlorinator_device(components={"9": {"reportedValue": 1}}))
    off = _chlorinator(_chlorinator_device(components={"9": {"reportedValue": 0}}))
    assert on.icon == "mdi:flask"
    assert off.icon == "mdi:flask-outline"


def test_chlorinator_unique_id() -> None:
    """unique_id follows the documented domain/pool/device pattern."""
    entity = _chlorinator(_chlorinator_device())
    assert entity.unique_id == f"fluidra_pool_{POOL_ID}_{DEVICE_ID}_chlorinator"


def test_chlorinator_extra_state_attributes() -> None:
    """Attributes expose component id, operation tag and pending flag."""
    entity = _chlorinator(_chlorinator_device())
    attrs = entity.extra_state_attributes
    assert attrs["component_id"] == 9
    assert attrs["operation"] == "chlorinator_control"
    assert attrs["device_id"] == DEVICE_ID
    assert attrs["pending_action"] is False
    entity._set_pending_state(True)
    assert entity.extra_state_attributes["pending_action"] is True


async def test_chlorinator_turn_on_success_writes_one_and_refreshes() -> None:
    """Successful ON writes value 1 to the on/off component and refreshes."""
    entity = _chlorinator(_chlorinator_device())
    await entity.async_turn_on()
    entity._api.control_device_component.assert_awaited_once_with(DEVICE_ID, 9, 1)
    entity.coordinator.async_request_refresh.assert_awaited_once()
    assert entity._pending_state is True  # kept until coordinator confirms


async def test_chlorinator_turn_off_success_writes_zero_and_refreshes() -> None:
    """Successful OFF writes value 0 to the on/off component and refreshes."""
    entity = _chlorinator(_chlorinator_device(components={"9": {"reportedValue": 1}}))
    await entity.async_turn_off()
    entity._api.control_device_component.assert_awaited_once_with(DEVICE_ID, 9, 0)
    entity.coordinator.async_request_refresh.assert_awaited_once()


async def test_chlorinator_turn_on_returns_false_reverts_no_refresh() -> None:
    """API False rolls back the optimistic state, raises and skips the refresh."""
    entity = _chlorinator(_chlorinator_device(), control_device_component=AsyncMock(return_value=False))
    with pytest.raises(HomeAssistantError):
        await entity.async_turn_on()
    assert entity._pending_state is None
    entity.coordinator.async_request_refresh.assert_not_awaited()


async def test_chlorinator_turn_off_returns_false_reverts_no_refresh() -> None:
    """API False on OFF rolls back too, raising HomeAssistantError."""
    entity = _chlorinator(_chlorinator_device(), control_device_component=AsyncMock(return_value=False))
    with pytest.raises(HomeAssistantError):
        await entity.async_turn_off()
    assert entity._pending_state is None
    entity.coordinator.async_request_refresh.assert_not_awaited()


# =========================================================================
# FluidraChlorinatorBoostSwitch
# =========================================================================


def _boost(device: dict, **api_overrides: Any) -> FluidraChlorinatorBoostSwitch:
    entity = FluidraChlorinatorBoostSwitch(_coordinator([device]), _api(**api_overrides), POOL_ID, DEVICE_ID)
    _attach_ha(entity)
    return entity


def test_boost_is_on_reads_boost_component() -> None:
    """The boost_mode component reportedValue drives is_on."""
    device = _chlorinator_device(features={"boost_mode": 245}, components={"245": {"reportedValue": True}})
    assert _boost(device).is_on is True
    device_off = _chlorinator_device(features={"boost_mode": 245}, components={"245": {"reportedValue": False}})
    assert _boost(device_off).is_on is False


def test_boost_is_on_default_component() -> None:
    """Without a feature, the default boost component is 245."""
    device = _chlorinator_device(components={"245": {"reportedValue": True}})
    assert _boost(device).is_on is True


def test_boost_pending_state_shows_through() -> None:
    """Optimistic boost ON shows even while the component still reports off."""
    device = _chlorinator_device(components={"245": {"reportedValue": False}})
    entity = _boost(device)
    entity._set_pending_state(True)
    assert entity.is_on is True


def test_boost_pending_state_clears_on_expiry() -> None:
    """An expired pending boost value yields the real state and clears."""
    device = _chlorinator_device(components={"245": {"reportedValue": False}})
    entity = _boost(device)
    entity._set_pending_state(True)
    entity._last_action_time = 0.0
    assert entity.is_on is False
    assert entity._pending_state is None


def test_boost_available_base_unavailable() -> None:
    """When the base entity is unavailable (offline) boost is unavailable too."""
    device = _chlorinator_device(components={})
    device["online"] = False
    entity = _boost(device)
    assert entity.available is False


def test_boost_available_skip_mode_select_true() -> None:
    """skip_mode_select makes boost available regardless of mode."""
    device = _chlorinator_device(features={"skip_mode_select": True})
    entity = _boost(device)
    assert entity.available is True


def test_boost_available_requires_mode_on() -> None:
    """Without skip_mode_select boost is available only when mode == on."""
    on_device = _chlorinator_device(features={"mode_component": 20}, components={"20": {"reportedValue": 1}})
    off_device = _chlorinator_device(features={"mode_component": 20}, components={"20": {"reportedValue": 0}})
    assert _boost(on_device).available is True
    assert _boost(off_device).available is False


def test_boost_get_current_mode_default_mapping() -> None:
    """Default mapping translates component values to off/on/auto."""
    device = _chlorinator_device(features={"mode_component": 20}, components={"20": {"reportedValue": 2}})
    assert _boost(device)._get_current_mode() == "auto"


def test_boost_get_current_mode_custom_mapping() -> None:
    """A configured mode_mapping overrides the default translation."""
    device = _chlorinator_device(
        features={"mode_component": 20, "mode_mapping": {"0": "off", "3": "on"}},
        components={"20": {"reportedValue": 3}},
    )
    assert _boost(device)._get_current_mode() == "on"


def test_boost_get_current_mode_invalid_value_defaults_off() -> None:
    """A non-numeric reported value falls back to off."""
    device = _chlorinator_device(features={"mode_component": 20}, components={"20": {"reportedValue": "n/a"}})
    assert _boost(device)._get_current_mode() == "off"


def test_boost_unique_id() -> None:
    """Boost unique_id is set from _attr_unique_id in the constructor."""
    entity = _boost(_chlorinator_device())
    assert entity.unique_id == f"fluidra_{DEVICE_ID}_boost_mode"


def test_boost_extra_state_attributes() -> None:
    """Boost attributes expose component, device id, current mode, pending flag."""
    device = _chlorinator_device(
        features={"boost_mode": 245, "mode_component": 20},
        components={"20": {"reportedValue": 1}},
    )
    entity = _boost(device)
    attrs = entity.extra_state_attributes
    assert attrs["component"] == 245
    assert attrs["device_id"] == DEVICE_ID
    assert attrs["current_mode"] == "on"
    assert attrs["pending_action"] is False


async def test_boost_turn_on_skip_mode_select_success() -> None:
    """skip_mode_select writes only the boost component (no mode write)."""
    device = _chlorinator_device(features={"skip_mode_select": True, "boost_mode": 245})
    entity = _boost(device)
    await entity.async_turn_on()
    entity._api.control_device_component.assert_awaited_once_with(DEVICE_ID, 245, True)
    entity.coordinator.async_request_refresh.assert_awaited_once()
    assert entity._pending_state is True


async def test_boost_turn_on_sets_mode_first_when_not_on() -> None:
    """Without skip_mode_select and mode != on, mode is written before boost."""
    device = _chlorinator_device(
        features={"mode_component": 20, "boost_mode": 245},
        components={"20": {"reportedValue": 0}},  # mode off
    )
    entity = _boost(device)
    await entity.async_turn_on()
    # First the mode write (component 20 → on_value 1), then the boost write.
    assert entity._api.control_device_component.await_args_list[0].args == (DEVICE_ID, 20, 1)
    assert entity._api.control_device_component.await_args_list[1].args == (DEVICE_ID, 245, True)
    entity.coordinator.async_request_refresh.assert_awaited_once()


async def test_boost_turn_on_mode_already_on_skips_mode_write() -> None:
    """If mode is already on, only the boost component is written."""
    device = _chlorinator_device(
        features={"mode_component": 20, "boost_mode": 245},
        components={"20": {"reportedValue": 1}},  # mode on
    )
    entity = _boost(device)
    await entity.async_turn_on()
    entity._api.control_device_component.assert_awaited_once_with(DEVICE_ID, 245, True)


async def test_boost_turn_on_custom_mapping_on_value() -> None:
    """A custom mode_mapping resolves the 'on' value used for the mode write."""
    device = _chlorinator_device(
        features={
            "mode_component": 20,
            "boost_mode": 245,
            "mode_mapping": {"0": "off", "5": "on"},
        },
        components={"20": {"reportedValue": 0}},  # off → needs mode write
    )
    entity = _boost(device)
    await entity.async_turn_on()
    assert entity._api.control_device_component.await_args_list[0].args == (DEVICE_ID, 20, 5)


async def test_boost_turn_on_returns_false_reverts() -> None:
    """API False on boost ON clears the optimistic state, raises, no refresh."""
    device = _chlorinator_device(features={"skip_mode_select": True, "boost_mode": 245})
    entity = _boost(device, control_device_component=AsyncMock(return_value=False))
    with pytest.raises(HomeAssistantError):
        await entity.async_turn_on()
    assert entity._pending_state is None
    entity.coordinator.async_request_refresh.assert_not_awaited()


async def test_boost_turn_off_success() -> None:
    """Boost OFF writes False to the boost component and refreshes."""
    device = _chlorinator_device(features={"boost_mode": 245})
    entity = _boost(device)
    await entity.async_turn_off()
    entity._api.control_device_component.assert_awaited_once_with(DEVICE_ID, 245, False)
    entity.coordinator.async_request_refresh.assert_awaited_once()
    assert entity._pending_state is False


async def test_boost_turn_off_returns_false_reverts() -> None:
    """API False on boost OFF clears the optimistic state, raises, no refresh."""
    device = _chlorinator_device(features={"boost_mode": 245})
    entity = _boost(device, control_device_component=AsyncMock(return_value=False))
    with pytest.raises(HomeAssistantError):
        await entity.async_turn_off()
    assert entity._pending_state is None
    entity.coordinator.async_request_refresh.assert_not_awaited()


# =========================================================================
# FluidraHeatPumpSwitch
# =========================================================================


def _heatpump_device(features: dict | None = None, **extra: Any) -> dict:
    device = {
        "device_id": DEVICE_ID,
        "name": "Heat Pump",
        "type": "heat_pump",
        "online": True,
        "components": {},
    }
    device.update(extra)
    _identify(device, device_type="heat_pump", entities=["switch"], features=features or {})
    return device


def _heatpump(device: dict, **api_overrides: Any) -> FluidraHeatPumpSwitch:
    entity = FluidraHeatPumpSwitch(_coordinator([device]), _api(**api_overrides), POOL_ID, DEVICE_ID)
    _attach_ha(entity)
    return entity


def test_heatpump_is_on_uses_heat_pump_reported() -> None:
    """heat_pump_reported is the highest-priority real-time source."""
    device = _heatpump_device(heat_pump_reported=1, pump_reported=0, is_running=False)
    assert _heatpump(device).is_on is True
    device_off = _heatpump_device(heat_pump_reported=0, is_running=True)
    assert _heatpump(device_off).is_on is False


def test_heatpump_is_on_falls_back_to_pump_reported() -> None:
    """Without heat_pump_reported, pump_reported is used."""
    device = _heatpump_device(pump_reported=1, is_running=False)
    assert _heatpump(device).is_on is True


def test_heatpump_is_on_falls_back_to_is_running() -> None:
    """Without reported values, is_running marks it on."""
    device = _heatpump_device(is_running=True)
    assert _heatpump(device).is_on is True


def test_heatpump_is_on_falls_back_to_is_heating() -> None:
    """Final fallback is the is_heating flag."""
    device = _heatpump_device(is_heating=True)
    assert _heatpump(device).is_on is True
    device_default = _heatpump_device()
    assert _heatpump(device_default).is_on is False


def test_heatpump_pending_state_shows_through() -> None:
    """A fresh optimistic value short-circuits the reported chain."""
    device = _heatpump_device(heat_pump_reported=0)
    entity = _heatpump(device)
    entity._set_pending_state(True)
    assert entity.is_on is True


def test_heatpump_pending_state_clears_on_expiry() -> None:
    """An expired pending value clears and falls back to reported state."""
    device = _heatpump_device(heat_pump_reported=0)
    entity = _heatpump(device)
    entity._set_pending_state(True)
    entity._last_action_time = 0.0
    assert entity.is_on is False
    assert entity._pending_state is None


def test_heatpump_pending_state_clears_when_actual_matches() -> None:
    """Optimistic state clears as soon as the poll confirms it, not only on timeout (sensor_switch-1)."""
    device = _heatpump_device(heat_pump_reported=1)
    entity = _heatpump(device)
    entity._set_pending_state(True)
    assert entity.is_on is True
    # Reconciled against the polled value instead of staying pinned for the 10s window.
    assert entity._pending_state is None


def test_heatpump_icon_reflects_state() -> None:
    """Icon toggles between heat-pump and heat-pump-outline."""
    on = _heatpump(_heatpump_device(heat_pump_reported=1))
    off = _heatpump(_heatpump_device(heat_pump_reported=0))
    assert on.icon == "mdi:heat-pump"
    assert off.icon == "mdi:heat-pump-outline"


def test_heatpump_extra_state_attributes_without_temperatures() -> None:
    """No temperature keys present → attrs omit current/target temperature."""
    device = _heatpump_device(heat_pump_reported=1)
    attrs = _heatpump(device).extra_state_attributes
    assert attrs["operation"] == "heat_pump_control"
    assert attrs["device_type"] == "heat_pump"
    assert attrs["heat_pump_reported"] == 1
    assert "current_temperature" not in attrs
    assert "target_temperature" not in attrs


def test_heatpump_extra_state_attributes_with_temperatures() -> None:
    """Temperature keys present → attrs include them."""
    device = _heatpump_device(current_temperature=24, target_temperature=28)
    attrs = _heatpump(device).extra_state_attributes
    assert attrs["current_temperature"] == 24
    assert attrs["target_temperature"] == 28


async def test_heatpump_turn_on_non_z550_uses_start_pump() -> None:
    """Default heat pumps turn on via start_pump and refresh."""
    entity = _heatpump(_heatpump_device())
    await entity.async_turn_on()
    entity._api.start_pump.assert_awaited_once_with(DEVICE_ID)
    entity.coordinator.async_request_refresh.assert_awaited_once()
    assert entity._pending_state is True


async def test_heatpump_turn_off_non_z550_uses_stop_pump() -> None:
    """Default heat pumps turn off via stop_pump and refresh."""
    entity = _heatpump(_heatpump_device(is_running=True))
    await entity.async_turn_off()
    entity._api.stop_pump.assert_awaited_once_with(DEVICE_ID)
    entity.coordinator.async_request_refresh.assert_awaited_once()
    assert entity._pending_state is False


async def test_heatpump_turn_on_z550_uses_component_21() -> None:
    """z550_mode turns on via control_device_component(21, 1)."""
    entity = _heatpump(_heatpump_device(features={"z550_mode": True}))
    await entity.async_turn_on()
    entity._api.control_device_component.assert_awaited_once_with(DEVICE_ID, 21, 1)
    entity._api.start_pump.assert_not_awaited()
    entity.coordinator.async_request_refresh.assert_awaited_once()


async def test_heatpump_turn_off_z550_uses_component_21() -> None:
    """z550_mode turns off via control_device_component(21, 0)."""
    entity = _heatpump(_heatpump_device(features={"z550_mode": True}, is_running=True))
    await entity.async_turn_off()
    entity._api.control_device_component.assert_awaited_once_with(DEVICE_ID, 21, 0)
    entity._api.stop_pump.assert_not_awaited()
    entity.coordinator.async_request_refresh.assert_awaited_once()


async def test_heatpump_turn_on_returns_false_reverts() -> None:
    """start_pump False clears the optimistic state, raises and skips refresh."""
    entity = _heatpump(_heatpump_device(), start_pump=AsyncMock(return_value=False))
    with pytest.raises(HomeAssistantError):
        await entity.async_turn_on()
    assert entity._pending_state is None
    entity.coordinator.async_request_refresh.assert_not_awaited()


async def test_heatpump_turn_off_returns_false_reverts() -> None:
    """stop_pump False clears the optimistic state, raises and skips refresh."""
    entity = _heatpump(_heatpump_device(is_running=True), stop_pump=AsyncMock(return_value=False))
    with pytest.raises(HomeAssistantError):
        await entity.async_turn_off()
    assert entity._pending_state is None
    entity.coordinator.async_request_refresh.assert_not_awaited()


# =========================================================================
# FluidraHeaterSwitch
# =========================================================================


def _heater_device(features: dict | None = None, **extra: Any) -> dict:
    device = {
        "device_id": DEVICE_ID,
        "name": "Heater",
        "type": "heater",
        "online": True,
        "components": {},
    }
    device.update(extra)
    _identify(device, device_type="heater", entities=["switch"], features=features or {})
    return device


def _heater(device: dict, **api_overrides: Any) -> FluidraHeaterSwitch:
    entity = FluidraHeaterSwitch(_coordinator([device]), _api(**api_overrides), POOL_ID, DEVICE_ID)
    _attach_ha(entity)
    return entity


def test_heater_is_on_uses_is_heating() -> None:
    """is_heating is honoured for the heater state."""
    assert _heater(_heater_device(is_heating=True)).is_on is True


def test_heater_is_on_uses_is_running() -> None:
    """is_running alone marks the heater on."""
    assert _heater(_heater_device(is_running=True)).is_on is True
    assert _heater(_heater_device()).is_on is False


def test_heater_pending_state_shows_through() -> None:
    """Optimistic ON shows even with a falsy real state."""
    entity = _heater(_heater_device(is_heating=False))
    entity._set_pending_state(True)
    assert entity.is_on is True


def test_heater_pending_state_clears_on_expiry() -> None:
    """Expired optimistic state clears and yields the real value."""
    entity = _heater(_heater_device(is_heating=False))
    entity._set_pending_state(True)
    entity._last_action_time = 0.0
    assert entity.is_on is False
    assert entity._pending_state is None


def test_heater_icon_reflects_state() -> None:
    """Icon toggles between heat-wave (on) and snowflake (off)."""
    on = _heater(_heater_device(is_heating=True))
    off = _heater(_heater_device())
    assert on.icon == "mdi:heat-wave"
    assert off.icon == "mdi:snowflake"


def test_heater_unique_id() -> None:
    """unique_id follows the documented domain/pool/device pattern."""
    entity = _heater(_heater_device())
    assert entity.unique_id == f"fluidra_pool_{POOL_ID}_{DEVICE_ID}_heater"


def test_heater_extra_state_attributes_without_temperatures() -> None:
    """No temperature keys → empty attributes dict."""
    attrs = _heater(_heater_device()).extra_state_attributes
    assert attrs == {}


def test_heater_extra_state_attributes_with_temperatures() -> None:
    """Temperature keys present → attrs include them."""
    device = _heater_device(current_temperature=22, target_temperature=27)
    attrs = _heater(device).extra_state_attributes
    assert attrs == {"current_temperature": 22, "target_temperature": 27}


async def test_heater_turn_on_success_writes_component_9_one() -> None:
    """Heater ON writes 1 to the generic on/off component and refreshes."""
    entity = _heater(_heater_device())
    await entity.async_turn_on()
    entity._api.control_device_component.assert_awaited_once_with(DEVICE_ID, 9, 1)
    entity.coordinator.async_request_refresh.assert_awaited_once()
    assert entity._pending_state is True


async def test_heater_turn_off_success_writes_component_9_zero() -> None:
    """Heater OFF writes 0 to the generic on/off component and refreshes."""
    entity = _heater(_heater_device(is_heating=True))
    await entity.async_turn_off()
    entity._api.control_device_component.assert_awaited_once_with(DEVICE_ID, 9, 0)
    entity.coordinator.async_request_refresh.assert_awaited_once()
    assert entity._pending_state is False


async def test_heater_turn_on_returns_false_reverts() -> None:
    """API False on heater ON clears the optimistic state, raises, no refresh."""
    entity = _heater(_heater_device(), control_device_component=AsyncMock(return_value=False))
    with pytest.raises(HomeAssistantError):
        await entity.async_turn_on()
    assert entity._pending_state is None
    entity.coordinator.async_request_refresh.assert_not_awaited()


async def test_heater_turn_off_returns_false_reverts() -> None:
    """API False on heater OFF clears the optimistic state, raises, no refresh."""
    entity = _heater(_heater_device(is_heating=True), control_device_component=AsyncMock(return_value=False))
    with pytest.raises(HomeAssistantError):
        await entity.async_turn_off()
    assert entity._pending_state is None
    entity.coordinator.async_request_refresh.assert_not_awaited()
