"""Base sensor classes for Fluidra Pool integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ..const import DOMAIN
from ..entity import FluidraPoolEntity

if TYPE_CHECKING:
    from ..coordinator import FluidraDataUpdateCoordinator
    from ..fluidra_api import FluidraPoolAPI


class FluidraPoolSensorEntity(FluidraPoolEntity, SensorEntity):
    """Base class for Fluidra Pool device-attached sensor entities."""

    __slots__ = ("_api", "_sensor_type")

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api: FluidraPoolAPI,
        pool_id: str,
        device_id: str,
        sensor_type: str = "",
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, pool_id, device_id)
        self._api = api
        self._sensor_type = sensor_type

    @property
    def unique_id(self) -> str:
        """Return unique ID."""
        suffix = f"_{self._sensor_type}" if self._sensor_type else ""
        return f"{DOMAIN}_{self._pool_id}_{self._device_id}_sensor{suffix}"


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
