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
            device_id = device.get("device_id")
            device_type = device.get("type", "")

            # Chlorinator chlorination level
            if device_type == "chlorinator":
                entities.append(FluidraChlorinatorLevelNumber(coordinator, coordinator.api, pool["id"], device_id))
                entities.append(FluidraChlorinatorPhSetpoint(coordinator, coordinator.api, pool["id"], device_id))
                entities.append(FluidraChlorinatorOrpSetpoint(coordinator, coordinator.api, pool["id"], device_id))
                _LOGGER.info(f"✅ Adding chlorinator controls (level, pH, ORP) for {device_id}")

            if device_type == DEVICE_TYPE_PUMP:
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


class FluidraChlorinatorLevelNumber(CoordinatorEntity, NumberEntity):
    """Number entity for chlorinator chlorination level (0-100%)."""

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api,
        pool_id: str,
        device_id: str,
    ) -> None:
        """Initialize the chlorinator level control."""
        super().__init__(coordinator)
        self._api = api
        self._pool_id = pool_id
        self._device_id = device_id

        device_name = self.device_data.get("name") or f"Chlorinator {self._device_id}"

        self._attr_name = f"{device_name} Chlorination Level"
        self._attr_unique_id = f"fluidra_{self._device_id}_chlorination_level"
        self._attr_mode = "slider"

        self._attr_native_min_value = 0
        self._attr_native_max_value = 100
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
        device_name = self.device_data.get("name") or f"Chlorinator {self._device_id}"
        return {
            "identifiers": {(DOMAIN, self._device_id)},
            "name": device_name,
            "manufacturer": self.device_data.get("manufacturer", "Fluidra"),
            "model": self.device_data.get("model", "Chlorinator"),
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
        """Return the current chlorination level from component 4."""
        components = self.device_data.get("components", {})
        component_4_data = components.get("4", {})
        current_value = component_4_data.get("reportedValue", 0)

        return current_value

    async def async_set_native_value(self, value: float) -> None:
        """Set the chlorination level via component 4."""
        int_value = int(value)

        _LOGGER.info(f"Setting chlorinator {self._device_id} chlorination level to {int_value}%")

        try:
            # Write to component 4
            success = await self._api.control_device_component(self._device_id, 4, int_value)

            if success:
                _LOGGER.info(f"✅ Chlorination level set to {int_value}%")
                import asyncio
                await asyncio.sleep(2)
                await self.coordinator.async_request_refresh()
            else:
                _LOGGER.error(f"❌ Failed to set chlorination level to {int_value}%")

        except Exception as err:
            _LOGGER.error(f"Error setting chlorination level: {err}")
            raise

    @property
    def icon(self) -> str:
        """Return the icon for the entity."""
        current_level = self.native_value or 0
        if current_level == 0:
            return "mdi:water-percent-alert"
        elif current_level < 30:
            return "mdi:water-percent"
        elif current_level < 70:
            return "mdi:water-percent"
        else:
            return "mdi:water-percent"

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return additional state attributes."""
        current_level = self.native_value or 0

        return {
            "chlorination_range": "0-100%",
            "read_component": 164,
            "write_component": 4,
            "current_level": current_level,
            "device_id": self._device_id,
        }


class FluidraChlorinatorPhSetpoint(CoordinatorEntity, NumberEntity):
    """Number entity for chlorinator pH setpoint control."""

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api,
        pool_id: str,
        device_id: str,
    ) -> None:
        """Initialize the pH setpoint control."""
        super().__init__(coordinator)
        self._api = api
        self._pool_id = pool_id
        self._device_id = device_id

        device_name = self.device_data.get("name") or f"Chlorinator {self._device_id}"

        self._attr_name = f"{device_name} pH Setpoint"
        self._attr_unique_id = f"fluidra_{self._device_id}_ph_setpoint"
        self._attr_mode = "slider"

        # pH range: 7.0-7.8 (typical pool values)
        self._attr_native_min_value = 7.0
        self._attr_native_max_value = 7.8
        self._attr_native_step = 0.1
        self._attr_native_unit_of_measurement = None
        self._attr_device_class = None

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
        device_name = self.device_data.get("name") or f"Chlorinator {self._device_id}"
        return {
            "identifiers": {(DOMAIN, self._device_id)},
            "name": device_name,
            "manufacturer": self.device_data.get("manufacturer", "Fluidra"),
            "model": self.device_data.get("model", "Chlorinator"),
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
        """Return the current pH setpoint from component 8."""
        components = self.device_data.get("components", {})
        component_8_data = components.get("8", {})
        # Component value is pH * 100 (e.g., 720 = 7.20)
        raw_value = component_8_data.get("reportedValue")

        if raw_value is None:
            return 7.2  # Default value

        try:
            return float(raw_value) / 100
        except (ValueError, TypeError):
            return 7.2

    async def async_set_native_value(self, value: float) -> None:
        """Set the pH setpoint via component 8."""
        # Convert pH value to API format (multiply by 100)
        int_value = int(value * 100)

        _LOGGER.info(f"Setting chlorinator {self._device_id} pH setpoint to {value} (API value: {int_value})")

        try:
            # Write to component 8
            success = await self._api.control_device_component(self._device_id, 8, int_value)

            if success:
                _LOGGER.info(f"✅ pH setpoint set to {value}")
                import asyncio
                await asyncio.sleep(2)
                await self.coordinator.async_request_refresh()
            else:
                _LOGGER.error(f"❌ Failed to set pH setpoint to {value}")

        except Exception as err:
            _LOGGER.error(f"Error setting pH setpoint: {err}")
            raise

    @property
    def icon(self) -> str:
        """Return the icon for the entity."""
        return "mdi:ph"

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return additional state attributes."""
        # Get current pH reading from component 172
        components = self.device_data.get("components", {})
        component_172_data = components.get("172", {})
        raw_reading = component_172_data.get("reportedValue")

        current_ph = None
        if raw_reading is not None:
            try:
                current_ph = float(raw_reading) / 100
            except (ValueError, TypeError):
                pass

        return {
            "ph_range": "6.8-7.6",
            "read_component": 172,
            "write_component": 8,
            "current_ph_reading": current_ph,
            "device_id": self._device_id,
        }


class FluidraChlorinatorOrpSetpoint(CoordinatorEntity, NumberEntity):
    """Number entity for chlorinator ORP/Redox setpoint control."""

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api,
        pool_id: str,
        device_id: str,
    ) -> None:
        """Initialize the ORP setpoint control."""
        super().__init__(coordinator)
        self._api = api
        self._pool_id = pool_id
        self._device_id = device_id

        device_name = self.device_data.get("name") or f"Chlorinator {self._device_id}"

        self._attr_name = f"{device_name} ORP Setpoint"
        self._attr_unique_id = f"fluidra_{self._device_id}_orp_setpoint"
        self._attr_mode = "slider"

        # ORP range: 600-850 mV (typical pool values)
        self._attr_native_min_value = 600
        self._attr_native_max_value = 850
        self._attr_native_step = 10
        self._attr_native_unit_of_measurement = "mV"
        self._attr_device_class = NumberDeviceClass.VOLTAGE

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
        device_name = self.device_data.get("name") or f"Chlorinator {self._device_id}"
        return {
            "identifiers": {(DOMAIN, self._device_id)},
            "name": device_name,
            "manufacturer": self.device_data.get("manufacturer", "Fluidra"),
            "model": self.device_data.get("model", "Chlorinator"),
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
        """Return the current ORP setpoint from component 11."""
        components = self.device_data.get("components", {})
        component_11_data = components.get("11", {})
        # Component value is in mV directly
        raw_value = component_11_data.get("reportedValue")

        if raw_value is None:
            return 700  # Default value

        return raw_value

    async def async_set_native_value(self, value: float) -> None:
        """Set the ORP setpoint via component 11."""
        int_value = int(value)

        _LOGGER.info(f"Setting chlorinator {self._device_id} ORP setpoint to {int_value} mV")

        try:
            # Write to component 11
            success = await self._api.control_device_component(self._device_id, 11, int_value)

            if success:
                _LOGGER.info(f"✅ ORP setpoint set to {int_value} mV")
                import asyncio
                await asyncio.sleep(2)
                await self.coordinator.async_request_refresh()
            else:
                _LOGGER.error(f"❌ Failed to set ORP setpoint to {int_value} mV")

        except Exception as err:
            _LOGGER.error(f"Error setting ORP setpoint: {err}")
            raise

    @property
    def icon(self) -> str:
        """Return the icon for the entity."""
        return "mdi:lightning-bolt"

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return additional state attributes."""
        # Get current ORP reading from component 177
        components = self.device_data.get("components", {})
        component_177_data = components.get("177", {})
        current_orp = component_177_data.get("reportedValue")

        return {
            "orp_range": "650-750 mV",
            "read_component": 177,
            "write_component": 11,
            "current_orp_reading": current_orp,
            "device_id": self._device_id,
        }


