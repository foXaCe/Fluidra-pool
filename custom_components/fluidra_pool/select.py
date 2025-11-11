"""Select platform for Fluidra Pool integration."""

import logging
from typing import Any, Dict

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import FluidraDataUpdateCoordinator
from .device_registry import DeviceIdentifier

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Fluidra Pool select entities."""
    coordinator: FluidraDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    entities = []

    pools = await coordinator.api.get_pools()
    for pool in pools:
        for device in pool["devices"]:
            device_id = device.get("device_id")
            device_type = device.get("type", "")

            if not device_id:
                continue

            # Chlorinator mode select (OFF/ON/AUTO) - skip for CC24033907
            if device_type == "chlorinator":
                skip_mode = DeviceIdentifier.has_feature(device, "skip_mode_select")
                if not skip_mode:
                    entities.append(FluidraChlorinatorModeSelect(coordinator, coordinator.api, pool["id"], device_id))

            # Skip heat pumps - they don't have speed or schedule controls
            if DeviceIdentifier.has_feature(device, "skip_schedules"):
                continue

            # Speed select for variable speed pumps
            if DeviceIdentifier.should_create_entity(device, "select") and device.get("variable_speed"):
                entities.append(FluidraPumpSpeedSelect(coordinator, coordinator.api, pool["id"], device_id))

            # Schedule mode selects for pumps with schedules
            if DeviceIdentifier.should_create_entity(device, "select") and device.get("schedule_data"):
                # Create selects for the actual 8 schedulers found
                for schedule_id in ["1", "2", "3", "4", "5", "6", "7", "8"]:
                    entities.append(
                        FluidraScheduleModeSelect(coordinator, coordinator.api, pool["id"], device_id, schedule_id)
                    )

    async_add_entities(entities)


class FluidraPumpSpeedSelect(CoordinatorEntity, SelectEntity):
    """Representation of a Fluidra pump speed select control."""

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api,
        pool_id: str,
        device_id: str,
    ) -> None:
        """Initialize the pump speed select."""
        super().__init__(coordinator)
        self._api = api
        self._pool_id = pool_id
        self._device_id = device_id
        self._optimistic_option = None  # Option optimiste temporaire pendant les actions

        device_name = self.device_data.get("name") or f"E30iQ Pump {self._device_id}"

        self._attr_name = f"{device_name} Speed Level"
        self._attr_unique_id = f"fluidra_{self._device_id}_speed_level"
        self._attr_translation_key = "pump_speed"

        # Options using internal keys (will be translated)
        self._attr_options = ["stopped", "low", "medium", "high"]

        # Mapping options → API values
        # "stopped" = pump ON but no specific flow (natural state)
        self._speed_mapping = {
            "stopped": {"component": 9, "value": 1, "percent": 0, "keep_pump_on": True},
            "low": {"component": 11, "value": 0, "percent": 45},
            "medium": {"component": 11, "value": 1, "percent": 65},
            "high": {"component": 11, "value": 2, "percent": 100},
        }

        # Inverse mapping for display
        self._percent_to_option = {0: "stopped", 45: "low", 65: "medium", 100: "high"}

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
        # Si le mode auto est activé, désactiver le contrôle manuel de vitesse
        # Utiliser auto_reported en priorité (real-time API)
        auto_reported = self.device_data.get("auto_reported")
        if auto_reported is not None:
            auto_mode_enabled = bool(auto_reported)
        else:
            # Fallback sur auto_mode_enabled pour compatibilité
            auto_mode_enabled = self.device_data.get("auto_mode_enabled", False)

        if auto_mode_enabled:
            return False

        return self.coordinator.last_update_success and self.device_data.get("online", False)

    @property
    def current_option(self) -> str | None:
        """Return the current speed option."""
        # Si on a une option optimiste en cours, l'utiliser en priorité
        if self._optimistic_option is not None:
            return self._optimistic_option

        # Priorité à l'état is_running (component 9)
        is_running = self.device_data.get("is_running", False)

        if not is_running:
            # Pump completely OFF (should not happen with new logic)
            return "stopped"

        # If running, check Component 11 level first
        speed_level = self.device_data.get("speed_level_reported")
        if speed_level is not None:
            level_to_option = {0: "low", 1: "medium", 2: "high"}
            return level_to_option.get(speed_level, "low")

        # Fallback to percentage for compatibility
        current_percent = self.device_data.get("speed_percent", 0)

        # If pump ON but 0% or empty speed_percent, it's "stopped"
        if current_percent == 0:
            return "stopped"

        return self._percent_to_option.get(current_percent, "low")

    async def async_select_option(self, option: str) -> None:
        """Select new speed option."""
        if option not in self._speed_mapping:
            return

        speed_config = self._speed_mapping[option]
        component = speed_config["component"]
        value = speed_config["value"]
        speed_config["percent"]

        try:
            # Définir l'option optimiste immédiatement
            self._optimistic_option = option
            self.async_write_ha_state()  # Mettre à jour l'interface immédiatement

            # Petit délai pour s'assurer que l'option optimiste est prise en compte
            import asyncio

            await asyncio.sleep(0.1)

            # For "stopped", ensure pump is ON but no active speed
            if option == "stopped":
                # 1. S'assurer que la pompe est ON (component 9 = 1)
                success = await self._api.control_device_component(self._device_id, 9, 1)
                if success:
                    # 2. CRUCIAL: Explicitly disable speed by sending special value
                    # Try sending -1 or a value meaning "no active speed"
                    try:
                        await self._api.control_device_component(self._device_id, 11, -1)
                    except Exception:
                        # Fallback: manually mark in device data
                        device = self._api.get_device_by_id(self._device_id)
                        if device:
                            device["speed_percent"] = 0
                            device["speed_level_reported"] = None
            else:
                # Pour les autres modes, d'abord s'assurer que la pompe est ON puis définir la vitesse
                # 1. S'assurer que la pompe est ON
                await self._api.control_device_component(self._device_id, 9, 1)
                # 2. Définir la vitesse
                success = await self._api.control_device_component(self._device_id, component, value)

            if success:
                # Attendre que l'API se synchronise
                import asyncio

                await asyncio.sleep(3)  # Augmenté à 3 secondes pour plus de stabilité
                # Récupérer l'état réel immédiatement
                await self._refresh_device_state()
                await self.coordinator.async_request_refresh()

        except Exception:
            raise
        finally:
            # Toujours effacer l'option optimiste
            self._optimistic_option = None
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

            # Component 11 (speed level)
            speed_state = await self._api.get_device_component_state(self._device_id, 11)
            if speed_state:
                speed_level = speed_state.get("reportedValue", 0)
                device = self._api.get_device_by_id(self._device_id)
                if device:
                    # Si la pompe est ON
                    if device.get("is_running", False):
                        # Enregistrer le niveau de vitesse rapporté
                        device["speed_level_reported"] = speed_level
                        # Calculer le pourcentage correspondant
                        speed_percent = self._api.speed_percentages.get(speed_level, 45)
                        device["speed_percent"] = speed_percent
                    else:
                        # Pompe OFF (ne devrait plus arriver avec la nouvelle logique)
                        device["speed_percent"] = 0
                        device["speed_level_reported"] = None

        except Exception:
            pass

    @property
    def icon(self) -> str:
        """Return the icon for the entity."""
        # If auto mode is enabled, show different icon
        # Use auto_reported in priority (real-time API)
        auto_reported = self.device_data.get("auto_reported")
        if auto_reported is not None:
            auto_mode_enabled = bool(auto_reported)
        else:
            auto_mode_enabled = self.device_data.get("auto_mode_enabled", False)

        if auto_mode_enabled:
            return "mdi:autorenew"  # Icon to indicate automatic control

        current_option = self.current_option
        if current_option == "stopped":
            return "mdi:pump"  # Pump ON but not running
        if current_option in {"low", "medium"}:
            return "mdi:pump"
        # high
        return "mdi:pump"

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return additional state attributes."""
        current_percent = self.device_data.get("speed_percent", 0)

        # Utiliser auto_reported en priorité (real-time API)
        auto_reported = self.device_data.get("auto_reported")
        if auto_reported is not None:
            auto_mode_enabled = bool(auto_reported)
        else:
            auto_mode_enabled = self.device_data.get("auto_mode_enabled", False)

        attrs = {
            "speed_percent": current_percent,
            "pump_model": self.device_data.get("model", "E30iQ"),
            "pump_type": self.device_data.get("pump_type", "variable_speed"),
            "operation_mode": self.device_data.get("operation_mode", 0),
            "auto_mode": auto_mode_enabled,
            "online": self.device_data.get("online", False),
            # État optimiste
            "optimistic_option": self._optimistic_option,
            "using_optimistic": self._optimistic_option is not None,
        }

        # Ajouter une indication si le contrôle est désactivé par le mode auto
        if auto_mode_enabled:
            attrs["control_status"] = "Contrôlé par le mode automatique"
            attrs["manual_control_disabled"] = True
        else:
            attrs["control_status"] = "Contrôle manuel disponible"
            attrs["manual_control_disabled"] = False

        return attrs


class FluidraScheduleModeSelect(CoordinatorEntity, SelectEntity):
    """Select entity for choosing schedule mode (speed level) for existing schedules."""

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api,
        pool_id: str,
        device_id: str,
        schedule_id: str,
    ) -> None:
        """Initialize the schedule mode select."""
        super().__init__(coordinator)
        self._api = api
        self._pool_id = pool_id
        self._device_id = device_id
        self._schedule_id = schedule_id

        self.device_data.get("name") or f"E30iQ Pump {self._device_id}"
        self._attr_translation_key = "schedule_mode"
        self._attr_translation_placeholders = {"schedule_id": schedule_id}
        self._attr_unique_id = f"fluidra_{self._device_id}_schedule_{schedule_id}_mode"
        self._attr_entity_category = EntityCategory.CONFIG

        # Speed options for schedules (using translation keys from schedule_mode.state)
        self._attr_options = ["0", "1", "2"]

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

    def _get_schedule_data(self) -> dict | None:
        """Get schedule data from coordinator."""
        try:
            # Get schedules from device data like the sensor does
            device_data = self.device_data

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
    def current_option(self) -> str | None:
        """Return the current mode option."""
        schedule = self._get_schedule_data()
        if schedule:
            operation = schedule.get("startActions", {}).get("operationName", "0")
            return str(operation)
        return "0"

    async def async_select_option(self, option: str) -> None:
        """Select new mode option using exact mobile app format."""
        if option not in self._attr_options:
            return

        try:
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
                start_time = self._convert_cron_days(sched.get("startTime", ""))
                end_time = self._convert_cron_days(sched.get("endTime", ""))

                # If this is the schedule we're updating, use the new mode
                operation_name = (
                    option
                    if str(sched.get("id")) == str(self._schedule_id)
                    else str(sched.get("startActions", {}).get("operationName", "0"))
                )

                scheduler = {
                    "id": sched.get("id"),
                    "groupId": sched.get("id"),  # Mobile app always uses id as groupId
                    "enabled": sched.get("enabled", False),
                    "startTime": start_time,
                    "endTime": end_time,
                    "startActions": {"operationName": operation_name},
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

        except Exception:
            pass

    def _convert_cron_days(self, cron_time: str) -> str:
        """Convert cron time from HA format (0,1,2,3,4,5,6) to mobile format (1,2,3,4,5,6,7)."""
        if not cron_time:
            return "00 00 * * 1,2,3,4,5,6,7"

        parts = cron_time.split()
        if len(parts) >= 5:
            try:
                # Convert day numbers: 0->7, 1->1, 2->2, etc.
                old_days = parts[4].split(",")
                new_days = []
                for day in old_days:
                    day_num = int(day.strip())
                    if day_num == 0:  # Sunday: 0 -> 7
                        new_days.append("7")
                    else:  # Monday-Saturday: 1-6 -> 1-6
                        new_days.append(str(day_num))

                # Sort days to match mobile app format
                new_days_sorted = sorted([int(d) for d in new_days])
                parts[4] = ",".join(map(str, new_days_sorted))
                return " ".join(parts)
            except (ValueError, IndexError):
                pass

        return cron_time

    @property
    def icon(self) -> str:
        """Return the icon for the entity."""
        icons = {"0": "mdi:speedometer-slow", "1": "mdi:speedometer-medium", "2": "mdi:speedometer"}
        return icons.get(self.current_option, "mdi:speedometer")

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return additional state attributes."""
        schedule = self._get_schedule_data()
        attrs = {
            "schedule_id": self._schedule_id,
            "device_id": self._device_id,
            "available_modes": self._attr_options,
        }

        if schedule:
            attrs.update(
                {
                    "start_time": schedule.get("startTime", ""),
                    "end_time": schedule.get("endTime", ""),
                    "enabled": schedule.get("enabled", False),
                    "state": schedule.get("state", "IDLE"),
                }
            )

        return attrs


class FluidraChlorinatorModeSelect(CoordinatorEntity, SelectEntity):
    """Select entity for chlorinator mode (OFF/ON/AUTO)."""

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api,
        pool_id: str,
        device_id: str,
    ) -> None:
        """Initialize the chlorinator mode select."""
        super().__init__(coordinator)
        self._api = api
        self._pool_id = pool_id
        self._device_id = device_id
        self._optimistic_option = None

        device_name = self.device_data.get("name") or f"Chlorinator {self._device_id}"

        self._attr_name = f"{device_name} Mode"
        self._attr_unique_id = f"fluidra_{self._device_id}_mode"
        self._attr_translation_key = "chlorinator_mode"

        # Mode options: OFF, ON, AUTO (internal values in English)
        self._attr_options = ["off", "on", "auto"]

        # Mapping options → component 20 values
        self._mode_mapping = {"off": 0, "on": 1, "auto": 2}

        # Inverse mapping for display
        self._value_to_mode = {0: "off", 1: "on", 2: "auto"}

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
    def current_option(self) -> str | None:
        """Return the current mode option."""
        # Use optimistic option if set
        if self._optimistic_option is not None:
            return self._optimistic_option

        # Get current mode from component 20
        mode_value = self.device_data.get("mode_reported", 0)
        return self._value_to_mode.get(mode_value, "off")

    async def async_select_option(self, option: str) -> None:
        """Select new mode option."""
        if option not in self._mode_mapping:
            return

        mode_value = self._mode_mapping[option]

        try:
            # Set optimistic option immediately
            self._optimistic_option = option
            self.async_write_ha_state()

            # Small delay for UI update
            import asyncio

            await asyncio.sleep(0.1)

            # Send command to API (component 20)
            success = await self._api.control_device_component(self._device_id, 20, mode_value)

            if success:
                await asyncio.sleep(2)
                await self.coordinator.async_request_refresh()

        except Exception:
            raise
        finally:
            # Clear optimistic option
            self._optimistic_option = None
            self.async_write_ha_state()

    @property
    def icon(self) -> str:
        """Return the icon for the entity."""
        current = self.current_option
        if current == "off":
            return "mdi:water-off"
        if current == "on":
            return "mdi:water"
        # auto
        return "mdi:water-sync"

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return additional state attributes."""
        return {
            "device_id": self._device_id,
            "mode_component": 20,
            "optimistic_option": self._optimistic_option,
        }
