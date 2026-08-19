"""Tests for aux-output schedules on the eXO iQ (Aux 1 = c22, Aux 2 = c24).

Covers the time entities (start/end) and the enable switch, plus the
coordinator parsing of the fixed aux schedule registers (Issue #174).
"""

from __future__ import annotations

from datetime import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.fluidra_pool.device_registry import DeviceIdentifier
from custom_components.fluidra_pool.switch.schedule import FluidraScheduleEnableSwitch
from custom_components.fluidra_pool.time.aux_schedule import (
    FluidraAuxScheduleEndTimeEntity,
    FluidraAuxScheduleStartTimeEntity,
)

POOL_ID = "pool-1"
DEVICE_ID = "NS25007212"

AUX_SCHEDULE_COMPONENTS = {"1": 22, "2": 24}


@pytest.fixture(autouse=True)
def _skip_sleeps() -> Any:
    """Skip optimistic delays so tests don't wait."""
    with patch("custom_components.fluidra_pool.time.aux_schedule.asyncio.sleep", new=AsyncMock()):
        yield


def _identify_cache() -> dict:
    return {
        "_identify_cache": {
            "key": (DEVICE_ID, "", "", "chlorinator", ""),
            "config": SimpleNamespace(
                device_type="chlorinator",
                features={
                    "schedule_component": 20,
                    "aux_schedule_components": AUX_SCHEDULE_COMPONENTS,
                    "aux_schedule_count": 2,
                },
                components_range=25,
                required_components=[0, 1, 2, 3],
                entities=[],
            ),
        },
    }


def _aux_device(aux_schedules: dict[str, list[dict]] | None) -> dict:
    """eXO device with per-aux schedule data (Aux 1 = c22, Aux 2 = c24)."""
    device: dict[str, Any] = {
        "device_id": DEVICE_ID,
        "name": "eXO iQ 18 R",
        "family": "",
        "model": "",
        "type": "chlorinator",
        "online": True,
        "components": {},
        **_identify_cache(),
    }
    if aux_schedules is not None:
        device["aux_schedule_data"] = aux_schedules
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


AUX1_SCHEDULE = {
    "id": 1,
    "groupId": 1,
    "state": "IDLE",
    "enabled": True,
    "startTime": "0 20 * * 1,2,3",
    "endTime": "0 21 * * 1,2,3",
    "startActions": {},
}


# --- coordinator parsing --------------------------------------------------


def test_coordinator_parses_aux_schedule_registers() -> None:
    """c22/c24 reportedValue lists land in device['aux_schedule_data'] keyed by aux."""
    device = _aux_device(None)
    device["type"] = "chlorinator"
    device["family"] = "chlorinator"

    config = DeviceIdentifier.identify_device(device)
    assert config is not None
    assert config.features.get("aux_schedule_components") == AUX_SCHEDULE_COMPONENTS

    # Directly exercise the decoder branch without a full coordinator.
    from custom_components.fluidra_pool.coordinator.coordinator import (
        FluidraDataUpdateCoordinator as _Coord,
    )

    coord = _Coord.__new__(_Coord)  # bypass __init__; only _process_component_state is used
    coord._track_schedule_count = MagicMock()
    coord._process_component_state(device, POOL_ID, 22, {"reportedValue": [AUX1_SCHEDULE], "id": 22})
    coord._process_component_state(device, POOL_ID, 24, {"reportedValue": [], "id": 24})

    assert device["aux_schedule_data"]["1"] == [AUX1_SCHEDULE]
    assert device["aux_schedule_data"]["2"] == []


def test_coordinator_does_not_parse_aux_registers_without_feature() -> None:
    """A device without aux_schedule_components ignores c22/c24 as aux schedules."""
    from custom_components.fluidra_pool.coordinator.coordinator import (
        FluidraDataUpdateCoordinator as _Coord,
    )

    plain_device: dict[str, Any] = {
        "device_id": "DM-OTHER",
        "name": "Chlorinator",
        "family": "",
        "model": "",
        "type": "chlorinator",
        "online": True,
        "components": {},
        "_identify_cache": {
            "key": ("DM-OTHER", "", "", "chlorinator", ""),
            "config": SimpleNamespace(
                device_type="chlorinator",
                features={},  # no aux_schedule_components
                components_range=25,
                required_components=[0, 1, 2, 3],
                entities=[],
            ),
        },
    }
    coord = _Coord.__new__(_Coord)
    coord._track_schedule_count = MagicMock()
    coord._process_component_state(plain_device, POOL_ID, 22, {"reportedValue": [AUX1_SCHEDULE], "id": 22})

    assert "aux_schedule_data" not in plain_device


# --- time entity native_value --------------------------------------------


def test_aux_schedule_start_native_value_decodes_cron() -> None:
    """The start time entity reads the aux 1 slot from aux_schedule_data."""
    device = _aux_device({"1": [AUX1_SCHEDULE]})
    entity = FluidraAuxScheduleStartTimeEntity(_coord(device), _api(), POOL_ID, DEVICE_ID, "1", "1")
    _attach_ha(entity)

    assert entity.native_value == time(20, 0)
    assert entity.available is True


def test_aux_schedule_end_native_value_decodes_cron() -> None:
    """The end time entity reads the aux 1 slot from aux_schedule_data."""
    device = _aux_device({"1": [AUX1_SCHEDULE]})
    entity = FluidraAuxScheduleEndTimeEntity(_coord(device), _api(), POOL_ID, DEVICE_ID, "1", "1")
    _attach_ha(entity)

    assert entity.native_value == time(21, 0)


def test_aux_schedule_entity_unavailable_without_aux_data() -> None:
    """No aux schedule data for this aux → the entity stays unavailable."""
    device = _aux_device({})
    entity = FluidraAuxScheduleStartTimeEntity(_coord(device), _api(), POOL_ID, DEVICE_ID, "1", "1")
    _attach_ha(entity)

    assert entity.native_value is None
    assert entity.available is False


def test_aux_schedule_entity_unavailable_when_aux_register_empty() -> None:
    """An empty aux register (no schedules configured) leaves the entity unavailable."""
    device = _aux_device({"1": []})
    entity = FluidraAuxScheduleStartTimeEntity(_coord(device), _api(), POOL_ID, DEVICE_ID, "1", "1")
    _attach_ha(entity)

    assert entity.native_value is None
    assert entity.available is False


def test_aux_schedule_icons() -> None:
    """Start and end time entities expose their clock icons."""
    device = _aux_device({"1": [AUX1_SCHEDULE]})
    start = FluidraAuxScheduleStartTimeEntity(_coord(device), _api(), POOL_ID, DEVICE_ID, "1", "1")
    end = FluidraAuxScheduleEndTimeEntity(_coord(device), _api(), POOL_ID, DEVICE_ID, "1", "1")
    assert start.icon == "mdi:clock-start"
    assert end.icon == "mdi:clock-end"


def test_aux_schedule_native_value_returns_optimistic() -> None:
    """While an optimistic value is pending it wins over the API data."""
    device = _aux_device({"1": [AUX1_SCHEDULE]})
    start = FluidraAuxScheduleStartTimeEntity(_coord(device), _api(), POOL_ID, DEVICE_ID, "1", "1")
    end = FluidraAuxScheduleEndTimeEntity(_coord(device), _api(), POOL_ID, DEVICE_ID, "1", "1")
    _attach_ha(start)
    _attach_ha(end)

    start._optimistic_value = time(6, 0)
    end._optimistic_value = time(7, 30)
    assert start.native_value == time(6, 0)
    assert end.native_value == time(7, 30)


# --- time entity write path -----------------------------------------------


async def test_aux_schedule_start_set_value_writes_aux_component() -> None:
    """Setting the start time PUTs the aux 1 register (component 22)."""
    device = _aux_device({"1": [dict(AUX1_SCHEDULE)]})
    api = _api()
    entity = FluidraAuxScheduleStartTimeEntity(_coord(device), api, POOL_ID, DEVICE_ID, "1", "1")
    _attach_ha(entity)

    await entity.async_set_value(time(8, 30))

    api.set_schedule.assert_awaited_once()
    _args, kwargs = api.set_schedule.await_args
    assert kwargs["component_id"] == 22
    sent = _args[1]
    assert sent[0]["startTime"] == "30 8 * * 1,2,3"
    assert sent[0]["endTime"] == "0 21 * * 1,2,3"
    assert sent[0]["enabled"] is True
    # Read-only runtime fields are stripped for the write (Issue #89/#174).
    assert "state" not in sent[0]
    assert "endActions" not in sent[0]


async def test_aux_schedule_end_set_value_writes_aux_component() -> None:
    """Setting the end time PUTs the aux 2 register (component 24)."""
    device = _aux_device({"2": [dict(AUX1_SCHEDULE)]})
    api = _api()
    entity = FluidraAuxScheduleEndTimeEntity(_coord(device), api, POOL_ID, DEVICE_ID, "2", "1")
    _attach_ha(entity)

    await entity.async_set_value(time(22, 15))

    api.set_schedule.assert_awaited_once()
    _args, kwargs = api.set_schedule.await_args
    assert kwargs["component_id"] == 24
    sent = _args[1]
    assert sent[0]["endTime"] == "15 22 * * 1,2,3"


async def test_aux_schedule_start_set_value_no_op_without_aux_data() -> None:
    """No aux schedule data → the write is a no-op (no API call)."""
    device = _aux_device({})
    api = _api()
    entity = FluidraAuxScheduleStartTimeEntity(_coord(device), api, POOL_ID, DEVICE_ID, "1", "1")
    _attach_ha(entity)

    await entity.async_set_value(time(8, 0))
    api.set_schedule.assert_not_awaited()


async def test_aux_schedule_start_set_value_raises_on_api_failure() -> None:
    """A rejected PUT surfaces as HomeAssistantError."""
    device = _aux_device({"1": [dict(AUX1_SCHEDULE)]})
    api = _api(success=False)
    entity = FluidraAuxScheduleStartTimeEntity(_coord(device), api, POOL_ID, DEVICE_ID, "1", "1")
    _attach_ha(entity)

    with pytest.raises(HomeAssistantError):
        await entity.async_set_value(time(8, 0))


async def test_aux_schedule_set_value_raises_on_connection_error() -> None:
    """A connection error during the PUT surfaces as HomeAssistantError."""
    from custom_components.fluidra_pool.api_resilience import FluidraConnectionError

    device = _aux_device({"1": [dict(AUX1_SCHEDULE)]})
    api = SimpleNamespace(set_schedule=AsyncMock(side_effect=FluidraConnectionError("net down")))
    start = FluidraAuxScheduleStartTimeEntity(_coord(device), api, POOL_ID, DEVICE_ID, "1", "1")
    end = FluidraAuxScheduleEndTimeEntity(_coord(device), api, POOL_ID, DEVICE_ID, "1", "1")
    _attach_ha(start)
    _attach_ha(end)

    with pytest.raises(HomeAssistantError):
        await start.async_set_value(time(8, 0))
    with pytest.raises(HomeAssistantError):
        await end.async_set_value(time(9, 0))


async def test_aux_schedule_start_set_value_rejects_overlap() -> None:
    """A start time overlapping another enabled aux-1 slot is rejected."""
    from homeassistant.exceptions import ServiceValidationError

    slot2 = {
        "id": 2,
        "groupId": 2,
        "state": "IDLE",
        "enabled": True,
        "startTime": "0 20 * * 1,2,3",
        "endTime": "0 21 * * 1,2,3",
        "startActions": {},
    }
    device = _aux_device({"1": [dict(AUX1_SCHEDULE), slot2]})
    entity = FluidraAuxScheduleStartTimeEntity(_coord(device), _api(), POOL_ID, DEVICE_ID, "1", "1")
    _attach_ha(entity)

    # Editing slot 1's start to 20:30 collides with slot 2's 20:00-21:00 window.
    with pytest.raises(ServiceValidationError):
        await entity.async_set_value(time(20, 30))


async def test_aux_schedule_end_set_value_rejects_overlap() -> None:
    """An end time overlapping another enabled aux-1 slot is rejected."""
    from homeassistant.exceptions import ServiceValidationError

    slot2 = {
        "id": 2,
        "groupId": 2,
        "state": "IDLE",
        "enabled": True,
        "startTime": "0 20 * * 1,2,3",
        "endTime": "0 21 * * 1,2,3",
        "startActions": {},
    }
    device = _aux_device({"1": [dict(AUX1_SCHEDULE), slot2]})
    entity = FluidraAuxScheduleEndTimeEntity(_coord(device), _api(), POOL_ID, DEVICE_ID, "1", "1")
    _attach_ha(entity)

    # Editing slot 1's end to 20:45 collides with slot 2's 20:00-21:00 window.
    with pytest.raises(ServiceValidationError):
        await entity.async_set_value(time(20, 45))


# --- enable switch --------------------------------------------------------


def test_aux_schedule_enable_switch_is_on_from_aux_data() -> None:
    """The enable switch reads the aux slot's enabled flag."""
    device = _aux_device({"1": [AUX1_SCHEDULE]})
    entity = FluidraScheduleEnableSwitch(_coord(device), _api(), POOL_ID, DEVICE_ID, "1", aux_number="1")
    _attach_ha(entity)

    assert entity.is_on is True
    assert entity.available is True


def test_aux_schedule_enable_switch_unique_id() -> None:
    """The aux enable switch has a distinct unique_id (aux-qualified)."""
    device = _aux_device({"1": [AUX1_SCHEDULE]})
    entity = FluidraScheduleEnableSwitch(_coord(device), _api(), POOL_ID, DEVICE_ID, "1", aux_number="1")
    assert entity.unique_id == f"fluidra_{DEVICE_ID}_aux1_schedule_1_enabled"


async def test_aux_schedule_enable_switch_turn_off_writes_aux_component() -> None:
    """Disabling an aux schedule PUTs the aux 1 register (component 22)."""
    device = _aux_device({"1": [dict(AUX1_SCHEDULE)]})
    api = _api()
    entity = FluidraScheduleEnableSwitch(_coord(device), api, POOL_ID, DEVICE_ID, "1", aux_number="1")
    _attach_ha(entity)

    await entity.async_turn_off()

    api.set_schedule.assert_awaited_once()
    _args, kwargs = api.set_schedule.await_args
    assert kwargs["component_id"] == 22
    assert _args[1][0]["enabled"] is False


async def test_aux_schedule_enable_switch_turn_on_writes_aux_component() -> None:
    """Enabling an aux schedule PUTs the aux 2 register (component 24)."""
    disabled = dict(AUX1_SCHEDULE)
    disabled["enabled"] = False
    device = _aux_device({"2": [disabled]})
    api = _api()
    entity = FluidraScheduleEnableSwitch(_coord(device), api, POOL_ID, DEVICE_ID, "1", aux_number="2")
    _attach_ha(entity)

    await entity.async_turn_on()

    api.set_schedule.assert_awaited_once()
    _args, kwargs = api.set_schedule.await_args
    assert kwargs["component_id"] == 24
    assert _args[1][0]["enabled"] is True


async def test_aux_schedule_enable_switch_no_op_without_aux_data() -> None:
    """No aux data → toggle is a no-op."""
    device = _aux_device({})
    api = _api()
    entity = FluidraScheduleEnableSwitch(_coord(device), api, POOL_ID, DEVICE_ID, "1", aux_number="1")
    _attach_ha(entity)

    await entity.async_turn_on()
    api.set_schedule.assert_not_awaited()


# --- platform gating ------------------------------------------------------


def test_aux_schedule_entities_only_for_exo_profile() -> None:
    """Devices without aux_schedule_components get no aux schedule entities.

    Exercises the platform wiring through the shared builder for a non-eXO
    device (a plain pump) to prove no aux entities are produced.
    """
    from homeassistant.components.switch import SwitchEntity
    from homeassistant.components.time import TimeEntity

    from custom_components.fluidra_pool.switch.__init__ import async_setup_entry as switch_setup
    from custom_components.fluidra_pool.time.__init__ import async_setup_entry as time_setup

    pump_device: dict[str, Any] = {
        "device_id": "PUMP-1",
        "name": "Pump",
        "family": "",
        "model": "E30iQ",
        "type": "pump",
        "online": True,
        "components": {},
        "schedule_data": [],
        "_identify_cache": {
            "key": ("PUMP-1", "", "", "pump", ""),
            "config": SimpleNamespace(
                device_type="pump",
                features={"skip_schedules": True},
                components_range=25,
                required_components=[0, 1, 2, 3],
                entities=[],
            ),
        },
    }
    coordinator = _coord(pump_device)
    coordinator.api.cached_pools = [{"id": POOL_ID, "devices": [pump_device]}]
    coordinator.get_pools_from_data = MagicMock(return_value=[])
    config_entry = MagicMock(runtime_data=SimpleNamespace(coordinator=coordinator))
    config_entry.async_on_unload = lambda _callback: None

    added_switch: list[SwitchEntity] = []
    added_time: list[TimeEntity] = []

    import asyncio

    async def _run() -> None:
        await switch_setup(None, config_entry, added_switch.append)
        await time_setup(None, config_entry, added_time.append)

    asyncio.get_event_loop().run_until_complete(_run())

    # No aux schedule entities on a device without the feature.
    assert not any("aux1" in str(e) for e in added_switch)
    assert not any("aux1" in str(e) for e in added_time)


# --- helpers --------------------------------------------------------------


def test_get_aux_schedule_data_finds_slot() -> None:
    """get_aux_schedule_data matches by string id across aux numbers."""
    from custom_components.fluidra_pool.helpers import get_aux_schedule_data

    device = _aux_device({"1": [AUX1_SCHEDULE], "2": [{"id": 1, "enabled": False}]})
    assert get_aux_schedule_data(device, "1", 1) == AUX1_SCHEDULE
    assert get_aux_schedule_data(device, "2", "1") == {"id": 1, "enabled": False}
    assert get_aux_schedule_data(device, "1", "99") is None
    assert get_aux_schedule_data({}, "1", "1") is None


# --- Issue #174: a colour-LED aux carries its colour in the slot --------------


COLOUR_SLOT = {
    "id": 1,
    "groupId": 1,
    "enabled": True,
    "startTime": "00 10 * * 5",
    "endTime": "00 11 * * 5",
    "startActions": {"operationName": "1", "componentActions": [{"id": 0, "reportedValue": 7}]},
}


def test_aux_switch_reports_the_raw_colour_and_both_candidate_names() -> None:
    """The two LED tables disagree on index 7, so neither name is asserted."""
    device = _aux_device({"1": [COLOUR_SLOT]})
    entity = FluidraScheduleEnableSwitch(_coord(device), _api(), POOL_ID, DEVICE_ID, "1", aux_number="1")
    _attach_ha(entity)

    attrs = entity.extra_state_attributes
    assert attrs["colour_index"] == 7
    assert attrs["colour_candidates"] == {"lumiplus": "sequence_1", "zodiac_nl": "emerald_green"}
    assert attrs["schedule_component"] == 22


def test_aux_switch_omits_colour_for_a_plain_on_off_slot() -> None:
    """An output with no componentActions has no colour to report."""
    device = _aux_device({"1": [AUX1_SCHEDULE]})
    entity = FluidraScheduleEnableSwitch(_coord(device), _api(), POOL_ID, DEVICE_ID, "1", aux_number="1")
    _attach_ha(entity)

    attrs = entity.extra_state_attributes
    assert "colour_index" not in attrs
    assert "colour_candidates" not in attrs


def test_aux_switch_omits_colour_when_the_index_is_off_both_tables() -> None:
    """An unknown index is not dressed up with a name it may not have."""
    slot = {**COLOUR_SLOT, "startActions": {"componentActions": [{"id": 0, "reportedValue": 99}]}}
    device = _aux_device({"1": [slot]})
    entity = FluidraScheduleEnableSwitch(_coord(device), _api(), POOL_ID, DEVICE_ID, "1", aux_number="1")
    _attach_ha(entity)

    assert "colour_index" not in entity.extra_state_attributes


def test_main_schedule_switch_reports_no_colour_attributes() -> None:
    """Only aux outputs drive LEDs; the pump switch must be untouched."""
    device = _aux_device({"1": [COLOUR_SLOT]})
    device["schedule_data"] = [dict(AUX1_SCHEDULE)]
    entity = FluidraScheduleEnableSwitch(_coord(device), _api(), POOL_ID, DEVICE_ID, "1")
    _attach_ha(entity)

    attrs = entity.extra_state_attributes
    assert "schedule_component" not in attrs
    assert "colour_index" not in attrs
