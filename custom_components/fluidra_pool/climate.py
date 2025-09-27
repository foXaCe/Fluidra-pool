"""Climate platform for Fluidra Pool integration."""

import logging
from typing import Any, Optional, List

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
    HVACAction,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, DEVICE_TYPE_HEAT_PUMP
from .coordinator import FluidraDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


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
        pool_name = self.pool_data.get('name', 'Piscine')
        device_name = self.device_data.get('name', 'Pompe à chaleur')
        return f"{pool_name} {device_name}"

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
    def hvac_modes(self) -> List[HVACMode]:
        """Return the list of available hvac operation modes."""
        return [HVACMode.OFF, HVACMode.HEAT]

    @property
    def hvac_mode(self) -> HVACMode:
        """Return current hvac operation mode."""
        if self.device_data.get("is_heating", False):
            return HVACMode.HEAT
        return HVACMode.OFF

    @property
    def hvac_action(self) -> Optional[HVACAction]:
        """Return the current hvac action."""
        if self.device_data.get("is_heating", False):
            return HVACAction.HEATING
        return HVACAction.OFF

    @property
    def supported_features(self) -> int:
        """Return the list of supported features."""
        return ClimateEntityFeature.TARGET_TEMPERATURE

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

        except Exception as e:
            _LOGGER.error(f"❌ Error setting HVAC mode {hvac_mode}: {e}")

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

    @property
    def extra_state_attributes(self) -> dict:
        """Return extra state attributes."""
        attrs = {
            "device_type": "heat_pump",
            "device_id": self._device_id,
            "pool_id": self._pool_id,
            "model": self.device_data.get("model", "Heat Pump"),
            "family": self.device_data.get("family", ""),
            # API data
            "connectivity": self.device_data.get("connectivity", {}),
            "last_update": self.device_data.get("last_update"),
            # UI responsiveness indicators
            "pending_temperature": self._pending_temperature is not None,
            "action_timestamp": self._last_action_time
        }

        # Ajouter les informations de firmware si disponibles
        if "firmware_version" in self.device_data:
            attrs["firmware_version"] = self.device_data["firmware_version"]
        if "ip_address" in self.device_data:
            attrs["ip_address"] = self.device_data["ip_address"]

        return attrs