"""Issue #272 — a bridged Energy Connect chlorinator (thingType tecnoLC2).

The reporter's unit is published as ``CC24018113.nn_1`` behind its bridge, family
``Chlorinators``, thingType ``tecnoLC2``. Its registers, checked against the official
app: pH c165 (÷100), ORP c170 (mV), water temperature c172 (÷10), salinity c174
(÷100), production setpoint c10, pH setpoint c16 (÷100), ORP setpoint c20, boost c103.

The diagnostics showed ``_identify_cache.config`` as the generic ``chlorinator``
catch-all, which read like a mismatch: that cache holds the raw pattern match, and
the tecnoLC2 profile was only applied on top of it. The thingType now scores the
tecnoLC2 profile directly, and the dump names the profile actually in use.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from custom_components.fluidra_pool.binary_sensor import async_setup_entry as binary_sensor_setup
from custom_components.fluidra_pool.device_registry import DEVICE_CONFIGS, DeviceIdentifier
from custom_components.fluidra_pool.diagnostics import _redact_devices_data
from custom_components.fluidra_pool.fluidra_api import FluidraPoolAPI
from custom_components.fluidra_pool.number import async_setup_entry as number_setup
from custom_components.fluidra_pool.sensor import FluidraChlorinatorSensor
from custom_components.fluidra_pool.sensor import async_setup_entry as sensor_setup
from custom_components.fluidra_pool.switch import async_setup_entry as switch_setup

POOL_ID = "pool_272"
DEVICE_ID = "CC24018113.nn_1"

_REGISTERS = {
    0: 0,
    1: 178,
    2: 86,
    3: 7,
    10: 60,
    16: 740,
    20: 720,
    103: False,
    111: 0,
    118: 1,
    135: 0,
    165: 715,
    170: 698,
    172: 146,
    174: 442,
}
_ALARMS = [{"id": 11, "type": "error", "errorCode": "LOW TEMPERATURE", "value": True}]


def _tree() -> list[dict]:
    """The pool device tree as the cloud returns it: the chlorinator behind its bridge."""
    return [
        {
            "id": "CC24018113",
            "type": "connected",
            "info": {"name": "Energy Connect", "family": "Bridges"},
            "devices": [
                {
                    "id": DEVICE_ID,
                    "type": "connected",
                    "thingType": "tecnoLC2",
                    "bridgedInfo": {"protocol": "nn"},
                    "info": {"name": "Chlorinator", "family": "Chlorinators"},
                }
            ],
        }
    ]


async def _discovered_device() -> dict:
    api = FluidraPoolAPI("user@example.com", "secret")
    api._request = AsyncMock(return_value=(200, _tree(), ""))
    devices = await api._discover_devices_for_pool(POOL_ID, {})
    assert [d["device_id"] for d in devices] == [DEVICE_ID]
    return devices[0]


def _polled(device: dict) -> dict:
    device = copy.deepcopy(device)
    device["components"] = {str(k): {"reportedValue": v, "ts": 1} for k, v in _REGISTERS.items()}
    device["alarms"] = copy.deepcopy(_ALARMS)
    device["online"] = True
    return device


def _config_name(config) -> str | None:
    return next((name for name, value in DEVICE_CONFIGS.items() if value is config), None)


async def test_discovery_routes_the_bridged_unit_to_the_tecnolc2_profile():
    device = await _discovered_device()
    assert device["thing_type"] == "tecnoLC2"
    # Before any register is scanned, and on the cached raw match too.
    assert _config_name(DeviceIdentifier.identify_device(device)) == "tecnolc2_signature"
    assert _config_name(device["_identify_cache"]["config"]) == "tecnolc2_signature"
    assert DeviceIdentifier.identify_device(device).verified is True


async def test_profile_reads_and_writes_the_reported_registers():
    device = _polled(await _discovered_device())
    assert DeviceIdentifier.get_feature(device, "sensors") == {
        "ph": 165,
        "orp": 170,
        "temperature": 172,
        "salinity": 174,
    }
    assert DeviceIdentifier.get_feature(device, "chlorination_level") == 10
    assert DeviceIdentifier.get_feature(device, "ph_setpoint") == 16
    assert DeviceIdentifier.get_feature(device, "orp_setpoint") == 20
    assert DeviceIdentifier.get_feature(device, "boost_mode") == 103
    scanned = set(DeviceIdentifier.get_feature(device, "specific_components"))
    assert {10, 16, 20, 103, 165, 170, 172, 174} <= scanned


async def test_serial_specific_energy_connect_profile_still_wins():
    """Units with their own verified profile keep it (Issue #36 layout)."""
    device = {
        "device_id": "CC24054221.nn_1",
        "name": "Chlorinator",
        "type": "chlorinator",
        "family": "Chlorinators",
        "thing_type": "tecnoLC2",
        "components": {},
    }
    assert _config_name(DeviceIdentifier.identify_device(device)) == "cc_energy_connect_bridged_chlorinator"


async def _entities(device: dict) -> list:
    coordinator = MagicMock()
    pool = {"id": POOL_ID, "name": "Pool", "devices": [device]}
    coordinator.data = {POOL_ID: pool}
    coordinator.last_update_success = True
    coordinator.async_request_refresh = AsyncMock()
    coordinator.api = SimpleNamespace(cached_pools=[pool], get_pools=AsyncMock(return_value=[pool]))
    entry = SimpleNamespace(runtime_data=SimpleNamespace(coordinator=coordinator), async_on_unload=lambda _u: None)
    added: list = []
    for setup in (sensor_setup, number_setup, binary_sensor_setup, switch_setup):
        await setup(MagicMock(), entry, MagicMock(side_effect=lambda entities, *a, **k: added.extend(list(entities))))
    return added


async def test_entities_show_the_values_of_the_official_app():
    added = await _entities(_polled(await _discovered_device()))
    by_uid = {entity.unique_id: entity for entity in added}

    def value(suffix: str):
        entity = by_uid[f"fluidra_{DEVICE_ID}_{suffix}"]
        assert entity.available
        return entity.native_value if hasattr(type(entity), "native_value") else entity.is_on

    assert value("ph") == 7.15
    assert value("orp") == 698
    assert value("temperature") == 14.6
    assert value("salinity") == 4.42
    assert value("chlorination_level") == 60
    assert value("ph_setpoint") == 7.4
    assert value("orp_setpoint") == 720
    assert value("boost_mode") is False
    assert value("alarm") is True
    # The catch-all's free-chlorine register does not exist on this unit.
    assert not any(isinstance(e, FluidraChlorinatorSensor) and e.unique_id.endswith("_free_chlorine") for e in added)


async def test_diagnostics_name_the_profile_in_use():
    device = _polled(await _discovered_device())
    redacted = _redact_devices_data([device])[0]
    assert redacted["resolved_profile"] == "tecnolc2_signature"
    assert redacted["profile_verified"] is True
