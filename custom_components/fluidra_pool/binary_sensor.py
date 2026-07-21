"""Binary sensor platform for Fluidra Pool integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN, FluidraPoolConfigEntry
from .device_registry import DeviceIdentifier
from .entity import FluidraPoolEntity
from .platform_setup import async_setup_dynamic_platform

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import FluidraDataUpdateCoordinator
    from .fluidra_api import FluidraPoolAPI

PARALLEL_UPDATES = 0  # Coordinator handles all updates


class FluidraChlorinatorProducingBinarySensor(FluidraPoolEntity, BinarySensorEntity):
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
        super().__init__(coordinator, pool_id, device_id)
        self._api = api
        self._component_id = component_id

        self._attr_unique_id = f"fluidra_{self._device_id}_producing"
        self._attr_translation_key = "chlorinator_producing"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        device_name = self.device_data.get("name") or f"Chlorinator {self._device_id}"
        firmware = self.device_data.get("firmware_version_component")
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=device_name,
            manufacturer=self.device_data.get("manufacturer", "Fluidra"),
            model="Chlorinator",
            sw_version=str(firmware) if firmware is not None else None,
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


class FluidraPumpSpeedInputBinarySensor(FluidraPoolEntity, BinarySensorEntity):
    """Speed-preset dry-contact digital input on a Victoria VS pump (Issue #144).

    These physical input terminals — Low (c29), Medium (c28), High (c27) — read
    active only when an external relay is wired to them (e.g. an ice-guard
    interlock forcing the pump on). Exposed as diagnostic binary sensors so users
    can automate on them. Decoded by the coordinator into ``pump_speed_input_*``.
    """

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api: FluidraPoolAPI,
        pool_id: str,
        device_id: str,
        tier: str,
    ) -> None:
        """Initialize the speed-input binary sensor for a given tier."""
        super().__init__(coordinator, pool_id, device_id)
        self._api = api
        self._tier = tier
        self._attr_unique_id = f"{DOMAIN}_{pool_id}_{device_id}_speed_input_{tier}"
        self._attr_translation_key = f"speed_input_{tier}"

    @property
    def is_on(self) -> bool | None:
        """Return True when this dry-contact input is active."""
        value = self.device_data.get(f"pump_speed_input_{self._tier}")
        return bool(value) if value is not None else None


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: FluidraPoolConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Fluidra Pool binary sensors, including devices added later."""
    coordinator = config_entry.runtime_data.coordinator

    def _build(pool_id: str, device: dict[str, Any]) -> list[BinarySensorEntity]:
        """Create binary sensors for one device."""
        entities: list[BinarySensorEntity] = []
        device_id = device["device_id"]

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

        # Victoria VS speed-preset dry-contact inputs (Issue #144).
        speed_inputs = DeviceIdentifier.get_feature(device, "speed_input_components")
        if isinstance(speed_inputs, dict):
            for tier in ("low", "medium", "high"):
                if tier in speed_inputs:
                    entities.append(
                        FluidraPumpSpeedInputBinarySensor(
                            coordinator,
                            coordinator.api,
                            pool_id,
                            device_id,
                            tier,
                        )
                    )

        return entities

    await async_setup_dynamic_platform(config_entry, async_add_entities, _build)
