"""Binary sensor platform for Fluidra Pool integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, FluidraPoolConfigEntry
from .device_registry import DeviceIdentifier

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import FluidraDataUpdateCoordinator
    from .fluidra_api import FluidraPoolAPI

PARALLEL_UPDATES = 0  # Coordinator handles all updates


class FluidraChlorinatorProducingBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Binary sensor for the chlorinator cell active-production state.

    In ORP/CLI regulation the cell cycles on and off around the setpoint: the
    production register (configured via the ``cell_production_state`` feature)
    reads ``0`` when the cell is idle and a non-zero production percentage when
    it is actively producing chlorine (Issue #109).
    """

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api: FluidraPoolAPI,
        pool_id: str,
        device_id: str,
        component_id: int,
    ) -> None:
        """Initialize the chlorinator producing binary sensor."""
        super().__init__(coordinator)
        self._api = api
        self._pool_id = pool_id
        self._device_id = device_id
        self._component_id = component_id

        self._attr_unique_id = f"fluidra_{self._device_id}_producing"
        self._attr_translation_key = "chlorinator_producing"

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

        Mirrors the chlorinator sensors: bridged children can report
        ``online=False`` even while polling succeeds, so use the presence of
        fresh component data as the availability signal instead (Issue #63).
        """
        return self.coordinator.last_update_success and bool(self.device_data.get("components"))

    @property
    def is_on(self) -> bool | None:
        """Return True when the cell is actively producing."""
        components = self.device_data.get("components", {})
        component_data = components.get(str(self._component_id), {})
        raw_value = component_data.get("reportedValue")

        if raw_value is None:
            return None

        try:
            return float(raw_value) > 0
        except (ValueError, TypeError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        components = self.device_data.get("components", {})
        component_data = components.get(str(self._component_id), {})

        return {
            "component_id": self._component_id,
            "raw_value": component_data.get("reportedValue"),
            "device_id": self._device_id,
        }


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: FluidraPoolConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Fluidra Pool binary sensors, including devices added later."""
    coordinator = config_entry.runtime_data.coordinator
    known_devices: set[str] = set()

    @callback
    def _add_entities(pools: list[dict[str, Any]]) -> None:
        """Create entities for any device not seen yet (dynamic-devices)."""
        entities: list[BinarySensorEntity] = []

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

                config = DeviceIdentifier.identify_device(device)
                device_type = config.device_type if config else device.get("type", "")
                if device_type != "chlorinator":
                    continue

                production_component = DeviceIdentifier.get_feature(device, "cell_production_state")
                if production_component is not None:
                    entities.append(
                        FluidraChlorinatorProducingBinarySensor(
                            coordinator,
                            coordinator.api,
                            pool_id,
                            device_id,
                            production_component,
                        )
                    )

        if entities:
            async_add_entities(entities)

    # Initial setup from the cached discovery (fast startup, unchanged behaviour).
    pools = coordinator.api.cached_pools or await coordinator.api.get_pools()
    _add_entities(pools)

    # Add entities for devices that appear on later polls, without a reload.
    @callback
    def _on_coordinator_update() -> None:
        _add_entities(coordinator.get_pools_from_data())

    config_entry.async_on_unload(coordinator.async_add_listener(_on_coordinator_update))
