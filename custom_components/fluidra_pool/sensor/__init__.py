"""Sensor platform for Fluidra Pool integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.sensor import SensorEntity

from ..const import FluidraPoolConfigEntry
from ..device_registry import DeviceIdentifier
from .base import FluidraPoolSensorBase, FluidraPoolSensorEntity
from .chlorinator import FluidraChlorinatorSensor
from .device import (
    FluidraDeviceInfoSensor,
    FluidraLightBrightnessSensor,
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
    """Set up Fluidra Pool sensor entities."""
    coordinator = config_entry.runtime_data.coordinator

    entities: list[SensorEntity] = []

    # Use cached pools data instead of API call for faster startup
    pools = coordinator.api.cached_pools or await coordinator.api.get_pools()
    for pool in pools:
        for device in pool["devices"]:
            device_id = device.get("device_id")

            if not device_id:
                continue

            # Use device registry to determine which sensors to create
            if DeviceIdentifier.should_create_entity(device, "sensor_info"):
                entities.append(FluidraDeviceInfoSensor(coordinator, coordinator.api, pool["id"], device_id))

            if DeviceIdentifier.should_create_entity(device, "sensor_schedule"):
                entities.append(FluidraPumpScheduleSensor(coordinator, coordinator.api, pool["id"], device_id))

            if DeviceIdentifier.should_create_entity(device, "sensor_speed"):
                entities.append(FluidraPumpSpeedSensor(coordinator, coordinator.api, pool["id"], device_id))

            if DeviceIdentifier.should_create_entity(device, "sensor_temperature"):
                # Temperature sensors for heaters / heat pumps.
                if "target_temperature" in device:
                    entities.append(
                        FluidraTemperatureSensor(coordinator, coordinator.api, pool["id"], device_id, "target")
                    )
                # Z550iQ+ heat pump specific temperature sensors
                if DeviceIdentifier.has_feature(device, "z550_mode"):
                    entities.append(
                        FluidraTemperatureSensor(coordinator, coordinator.api, pool["id"], device_id, "water")
                    )
                    entities.append(
                        FluidraTemperatureSensor(coordinator, coordinator.api, pool["id"], device_id, "air")
                    )
                # Z260iQ heat pump specific temperature sensors
                if DeviceIdentifier.has_feature(device, "z260iq_mode"):
                    entities.append(
                        FluidraTemperatureSensor(coordinator, coordinator.api, pool["id"], device_id, "water")
                    )
                    entities.append(
                        FluidraTemperatureSensor(coordinator, coordinator.api, pool["id"], device_id, "air")
                    )

            if DeviceIdentifier.should_create_entity(device, "sensor_brightness"):
                entities.append(FluidraLightBrightnessSensor(coordinator, coordinator.api, pool["id"], device_id))

            if DeviceIdentifier.should_create_entity(device, "sensor_running_hours"):
                entities.append(FluidraRunningHoursSensor(coordinator, coordinator.api, pool["id"], device_id))

            # Chlorinator sensors - create based on sensors_config from device registry
            config = DeviceIdentifier.identify_device(device)
            device_type = config.device_type if config else device.get("type", "")
            if device_type == "chlorinator":
                sensors_config = DeviceIdentifier.get_feature(device, "sensors", {})

                for sensor_type in ("ph", "orp", "free_chlorine", "temperature", "salinity", "chlorination_actual"):
                    if sensor_type in sensors_config:
                        entities.append(
                            FluidraChlorinatorSensor(
                                coordinator,
                                coordinator.api,
                                pool["id"],
                                device_id,
                                sensor_type,
                                sensors_config[sensor_type],
                            )
                        )

        # Pool-level sensors (not tied to a specific device).
        entities.append(FluidraPoolWeatherSensor(coordinator, coordinator.api, pool["id"]))
        entities.append(FluidraPoolStatusSensor(coordinator, coordinator.api, pool["id"]))
        entities.append(FluidraPoolLocationSensor(coordinator, coordinator.api, pool["id"]))
        entities.append(FluidraPoolWaterQualitySensor(coordinator, coordinator.api, pool["id"]))

    async_add_entities(entities)
