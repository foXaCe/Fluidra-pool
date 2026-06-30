"""Tests for the binary_sensor platform (chlorinator cell production state)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.fluidra_pool.binary_sensor import (
    FluidraChlorinatorProducingBinarySensor,
    async_setup_entry,
)
from custom_components.fluidra_pool.const import DOMAIN
from custom_components.fluidra_pool.device_registry import DEVICE_CONFIGS, DeviceIdentifier

POOL_ID = "pool-1"
DEVICE_ID = "TEST-DEV-001"
PRODUCTION_COMPONENT = 154


def _coord(devices: list[dict]) -> Any:
    coordinator = MagicMock()
    coordinator.data = {POOL_ID: {"id": POOL_ID, "name": "Pool", "devices": devices}}
    coordinator.last_update_success = True
    return coordinator


def _device(components: dict | None = None, **extra: Any) -> dict:
    device = {
        "device_id": DEVICE_ID,
        "name": "Chlorinator",
        "family": "Chlorinators",
        "type": "chlorinator",
        "model": "Chlorinator",
        "online": True,
        "components": components or {},
    }
    device.update(extra)
    return device


def _sensor(device: dict) -> FluidraChlorinatorProducingBinarySensor:
    return FluidraChlorinatorProducingBinarySensor(
        _coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID, PRODUCTION_COMPONENT
    )


def test_is_on_true_when_producing() -> None:
    """A non-zero production register means the cell is actively producing."""
    device = _device(components={"154": {"reportedValue": 100}})
    assert _sensor(device).is_on is True


def test_is_on_false_when_idle() -> None:
    """A zero production register means the cell is idle."""
    device = _device(components={"154": {"reportedValue": 0}})
    assert _sensor(device).is_on is False


def test_is_on_none_when_no_reading() -> None:
    """Missing reportedValue returns None (HA shows unavailable), not a stale off."""
    device = _device(components={})
    assert _sensor(device).is_on is None


def test_is_on_none_on_non_numeric_value() -> None:
    """Unparsable readings degrade gracefully to None."""
    device = _device(components={"154": {"reportedValue": "n/a"}})
    assert _sensor(device).is_on is None


def test_available_when_components_present_even_if_offline() -> None:
    """Bridged children mis-report online=False; availability follows component data (Issue #63)."""
    device = _device(components={"154": {"reportedValue": 0}}, online=False)
    assert _sensor(device).available is True


def test_unavailable_when_no_components() -> None:
    """Without any component data the sensor is unavailable."""
    device = _device(components={})
    assert _sensor(device).available is False


def test_device_data_empty_when_coordinator_has_no_data() -> None:
    """A coordinator with data=None yields empty device_data and a None reading."""
    device = _device(components={"154": {"reportedValue": 100}})
    sensor = _sensor(device)
    sensor.coordinator.data = None
    assert sensor.device_data == {}
    assert sensor.is_on is None


def test_device_data_empty_when_device_absent() -> None:
    """A pool that doesn't contain this device_id yields empty device_data."""
    other = _device(components={"154": {"reportedValue": 100}})
    other["device_id"] = "OTHER-DEV"
    sensor = FluidraChlorinatorProducingBinarySensor(
        _coord([other]), SimpleNamespace(), POOL_ID, DEVICE_ID, PRODUCTION_COMPONENT
    )
    assert sensor.device_data == {}


def test_device_info_uses_device_name_and_links_pool() -> None:
    """device_info carries the device name and is wired to the pool via_device."""
    device = _device(components={"154": {"reportedValue": 0}})
    info = _sensor(device).device_info
    assert info["name"] == "Chlorinator"
    assert (DOMAIN, DEVICE_ID) in info["identifiers"]
    assert info["via_device"] == (DOMAIN, POOL_ID)


async def test_setup_ignores_non_chlorinator_devices() -> None:
    """A non-chlorinator device never gets a producing binary sensor."""
    pump = _pinned_chlorinator("pump1", production_component=154)
    pump["type"] = "pump"
    pump["_identify_cache"]["config"].device_type = "pump"
    added, _, _ = await _run_setup([pump])
    assert added == []


def test_unique_id_and_attributes() -> None:
    """Unique id is stable and attributes expose the raw register for debugging."""
    device = _device(components={"154": {"reportedValue": 100}})
    sensor = _sensor(device)
    assert sensor.unique_id == f"fluidra_{DEVICE_ID}_producing"
    attrs = sensor.extra_state_attributes
    assert attrs["component_id"] == PRODUCTION_COMPONENT
    assert attrs["raw_value"] == 100


def test_clear_connect_evo12_profile_exposes_production_register() -> None:
    """The CC25019224/CC25009932 profile maps the cell production state to c154 (Issue #109)."""
    config = DEVICE_CONFIGS["cc25019224_chlorinator"]
    assert config.features["cell_production_state"] == 154
    assert 154 in config.features["specific_components"]

    device = {
        "device_id": "CC25009932.nn_1",
        "name": "Chlorinator",
        "family": "Chlorinators",
        "type": "chlorinator",
        "model": "Chlorinator",
        "components": {"165": {"reportedValue": 731}},
    }
    assert DeviceIdentifier.identify_device(device) is config
    assert DeviceIdentifier.get_feature(device, "cell_production_state") == 154


# --- async_setup_entry — dynamic-devices wiring ------------------------------


def _pinned_chlorinator(device_id: str, *, production_component: int | None) -> dict:
    """Build a chlorinator with its identify cache pinned to a known feature set."""
    features = {} if production_component is None else {"cell_production_state": production_component}
    return {
        "device_id": device_id,
        "name": "Chlorinator",
        "family": "Chlorinators",
        "type": "chlorinator",
        "model": "Chlorinator",
        "online": True,
        "components": {"154": {"reportedValue": 0}},
        "_identify_cache": {
            "key": (device_id, "Chlorinators", "Chlorinator", "chlorinator", ""),
            "config": SimpleNamespace(
                device_type="chlorinator",
                features=features,
                components_range=25,
                required_components=[0, 1, 2, 3],
                entities=["sensor_info"],
            ),
        },
    }


async def _run_setup(devices: list[dict]) -> tuple[list[Any], list[Any], dict]:
    pool = {"id": POOL_ID, "name": "Pool", "devices": devices}
    coordinator = MagicMock()
    coordinator.data = {POOL_ID: pool}
    coordinator.last_update_success = True
    coordinator.api = SimpleNamespace(cached_pools=[pool], get_pools=AsyncMock(return_value=[pool]))
    coordinator.get_pools_from_data = lambda: [{"id": POOL_ID, **coordinator.data[POOL_ID]}]
    listeners: list[Any] = []
    coordinator.async_add_listener = lambda cb: listeners.append(cb) or (lambda: None)

    added: list[Any] = []
    entry = SimpleNamespace(
        runtime_data=SimpleNamespace(coordinator=coordinator),
        async_on_unload=lambda _unsub: None,
    )
    async_add = MagicMock(side_effect=lambda ents, *a, **k: added.extend(list(ents)))
    await async_setup_entry(MagicMock(), entry, async_add)
    return added, listeners, pool


async def test_setup_creates_producing_sensor_only_when_feature_present() -> None:
    """Only chlorinators that declare cell_production_state get a producing binary sensor."""
    with_feature = _pinned_chlorinator("dev1", production_component=154)
    without_feature = _pinned_chlorinator("dev2", production_component=None)
    added, listeners, _ = await _run_setup([with_feature, without_feature])

    uids = {e.unique_id for e in added}
    assert "fluidra_dev1_producing" in uids
    assert not any("dev2" in u for u in uids)
    assert listeners, "a coordinator update listener must be registered for dynamic devices"


async def test_setup_adds_new_device_dynamically() -> None:
    """dynamic-devices: a chlorinator appearing on a later poll is wired without a reload."""
    added, listeners, pool = await _run_setup([_pinned_chlorinator("dev1", production_component=154)])
    uids_after_setup = {e.unique_id for e in added}

    pool["devices"].append(_pinned_chlorinator("dev2", production_component=154))
    listeners[0]()

    new_uids = {e.unique_id for e in added} - uids_after_setup
    assert new_uids == {"fluidra_dev2_producing"}


async def test_setup_falls_back_to_get_pools_when_no_cache() -> None:
    """With no cached discovery, setup awaits get_pools() for the initial entities."""
    device = _pinned_chlorinator("dev1", production_component=154)
    pool = {"id": POOL_ID, "name": "Pool", "devices": [device]}
    coordinator = MagicMock()
    coordinator.data = {POOL_ID: pool}
    coordinator.last_update_success = True
    coordinator.api = SimpleNamespace(cached_pools=None, get_pools=AsyncMock(return_value=[pool]))
    coordinator.get_pools_from_data = lambda: [pool]
    coordinator.async_add_listener = lambda cb: lambda: None

    added: list[Any] = []
    entry = SimpleNamespace(
        runtime_data=SimpleNamespace(coordinator=coordinator),
        async_on_unload=lambda _unsub: None,
    )
    async_add = MagicMock(side_effect=lambda ents, *a, **k: added.extend(list(ents)))
    await async_setup_entry(MagicMock(), entry, async_add)

    coordinator.api.get_pools.assert_awaited_once()
    assert {e.unique_id for e in added} == {"fluidra_dev1_producing"}


@pytest.mark.parametrize("device_id", ["", None])
async def test_setup_skips_devices_without_id(device_id: Any) -> None:
    """Devices missing a device_id are skipped (no crash, no entity)."""
    device = _pinned_chlorinator("dev1", production_component=154)
    device["device_id"] = device_id
    added, _, _ = await _run_setup([device])
    assert added == []
