"""Climate platform for Fluidra Pool integration."""

import logging
import time
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    Z550_MAX_TEMP,
    Z550_MIN_TEMP,
    Z550_MODE_AUTO,
    Z550_MODE_COOLING,
    Z550_MODE_HEATING,
    Z550_PRESET_MODES,
    Z550_PRESET_SMART,
    Z550_PRESET_TO_VALUE,
    Z550_STATE_COOLING,
    Z550_STATE_HEATING,
    Z550_STATE_IDLE,
    Z550_TEMP_STEP,
    Z550_VALUE_TO_PRESET,
    FluidraPoolConfigEntry,
)
from .device_registry import DeviceIdentifier

_LOGGER = logging.getLogger(__name__)

# LG Heat Pump preset modes (mapped to component 14 values)
LG_PRESET_SMART_HEATING = "smart_heating"  # desiredValue: 0
LG_PRESET_SMART_COOLING = "smart_cooling"  # desiredValue: 1
LG_PRESET_SMART_HEAT_COOL = "smart_heat_cool"  # desiredValue: 2
LG_PRESET_BOOST_HEATING = "boost_heating"  # desiredValue: 3
LG_PRESET_SILENCE_HEATING = "silence_heating"  # desiredValue: 4
LG_PRESET_BOOST_COOLING = "boost_cooling"  # desiredValue: 5
LG_PRESET_SILENCE_COOLING = "silence_cooling"  # desiredValue: 6

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
    config_entry: FluidraPoolConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Fluidra Pool climate entities."""
    coordinator = config_entry.runtime_data.coordinator

    entities = []

    # Use cached pools data instead of API call for faster startup
    pools = coordinator.api._pools or await coordinator.api.get_pools()
    for pool in pools:
        for device in pool["devices"]:
            device_id = device.get("device_id")

            # Create climate entities based on device registry
            if device_id and DeviceIdentifier.should_create_entity(device, "climate"):
                entities.append(FluidraHeatPumpClimate(coordinator, coordinator.api, pool["id"], device_id))

    async_add_entities(entities)


class FluidraHeatPumpClimate(CoordinatorEntity, ClimateEntity):
    """Climate entity for Fluidra heat pumps."""

    _attr_has_entity_name = True  # 🥉 OBLIGATOIRE (Bronze)

    def __init__(self, coordinator, api, pool_id: str, device_id: str):
        """Initialize the climate entity."""
        super().__init__(coordinator)
        self._api = api
        self._pool_id = pool_id
        self._device_id = device_id
        # Optimistic state management
        self._pending_temperature = None
        self._pending_preset_mode = None
        self._pending_hvac_mode = None
        self._is_updating = False  # Indicates an action is in progress
        # Timestamps for optimistic state timeout (5 second timeout)
        self._last_action_time: float | None = None
        self._last_preset_action_time: float | None = None
        self._last_hvac_action_time: float | None = None

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
    def unique_id(self) -> str:
        """Return unique ID."""
        return f"{DOMAIN}_{self._pool_id}_{self._device_id}_climate"

    @property
    def name(self) -> str:
        """Return the name of the climate entity."""
        pool_name = self.pool_data.get("name", "Pool")
        device_name = self.device_data.get("name", "Heat Pump")
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
    def current_temperature(self) -> float | None:
        """Return the current temperature (pool water temperature)."""
        # Afficher la température de l'eau de la piscine comme température actuelle
        return self.device_data.get("water_temperature")

    @property
    def target_temperature(self) -> float | None:
        """Return the target temperature."""
        actual_temp = self.device_data.get("target_temperature")

        # Return optimistic temperature while waiting for server confirmation
        if self._pending_temperature is not None:
            # Clear optimistic state if server confirmed the change
            if actual_temp == self._pending_temperature:
                self._pending_temperature = None
            else:
                return self._pending_temperature

        return actual_temp

    @property
    def min_temp(self) -> float:
        """Return the minimum temperature."""
        if DeviceIdentifier.has_feature(self.device_data, "z550_mode"):
            return Z550_MIN_TEMP
        return 10.0

    @property
    def max_temp(self) -> float:
        """Return the maximum temperature."""
        if DeviceIdentifier.has_feature(self.device_data, "z550_mode"):
            return Z550_MAX_TEMP
        return 40.0

    @property
    def target_temperature_step(self) -> float:
        """Return the supported step of target temperature."""
        if DeviceIdentifier.has_feature(self.device_data, "z550_mode"):
            return Z550_TEMP_STEP
        return 1.0

    @property
    def supported_features(self) -> int:
        """Return the supported features."""
        features = ClimateEntityFeature.TARGET_TEMPERATURE

        # Use device registry to determine features
        if DeviceIdentifier.has_feature(self.device_data, "preset_modes"):
            features |= ClimateEntityFeature.PRESET_MODE

        # Always enable HVAC controls for heat pumps
        features |= ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF

        return features

    @property
    def hvac_modes(self) -> list[HVACMode]:
        """Return the list of available hvac operation modes."""
        # Z550iQ+ supports heat/cool/auto
        if DeviceIdentifier.has_feature(self.device_data, "z550_mode"):
            return [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL, HVACMode.HEAT_COOL]
        return [HVACMode.OFF, HVACMode.HEAT]

    @property
    def preset_modes(self) -> list[str]:
        """Return available preset modes for heat pumps with this feature."""
        if DeviceIdentifier.has_feature(self.device_data, "z550_mode"):
            return Z550_PRESET_MODES
        if DeviceIdentifier.has_feature(self.device_data, "preset_modes"):
            return LG_PRESET_MODES
        return []

    @property
    def preset_mode(self) -> str:
        """Return current preset mode for heat pumps with this feature."""
        device_data = self.device_data

        # Check for pending optimistic preset mode first
        if self._pending_preset_mode is not None and self._last_preset_action_time is not None:
            # Clear pending mode after 5 seconds
            if time.time() - self._last_preset_action_time > 5:
                self._pending_preset_mode = None
                self._last_preset_action_time = None
            else:
                return self._pending_preset_mode

        # Z550iQ+ preset modes from component 17
        if DeviceIdentifier.has_feature(device_data, "z550_mode"):
            z550_preset = device_data.get("z550_preset_reported")
            if z550_preset is not None:
                return Z550_VALUE_TO_PRESET.get(z550_preset, Z550_PRESET_SMART)
            # Try to get from components
            components = device_data.get("components", {})
            if isinstance(components, dict) and "17" in components:
                reported_value = components["17"].get("reportedValue")
                if reported_value is not None:
                    return Z550_VALUE_TO_PRESET.get(reported_value, Z550_PRESET_SMART)
            return Z550_PRESET_SMART

        # LG preset modes
        if not DeviceIdentifier.has_feature(device_data, "preset_modes"):
            return None

        # Get current mode from component 14 value
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
        if self._pending_hvac_mode is not None and self._last_hvac_action_time is not None:
            # Clear pending mode after 5 seconds
            if time.time() - self._last_hvac_action_time > 5:
                self._pending_hvac_mode = None
                self._last_hvac_action_time = None
            else:
                return self._pending_hvac_mode

        # If we have a pending preset mode, the heat pump should stay HEAT
        if self._pending_preset_mode is not None:
            return HVACMode.HEAT

        device_data = self.device_data

        # Z550iQ+ specific mode handling
        if DeviceIdentifier.has_feature(device_data, "z550_mode"):
            # Check if heat pump is ON (component 21)
            heat_pump_reported = device_data.get("heat_pump_reported")
            if not heat_pump_reported:
                return HVACMode.OFF

            # Get mode from component 16: 0=heating, 1=cooling, 2=auto
            z550_mode = device_data.get("z550_mode_reported")
            if z550_mode == Z550_MODE_HEATING:
                return HVACMode.HEAT
            elif z550_mode == Z550_MODE_COOLING:
                return HVACMode.COOL
            elif z550_mode == Z550_MODE_AUTO:
                return HVACMode.HEAT_COOL
            # Default to HEAT if mode unknown but pump is ON
            return HVACMode.HEAT

        # For heat pumps with preset modes, use multiple sources like the switch
        if DeviceIdentifier.has_feature(device_data, "preset_modes"):
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
            return HVACMode.OFF

        # 2. Fallback: is_heating (compatibility)
        if device_data.get("is_heating", False):
            return HVACMode.HEAT

        # 3. Fallback: is_running (état général)
        if device_data.get("is_running", False):
            return HVACMode.HEAT

        return HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction | None:
        """Return the current hvac action."""
        device_data = self.device_data

        # Z550iQ+ uses component 61 for detailed state
        if DeviceIdentifier.has_feature(device_data, "z550_mode"):
            z550_state = device_data.get("z550_state_reported")
            if z550_state == Z550_STATE_HEATING:
                return HVACAction.HEATING
            elif z550_state == Z550_STATE_COOLING:
                return HVACAction.COOLING
            elif z550_state == Z550_STATE_IDLE:
                return HVACAction.IDLE
            # No flow or unknown state = OFF
            return HVACAction.OFF

        if device_data.get("is_heating", False):
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
            # Optimistic update - show immediately in UI

            self._pending_hvac_mode = hvac_mode
            self._last_hvac_action_time = time.time()
            self.async_write_ha_state()

            success = False

            # Z550iQ+ specific mode handling
            if DeviceIdentifier.has_feature(self.device_data, "z550_mode"):
                if hvac_mode == HVACMode.OFF:
                    # Turn OFF: component 21 = 0
                    success = await self._api.control_device_component(self._device_id, 21, 0)
                else:
                    # Turn ON first: component 21 = 1
                    success = await self._api.control_device_component(self._device_id, 21, 1)
                    if success:
                        # Set mode: component 16 (0=heating, 1=cooling, 2=auto)
                        if hvac_mode == HVACMode.HEAT:
                            success = await self._api.control_device_component(self._device_id, 16, Z550_MODE_HEATING)
                        elif hvac_mode == HVACMode.COOL:
                            success = await self._api.control_device_component(self._device_id, 16, Z550_MODE_COOLING)
                        elif hvac_mode == HVACMode.HEAT_COOL:
                            success = await self._api.control_device_component(self._device_id, 16, Z550_MODE_AUTO)
            else:
                # Standard heat pump (LG, etc.)
                if hvac_mode == HVACMode.HEAT:
                    success = await self._api.start_pump(self._device_id)
                elif hvac_mode == HVACMode.OFF:
                    success = await self._api.stop_pump(self._device_id)
                else:
                    # Clear optimistic state for unsupported modes
                    self._pending_hvac_mode = None
                    self._last_hvac_action_time = None
                    return

            if success:
                # Keep optimistic state - property will auto-clear after 5 seconds
                await self.coordinator.async_request_refresh()
            else:
                # Clear optimistic state on failure
                self._pending_hvac_mode = None
                self._last_hvac_action_time = None
                self.async_write_ha_state()
                # Stocker l'erreur pour affichage utilisateur
                device = self._api.get_device_by_id(self._device_id)
                if device:
                    device["last_control_error"] = f"Failed to set mode to {hvac_mode}"
                    device["permission_error"] = True
                await self.coordinator.async_request_refresh()

        except Exception as e:
            # Clear optimistic state on error
            self._pending_hvac_mode = None
            self._last_hvac_action_time = None
            self.async_write_ha_state()
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

        # Validate temperature is within bounds
        min_t = self.min_temp
        max_t = self.max_temp
        if temperature < min_t or temperature > max_t:
            raise ServiceValidationError(
                f"Temperature {temperature}°C is out of range ({min_t}°C - {max_t}°C)",
                translation_domain=DOMAIN,
                translation_key="temperature_out_of_range",
                translation_placeholders={
                    "temperature": str(temperature),
                    "min_temp": str(min_t),
                    "max_temp": str(max_t),
                },
            )

        try:
            # Mise à jour optimiste immédiate

            self._pending_temperature = temperature
            self._last_action_time = time.time()
            self.async_write_ha_state()

            # Ici il faudrait implémenter la méthode pour définir la température
            # via l'API Fluidra - à adapter selon les composants découverts
            success = await self._api.set_heat_pump_temperature(self._device_id, temperature)

            if success:
                # Keep optimistic state - property will auto-clear after 5 seconds
                await self.coordinator.async_request_refresh()
            else:
                # Annuler la température optimiste en cas d'échec
                self._pending_temperature = None
                self._last_action_time = None
                self.async_write_ha_state()

        except Exception as e:
            _LOGGER.error("Error setting temperature for %s: %s", self._device_id, e)
            # Annuler la température optimiste en cas d'erreur
            self._pending_temperature = None
            self._last_action_time = None
            self.async_write_ha_state()
            raise

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set new preset mode for heat pumps with this feature."""
        try:
            # Optimistic update - show immediately in UI

            self._pending_preset_mode = preset_mode
            self._last_preset_action_time = time.time()
            self.async_write_ha_state()

            success = False

            # Z550iQ+ uses component 17 for preset modes
            if DeviceIdentifier.has_feature(self.device_data, "z550_mode"):
                if preset_mode not in Z550_PRESET_TO_VALUE:
                    self._pending_preset_mode = None
                    self._last_preset_action_time = None
                    return
                mode_value = Z550_PRESET_TO_VALUE[preset_mode]
                success = await self._api.control_device_component(self._device_id, 17, mode_value)
            elif DeviceIdentifier.has_feature(self.device_data, "preset_modes"):
                # LG heat pumps use component 14
                if preset_mode not in LG_MODE_TO_VALUE:
                    self._pending_preset_mode = None
                    self._last_preset_action_time = None
                    return
                mode_value = LG_MODE_TO_VALUE[preset_mode]
                success = await self._api.control_device_component(self._device_id, 14, mode_value)
            else:
                self._pending_preset_mode = None
                self._last_preset_action_time = None
                return

            if success:
                # Keep optimistic state until coordinator refresh completes
                # The property will clear it after 5 seconds automatically
                await self.coordinator.async_request_refresh()
            else:
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
            "is_updating": self._is_updating,
            # State sources
            "heat_pump_reported": device_data.get("heat_pump_reported"),
            "is_heating": device_data.get("is_heating"),
            "is_running": device_data.get("is_running"),
            # Données brutes des composants
            "component_13_raw": device_data.get("component_13_data", {}).get("reportedValue"),
            "component_15_raw": device_data.get("component_15_speed"),
            "component_15_temperature": device_data.get("target_temperature"),
            # État de synchronisation
            "state_sync_working": device_data.get("heat_pump_reported") is not None,
            "control_working": not device_data.get("permission_error", False),
        }

        # Ajouter la température de l'eau de la piscine si disponible
        # La température de l'eau vient du component 19, 62 ou 65 du device
        water_temp = device_data.get("water_temperature")
        if water_temp is not None:
            attrs["water_temperature"] = water_temp

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

        # Z550iQ+ specific attributes
        if DeviceIdentifier.has_feature(device_data, "z550_mode"):
            # Air temperature
            air_temp = device_data.get("air_temperature")
            if air_temp is not None:
                attrs["air_temperature"] = air_temp

            # Mode (0=heating, 1=cooling, 2=auto)
            z550_mode = device_data.get("z550_mode_reported")
            if z550_mode is not None:
                mode_names = {0: "heating", 1: "cooling", 2: "auto"}
                attrs["z550_mode"] = mode_names.get(z550_mode, f"unknown ({z550_mode})")
                attrs["z550_mode_raw"] = z550_mode

            # State (0=idle, 2=heating, 3=cooling, 11=no flow)
            z550_state = device_data.get("z550_state_reported")
            if z550_state is not None:
                state_names = {0: "idle", 2: "heating", 3: "cooling", 11: "no_flow"}
                attrs["z550_state"] = state_names.get(z550_state, f"unknown ({z550_state})")
                attrs["z550_state_raw"] = z550_state

            # Preset mode (0=silence, 1=smart, 2=boost)
            z550_preset = device_data.get("z550_preset_reported")
            if z550_preset is not None:
                preset_names = {0: "silence", 1: "smart", 2: "boost"}
                attrs["z550_preset"] = preset_names.get(z550_preset, f"unknown ({z550_preset})")
                attrs["z550_preset_raw"] = z550_preset

            # Raw component values for debugging
            attrs["component_21_raw"] = device_data.get("components", {}).get("21", {}).get("reportedValue")
            attrs["component_16_raw"] = device_data.get("components", {}).get("16", {}).get("reportedValue")
            attrs["component_17_raw"] = device_data.get("components", {}).get("17", {}).get("reportedValue")
            attrs["component_37_raw"] = device_data.get("components", {}).get("37", {}).get("reportedValue")
            attrs["component_40_raw"] = device_data.get("components", {}).get("40", {}).get("reportedValue")
            attrs["component_61_raw"] = device_data.get("components", {}).get("61", {}).get("reportedValue")

        return attrs
