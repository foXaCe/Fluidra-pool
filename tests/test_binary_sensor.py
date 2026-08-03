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
    assert "fluidra_dev2_producing" not in uids
    assert listeners, "a coordinator update listener must be registered for dynamic devices"


async def test_setup_creates_alarm_sensor_regardless_of_production_feature() -> None:
    """The alarm sensor lives in the raw status tree, not cell_production_state -- every
    chlorinator gets it, even ones that never report cell production (Issue #163 follow-up
    to CodeRabbit finding: the alarm sensor was previously nested inside the
    production_component guard and silently skipped for these devices)."""
    without_feature = _pinned_chlorinator("dev1", production_component=None)
    added, _, _ = await _run_setup([without_feature])

    uids = {e.unique_id for e in added}
    assert uids == {"fluidra_dev1_alarm"}


async def test_setup_adds_new_device_dynamically() -> None:
    """dynamic-devices: a chlorinator appearing on a later poll is wired without a reload."""
    added, listeners, pool = await _run_setup([_pinned_chlorinator("dev1", production_component=154)])
    uids_after_setup = {e.unique_id for e in added}

    pool["devices"].append(_pinned_chlorinator("dev2", production_component=154))
    listeners[0]()

    new_uids = {e.unique_id for e in added} - uids_after_setup
    # Each chlorinator gets both its producing sensor and its alarm sensor.
    assert new_uids == {"fluidra_dev2_producing", "fluidra_dev2_alarm"}


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
    assert {e.unique_id for e in added} == {"fluidra_dev1_producing", "fluidra_dev1_alarm"}


@pytest.mark.parametrize("device_id", ["", None])
async def test_setup_skips_devices_without_id(device_id: Any) -> None:
    """Devices missing a device_id are skipped (no crash, no entity)."""
    device = _pinned_chlorinator("dev1", production_component=154)
    device["device_id"] = device_id
    added, _, _ = await _run_setup([device])
    assert added == []


# --- Victoria VS speed-preset digital-input binary sensors (Issue #144) ---

from custom_components.fluidra_pool.binary_sensor import FluidraPumpSpeedInputBinarySensor  # noqa: E402


def _input_sensor(device: dict, tier: str) -> FluidraPumpSpeedInputBinarySensor:
    return FluidraPumpSpeedInputBinarySensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID, tier)


def test_speed_input_on_when_flag_true() -> None:
    """The dry-contact input reads on when the coordinator flag is set."""
    device = _device(pump_speed_input_low=True)
    sensor = _input_sensor(device, "low")
    assert sensor.is_on is True
    assert sensor.unique_id == f"{DOMAIN}_{POOL_ID}_{DEVICE_ID}_speed_input_low"


def test_speed_input_off_when_flag_false() -> None:
    device = _device(pump_speed_input_medium=False)
    assert _input_sensor(device, "medium").is_on is False


def test_speed_input_none_when_flag_absent() -> None:
    """Before the register is scanned there's no flag → unknown, not off."""
    device = _device()
    assert _input_sensor(device, "high").is_on is None


async def test_setup_creates_speed_input_binary_sensors_for_victoria() -> None:
    """A Victoria pump (speed_input_components feature) gets three input sensors."""
    device = {
        "device_id": DEVICE_ID,
        "name": "Victoria Smart Connect VS",
        "family": "Filtration Pumps",
        "model": "Victoria Smart Connect VS",
        "type": "pump",
        "online": True,
        "components": {},
    }
    coordinator = MagicMock()
    pool = {"id": POOL_ID, "name": "Pool", "devices": [device]}
    coordinator.data = {POOL_ID: pool}
    coordinator.last_update_success = True
    coordinator.api = SimpleNamespace(cached_pools=[pool], get_pools=AsyncMock(return_value=[pool]))
    added: list[Any] = []
    entry = SimpleNamespace(
        runtime_data=SimpleNamespace(coordinator=coordinator),
        async_on_unload=lambda _u: None,
    )
    await async_setup_entry(MagicMock(), entry, MagicMock(side_effect=lambda e, *a, **k: added.extend(list(e))))
    input_sensors = [e for e in added if isinstance(e, FluidraPumpSpeedInputBinarySensor)]
    assert {s._tier for s in input_sensors} == {"low", "medium", "high"}


# --- Z260iQ-family fault sensors (Issue #139) ----------------------------

from custom_components.fluidra_pool.binary_sensor import FluidraHeatPumpAlarmBinarySensor  # noqa: E402


def _alarm(device: dict) -> FluidraHeatPumpAlarmBinarySensor:
    return FluidraHeatPumpAlarmBinarySensor(
        _coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID, "air_temperature_alarm"
    )


def test_heat_pump_alarm_on_when_faulted() -> None:
    assert _alarm(_device(air_temperature_alarm=True)).is_on is True


def test_heat_pump_alarm_off_when_clear() -> None:
    assert _alarm(_device(air_temperature_alarm=False)).is_on is False


def test_heat_pump_alarm_unknown_before_reported() -> None:
    """Not yet polled → unknown rather than a reassuring but unfounded 'no fault'."""
    assert _alarm(_device()).is_on is None


def test_heat_pump_alarm_unique_id() -> None:
    assert _alarm(_device()).unique_id == f"{DOMAIN}_{POOL_ID}_{DEVICE_ID}_air_temperature_alarm"


# --- Chlorinator alarm offline guard (Issue #163) -------------------------

from custom_components.fluidra_pool.binary_sensor import FluidraChlorinatorAlarmBinarySensor  # noqa: E402

PH_ALARM = {
    "errorCode": "PUMPSTOP_PH",
    "default": {"title": "pH dosing stop", "text": "pH pump stopped dosing"},
    "value": True,
}


def _chlorinator_alarm(device: dict) -> FluidraChlorinatorAlarmBinarySensor:
    return FluidraChlorinatorAlarmBinarySensor(_coord([device]), POOL_ID, DEVICE_ID)


def test_chlorinator_alarm_is_on_none_when_device_offline_with_cached_alarm() -> None:
    """Fluidra's cloud can keep serving a cached alarms[] snapshot for a
    disconnected device (confirmed 2026-07-20: PUMPSTOP PH stayed reported
    True for 8+ hours after the chlorinator was physically powered off,
    alongside a frozen ORP reading) -- online=False must make is_on
    unknown rather than trust that stale True.
    """
    sensor = _chlorinator_alarm(_device(online=False, alarms=[PH_ALARM]))
    assert sensor.is_on is None


def test_chlorinator_alarm_last_known_state_freezes_when_offline() -> None:
    """last_known_state should mirror the last *trustworthy* poll and stay
    frozen once the device goes offline, rather than being cleared or
    reflecting the (untrustworthy) cached alarms[] content.
    """
    online_device = _device(online=True, alarms=[PH_ALARM])
    sensor = _chlorinator_alarm(online_device)

    # A trustworthy poll while online confirms the alarm is active.
    sensor._update_last_known()
    assert sensor._last_known_state is True
    frozen_at = sensor._last_known_at
    assert frozen_at is not None

    # Device goes offline; cloud keeps echoing the same cached alarms[].
    # A further update must NOT treat this poll as trustworthy.
    sensor.coordinator.data[POOL_ID]["devices"][0]["online"] = False
    sensor._update_last_known()

    assert sensor._last_known_state is True
    assert sensor._last_known_at == frozen_at
    assert sensor.is_on is None


def test_chlorinator_alarm_on_with_active_alarm() -> None:
    """A trustworthy poll carrying an active alarm reads on."""
    sensor = _chlorinator_alarm(_device(online=True, alarms=[PH_ALARM]))
    assert sensor.is_on is True
    attrs = sensor.extra_state_attributes
    assert attrs["error_code"] == "PUMPSTOP_PH"
    assert attrs["title"] == "pH dosing stop"
    assert attrs["active_alarm_count"] == 1
    assert attrs["device_offline"] is False


def test_chlorinator_alarm_off_without_active_alarms() -> None:
    """Entries whose value is falsy don't count as active."""
    cleared = {**PH_ALARM, "value": False}
    sensor = _chlorinator_alarm(_device(online=True, alarms=[cleared]))
    assert sensor.is_on is False
    assert sensor.extra_state_attributes["active_alarm_count"] == 0
    assert sensor.extra_state_attributes["active_alarms"] == []
    assert "error_code" not in sensor.extra_state_attributes


def test_chlorinator_alarm_counts_multiple_and_surfaces_the_first() -> None:
    """With several active alarms the count reflects all, details show the first."""
    second = {"errorCode": "SALT_LOW", "default": {"title": "Low salt", "text": "Add salt"}, "value": True}
    sensor = _chlorinator_alarm(_device(online=True, alarms=[PH_ALARM, second]))
    attrs = sensor.extra_state_attributes
    assert attrs["active_alarm_count"] == 2
    assert attrs["error_code"] == "PUMPSTOP_PH"
    assert attrs["title"] == "pH dosing stop"
    assert attrs["text"] == "pH pump stopped dosing"
    assert attrs["active_alarms"] == [
        {"error_code": "PUMPSTOP_PH", "title": "pH dosing stop", "text": "pH pump stopped dosing"},
        {"error_code": "SALT_LOW", "title": "Low salt", "text": "Add salt"},
    ]


def test_chlorinator_alarm_active_alarms_covers_unlisted_error_codes() -> None:
    """active_alarms doesn't assume a closed set of alarm types."""
    third = {"errorCode": "SALT_HIGH", "default": {"title": "High salt", "text": "Salt too high"}, "value": True}
    sensor = _chlorinator_alarm(_device(online=True, alarms=[PH_ALARM, third]))
    attrs = sensor.extra_state_attributes
    assert attrs["active_alarm_count"] == 2
    assert attrs["active_alarms"] == [
        {"error_code": "PUMPSTOP_PH", "title": "pH dosing stop", "text": "pH pump stopped dosing"},
        {"error_code": "SALT_HIGH", "title": "High salt", "text": "Salt too high"},
    ]


def test_chlorinator_alarm_active_alarms_excludes_malformed_entries() -> None:
    """Junk mixed in among valid alarms is excluded from active_alarms too."""
    second = {"errorCode": "SALT_LOW", "default": {"title": "Low salt", "text": "Add salt"}, "value": True}
    sensor = _chlorinator_alarm(_device(online=True, alarms=["junk", None, 42, {"no_value": 1}, PH_ALARM, second]))
    attrs = sensor.extra_state_attributes
    assert attrs["active_alarm_count"] == 2
    assert attrs["active_alarms"] == [
        {"error_code": "PUMPSTOP_PH", "title": "pH dosing stop", "text": "pH pump stopped dosing"},
        {"error_code": "SALT_LOW", "title": "Low salt", "text": "Add salt"},
    ]


def test_chlorinator_alarm_active_alarms_excludes_malformed_default() -> None:
    """An active alarm with a non-dict `default` is excluded, not raised."""
    valid = {"errorCode": "SALT_LOW", "default": {"title": "Low salt", "text": "Add salt"}, "value": True}
    malformed = {"errorCode": "WEIRD", "default": "invalid", "value": True}
    sensor = _chlorinator_alarm(_device(online=True, alarms=[valid, malformed]))
    attrs = sensor.extra_state_attributes
    assert attrs["active_alarm_count"] == 1
    assert attrs["active_alarms"] == [
        {"error_code": "SALT_LOW", "title": "Low salt", "text": "Add salt"},
    ]


def test_chlorinator_alarm_active_alarms_treats_none_default_as_missing() -> None:
    """An explicit `default: None` is treated the same as a missing `default` key --
    intentional: it doesn't crash, it just yields title/text as None, same as any
    other alarm with partial data. Rejecting it would silently drop a real active
    alarm just because detail fields are absent (see PR discussion)."""
    valid = {"errorCode": "SALT_LOW", "default": {"title": "Low salt", "text": "Add salt"}, "value": True}
    none_default = {"errorCode": "UNKNOWN_CODE", "default": None, "value": True}
    sensor = _chlorinator_alarm(_device(online=True, alarms=[valid, none_default]))
    attrs = sensor.extra_state_attributes
    assert attrs["active_alarm_count"] == 2
    assert attrs["active_alarms"] == [
        {"error_code": "SALT_LOW", "title": "Low salt", "text": "Add salt"},
        {"error_code": "UNKNOWN_CODE", "title": None, "text": None},
    ]


def test_chlorinator_alarm_ignores_malformed_entries() -> None:
    """Junk in alarms[] is skipped rather than crashing or counting."""
    sensor = _chlorinator_alarm(_device(online=True, alarms=["junk", None, 42, {"no_value": 1}, PH_ALARM]))
    assert sensor.is_on is True
    assert sensor.extra_state_attributes["active_alarm_count"] == 1


def test_chlorinator_alarm_handles_missing_alarms_key() -> None:
    """A device the cloud never sent alarms for reads off, not unknown."""
    sensor = _chlorinator_alarm(_device(online=True))
    assert sensor.is_on is False


def test_chlorinator_alarm_unknown_when_coordinator_failed() -> None:
    """A failed poll can't be trusted either, even with the device online."""
    device = _device(online=True, alarms=[PH_ALARM])
    coord = _coord([device])
    coord.last_update_success = False
    sensor = FluidraChlorinatorAlarmBinarySensor(coord, POOL_ID, DEVICE_ID)
    assert sensor.is_on is None


def test_chlorinator_alarm_exposes_last_known_while_offline() -> None:
    """Offline: is_on is unknown, but the last confirmed state stays visible.

    Without this a dashboard can only show a bare "unknown"; with it, it can say
    "last known: alarm active, confirmed at <time>".
    """
    device = _device(online=True, alarms=[PH_ALARM])
    sensor = _chlorinator_alarm(device)
    sensor._update_last_known()  # a trustworthy poll happened

    device["online"] = False
    assert sensor.is_on is None  # refuses to trust the cached snapshot
    attrs = sensor.extra_state_attributes
    assert attrs["device_offline"] is True
    assert attrs["last_known_state"] is True
    assert attrs["last_known_at"] is not None


def test_chlorinator_alarm_last_known_absent_before_any_trustworthy_poll() -> None:
    """Never confirmed yet → the attributes exist but stay empty, not fabricated."""
    attrs = _chlorinator_alarm(_device(online=False, alarms=[PH_ALARM])).extra_state_attributes
    assert attrs["last_known_state"] is None
    assert attrs["last_known_at"] is None


def test_no_flow_alarm_sensor_exposes_c28_state() -> None:
    """E03 no-flow (c28) gets its own entity, not just a climate attribute (Issue #139)."""
    sensor = FluidraHeatPumpAlarmBinarySensor(
        _coord([_device(no_flow_alarm=True)]), SimpleNamespace(), POOL_ID, DEVICE_ID, "no_flow_alarm"
    )
    assert sensor.is_on is True
    assert sensor.unique_id == f"{DOMAIN}_{POOL_ID}_{DEVICE_ID}_no_flow_alarm"


async def test_setup_creates_both_heat_pump_alarm_sensors() -> None:
    """A Z260iQ-family unit gets both the air-temperature and no-flow fault sensors."""
    from types import SimpleNamespace as SN

    device = {
        "device_id": "HP-1",
        "name": "Z250iQ",
        "family": "Heat Pumps",
        "type": "heat_pump",
        "model": "Z250iQ",
        "online": True,
        "components": {},
        "_identify_cache": {
            "key": ("HP-1", "Heat Pumps", "Z250iQ", "heat_pump", ""),
            "config": SN(
                device_type="heat_pump",
                features={"z260iq_mode": True},
                components_range=25,
                required_components=[0, 1, 2, 3],
                entities=[],
            ),
        },
    }
    coordinator = MagicMock()
    pool = {"id": POOL_ID, "name": "Pool", "devices": [device]}
    coordinator.data = {POOL_ID: pool}
    coordinator.last_update_success = True
    coordinator.api = SimpleNamespace(cached_pools=[pool], get_pools=AsyncMock(return_value=[pool]))
    added: list[Any] = []
    entry = SimpleNamespace(
        runtime_data=SimpleNamespace(coordinator=coordinator),
        async_on_unload=lambda _u: None,
    )
    await async_setup_entry(MagicMock(), entry, MagicMock(side_effect=lambda e, *a, **k: added.extend(list(e))))
    keys = {e._alarm_key for e in added if isinstance(e, FluidraHeatPumpAlarmBinarySensor)}
    assert keys == {"air_temperature_alarm", "no_flow_alarm"}
