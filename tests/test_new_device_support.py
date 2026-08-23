"""New device support: Zodiac Freedom Lite robot, Command Connect cabinet and
heat-pump activity sensor (Issues #210, #211, #212)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.fluidra_pool.device_registry import DeviceIdentifier
from custom_components.fluidra_pool.device_registry.configs import DEVICE_CONFIGS
from custom_components.fluidra_pool.fluidra_api._helpers import classify_device_type
from custom_components.fluidra_pool.sensor import (
    FluidraDeviceBatterySensor,
    FluidraHeatPumpActivitySensor,
    FluidraScheduleDaysSensor,
)
from custom_components.fluidra_pool.switch import FluidraChlorinatorToggleSwitch
from tests.test_sensor_full import _run_setup  # noqa: F401  (reused setup helper)

POOL_ID = "pool-1"


def _robot_device() -> dict[str, Any]:
    """Freedom Lite as the coordinator reports it (Issue #212 dump)."""
    return {
        "device_id": "NLX61690448",
        "name": "Zodiac Freedom Lite",
        "family": "Robots",
        "model": "Zodiac Freedom Lite",
        "type": "robot",
        "components": {
            "25": {"reportedValue": ["saturday", "wednesday"]},
            "26": {"reportedValue": 79},
        },
    }


def _cabinet_device() -> dict[str, Any]:
    """Command Connect cabinet as mapped by @efgonzalez (Issue #210)."""
    return {
        "device_id": "QR24xxxx.ndsr_1",
        "name": "Command Connect",
        "family": "Cabinets",
        "model": "Command Connect",
        "type": "cabinet",
        "components": {
            "13": {"reportedValue": False},
            "15": {"reportedValue": True},
            "24": {"reportedValue": True},
            "26": {"reportedValue": False},
        },
    }


def _coordinator(device: dict[str, Any]) -> Any:
    coordinator = MagicMock()
    coordinator.data = {POOL_ID: {"id": POOL_ID, "name": "Pool", "devices": [device]}}
    coordinator.async_request_refresh = AsyncMock()
    coordinator.last_update_success = True
    return coordinator


# --------------------------------------------------------------------------- #
# Classification                                                               #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("family", "name", "expected"),
    [
        ("Cabinets", "Command Connect", "cabinet"),
        ("Bridges", "iQBridgeZB", "unknown"),
        ("Robots", "Freedom Lite", "robot"),
        ("Cleaners", "Whatever", "robot"),
        ("Something", "My pool cleaner", "robot"),
        ("Pumps", "VS Pump", "pump"),
    ],
)
def test_classify_new_families(family: str, name: str, expected: str) -> None:
    assert classify_device_type(family, name) == expected


# --------------------------------------------------------------------------- #
# Freedom Lite robot profile (Issue #212)                                      #
# --------------------------------------------------------------------------- #


class TestFreedomLiteProfile:
    def test_identifies_by_serial_and_model(self) -> None:
        device = _robot_device()
        config = DeviceIdentifier.identify_device(device)
        assert config is DEVICE_CONFIGS["freedom_lite_robot"]
        assert config.device_type == "robot"
        assert config.verified is True

    def test_declared_registers_are_scanned(self) -> None:
        features = DEVICE_CONFIGS["freedom_lite_robot"].features
        for component in (25, 26):
            assert component in features["specific_components"]
        assert features["battery_component"] == 26
        assert features["schedule_days_component"] == 25

    def test_entities_declared(self) -> None:
        entities = DEVICE_CONFIGS["freedom_lite_robot"].entities
        assert "sensor_battery" in entities
        assert "sensor_schedule_days" in entities


class TestRobotSensors:
    def test_battery_reads_percent_register(self) -> None:
        device = _robot_device()
        sensor = FluidraDeviceBatterySensor(_coordinator(device), None, POOL_ID, device["device_id"])
        assert sensor.native_value == 79
        assert sensor.native_unit_of_measurement == "%"

    def test_battery_handles_missing_and_garbage(self) -> None:
        device = _robot_device()
        device["components"] = {}
        sensor = FluidraDeviceBatterySensor(_coordinator(device), None, POOL_ID, device["device_id"])
        assert sensor.native_value is None
        device["components"] = {"26": {"reportedValue": "not-a-number"}}
        assert sensor.native_value is None
        # float("inf") survives float() but raises OverflowError on round().
        device["components"] = {"26": {"reportedValue": float("inf")}}
        assert sensor.native_value is None

    def test_schedule_days_sorted_in_week_order(self) -> None:
        device = _robot_device()
        sensor = FluidraScheduleDaysSensor(_coordinator(device), None, POOL_ID, device["device_id"])
        assert sensor.native_value == "wednesday, saturday"

    def test_schedule_days_empty_and_unknown(self) -> None:
        device = _robot_device()
        sensor = FluidraScheduleDaysSensor(_coordinator(device), None, POOL_ID, device["device_id"])
        device["components"]["25"]["reportedValue"] = []
        assert sensor.native_value == ""
        device["components"]["25"]["reportedValue"] = ["monday", "journee"]
        value = sensor.native_value
        assert isinstance(value, str)
        assert value.startswith("monday")
        assert "journee" in value
        device["components"]["25"]["reportedValue"] = "bogus"
        assert sensor.native_value is None


# --------------------------------------------------------------------------- #
# Command Connect cabinet (Issue #210)                                         #
# --------------------------------------------------------------------------- #


class TestCabinetProfile:
    def test_identifies_by_family(self) -> None:
        device = _cabinet_device()
        config = DeviceIdentifier.identify_device(device)
        assert config is DEVICE_CONFIGS["command_connect_cabinet"]
        assert config.device_type == "cabinet"
        assert config.verified is True

    def test_boolean_writes_feature_declared(self) -> None:
        features = DEVICE_CONFIGS["command_connect_cabinet"].features
        assert features["boolean_writes"] is True
        for component in (13, 15, 24, 26):
            assert component in features["specific_components"]

    def test_four_toggles_declared(self) -> None:
        toggles = DEVICE_CONFIGS["command_connect_cabinet"].features["toggle_switches"]
        keys = [feature for feature, _key, _icon in toggles]
        assert sorted(keys) == [
            "cabinet_lights",
            "cabinet_lights_auto_mode",
            "cabinet_pump",
            "cabinet_pump_auto_mode",
        ]

    def test_toggle_writes_booleans_not_integers(self) -> None:
        """The cabinet silently ignores integer writes — the payload must carry
        a JSON true/false, never 1/0 (Issue #210 measured behaviour)."""
        device = _cabinet_device()
        api = SimpleNamespace(control_device_component=AsyncMock(return_value=True))
        switch = FluidraChlorinatorToggleSwitch(
            _coordinator(device), api, POOL_ID, device["device_id"], "cabinet_pump", "cabinet_pump", "mdi:pump"
        )
        switch.hass = MagicMock()
        switch.async_write_ha_state = MagicMock()

        assert switch._component() == 13
        assert switch.is_on is False

        with patch("custom_components.fluidra_pool.switch.chlorinator.asyncio.sleep", new=AsyncMock()):
            asyncio.run(switch.async_turn_on())
        args = api.control_device_component.call_args.args
        assert args[1] == 13
        assert args[2] is True  # boolean, not 1


# --------------------------------------------------------------------------- #
# Heat pump activity sensor (Issue #211)                                       #
# --------------------------------------------------------------------------- #


def _heat_pump_device(components: dict[int, Any], **extra: Any) -> dict[str, Any]:
    device: dict[str, Any] = {
        "device_id": "LF000000",
        "name": "Z250iQ",
        "family": "heat pump",
        "type": "heat_pump",
        "components": {str(k): {"reportedValue": v} for k, v in components.items()},
    }
    device.update(extra)
    device["_identify_cache"] = {
        "key": (device["device_id"], device["family"], "", "heat_pump", ""),
        "config": SimpleNamespace(
            device_type="heat_pump",
            features={"z260iq_mode": True},
            entities=["climate", "switch", "sensor_info", "sensor_activity"],
            components_range=5,
            required_components=[0, 1, 2, 3],
        ),
    }
    return device


class TestHeatPumpActivitySensor:
    def _sensor(self, device: dict[str, Any]) -> FluidraHeatPumpActivitySensor:
        return FluidraHeatPumpActivitySensor(_coordinator(device), None, POOL_ID, device["device_id"])

    def test_options_cover_the_enum(self) -> None:
        # Read off an instance: HA's cached-properties machinery wraps the
        # class-level `_attr_options` assignment.
        sensor = self._sensor(_heat_pump_device({}))
        assert sensor._attr_options == ["off", "idle", "heating", "cooling"]

    def test_z260iq_off_when_unit_off(self) -> None:
        # heat_pump_reported=False → OFF branch of the Z260iq behavior.
        sensor = self._sensor(
            _heat_pump_device({13: 0}, heat_pump_reported=False, water_temperature=20.0, target_temperature=28.0)
        )
        assert sensor.native_value == "off"

    def test_z260iq_idle_inside_deadband(self) -> None:
        # ON in Smart Heat+Cool (mode 2) with the setpoint satisfied: the same
        # ±2 °C deadband inference as the climate entity reports IDLE.
        sensor = self._sensor(
            _heat_pump_device(
                {13: 1, 14: 2},
                heat_pump_reported=True,
                z260iq_mode_value=2,
                water_temperature=27.5,
                target_temperature=28.0,
            )
        )
        assert sensor.native_value == "idle"

    def test_smart_heat_cool_infers_heating_and_cooling(self) -> None:
        heating = self._sensor(
            _heat_pump_device(
                {13: 1, 14: 2},
                heat_pump_reported=True,
                z260iq_mode_value=2,
                water_temperature=24.0,
                target_temperature=28.0,
            )
        )
        assert heating.native_value == "heating"
        cooling = self._sensor(
            _heat_pump_device(
                {13: 1, 14: 2},
                heat_pump_reported=True,
                z260iq_mode_value=2,
                # Strictly outside the ±2 °C deadband (30-28 would sit inside it).
                water_temperature=30.5,
                target_temperature=28.0,
            )
        )
        assert cooling.native_value == "cooling"

    def test_compressor_state_wins_over_mode(self) -> None:
        # c75 decoded by the coordinator beats c14: a unit set to Smart Heating
        # whose compressor idles must report idle, not heating (Issue #139).
        sensor = self._sensor(
            _heat_pump_device(
                {13: 1, 14: 0, 75: 0},
                heat_pump_reported=True,
                z260iq_mode_value=0,
                compressor_state=0,
            )
        )
        assert sensor.native_value == "idle"


# --------------------------------------------------------------------------- #
# Platform wiring                                                              #
# --------------------------------------------------------------------------- #


def _setup_coordinator(devices: list[dict[str, Any]]) -> Any:
    """Coordinator whose .data + .api.cached_pools are both populated for setup."""
    coordinator = MagicMock()
    pool = {"id": POOL_ID, "name": "Pool", "devices": devices}
    coordinator.data = {POOL_ID: pool}
    coordinator.last_update_success = True
    coordinator.api = SimpleNamespace(
        cached_pools=[pool],
        get_pools=AsyncMock(return_value=[pool]),
    )
    return coordinator


async def _run_platform_setup(platform: Any, devices: list[dict[str, Any]]) -> list[Any]:
    entry = SimpleNamespace(
        runtime_data=SimpleNamespace(coordinator=_setup_coordinator(devices)),
        async_on_unload=lambda _unsub: None,
    )
    added: list[Any] = []

    def _add(entities, *a, **k):
        added.extend(list(entities))

    async_add = MagicMock(side_effect=_add)
    await platform.async_setup_entry(MagicMock(), entry, async_add)
    return added


async def test_cabinet_setup_creates_four_switches() -> None:
    from custom_components.fluidra_pool.switch import async_setup_entry as switch_setup

    device = _cabinet_device()
    entities = await _run_platform_setup(SimpleNamespace(async_setup_entry=switch_setup), [device])
    keys = sorted(e._attr_translation_key for e in entities)
    assert keys == [
        "cabinet_lights",
        "cabinet_lights_auto_mode",
        "cabinet_pump",
        "cabinet_pump_auto_mode",
    ]


async def test_robot_setup_creates_battery_and_schedule_days() -> None:
    from custom_components.fluidra_pool.sensor import async_setup_entry as sensor_setup

    device = _robot_device()
    entities = await _run_platform_setup(SimpleNamespace(async_setup_entry=sensor_setup), [device])
    keys = sorted(e._attr_translation_key for e in entities)
    # The pool-level sensors (weather/status/location/water quality) come with
    # every setup; the robot must contribute exactly its two.
    assert [k for k in keys if k in ("battery_level", "schedule_days")] == ["battery_level", "schedule_days"]


async def test_heat_pump_setup_creates_activity_sensor() -> None:
    from custom_components.fluidra_pool.sensor import async_setup_entry as sensor_setup

    device = _heat_pump_device({}, heat_pump_reported=False)
    entities = await _run_platform_setup(SimpleNamespace(async_setup_entry=sensor_setup), [device])
    activities = [e for e in entities if isinstance(e, FluidraHeatPumpActivitySensor)]
    assert len(activities) == 1
    assert activities[0].native_value == "off"
