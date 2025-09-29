"""Switch platform for Fluidra Pool integration."""

import logging
from typing import Any, Optional

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import EntityCategory

from .const import DOMAIN, DEVICE_TYPE_PUMP, DEVICE_TYPE_HEAT_PUMP, DEVICE_TYPE_HEATER
from .coordinator import FluidraDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


def _is_eco_elyo_heat_pump(device: dict) -> bool:
    """Detect if device is an Eco Elyo heat pump based on device signature."""
    if not isinstance(device, dict):
        return False

    device_id = device.get("device_id", "")
    device_name = device.get("name", "").lower()
    family = device.get("family", "").lower()
    model = device.get("model", "").lower()

    # Check for specific Eco Elyo signatures
    eco_elyo_indicators = [
        # Device ID patterns
        device_id.startswith("LG"),  # LG24350023 pattern
        # Name/model indicators
        "eco" in device_name and "elyo" in device_name,
        "astralpool" in model,
        "eco elyo" in family,
    ]

    # Component 7 check with safe type handling
    try:
        components = device.get("components", [])
        if components:
            for comp in components:
                # Handle both dict and string components safely
                if isinstance(comp, dict) and comp.get("id") == 7:
                    reported_value = str(comp.get("reportedValue", ""))
                    if "BXWAA" in reported_value:
                        eco_elyo_indicators.append(True)
                        break
    except (AttributeError, TypeError):
        # If component analysis fails, continue with other indicators
        pass

    return any(eco_elyo_indicators)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Fluidra Pool switch entities."""
    coordinator: FluidraDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    entities = []

    pools = await coordinator.api.get_pools()
    for pool in pools:
        for device in pool["devices"]:
            device_type = device.get("type", "").lower()
            device_id = device.get("device_id")

            if not device_id:
                continue

            # Check if this is an Eco Elyo heat pump (special case)
            is_eco_elyo = _is_eco_elyo_heat_pump(device)

            if is_eco_elyo:
                # Create heat pump switch instead of pump switches
                entities.append(FluidraHeatPumpSwitch(coordinator, coordinator.api, pool["id"], device_id))
                # Skip auto mode for LG heat pumps (they have native smart modes)
                # Only create auto mode switch for non-LG heat pumps
                if not device_id.startswith("LG"):
                    if device.get("auto_mode_enabled") is not None or device.get("auto_reported") is not None:
                        entities.append(FluidraAutoModeSwitch(coordinator, coordinator.api, pool["id"], device_id))
                else:
                    pass  # Skip auto mode for LG heat pumps
                # Skip pump and scheduler creation for Eco Elyo
                continue

            # Create pump switches (for regular pumps)
            if "pump" in device_type:
                # Switch pour pompe ON/OFF
                entities.append(FluidraPumpSwitch(coordinator, coordinator.api, pool["id"], device_id))
                # Switch pour mode auto ON/OFF
                entities.append(FluidraAutoModeSwitch(coordinator, coordinator.api, pool["id"], device_id))

            # Create heat pump switches (for devices already detected as heat_pump)
            elif "heat_pump" in device_type:
                # Switch pour pompe à chaleur ON/OFF
                entities.append(FluidraHeatPumpSwitch(coordinator, coordinator.api, pool["id"], device_id))

            # Create heater switches
            elif "heater" in device_type or "heat" in device_type:
                entities.append(FluidraHeaterSwitch(coordinator, pool, device))

            # Create schedule enable switches ONLY for regular pumps (not Eco Elyo)
            if "pump" in device_type and not is_eco_elyo:
                # Create switches for the actual 8 schedulers found
                for schedule_id in ["1", "2", "3", "4", "5", "6", "7", "8"]:
                    entities.append(FluidraScheduleEnableSwitch(
                        coordinator,
                        coordinator.api,
                        pool["id"],
                        device_id,
                        schedule_id
                    ))

    async_add_entities(entities)


class FluidraPoolSwitchEntity(CoordinatorEntity, SwitchEntity):
    """Base class for Fluidra Pool switch entities."""

    def __init__(self, coordinator, api, pool_id: str, device_id: str):
        """Initialize the switch."""
        super().__init__(coordinator)
        self._api = api
        self._pool_id = pool_id
        self._device_id = device_id
        self._pending_state = None
        self._last_action_time = None

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
        return f"{DOMAIN}_{self._pool_id}_{self._device_id}"

    @property
    def device_info(self) -> dict:
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

    @property
    def assumed_state(self) -> bool:
        """Return True if state is assumed during command execution."""
        return self._pending_state is not None

    def _set_pending_state(self, state: bool) -> None:
        """Set pending state for optimistic UI updates."""
        import time
        self._pending_state = state
        self._last_action_time = time.time()
        self.async_write_ha_state()

    def _clear_pending_state(self) -> None:
        """Clear pending state after API confirmation."""
        self._pending_state = None
        self._last_action_time = None
        self.async_write_ha_state()


    async def _refresh_device_state(self) -> None:
        """Refresh device state by polling real API components."""
        try:
            _LOGGER.info(f"🔄 Refreshing real-time state for device {self._device_id}")

            # Rafraîchir les états des composants critiques
            # Component 9 (on/off)
            pump_state = await self._api.get_device_component_state(self._device_id, 9)
            if pump_state:
                reported_value = pump_state.get("reportedValue", 0)
                device = self._api.get_device_by_id(self._device_id)
                if device:
                    device["is_running"] = bool(reported_value)
                    device["pump_reported"] = reported_value
                    _LOGGER.info(f"✅ Updated device {self._device_id} is_running: {bool(reported_value)}")

            # Component 10 (auto mode) - AJOUTÉ
            auto_state = await self._api.get_device_component_state(self._device_id, 10)
            if auto_state:
                auto_reported = auto_state.get("reportedValue", 0)
                device = self._api.get_device_by_id(self._device_id)
                if device:
                    device["auto_mode_enabled"] = bool(auto_reported)
                    device["auto_reported"] = auto_reported
                    _LOGGER.info(f"✅ Updated device {self._device_id} auto_mode: {bool(auto_reported)}")

            # Component 11 (speed level)
            speed_state = await self._api.get_device_component_state(self._device_id, 11)
            if speed_state:
                speed_level = speed_state.get("reportedValue", 0)
                device = self._api.get_device_by_id(self._device_id)
                if device:
                    # Seulement si la pompe tourne
                    if device.get("is_running", False):
                        speed_percent = self._api.speed_percentages.get(speed_level, 45)
                        device["speed_percent"] = speed_percent
                        _LOGGER.info(f"✅ Updated device {self._device_id} speed: {speed_percent}%")
                    else:
                        device["speed_percent"] = 0
                        _LOGGER.info(f"✅ Device {self._device_id} stopped, speed set to 0%")

        except Exception as e:
            _LOGGER.error(f"❌ Error refreshing device state: {e}")


class FluidraPumpSwitch(FluidraPoolSwitchEntity):
    """Switch for controlling pool pumps (ON/OFF)."""

    def __init__(self, coordinator, api, pool_id: str, device_id: str):
        """Initialize the switch."""
        super().__init__(coordinator, api, pool_id, device_id)

    @property
    def name(self) -> str:
        """Return the name of the switch."""
        pool_name = self.pool_data.get('name', 'Pool')
        device_name = self.device_data.get('name', 'Pump')
        return f"{pool_name} {device_name}"

    @property
    def translation_key(self) -> str:
        """Return the translation key."""
        return "pump"

    @property
    def icon(self) -> str:
        """Return the icon of the switch."""
        if self.is_on:
            return "mdi:pump"
        return "mdi:pump-off"

    @property
    def entity_picture(self) -> str:
        """Return entity picture for better visual representation."""
        return None

    @property
    def device_class(self) -> str:
        """Return device class for proper styling."""
        return "switch"

    @property
    def is_on(self) -> bool:
        """Return true if the pump is on using optimistic UI or real-time reported value."""
        # Si on a un état en attente, l'utiliser pour la réactivité
        if self._pending_state is not None:
            import time
            # Effacer l'état en attente après 10 secondes de sécurité
            if time.time() - self._last_action_time > 10:
                self._clear_pending_state()
            else:
                return self._pending_state

        # Utiliser reportedValue du polling temps réel si disponible
        pump_reported = self.device_data.get("pump_reported")
        if pump_reported is not None:
            return bool(pump_reported)
        # Fallback sur is_running pour compatibilité
        return self.device_data.get("is_running", False)


    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the pump on using discovered API with optimistic UI."""
        try:
            _LOGGER.info(f"🚀 Starting pump {self._device_id}")

            # Mise à jour optimiste immédiate pour la réactivité
            self._set_pending_state(True)

            success = await self._api.start_pump(self._device_id)
            if success:
                _LOGGER.info(f"✅ Successfully started pump {self._device_id}")
                # Attendre que l'API se synchronise
                import asyncio
                await asyncio.sleep(2)
                # Récupérer l'état réel immédiatement
                await self._refresh_device_state()
                await self.coordinator.async_request_refresh()
                # Effacer l'état en attente après confirmation
                self._clear_pending_state()
            else:
                _LOGGER.error(f"❌ Failed to start pump {self._device_id}")
                # Annuler l'état optimiste en cas d'échec
                self._clear_pending_state()
        except Exception as e:
            _LOGGER.error(f"❌ Error starting pump {self._device_id}: {e}")
            # Annuler l'état optimiste en cas d'erreur
            self._clear_pending_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the pump off using discovered API with optimistic UI."""
        try:
            _LOGGER.info(f"🚀 Stopping pump {self._device_id}")

            # Mise à jour optimiste immédiate pour la réactivité
            self._set_pending_state(False)

            success = await self._api.stop_pump(self._device_id)
            if success:
                _LOGGER.info(f"✅ Successfully stopped pump {self._device_id}")
                # Attendre que l'API se synchronise
                import asyncio
                await asyncio.sleep(2)
                # Récupérer l'état réel immédiatement
                await self._refresh_device_state()
                await self.coordinator.async_request_refresh()
                # Effacer l'état en attente après confirmation
                self._clear_pending_state()
            else:
                _LOGGER.error(f"❌ Failed to stop pump {self._device_id}")
                # Annuler l'état optimiste en cas d'échec
                self._clear_pending_state()
        except Exception as e:
            _LOGGER.error(f"❌ Error stopping pump {self._device_id}: {e}")
            # Annuler l'état optimiste en cas d'erreur
            self._clear_pending_state()

    @property
    def extra_state_attributes(self) -> dict:
        """Return extra state attributes."""
        attrs = {
            "component_id": 9,
            "operation": "pump_control",
            "speed_percent": self.device_data.get("speed_percent", 0),
            "operation_mode": self.device_data.get("operation_mode", 0),
            # Real-time API data from reverse engineering
            "pump_reported": self.device_data.get("pump_reported"),
            "pump_desired": self.device_data.get("pump_desired"),
            "connectivity": self.device_data.get("connectivity", {}),
            "last_update": self.device_data.get("last_update"),
            # UI responsiveness indicators
            "pending_action": self._pending_state is not None,
            "action_timestamp": self._last_action_time
        }
        return attrs


class FluidraHeatPumpSwitch(FluidraPoolSwitchEntity):
    """Switch for controlling pool heat pumps (Astralpool Eco Elyo, etc.)."""

    def __init__(self, coordinator, api, pool_id: str, device_id: str):
        """Initialize the switch."""
        super().__init__(coordinator, api, pool_id, device_id)
        # Détecter si c'est un Eco Elyo pour un meilleur nommage
        self._is_eco_elyo = False  # Sera mis à jour dynamiquement

    @property
    def name(self) -> str:
        """Return the name of the switch."""
        pool_name = self.pool_data.get('name', 'Pool')
        device_name = self.device_data.get('name', 'Heat Pump')

        # Vérifier si c'est un Eco Elyo pour un nom plus spécifique
        if _is_eco_elyo_heat_pump(self.device_data):
            return f"{pool_name} Eco Elyo"

        return f"{pool_name} {device_name}"

    @property
    def translation_key(self) -> str:
        """Return the translation key."""
        return "heat_pump"

    @property
    def icon(self) -> str:
        """Return the icon of the switch."""
        if self.is_on:
            return "mdi:heat-pump"
        return "mdi:heat-pump-outline"

    @property
    def entity_picture(self) -> str:
        """Return entity picture for better visual representation."""
        return None

    @property
    def device_class(self) -> str:
        """Return device class for proper styling."""
        return "switch"

    @property
    def is_on(self) -> bool:
        """Return true if the heat pump is on using optimistic UI or real-time reported value."""
        # Si on a un état en attente, l'utiliser pour la réactivité
        if self._pending_state is not None:
            import time
            # Effacer l'état en attente après 10 secondes de sécurité
            if time.time() - self._last_action_time > 10:
                self._clear_pending_state()
            else:
                return self._pending_state

        # Pour l'Eco Elyo, utiliser plusieurs sources de données
        # 1. heat_pump_reported du polling temps réel
        heat_pump_reported = self.device_data.get("heat_pump_reported")
        if heat_pump_reported is not None:
            return bool(heat_pump_reported)

        # 2. pump_reported (fallback pour Eco Elyo détecté comme pompe)
        pump_reported = self.device_data.get("pump_reported")
        if pump_reported is not None:
            return bool(pump_reported)

        # 3. is_running (état de base pour pompes)
        if self.device_data.get("is_running", False):
            return True

        # 4. Fallback sur is_heating
        return self.device_data.get("is_heating", False)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the heat pump on using discovered API with optimistic UI."""
        try:
            _LOGGER.info(f"🚀 Starting heat pump {self._device_id}")

            # Mise à jour optimiste immédiate pour la réactivité
            self._set_pending_state(True)

            # Tenter d'utiliser le component 9 pour les pompes à chaleur
            success = await self._api.start_pump(self._device_id)
            if success:
                _LOGGER.info(f"✅ Successfully started heat pump {self._device_id}")
                # Attendre que l'API se synchronise
                import asyncio
                await asyncio.sleep(2)
                # Récupérer l'état réel immédiatement
                await self._refresh_heat_pump_state()
                await self.coordinator.async_request_refresh()
                # Effacer l'état en attente après confirmation
                self._clear_pending_state()
            else:
                _LOGGER.error(f"❌ Failed to start heat pump {self._device_id}")
                # Annuler l'état optimiste en cas d'échec
                self._clear_pending_state()
        except Exception as e:
            _LOGGER.error(f"❌ Error starting heat pump {self._device_id}: {e}")
            # Annuler l'état optimiste en cas d'erreur
            self._clear_pending_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the heat pump off using discovered API with optimistic UI."""
        try:
            _LOGGER.info(f"🚀 Stopping heat pump {self._device_id}")

            # Mise à jour optimiste immédiate pour la réactivité
            self._set_pending_state(False)

            success = await self._api.stop_pump(self._device_id)
            if success:
                _LOGGER.info(f"✅ Successfully stopped heat pump {self._device_id}")
                # Attendre que l'API se synchronise
                import asyncio
                await asyncio.sleep(2)
                # Récupérer l'état réel immédiatement
                await self._refresh_heat_pump_state()
                await self.coordinator.async_request_refresh()
                # Effacer l'état en attente après confirmation
                self._clear_pending_state()
            else:
                _LOGGER.error(f"❌ Failed to stop heat pump {self._device_id}")
                # Annuler l'état optimiste en cas d'échec
                self._clear_pending_state()
        except Exception as e:
            _LOGGER.error(f"❌ Error stopping heat pump {self._device_id}: {e}")
            # Annuler l'état optimiste en cas d'erreur
            self._clear_pending_state()

    async def _refresh_heat_pump_state(self) -> None:
        """Refresh heat pump state by polling real API components."""
        try:
            _LOGGER.info(f"🔄 Refreshing real-time state for heat pump {self._device_id}")

            # Component 9 (on/off) - standard pour pompes et pompes à chaleur
            heat_pump_state = await self._api.get_device_component_state(self._device_id, 9)
            if heat_pump_state:
                reported_value = heat_pump_state.get("reportedValue", 0)
                device = self._api.get_device_by_id(self._device_id)
                if device:
                    device["is_heating"] = bool(reported_value)
                    device["heat_pump_reported"] = reported_value
                    _LOGGER.info(f"✅ Updated heat pump {self._device_id} is_heating: {bool(reported_value)}")

        except Exception as e:
            _LOGGER.error(f"❌ Error refreshing heat pump state: {e}")

    @property
    def extra_state_attributes(self) -> dict:
        """Return extra state attributes."""
        attrs = {
            "component_id": 9,
            "operation": "heat_pump_control",
            "device_type": "heat_pump",
            # Real-time API data from reverse engineering
            "heat_pump_reported": self.device_data.get("heat_pump_reported"),
            "heat_pump_desired": self.device_data.get("heat_pump_desired"),
            "connectivity": self.device_data.get("connectivity", {}),
            "last_update": self.device_data.get("last_update"),
            # UI responsiveness indicators
            "pending_action": self._pending_state is not None,
            "action_timestamp": self._last_action_time
        }

        # Ajouter les données de température si disponibles
        if "current_temperature" in self.device_data:
            attrs["current_temperature"] = self.device_data["current_temperature"]
        if "target_temperature" in self.device_data:
            attrs["target_temperature"] = self.device_data["target_temperature"]

        return attrs


class FluidraHeaterSwitch(FluidraPoolSwitchEntity):
    """Switch for controlling pool heaters."""

    @property
    def name(self) -> str:
        """Return the name of the switch."""
        device_name = self.device_data.get('name', f'Device {self._device_id}')
        pool_name = self.pool_data.get('name', 'Pool')
        return f"{pool_name} {device_name}"

    @property
    def translation_key(self) -> str:
        """Return the translation key."""
        return "heater"

    @property
    def icon(self) -> str:
        """Return the icon of the switch."""
        if self.is_on:
            return "mdi:heat-wave"
        return "mdi:snowflake"

    @property
    def entity_picture(self) -> str:
        """Return entity picture for better visual representation."""
        return None

    @property
    def device_class(self) -> str:
        """Return device class for proper styling."""
        return "switch"

    @property
    def is_on(self) -> bool:
        """Return true if the heater is on."""
        return self.device_data.get("is_heating", False)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the heater on."""
        device = self._api.get_device_by_id(self._device_id)
        if device and hasattr(device, "turn_on"):
            await device.turn_on()
            await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the heater off."""
        device = self._api.get_device_by_id(self._device_id)
        if device and hasattr(device, "turn_off"):
            await device.turn_off()
            await self.coordinator.async_request_refresh()

    @property
    def extra_state_attributes(self) -> dict:
        """Return extra state attributes."""
        attrs = {}
        if "current_temperature" in self.device_data:
            attrs["current_temperature"] = self.device_data["current_temperature"]
        if "target_temperature" in self.device_data:
            attrs["target_temperature"] = self.device_data["target_temperature"]
        return attrs





class FluidraAutoModeSwitch(FluidraPoolSwitchEntity):
    """Switch for controlling pump auto mode (ON/OFF)."""

    def __init__(self, coordinator, api, pool_id: str, device_id: str):
        """Initialize the switch."""
        super().__init__(coordinator, api, pool_id, device_id)

    @property
    def name(self) -> str:
        """Return the name of the switch."""
        pool_name = self.pool_data.get('name', 'Pool')
        device_name = self.device_data.get('name', 'Pump')
        return f"{pool_name} {device_name} Auto Mode"

    @property
    def translation_key(self) -> str:
        """Return the translation key."""
        return "auto_mode"

    @property
    def unique_id(self) -> str:
        """Return unique ID."""
        return f"{DOMAIN}_{self._pool_id}_{self._device_id}_auto_mode"

    @property
    def icon(self) -> str:
        """Return the icon of the switch."""
        if self.is_on:
            return "mdi:auto-mode"
        return "mdi:autorenew-off"

    @property
    def entity_picture(self) -> str:
        """Return entity picture for better visual representation."""
        return None

    @property
    def device_class(self) -> str:
        """Return device class for proper styling."""
        return "switch"

    @property
    def is_on(self) -> bool:
        """Return true if auto mode is on using optimistic UI or real-time reported value."""
        # Si on a un état en attente, l'utiliser pour la réactivité
        if self._pending_state is not None:
            import time
            # Effacer l'état en attente après 10 secondes de sécurité
            if time.time() - self._last_action_time > 10:
                self._clear_pending_state()
            else:
                return self._pending_state

        # Utiliser reportedValue du polling temps réel si disponible
        auto_reported = self.device_data.get("auto_reported")
        if auto_reported is not None:
            return bool(auto_reported)
        # Fallback sur auto_mode_enabled pour compatibilité
        return self.device_data.get("auto_mode_enabled", False)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn auto mode on using discovered Component 10 with optimistic UI."""
        try:
            _LOGGER.info(f"🚀 Enabling auto mode for {self._device_id}")

            # Mise à jour optimiste immédiate pour la réactivité
            self._set_pending_state(True)

            success = await self._api.enable_auto_mode(self._device_id)
            if success:
                _LOGGER.info(f"✅ Successfully enabled auto mode for {self._device_id}")
                # Attendre que l'API se synchronise
                import asyncio
                await asyncio.sleep(2)
                # Récupérer l'état réel immédiatement
                await self._refresh_device_state()
                await self.coordinator.async_request_refresh()
                # Effacer l'état en attente après confirmation
                self._clear_pending_state()
            else:
                _LOGGER.error(f"❌ Failed to enable auto mode for {self._device_id}")
                # Annuler l'état optimiste en cas d'échec
                self._clear_pending_state()
        except Exception as e:
            _LOGGER.error(f"❌ Error enabling auto mode {self._device_id}: {e}")
            # Annuler l'état optimiste en cas d'erreur
            self._clear_pending_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn auto mode off using discovered Component 10 with optimistic UI."""
        try:
            _LOGGER.info(f"🚀 Disabling auto mode for {self._device_id}")

            # Mise à jour optimiste immédiate pour la réactivité
            self._set_pending_state(False)

            success = await self._api.disable_auto_mode(self._device_id)
            if success:
                _LOGGER.info(f"✅ Successfully disabled auto mode for {self._device_id}")
                # Attendre que l'API se synchronise
                import asyncio
                await asyncio.sleep(2)
                # Récupérer l'état réel immédiatement
                await self._refresh_device_state()
                await self.coordinator.async_request_refresh()
                # Effacer l'état en attente après confirmation
                self._clear_pending_state()
            else:
                _LOGGER.error(f"❌ Failed to disable auto mode for {self._device_id}")
                # Annuler l'état optimiste en cas d'échec
                self._clear_pending_state()
        except Exception as e:
            _LOGGER.error(f"❌ Error disabling auto mode {self._device_id}: {e}")
            # Annuler l'état optimiste en cas d'erreur
            self._clear_pending_state()

    @property
    def extra_state_attributes(self) -> dict:
        """Return extra state attributes."""
        return {
            "component_id": 10,
            "operation": "auto_mode_control",
            "function": "Mode automatique/programmé",
            # Real-time API data from reverse engineering
            "auto_reported": self.device_data.get("auto_reported"),
            "auto_desired": self.device_data.get("auto_desired"),
            "connectivity": self.device_data.get("connectivity", {}),
            "last_update": self.device_data.get("last_update"),
            # UI responsiveness indicators
            "pending_action": self._pending_state is not None,
            "action_timestamp": self._last_action_time
        }


class FluidraScheduleEnableSwitch(FluidraPoolSwitchEntity):
    """Switch for enabling/disabling existing schedules."""

    def __init__(self, coordinator, api, pool_id: str, device_id: str, schedule_id: str):
        """Initialize the switch."""
        super().__init__(coordinator, api, pool_id, device_id)
        self._schedule_id = schedule_id

        device_name = self.device_data.get("name") or f"E30iQ Pump {self._device_id}"
        self._attr_translation_key = "schedule_enable"
        self._attr_translation_placeholders = {"schedule_id": schedule_id}
        self._attr_unique_id = f"fluidra_{self._device_id}_schedule_{schedule_id}_enabled"
        self._attr_entity_category = EntityCategory.CONFIG


    @property
    def unique_id(self) -> str:
        """Return unique ID."""
        return self._attr_unique_id

    @property
    def icon(self) -> str:
        """Return the icon of the switch."""
        if self.is_on:
            return "mdi:calendar-clock"
        return "mdi:calendar-outline"

    @property
    def entity_picture(self) -> str:
        """Return entity picture for better visual representation."""
        return None

    @property
    def device_class(self) -> str:
        """Return device class for proper styling."""
        return "switch"

    def _get_schedule_data(self) -> Optional[dict]:
        """Get schedule data from coordinator."""
        try:
            # Get schedules from device data like the sensor does
            device_data = self.device_data
            _LOGGER.debug(f"[{self._schedule_id}] Device data keys: {list(device_data.keys())}")

            if "schedule_data" in device_data:
                schedules = device_data["schedule_data"]
                _LOGGER.debug(f"[{self._schedule_id}] Found {len(schedules)} schedules in device data")

                for schedule in schedules:
                    schedule_id = schedule.get("id")
                    _LOGGER.debug(f"[{self._schedule_id}] Checking schedule with id: {schedule_id} (type: {type(schedule_id)})")
                    # Compare both as string and int to handle type mismatch
                    if str(schedule_id) == str(self._schedule_id):
                        _LOGGER.info(f"[{self._schedule_id}] ✅ Found matching schedule!")
                        return schedule

                _LOGGER.warning(f"[{self._schedule_id}] ❌ Schedule not found in {len(schedules)} schedules")
                _LOGGER.warning(f"[{self._schedule_id}] Available schedule IDs:")
                for i, schedule in enumerate(schedules):
                    schedule_id = schedule.get('id')
                    enabled = schedule.get('enabled', False)
                    _LOGGER.warning(f"[{self._schedule_id}]   {i}: id='{schedule_id}', enabled={enabled}")
            else:
                _LOGGER.warning(f"[{self._schedule_id}] ❌ No 'schedule_data' key in device data")

        except Exception as e:
            _LOGGER.error(f"[{self._schedule_id}] Error getting schedule data: {e}")
        return None

    @property
    def available(self) -> bool:
        """Return True if the schedule exists."""
        result = self._get_schedule_data() is not None
        _LOGGER.debug(f"[{self._schedule_id}] Switch available: {result}")
        return result

    @property
    def is_on(self) -> bool:
        """Return true if the schedule is enabled using optimistic UI."""
        # Si on a un état en attente, l'utiliser pour la réactivité
        if self._pending_state is not None:
            import time
            # Effacer l'état en attente après 10 secondes de sécurité
            if time.time() - self._last_action_time > 10:
                self._clear_pending_state()
            else:
                return self._pending_state

        schedule = self._get_schedule_data()
        if schedule:
            return schedule.get("enabled", False)
        return False

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the schedule using exact mobile app format with optimistic UI."""
        _LOGGER.info(f"[{self._schedule_id}] 🔄 Attempting to enable schedule...")
        try:
            # Mise à jour optimiste immédiate pour la réactivité
            self._set_pending_state(True)
            # Get all current schedule data
            device_data = self.device_data
            if "schedule_data" not in device_data:
                _LOGGER.error(f"[{self._schedule_id}] No schedule data found for device {self._device_id}")
                return

            current_schedules = device_data["schedule_data"]
            if not current_schedules:
                _LOGGER.error(f"[{self._schedule_id}] No schedules found for device {self._device_id}")
                return

            # Create complete schedule list with EXACT format from mobile app
            updated_schedules = []
            for sched in current_schedules:
                # Convert cron format 0,1,2,3,4,5,6 to 1,2,3,4,5,6,7 for mobile app
                start_time = self._convert_cron_days(sched.get("startTime", ""))
                end_time = self._convert_cron_days(sched.get("endTime", ""))

                scheduler = {
                    "id": sched.get("id"),
                    "groupId": sched.get("id"),  # Mobile app always uses id as groupId
                    "enabled": True if str(sched.get("id")) == str(self._schedule_id) else sched.get("enabled", False),
                    "startTime": start_time,
                    "endTime": end_time,
                    "startActions": {
                        "operationName": str(sched.get("startActions", {}).get("operationName", "0"))
                    }
                }
                updated_schedules.append(scheduler)

            # Ensure we have exactly 8 schedulers (add missing ones)
            while len(updated_schedules) < 8:
                missing_id = len(updated_schedules) + 1
                updated_schedules.append({
                    "id": missing_id,
                    "groupId": missing_id,
                    "enabled": True if missing_id == int(self._schedule_id) else False,
                    "startTime": "00 00 * * 1,2,3,4,5,6,7",
                    "endTime": "00 01 * * 1,2,3,4,5,6,7",
                    "startActions": {"operationName": "0"}
                })

            _LOGGER.info(f"[{self._schedule_id}] Sending {len(updated_schedules)} schedules to API")

            # Send update to API
            success = await self._api.set_schedule(self._device_id, updated_schedules)
            if success:
                _LOGGER.info(f"[{self._schedule_id}] ✅ Successfully enabled schedule")
                await self.coordinator.async_request_refresh()
                # Effacer l'état en attente après confirmation
                self._clear_pending_state()
            else:
                _LOGGER.error(f"[{self._schedule_id}] ❌ Failed to enable schedule")
                # Annuler l'état optimiste en cas d'échec
                self._clear_pending_state()

        except Exception as e:
            _LOGGER.error(f"[{self._schedule_id}] ❌ Error enabling schedule: {e}")
            # Annuler l'état optimiste en cas d'erreur
            self._clear_pending_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the switch using exact mobile app format with optimistic UI."""
        try:
            # Mise à jour optimiste immédiate pour la réactivité
            self._set_pending_state(False)
            # Get all current schedule data
            device_data = self.device_data
            if "schedule_data" not in device_data:
                _LOGGER.error(f"No schedule data found for device {self._device_id}")
                return

            current_schedules = device_data["schedule_data"]
            if not current_schedules:
                return

            # Create complete schedule list with EXACT format from mobile app
            updated_schedules = []
            for sched in current_schedules:
                # Convert cron format 0,1,2,3,4,5,6 to 1,2,3,4,5,6,7 for mobile app
                start_time = self._convert_cron_days(sched.get("startTime", ""))
                end_time = self._convert_cron_days(sched.get("endTime", ""))

                scheduler = {
                    "id": sched.get("id"),
                    "groupId": sched.get("id"),  # Mobile app always uses id as groupId
                    "enabled": False if str(sched.get("id")) == str(self._schedule_id) else sched.get("enabled", False),
                    "startTime": start_time,
                    "endTime": end_time,
                    "startActions": {
                        "operationName": str(sched.get("startActions", {}).get("operationName", "0"))
                    }
                }
                updated_schedules.append(scheduler)

            # Ensure we have exactly 8 schedulers (add missing ones)
            while len(updated_schedules) < 8:
                missing_id = len(updated_schedules) + 1
                updated_schedules.append({
                    "id": missing_id,
                    "groupId": missing_id,
                    "enabled": False,
                    "startTime": "00 00 * * 1,2,3,4,5,6,7",
                    "endTime": "00 01 * * 1,2,3,4,5,6,7",
                    "startActions": {"operationName": "0"}
                })

            # Send update to API
            success = await self._api.set_schedule(self._device_id, updated_schedules)
            if success:
                _LOGGER.info(f"✅ Disabled schedule {self._schedule_id}")
                await self.coordinator.async_request_refresh()
                # Effacer l'état en attente après confirmation
                self._clear_pending_state()
            else:
                _LOGGER.error(f"❌ Failed to disable schedule {self._schedule_id}")
                # Annuler l'état optimiste en cas d'échec
                self._clear_pending_state()

        except Exception as e:
            _LOGGER.error(f"❌ Error disabling schedule {self._schedule_id}: {e}")
            # Annuler l'état optimiste en cas d'erreur
            self._clear_pending_state()

    def _convert_cron_days(self, cron_time: str) -> str:
        """Convert cron time from HA format (0,1,2,3,4,5,6) to mobile format (1,2,3,4,5,6,7)."""
        if not cron_time:
            return "00 00 * * 1,2,3,4,5,6,7"

        parts = cron_time.split()
        if len(parts) >= 5:
            try:
                # Convert day numbers: 0->7, 1->1, 2->2, etc.
                old_days = parts[4].split(',')
                new_days = []
                for day in old_days:
                    day_num = int(day.strip())
                    if day_num == 0:  # Sunday: 0 -> 7
                        new_days.append("7")
                    else:  # Monday-Saturday: 1-6 -> 1-6
                        new_days.append(str(day_num))

                # Sort days to match mobile app format
                new_days_sorted = sorted([int(d) for d in new_days])
                parts[4] = ','.join(map(str, new_days_sorted))
                return ' '.join(parts)
            except (ValueError, IndexError):
                pass

        return cron_time

    @property
    def extra_state_attributes(self) -> dict:
        """Return extra state attributes."""
        schedule = self._get_schedule_data()
        attrs = {
            "schedule_id": self._schedule_id,
            "device_id": self._device_id,
        }

        if schedule:
            attrs.update({
                "start_time": schedule.get("startTime", ""),
                "end_time": schedule.get("endTime", ""),
                "state": schedule.get("state", "IDLE"),
                "start_action": schedule.get("startActions", {}),
                "end_action": schedule.get("endActions", {}),
            })

        # UI responsiveness indicators
        attrs.update({
            "pending_action": self._pending_state is not None,
            "action_timestamp": self._last_action_time
        })

        return attrs