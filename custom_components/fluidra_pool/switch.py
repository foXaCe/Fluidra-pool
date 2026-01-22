"""Switch platform for Fluidra Pool integration."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, SWITCH_CONFIRMATION_DELAY, FluidraPoolConfigEntry
from .coordinator import FluidraDataUpdateCoordinator
from .device_registry import DeviceIdentifier
from .utils import convert_cron_days

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: FluidraPoolConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Fluidra Pool switch entities."""
    coordinator = config_entry.runtime_data.coordinator

    entities = []

    # Use cached pools data instead of API call for faster startup
    pools = coordinator.api._pools or await coordinator.api.get_pools()
    for pool in pools:
        for device in pool["devices"]:
            device_id = device.get("device_id")

            if not device_id:
                continue

            # Use device registry to determine which switches to create
            if DeviceIdentifier.should_create_entity(device, "switch"):
                # Determine switch type based on device type
                device_config = DeviceIdentifier.identify_device(device)
                if device_config:
                    device_type = device_config.device_type

                    if device_type == "heat_pump":
                        entities.append(FluidraHeatPumpSwitch(coordinator, coordinator.api, pool["id"], device_id))
                    elif device_type == "pump":
                        entities.append(FluidraPumpSwitch(coordinator, coordinator.api, pool["id"], device_id))
                    elif device_type == "heater":
                        entities.append(FluidraHeaterSwitch(coordinator, pool, device))

            # Create auto mode switch if device supports it
            if DeviceIdentifier.should_create_entity(device, "switch_auto"):
                if not DeviceIdentifier.has_feature(device, "skip_auto_mode"):
                    entities.append(FluidraAutoModeSwitch(coordinator, coordinator.api, pool["id"], device_id))

            # Create schedule enable switches if device supports schedules
            if DeviceIdentifier.has_feature(device, "schedules"):
                schedule_count = DeviceIdentifier.get_feature(device, "schedule_count", 8)
                for schedule_id in [str(i) for i in range(1, schedule_count + 1)]:
                    entities.append(
                        FluidraScheduleEnableSwitch(coordinator, coordinator.api, pool["id"], device_id, schedule_id)
                    )

            # Create boost mode switch for chlorinator
            if DeviceIdentifier.has_feature(device, "boost_mode"):
                entities.append(FluidraChlorinatorBoostSwitch(coordinator, coordinator.api, pool["id"], device_id))

    async_add_entities(entities)


class FluidraPoolSwitchEntity(CoordinatorEntity, SwitchEntity):
    """Base class for Fluidra Pool switch entities."""

    # 🏆 __slots__ for memory efficiency (Platinum)
    __slots__ = ("_api", "_pool_id", "_device_id", "_pending_state", "_last_action_time")

    _attr_has_entity_name = True

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
            # Rafraîchir les états des composants critiques
            # Component 9 (on/off)
            pump_state = await self._api.get_device_component_state(self._device_id, 9)
            if pump_state:
                reported_value = pump_state.get("reportedValue", 0)
                device = self._api.get_device_by_id(self._device_id)
                if device:
                    device["is_running"] = bool(reported_value)
                    device["pump_reported"] = reported_value

            # Component 10 (auto mode) - AJOUTÉ
            auto_state = await self._api.get_device_component_state(self._device_id, 10)
            if auto_state:
                auto_reported = auto_state.get("reportedValue", 0)
                device = self._api.get_device_by_id(self._device_id)
                if device:
                    device["auto_mode_enabled"] = bool(auto_reported)
                    device["auto_reported"] = auto_reported

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
                    else:
                        device["speed_percent"] = 0

        except Exception:
            pass


class FluidraPumpSwitch(FluidraPoolSwitchEntity):
    """Switch for controlling pool pumps (ON/OFF)."""

    def __init__(self, coordinator, api, pool_id: str, device_id: str):
        """Initialize the switch."""
        super().__init__(coordinator, api, pool_id, device_id)

    @property
    def name(self) -> str:
        """Return the name of the switch."""
        pool_name = self.pool_data.get("name", "Pool")
        device_name = self.device_data.get("name", "Pump")
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
            # Mise à jour optimiste immédiate pour la réactivité
            self._set_pending_state(True)

            success = await self._api.start_pump(self._device_id)
            if success:
                # Attendre que l'API se synchronise

                await asyncio.sleep(SWITCH_CONFIRMATION_DELAY)
                # Récupérer l'état réel immédiatement
                await self._refresh_device_state()
                await self.coordinator.async_request_refresh()
                # Effacer l'état en attente après confirmation
                self._clear_pending_state()
            else:
                # Annuler l'état optimiste en cas d'échec
                self._clear_pending_state()
        except Exception:
            pass
            # Annuler l'état optimiste en cas d'erreur
            self._clear_pending_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the pump off using discovered API with optimistic UI."""
        try:
            # Mise à jour optimiste immédiate pour la réactivité
            self._set_pending_state(False)

            success = await self._api.stop_pump(self._device_id)
            if success:
                # Attendre que l'API se synchronise

                await asyncio.sleep(SWITCH_CONFIRMATION_DELAY)
                # Récupérer l'état réel immédiatement
                await self._refresh_device_state()
                await self.coordinator.async_request_refresh()
                # Effacer l'état en attente après confirmation
                self._clear_pending_state()
            else:
                # Annuler l'état optimiste en cas d'échec
                self._clear_pending_state()
        except Exception:
            pass
            # Annuler l'état optimiste en cas d'erreur
            self._clear_pending_state()

    @property
    def extra_state_attributes(self) -> dict:
        """Return extra state attributes."""
        return {
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
            "action_timestamp": self._last_action_time,
        }


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
        pool_name = self.pool_data.get("name", "Pool")
        device_name = self.device_data.get("name", "Heat Pump")

        # Vérifier si c'est un Eco Elyo pour un nom plus spécifique
        device_config = DeviceIdentifier.identify_device(self.device_data)
        if device_config and "lg" in device_config.identifier_patterns[0].lower():
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
            # Mise à jour optimiste immédiate pour la réactivité
            self._set_pending_state(True)
            self.async_write_ha_state()

            # Z550iQ+ uses component 21 for ON/OFF
            if DeviceIdentifier.has_feature(self.device_data, "z550_mode"):
                _LOGGER.debug("Z550iQ+ turn ON: using component 21 for device %s", self._device_id)
                success = await self._api.control_device_component(self._device_id, 21, 1)
                _LOGGER.debug("Z550iQ+ turn ON result: %s", success)
            else:
                # Standard heat pumps use component 9
                success = await self._api.start_pump(self._device_id)

            if success:
                # Keep optimistic state - property will auto-clear after timeout
                await self.coordinator.async_request_refresh()
            else:
                # Annuler l'état optimiste en cas d'échec
                self._clear_pending_state()
                self.async_write_ha_state()
        except Exception as e:
            _LOGGER.error("Error turning on heat pump %s: %s", self._device_id, e)
            self._clear_pending_state()
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the heat pump off using discovered API with optimistic UI."""
        try:
            # Mise à jour optimiste immédiate pour la réactivité
            self._set_pending_state(False)
            self.async_write_ha_state()

            # Z550iQ+ uses component 21 for ON/OFF
            if DeviceIdentifier.has_feature(self.device_data, "z550_mode"):
                _LOGGER.debug("Z550iQ+ turn OFF: using component 21 for device %s", self._device_id)
                success = await self._api.control_device_component(self._device_id, 21, 0)
                _LOGGER.debug("Z550iQ+ turn OFF result: %s", success)
            else:
                # Standard heat pumps use component 9
                success = await self._api.stop_pump(self._device_id)

            if success:
                # Keep optimistic state - property will auto-clear after timeout
                await self.coordinator.async_request_refresh()
            else:
                # Annuler l'état optimiste en cas d'échec
                self._clear_pending_state()
                self.async_write_ha_state()
        except Exception as e:
            _LOGGER.error("Error turning off heat pump %s: %s", self._device_id, e)
            self._clear_pending_state()
            self.async_write_ha_state()

    async def _refresh_heat_pump_state(self) -> None:
        """Refresh heat pump state by polling real API components."""
        try:
            # Component 9 (on/off) - standard pour pompes et pompes à chaleur
            heat_pump_state = await self._api.get_device_component_state(self._device_id, 9)
            if heat_pump_state:
                reported_value = heat_pump_state.get("reportedValue", 0)
                device = self._api.get_device_by_id(self._device_id)
                if device:
                    device["is_heating"] = bool(reported_value)
                    device["heat_pump_reported"] = reported_value

        except Exception:
            pass

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
            "action_timestamp": self._last_action_time,
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
        device_name = self.device_data.get("name", f"Device {self._device_id}")
        pool_name = self.pool_data.get("name", "Pool")
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
        pool_name = self.pool_data.get("name", "Pool")
        device_name = self.device_data.get("name", "Pump")
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
            # Mise à jour optimiste immédiate pour la réactivité
            self._set_pending_state(True)

            success = await self._api.enable_auto_mode(self._device_id)
            if success:
                # Attendre que l'API se synchronise

                await asyncio.sleep(SWITCH_CONFIRMATION_DELAY)
                # Récupérer l'état réel immédiatement
                await self._refresh_device_state()
                await self.coordinator.async_request_refresh()
                # Effacer l'état en attente après confirmation
                self._clear_pending_state()
            else:
                # Annuler l'état optimiste en cas d'échec
                self._clear_pending_state()
        except Exception:
            pass
            # Annuler l'état optimiste en cas d'erreur
            self._clear_pending_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn auto mode off using discovered Component 10 with optimistic UI."""
        try:
            # Mise à jour optimiste immédiate pour la réactivité
            self._set_pending_state(False)

            success = await self._api.disable_auto_mode(self._device_id)
            if success:
                # Attendre que l'API se synchronise

                await asyncio.sleep(SWITCH_CONFIRMATION_DELAY)
                # Récupérer l'état réel immédiatement
                await self._refresh_device_state()
                await self.coordinator.async_request_refresh()
                # Effacer l'état en attente après confirmation
                self._clear_pending_state()
            else:
                # Annuler l'état optimiste en cas d'échec
                self._clear_pending_state()
        except Exception:
            pass
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
            "action_timestamp": self._last_action_time,
        }


class FluidraScheduleEnableSwitch(FluidraPoolSwitchEntity):
    """Switch for enabling/disabling existing schedules."""

    def __init__(self, coordinator, api, pool_id: str, device_id: str, schedule_id: str):
        """Initialize the switch."""
        super().__init__(coordinator, api, pool_id, device_id)
        self._schedule_id = schedule_id

        device_name = self.device_data.get("name")
        if not device_name:
            model = self.device_data.get("model", "Pump")
            device_name = f"{model} {self._device_id}"
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

    def _get_schedule_data(self) -> dict | None:
        """Get schedule data from coordinator."""
        try:
            # Get schedules from device data like the sensor does
            device_data = self.device_data

            # Si aucune donnée n'est disponible, retourner None
            if not device_data:
                return None

            if "schedule_data" in device_data:
                schedules = device_data["schedule_data"]

                for schedule in schedules:
                    schedule_id = schedule.get("id")
                    # Compare both as string and int to handle type mismatch
                    if str(schedule_id) == str(self._schedule_id):
                        return schedule

        except Exception:
            pass
        return None

    @property
    def available(self) -> bool:
        """Return True if the schedule exists."""
        return self._get_schedule_data() is not None

    @property
    def is_on(self) -> bool:
        """Return true if the schedule is enabled using optimistic UI."""
        # Si on a un état en attente, l'utiliser pour la réactivité
        if self._pending_state is not None:
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
        try:
            # Mise à jour optimiste immédiate pour la réactivité
            self._set_pending_state(True)
            # Get all current schedule data
            device_data = self.device_data
            if "schedule_data" not in device_data:
                return

            current_schedules = device_data["schedule_data"]
            if not current_schedules:
                return

            # Create complete schedule list with EXACT format from mobile app
            updated_schedules = []
            for sched in current_schedules:
                # Convert cron format 0,1,2,3,4,5,6 to 1,2,3,4,5,6,7 for mobile app
                start_time = convert_cron_days(sched.get("startTime", ""))
                end_time = convert_cron_days(sched.get("endTime", ""))

                scheduler = {
                    "id": sched.get("id"),
                    "groupId": sched.get("id"),  # Mobile app always uses id as groupId
                    "enabled": True if str(sched.get("id")) == str(self._schedule_id) else sched.get("enabled", False),
                    "startTime": start_time,
                    "endTime": end_time,
                    "startActions": {"operationName": str(sched.get("startActions", {}).get("operationName", "0"))},
                }
                updated_schedules.append(scheduler)

            # Ensure we have exactly 8 schedulers (add missing ones)
            while len(updated_schedules) < 8:
                missing_id = len(updated_schedules) + 1
                updated_schedules.append(
                    {
                        "id": missing_id,
                        "groupId": missing_id,
                        "enabled": missing_id == int(self._schedule_id),
                        "startTime": "00 00 * * 1,2,3,4,5,6,7",
                        "endTime": "00 01 * * 1,2,3,4,5,6,7",
                        "startActions": {"operationName": "0"},
                    }
                )

            # Send update to API
            success = await self._api.set_schedule(self._device_id, updated_schedules)
            if success:
                await self.coordinator.async_request_refresh()
                # Effacer l'état en attente après confirmation
                self._clear_pending_state()
            else:
                # Annuler l'état optimiste en cas d'échec
                self._clear_pending_state()

        except Exception:
            pass
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
                return

            current_schedules = device_data["schedule_data"]
            if not current_schedules:
                return

            # Create complete schedule list with EXACT format from mobile app
            updated_schedules = []
            for sched in current_schedules:
                # Convert cron format 0,1,2,3,4,5,6 to 1,2,3,4,5,6,7 for mobile app
                start_time = convert_cron_days(sched.get("startTime", ""))
                end_time = convert_cron_days(sched.get("endTime", ""))

                scheduler = {
                    "id": sched.get("id"),
                    "groupId": sched.get("id"),  # Mobile app always uses id as groupId
                    "enabled": False if str(sched.get("id")) == str(self._schedule_id) else sched.get("enabled", False),
                    "startTime": start_time,
                    "endTime": end_time,
                    "startActions": {"operationName": str(sched.get("startActions", {}).get("operationName", "0"))},
                }
                updated_schedules.append(scheduler)

            # Ensure we have exactly 8 schedulers (add missing ones)
            while len(updated_schedules) < 8:
                missing_id = len(updated_schedules) + 1
                updated_schedules.append(
                    {
                        "id": missing_id,
                        "groupId": missing_id,
                        "enabled": False,
                        "startTime": "00 00 * * 1,2,3,4,5,6,7",
                        "endTime": "00 01 * * 1,2,3,4,5,6,7",
                        "startActions": {"operationName": "0"},
                    }
                )

            # Send update to API
            success = await self._api.set_schedule(self._device_id, updated_schedules)
            if success:
                await self.coordinator.async_request_refresh()
                # Effacer l'état en attente après confirmation
                self._clear_pending_state()
            else:
                # Annuler l'état optimiste en cas d'échec
                self._clear_pending_state()

        except Exception:
            pass
            # Annuler l'état optimiste en cas d'erreur
            self._clear_pending_state()

    @property
    def extra_state_attributes(self) -> dict:
        """Return extra state attributes."""
        schedule = self._get_schedule_data()
        attrs = {
            "schedule_id": self._schedule_id,
            "device_id": self._device_id,
        }

        if schedule:
            attrs.update(
                {
                    "start_time": schedule.get("startTime", ""),
                    "end_time": schedule.get("endTime", ""),
                    "state": schedule.get("state", "IDLE"),
                    "start_action": schedule.get("startActions", {}),
                    "end_action": schedule.get("endActions", {}),
                }
            )

        # UI responsiveness indicators
        attrs.update({"pending_action": self._pending_state is not None, "action_timestamp": self._last_action_time})

        return attrs


class FluidraChlorinatorBoostSwitch(FluidraPoolSwitchEntity):
    """Switch for chlorinator boost mode."""

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api,
        pool_id: str,
        device_id: str,
    ) -> None:
        """Initialize the boost mode switch."""
        super().__init__(coordinator, api, pool_id, device_id)

        device_name = self.device_data.get("name") or f"Chlorinator {self._device_id}"

        self._attr_name = f"{device_name} Boost Mode"
        self._attr_unique_id = f"fluidra_{self._device_id}_boost_mode"
        self._attr_icon = "mdi:rocket-launch"

    @property
    def is_on(self) -> bool:
        """Return true if boost mode is on using optimistic UI."""
        # Get component number dynamically from device config
        boost_component = DeviceIdentifier.get_feature(self.device_data, "boost_mode", 245)

        components = self.device_data.get("components", {})
        component_data = components.get(str(boost_component), {})
        boost_value = component_data.get("reportedValue", False)
        actual_state = bool(boost_value)

        # Si on a un état en attente
        if self._pending_state is not None:
            # Si le serveur confirme l'état attendu, clear le pending state
            if actual_state == self._pending_state or time.time() - self._last_action_time > 10:
                self._clear_pending_state()
                return actual_state
            return self._pending_state

        return actual_state

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn boost mode on with optimistic UI."""
        # Get component number dynamically from device config
        boost_component = DeviceIdentifier.get_feature(self.device_data, "boost_mode", 245)

        try:
            # Mise à jour optimiste immédiate pour la réactivité
            self._set_pending_state(True)

            success = await self._api.control_device_component(self._device_id, boost_component, True)

            if success:
                await asyncio.sleep(SWITCH_CONFIRMATION_DELAY)
                await self.coordinator.async_request_refresh()
                # Le pending state se clear automatiquement dans is_on() quand le serveur confirme
            else:
                self._clear_pending_state()

        except Exception:
            pass
            self._clear_pending_state()
            raise

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn boost mode off with optimistic UI."""
        # Get component number dynamically from device config
        boost_component = DeviceIdentifier.get_feature(self.device_data, "boost_mode", 245)

        try:
            # Mise à jour optimiste immédiate pour la réactivité
            self._set_pending_state(False)

            success = await self._api.control_device_component(self._device_id, boost_component, False)

            if success:
                await asyncio.sleep(SWITCH_CONFIRMATION_DELAY)
                await self.coordinator.async_request_refresh()
                # Le pending state se clear automatiquement dans is_on() quand le serveur confirme
            else:
                self._clear_pending_state()

        except Exception:
            pass
            self._clear_pending_state()
            raise

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        # Get component number dynamically from device config
        boost_component = DeviceIdentifier.get_feature(self.device_data, "boost_mode", 245)

        return {
            "component": boost_component,
            "device_id": self._device_id,
            "pending_action": self._pending_state is not None,
            "action_timestamp": self._last_action_time,
        }
