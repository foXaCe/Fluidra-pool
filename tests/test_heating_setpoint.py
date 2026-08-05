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


@pytest.mark.parametrize("name", ["Z250iQ", "Zodiac Z250iQ", "Z260iQ", "Heat pump"])
def test_lf_heat_pumps_route_to_a_profile_exposing_the_wifi_signal(name: str) -> None:
    """Issue #183: c2 already decodes to the RSSI; the entity was the missing piece.

    Asserted through device identification rather than against a single profile
    by name: LF* units split across the Z250iQ and Z260iQ profiles depending on
    their reported name, so checking one profile's entity list passes while real
    hardware still gets nothing. That is exactly how the first fix shipped broken.
    """
    device = {
        "device_id": "LF25012345",
        "name": name,
        "family": "heat pump",
        "type": "heat_pump",
        "model": "",
        "components": {},
    }
    config = DeviceIdentifier.identify_device(device)
    assert config is not None
    assert "sensor_wifi_signal" in config.entities
    # The RSSI only reaches the entity if c2 is actually polled and not skipped.
    assert config.components_range > 2
    assert config.features.get("skip_signal") is not True


def test_both_lf_profiles_expose_the_wifi_signal() -> None:
    """Neither Z250iQ nor Z260iQ may regress, whichever one identification picks."""
    for profile in ("z250iq_heat_pump", "z260iq_heat_pump"):
        assert "sensor_wifi_signal" in DEVICE_CONFIGS[profile].entities, profile
