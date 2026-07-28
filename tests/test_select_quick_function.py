"""Tests for the Victoria quick-function select (Issue #144).

Options come from the pump's own preset slots (c126-c134, consolidated JSON
objects), and selecting one writes its slot index to c20.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
import pytest

from custom_components.fluidra_pool.const import DOMAIN
from custom_components.fluidra_pool.select import FluidraVictoriaQuickFunctionSelect

POOL_ID = "pool-1"
DEVICE_ID = "VIC-1"

PRESETS = {
    0: {"name": "CLEAN", "mode": "SPEED", "setpoint": 100, "duration": "PT60M"},
    1: {"name": "HIGH SPEED", "mode": "SPEED", "setpoint": 100, "duration": "P0"},
    2: {"name": "MID SPEED", "mode": "SPEED", "setpoint": 75, "duration": "P0"},
    4: {"name": "5 m3/h", "mode": "FLOW", "setpoint": 5, "duration": "P0"},
}


def _coord(device: dict, *, access_level: str | None = None) -> Any:
    coordinator = MagicMock()
    pool: dict[str, Any] = {"id": POOL_ID, "name": "Pool", "devices": [device]}
    if access_level:
        pool["access_level"] = access_level
    coordinator.data = {POOL_ID: pool}
    coordinator.last_update_success = True
    coordinator.async_request_refresh = AsyncMock()
    return coordinator


def _device(**extra: Any) -> dict:
    device = {
        "device_id": DEVICE_ID,
        "name": "Victoria Smart Connect VS",
        "type": "pump",
        "online": True,
        "components": {},
        "pump_presets": dict(PRESETS),
    }
    device.update(extra)
    return device


def _select(device: dict, api: Any = None, **coord_kw: Any) -> FluidraVictoriaQuickFunctionSelect:
    return FluidraVictoriaQuickFunctionSelect(_coord(device, **coord_kw), api or SimpleNamespace(), POOL_ID, DEVICE_ID)


@pytest.fixture(autouse=True)
def _no_delay() -> Any:
    with patch("custom_components.fluidra_pool.select.pump.asyncio.sleep", new=AsyncMock()):
        yield


def test_options_built_from_preset_slots_in_order() -> None:
    """Labels carry the name and target; empty slots are skipped."""
    assert _select(_device()).options == [
        "CLEAN (100 %)",
        "HIGH SPEED (100 %)",
        "MID SPEED (75 %)",
        "5 m3/h (5 m³/h)",
    ]


def test_options_empty_without_presets() -> None:
    assert _select(_device(pump_presets={})).options == []
    assert _select(_device(pump_presets=None)).options == []


def test_label_falls_back_to_name_when_target_missing() -> None:
    """A slot without a usable mode/setpoint still shows its name."""
    device = _device(pump_presets={0: {"name": "ONLY NAME"}})
    assert _select(device).options == ["ONLY NAME"]


def test_current_option_reflects_running_quick_function() -> None:
    device = _device(
        pump_mode="QUICK FUNCTION",
        pump_quick_function={"name": "MID SPEED", "mode": "SPEED", "setpoint": 75},
    )
    assert _select(device).current_option == "MID SPEED (75 %)"


def test_current_option_none_during_schedule() -> None:
    """c135 goes stale under AUTO, so no option is reported as current (Issue #144)."""
    device = _device(
        pump_mode="AUTO",
        pump_quick_function={"name": "MID SPEED", "mode": "SPEED", "setpoint": 75},
    )
    assert _select(device).current_option is None


def test_current_option_none_when_active_preset_unknown() -> None:
    """An active preset that isn't in the slot list isn't reported as an option."""
    device = _device(
        pump_mode="QUICK FUNCTION",
        pump_quick_function={"name": "GONE", "mode": "SPEED", "setpoint": 10},
    )
    assert _select(device).current_option is None


async def test_select_writes_slot_index_to_c20() -> None:
    """Choosing an option triggers that preset by its slot index."""
    api = SimpleNamespace(trigger_quick_function=AsyncMock(return_value=True))
    select = _select(_device(), api)
    await select.async_select_option("5 m3/h (5 m³/h)")
    api.trigger_quick_function.assert_awaited_once_with(DEVICE_ID, 4)  # slot 4, not position 3
    select.coordinator.async_request_refresh.assert_awaited_once()


async def test_select_unknown_option_raises_validation_error() -> None:
    api = SimpleNamespace(trigger_quick_function=AsyncMock(return_value=True))
    select = _select(_device(), api)
    with pytest.raises(ServiceValidationError):
        await select.async_select_option("NOPE")
    api.trigger_quick_function.assert_not_awaited()


async def test_select_raises_when_write_fails() -> None:
    api = SimpleNamespace(trigger_quick_function=AsyncMock(return_value=False))
    with pytest.raises(HomeAssistantError):
        await _select(_device(), api).async_select_option("HIGH SPEED (100 %)")


async def test_select_wraps_network_error() -> None:
    import aiohttp

    api = SimpleNamespace(trigger_quick_function=AsyncMock(side_effect=aiohttp.ClientError("boom")))
    with pytest.raises(HomeAssistantError):
        await _select(_device(), api).async_select_option("HIGH SPEED (100 %)")


async def test_select_blocked_on_viewer_pool() -> None:
    """Read-only pools refuse before any write (Issue #133)."""
    api = SimpleNamespace(trigger_quick_function=AsyncMock(return_value=True))
    select = _select(_device(), api, access_level="viewer")
    with pytest.raises(ServiceValidationError):
        await select.async_select_option("HIGH SPEED (100 %)")
    api.trigger_quick_function.assert_not_awaited()


def test_unique_id_format() -> None:
    assert _select(_device()).unique_id == f"{DOMAIN}_{POOL_ID}_{DEVICE_ID}_quick_function"
