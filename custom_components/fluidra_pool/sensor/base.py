"""Base sensor classes for Fluidra Pool integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ..const import DOMAIN
from ..device_registry import DeviceIdentifier

if TYPE_CHECKING:
    from ..coordinator import FluidraDataUpdateCoordinator
    from ..fluidra_api import FluidraPoolAPI


class FluidraPoolSensorEntity(CoordinatorEntity, SensorEntity):
    """Base class for Fluidra Pool device-attached sensor entities."""

    __slots__ = ("_api", "_device_id", "_pool_id", "_sensor_type")

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api: FluidraPoolAPI,
        pool_id: str,
        device_id: str,
        sensor_type: str = "",
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._api = api
        self._pool_id = pool_id
        self._device_id = device_id
        self._sensor_type = sensor_type

    @property
    def device_data(self) -> dict[str, Any]:
        """Get device data from coordinator."""
        data = self.coordinator.data
        if data is None:
            return {}
        pool: dict[str, Any] | None = data.get(self._pool_id)
        if pool:
            devices: list[dict[str, Any]] = pool.get("devices", [])
            for device in devices:
                if device.get("device_id") == self._device_id:
                    return device
        return {}

    @property
    def pool_data(self) -> dict[str, Any]:
        """Get pool data from coordinator."""
        data = self.coordinator.data
        if data is None:
            return {}
        pool: dict[str, Any] = data.get(self._pool_id, {})
        return pool

    @property
    def unique_id(self) -> str:
        """Return unique ID."""
        suffix = f"_{self._sensor_type}" if self._sensor_type else ""
        return f"{DOMAIN}_{self._pool_id}_{self._device_id}_sensor{suffix}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info using device registry for consistent naming."""
        device_data = self.device_data
        config = DeviceIdentifier.identify_device(device_data)

        model_map = {
            "chlorinator": "Chlorinator",
            "pump": "Pump",
            "heat_pump": "Heat Pump",
            "light": "Light",
            "heater": "Heater",
        }
        default_model = model_map.get(config.device_type, "Pool Equipment") if config else "Pool Equipment"

        device_name = device_data.get("name", f"Device {self._device_id}")
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=device_name,
            manufacturer=device_data.get("manufacturer", "Fluidra"),
            model=default_model,
            via_device=(DOMAIN, self._pool_id),
        )

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.last_update_success and self.device_data.get("online", False)


class FluidraPoolSensorBase(CoordinatorEntity, SensorEntity):
    """Base class for pool-level sensor entities (not bound to a single device)."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api: FluidraPoolAPI,
        pool_id: str,
        sensor_type: str = "",
    ) -> None:
        """Initialize the pool sensor."""
        super().__init__(coordinator)
        self._api = api
        self._pool_id = pool_id
        self._sensor_type = sensor_type

    @property
    def pool_data(self) -> dict[str, Any]:
        """Get pool data from coordinator."""
        data = self.coordinator.data
        if data is None:
            return {}
        pool: dict[str, Any] = data.get(self._pool_id, {})
        return pool

    @property
    def unique_id(self) -> str:
        """Return unique ID."""
        suffix = f"_{self._sensor_type}" if self._sensor_type else ""
        return f"{DOMAIN}_{self._pool_id}_pool{suffix}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for the pool."""
        pool_name = self.pool_data.get("name", f"Pool {self._pool_id}")
        return DeviceInfo(
            identifiers={(DOMAIN, self._pool_id)},
            name=pool_name,
            manufacturer="Fluidra",
            model="Pool System",
            sw_version="1.0",
        )

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.last_update_success
