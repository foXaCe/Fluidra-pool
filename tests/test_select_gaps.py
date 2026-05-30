"""Coverage-gap tests for select.pump and select.chlorinator.

Focus on SUCCESS paths, properties, and branches NOT covered by
test_select.py / test_entity_error_paths.py:

pump.py:
  - available: auto-off branches (auto_reported / auto_mode_enabled fallback),
    coordinator-success + online → True
  - current_option: optimistic precedence, "stopped" when not running,
    speed_level_reported 0/1/2, speed_percent fallback 0/45/65/100
  - async_select_option SUCCESS for "stopped" and for low/medium/high
  - icon (auto vs manual)
  - extra_state_attributes (auto on/off branches + control_status strings)

chlorinator.py:
  - _get_api_mode (mode_mapping override, default, invalid value → off)
  - current_option (optimistic not expired / expired / none)
  - _optimistic_expired
  - _handle_coordinator_update (clears on confirm / on expiry / keeps otherwise)
  - icon (off/on/auto)
  - extra_state_attributes
  - async_select_option SUCCESS + option-not-in-mapping early return
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.fluidra_pool.select import (
    FluidraChlorinatorModeSelect,
    FluidraPumpSpeedSelect,
)

POOL_ID = "pool-1"
PUMP_ID = "TEST-PUMP-001"
CHLOR_ID = "TEST-CHLOR-002"


def _coord_with(device: dict) -> Any:
    coordinator = MagicMock()
    coordinator.data = {POOL_ID: {"id": POOL_ID, "name": "Pool", "devices": [device]}}
    coordinator.async_request_refresh = AsyncMock()
    coordinator.last_update_success = True
    return coordinator


def _api() -> SimpleNamespace:
    return SimpleNamespace(control_device_component=AsyncMock(return_value=True))


def _attach_ha(entity) -> None:
    entity.hass = MagicMock()
    entity.async_write_ha_state = MagicMock()


def _pinned_device(device_id: str, features: dict | None = None, **extra: Any) -> dict:
    """Build a device dict whose _identify_cache pins identify_device to fixed features."""
    components = extra.pop("components", {})
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
                device_type="generic",
                features=features or {},
                components_range=25,
                required_components=[0, 1, 2, 3],
                entities=[],
            ),
        },
    }
    device.update(extra)
    return device


@pytest.fixture(autouse=True)
def _skip_delays() -> Any:
    """Don't actually sleep during optimistic command sequences."""
    with (
        patch("custom_components.fluidra_pool.select.pump.asyncio.sleep", new=AsyncMock()),
        patch("custom_components.fluidra_pool.select.chlorinator.asyncio.sleep", new=AsyncMock()),
    ):
        yield


# ======================================================================
# FluidraPumpSpeedSelect
# ======================================================================


def _pump_speed(device_extra: dict, *, api: SimpleNamespace | None = None) -> FluidraPumpSpeedSelect:
    device = _pinned_device(PUMP_ID, **device_extra)
    select = FluidraPumpSpeedSelect(_coord_with(device), api or _api(), POOL_ID, PUMP_ID)
    _attach_ha(select)
    return select


# --- available ---------------------------------------------------------


def test_pump_available_auto_reported_zero_then_success_online_true() -> None:
    """auto_reported=0 means auto OFF → falls through to coordinator+online check."""
    select = _pump_speed({"auto_reported": 0, "online": True})
    assert select.available is True


def test_pump_available_auto_mode_enabled_fallback_true_blocks() -> None:
    """Without auto_reported, auto_mode_enabled fallback blocks availability."""
    select = _pump_speed({"auto_mode_enabled": True, "online": True})
    assert select.available is False


def test_pump_available_fallback_false_then_online_true() -> None:
    """No auto_reported, auto_mode_enabled=False → available when online + success."""
    select = _pump_speed({"auto_mode_enabled": False, "online": True})
    assert select.available is True


def test_pump_available_offline_returns_false() -> None:
    """Auto off but device offline → unavailable."""
    select = _pump_speed({"auto_reported": 0, "online": False})
    assert select.available is False


def test_pump_available_coordinator_failed_returns_false() -> None:
    """Auto off + online but coordinator last update failed → unavailable."""
    select = _pump_speed({"auto_reported": 0, "online": True})
    select.coordinator.last_update_success = False
    assert select.available is False


# --- current_option ----------------------------------------------------


def test_pump_current_option_optimistic_precedence() -> None:
    """Optimistic option overrides reported state."""
    select = _pump_speed({"is_running": True, "speed_level_reported": 2})
    select._optimistic_option = "low"
    assert select.current_option == "low"


def test_pump_current_option_not_running_is_stopped() -> None:
    """Not running → stopped."""
    select = _pump_speed({"is_running": False, "speed_percent": 100})
    assert select.current_option == "stopped"


@pytest.mark.parametrize(
    ("level", "expected"),
    [(0, "low"), (1, "medium"), (2, "high")],
)
def test_pump_current_option_speed_level_reported(level: int, expected: str) -> None:
    """speed_level_reported 0/1/2 → low/medium/high."""
    select = _pump_speed({"is_running": True, "speed_level_reported": level})
    assert select.current_option == expected


def test_pump_current_option_speed_level_unknown_defaults_low() -> None:
    """An out-of-range speed_level falls back to low."""
    select = _pump_speed({"is_running": True, "speed_level_reported": 9})
    assert select.current_option == "low"


@pytest.mark.parametrize(
    ("percent", "expected"),
    [(0, "stopped"), (45, "low"), (65, "medium"), (100, "high")],
)
def test_pump_current_option_speed_percent_fallback(percent: int, expected: str) -> None:
    """Without speed_level_reported, speed_percent maps back to a label."""
    select = _pump_speed({"is_running": True, "speed_percent": percent})
    assert select.current_option == expected


def test_pump_current_option_speed_percent_unknown_defaults_low() -> None:
    """A non-mapped non-zero percent falls back to low."""
    select = _pump_speed({"is_running": True, "speed_percent": 33})
    assert select.current_option == "low"


# --- async_select_option SUCCESS --------------------------------------


async def test_pump_select_stopped_writes_onoff_and_clears_speed() -> None:
    """'stopped' writes component 9 (ON) then best-effort clears speed via component 11=-1."""
    select = _pump_speed({"is_running": True, "online": True})
    await select.async_select_option("stopped")

    calls = select._api.control_device_component.await_args_list
    assert calls[0].args == (PUMP_ID, 9, 1)  # pump ON / natural state
    assert calls[1].args == (PUMP_ID, 11, -1)  # best-effort speed clear
    select.coordinator.async_request_refresh.assert_awaited_once()
    # Optimistic flag is cleared in the finally block.
    assert select._optimistic_option is None


@pytest.mark.parametrize(
    ("option", "expected_value"),
    [("low", 0), ("medium", 1), ("high", 2)],
)
async def test_pump_select_speed_writes_onoff_then_component(option: str, expected_value: int) -> None:
    """low/medium/high first turn pump ON (comp 9) then set comp 11 to the level."""
    select = _pump_speed({"is_running": False, "online": True})
    await select.async_select_option(option)

    calls = select._api.control_device_component.await_args_list
    assert calls[0].args == (PUMP_ID, 9, 1)
    assert calls[1].args == (PUMP_ID, 11, expected_value)
    select.coordinator.async_request_refresh.assert_awaited_once()
    assert select._optimistic_option is None


async def test_pump_select_optimistic_set_during_call() -> None:
    """The optimistic option is set before the API call (visible mid-flight)."""
    seen: list[str | None] = []

    async def _capture(*_args, **_kwargs):
        seen.append(select._optimistic_option)
        return True

    select = _pump_speed({"is_running": False, "online": True})
    select._api.control_device_component = AsyncMock(side_effect=_capture)
    await select.async_select_option("medium")
    assert seen
    assert seen[0] == "medium"


# --- icon --------------------------------------------------------------


def test_pump_icon_auto_mode_reported() -> None:
    """auto_reported truthy → autorenew icon."""
    select = _pump_speed({"auto_reported": 1})
    assert select.icon == "mdi:autorenew"


def test_pump_icon_auto_mode_enabled_fallback() -> None:
    """auto_mode_enabled fallback → autorenew icon."""
    select = _pump_speed({"auto_mode_enabled": True})
    assert select.icon == "mdi:autorenew"


def test_pump_icon_manual_stopped() -> None:
    """Manual + stopped → pump icon."""
    select = _pump_speed({"auto_reported": 0, "is_running": False})
    assert select.icon == "mdi:pump"


def test_pump_icon_manual_running() -> None:
    """Manual + running → pump icon."""
    select = _pump_speed({"auto_reported": 0, "is_running": True, "speed_level_reported": 1})
    assert select.icon == "mdi:pump"


# --- extra_state_attributes -------------------------------------------


def test_pump_attrs_auto_branch() -> None:
    """Auto mode reported → control_status / manual_control_disabled reflect auto lock."""
    select = _pump_speed(
        {
            "auto_reported": 1,
            "speed_percent": 65,
            "model": "VS3",
            "pump_type": "variable_speed",
            "operation_mode": 2,
            "online": True,
        }
    )
    attrs = select.extra_state_attributes
    assert attrs["auto_mode"] is True
    assert attrs["control_status"] == "Contrôlé par le mode automatique"
    assert attrs["manual_control_disabled"] is True
    assert attrs["speed_percent"] == 65
    assert attrs["pump_model"] == "VS3"
    assert attrs["pump_type"] == "variable_speed"
    assert attrs["operation_mode"] == 2
    assert attrs["online"] is True
    assert attrs["using_optimistic"] is False
    assert attrs["optimistic_option"] is None


def test_pump_attrs_manual_branch_with_fallback_and_optimistic() -> None:
    """No auto_reported + auto_mode_enabled False → manual branch; optimistic surfaces."""
    select = _pump_speed({"auto_mode_enabled": False, "speed_percent": 45})
    select._optimistic_option = "low"
    attrs = select.extra_state_attributes
    assert attrs["auto_mode"] is False
    assert attrs["control_status"] == "Contrôle manuel disponible"
    assert attrs["manual_control_disabled"] is False
    assert attrs["using_optimistic"] is True
    assert attrs["optimistic_option"] == "low"
    # The pinned device dict carries an explicit empty model, so the "" value is
    # surfaced (the "E30iQ" default only applies when the key is absent).
    assert attrs["pump_model"] == ""
    # pump_type / operation_mode keys are absent → their defaults apply.
    assert attrs["pump_type"] == "variable_speed"
    assert attrs["operation_mode"] == 0


# ======================================================================
# FluidraChlorinatorModeSelect
# ======================================================================


def _chlor(
    components: dict | None = None,
    features: dict | None = None,
    **extra: Any,
) -> FluidraChlorinatorModeSelect:
    device = _pinned_device(
        CHLOR_ID,
        features=features if features is not None else {"mode_component": 20},
        components=components or {"20": {"reportedValue": 2}},
        **extra,
    )
    select = FluidraChlorinatorModeSelect(_coord_with(device), _api(), POOL_ID, CHLOR_ID)
    _attach_ha(select)
    return select


# --- _get_api_mode -----------------------------------------------------


def test_chlor_get_api_mode_default_mapping() -> None:
    """Default mapping decodes component value via mode_component (20)."""
    select = _chlor({"20": {"reportedValue": 1}})
    assert select._get_api_mode() == "on"


def test_chlor_get_api_mode_custom_mode_mapping_override() -> None:
    """A per-device mode_mapping override changes value→label decoding."""
    select = _chlor(
        components={"15": {"reportedValue": 3}},
        features={"mode_component": 15, "mode_mapping": {"3": "auto", "0": "off", "5": "on"}},
    )
    assert select._value_to_mode == {3: "auto", 0: "off", 5: "on"}
    assert select._get_api_mode() == "auto"


def test_chlor_get_api_mode_invalid_value_defaults_off() -> None:
    """A non-int reportedValue is coerced to 0 → off."""
    select = _chlor({"20": {"reportedValue": "not-a-number"}})
    assert select._get_api_mode() == "off"


def test_chlor_get_api_mode_value_not_in_mapping_defaults_off() -> None:
    """An int value absent from the mapping defaults to off."""
    select = _chlor({"20": {"reportedValue": 7}})
    assert select._get_api_mode() == "off"


def test_chlor_get_api_mode_falls_back_to_mode_reported() -> None:
    """When the component is missing reportedValue, fall back to mode_reported."""
    select = _chlor({"20": {}}, mode_reported=2)
    assert select._get_api_mode() == "auto"


# --- _optimistic_expired ----------------------------------------------


def test_chlor_optimistic_not_expired_recent() -> None:
    """A just-set optimistic timestamp is not expired."""
    select = _chlor()
    select._optimistic_time = time.time()
    assert select._optimistic_expired() is False


def test_chlor_optimistic_expired_old() -> None:
    """A timestamp older than OPTIMISTIC_TIMEOUT is expired."""
    select = _chlor()
    select._optimistic_time = time.time() - (select.OPTIMISTIC_TIMEOUT + 10)
    assert select._optimistic_expired() is True


# --- current_option ----------------------------------------------------


def test_chlor_current_option_optimistic_not_expired() -> None:
    """Recent optimistic value wins over API mode."""
    select = _chlor({"20": {"reportedValue": 0}})  # api would say off
    select._optimistic_option = "auto"
    select._optimistic_time = time.time()
    assert select.current_option == "auto"


def test_chlor_current_option_optimistic_expired_uses_api() -> None:
    """An expired optimistic value is ignored; API mode is returned."""
    select = _chlor({"20": {"reportedValue": 1}})  # api: on
    select._optimistic_option = "auto"
    select._optimistic_time = time.time() - (select.OPTIMISTIC_TIMEOUT + 10)
    assert select.current_option == "on"


def test_chlor_current_option_no_optimistic_uses_api() -> None:
    """No optimistic value → API mode."""
    select = _chlor({"20": {"reportedValue": 2}})
    assert select._optimistic_option is None
    assert select.current_option == "auto"


# --- _handle_coordinator_update ---------------------------------------


def test_chlor_handle_update_clears_when_api_confirms() -> None:
    """Optimistic value cleared once API mode matches it."""
    select = _chlor({"20": {"reportedValue": 2}})  # api: auto
    select._optimistic_option = "auto"
    select._optimistic_time = time.time()
    select._handle_coordinator_update()
    assert select._optimistic_option is None
    select.async_write_ha_state.assert_called()


def test_chlor_handle_update_clears_when_expired() -> None:
    """Optimistic value cleared once it expires even if API disagrees."""
    select = _chlor({"20": {"reportedValue": 1}})  # api: on (≠ auto)
    select._optimistic_option = "auto"
    select._optimistic_time = time.time() - (select.OPTIMISTIC_TIMEOUT + 10)
    select._handle_coordinator_update()
    assert select._optimistic_option is None


def test_chlor_handle_update_keeps_when_unconfirmed_and_fresh() -> None:
    """Optimistic value kept while API still disagrees and it hasn't expired."""
    select = _chlor({"20": {"reportedValue": 1}})  # api: on (≠ auto)
    select._optimistic_option = "auto"
    select._optimistic_time = time.time()
    select._handle_coordinator_update()
    assert select._optimistic_option == "auto"


def test_chlor_handle_update_no_optimistic_is_noop() -> None:
    """With no optimistic value, the update just calls through to the base."""
    select = _chlor({"20": {"reportedValue": 0}})
    select._handle_coordinator_update()
    assert select._optimistic_option is None
    select.async_write_ha_state.assert_called()


# --- icon --------------------------------------------------------------


def test_chlor_icon_off() -> None:
    """off → water-off icon."""
    select = _chlor({"20": {"reportedValue": 0}})
    assert select.icon == "mdi:water-off"


def test_chlor_icon_on() -> None:
    """on → water icon."""
    select = _chlor({"20": {"reportedValue": 1}})
    assert select.icon == "mdi:water"


def test_chlor_icon_auto() -> None:
    """auto (and anything else) → water-sync icon."""
    select = _chlor({"20": {"reportedValue": 2}})
    assert select.icon == "mdi:water-sync"


# --- extra_state_attributes -------------------------------------------


def test_chlor_extra_state_attributes() -> None:
    """Attributes expose device_id, the configured mode_component, and optimistic value."""
    select = _chlor(components={"20": {"reportedValue": 2}}, features={"mode_component": 20})
    select._optimistic_option = "on"
    attrs = select.extra_state_attributes
    assert attrs["device_id"] == CHLOR_ID
    assert attrs["mode_component"] == 20
    assert attrs["optimistic_option"] == "on"


# --- async_select_option ----------------------------------------------


async def test_chlor_select_success_sets_optimistic_and_refreshes() -> None:
    """Successful select sets optimistic state, writes value, and refreshes."""
    select = _chlor(components={"20": {"reportedValue": 0}}, features={"mode_component": 20})
    await select.async_select_option("on")
    select._api.control_device_component.assert_awaited_once_with(CHLOR_ID, 20, 1)
    select.coordinator.async_request_refresh.assert_awaited_once()
    # On success the optimistic value is intentionally retained until confirmed.
    assert select._optimistic_option == "on"
    assert select._optimistic_time > 0


async def test_chlor_select_uses_custom_mapping_and_component() -> None:
    """Custom mode_mapping + mode_component route the write to the right value/comp."""
    select = _chlor(
        components={"15": {"reportedValue": 0}},
        features={"mode_component": 15, "mode_mapping": {"0": "off", "5": "on", "9": "auto"}},
    )
    await select.async_select_option("auto")
    select._api.control_device_component.assert_awaited_once_with(CHLOR_ID, 15, 9)


async def test_chlor_select_unknown_option_early_return() -> None:
    """An option not present in the mapping returns immediately, no API call."""
    select = _chlor()
    await select.async_select_option("turbo")
    select._api.control_device_component.assert_not_called()
    select.coordinator.async_request_refresh.assert_not_called()
    assert select._optimistic_option is None
