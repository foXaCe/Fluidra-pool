"""Tests for the button platform (Victoria VS Stop button, Issue #144)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
import pytest

from custom_components.fluidra_pool.button import FluidraPumpStopButton, async_setup_entry
from custom_components.fluidra_pool.const import DOMAIN

POOL_ID = "pool-1"
DEVICE_ID = "VIC-1"


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
    }
    device.update(extra)
    return device


def _button(device: dict, api: Any, **coord_kw: Any) -> FluidraPumpStopButton:
    return FluidraPumpStopButton(_coord(device, **coord_kw), api, POOL_ID, DEVICE_ID)


def test_unique_id_format() -> None:
    btn = _button(_device(is_running=True), SimpleNamespace())
    assert btn.unique_id == f"{DOMAIN}_{POOL_ID}_{DEVICE_ID}_stop"


def test_available_only_when_running() -> None:
    """The Stop button is disabled while the pump is already stopped."""
    assert _button(_device(is_running=True), SimpleNamespace()).available is True
    assert _button(_device(is_running=False), SimpleNamespace()).available is False


async def test_press_calls_pause_and_refreshes() -> None:
    """Pressing Stop pauses the pump (c15) and requests a refresh."""
    api = SimpleNamespace(pause_pump=AsyncMock(return_value=True))
    btn = _button(_device(is_running=True), api)
    await btn.async_press()
    api.pause_pump.assert_awaited_once_with(DEVICE_ID)
    btn.coordinator.async_request_refresh.assert_awaited_once()


async def test_press_raises_on_api_failure() -> None:
    """A failed pause surfaces a HomeAssistantError."""
    api = SimpleNamespace(pause_pump=AsyncMock(return_value=False))
    btn = _button(_device(is_running=True), api)
    with pytest.raises(HomeAssistantError):
        await btn.async_press()


async def test_press_wraps_network_error() -> None:
    """A network error from the API is wrapped in a HomeAssistantError."""
    import aiohttp

    api = SimpleNamespace(pause_pump=AsyncMock(side_effect=aiohttp.ClientError("boom")))
    btn = _button(_device(is_running=True), api)
    with pytest.raises(HomeAssistantError):
        await btn.async_press()


async def test_press_blocked_for_viewer_pool() -> None:
    """A viewer (read-only) pool blocks the press before any API call."""
    api = SimpleNamespace(pause_pump=AsyncMock(return_value=True))
    btn = _button(_device(is_running=True), api, access_level="viewer")
    with pytest.raises(ServiceValidationError):
        await btn.async_press()
    api.pause_pump.assert_not_awaited()


async def test_setup_creates_stop_button_for_victoria() -> None:
    """A device whose profile declares button_stop gets a Stop button."""
    device = {
        "device_id": DEVICE_ID,
        "name": "Victoria Smart Connect VS",
        "family": "Filtration Pumps",
        "model": "Victoria Smart Connect VS",
        "type": "pump",
        "online": True,
        "components": {},
    }
    coordinator = MagicMock()
    pool = {"id": POOL_ID, "name": "Pool", "devices": [device]}
    coordinator.data = {POOL_ID: pool}
    coordinator.last_update_success = True
    coordinator.api = SimpleNamespace(cached_pools=[pool], get_pools=AsyncMock(return_value=[pool]))
    added: list[Any] = []
    entry = SimpleNamespace(
        runtime_data=SimpleNamespace(coordinator=coordinator),
        async_on_unload=lambda _u: None,
    )
    await async_setup_entry(MagicMock(), entry, MagicMock(side_effect=lambda e, *a, **k: added.extend(list(e))))
    assert any(isinstance(e, FluidraPumpStopButton) for e in added)
