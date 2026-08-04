"""Tests for the eXO iQ auxiliary output selects (Issue #175).

@Inervo's captures show c30 going 0 -> 1 when Aux 1's light was switched on,
and c31 sitting at 2 with Aux 2 on Auto, dropping to 0 when switched off.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.fluidra_pool.select.aux import FluidraAuxOutputSelect

POOL_ID = "pool-1"
DEVICE_ID = "NS25007212"


def _coord(device: dict) -> Any:
    coordinator = MagicMock()
    coordinator.data = {POOL_ID: {"id": POOL_ID, "name": "Pool", "devices": [device]}}
    coordinator.last_update_success = True
    coordinator.async_request_refresh = AsyncMock()
    return coordinator


def _device(**components: Any) -> dict:
    return {
        "device_id": DEVICE_ID,
        "name": "Zodiac EXO iQ 35",
        "family": "Chlorinators",
        "type": "connected",
        "online": True,
        "components": {str(k): {"reportedValue": v} for k, v in components.items()},
    }


def _select(device: dict, api: Any, aux_number: str = "1", component: int = 30) -> FluidraAuxOutputSelect:
    entity = FluidraAuxOutputSelect(_coord(device), api, POOL_ID, DEVICE_ID, aux_number, component)
    entity.hass = MagicMock()
    entity.async_write_ha_state = MagicMock()
    return entity


@pytest.mark.parametrize(("value", "expected"), [(0, "off"), (1, "on"), (2, "auto")])
def test_reads_each_state(value: int, expected: str) -> None:
    assert _select(_device(**{"30": value}), MagicMock()).current_option == expected


def test_aux2_reads_its_own_register() -> None:
    """Aux 2 must read c31, not Aux 1's c30."""
    device = _device(**{"30": 0, "31": 2})
    assert _select(device, MagicMock(), "2", 31).current_option == "auto"
    assert _select(device, MagicMock(), "1", 30).current_option == "off"


def test_unique_ids_do_not_collide() -> None:
    device = _device(**{"30": 0, "31": 0})
    assert _select(device, MagicMock(), "1", 30).unique_id != _select(device, MagicMock(), "2", 31).unique_id


def test_unknown_or_missing_values_report_none() -> None:
    """An unmapped value is reported as unknown rather than silently as 'off'."""
    assert _select(_device(**{"30": 7}), MagicMock()).current_option is None
    assert _select(_device(), MagicMock()).current_option is None
    assert _select(_device(**{"30": "junk"}), MagicMock()).current_option is None


@pytest.mark.parametrize(("option", "value"), [("off", 0), ("on", 1), ("auto", 2)])
async def test_selecting_writes_the_mapped_value(option: str, value: int) -> None:
    api = MagicMock()
    api.control_device_component = AsyncMock(return_value=True)
    entity = _select(_device(**{"30": 0}), api)

    with patch("custom_components.fluidra_pool.select.aux.asyncio.sleep", AsyncMock()):
        await entity.async_select_option(option)

    api.control_device_component.assert_awaited_once_with(DEVICE_ID, 30, value)


async def test_refused_write_clears_the_optimistic_state() -> None:
    """A refused write must not leave the UI showing a state the device never took."""
    api = MagicMock()
    api.control_device_component = AsyncMock(return_value=False)
    entity = _select(_device(**{"30": 0}), api)

    with (
        patch("custom_components.fluidra_pool.select.aux.asyncio.sleep", AsyncMock()),
        pytest.raises(HomeAssistantError),
    ):
        await entity.async_select_option("on")

    assert entity.current_option == "off"


async def test_unknown_option_is_ignored() -> None:
    api = MagicMock()
    api.control_device_component = AsyncMock(return_value=True)
    entity = _select(_device(**{"30": 0}), api)

    await entity.async_select_option("turbo")

    api.control_device_component.assert_not_awaited()


def test_assigned_function_is_exposed_as_an_attribute() -> None:
    """c90/c91 name what the output drives; surface it without needing the app."""
    device = _device(**{"30": 0, "31": 2, "90": "Lighting", "91": "Other"})
    assert _select(device, MagicMock(), "1", 30).extra_state_attributes["assigned_function"] == "Lighting"
    assert _select(device, MagicMock(), "2", 31).extra_state_attributes["assigned_function"] == "Other"


def test_assigned_function_omitted_when_unavailable() -> None:
    """No label register, no attribute — rather than an empty or misleading one."""
    assert "assigned_function" not in _select(_device(**{"30": 0}), MagicMock()).extra_state_attributes
