"""Tests for the eXO iQ heating setpoint (Issue #175) and the Z250iQ RSSI (Issue #183)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.fluidra_pool.device_registry import DEVICE_CONFIGS, DeviceIdentifier
from custom_components.fluidra_pool.number import FluidraHeatingSetpoint

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


def _number(device: dict, api: Any) -> FluidraHeatingSetpoint:
    entity = FluidraHeatingSetpoint(_coord(device), api, POOL_ID, DEVICE_ID)
    entity.hass = MagicMock()
    entity.async_write_ha_state = MagicMock()
    return entity


def test_reads_the_setpoint_in_whole_degrees() -> None:
    """c43 is direct °C — 27 means 27 °C, not 2.7."""
    assert _number(_device(**{"43": 27, "88": True}), MagicMock()).native_value == 27.0


def test_unavailable_until_heating_is_configured() -> None:
    """c88 flips to True once an aux output is assigned to heating."""
    assert _number(_device(**{"43": 27, "88": False}), MagicMock()).available is False
    assert _number(_device(**{"43": 27, "88": True}), MagicMock()).available is True


async def test_setting_writes_the_integer_to_c43() -> None:
    api = MagicMock()
    api.control_device_component = AsyncMock(return_value=True)
    entity = _number(_device(**{"43": 27, "88": True}), api)

    await entity.async_set_native_value(23)

    api.control_device_component.assert_awaited_once_with(DEVICE_ID, 43, 23)


async def test_refused_write_raises() -> None:
    api = MagicMock()
    api.control_device_component = AsyncMock(return_value=False)
    entity = _number(_device(**{"43": 27, "88": True}), api)

    with pytest.raises(HomeAssistantError):
        await entity.async_set_native_value(23)


def test_missing_or_junk_setpoint_reports_none() -> None:
    assert _number(_device(**{"88": True}), MagicMock()).native_value is None
    assert _number(_device(**{"43": "n/a", "88": True}), MagicMock()).native_value is None


def test_exo_profile_declares_the_heating_registers() -> None:
    device = _device(**{"43": 27, "88": True})
    assert DeviceIdentifier.get_feature(device, "heating_setpoint") == 43
    assert DeviceIdentifier.get_feature(device, "heating_configured") == 88
    scanned = DeviceIdentifier.get_feature(device, "specific_components", [])
    assert 43 in scanned
    assert 88 in scanned


def test_z260iq_exposes_the_wifi_signal_sensor() -> None:
    """Issue #183: c2 already decodes to the RSSI; the profile only lacked the entity."""
    assert "sensor_wifi_signal" in DEVICE_CONFIGS["z260iq_heat_pump"].entities
