"""Base entity classes for Fluidra Pool integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

if TYPE_CHECKING:
    from .coordinator import FluidraDataUpdateCoordinator
    from .fluidra_api import FluidraPoolAPI


class FluidraPoolEntity(CoordinatorEntity):
    """Base class for all Fluidra Pool entities (read-only)."""

    __slots__ = ("_pool_id", "_device_id")

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        pool_id: str,
        device_id: str,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._pool_id = pool_id
        self._device_id = device_id

    @property
    def device_data(self) -> dict[str, Any]:
        """Get device data from coordinator."""
        if self.coordinator.data is None:
            return {}
        pool = self.coordinator.data.get(self._pool_id)
        if pool:
            for device in pool.get("devices", []):
                if device.get("device_id") == self._device_id:
                    return device
        return {}

    @property
    def pool_data(self) -> dict[str, Any]:
        """Get pool data from coordinator."""
        if self.coordinator.data is None:
            return {}
        return self.coordinator.data.get(self._pool_id, {})

    @property
    def device_info(self) -> dict[str, Any]:
        """Return device info."""
        device_data = self.device_data
        device_name = device_data.get("name", f"Device {self._device_id}")
        return {
            "identifiers": {(DOMAIN, self._device_id)},
            "name": device_name,
            "manufacturer": device_data.get("manufacturer", "Fluidra"),
            "model": device_data.get("model", "Pool Equipment"),
            "via_device": (DOMAIN, self._pool_id),
        }

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.last_update_success and self.device_data.get("online", False)


class FluidraPoolControlEntity(FluidraPoolEntity):
    """Base class for Fluidra Pool entities that control devices."""

    __slots__ = ("_api",)

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api: FluidraPoolAPI,
        pool_id: str,
        device_id: str,
    ) -> None:
        """Initialize the control entity."""
        super().__init__(coordinator, pool_id, device_id)
        self._api = api
