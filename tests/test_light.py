"""Tests for the LumiPlus Connect light platform."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from homeassistant.components.light import ATTR_BRIGHTNESS, ATTR_RGBW_COLOR
from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.fluidra_pool.const import (
    LUMIPLUS_COMPONENT_BRIGHTNESS,
    LUMIPLUS_COMPONENT_COLOR,
    LUMIPLUS_COMPONENT_POWER,
)
from custom_components.fluidra_pool.light import FluidraLight, async_setup_entry

POOL_ID = "pool-1"
DEVICE_ID = "LP24-001"


def _build_light(components: dict | None = None) -> FluidraLight:
    """Instantiate a FluidraLight wired to a fake coordinator/api."""
    coordinator = MagicMock()
    coordinator.data = {
        POOL_ID: {
            "id": POOL_ID,
            "name": "Pool",
            "devices": [
                {
                    "device_id": DEVICE_ID,
                    "name": "Light",
                    "type": "light",
                    "components": components or {},
                }
            ],
        }
    }
    coordinator.async_request_refresh = AsyncMock()
    coordinator.last_update_success = True

    api = SimpleNamespace(
        set_component_value=AsyncMock(return_value=True),
        set_component_json_value=AsyncMock(return_value=True),
        set_component_string_value=AsyncMock(return_value=True),
    )

    light = FluidraLight(coordinator, api, POOL_ID, DEVICE_ID)
    light.hass = MagicMock()
    light.async_write_ha_state = MagicMock()
    return light


def test_is_on_reads_power_component_when_no_optimistic_state() -> None:
    """Without optimistic state, is_on reflects the LumiPlus power component."""
    light = _build_light({str(LUMIPLUS_COMPONENT_POWER): {"reportedValue": "1"}})
    assert light.is_on is True


def test_is_on_returns_false_when_no_reported_value() -> None:
    """Missing reported_value yields False rather than blowing up."""
    light = _build_light({})
    assert light.is_on is False


def test_is_on_handles_non_numeric_reported_value() -> None:
    """Garbage strings on the power component are treated as off (no crash)."""
    light = _build_light({str(LUMIPLUS_COMPONENT_POWER): {"reportedValue": "yes"}})
    assert light.is_on is False


def test_brightness_converts_0_100_scale_to_0_255() -> None:
    """Brightness reported on the 0-100 scale is mapped to HA's 0-255 scale."""
    light = _build_light({str(LUMIPLUS_COMPONENT_BRIGHTNESS): {"reportedValue": 50}})
    # 50 * 255 / 100 = 127.5 → rounded to 128
    assert light.brightness == 128


def test_brightness_missing_returns_none() -> None:
    """Without a brightness reading, brightness is None (unavailable)."""
    light = _build_light({})
    assert light.brightness is None


def test_rgbw_color_reads_rgb_plus_white_extra() -> None:
    """RGBW value is reconstructed from r/g/b and the nested extra.w field."""
    light = _build_light(
        {str(LUMIPLUS_COMPONENT_COLOR): {"reportedValue": {"r": 255, "g": 128, "b": 64, "extra": {"w": 200}}}}
    )
    assert light.rgbw_color == (255, 128, 64, 200)


def test_rgbw_color_returns_none_for_non_dict_value() -> None:
    """A non-dict color component value is treated as unavailable."""
    light = _build_light({str(LUMIPLUS_COMPONENT_COLOR): {"reportedValue": "off"}})
    assert light.rgbw_color is None


async def test_async_turn_on_writes_power_brightness_color_and_requests_refresh() -> None:
    """turn_on issues the three component writes and triggers a coordinator refresh."""
    light = _build_light()
    await light.async_turn_on(**{ATTR_BRIGHTNESS: 255, ATTR_RGBW_COLOR: (10, 20, 30, 40)})

    # Optimistic state set immediately so the UI flips before the refresh lands.
    assert light.is_on is True
    assert light.brightness == 255
    assert light.rgbw_color == (10, 20, 30, 40)

    # Brightness goes back to 0-100 on the wire.
    light._api.set_component_value.assert_awaited_once_with(DEVICE_ID, LUMIPLUS_COMPONENT_BRIGHTNESS, 100)
    # Color payload keeps the RGBW + colour-temperature shape Fluidra expects.
    light._api.set_component_json_value.assert_awaited_once_with(
        DEVICE_ID,
        LUMIPLUS_COMPONENT_COLOR,
        {"r": 10, "g": 20, "b": 30, "k": 5000, "extra": {"w": 40}},
    )
    light._api.set_component_string_value.assert_awaited_once_with(DEVICE_ID, LUMIPLUS_COMPONENT_POWER, "1")
    light.coordinator.async_request_refresh.assert_awaited_once()


async def test_async_turn_on_raises_and_rolls_back_when_brightness_write_fails() -> None:
    """A False return from the brightness sub-command must fail and roll back (climate_light_number-3).

    Previously only the power write's result was checked, so a rejected brightness
    or colour write left the optimistic overrides stuck on the card indefinitely.
    """
    light = _build_light()
    light._api.set_component_value = AsyncMock(return_value=False)  # brightness rejected

    with pytest.raises(HomeAssistantError):
        await light.async_turn_on(**{ATTR_BRIGHTNESS: 255})

    assert light._optimistic_is_on is None
    assert light._optimistic_brightness is None
    light.coordinator.async_request_refresh.assert_not_awaited()


async def test_async_turn_off_sets_power_to_zero() -> None:
    """turn_off writes the power component to "0" and refreshes the coordinator."""
    light = _build_light({str(LUMIPLUS_COMPONENT_POWER): {"reportedValue": "1"}})
    await light.async_turn_off()

    assert light.is_on is False
    light._api.set_component_string_value.assert_awaited_once_with(DEVICE_ID, LUMIPLUS_COMPONENT_POWER, "0")
    light.coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.parametrize(("reported", "expected_keeps_optimistic"), [("1", False), ("0", True)])
def test_handle_coordinator_update_clears_optimistic_when_backend_caught_up(
    reported: str, expected_keeps_optimistic: bool
) -> None:
    """Optimistic ON drops as soon as the backend echoes 1; stays put otherwise."""
    light = _build_light({str(LUMIPLUS_COMPONENT_POWER): {"reportedValue": reported}})
    light._optimistic_is_on = True
    light._handle_coordinator_update()
    assert (light._optimistic_is_on is True) is expected_keeps_optimistic


# --- brightness / rgbw_color guarded parse paths -------------------------


def test_brightness_returns_none_on_non_numeric_reported_value() -> None:
    """A non-numeric brightness reportedValue hits the guarded except -> None (lines 127-128)."""
    light = _build_light({str(LUMIPLUS_COMPONENT_BRIGHTNESS): {"reportedValue": "bright"}})
    assert light.brightness is None


def test_rgbw_color_returns_none_on_non_numeric_channel() -> None:
    """A non-numeric colour channel triggers the guarded except -> None (lines 143-144)."""
    light = _build_light(
        {str(LUMIPLUS_COMPONENT_COLOR): {"reportedValue": {"r": "x", "g": 0, "b": 0, "extra": {"w": 0}}}}
    )
    assert light.rgbw_color is None


# --- _handle_coordinator_update: brightness + rgbw clearing & guards -------


def test_handle_coordinator_update_power_bad_reported_value_swallowed() -> None:
    """A non-numeric reported power while optimistic ON hits the guarded pass (lines 155-156)."""
    light = _build_light({str(LUMIPLUS_COMPONENT_POWER): {"reportedValue": "garbage"}})
    light._optimistic_is_on = True
    light._handle_coordinator_update()
    # The except path is a no-op, so the optimistic flag is preserved.
    assert light._optimistic_is_on is True


def test_handle_coordinator_update_clears_brightness_once_backend_matches() -> None:
    """Optimistic brightness drops when the device echoes the matching 0-100 value (lines 161-165)."""
    # 128/255*100 ≈ 50.2 -> round() == 50; device reports 50 -> match -> clear.
    light = _build_light({str(LUMIPLUS_COMPONENT_BRIGHTNESS): {"reportedValue": 50}})
    light._optimistic_brightness = 128
    light._handle_coordinator_update()
    assert light._optimistic_brightness is None


def test_handle_coordinator_update_keeps_brightness_until_backend_matches() -> None:
    """Optimistic brightness stays put while the device still reports the old value."""
    light = _build_light({str(LUMIPLUS_COMPONENT_BRIGHTNESS): {"reportedValue": 10}})
    light._optimistic_brightness = 255  # expects ~100 on the wire
    light._handle_coordinator_update()
    assert light._optimistic_brightness == 255


def test_handle_coordinator_update_brightness_bad_reported_value_swallowed() -> None:
    """A non-numeric reported brightness hits the guarded except (lines 166-167) and is kept."""
    light = _build_light({str(LUMIPLUS_COMPONENT_BRIGHTNESS): {"reportedValue": "nope"}})
    light._optimistic_brightness = 200
    light._handle_coordinator_update()
    assert light._optimistic_brightness == 200


def test_handle_coordinator_update_clears_rgbw_once_backend_matches() -> None:
    """Optimistic RGBW drops on an exact tuple match from the backend (lines 171-179)."""
    light = _build_light(
        {str(LUMIPLUS_COMPONENT_COLOR): {"reportedValue": {"r": 10, "g": 20, "b": 30, "extra": {"w": 40}}}}
    )
    light._optimistic_rgbw = (10, 20, 30, 40)
    light._handle_coordinator_update()
    assert light._optimistic_rgbw is None


def test_handle_coordinator_update_keeps_rgbw_until_backend_matches() -> None:
    """Optimistic RGBW stays put while the backend still reports a different colour."""
    light = _build_light(
        {str(LUMIPLUS_COMPONENT_COLOR): {"reportedValue": {"r": 0, "g": 0, "b": 0, "extra": {"w": 0}}}}
    )
    light._optimistic_rgbw = (10, 20, 30, 40)
    light._handle_coordinator_update()
    assert light._optimistic_rgbw == (10, 20, 30, 40)


def test_handle_coordinator_update_rgbw_bad_channel_swallowed() -> None:
    """A non-numeric colour channel during reconciliation hits the guarded except (lines 180-181)."""
    light = _build_light(
        {str(LUMIPLUS_COMPONENT_COLOR): {"reportedValue": {"r": "bad", "g": 0, "b": 0, "extra": {"w": 0}}}}
    )
    light._optimistic_rgbw = (10, 20, 30, 40)
    light._handle_coordinator_update()
    # Parse failed -> kept (not cleared).
    assert light._optimistic_rgbw == (10, 20, 30, 40)


# --- async_turn_on / async_turn_off failure paths -------------------------


async def test_async_turn_on_color_path_and_optimistic_state() -> None:
    """turn_on with only RGBW_COLOR drives the colour write and exposes the optimistic colour."""
    light = _build_light()
    await light.async_turn_on(**{ATTR_RGBW_COLOR: (1, 2, 3, 4)})

    assert light.is_on is True
    assert light.rgbw_color == (1, 2, 3, 4)
    light._api.set_component_json_value.assert_awaited_once_with(
        DEVICE_ID,
        LUMIPLUS_COMPONENT_COLOR,
        {"r": 1, "g": 2, "b": 3, "k": 5000, "extra": {"w": 4}},
    )
    light.coordinator.async_request_refresh.assert_awaited_once()


async def test_async_turn_on_raises_when_power_write_fails() -> None:
    """A False from the power write rolls back every optimistic override and raises."""
    light = _build_light()
    light._api.set_component_string_value = AsyncMock(return_value=False)

    with pytest.raises(HomeAssistantError):
        await light.async_turn_on(**{ATTR_BRIGHTNESS: 128, ATTR_RGBW_COLOR: (1, 2, 3, 4)})

    assert light._optimistic_is_on is None
    assert light._optimistic_brightness is None
    assert light._optimistic_rgbw is None
    light.coordinator.async_request_refresh.assert_not_awaited()


async def test_async_turn_on_raises_and_rolls_back_on_api_exception() -> None:
    """A raised TimeoutError mid-write rolls back optimistics and surfaces HomeAssistantError."""
    light = _build_light()
    light._api.set_component_value = AsyncMock(side_effect=TimeoutError)

    with pytest.raises(HomeAssistantError):
        await light.async_turn_on(**{ATTR_BRIGHTNESS: 200})

    assert light._optimistic_is_on is None
    assert light._optimistic_brightness is None
    light.async_write_ha_state.assert_called()
    light.coordinator.async_request_refresh.assert_not_awaited()


async def test_async_turn_off_raises_when_power_write_fails() -> None:
    """A False from the power-off write rolls back the optimistic flag and raises."""
    light = _build_light()
    light._api.set_component_string_value = AsyncMock(return_value=False)

    with pytest.raises(HomeAssistantError):
        await light.async_turn_off()

    assert light._optimistic_is_on is None
    light.coordinator.async_request_refresh.assert_not_awaited()


async def test_async_turn_off_raises_and_rolls_back_on_api_exception() -> None:
    """A raised ClientError on turn_off rolls back and raises HomeAssistantError."""
    import aiohttp

    light = _build_light()
    light._api.set_component_string_value = AsyncMock(side_effect=aiohttp.ClientError)

    with pytest.raises(HomeAssistantError):
        await light.async_turn_off()

    assert light._optimistic_is_on is None
    light.coordinator.async_request_refresh.assert_not_awaited()


# --- async_setup_entry ---------------------------------------------------


async def test_setup_adds_new_device_dynamically() -> None:
    """dynamic-devices: a light appearing on a later poll is wired without a reload."""

    def _light_device(device_id: str) -> dict:
        return {"device_id": device_id, "name": "Light", "type": "light", "components": {}}

    pool = {"id": POOL_ID, "name": "Pool", "devices": [_light_device("LIGHT-1")]}
    coordinator = MagicMock()
    coordinator.data = {POOL_ID: pool}
    coordinator.last_update_success = True
    coordinator.api = SimpleNamespace(cached_pools=[pool])
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
    assert any("LIGHT-1" in u for u in uids_after_setup)
    assert not any("LIGHT-2" in u for u in uids_after_setup)
    assert listeners, "a coordinator update listener must be registered for dynamic devices"

    # A new light shows up on a later poll; firing the listener must wire it.
    pool["devices"].append(_light_device("LIGHT-2"))
    listeners[0]()

    new_uids = {e.unique_id for e in added} - uids_after_setup
    assert new_uids, "new device entities should be added without a reload"
    assert all("LIGHT-2" in u for u in new_uids), "only the newly-added device's entities are created"
