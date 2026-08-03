"""Tests for the eXO iQ Boost / Low / Freeze registers (Issue #175).

@Inervo's captures pinned each of these by toggling one feature in the app and
diffing the full register dump: c46 = boost, c45 = low, c48 = freeze protection,
c51 = boost minutes remaining (1438 on a fresh 24 h cycle).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import UnitOfTime
from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.fluidra_pool.sensor.chlorinator import FluidraBoostRemainingSensor
from custom_components.fluidra_pool.switch.chlorinator import (
    FluidraChlorinatorBoostSwitch,
    FluidraChlorinatorToggleSwitch,
)

POOL_ID = "pool-1"
DEVICE_ID = "NS25007212"


def _coord(device: dict) -> Any:
    coordinator = MagicMock()
    coordinator.data = {POOL_ID: {"id": POOL_ID, "name": "Pool", "devices": [device]}}
    coordinator.last_update_success = True
    coordinator.async_request_refresh = AsyncMock()
    return coordinator


def _device(**components: Any) -> dict:
    """An eXO iQ as identified by its NS* serial, with the given registers."""
    return {
        "device_id": DEVICE_ID,
        "name": "Zodiac EXO iQ 35",
        "family": "Chlorinators",
        "type": "connected",
        "online": True,
        "components": {str(k): {"reportedValue": v} for k, v in components.items()},
    }


def _attach(entity: Any) -> Any:
    """Attach the bits HA usually provides so async_write_ha_state doesn't blow up."""
    entity.hass = MagicMock()
    entity.async_write_ha_state = MagicMock()
    return entity


def _toggle(device: dict, api: Any, feature: str, key: str) -> FluidraChlorinatorToggleSwitch:
    switch = FluidraChlorinatorToggleSwitch(_coord(device), api, POOL_ID, DEVICE_ID, feature, key, "mdi:test")
    return _attach(switch)


# --- Low / Freeze toggles ---------------------------------------------------


@pytest.mark.parametrize(
    ("feature", "key", "component"),
    [("low_mode", "low_mode", 45), ("freeze_protection", "freeze_protection", 48)],
)
def test_toggle_reads_its_register(feature: str, key: str, component: int) -> None:
    api = MagicMock()
    assert _toggle(_device(**{str(component): True}), api, feature, key).is_on is True
    assert _toggle(_device(**{str(component): False}), api, feature, key).is_on is False


def test_toggle_unique_id_is_per_feature() -> None:
    """Low and Freeze must not collide on the same unique_id."""
    api = MagicMock()
    low = _toggle(_device(**{"45": False}), api, "low_mode", "low_mode")
    freeze = _toggle(_device(**{"48": False}), api, "freeze_protection", "freeze_protection")
    assert low.unique_id != freeze.unique_id
    assert low.unique_id == f"fluidra_{DEVICE_ID}_low_mode"


async def test_toggle_writes_the_mapped_component() -> None:
    """Turning Low on writes True to c45 — the register, not the feature name."""
    api = MagicMock()
    api.control_device_component = AsyncMock(return_value=True)
    switch = _toggle(_device(**{"45": False}), api, "low_mode", "low_mode")

    await switch.async_turn_on()

    api.control_device_component.assert_awaited_once_with(DEVICE_ID, 45, True)


async def test_toggle_turn_off_writes_false() -> None:
    api = MagicMock()
    api.control_device_component = AsyncMock(return_value=True)
    switch = _toggle(_device(**{"48": True}), api, "freeze_protection", "freeze_protection")

    await switch.async_turn_off()

    api.control_device_component.assert_awaited_once_with(DEVICE_ID, 48, False)


async def test_toggle_raises_when_the_write_is_refused() -> None:
    """A refused write must surface, not leave a silently wrong optimistic state."""
    api = MagicMock()
    api.control_device_component = AsyncMock(return_value=False)
    switch = _toggle(_device(**{"45": False}), api, "low_mode", "low_mode")

    with pytest.raises(HomeAssistantError):
        await switch.async_turn_on()
    assert switch.is_on is False


async def test_toggle_without_the_feature_does_not_write() -> None:
    """A profile that never declares the feature must not write to component None."""
    api = MagicMock()
    api.control_device_component = AsyncMock(return_value=True)
    switch = _toggle(_device(), api, "not_a_feature", "low_mode")

    with pytest.raises(HomeAssistantError):
        await switch.async_turn_on()
    api.control_device_component.assert_not_awaited()


# --- Boost on the eXO -------------------------------------------------------


def _boost(device: dict, api: Any) -> FluidraChlorinatorBoostSwitch:
    return _attach(FluidraChlorinatorBoostSwitch(_coord(device), api, POOL_ID, DEVICE_ID))


def test_boost_available_while_the_exo_is_in_auto() -> None:
    """boost_requires_on_mode=False: AUTO (c13=1) must not hide the switch."""
    switch = _boost(_device(**{"13": 1, "46": False}), MagicMock())
    # Guard the premise: on the eXO mapping c13=1 is AUTO, so without the
    # feature this switch would be unavailable and the assert below would
    # pass for the wrong reason under the default {0: off, 1: on} mapping.
    assert switch._get_current_mode() == "auto"
    assert switch.available is True


async def test_boost_does_not_clobber_auto_mode() -> None:
    """Enabling boost from AUTO must write c46 only — not force the unit to ON."""
    api = MagicMock()
    api.control_device_component = AsyncMock(return_value=True)
    switch = _boost(_device(**{"13": 1, "46": False}), api)

    await switch.async_turn_on()

    api.control_device_component.assert_awaited_once_with(DEVICE_ID, 46, True)


def test_boost_reads_c46() -> None:
    assert _boost(_device(**{"13": 1, "46": True}), MagicMock()).is_on is True
    assert _boost(_device(**{"13": 1, "46": False}), MagicMock()).is_on is False


# --- Boost countdown --------------------------------------------------------


def _remaining(device: dict) -> FluidraBoostRemainingSensor:
    return FluidraBoostRemainingSensor(_coord(device), MagicMock(), POOL_ID, DEVICE_ID)


def test_boost_countdown_reports_minutes() -> None:
    sensor = _remaining(_device(**{"51": 1438}))
    assert sensor.native_value == 1438
    assert sensor.native_unit_of_measurement == UnitOfTime.MINUTES
    assert sensor.device_class == SensorDeviceClass.DURATION


def test_boost_countdown_zero_is_a_real_reading() -> None:
    """Boost off reports 0; that is data, not a missing value."""
    assert _remaining(_device(**{"51": 0})).native_value == 0


def test_boost_countdown_handles_missing_and_junk_values() -> None:
    assert _remaining(_device()).native_value is None
    assert _remaining(_device(**{"51": "n/a"})).native_value is None
