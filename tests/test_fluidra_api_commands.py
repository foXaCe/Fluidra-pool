"""Tests for fluidra_api/_commands.py (start/stop pump, auto mode, setpoint)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.fluidra_pool.const import (
    COMPONENT_AUTO_MODE,
    COMPONENT_HEAT_PUMP_ONOFF,
    COMPONENT_HEAT_PUMP_SETPOINT,
    COMPONENT_PUMP_ONOFF,
    COMPONENT_PUMP_SPEED,
)
from custom_components.fluidra_pool.fluidra_api._commands import CommandsMixin


# A bare instance of the mixin attached to the relevant attrs/methods we need.
class _FakeAPI(CommandsMixin):
    """Minimal stub: the mixin only touches control_device_component + get_device_by_id."""

    def __init__(self, *, is_heat_pump_device: dict | None = None) -> None:
        self.control_device_component = AsyncMock(return_value=True)
        self._device = is_heat_pump_device

    def get_device_by_id(self, _device_id: str) -> dict | None:
        """Return the canned device dict."""
        return self._device


@pytest.fixture(autouse=True)
def _skip_pump_delay() -> Any:
    """Skip the asyncio.sleep call in start_pump."""
    with patch("custom_components.fluidra_pool.fluidra_api._commands.asyncio.sleep", new=AsyncMock()):
        yield


# --- _is_heat_pump -------------------------------------------------------


def test_is_heat_pump_returns_false_when_device_unknown() -> None:
    """No matching device → not a heat pump."""
    api = _FakeAPI()
    assert api._is_heat_pump("X") is False


def test_is_heat_pump_returns_true_for_heat_pump_device() -> None:
    """A device classified as heat_pump returns True."""
    api = _FakeAPI(is_heat_pump_device={"device_id": "LG-001", "family": "Eco Elyo"})
    with patch(
        "custom_components.fluidra_pool.fluidra_api._commands.DeviceIdentifier.identify_device",
        return_value=SimpleNamespace(device_type="heat_pump"),
    ):
        assert api._is_heat_pump("LG-001") is True


def test_is_heat_pump_returns_false_for_pump_device() -> None:
    """A pump device is not a heat pump."""
    api = _FakeAPI(is_heat_pump_device={"device_id": "PUMP-1", "family": "pump"})
    with patch(
        "custom_components.fluidra_pool.fluidra_api._commands.DeviceIdentifier.identify_device",
        return_value=SimpleNamespace(device_type="pump", features={}),
    ):
        assert api._is_heat_pump("PUMP-1") is False


# --- start_pump / stop_pump ---------------------------------------------


async def test_start_pump_for_regular_pump_calls_pump_onoff_then_speed() -> None:
    """Regular pump: turn ON via COMPONENT_PUMP_ONOFF then nudge speed=0."""
    api = _FakeAPI(is_heat_pump_device={"device_id": "P1"})
    with patch(
        "custom_components.fluidra_pool.fluidra_api._commands.DeviceIdentifier.identify_device",
        return_value=SimpleNamespace(device_type="pump", features={}),
    ):
        success = await api.start_pump("P1")

    assert success is True
    assert api.control_device_component.await_count == 2
    api.control_device_component.assert_any_await("P1", COMPONENT_PUMP_ONOFF, 1)
    api.control_device_component.assert_any_await("P1", COMPONENT_PUMP_SPEED, 0)


async def test_start_pump_for_heat_pump_calls_heat_pump_onoff_only() -> None:
    """Heat pump: a single write to COMPONENT_HEAT_PUMP_ONOFF."""
    api = _FakeAPI(is_heat_pump_device={"device_id": "HP-1"})
    with patch(
        "custom_components.fluidra_pool.fluidra_api._commands.DeviceIdentifier.identify_device",
        return_value=SimpleNamespace(device_type="heat_pump"),
    ):
        success = await api.start_pump("HP-1")

    assert success is True
    api.control_device_component.assert_awaited_once_with("HP-1", COMPONENT_HEAT_PUMP_ONOFF, 1)


async def test_start_pump_returns_false_when_initial_write_fails() -> None:
    """If the ON write fails, no follow-up speed write is attempted."""
    api = _FakeAPI(is_heat_pump_device={"device_id": "P1"})
    api.control_device_component = AsyncMock(return_value=False)
    with patch(
        "custom_components.fluidra_pool.fluidra_api._commands.DeviceIdentifier.identify_device",
        return_value=SimpleNamespace(device_type="pump", features={}),
    ):
        success = await api.start_pump("P1")

    assert success is False
    api.control_device_component.assert_awaited_once()


async def test_stop_pump_routes_by_device_type() -> None:
    """stop_pump uses COMPONENT_HEAT_PUMP_ONOFF or COMPONENT_PUMP_ONOFF based on device."""
    api = _FakeAPI(is_heat_pump_device={"device_id": "HP-1"})
    with patch(
        "custom_components.fluidra_pool.fluidra_api._commands.DeviceIdentifier.identify_device",
        return_value=SimpleNamespace(device_type="heat_pump"),
    ):
        await api.stop_pump("HP-1")
    api.control_device_component.assert_awaited_once_with("HP-1", COMPONENT_HEAT_PUMP_ONOFF, 0)


async def test_stop_pump_regular_pump_uses_pump_onoff() -> None:
    """A regular (non-Victoria) pump stops via COMPONENT_PUMP_ONOFF (c9=0)."""
    api = _FakeAPI(is_heat_pump_device={"device_id": "P1"})
    with patch(
        "custom_components.fluidra_pool.fluidra_api._commands.DeviceIdentifier.identify_device",
        return_value=SimpleNamespace(device_type="pump", features={}),
    ):
        await api.stop_pump("P1")
    api.control_device_component.assert_awaited_once_with("P1", COMPONENT_PUMP_ONOFF, 0)


# --- enable/disable auto mode --------------------------------------------


async def test_enable_auto_mode_powers_pump_on_then_writes_component_ten() -> None:
    """Auto-mode ON powers the pump on (comp 9) before writing comp 10.

    A standby pump silently ignores the auto-mode write, so the command must be
    preceded by a power-on.
    """
    api = _FakeAPI()
    with patch("custom_components.fluidra_pool.fluidra_api._commands.asyncio.sleep", new=AsyncMock()):
        result = await api.enable_auto_mode("P1")
    assert result is True
    assert api.control_device_component.await_args_list[0].args == ("P1", COMPONENT_PUMP_ONOFF, 1)
    assert api.control_device_component.await_args_list[-1].args == ("P1", COMPONENT_AUTO_MODE, 1)


async def test_enable_auto_mode_aborts_if_power_on_fails() -> None:
    """If powering the pump on fails, auto mode is not written."""
    api = _FakeAPI()
    api.control_device_component = AsyncMock(return_value=False)
    with patch("custom_components.fluidra_pool.fluidra_api._commands.asyncio.sleep", new=AsyncMock()):
        result = await api.enable_auto_mode("P1")
    assert result is False
    api.control_device_component.assert_awaited_once_with("P1", COMPONENT_PUMP_ONOFF, 1)


async def test_disable_auto_mode_writes_component_with_value_zero() -> None:
    """Auto-mode OFF is component COMPONENT_AUTO_MODE = 0."""
    api = _FakeAPI()
    await api.disable_auto_mode("P1")
    api.control_device_component.assert_awaited_once_with("P1", COMPONENT_AUTO_MODE, 0)


# --- set_heat_pump_temperature ------------------------------------------


async def test_set_heat_pump_temperature_writes_decidegrees_and_caches_target() -> None:
    """Target 28.5°C is sent as integer 285 (×10) and cached on the device."""
    device = {"device_id": "HP-1"}
    api = _FakeAPI(is_heat_pump_device=device)

    success = await api.set_heat_pump_temperature("HP-1", 28.5)

    assert success is True
    api.control_device_component.assert_awaited_once_with("HP-1", COMPONENT_HEAT_PUMP_SETPOINT, 285)
    assert device["target_temperature"] == 28.5


async def test_set_heat_pump_temperature_skips_cache_when_write_fails() -> None:
    """If the API write fails, we don't pretend the setpoint changed."""
    device = {"device_id": "HP-1"}
    api = _FakeAPI(is_heat_pump_device=device)
    api.control_device_component = AsyncMock(return_value=False)

    success = await api.set_heat_pump_temperature("HP-1", 28.5)

    assert success is False
    assert "target_temperature" not in device


# --- Victoria Smart Connect VS write path (Issue #144) ------------------

from contextlib import contextmanager  # noqa: E402

from custom_components.fluidra_pool.const import (  # noqa: E402
    COMPONENT_VICTORIA_AUTO_SCHEDULE,
    COMPONENT_VICTORIA_QUICK_FUNCTION,
    COMPONENT_VICTORIA_STOP,
)


@contextmanager
def _as_victoria() -> Any:
    """Make DeviceIdentifier see the device as a Victoria VS pump."""
    with (
        patch(
            "custom_components.fluidra_pool.fluidra_api._commands.DeviceIdentifier.get_feature",
            side_effect=lambda _d, feat, *a: feat == "victoria_vs_mode",
        ),
        patch(
            "custom_components.fluidra_pool.fluidra_api._commands.DeviceIdentifier.identify_device",
            return_value=SimpleNamespace(device_type="pump", features={}),
        ),
    ):
        yield


async def test_victoria_start_pump_enables_auto_schedule() -> None:
    """Victoria has no direct run write — start (re)enables the auto schedule (c13=1)."""
    api = _FakeAPI(is_heat_pump_device={"device_id": "VIC-1"})
    with _as_victoria():
        assert await api.start_pump("VIC-1") is True
    api.control_device_component.assert_awaited_once_with("VIC-1", COMPONENT_VICTORIA_AUTO_SCHEDULE, 1)


async def test_victoria_stop_pump_disables_schedule_then_fires_stop() -> None:
    """Victoria full stop: c13=0 (so the stop halts the motor) then c15=1."""
    api = _FakeAPI(is_heat_pump_device={"device_id": "VIC-1"})
    with _as_victoria():
        assert await api.stop_pump("VIC-1") is True
    calls = [c.args for c in api.control_device_component.await_args_list]
    assert calls == [("VIC-1", COMPONENT_VICTORIA_AUTO_SCHEDULE, 0), ("VIC-1", COMPONENT_VICTORIA_STOP, 1)]


async def test_victoria_enable_auto_mode_writes_c13_once() -> None:
    """Victoria auto mode is a single boolean on c13 — no separate power-on step."""
    api = _FakeAPI(is_heat_pump_device={"device_id": "VIC-1"})
    with _as_victoria():
        assert await api.enable_auto_mode("VIC-1") is True
    api.control_device_component.assert_awaited_once_with("VIC-1", COMPONENT_VICTORIA_AUTO_SCHEDULE, 1)


async def test_victoria_disable_auto_mode_writes_c13_zero() -> None:
    api = _FakeAPI(is_heat_pump_device={"device_id": "VIC-1"})
    with _as_victoria():
        assert await api.disable_auto_mode("VIC-1") is True
    api.control_device_component.assert_awaited_once_with("VIC-1", COMPONENT_VICTORIA_AUTO_SCHEDULE, 0)


async def test_victoria_trigger_quick_function_writes_c20_index() -> None:
    """Quick function is triggered by writing the preset index to c20."""
    api = _FakeAPI(is_heat_pump_device={"device_id": "VIC-1"})
    assert await api.trigger_quick_function("VIC-1", 2) is True
    api.control_device_component.assert_awaited_once_with("VIC-1", COMPONENT_VICTORIA_QUICK_FUNCTION, 2)


async def test_non_victoria_pump_keeps_c9_c10_write_path() -> None:
    """A non-Victoria pump still uses the E30iQ c9/c10 write path (regression guard)."""
    api = _FakeAPI(is_heat_pump_device={"device_id": "P1"})
    with patch(
        "custom_components.fluidra_pool.fluidra_api._commands.DeviceIdentifier.get_feature",
        return_value=False,
    ):
        await api.disable_auto_mode("P1")
    api.control_device_component.assert_awaited_once_with("P1", COMPONENT_AUTO_MODE, 0)
