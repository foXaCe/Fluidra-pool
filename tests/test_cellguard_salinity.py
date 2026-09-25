"""CellGuard salinity validity and history through real HA entity lifecycles."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import logging
from typing import Any
from unittest.mock import AsyncMock

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers.entity_component import EntityComponent
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import mock_restore_cache_with_extra_data

from custom_components.fluidra_pool.sensor.salinity import (
    FluidraCellGuardSalinitySensor,
    FluidraSalinityStatusSensor,
)

_LOGGER = logging.getLogger(__name__)
POOL_ID = "pool-cellguard-test"
DEVICE_ID = "DM24086706.TEST"
SENSOR_ID = "sensor.cellguard_test_salinity"
STATUS_ID = "sensor.cellguard_test_salinity_status"
T0 = datetime(2026, 9, 25, 8, 0, tzinfo=UTC)
TS0 = int(T0.timestamp())
_UNCHANGED = object()
FLOW_ALARM = {"errorCode": "FLOW", "type": "error", "value": True}


def _device(production: Any = 90, salinity: Any = 180, timestamp: Any = TS0) -> dict[str, Any]:
    """Synthetic device: high targets deliberately differ from actual production."""
    return {
        "device_id": DEVICE_ID,
        "name": "CellGuard test",
        "family": "Chlorinators",
        "type": "chlorinator",
        "model": "Chlorinator",
        "online": True,
        "components": {
            "4": {"reportedValue": 90, "desiredValue": 90},
            "164": {"reportedValue": production, "ts": TS0},
            "185": {"reportedValue": salinity, "ts": timestamp},
            "263": {"reportedValue": 90, "desiredValue": 90},
        },
        "alarms": [],
    }


@dataclass
class SalinityHarness:
    """Deliver cloud snapshots through the HA coordinator, without API writes."""

    hass: HomeAssistant
    component: EntityComponent[SensorEntity]
    coordinator: DataUpdateCoordinator
    api: AsyncMock
    device: dict[str, Any]
    sensor: FluidraCellGuardSalinitySensor
    status: FluidraSalinityStatusSensor

    async def publish(
        self,
        *,
        production: Any = _UNCHANGED,
        salinity: Any = _UNCHANGED,
        salinity_ts: Any = _UNCHANGED,
        production_ts: Any = _UNCHANGED,
        online: Any = _UNCHANGED,
        alarms: Any = _UNCHANGED,
    ) -> None:
        """Publish one complete cloud response using the real HA listener path."""
        components = self.device["components"]
        for component_id, value in (("164", production), ("185", salinity)):
            if value is not _UNCHANGED:
                components[component_id]["reportedValue"] = value
        for component_id, timestamp in (("164", production_ts), ("185", salinity_ts)):
            if timestamp is not _UNCHANGED:
                if timestamp is None:
                    components[component_id].pop("ts", None)
                else:
                    components[component_id]["ts"] = timestamp
        if online is not _UNCHANGED:
            self.device["online"] = online
        if alarms is not _UNCHANGED:
            self.device["alarms"] = alarms
        self.coordinator.async_set_updated_data(
            {POOL_ID: {"id": POOL_ID, "name": "Test pool", "devices": [deepcopy(self.device)]}}
        )
        await self.hass.async_block_till_done()


@pytest.fixture
async def make_sensor(hass: HomeAssistant) -> Callable[..., Awaitable[SalinityHarness]]:
    """Install both entities as HA sensors, including restore and update hooks."""
    component: EntityComponent[SensorEntity] = EntityComponent(_LOGGER, "sensor", hass)

    async def create(
        device: dict[str, Any] | None = None,
        restored: tuple[State, dict[str, Any]] | None = None,
        *,
        numeric_enabled: bool = True,
    ) -> SalinityHarness:
        if restored is not None:
            mock_restore_cache_with_extra_data(hass, [restored])
        device = deepcopy(device if device is not None else _device())
        coordinator = DataUpdateCoordinator(hass, _LOGGER, name="CellGuard test", config_entry=None)
        coordinator.pool_device_ids = {}  # type: ignore[attr-defined]
        coordinator.async_set_updated_data(
            {POOL_ID: {"id": POOL_ID, "name": "Test pool", "devices": [deepcopy(device)]}}
        )
        api = AsyncMock()
        sensor = FluidraCellGuardSalinitySensor(coordinator, api, POOL_ID, DEVICE_ID, 185)
        status = FluidraSalinityStatusSensor(sensor)
        sensor.entity_id = SENSOR_ID
        status.entity_id = STATUS_ID
        await component.async_add_entities([sensor, status] if numeric_enabled else [status])
        await hass.async_block_till_done()
        return SalinityHarness(hass, component, coordinator, api, device, sensor, status)

    return create


def _assert_state(harness: SalinityHarness, expected: float | None, status: str) -> dict[str, Any]:
    """Check both Python-facing values and the HA state-machine result."""
    assert harness.sensor.native_value == expected
    assert harness.status.native_value == status
    state = harness.hass.states.get(SENSOR_ID)
    status_state = harness.hass.states.get(STATUS_ID)
    assert state is not None
    assert status_state is not None
    assert state.state == (str(expected) if expected is not None else "unknown")
    assert status_state.state == status
    attributes = harness.sensor.extra_state_attributes
    assert attributes["measurement_status"] == status
    assert attributes["is_current_reading"] is (status == "valid")
    return attributes


def _assert_history(attributes: dict[str, Any], value: float = 1.8, when: datetime = T0) -> None:
    assert attributes["last_known_value"] == value
    assert dt_util.parse_datetime(attributes["last_known_at"]) == when


async def test_existing_salinity_identity_and_status_enum(make_sensor: Any) -> None:
    """The new validity companion must not replace the existing salinity entity."""
    harness = await make_sensor()
    assert harness.sensor.unique_id == f"fluidra_{DEVICE_ID}_salinity"
    assert harness.sensor.native_unit_of_measurement == "g/L"
    assert harness.status.device_class == SensorDeviceClass.ENUM
    assert set(harness.status.options) == {
        "valid",
        "not_producing",
        "low_production",
        "production_unknown",
        "awaiting_reading",
        "no_flow",
        "offline",
    }
    _assert_history(_assert_state(harness, 1.8, "valid"))
    assert not harness.api.mock_calls


@pytest.mark.parametrize(("production", "status"), [(0, "not_producing"), (29, "low_production")])
async def test_high_target_does_not_validate_cached_salt(make_sensor: Any, production: int, status: str) -> None:
    """Targets c4/c263=90 cannot make a cached positive salt value current."""
    harness = await make_sensor(_device(production=production))
    attributes = _assert_state(harness, None, status)
    assert attributes.get("last_known_value") is None
    assert attributes.get("last_known_at") is None


@pytest.mark.parametrize("production", [30, 90, 100])
async def test_actual_production_at_or_above_thirty_accepts_reading(make_sensor: Any, production: int) -> None:
    harness = await make_sensor(_device(production=production))
    _assert_history(_assert_state(harness, 1.8, "valid"))


async def test_held_reading_keeps_original_cloud_time_after_production_stops(make_sensor: Any) -> None:
    harness = await make_sensor()
    await harness.publish(production=0, production_ts=TS0 + 60, salinity=0, salinity_ts=TS0 + 60)
    _assert_history(_assert_state(harness, 1.8, "not_producing"))
    await harness.publish(production=29, production_ts=TS0 + 120, salinity=185, salinity_ts=TS0 + 120)
    _assert_history(_assert_state(harness, 1.8, "low_production"))


async def test_zero_while_producing_waits_for_measurement(make_sensor: Any) -> None:
    harness = await make_sensor(_device(salinity=0))
    attributes = _assert_state(harness, None, "awaiting_reading")
    assert attributes.get("last_known_value") is None


@pytest.mark.parametrize("production", [None, True, False, "bad", float("nan"), float("inf"), -1, 101])
async def test_unusable_actual_production_cannot_be_replaced_by_targets(make_sensor: Any, production: Any) -> None:
    harness = await make_sensor(_device(production=production))
    attributes = _assert_state(harness, None, "production_unknown")
    assert attributes.get("last_known_at") is None


async def test_missing_actual_production_register_does_not_fall_back_to_a_target(make_sensor: Any) -> None:
    device = _device()
    del device["components"]["164"]
    harness = await make_sensor(device)
    _assert_state(harness, None, "production_unknown")


@pytest.mark.parametrize("salinity", [None, True, False, "bad", float("nan"), float("inf"), -180])
async def test_malformed_salt_does_not_replace_history(make_sensor: Any, salinity: Any) -> None:
    harness = await make_sensor()
    await harness.publish(salinity=salinity, salinity_ts=TS0 + 60)
    _assert_history(_assert_state(harness, 1.8, "awaiting_reading"))


@pytest.mark.parametrize("timestamp", [None, True, "bad", float("nan"), float("inf"), -1])
async def test_unusable_cloud_time_does_not_invent_a_measurement_time(make_sensor: Any, timestamp: Any) -> None:
    harness = await make_sensor(_device(timestamp=timestamp))
    attributes = _assert_state(harness, None, "awaiting_reading")
    assert attributes.get("last_known_value") is None
    assert attributes.get("last_known_at") is None


async def test_iso_cloud_time_preserves_its_instant(make_sensor: Any) -> None:
    harness = await make_sensor(_device(timestamp="2026-09-25T10:00:00+02:00"))
    _assert_history(_assert_state(harness, 1.8, "valid"))


async def test_repeated_cloud_snapshot_does_not_rejuvenate_history(make_sensor: Any) -> None:
    harness = await make_sensor()
    for _ in range(3):
        await harness.publish()
        _assert_history(_assert_state(harness, 1.8, "valid"))


async def test_cloud_timestamp_rollback_does_not_replace_newer_history(make_sensor: Any) -> None:
    harness = await make_sensor()
    await harness.publish(salinity=220, salinity_ts=TS0 - 60)
    _assert_history(_assert_state(harness, 1.8, "awaiting_reading"))


async def test_resuming_production_requires_salt_measured_after_the_stop(make_sensor: Any) -> None:
    harness = await make_sensor()
    await harness.publish(production=0, production_ts=TS0 + 60, salinity=0, salinity_ts=TS0 + 60)
    _assert_history(_assert_state(harness, 1.8, "not_producing"))
    await harness.publish(production=90, production_ts=TS0 + 120, salinity=180, salinity_ts=TS0)
    _assert_history(_assert_state(harness, 1.8, "awaiting_reading"))
    await harness.publish(salinity=180, salinity_ts=TS0 + 121)
    _assert_history(_assert_state(harness, 1.8, "valid"), when=T0 + timedelta(seconds=121))


async def test_active_flow_alarm_blocks_even_positive_readings_but_preserves_history(make_sensor: Any) -> None:
    harness = await make_sensor()
    await harness.publish(alarms=[FLOW_ALARM], salinity=200, salinity_ts=TS0 + 60)
    _assert_history(_assert_state(harness, None, "no_flow"))


async def test_cleared_or_unrelated_alarms_do_not_block_readings(make_sensor: Any) -> None:
    device = _device()
    device["alarms"] = [{**FLOW_ALARM, "value": False}, {"errorCode": "PH_HIGH", "value": True}, "junk"]
    harness = await make_sensor(device)
    _assert_history(_assert_state(harness, 1.8, "valid"))


async def test_offline_cached_snapshot_keeps_history_without_claiming_a_current_measurement(make_sensor: Any) -> None:
    harness = await make_sensor()
    await harness.publish(online=False)
    _assert_history(_assert_state(harness, 1.8, "offline"))


@pytest.mark.parametrize("missing", ["device", "components"])
async def test_missing_data_marks_sensor_unavailable_and_preserves_history(make_sensor: Any, missing: str) -> None:
    """A successful but incomplete cloud response is not live measurement data."""
    harness = await make_sensor()
    data = deepcopy(harness.coordinator.data)
    if missing == "device":
        data[POOL_ID]["devices"] = []
    else:
        data[POOL_ID]["devices"][0]["components"] = {}
    harness.coordinator.async_set_updated_data(data)
    await harness.hass.async_block_till_done()

    assert harness.sensor.available is False
    assert harness.sensor.native_value == 1.8
    state = harness.hass.states.get(SENSOR_ID)
    assert state is not None
    assert state.state == "unavailable"
    assert harness.status.native_value == "offline"
    status_state = harness.hass.states.get(STATUS_ID)
    assert status_state is not None
    assert status_state.state == "offline"
    attributes = harness.sensor.extra_state_attributes
    assert attributes["measurement_status"] == "offline"
    assert attributes["is_current_reading"] is False
    _assert_history(attributes)


async def test_status_without_enabled_numeric_source_is_unavailable(make_sensor: Any) -> None:
    """A disabled numeric entity cannot feed valid status updates to its companion."""
    harness = await make_sensor(numeric_enabled=False)
    assert harness.hass.states.get(SENSOR_ID) is None
    assert harness.status.available is False
    state = harness.hass.states.get(STATUS_ID)
    assert state is not None
    assert state.state == "unavailable"
    await harness.publish(salinity=190, salinity_ts=TS0 + 60)
    state = harness.hass.states.get(STATUS_ID)
    assert state is not None
    assert state.state == "unavailable"


async def test_removing_numeric_source_marks_companion_unavailable(make_sensor: Any) -> None:
    """Removing the source must not leave its previously valid companion frozen."""
    harness = await make_sensor()
    _assert_state(harness, 1.8, "valid")
    await harness.component.async_remove_entity(SENSOR_ID)
    await harness.hass.async_block_till_done()
    assert harness.status.available is False
    state = harness.hass.states.get(STATUS_ID)
    assert state is not None
    assert state.state == "unavailable"


async def test_restore_extra_data_preserves_measurement_age_and_resume_barrier(make_sensor: Any) -> None:
    """Restore through HA's extra-data store, not just an internal helper."""
    first = await make_sensor()
    await first.publish(production=0, production_ts=TS0 + 60, salinity=0, salinity_ts=TS0 + 60)
    state = first.hass.states.get(SENSOR_ID)
    assert state is not None
    extra_data = first.sensor.extra_restore_state_data.as_dict()
    await first.component.async_remove_entity(STATUS_ID)
    await first.component.async_remove_entity(SENSOR_ID)

    restored = await make_sensor(first.device, restored=(state, extra_data))
    _assert_history(_assert_state(restored, 1.8, "not_producing"))
    await restored.publish(production=90, production_ts=TS0 + 120, salinity=180, salinity_ts=TS0)
    _assert_history(_assert_state(restored, 1.8, "awaiting_reading"))
    await restored.publish(salinity=181, salinity_ts=TS0 + 121)
    _assert_history(_assert_state(restored, 1.81, "valid"), value=1.81, when=T0 + timedelta(seconds=121))


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("last_known_value", float("nan")),
        ("last_known_value", float("inf")),
        ("last_known_value", True),
        ("last_known_value", -1),
        ("last_known_at", None),
        ("last_known_at", "not-a-date"),
        ("last_known_at", "2026-09-25T08:00:00"),
    ],
)
async def test_malformed_restore_history_is_not_published(make_sensor: Any, field: str, bad_value: Any) -> None:
    """Damaged restore metadata must not turn a stopped cell into a salt reading."""
    first = await make_sensor()
    await first.publish(production=0, production_ts=TS0 + 60, salinity=0, salinity_ts=TS0 + 60)
    state = first.hass.states.get(SENSOR_ID)
    assert state is not None
    extra_data = first.sensor.extra_restore_state_data.as_dict()
    extra_data["cellguard_salinity"][field] = bad_value
    await first.component.async_remove_entity(STATUS_ID)
    await first.component.async_remove_entity(SENSOR_ID)

    restored = await make_sensor(first.device, restored=(state, extra_data))
    attributes = _assert_state(restored, None, "not_producing")
    assert attributes.get("last_known_value") is None
    assert attributes.get("last_known_at") is None
