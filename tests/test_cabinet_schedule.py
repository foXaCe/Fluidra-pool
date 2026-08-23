"""Tests for Command Connect cabinet schedules (c35 pump / c36 lights, Issue #210)."""

from __future__ import annotations

from datetime import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.fluidra_pool.device_registry import DEVICE_CONFIGS, DeviceIdentifier
from custom_components.fluidra_pool.helpers import (
    CABINET_SCHEDULE_ALL_DAYS,
    build_cabinet_schedule_slot,
    get_cabinet_schedule_data,
    resolve_cabinet_schedule_component,
    schedule_slots_for_write,
)
from custom_components.fluidra_pool.switch.schedule import FluidraScheduleEnableSwitch
from custom_components.fluidra_pool.time.cabinet_schedule import (
    FluidraCabinetScheduleEndTimeEntity,
    FluidraCabinetScheduleStartTimeEntity,
)
from custom_components.fluidra_pool.write_verification import (
    VERDICT_LOST,
    VERDICT_VERIFIED,
    WriteVerifier,
    normalize_component_value,
)

POOL_ID = "pool-1"
DEVICE_ID = "QR24xxxx.ndsr_1"

CABINET_SCHEDULE_COMPONENTS = {"pump": 35, "lights": 36}

PUMP_SCHEDULE = {
    "id": 1,
    "groupId": 1,
    "enabled": True,
    "state": "RUNNING",
    "startTime": "15 10 * * 0,1,2,3,4,5,6",
    "endTime": "30 18 * * 0,1,2,3,4,5,6",
    "startActions": {"operationName": "1"},
}

LIGHTS_SCHEDULE = {
    "id": 1,
    "groupId": 1,
    "enabled": True,
    "state": "IDLE",
    "startTime": "25 18 * * 0,1,2,3,4,5,6",
    "endTime": "00 22 * * 0,1,2,3,4,5,6",
    "startActions": {"operationName": "1"},
}


@pytest.fixture(autouse=True)
def _skip_sleeps() -> Any:
    """Skip confirmation delays so tests don't wait."""
    with patch("custom_components.fluidra_pool.time.cabinet_schedule.asyncio.sleep", new=AsyncMock()):
        yield


def _identify_cache() -> dict:
    return {
        "_identify_cache": {
            "key": (DEVICE_ID, "Cabinets", "Command Connect", "cabinet", ""),
            "config": SimpleNamespace(
                device_type="cabinet",
                features={
                    "cabinet_schedule_components": CABINET_SCHEDULE_COMPONENTS,
                    "cabinet_schedule_count": 1,
                    "cabinet_packed_config": 16,
                    "schedule_local_time": True,
                    "schedule_armed_window": True,
                    "boolean_writes": True,
                },
                components_range=40,
                required_components=[13, 24],
                entities=["switch"],
            ),
        },
    }


def _cabinet_device(schedules: dict[str, list[dict]] | None = None) -> dict[str, Any]:
    device: dict[str, Any] = {
        "device_id": DEVICE_ID,
        "name": "Command Connect",
        "family": "Cabinets",
        "model": "Command Connect",
        "type": "cabinet",
        "online": True,
        "components": {
            "13": {"reportedValue": False},
            "15": {"reportedValue": True},
            "16": {"reportedValue": "01101518301270101"},
            "24": {"reportedValue": True},
            "26": {"reportedValue": False},
            "35": {"reportedValue": [PUMP_SCHEDULE]},
            "36": {"reportedValue": [LIGHTS_SCHEDULE]},
        },
        **_identify_cache(),
    }
    if schedules is not None:
        device["cabinet_schedule_data"] = schedules
    return device


def _coord(device: dict) -> Any:
    coordinator = MagicMock()
    coordinator.data = {POOL_ID: {"id": POOL_ID, "name": "Pool", "devices": [device]}}
    coordinator.async_request_refresh = AsyncMock()
    coordinator.last_update_success = True
    return coordinator


def _attach_ha(entity) -> None:
    entity.hass = MagicMock()
    entity.async_write_ha_state = MagicMock()


def _api(*, success: bool = True) -> SimpleNamespace:
    return SimpleNamespace(set_schedule=AsyncMock(return_value=success))


# --- profile / helpers ----------------------------------------------------


def test_cabinet_profile_declares_schedule_registers() -> None:
    features = DEVICE_CONFIGS["command_connect_cabinet"].features
    assert features["cabinet_schedule_components"] == CABINET_SCHEDULE_COMPONENTS
    assert features["cabinet_schedule_count"] == 1
    assert features["cabinet_packed_config"] == 16
    assert features["schedule_local_time"] is True
    assert features["schedule_armed_window"] is True
    for component in (35, 36, 16):
        assert component in features["specific_components"]


def test_build_cabinet_schedule_slot_matches_capture() -> None:
    """Exact write shape from @efgonzalez — no state, local CRON, days 0-6."""
    slot = build_cabinet_schedule_slot(1, time(10, 15), time(18, 30))
    assert slot == {
        "id": 1,
        "groupId": 1,
        "enabled": True,
        "startTime": "15 10 * * 0,1,2,3,4,5,6",
        "endTime": "30 18 * * 0,1,2,3,4,5,6",
        "startActions": {"operationName": "1"},
    }
    assert "state" not in slot
    assert CABINET_SCHEDULE_ALL_DAYS in slot["startTime"]


def test_schedule_slots_for_write_strips_state() -> None:
    normalised = schedule_slots_for_write([PUMP_SCHEDULE])
    assert "state" not in normalised[0]
    assert normalised[0]["startTime"] == PUMP_SCHEDULE["startTime"]


def test_get_and_resolve_cabinet_schedule() -> None:
    device = _cabinet_device({"pump": [PUMP_SCHEDULE], "lights": [LIGHTS_SCHEDULE]})
    assert get_cabinet_schedule_data(device, "pump", 1) == PUMP_SCHEDULE
    assert get_cabinet_schedule_data(device, "lights", "1") == LIGHTS_SCHEDULE
    assert get_cabinet_schedule_data(device, "pump", 99) is None
    assert resolve_cabinet_schedule_component(device, "pump") == 35
    assert resolve_cabinet_schedule_component(device, "lights") == 36


# --- coordinator parsing --------------------------------------------------


def test_coordinator_parses_cabinet_schedule_registers() -> None:
    device = _cabinet_device(None)
    # Drop the pre-seeded cache schedules path; exercise the decoder.
    device.pop("cabinet_schedule_data", None)

    from custom_components.fluidra_pool.coordinator.coordinator import (
        FluidraDataUpdateCoordinator as _Coord,
    )

    coord = _Coord.__new__(_Coord)
    coord._track_schedule_count = MagicMock()
    coord._process_component_state(device, POOL_ID, 35, {"reportedValue": [PUMP_SCHEDULE]})
    coord._process_component_state(device, POOL_ID, 36, {"reportedValue": [LIGHTS_SCHEDULE]})
    assert device["cabinet_schedule_data"]["pump"] == [PUMP_SCHEDULE]
    assert device["cabinet_schedule_data"]["lights"] == [LIGHTS_SCHEDULE]

    coord._apply_resolved_cabinet_schedules(device)
    assert device["cabinet_schedule_components_resolved"] == {"pump": 35, "lights": 36}


# --- time entities --------------------------------------------------------


def test_cabinet_schedule_start_native_value() -> None:
    device = _cabinet_device({"pump": [PUMP_SCHEDULE]})
    entity = FluidraCabinetScheduleStartTimeEntity(_coord(device), _api(), POOL_ID, DEVICE_ID, "pump", "1")
    assert entity.native_value == time(10, 15)
    assert entity.extra_state_attributes["schedule_semantics"] == "armed_window"
    assert entity.extra_state_attributes["schedule_local_time"] is True


def test_cabinet_schedule_end_native_value() -> None:
    device = _cabinet_device({"lights": [LIGHTS_SCHEDULE]})
    entity = FluidraCabinetScheduleEndTimeEntity(_coord(device), _api(), POOL_ID, DEVICE_ID, "lights", "1")
    assert entity.native_value == time(22, 0)


def test_cabinet_schedule_available_when_empty() -> None:
    device = _cabinet_device({"pump": []})
    entity = FluidraCabinetScheduleStartTimeEntity(_coord(device), _api(), POOL_ID, DEVICE_ID, "pump", "1")
    assert entity.available is True
    assert entity.native_value is None


@pytest.mark.asyncio
async def test_cabinet_schedule_start_writes_pump_component_without_state() -> None:
    device = _cabinet_device({"pump": [PUMP_SCHEDULE]})
    api = _api()
    entity = FluidraCabinetScheduleStartTimeEntity(_coord(device), api, POOL_ID, DEVICE_ID, "pump", "1")
    _attach_ha(entity)

    await entity.async_set_value(time(11, 0))

    api.set_schedule.assert_awaited_once()
    args, kwargs = api.set_schedule.await_args
    assert args[0] == DEVICE_ID
    assert kwargs["component_id"] == 35
    sent = args[1]
    assert len(sent) == 1
    assert "state" not in sent[0]
    assert sent[0]["startTime"].startswith("0 11")
    assert "0,1,2,3,4,5,6" in sent[0]["startTime"]
    assert sent[0]["endTime"] == PUMP_SCHEDULE["endTime"]


@pytest.mark.asyncio
async def test_cabinet_schedule_end_writes_lights_component() -> None:
    device = _cabinet_device({"lights": [LIGHTS_SCHEDULE]})
    api = _api()
    entity = FluidraCabinetScheduleEndTimeEntity(_coord(device), api, POOL_ID, DEVICE_ID, "lights", "1")
    _attach_ha(entity)

    await entity.async_set_value(time(23, 0))

    args, kwargs = api.set_schedule.await_args
    assert kwargs["component_id"] == 36
    assert "state" not in args[1][0]
    assert args[1][0]["endTime"].startswith("0 23")


@pytest.mark.asyncio
async def test_cabinet_schedule_seeds_slot_when_empty() -> None:
    device = _cabinet_device({"pump": []})
    api = _api()
    entity = FluidraCabinetScheduleStartTimeEntity(_coord(device), api, POOL_ID, DEVICE_ID, "pump", "1")
    _attach_ha(entity)

    await entity.async_set_value(time(10, 15))

    sent = api.set_schedule.await_args.args[1]
    assert sent == [build_cabinet_schedule_slot(1, time(10, 15), time(11, 15), enabled=True)]


@pytest.mark.asyncio
async def test_cabinet_schedule_set_raises_on_api_failure() -> None:
    device = _cabinet_device({"pump": [PUMP_SCHEDULE]})
    entity = FluidraCabinetScheduleStartTimeEntity(_coord(device), _api(success=False), POOL_ID, DEVICE_ID, "pump", "1")
    _attach_ha(entity)
    with pytest.raises(HomeAssistantError):
        await entity.async_set_value(time(9, 0))


# --- enable switch --------------------------------------------------------


def test_cabinet_enable_switch_is_on() -> None:
    device = _cabinet_device({"pump": [PUMP_SCHEDULE]})
    switch = FluidraScheduleEnableSwitch(_coord(device), _api(), POOL_ID, DEVICE_ID, "1", cabinet_output="pump")
    assert switch.is_on is True
    assert switch.unique_id.endswith("cabinet_pump_schedule_1_enabled")
    assert switch.extra_state_attributes["schedule_semantics"] == "armed_window"


@pytest.mark.asyncio
async def test_cabinet_enable_switch_turn_off_strips_state() -> None:
    device = _cabinet_device({"lights": [LIGHTS_SCHEDULE]})
    api = _api()
    switch = FluidraScheduleEnableSwitch(_coord(device), api, POOL_ID, DEVICE_ID, "1", cabinet_output="lights")
    _attach_ha(switch)

    await switch.async_turn_off()

    args, kwargs = api.set_schedule.await_args
    assert kwargs["component_id"] == 36
    assert args[1][0]["enabled"] is False
    assert "state" not in args[1][0]


# --- entity setup ---------------------------------------------------------


def test_cabinet_schedule_entities_created_for_profile() -> None:
    from custom_components.fluidra_pool.time import __init__ as time_platform
    from custom_components.fluidra_pool.time import async_setup_entry as _unused  # noqa: F401

    # Replicate the builder logic used by async_setup_entry.
    device = {
        "device_id": DEVICE_ID,
        "family": "Cabinets",
        "name": "Command Connect",
        "type": "cabinet",
    }
    config = DeviceIdentifier.identify_device(device)
    assert config is DEVICE_CONFIGS["command_connect_cabinet"]

    components = DeviceIdentifier.get_feature(device, "cabinet_schedule_components", {})
    count = DeviceIdentifier.get_feature(device, "cabinet_schedule_count", 1)
    expected = len(components) * count * 2  # start + end
    assert expected == 4
    assert "pump" in components
    assert "lights" in components
    # Silence unused import lint for the platform module touch.
    assert time_platform is not None


# --- write verification for schedule lists --------------------------------


def test_normalize_strips_state_for_schedule_list_compare() -> None:
    reported = [PUMP_SCHEDULE]
    desired = schedule_slots_for_write([PUMP_SCHEDULE])
    assert normalize_component_value(reported) == normalize_component_value(desired)


def test_write_verifier_verifies_schedule_list_round_trip() -> None:
    verifier = WriteVerifier()
    desired = schedule_slots_for_write([PUMP_SCHEDULE])
    verifier.record(DEVICE_ID, 35, desired, baseline=[])
    pending = next(iter(verifier._pending.values()))
    # Device echoes the slot back with a runtime state field.
    assert verifier.resolve(pending, [PUMP_SCHEDULE]) == VERDICT_VERIFIED


def test_write_verifier_detects_lost_schedule_clear() -> None:
    verifier = WriteVerifier()
    baseline = [PUMP_SCHEDULE]
    verifier.record(DEVICE_ID, 35, [], baseline=baseline)
    pending = next(iter(verifier._pending.values()))
    assert verifier.resolve(pending, baseline) == VERDICT_LOST


def test_write_verifier_verifies_empty_clear() -> None:
    verifier = WriteVerifier()
    verifier.record(DEVICE_ID, 36, [], baseline=[LIGHTS_SCHEDULE])
    pending = next(iter(verifier._pending.values()))
    assert verifier.resolve(pending, []) == VERDICT_VERIFIED
