"""Tests for the select platform (pump speed, chlorinator mode, light effect…)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
import pytest

from custom_components.fluidra_pool.select import (
    FluidraChlorinatorModeSelect,
    FluidraLightEffectSelect,
    FluidraPumpSpeedSelect,
    async_setup_entry,
)

POOL_ID = "pool-1"
PUMP_ID = "TEST-PUMP-001"
CHLOR_ID = "TEST-CHLOR-002"
LIGHT_ID = "TEST-LIGHT-003"


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
    """Don't actually sleep between optimistic write and API call."""
    with (
        patch("custom_components.fluidra_pool.select.pump.asyncio.sleep", new=AsyncMock()),
        patch("custom_components.fluidra_pool.select.chlorinator.asyncio.sleep", new=AsyncMock()),
        patch("custom_components.fluidra_pool.select.light.asyncio.sleep", new=AsyncMock()),
        patch("custom_components.fluidra_pool.select.schedule.asyncio.sleep", new=AsyncMock()),
    ):
        yield


# --- FluidraPumpSpeedSelect ---------------------------------------------


def _pump_speed(device_extra: dict) -> FluidraPumpSpeedSelect:
    device = _pinned_device(PUMP_ID, **device_extra)
    select = FluidraPumpSpeedSelect(_coord_with(device), _api(), POOL_ID, PUMP_ID)
    _attach_ha(select)
    return select


def test_pump_speed_returns_stopped_when_pump_off() -> None:
    """Pump off → option = stopped regardless of speed_percent."""
    select = _pump_speed({"is_running": False, "speed_percent": 65})
    assert select.current_option == "stopped"


@pytest.mark.parametrize(
    ("speed_level", "expected"),
    [(0, "low"), (1, "medium"), (2, "high")],
)
def test_pump_speed_uses_speed_level_reported(speed_level: int, expected: str) -> None:
    """speed_level_reported (component 11) drives the option when running."""
    select = _pump_speed({"is_running": True, "speed_level_reported": speed_level})
    assert select.current_option == expected


def test_pump_speed_falls_back_to_percentage_mapping() -> None:
    """Without speed_level_reported, the percentage is mapped back to a label."""
    select = _pump_speed({"is_running": True, "speed_percent": 100})
    assert select.current_option == "high"


def test_pump_speed_optimistic_option_takes_precedence() -> None:
    """An optimistic option overrides whatever the device currently reports."""
    select = _pump_speed({"is_running": True, "speed_level_reported": 0})
    select._optimistic_option = "high"
    assert select.current_option == "high"


def test_pump_speed_stays_available_when_auto_mode_active() -> None:
    """Auto mode no longer hides the entity: the state stays readable."""
    select = _pump_speed({"is_running": True, "auto_reported": 1, "online": True, "speed_percent": 65})
    assert select.available is True
    assert select.current_option == "medium"


async def test_pump_speed_select_rejected_in_auto_mode() -> None:
    """A manual write while auto mode drives the pump raises a clear error."""
    select = _pump_speed({"is_running": True, "auto_reported": 1, "online": True})
    with pytest.raises(ServiceValidationError):
        await select.async_select_option("high")
    select._api.control_device_component.assert_not_awaited()


async def test_pump_speed_async_select_low_writes_pump_on_then_speed() -> None:
    """Selecting low first turns the pump ON then sets speed level 0 on component 11."""
    select = _pump_speed({"is_running": False, "online": True})
    await select.async_select_option("low")

    calls = select._api.control_device_component.await_args_list
    assert calls[0].args == (PUMP_ID, 9, 1)  # pump ON
    assert calls[1].args == (PUMP_ID, 11, 0)  # speed level low
    select.coordinator.async_request_refresh.assert_awaited_once()


async def test_pump_speed_async_select_unknown_option_is_noop() -> None:
    """A bogus option is silently ignored, no API calls."""
    select = _pump_speed({"is_running": False})
    await select.async_select_option("warp_speed")
    select._api.control_device_component.assert_not_called()


# --- FluidraChlorinatorModeSelect ---------------------------------------


def _chlor_mode(components: dict | None = None) -> FluidraChlorinatorModeSelect:
    device = _pinned_device(
        CHLOR_ID,
        features={"mode_component": 20},
        components=components or {"20": {"reportedValue": 2}},
    )
    select = FluidraChlorinatorModeSelect(_coord_with(device), _api(), POOL_ID, CHLOR_ID)
    _attach_ha(select)
    return select


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0, "off"), (1, "on"), (2, "auto")],
)
def test_chlor_mode_current_option_decodes_value(value: int, expected: str) -> None:
    """Component 20 raw value maps to the off/on/auto label."""
    select = _chlor_mode({"20": {"reportedValue": value}})
    assert select.current_option == expected


def test_chlor_mode_unknown_value_defaults_to_off() -> None:
    """A value outside the known set defaults to off (safe fallback)."""
    select = _chlor_mode({"20": {"reportedValue": 99}})
    assert select.current_option == "off"


async def test_chlor_mode_async_select_writes_value_to_mode_component() -> None:
    """Selecting auto writes value=2 on the configured mode_component."""
    select = _chlor_mode()
    await select.async_select_option("auto")
    select._api.control_device_component.assert_awaited_once_with(CHLOR_ID, 20, 2)
    select.coordinator.async_request_refresh.assert_awaited_once()


async def test_chlor_mode_async_select_on_failure_clears_optimistic() -> None:
    """If the API rejects the write, the optimistic option is rolled back."""
    select = _chlor_mode()
    select._api.control_device_component = AsyncMock(return_value=False)
    await select.async_select_option("on")
    assert select._optimistic_option is None


# --- FluidraLightEffectSelect -------------------------------------------


def _light_effect(comp_18: int | None) -> FluidraLightEffectSelect:
    components: dict[str, dict] = {}
    if comp_18 is not None:
        components["18"] = {"reportedValue": comp_18}
    device = _pinned_device(LIGHT_ID, features={"effect_select": 18}, components=components)
    select = FluidraLightEffectSelect(_coord_with(device), _api(), POOL_ID, LIGHT_ID)
    _attach_ha(select)
    return select


def test_light_effect_decodes_static_color_zero() -> None:
    """Effect value 0 is the static-colour scene."""
    select = _light_effect(0)
    assert select.current_option == "static_color"


@pytest.mark.parametrize(
    ("value", "expected"),
    [(1, "scene_1"), (4, "scene_4"), (8, "scene_8")],
)
def test_light_effect_decodes_numbered_scenes(value: int, expected: str) -> None:
    """Effect values 1-8 map to scene_1 through scene_8."""
    select = _light_effect(value)
    assert select.current_option == expected


async def test_light_effect_async_select_writes_component_18() -> None:
    """Selecting scene_5 writes value=5 to component 18 via control_device_component."""
    select = _light_effect(0)
    await select.async_select_option("scene_5")
    select._api.control_device_component.assert_awaited_once_with(LIGHT_ID, 18, 5)


async def test_light_effect_async_select_ignores_unknown_option() -> None:
    """An unknown effect string is a no-op."""
    select = _light_effect(0)
    await select.async_select_option("scene_99")
    select._api.control_device_component.assert_not_called()


def test_light_effect_decodes_string_reported_value() -> None:
    """A string reportedValue is coerced so the scene resolves instead of falling back (select_time-2)."""
    select = _light_effect("3")  # type: ignore[arg-type]  # the backend can report a string
    assert select.current_option == "scene_3"


async def test_light_effect_async_select_raises_on_api_rejection() -> None:
    """A False return from the backend surfaces as HomeAssistantError (select_time-4)."""
    select = _light_effect(0)
    select._api.control_device_component = AsyncMock(return_value=False)

    with pytest.raises(HomeAssistantError):
        await select.async_select_option("scene_5")

    # The finally block still clears the optimistic option on the way out.
    assert select._optimistic_option is None
    select.coordinator.async_request_refresh.assert_not_awaited()


def test_light_effect_optimistic_option_takes_precedence() -> None:
    """A pending optimistic option overrides the reported component value."""
    select = _light_effect(0)  # device reports static_color
    select._optimistic_option = "scene_7"
    assert select.current_option == "scene_7"


def test_light_effect_uncoercible_value_falls_back_to_static_color() -> None:
    """A non-numeric reportedValue can't be int()-coerced → static_color (lines 96-97)."""
    select = _light_effect("not-a-number")  # type: ignore[arg-type]
    assert select.current_option == "static_color"


def test_light_effect_icon_is_palette() -> None:
    """The light effect select always uses the palette icon."""
    select = _light_effect(0)
    assert select.icon == "mdi:palette"


def test_light_effect_extra_state_attributes() -> None:
    """Attributes expose device_id, effect component, reported/desired values and optimistic option."""
    device = _pinned_device(
        LIGHT_ID,
        features={"effect_select": 18},
        components={"18": {"reportedValue": 3, "desiredValue": 5}},
    )
    select = FluidraLightEffectSelect(_coord_with(device), _api(), POOL_ID, LIGHT_ID)
    _attach_ha(select)
    select._optimistic_option = "scene_2"
    attrs = select.extra_state_attributes
    assert attrs["device_id"] == LIGHT_ID
    assert attrs["effect_component"] == FluidraLightEffectSelect.EFFECT_COMPONENT
    assert attrs["reported_value"] == 3
    assert attrs["desired_value"] == 5
    assert attrs["optimistic_option"] == "scene_2"


def test_light_effect_extra_state_attributes_missing_component() -> None:
    """With no component data, reported/desired values are None."""
    select = _light_effect(None)  # no component 18 in the device
    attrs = select.extra_state_attributes
    assert attrs["reported_value"] is None
    assert attrs["desired_value"] is None
    assert attrs["optimistic_option"] is None


# --- async_setup_entry — dynamic-devices wiring ------------------------------


def _pinned_pump(device_id: str) -> dict:
    """Build a variable-speed pump device that yields a FluidraPumpSpeedSelect."""
    device = _pinned_device(
        device_id,
        device_type="pump",
        entities=["select"],
        variable_speed=True,
    )
    # _pinned_device pins device_type="generic"/entities=[]; override the cache.
    device["_identify_cache"]["config"].device_type = "pump"
    device["_identify_cache"]["config"].entities = ["select"]
    return device


async def test_setup_adds_new_device_dynamically() -> None:
    """dynamic-devices: a device appearing on a later poll is wired without a reload."""
    dev1 = _pinned_pump("dev1")
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
    pool["devices"].append(_pinned_pump("dev2"))
    listeners[0]()

    new_uids = {e.unique_id for e in added} - uids_after_setup
    assert new_uids, "new device entities should be added without a reload"
    assert all("dev2" in u for u in new_uids), "only the newly-added device's entities are created"


def test_pump_speed_auto_mode_zero_percent_reads_stopped() -> None:
    """In auto mode with 0% derived speed, the select reads 'stopped'."""
    select = _pump_speed({"is_running": True, "auto_reported": 1, "online": True, "speed_percent": 0})
    assert select.current_option == "stopped"


async def test_pump_speed_select_raises_when_api_reports_failure() -> None:
    """success=False from the API surfaces as HomeAssistantError (error convention)."""
    select = _pump_speed({"is_running": False, "online": True})
    select._api.control_device_component.return_value = False
    with pytest.raises(HomeAssistantError):
        await select.async_select_option("low")
    assert select._optimistic_option is None
