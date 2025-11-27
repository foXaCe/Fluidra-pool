"""Number platform for Fluidra Pool integration."""

import logging
from typing import Any, Dict

from homeassistant.components.number import NumberDeviceClass, NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEVICE_TYPE_PUMP, DOMAIN
from .coordinator import FluidraDataUpdateCoordinator
from .device_registry import DeviceIdentifier

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

            if device_type == DEVICE_TYPE_PUMP:
                # Groupe Réglages - Contrôles de vitesse temporairement désactivés
                pass

            # LumiPlus Connect effect speed control
            if device_type == "light":
                entities.append(FluidraLightEffectSpeed(coordinator, coordinator.api, pool["id"], device_id))

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
        return self.coordinator.last_update_success and self.device_data.get("online", False)

    @property
    def native_value(self) -> float | None:
        """Return the current component value."""
        components = self.device_data.get("components", {})
        component_data = components.get(str(self._component_id), {})

        # Use desiredValue preferentially to show immediate UI feedback
        value = component_data.get("desiredValue", component_data.get("reportedValue", 0))

        return float(value)

    async def async_set_native_value(self, value: float) -> None:
        """Set the component value."""
        int_value = int(value)

        try:
            success = await self._api.set_component_value(self._device_id, self._component_id, int_value)

            if success:
                await self.coordinator.async_request_refresh()

        except Exception:
            raise

    @property
    def icon(self) -> str:
        """Return the icon for the entity."""
        current_speed = self.native_value or 0
        if current_speed == 0:
            return "mdi:pump-off"
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
        return self.coordinator.last_update_success and self.device_data.get("online", False)

    @property
    def native_value(self) -> float | None:
        """Return the current speed value from component 15."""
        components = self.device_data.get("components", {})
        component_15_data = components.get("15", {})
        # Use desiredValue preferentially to show immediate UI feedback
        current_value = component_15_data.get("desiredValue", component_15_data.get("reportedValue", 50))

        return float(current_value)

    async def async_set_native_value(self, value: float) -> None:
        """Set the speed percentage directly to component 15."""
        int_value = int(value)

        try:
            success = await self._api.set_component_value(self._device_id, 15, int_value)
            if not success:
                return

            await self.coordinator.async_request_refresh()

        except Exception:
            pass
            raise

    @property
    def icon(self) -> str:
        """Return the icon for the entity."""
        current_speed = self.native_value or 0
        if current_speed >= 85:
            return "mdi:speedometer"
        if current_speed >= 65:
            return "mdi:speedometer-medium"
        if current_speed >= 40:
            return "mdi:speedometer-slow"
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
        self._attr_native_step = 1  # UI step (actual value rounded to 10 in async_set_native_value)
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
        return self.coordinator.last_update_success and self.device_data.get("online", False)

    @property
    def native_value(self) -> float | None:
        """Return the current chlorination level from component 10."""
        components = self.device_data.get("components", {})
        component_10 = components.get("10", {})
        value = component_10.get("desiredValue", component_10.get("reportedValue", 0))
        return float(value)

    async def async_set_native_value(self, value: float) -> None:
        """Set chlorination level to component 10."""
        # Round to nearest multiple of 10 for CC24033907 compatibility
        int_value = round(value / 10) * 10

        # Optimistic update: Update coordinator data immediately for instant UI feedback
        components = self.device_data.get("components", {})
        if "10" not in components:
            components["10"] = {}
        components["10"]["desiredValue"] = int_value
        self.async_write_ha_state()

        try:
            success = await self._api.control_device_component(self._device_id, 10, int_value)
            if not success:
                pass
        except Exception:
            raise

    @property
    def icon(self) -> str:
        """Return the icon."""
        return "mdi:water-percent"

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return state attributes."""
        return {
            "component": 10,
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
        return self.coordinator.last_update_success and self.device_data.get("online", False)

    @property
    def native_value(self) -> float | None:
        """Return the current pH setpoint."""
        # Get component config dynamically
        ph_config = DeviceIdentifier.get_feature(self.device_data, "ph_setpoint", {"write": 8, "read": 172})

        # Handle both formats: int (simple) or dict (separate read/write)
        if isinstance(ph_config, dict):
            read_component = ph_config.get("read", ph_config.get("write", 8))
        else:
            read_component = ph_config

        components = self.device_data.get("components", {})
        component_data = components.get(str(read_component), {})
        # Component value is pH * 100 (e.g., 720 = 7.20)
        # Use desiredValue preferentially to show immediate UI feedback
        raw_value = component_data.get("desiredValue", component_data.get("reportedValue"))

        if raw_value is None:
            return 7.2  # Default value

        try:
            return float(raw_value) / 100
        except Exception:
            pass
            return 7.2

    async def async_set_native_value(self, value: float) -> None:
        """Set the pH setpoint."""
        # Convert pH value to API format (multiply by 100)
        int_value = int(value * 100)

        # Get component config dynamically
        ph_config = DeviceIdentifier.get_feature(self.device_data, "ph_setpoint", {"write": 8, "read": 172})

        # Handle both formats: int (simple) or dict (separate read/write)
        if isinstance(ph_config, dict):
            write_component = ph_config.get("write", 8)
            read_component = ph_config.get("read", ph_config.get("write", 8))
        else:
            write_component = ph_config
            read_component = ph_config

        # Optimistic update: Update coordinator data immediately for instant UI feedback
        components = self.device_data.get("components", {})
        if str(read_component) not in components:
            components[str(read_component)] = {}
        components[str(read_component)]["desiredValue"] = int_value
        self.async_write_ha_state()

        try:
            success = await self._api.control_device_component(self._device_id, write_component, int_value)

            if not success:
                pass

        except Exception:
            raise

    @property
    def icon(self) -> str:
        """Return the icon for the entity."""
        return "mdi:ph"

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return additional state attributes."""
        # Get component config dynamically
        ph_config = DeviceIdentifier.get_feature(self.device_data, "ph_setpoint", {"write": 8, "read": 172})

        # Handle both formats: int (simple) or dict (separate read/write)
        if isinstance(ph_config, dict):
            read_component = ph_config.get("read", ph_config.get("write", 8))
            write_component = ph_config.get("write", 8)
        else:
            read_component = ph_config
            write_component = ph_config

        # Get current pH reading
        components = self.device_data.get("components", {})
        component_data = components.get(str(read_component), {})
        raw_reading = component_data.get("reportedValue")

        current_ph = None
        if raw_reading is not None:
            try:
                current_ph = float(raw_reading) / 100
            except Exception:
                pass
                pass

        return {
            "ph_range": "6.8-7.6",
            "read_component": read_component,
            "write_component": write_component,
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
        return self.coordinator.last_update_success and self.device_data.get("online", False)

    @property
    def native_value(self) -> float | None:
        """Return the current ORP setpoint."""
        # Get component config dynamically
        orp_config = DeviceIdentifier.get_feature(self.device_data, "orp_setpoint", {"write": 11, "read": 177})

        # Handle both formats: int (simple) or dict (separate read/write)
        if isinstance(orp_config, dict):
            read_component = orp_config.get("read", orp_config.get("write", 11))
        else:
            read_component = orp_config

        components = self.device_data.get("components", {})
        component_data = components.get(str(read_component), {})
        # Use desiredValue preferentially to show immediate UI feedback
        raw_value = component_data.get("desiredValue", component_data.get("reportedValue"))

        return float(raw_value) if raw_value is not None else 700.0

    async def async_set_native_value(self, value: float) -> None:
        """Set the ORP setpoint."""
        int_value = int(value)

        # Get component config dynamically
        orp_config = DeviceIdentifier.get_feature(self.device_data, "orp_setpoint", {"write": 11, "read": 177})

        # Handle both formats: int (simple) or dict (separate read/write)
        if isinstance(orp_config, dict):
            write_component = orp_config.get("write", 11)
            read_component = orp_config.get("read", orp_config.get("write", 11))
        else:
            write_component = orp_config
            read_component = orp_config

        # Optimistic update: Update coordinator data immediately for instant UI feedback
        components = self.device_data.get("components", {})
        if str(read_component) not in components:
            components[str(read_component)] = {}
        components[str(read_component)]["desiredValue"] = int_value
        self.async_write_ha_state()

        try:
            success = await self._api.control_device_component(self._device_id, write_component, int_value)

            if not success:
                pass

        except Exception:
            raise

    @property
    def icon(self) -> str:
        """Return the icon for the entity."""
        return "mdi:lightning-bolt"

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return additional state attributes."""
        # Get component config dynamically
        orp_config = DeviceIdentifier.get_feature(self.device_data, "orp_setpoint", {"write": 11, "read": 177})

        # Handle both formats: int (simple) or dict (separate read/write)
        if isinstance(orp_config, dict):
            read_component = orp_config.get("read", orp_config.get("write", 11))
            write_component = orp_config.get("write", 11)
        else:
            read_component = orp_config
            write_component = orp_config

        # Get current ORP reading
        components = self.device_data.get("components", {})
        component_data = components.get(str(read_component), {})
        current_orp = component_data.get("reportedValue")

        return {
            "orp_range": "650-750 mV",
            "read_component": read_component,
            "write_component": write_component,
            "current_orp_reading": current_orp,
            "device_id": self._device_id,
        }


class FluidraLightEffectSpeed(CoordinatorEntity, NumberEntity):
    """Number entity for LumiPlus Connect effect speed (1-8)."""

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api,
        pool_id: str,
        device_id: str,
    ) -> None:
        """Initialize the effect speed control."""
        super().__init__(coordinator)
        self._api = api
        self._pool_id = pool_id
        self._device_id = device_id

        device_name = self.device_data.get("name") or f"Pool Light {self._device_id}"

        self._attr_name = f"{device_name} Effect Speed"
        self._attr_unique_id = f"fluidra_{self._device_id}_effect_speed"
        self._attr_mode = "slider"
        self._attr_native_min_value = 1
        self._attr_native_max_value = 8
        self._attr_native_step = 1
        self._attr_native_unit_of_measurement = None

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
        device_name = self.device_data.get("name") or f"Pool Light {self._device_id}"
        return {
            "identifiers": {(DOMAIN, self._device_id)},
            "name": device_name,
            "manufacturer": self.device_data.get("manufacturer", "Fluidra"),
            "model": self.device_data.get("model", "LumiPlus Connect"),
            "via_device": (DOMAIN, self._pool_id),
        }

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self.coordinator.last_update_success and self.device_data.get("online", False)

    @property
    def native_value(self) -> float | None:
        """Return the current effect speed from component 20."""
        components = self.device_data.get("components", {})
        component_20 = components.get("20", {})
        value = component_20.get("desiredValue", component_20.get("reportedValue", 1))
        return float(value) if value else 1.0

    async def async_set_native_value(self, value: float) -> None:
        """Set effect speed to component 20."""
        int_value = int(value)

        try:
            success = await self._api.set_component_value(self._device_id, 20, int_value)
            if success:
                await self.coordinator.async_request_refresh()
        except Exception:
            raise

    @property
    def icon(self) -> str:
        """Return the icon."""
        return "mdi:speedometer"

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return state attributes."""
        return {
            "component": 20,
            "device_id": self._device_id,
            "speed_range": "1-8",
        }
