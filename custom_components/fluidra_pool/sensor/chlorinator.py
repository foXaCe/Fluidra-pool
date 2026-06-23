"""Chlorinator measurement sensors (pH, ORP, chlorine, temperature, salinity)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfTemperature
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ..const import DOMAIN
from ..device_registry import DeviceIdentifier

if TYPE_CHECKING:
    from ..coordinator import FluidraDataUpdateCoordinator
    from ..fluidra_api import FluidraPoolAPI

_LOGGER = logging.getLogger(__name__)


class FluidraChlorinatorSensor(CoordinatorEntity, SensorEntity):
    """Sensor for chlorinator measurements (pH, ORP, chlorine, temperature, salinity)."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api: FluidraPoolAPI,
        pool_id: str,
        device_id: str,
        sensor_type: str,
        component_id: int,
    ) -> None:
        """Initialize the chlorinator sensor."""
        super().__init__(coordinator)
        self._api = api
        self._pool_id = pool_id
        self._device_id = device_id
        self._sensor_type = sensor_type
        self._component_id = component_id

        # Sensor configuration based on type
        self._sensor_config: dict[str, dict[str, Any]] = {
            "ph": {
                "translation_key": "chlorinator_ph",
                "unit": None,
                "device_class": None,
                "state_class": SensorStateClass.MEASUREMENT,
                "icon": "mdi:ph",
                "divisor": 100,  # Component value is pH * 100 (720 = 7.20)
            },
            "orp": {
                "translation_key": "chlorinator_orp",
                "unit": "mV",
                "device_class": SensorDeviceClass.VOLTAGE,
                "state_class": SensorStateClass.MEASUREMENT,
                "icon": "mdi:lightning-bolt",
                "divisor": 1,
            },
            "free_chlorine": {
                "translation_key": "chlorinator_free_chlorine",
                "unit": "mg/L",
                "device_class": None,
                "state_class": SensorStateClass.MEASUREMENT,
                "icon": "mdi:test-tube",
                "divisor": 100,  # Component value is mg/L * 100
            },
            "temperature": {
                "translation_key": "chlorinator_water_temperature",
                "unit": UnitOfTemperature.CELSIUS,
                "device_class": SensorDeviceClass.TEMPERATURE,
                "state_class": SensorStateClass.MEASUREMENT,
                "icon": "mdi:thermometer",
                "divisor": 10,  # Component value is °C * 10
            },
            "salinity": {
                "translation_key": "chlorinator_salinity",
                "unit": "g/L",
                "device_class": None,
                "state_class": SensorStateClass.MEASUREMENT,
                "icon": "mdi:water-opacity",
                "divisor": 100,  # Component value is g/L * 100
            },
            "chlorination_actual": {
                "translation_key": "chlorinator_chlorination_actual",
                "unit": PERCENTAGE,
                "device_class": None,
                "state_class": SensorStateClass.MEASUREMENT,
                "icon": "mdi:percent",
                "divisor": 1,  # Already a percentage.
            },
        }

        config = self._sensor_config.get(sensor_type, {})
        self._attr_translation_key = config.get("translation_key", f"chlorinator_{sensor_type}")
        self._attr_unique_id = f"fluidra_{self._device_id}_{sensor_type}"
        self._attr_native_unit_of_measurement = config.get("unit")
        self._attr_device_class = config.get("device_class")
        self._attr_state_class = config.get("state_class")
        self._attr_icon = config.get("icon")
        self._divisor = config.get("divisor", 1)
        # Override divisor from device registry if available
        custom_divisors = DeviceIdentifier.get_feature(self.device_data, "sensor_divisors", {})
        if sensor_type in custom_divisors:
            self._divisor = custom_divisors[sensor_type]

    @property
    def device_data(self) -> dict[str, Any]:
        """Get device data from coordinator."""
        if self.coordinator.data is None:
            return {}
        pool: dict[str, Any] | None = self.coordinator.data.get(self._pool_id)
        if pool:
            devices: list[dict[str, Any]] = pool.get("devices", [])
            for device in devices:
                if device.get("device_id") == self._device_id:
                    return device
        return {}

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        device_name = self.device_data.get("name") or f"Chlorinator {self._device_id}"
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=device_name,
            manufacturer=self.device_data.get("manufacturer", "Fluidra"),
            model="Chlorinator",
            via_device=(DOMAIN, self._pool_id),
        )

    @property
    def available(self) -> bool:
        """Return True if entity is available.

        Bridged chlorinator children (`*.nn_*`) often report ``online=False``
        through their connectivity flag even when polling them succeeds, so
        gating on ``online`` makes the sensors permanently unavailable. Use
        the presence of fresh component data as the availability signal
        instead (Issue #63).
        """
        return self.coordinator.last_update_success and bool(self.device_data.get("components"))

    @property
    def native_value(self) -> float | None:
        """Return the sensor value."""
        components = self.device_data.get("components", {})
        component_data = components.get(str(self._component_id), {})
        raw_value = component_data.get("reportedValue")

        if raw_value is None:
            return None

        try:
            value: float = float(raw_value) / self._divisor
            return value
        except (ValueError, TypeError):
            _LOGGER.debug("Failed to parse sensor value %s for component %s", raw_value, self._component_id)
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        components = self.device_data.get("components", {})
        component_data = components.get(str(self._component_id), {})

        return {
            "component_id": self._component_id,
            "sensor_type": self._sensor_type,
            "raw_value": component_data.get("reportedValue"),
            "divisor": self._divisor,
            "device_id": self._device_id,
        }
