"""Climate platform for Fluidra Pool integration."""

import logging
from typing import Any, Optional, List

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
    HVACAction,
    PRESET_BOOST,
    PRESET_ECO,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, DEVICE_TYPE_HEAT_PUMP
from .coordinator import FluidraDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

# LG Heat Pump preset modes (mapped to component 14 values)
LG_PRESET_SMART_HEATING = "smart_heating"       # desiredValue: 0
LG_PRESET_SMART_COOLING = "smart_cooling"       # desiredValue: 1
LG_PRESET_SMART_HEAT_COOL = "smart_heat_cool"   # desiredValue: 2
LG_PRESET_BOOST_HEATING = "boost_heating"       # desiredValue: 3
LG_PRESET_SILENCE_HEATING = "silence_heating"   # desiredValue: 4
LG_PRESET_BOOST_COOLING = "boost_cooling"       # desiredValue: 5
LG_PRESET_SILENCE_COOLING = "silence_cooling"   # desiredValue: 6

LG_PRESET_MODES = [
    LG_PRESET_SMART_HEATING,
    LG_PRESET_SMART_COOLING,
    LG_PRESET_SMART_HEAT_COOL,
    LG_PRESET_BOOST_HEATING,
    LG_PRESET_SILENCE_HEATING,
    LG_PRESET_BOOST_COOLING,
    LG_PRESET_SILENCE_COOLING,
]

LG_MODE_TO_VALUE = {
    LG_PRESET_SMART_HEATING: 0,
    LG_PRESET_SMART_COOLING: 1,
    LG_PRESET_SMART_HEAT_COOL: 2,
    LG_PRESET_BOOST_HEATING: 3,
    LG_PRESET_SILENCE_HEATING: 4,
    LG_PRESET_BOOST_COOLING: 5,
    LG_PRESET_SILENCE_COOLING: 6,
}

LG_VALUE_TO_MODE = {v: k for k, v in LG_MODE_TO_VALUE.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Fluidra Pool climate entities."""
    coordinator: FluidraDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    entities = []

    pools = await coordinator.api.get_pools()
    for pool in pools:
        for device in pool["devices"]:
            device_type = device.get("type", "").lower()

            # Create climate entities for heat pumps only
            if "heat_pump" in device_type:
                device_id = device.get("device_id")
                if device_id:
                    entities.append(FluidraHeatPumpClimate(coordinator, coordinator.api, pool["id"], device_id))

    async_add_entities(entities)


class FluidraHeatPumpClimate(CoordinatorEntity, ClimateEntity):
    """Climate entity for Fluidra heat pumps."""

    def __init__(self, coordinator, api, pool_id: str, device_id: str):
        """Initialize the climate entity."""
        super().__init__(coordinator)
        self._api = api
        self._pool_id = pool_id
        self._device_id = device_id
        self._pending_temperature = None
        self._last_action_time = None
        # Optimistic state updates
        self._pending_preset_mode = None
        self._pending_hvac_mode = None
        self._last_preset_action_time = None
        self._last_hvac_action_time = None

    @property
    def device_data(self) -> dict:
        """Get device data from coordinator."""
        pool = self.coordinator.data.get(self._pool_id)
        if pool:
            for device in pool.get("devices", []):
                if device.get("device_id") == self._device_id:
                    return device
        return {}

    @property
    def pool_data(self) -> dict:
        """Get pool data from coordinator."""
        return self.coordinator.data.get(self._pool_id, {})

    @property
    def unique_id(self) -> str:
        """Return unique ID."""
        return f"{DOMAIN}_{self._pool_id}_{self._device_id}_climate"

    @property
    def name(self) -> str:
        """Return the name of the climate entity."""
        pool_name = self.pool_data.get('name', 'Pool')
        device_name = self.device_data.get('name', 'Heat Pump')
        return f"{pool_name} {device_name}"

    @property
    def translation_key(self) -> str:
        """Return the translation key."""
        return "heat_pump"

    @property
    def device_info(self) -> dict:
        """Return device info."""
        device_data = self.device_data
        device_name = device_data.get("name", f"Device {self._device_id}")
        return {
            "identifiers": {(DOMAIN, self._device_id)},
            "name": device_name,
            "manufacturer": device_data.get("manufacturer", "Fluidra"),
            "model": device_data.get("model", "Heat Pump"),
            "via_device": (DOMAIN, self._pool_id),
        }

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.last_update_success and self.device_data.get("online", False)

    @property
    def temperature_unit(self) -> str:
        """Return the unit of measurement."""
        return UnitOfTemperature.CELSIUS

    @property
    def current_temperature(self) -> Optional[float]:
        """Return the current temperature."""
        return self.device_data.get("current_temperature")

    @property
    def target_temperature(self) -> Optional[float]:
        """Return the target temperature."""
        # Si on a une température en attente, l'utiliser
        if self._pending_temperature is not None:
            import time
            # Effacer la température en attente après 15 secondes
            if time.time() - self._last_action_time > 15:
                self._pending_temperature = None
                self._last_action_time = None
            else:
                return self._pending_temperature

        return self.device_data.get("target_temperature")

    @property
    def min_temp(self) -> float:
        """Return the minimum temperature."""
        return 10.0

    @property
    def max_temp(self) -> float:
        """Return the maximum temperature."""
        return 40.0

    @property
    def target_temperature_step(self) -> float:
        """Return the supported step of target temperature."""
        return 1.0

    @property
    def supported_features(self) -> int:
        """Return the supported features."""
        features = ClimateEntityFeature.TARGET_TEMPERATURE

        # Force preset modes and HVAC controls for LG heat pumps
        if self._device_id.startswith("LG"):
            _LOGGER.info(f"🌡️ Adding preset mode and HVAC features for LG device {self._device_id}")
            features |= ClimateEntityFeature.PRESET_MODE
            features |= ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
        elif self.device_data.get("heat_pump_reported") is not None:
            # Si on peut détecter l'état via component 13, on active les modes ON/OFF
            features |= ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF

        return features

    @property
    def hvac_modes(self) -> List[HVACMode]:
        """Return the list of available hvac operation modes."""
        return [HVACMode.OFF, HVACMode.HEAT]

    @property
    def preset_modes(self) -> List[str]:
        """Return available preset modes for LG heat pumps."""
        # Force preset modes for LG heat pumps
        if self._device_id.startswith("LG"):
            _LOGGER.info(f"🌡️ Providing preset modes for LG device {self._device_id}: {LG_PRESET_MODES}")
            return LG_PRESET_MODES
        return []

    @property
    def preset_mode(self) -> str:
        """Return current preset mode for LG heat pumps."""
        if not self._device_id.startswith("LG"):
            return None

        # Check for pending optimistic preset mode first
        if self._pending_preset_mode is not None:
            import time
            # Clear pending mode after 10 seconds
            if time.time() - self._last_preset_action_time > 10:
                self._pending_preset_mode = None
                self._last_preset_action_time = None
            else:
                return self._pending_preset_mode

        # Get current mode from component 14 value
        device_data = self.device_data

        # Try to get mode from component states
        components = device_data.get("components", {})
        if isinstance(components, dict) and "14" in components:
            reported_value = components["14"].get("reportedValue")
            if reported_value is not None:
                return LG_VALUE_TO_MODE.get(reported_value, LG_PRESET_SMART_HEATING)

        # Fallback to smart heating
        return LG_PRESET_SMART_HEATING

    @property
    def hvac_mode(self) -> HVACMode:
        """Return current hvac operation mode."""

        # Check for pending optimistic HVAC mode first
        if self._pending_hvac_mode is not None:
            import time
            # Clear pending mode after 10 seconds
            if time.time() - self._last_hvac_action_time > 10:
                self._pending_hvac_mode = None
                self._last_hvac_action_time = None
            else:
                return self._pending_hvac_mode

        # Use same logic as switch for LG heat pumps
        device_data = self.device_data

        # For LG heat pumps, use multiple sources like the switch
        if self._device_id.startswith("LG"):
            # 1. heat_pump_reported from real-time polling
            heat_pump_reported = device_data.get("heat_pump_reported")
            if heat_pump_reported is not None:
                return HVACMode.HEAT if bool(heat_pump_reported) else HVACMode.OFF

            # 2. pump_reported (fallback for LG detected as pump)
            pump_reported = device_data.get("pump_reported")
            if pump_reported is not None:
                return HVACMode.HEAT if bool(pump_reported) else HVACMode.OFF

            # 3. is_running (base state for pumps)
            if device_data.get("is_running", False):
                return HVACMode.HEAT

            # 4. Fallback on is_heating
            return HVACMode.HEAT if device_data.get("is_heating", False) else HVACMode.OFF

        # Standard heat pump logic
        # 1. Priority: heat_pump_reported (specific heat pump state)
        heat_pump_reported = device_data.get("heat_pump_reported")
        if heat_pump_reported is not None:
            if heat_pump_reported:
                return HVACMode.HEAT
            else:
                return HVACMode.OFF

        # 2. Fallback: is_heating (compatibility)
        if device_data.get("is_heating", False):
            return HVACMode.HEAT

        # 3. Fallback: is_running (état général)
        if device_data.get("is_running", False):
            return HVACMode.HEAT

        return HVACMode.OFF

    @property
    def hvac_action(self) -> Optional[HVACAction]:
        """Return the current hvac action."""
        if self.device_data.get("is_heating", False):
            return HVACAction.HEATING
        return HVACAction.OFF


    @property
    def icon(self) -> str:
        """Return the icon of the climate entity."""
        if self.hvac_mode == HVACMode.HEAT:
            return "mdi:heat-pump"
        return "mdi:heat-pump-outline"

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new target hvac mode."""
        try:
            if hvac_mode == HVACMode.HEAT:
                _LOGGER.info(f"🚀 Turning on heat pump {self._device_id}")
                success = await self._api.start_pump(self._device_id)
            elif hvac_mode == HVACMode.OFF:
                _LOGGER.info(f"🚀 Turning off heat pump {self._device_id}")
                success = await self._api.stop_pump(self._device_id)
            else:
                _LOGGER.warning(f"Unsupported HVAC mode: {hvac_mode}")
                return

            if success:
                _LOGGER.info(f"✅ Successfully set HVAC mode to {hvac_mode}")
                await self.coordinator.async_request_refresh()
            else:
                _LOGGER.error(f"❌ Failed to set HVAC mode to {hvac_mode}")
                # Stocker l'erreur pour affichage utilisateur, mais ne pas modifier l'état
                # car l'état réel sera lu depuis le component 13
                device = self._api.get_device_by_id(self._device_id)
                if device:
                    device["last_control_error"] = f"Failed to set mode to {hvac_mode}"
                    device["permission_error"] = True

                # Demander un refresh pour obtenir l'état réel depuis l'API
                await self.coordinator.async_request_refresh()

        except Exception as e:
            _LOGGER.error(f"❌ Error setting HVAC mode {hvac_mode}: {e}")
            # Stocker l'erreur pour affichage utilisateur
            device = self._api.get_device_by_id(self._device_id)
            if device:
                device["last_control_error"] = str(e)
                device["permission_error"] = "403" in str(e) or "permission" in str(e).lower()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature."""
        temperature = kwargs.get("temperature")
        if temperature is None:
            return

        try:
            _LOGGER.info(f"🚀 Setting heat pump {self._device_id} temperature to {temperature}°C")

            # Mise à jour optimiste immédiate
            import time
            self._pending_temperature = temperature
            self._last_action_time = time.time()
            self.async_write_ha_state()

            # Ici il faudrait implémenter la méthode pour définir la température
            # via l'API Fluidra - à adapter selon les composants découverts
            success = await self._api.set_heat_pump_temperature(self._device_id, temperature)

            if success:
                _LOGGER.info(f"✅ Successfully set temperature to {temperature}°C")
                await self.coordinator.async_request_refresh()
                # Effacer la température en attente après confirmation
                self._pending_temperature = None
                self._last_action_time = None
            else:
                _LOGGER.error(f"❌ Failed to set temperature to {temperature}°C")
                # Annuler la température optimiste en cas d'échec
                self._pending_temperature = None
                self._last_action_time = None

        except Exception as e:
            _LOGGER.error(f"❌ Error setting temperature {temperature}°C: {e}")
            # Annuler la température optimiste en cas d'erreur
            self._pending_temperature = None
            self._last_action_time = None

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set new preset mode for LG heat pumps."""
        if not self._device_id.startswith("LG"):
            _LOGGER.warning(f"Preset modes not supported for device {self._device_id}")
            return

        if preset_mode not in LG_MODE_TO_VALUE:
            _LOGGER.warning(f"Unsupported preset mode: {preset_mode}")
            return

        try:
            mode_value = LG_MODE_TO_VALUE[preset_mode]
            _LOGGER.info(f"🚀 Setting heat pump {self._device_id} preset mode to {preset_mode} (value: {mode_value})")

            # Optimistic update - show immediately in UI
            import time
            self._pending_preset_mode = preset_mode
            self._last_preset_action_time = time.time()
            self.async_write_ha_state()

            success = await self._api.control_device_component(self._device_id, 14, mode_value)

            if success:
                _LOGGER.info(f"✅ Successfully set preset mode to {preset_mode}")
                # Clear optimistic state on success
                self._pending_preset_mode = None
                self._last_preset_action_time = None
                await self.coordinator.async_request_refresh()
            else:
                _LOGGER.error(f"❌ Failed to set preset mode to {preset_mode}")
                # Clear optimistic state on failure
                self._pending_preset_mode = None
                self._last_preset_action_time = None
                # Stocker l'erreur pour affichage utilisateur
                device = self._api.get_device_by_id(self._device_id)
                if device:
                    device["last_control_error"] = f"Failed to set preset mode to {preset_mode}"
                    device["permission_error"] = True

                # Demander un refresh pour obtenir l'état réel depuis l'API
                await self.coordinator.async_request_refresh()

        except Exception as e:
            _LOGGER.error(f"❌ Error setting preset mode {preset_mode}: {e}")
            # Clear optimistic state on error
            self._pending_preset_mode = None
            self._last_preset_action_time = None
            # Stocker l'erreur pour affichage utilisateur
            device = self._api.get_device_by_id(self._device_id)
            if device:
                device["last_control_error"] = str(e)
                device["permission_error"] = "403" in str(e) or "permission" in str(e).lower()

    @property
    def extra_state_attributes(self) -> dict:
        """Return extra state attributes."""
        device_data = self.device_data
        attrs = {
            "device_type": "heat_pump",
            "device_id": self._device_id,
            "pool_id": self._pool_id,
            "model": device_data.get("model", "Heat Pump"),
            "family": device_data.get("family", ""),
            # API data
            "connectivity": device_data.get("connectivity", {}),
            "last_update": device_data.get("last_update"),
            # UI responsiveness indicators
            "pending_temperature": self._pending_temperature is not None,
            "action_timestamp": self._last_action_time,
            # State sources pour debugging
            "heat_pump_reported": device_data.get("heat_pump_reported"),
            "is_heating": device_data.get("is_heating"),
            "is_running": device_data.get("is_running"),
            # Données brutes des composants
            "component_13_raw": device_data.get("component_13_data", {}).get("reportedValue"),
            "component_15_raw": device_data.get("component_15_speed"),
            "component_15_temperature": device_data.get("target_temperature"),
            # État de synchronisation
            "state_sync_working": device_data.get("heat_pump_reported") is not None,
            "control_working": not device_data.get("permission_error", False)
        }

        # Informations d'erreur pour feedback utilisateur
        if device_data.get("permission_error"):
            attrs["permission_error"] = True
            attrs["error_message"] = "Insufficient permissions to control this device"

        if device_data.get("last_control_error"):
            attrs["last_control_error"] = device_data["last_control_error"]

        # Ajouter les informations de firmware si disponibles
        if "firmware_version" in device_data:
            attrs["firmware_version"] = device_data["firmware_version"]
        if "ip_address" in device_data:
            attrs["ip_address"] = device_data["ip_address"]

        return attrs