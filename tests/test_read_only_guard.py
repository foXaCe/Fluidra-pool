"""Tests for the viewer (read-only) pool write guard — Issue #133.

A viewer account's control writes are accepted by the Fluidra cloud with a
fake HTTP 200 that never persists, so every control entry point must fail
fast with ServiceValidationError instead — before any optimistic state or
API call. Owner/shared/unknown access levels pass through untouched.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from homeassistant.exceptions import ServiceValidationError
import pytest

from custom_components.fluidra_pool.__init__ import _ensure_device_pool_writable
from custom_components.fluidra_pool.climate import FluidraHeatPumpClimate
from custom_components.fluidra_pool.entity import FluidraPoolControlEntity
from custom_components.fluidra_pool.light import FluidraLight
from custom_components.fluidra_pool.number import FluidraChlorinatorLevelNumber
from custom_components.fluidra_pool.select.pump import FluidraPumpSpeedSelect
from custom_components.fluidra_pool.switch import FluidraPumpSwitch

POOL_ID = "pool-1"
DEVICE_ID = "LE24500883"


def _coordinator(access_level: str | None, devices: list[dict] | None = None) -> Any:
    coordinator = MagicMock()
    pool: dict[str, Any] = {
        "id": POOL_ID,
        "name": "Casa",
        "devices": devices if devices is not None else [_device()],
    }
    if access_level is not None:
        pool["access_level"] = access_level
    coordinator.data = {POOL_ID: pool}
    coordinator.async_request_refresh = AsyncMock()
    coordinator.last_update_success = True
    return coordinator


def _device(**extra: Any) -> dict:
    device = {
        "device_id": DEVICE_ID,
        "name": "Device",
        "family": "",
        "type": "pump",
        "model": "",
        "online": True,
        "components": {},
    }
    device.update(extra)
    return device


def _api() -> SimpleNamespace:
    return SimpleNamespace(
        start_pump=AsyncMock(return_value=True),
        stop_pump=AsyncMock(return_value=True),
        control_device_component=AsyncMock(return_value=True),
        set_component_value=AsyncMock(return_value=True),
        set_heat_pump_temperature=AsyncMock(return_value=True),
    )


# --- base guard -----------------------------------------------------------


@pytest.mark.parametrize("access_level", ["owner", "shared", "unknown", None])
def test_guard_passes_for_non_viewer_levels(access_level: str | None) -> None:
    """Only the confirmed-read-only level blocks; everything else passes."""
    entity = FluidraPoolControlEntity(_coordinator(access_level), SimpleNamespace(), POOL_ID, DEVICE_ID)
    entity._ensure_pool_writable()  # must not raise


def test_guard_raises_for_viewer_pool() -> None:
    """A viewer pool raises ServiceValidationError with the pool name."""
    entity = FluidraPoolControlEntity(_coordinator("viewer"), SimpleNamespace(), POOL_ID, DEVICE_ID)
    with pytest.raises(ServiceValidationError) as err:
        entity._ensure_pool_writable()
    assert err.value.translation_key == "pool_read_only"
    assert err.value.translation_placeholders == {"pool_name": "Casa"}


# --- one representative entry point per platform ---------------------------


@pytest.mark.parametrize(
    ("entity_cls", "method", "args"),
    [
        (FluidraPumpSwitch, "async_turn_on", ()),
        (FluidraPumpSpeedSelect, "async_select_option", ("high",)),
        (FluidraChlorinatorLevelNumber, "async_set_native_value", (50.0,)),
        (FluidraHeatPumpClimate, "async_set_temperature", ()),
        (FluidraLight, "async_turn_on", ()),
    ],
)
async def test_control_methods_blocked_on_viewer_pool(entity_cls: type, method: str, args: tuple) -> None:
    """Each platform's control entry point fails fast without touching the API."""
    api = _api()
    entity = entity_cls(_coordinator("viewer"), api, POOL_ID, DEVICE_ID)
    entity.hass = MagicMock()
    entity.async_write_ha_state = MagicMock()

    kwargs = {"temperature": 26.0} if method == "async_set_temperature" else {}
    with pytest.raises(ServiceValidationError):
        await getattr(entity, method)(*args, **kwargs)

    for mock in vars(api).values():
        mock.assert_not_awaited()


async def test_control_method_proceeds_on_owner_pool() -> None:
    """An owner pool is not blocked: the API call goes through."""
    api = _api()
    switch = FluidraPumpSwitch(_coordinator("owner"), api, POOL_ID, DEVICE_ID)
    switch.hass = MagicMock()
    switch.async_write_ha_state = MagicMock()

    await switch.async_turn_on()
    api.start_pump.assert_awaited_once_with(DEVICE_ID)


# --- domain services guard --------------------------------------------------


def test_service_guard_raises_for_viewer_pool() -> None:
    coordinator = _coordinator("viewer")
    with pytest.raises(ServiceValidationError) as err:
        _ensure_device_pool_writable(coordinator, DEVICE_ID)
    assert err.value.translation_key == "pool_read_only"


def test_service_guard_passes_for_owner_and_unknown_device() -> None:
    _ensure_device_pool_writable(_coordinator("owner"), DEVICE_ID)  # must not raise
    _ensure_device_pool_writable(_coordinator("viewer"), "OTHER-DEVICE")  # not in pool → no-op
    coordinator = MagicMock()
    coordinator.data = None
    _ensure_device_pool_writable(coordinator, DEVICE_ID)  # no data yet → no-op
