"""Sensor platform for Fluidra Pool integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import callback

from ..const import DEVICE_TYPE_CHLORINATOR, FluidraPoolConfigEntry
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
    """Set up Fluidra Pool sensor entities, including devices/pools added later."""
    coordinator = config_entry.runtime_data.coordinator
    known_devices: set[str] = set()
    known_pools: set[str] = set()

    @callback
    def _add_entities(pools: list[dict[str, Any]]) -> None:
        """Create entities for any pool/device not seen yet (dynamic-devices)."""
        entities: list[SensorEntity] = []

        for pool in pools:
            pool_id = pool["id"]

            for device in pool.get("devices", []):
                device_id = device.get("device_id")
                if not device_id:
                    continue

                key = f"{pool_id}_{device_id}"
                if key in known_devices:
                    continue
                known_devices.add(key)

                # Use device registry to determine which sensors to create
                if DeviceIdentifier.should_create_entity(device, "sensor_info"):
                    entities.append(FluidraDeviceInfoSensor(coordinator, coordinator.api, pool_id, device_id))

                if DeviceIdentifier.should_create_entity(device, "sensor_schedule"):
                    entities.append(FluidraPumpScheduleSensor(coordinator, coordinator.api, pool_id, device_id))

                if DeviceIdentifier.should_create_entity(device, "sensor_speed"):
                    entities.append(FluidraPumpSpeedSensor(coordinator, coordinator.api, pool_id, device_id))

                if DeviceIdentifier.should_create_entity(device, "sensor_temperature"):
                    # Temperature sensors for heaters / heat pumps.
                    if "target_temperature" in device:
                        entities.append(
                            FluidraTemperatureSensor(coordinator, coordinator.api, pool_id, device_id, "target")
                        )
                    # Z550iQ+ heat pump specific temperature sensors
                    if DeviceIdentifier.has_feature(device, "z550_mode"):
                        entities.append(
                            FluidraTemperatureSensor(coordinator, coordinator.api, pool_id, device_id, "water")
                        )
                        entities.append(
                            FluidraTemperatureSensor(coordinator, coordinator.api, pool_id, device_id, "air")
                        )
                    # Z260iQ-family heat pump temperature sensors (incl. the
                    # Z250iQ, promoted to the same layout — Issue #139).
                    if DeviceIdentifier.has_feature(device, "z260iq_mode"):
                        entities.append(
                            FluidraTemperatureSensor(coordinator, coordinator.api, pool_id, device_id, "water")
                        )
                        entities.append(
                            FluidraTemperatureSensor(coordinator, coordinator.api, pool_id, device_id, "air")
                        )

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

            # Pool-level sensors (not tied to a specific device).
            if pool_id not in known_pools:
                known_pools.add(pool_id)
                entities.append(FluidraPoolWeatherSensor(coordinator, coordinator.api, pool_id))
                entities.append(FluidraPoolStatusSensor(coordinator, coordinator.api, pool_id))
                entities.append(FluidraPoolLocationSensor(coordinator, coordinator.api, pool_id))
                entities.append(FluidraPoolWaterQualitySensor(coordinator, coordinator.api, pool_id))

        if entities:
            async_add_entities(entities)

    # Initial setup from the cached discovery (fast startup, unchanged behaviour).
    pools = coordinator.api.cached_pools or await coordinator.api.get_pools()
    _add_entities(pools)

    # Add entities for devices/pools that appear on later polls, without a reload.
    @callback
    def _on_coordinator_update() -> None:
        _add_entities(coordinator.get_pools_from_data())

    config_entry.async_on_unload(coordinator.async_add_listener(_on_coordinator_update))
