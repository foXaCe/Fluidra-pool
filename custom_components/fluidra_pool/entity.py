"""Base entity classes for Fluidra Pool integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEVICE_MODEL_FALLBACK, DEVICE_MODEL_MAP, DOMAIN

if TYPE_CHECKING:
    from .coordinator import FluidraDataUpdateCoordinator
    from .fluidra_api import FluidraPoolAPI


class FluidraPoolEntity(CoordinatorEntity):
    """Base class for all Fluidra Pool entities (read-only)."""

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
    def device_info(self) -> DeviceInfo:
        """Return device info using device registry for consistent naming."""
        from .device_registry import DeviceIdentifier

        device_data = self.device_data
        config = DeviceIdentifier.identify_device(device_data)

        # Use device registry to determine model type
        if config:
            default_model = DEVICE_MODEL_MAP.get(config.device_type, DEVICE_MODEL_FALLBACK)
        else:
            default_model = DEVICE_MODEL_FALLBACK

        device_name = device_data.get("name", f"Device {self._device_id}")
        firmware = device_data.get("firmware_version_component")
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=device_name,
            manufacturer=device_data.get("manufacturer", "Fluidra"),
            model=default_model,
            sw_version=str(firmware) if firmware is not None else None,
            via_device=(DOMAIN, self._pool_id),
        )

    @property
    def available(self) -> bool:
        """Return if entity is available.

        Unavailable only when the coordinator failed, the device vanished from
        the data, or the cloud *explicitly* reports it offline. Missing
        connectivity info (first poll after startup, devices whose status
        carries no ``connectivity.connected``) must not read as offline.
        """
        device_data = self.device_data
        return self.coordinator.last_update_success and bool(device_data) and device_data.get("online") is not False


class FluidraPoolControlEntity(FluidraPoolEntity):
    """Base class for Fluidra Pool entities that control devices."""

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

    def _ensure_pool_writable(self) -> None:
        """Fail fast when the account only has viewer access to this pool.

        The Fluidra cloud accepts control writes from a viewer (read-only)
        account with a fake HTTP 200 that echoes the requested value but never
        persists it (Issue #133), so commands silently have no effect. Raise a
        clear error instead — before any optimistic state is set. Only the
        confirmed-read-only level blocks; owner/shared/unknown pass through.
        """
        if self.pool_data.get("access_level") == "viewer":
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="pool_read_only",
                translation_placeholders={"pool_name": str(self.pool_data.get("name", self._pool_id))},
            )
