"""Number platform for Fluidra Pool integration."""
import logging
from typing import Any, Dict, Optional

from homeassistant.components.number import NumberEntity, NumberDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import EntityCategory
from homeassistant.const import PERCENTAGE

from .const import DOMAIN, DEVICE_TYPE_PUMP
from .coordinator import FluidraDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Fluidra Pool number entities."""
    coordinator: FluidraDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    entities = []

    pools = await coordinator.api.get_pools()
    for pool in pools:
        for device in pool["devices"]:
            if device.get("type") == DEVICE_TYPE_PUMP:
                device_id = device["device_id"]

                # Groupe Réglages - Contrôles de vitesse temporairement désactivés
                pass

    async_add_entities(entities)


class FluidraPumpComponentNumber(CoordinatorEntity, NumberEntity):
    """Base class for Fluidra pump component controls."""

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api,
        pool_id: str,
        device_id: str,
        component_id: int,
        control_type: str,
    ) -> None:
        """Initialize the pump component number."""
        super().__init__(coordinator)
        self._api = api
        self._pool_id = pool_id
        self._device_id = device_id
        self._component_id = component_id
        self._control_type = control_type

        device_name = self.device_data.get("name") or f"E30iQ Pump {self._device_id}"

        self._attr_translation_key = f"pump_{control_type.lower()}"
        self._attr_translation_placeholders = {"device_name": device_name}
        self._attr_unique_id = f"fluidra_{self._device_id}_component_{component_id}"
        self._attr_entity_category = EntityCategory.CONFIG
        self._attr_mode = "slider"

    @property
    def device_data(self) -> dict:
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
    def pool_data(self) -> dict:
        """Get pool data from coordinator."""
        if self.coordinator.data is None:
            return {}
        return self.coordinator.data.get(self._pool_id, {})

    @property
    def device_info(self) -> Dict[str, Any]:
        """Return device information."""
        device_name = self.device_data.get("name") or f"E30iQ Pump {self._device_id}"
        return {
            "identifiers": {(DOMAIN, self._device_id)},
            "name": device_name,
            "manufacturer": self.device_data.get("manufacturer", "Fluidra"),
            "model": self.device_data.get("model", "E30iQ"),
            "via_device": (DOMAIN, self._pool_id),
        }

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return (
            self.coordinator.last_update_success
            and self.device_data.get("online", False)
        )

    @property
    def native_value(self) -> Optional[float]:
        """Return the current component value."""
        components = self.device_data.get("components", {})
        component_data = components.get(str(self._component_id), {})

        # Debug logging

        reported_value = component_data.get("reportedValue", 0)

        return reported_value

    async def async_set_native_value(self, value: float) -> None:
        """Set the component value."""
        int_value = int(value)

        _LOGGER.info(f"Setting component {self._component_id} on device {self._device_id} to {int_value}")

        try:
            success = await self._api.set_component_value(self._device_id, self._component_id, int_value)

            if success:
                _LOGGER.info(f"✅ Component {self._component_id} set to {int_value}")
                await self.coordinator.async_request_refresh()
            else:
                _LOGGER.error(f"❌ Failed to set component {self._component_id} to {int_value}")

        except Exception as err:
            _LOGGER.error(f"Error setting component {self._component_id}: {err}")
            raise

    @property
    def icon(self) -> str:
        """Return the icon for the entity."""
        current_speed = self.native_value or 0
        if current_speed == 0:
            return "mdi:pump-off"
        else:
            return "mdi:pump"

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return additional state attributes."""
        components = self.device_data.get("components", {})
        component_data = components.get(str(self._component_id), {})

        return {
            "component_id": self._component_id,
            "control_type": self._control_type,
            "reported_value": component_data.get("reportedValue"),
            "desired_value": component_data.get("desiredValue"),
            "pump_model": self.device_data.get("model", "E30iQ"),
            "online": self.device_data.get("online", False),
        }


class FluidraSpeedControl(CoordinatorEntity, NumberEntity):
    """Unified speed control for pump component 15 (40-105%)."""

    def __init__(self, coordinator, api, pool_id: str, device_id: str) -> None:
        """Initialize the speed control."""
        super().__init__(coordinator)
        self._api = api
        self._pool_id = pool_id
        self._device_id = device_id

        device_name = self.device_data.get("name") or f"E30iQ Pump {self._device_id}"

        self._attr_translation_key = "pump_vitesse"
        self._attr_translation_placeholders = {"device_name": device_name}
        self._attr_unique_id = f"fluidra_{self._device_id}_speed_control"
        self._attr_entity_category = EntityCategory.CONFIG
        self._attr_mode = "slider"

        self._attr_native_min_value = 40
        self._attr_native_max_value = 105
        self._attr_native_step = 1
        self._attr_native_unit_of_measurement = PERCENTAGE
        self._attr_device_class = NumberDeviceClass.POWER_FACTOR

    @property
    def device_data(self) -> dict:
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
    def device_info(self) -> Dict[str, Any]:
        """Return device information."""
        device_name = self.device_data.get("name") or f"E30iQ Pump {self._device_id}"
        return {
            "identifiers": {(DOMAIN, self._device_id)},
            "name": device_name,
            "manufacturer": self.device_data.get("manufacturer", "Fluidra"),
            "model": self.device_data.get("model", "E30iQ"),
            "via_device": (DOMAIN, self._pool_id),
        }

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return (
            self.coordinator.last_update_success
            and self.device_data.get("online", False)
        )

    @property
    def native_value(self) -> Optional[float]:
        """Return the current speed value from component 15."""
        components = self.device_data.get("components", {})
        component_15_data = components.get("15", {})
        current_value = component_15_data.get("reportedValue", 50)

        return current_value

    async def async_set_native_value(self, value: float) -> None:
        """Set the speed percentage directly to component 15."""
        int_value = int(value)

        _LOGGER.info(f"Setting pump speed to {int_value}%")

        try:
            success = await self._api.set_component_value(self._device_id, 15, int_value)
            if not success:
                _LOGGER.error(f"Failed to set speed percentage to {int_value}")
                return

            _LOGGER.info(f"✅ Pump speed set to {int_value}%")
            await self.coordinator.async_request_refresh()

        except Exception as err:
            _LOGGER.error(f"Error setting pump speed: {err}")
            raise

    @property
    def icon(self) -> str:
        """Return the icon for the entity."""
        current_speed = self.native_value or 0
        if current_speed >= 85:
            return "mdi:speedometer"
        elif current_speed >= 65:
            return "mdi:speedometer-medium"
        elif current_speed >= 40:
            return "mdi:speedometer-slow"
        else:
            return "mdi:pump-off"

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return additional state attributes."""
        current_speed = self.native_value or 0
        speed_level = "high" if current_speed >= 85 else "medium" if current_speed >= 65 else "low"

        return {
            "speed_range": "40-105%",
            "component_id": 15,
            "speed_level": speed_level,
            "pump_model": self.device_data.get("model", "E30iQ"),
            "online": self.device_data.get("online", False),
        }


