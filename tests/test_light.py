"""Tests for the LumiPlus Connect light platform."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from homeassistant.components.light import ATTR_BRIGHTNESS, ATTR_RGBW_COLOR
import pytest

from custom_components.fluidra_pool.const import (
    LUMIPLUS_COMPONENT_BRIGHTNESS,
    LUMIPLUS_COMPONENT_COLOR,
    LUMIPLUS_COMPONENT_POWER,
)
from custom_components.fluidra_pool.light import FluidraLight

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
