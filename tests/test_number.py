"""Tests for the number platform entities (chlorination, pH/ORP setpoints, effect speed)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.fluidra_pool.number import (
    FluidraChlorinatorLevelNumber,
    FluidraChlorinatorOrpSetpoint,
    FluidraChlorinatorPhSetpoint,
    FluidraLightEffectSpeed,
)

POOL_ID = "pool-1"
DEVICE_ID = "TEST-CHLOR-001"  # No match in DEVICE_CONFIGS → get_feature returns defaults


def _coord_with(device: dict) -> Any:
    coordinator = MagicMock()
    coordinator.data = {POOL_ID: {"id": POOL_ID, "name": "Pool", "devices": [device]}}
    coordinator.async_request_refresh = AsyncMock()
    coordinator.last_update_success = True
    return coordinator


def _api(success: bool = True) -> SimpleNamespace:
    return SimpleNamespace(control_device_component=AsyncMock(return_value=success))


def _attach_ha(entity) -> None:
    entity.hass = MagicMock()
    entity.async_write_ha_state = MagicMock()


def _chlorinator_device(components: dict | None = None, features: dict | None = None) -> dict:
    """Build a chlorinator-shaped device dict pinned to a fixed feature set.

    The `_identify_cache` key has to match the live cache_key computation
    (device_id, family, model, type, comp7_value) so identify_device short-circuits
    to our pinned config rather than scanning the global registry.
    """
    comp7 = ""
    components = components or {}
    if "7" in components and isinstance(components["7"], dict):
        comp7 = str(components["7"].get("reportedValue", ""))
    return {
        "device_id": DEVICE_ID,
        "name": "Chlorinator",
        "family": "",
        "type": "",
        "model": "",
        "online": True,
        "components": components,
        "_identify_cache": {
            "key": (DEVICE_ID, "", "", "", comp7),
            "config": SimpleNamespace(
                device_type="chlorinator",
                features=features or {},
                components_range=25,
                required_components=[0, 1, 2, 3],
                entities=[],
            ),
        },
    }


# --- FluidraChlorinatorLevelNumber ---------------------------------------


def test_chlorination_native_value_prefers_desired_over_reported() -> None:
    """desiredValue is preferred so user changes appear immediately."""
    device = _chlorinator_device(
        components={"10": {"reportedValue": 30, "desiredValue": 70}},
        features={"chlorination_level": 10},
    )
    number = FluidraChlorinatorLevelNumber(_coord_with(device), _api(), POOL_ID, DEVICE_ID)
    _attach_ha(number)
    assert number.native_value == 70.0


def test_chlorination_native_value_falls_back_to_reported() -> None:
    """Without a desiredValue, reportedValue drives the displayed level."""
    device = _chlorinator_device(
        components={"10": {"reportedValue": 80}},
        features={"chlorination_level": 10},
    )
    number = FluidraChlorinatorLevelNumber(_coord_with(device), _api(), POOL_ID, DEVICE_ID)
    _attach_ha(number)
    assert number.native_value == 80.0


async def test_chlorination_async_set_rounds_to_step_and_writes_component() -> None:
    """The slider value is rounded to the device's step before being sent."""
    device = _chlorinator_device(
        components={"10": {"reportedValue": 30}},
        features={"chlorination_level": 10, "chlorination_step": 10},
    )
    api = _api()
    number = FluidraChlorinatorLevelNumber(_coord_with(device), api, POOL_ID, DEVICE_ID)
    _attach_ha(number)

    await number.async_set_native_value(72.0)

    # 72 → round(72/10)*10 = 70
    api.control_device_component.assert_awaited_once_with(DEVICE_ID, 10, 70)
    number.coordinator.async_request_refresh.assert_awaited_once()


def test_chlorination_native_value_supports_dict_component() -> None:
    """A dict-form chlorination_level reads from its `read` component (Issue #3/#4)."""
    device = _chlorinator_device(
        components={"164": {"reportedValue": 60}},
        features={"chlorination_level": {"write": 4, "read": 164}},
    )
    number = FluidraChlorinatorLevelNumber(_coord_with(device), _api(), POOL_ID, DEVICE_ID)
    _attach_ha(number)
    assert number.native_value == 60.0


async def test_chlorination_async_set_dict_component_writes_to_write_component() -> None:
    """A dict-form chlorination_level writes to its `write` component, never crashing."""
    device = _chlorinator_device(
        components={"164": {"reportedValue": 30}},
        features={"chlorination_level": {"write": 4, "read": 164}, "chlorination_step": 10},
    )
    api = _api()
    number = FluidraChlorinatorLevelNumber(_coord_with(device), api, POOL_ID, DEVICE_ID)
    _attach_ha(number)

    await number.async_set_native_value(72.0)

    api.control_device_component.assert_awaited_once_with(DEVICE_ID, 4, 70)


# --- FluidraChlorinatorPhSetpoint ----------------------------------------


def test_ph_setpoint_native_value_divides_by_100() -> None:
    """pH setpoint raw 720 becomes 7.20."""
    device = _chlorinator_device(
        components={"172": {"reportedValue": 720}},
        features={"ph_setpoint": {"write": 8, "read": 172}},
    )
    number = FluidraChlorinatorPhSetpoint(_coord_with(device), _api(), POOL_ID, DEVICE_ID)
    _attach_ha(number)
    assert number.native_value == 7.2


def test_ph_setpoint_default_when_no_reading() -> None:
    """Missing reading falls back to 7.2 rather than returning None."""
    device = _chlorinator_device(
        components={},
        features={"ph_setpoint": {"write": 8, "read": 172}},
    )
    number = FluidraChlorinatorPhSetpoint(_coord_with(device), _api(), POOL_ID, DEVICE_ID)
    _attach_ha(number)
    assert number.native_value == 7.2


async def test_ph_setpoint_async_set_multiplies_by_divisor_and_writes() -> None:
    """Setting pH=7.4 with divisor 100 results in PUT component 8 value=740."""
    device = _chlorinator_device(
        components={},
        features={"ph_setpoint": {"write": 8, "read": 172}, "ph_setpoint_divisor": 100},
    )
    api = _api()
    number = FluidraChlorinatorPhSetpoint(_coord_with(device), api, POOL_ID, DEVICE_ID)
    _attach_ha(number)

    await number.async_set_native_value(7.4)

    api.control_device_component.assert_awaited_once_with(DEVICE_ID, 8, 740)


async def test_ph_setpoint_async_set_supports_simple_int_feature() -> None:
    """`ph_setpoint` as a plain int means write==read on the same component."""
    device = _chlorinator_device(
        components={},
        features={"ph_setpoint": 16, "ph_setpoint_divisor": 100},
    )
    api = _api()
    number = FluidraChlorinatorPhSetpoint(_coord_with(device), api, POOL_ID, DEVICE_ID)
    _attach_ha(number)

    await number.async_set_native_value(7.2)
    api.control_device_component.assert_awaited_once_with(DEVICE_ID, 16, 720)


# --- FluidraChlorinatorOrpSetpoint ---------------------------------------


def test_orp_setpoint_native_value_is_raw_mv() -> None:
    """ORP setpoint is reported in mV directly (no divisor)."""
    device = _chlorinator_device(
        components={"177": {"reportedValue": 700}},
        features={"orp_setpoint": {"write": 11, "read": 177}},
    )
    number = FluidraChlorinatorOrpSetpoint(_coord_with(device), _api(), POOL_ID, DEVICE_ID)
    _attach_ha(number)
    assert number.native_value == 700.0


async def test_orp_setpoint_async_set_writes_integer_value() -> None:
    """Setting ORP=720.4 mV truncates to 720 before being sent to the API."""
    device = _chlorinator_device(
        components={},
        features={"orp_setpoint": {"write": 11, "read": 177}},
    )
    api = _api()
    number = FluidraChlorinatorOrpSetpoint(_coord_with(device), api, POOL_ID, DEVICE_ID)
    _attach_ha(number)

    await number.async_set_native_value(720.4)
    api.control_device_component.assert_awaited_once_with(DEVICE_ID, 11, 720)


# --- FluidraLightEffectSpeed ---------------------------------------------


def _light_device(comp_20_value: int | None) -> dict:
    components: dict[str, dict] = {}
    if comp_20_value is not None:
        components["20"] = {"reportedValue": comp_20_value}
    return {
        "device_id": "LP24-001",
        "name": "Pool Light",
        "type": "light",
        "online": True,
        "components": components,
    }


def test_light_effect_speed_defaults_to_one_when_missing() -> None:
    """If component 20 is missing the slider defaults to 1 (minimum)."""
    device = _light_device(None)
    coord = _coord_with(device)
    # FluidraPoolControlEntity expects to find the device by id under the pool.
    coord.data[POOL_ID]["devices"][0]["device_id"] = "LP24-001"
    number = FluidraLightEffectSpeed(
        coord, SimpleNamespace(set_component_value=AsyncMock(return_value=True)), POOL_ID, "LP24-001"
    )
    _attach_ha(number)
    assert number.native_value == 1.0


def test_light_effect_speed_reads_component_20() -> None:
    """The slider value comes from component 20."""
    coord = _coord_with(_light_device(6))
    coord.data[POOL_ID]["devices"][0]["device_id"] = "LP24-001"
    number = FluidraLightEffectSpeed(
        coord, SimpleNamespace(set_component_value=AsyncMock(return_value=True)), POOL_ID, "LP24-001"
    )
    _attach_ha(number)
    assert number.native_value == 6.0


@pytest.mark.parametrize("incoming", [3.7, 3.4])
async def test_light_effect_speed_async_set_truncates_to_int(incoming: float) -> None:
    """Effect speed is always written as an int (component 20 expects integer 1-8)."""
    coord = _coord_with(_light_device(1))
    coord.data[POOL_ID]["devices"][0]["device_id"] = "LP24-001"
    api = SimpleNamespace(set_component_value=AsyncMock(return_value=True))
    number = FluidraLightEffectSpeed(coord, api, POOL_ID, "LP24-001")
    _attach_ha(number)

    await number.async_set_native_value(incoming)
    api.set_component_value.assert_awaited_once_with("LP24-001", 20, int(incoming))
