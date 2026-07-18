"""Sensor platform for Fluidra Pool integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorEntity

from ..const import DEVICE_TYPE_CHLORINATOR, FluidraPoolConfigEntry
from ..device_registry import DeviceIdentifier
from ..platform_setup import async_setup_dynamic_platform
from .base import FluidraPoolSensorBase, FluidraPoolSensorEntity
from .chlorinator import FluidraChlorinatorSensor
from .device import (
    FluidraDeviceInfoSensor,
    FluidraLightBrightnessSensor,
    FluidraPumpHeadSensor,
    FluidraPumpPowerSensor,
    FluidraPumpScheduleSensor,
    FluidraPumpSpeedSensor,
    FluidraRunningHoursSensor,
    FluidraTemperatureSensor,
)
from .pool import (
    FluidraPoolLocationSensor,
    FluidraPoolStatusSensor,
    FluidraPoolWaterQualitySensor,
    FluidraPoolWeatherSensor,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

__all__ = [
    "FluidraChlorinatorSensor",
    "FluidraDeviceInfoSensor",
    "FluidraLightBrightnessSensor",
    "FluidraPoolLocationSensor",
    "FluidraPoolSensorBase",
    "FluidraPoolSensorEntity",
    "FluidraPoolStatusSensor",
    "FluidraPoolWaterQualitySensor",
    "FluidraPoolWeatherSensor",
    "FluidraPumpHeadSensor",
    "FluidraPumpPowerSensor",
    "FluidraPumpScheduleSensor",
    "FluidraPumpSpeedSensor",
    "FluidraRunningHoursSensor",
    "FluidraTemperatureSensor",
    "async_setup_entry",
]

PARALLEL_UPDATES = 0  # Coordinator handles all updates


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: FluidraPoolConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Fluidra Pool sensor entities, including devices/pools added later."""
    coordinator = config_entry.runtime_data.coordinator

    def _build_device_sensors(pool_id: str, device: dict[str, Any]) -> list[SensorEntity]:
        """Create sensor entities for one device."""
        entities: list[SensorEntity] = []
        device_id = device["device_id"]

        # Use device registry to determine which sensors to create
        if DeviceIdentifier.should_create_entity(device, "sensor_info"):
            entities.append(FluidraDeviceInfoSensor(coordinator, coordinator.api, pool_id, device_id))

        if DeviceIdentifier.should_create_entity(device, "sensor_schedule"):
            entities.append(FluidraPumpScheduleSensor(coordinator, coordinator.api, pool_id, device_id))

        if DeviceIdentifier.should_create_entity(device, "sensor_speed"):
            entities.append(FluidraPumpSpeedSensor(coordinator, coordinator.api, pool_id, device_id))

        if DeviceIdentifier.should_create_entity(device, "sensor_power"):
            entities.append(FluidraPumpPowerSensor(coordinator, coordinator.api, pool_id, device_id))

        if DeviceIdentifier.should_create_entity(device, "sensor_head"):
            entities.append(FluidraPumpHeadSensor(coordinator, coordinator.api, pool_id, device_id))

        if DeviceIdentifier.should_create_entity(device, "sensor_temperature"):
            # Temperature sensors for heaters / heat pumps.
            if "target_temperature" in device:
                entities.append(FluidraTemperatureSensor(coordinator, coordinator.api, pool_id, device_id, "target"))
            # Z550iQ+ heat pump specific temperature sensors
            if DeviceIdentifier.has_feature(device, "z550_mode"):
                entities.append(FluidraTemperatureSensor(coordinator, coordinator.api, pool_id, device_id, "water"))
                entities.append(FluidraTemperatureSensor(coordinator, coordinator.api, pool_id, device_id, "air"))
            # Z260iQ-family heat pump temperature sensors (incl. the
            # Z250iQ, promoted to the same layout — Issue #139).
            if DeviceIdentifier.has_feature(device, "z260iq_mode"):
                entities.append(FluidraTemperatureSensor(coordinator, coordinator.api, pool_id, device_id, "water"))
                entities.append(FluidraTemperatureSensor(coordinator, coordinator.api, pool_id, device_id, "air"))

        if DeviceIdentifier.should_create_entity(device, "sensor_brightness"):
            entities.append(FluidraLightBrightnessSensor(coordinator, coordinator.api, pool_id, device_id))

        if DeviceIdentifier.should_create_entity(device, "sensor_running_hours"):
            entities.append(FluidraRunningHoursSensor(coordinator, coordinator.api, pool_id, device_id))

        # Chlorinator sensors - create based on sensors_config from device registry
        config = DeviceIdentifier.identify_device(device)
        device_type = config.device_type if config else device.get("type", "")
        if device_type == DEVICE_TYPE_CHLORINATOR:
            sensors_config = DeviceIdentifier.get_feature(device, "sensors", {})

            for sensor_type in (
                "ph",
                "orp",
                "free_chlorine",
                "temperature",
                "salinity",
                "chlorination_actual",
                "battery_voltage",
            ):
                if sensor_type in sensors_config:
                    entities.append(
                        FluidraChlorinatorSensor(
                            coordinator,
                            coordinator.api,
                            pool_id,
                            device_id,
                            sensor_type,
                            sensors_config[sensor_type],
                        )
                    )

        return entities

    def _build_pool_sensors(pool_id: str, pool: dict[str, Any]) -> list[SensorEntity]:
        """Create pool-level sensors (not tied to a specific device)."""
        return [
            FluidraPoolWeatherSensor(coordinator, coordinator.api, pool_id),
            FluidraPoolStatusSensor(coordinator, coordinator.api, pool_id),
            FluidraPoolLocationSensor(coordinator, coordinator.api, pool_id),
            FluidraPoolWaterQualitySensor(coordinator, coordinator.api, pool_id),
        ]

    await async_setup_dynamic_platform(
        config_entry,
        async_add_entities,
        _build_device_sensors,
        build_pool_entities=_build_pool_sensors,
    )
