"""Select platform for Fluidra Pool integration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    COMMAND_CONFIRMATION_DELAY,
    SWITCH_CONFIRMATION_DELAY,
    UI_UPDATE_DELAY,
    FluidraPoolConfigEntry,
)
from .coordinator import FluidraDataUpdateCoordinator
from .device_registry import DeviceIdentifier
from .entity import FluidraPoolControlEntity
from .utils import convert_cron_days

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: FluidraPoolConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Fluidra Pool select entities."""
    coordinator = config_entry.runtime_data.coordinator

    entities = []

    # Use cached pools data instead of API call for faster startup
    pools = coordinator.api.cached_pools or await coordinator.api.get_pools()
    for pool in pools:
        for device in pool["devices"]:
            device_id = device.get("device_id")
            # Use device registry to get proper device type
            config = DeviceIdentifier.identify_device(device)
            device_type = config.device_type if config else device.get("type", "")

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

            # Speed select for variable speed pumps (not for lights)
            if (
                device_type != "light"
                and DeviceIdentifier.should_create_entity(device, "select")
                and device.get("variable_speed")
            ):
                entities.append(FluidraPumpSpeedSelect(coordinator, coordinator.api, pool["id"], device_id))

            # Schedule mode selects for pumps with schedules (not for lights)
            if (
                device_type != "light"
                and DeviceIdentifier.should_create_entity(device, "select")
                and device.get("schedule_data")
            ):
                # Create selects for the actual 8 schedulers found
                for schedule_id in ["1", "2", "3", "4", "5", "6", "7", "8"]:
                    entities.append(
                        FluidraScheduleModeSelect(
                            coordinator,
                            coordinator.api,
                            pool["id"],
                            device_id,
                            schedule_id,
                        )
                    )

            # Light effect/scene select for LumiPlus Connect
            if device_type == "light":
                effect_component = DeviceIdentifier.get_feature(device, "effect_select")
                if effect_component:
                    entities.append(FluidraLightEffectSelect(coordinator, coordinator.api, pool["id"], device_id))

            # Schedule speed selects for chlorinators with schedules (e.g., DM24049704)
            if device_type == "chlorinator" and DeviceIdentifier.has_feature(device, "schedules"):
                schedule_count = DeviceIdentifier.get_feature(device, "schedule_count", 3)
                for i in range(schedule_count):
                    schedule_id = str(i)
                    entities.append(
                        FluidraChlorinatorScheduleSpeedSelect(
                            coordinator,
                            coordinator.api,
                            pool["id"],
                            device_id,
                            schedule_id,
                        )
                    )

    async_add_entities(entities)


class FluidraPumpSpeedSelect(FluidraPoolControlEntity, SelectEntity):
    """Representation of a Fluidra pump speed select control."""

    __slots__ = (
        "_optimistic_option",
        "_speed_mapping",
        "_percent_to_option",
    )

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api,
        pool_id: str,
        device_id: str,
    ) -> None:
        """Initialize the pump speed select."""
        super().__init__(coordinator, api, pool_id, device_id)
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

        try:
            # Définir l'option optimiste immédiatement
            self._optimistic_option = option
            self.async_write_ha_state()  # Mettre à jour l'interface immédiatement

            # Petit délai pour s'assurer que l'option optimiste est prise en compte

            await asyncio.sleep(UI_UPDATE_DELAY)

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
                        _LOGGER.debug("Failed to disable speed for %s, using fallback", self._device_id)
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

                await asyncio.sleep(COMMAND_CONFIRMATION_DELAY)  # Augmenté à 3 secondes pour plus de stabilité
                # Récupérer l'état réel immédiatement
                await self._refresh_device_state()
                await self.coordinator.async_request_refresh()

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
            _LOGGER.debug("Failed to refresh device state for %s", self._device_id)

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
    def extra_state_attributes(self) -> dict[str, Any]:
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


class FluidraScheduleModeSelect(FluidraPoolControlEntity, SelectEntity):
    """Select entity for choosing schedule mode (speed level) for existing schedules."""

    __slots__ = ("_schedule_id",)

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api,
        pool_id: str,
        device_id: str,
        schedule_id: str,
    ) -> None:
        """Initialize the schedule mode select."""
        super().__init__(coordinator, api, pool_id, device_id)
        self._schedule_id = schedule_id

        self._attr_translation_key = "schedule_mode"
        self._attr_translation_placeholders = {"schedule_id": schedule_id}
        self._attr_unique_id = f"fluidra_{self._device_id}_schedule_{schedule_id}_mode"
        self._attr_entity_category = EntityCategory.CONFIG

        # Speed options for schedules (using translation keys from schedule_mode.state)
        self._attr_options = ["0", "1", "2"]

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
            _LOGGER.debug("Failed to get schedule data for %s", self._device_id)
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
                start_time = convert_cron_days(sched.get("startTime", ""))
                end_time = convert_cron_days(sched.get("endTime", ""))

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
            _LOGGER.debug("Failed to update schedule mode for %s", self._device_id)

    @property
    def icon(self) -> str:
        """Return the icon for the entity."""
        icons = {
            "0": "mdi:speedometer-slow",
            "1": "mdi:speedometer-medium",
            "2": "mdi:speedometer",
        }
        return icons.get(self.current_option, "mdi:speedometer")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
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


class FluidraChlorinatorModeSelect(FluidraPoolControlEntity, SelectEntity):
    """Select entity for chlorinator mode (OFF/ON/AUTO)."""

    __slots__ = ("_optimistic_option", "_mode_mapping", "_value_to_mode")

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api,
        pool_id: str,
        device_id: str,
    ) -> None:
        """Initialize the chlorinator mode select."""
        super().__init__(coordinator, api, pool_id, device_id)
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

            await asyncio.sleep(UI_UPDATE_DELAY)

            # Send command to API (component 20)
            success = await self._api.control_device_component(self._device_id, 20, mode_value)

            if success:
                await asyncio.sleep(SWITCH_CONFIRMATION_DELAY)
                await self.coordinator.async_request_refresh()

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
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        return {
            "device_id": self._device_id,
            "mode_component": 20,
            "optimistic_option": self._optimistic_option,
        }


class FluidraLightEffectSelect(FluidraPoolControlEntity, SelectEntity):
    """Select entity for LumiPlus Connect light effect/scene selection."""

    EFFECT_COMPONENT = 18

    __slots__ = ("_optimistic_option", "_effect_mapping", "_value_to_effect")

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api,
        pool_id: str,
        device_id: str,
    ) -> None:
        """Initialize the light effect select."""
        super().__init__(coordinator, api, pool_id, device_id)
        self._optimistic_option = None

        device_name = self.device_data.get("name") or f"Pool Light {self._device_id}"

        self._attr_name = f"{device_name} Effect"
        self._attr_unique_id = f"fluidra_{self._device_id}_effect"
        self._attr_translation_key = "light_effect"

        # Effect options (Static color + 8 scenes as discovered via mitmproxy)
        self._attr_options = [
            "static_color",
            "scene_1",
            "scene_2",
            "scene_3",
            "scene_4",
            "scene_5",
            "scene_6",
            "scene_7",
            "scene_8",
        ]

        # Mapping options → component 18 values
        self._effect_mapping = {
            "static_color": 0,
            "scene_1": 1,
            "scene_2": 2,
            "scene_3": 3,
            "scene_4": 4,
            "scene_5": 5,
            "scene_6": 6,
            "scene_7": 7,
            "scene_8": 8,
        }

        # Inverse mapping for display
        self._value_to_effect = {
            0: "static_color",
            1: "scene_1",
            2: "scene_2",
            3: "scene_3",
            4: "scene_4",
            5: "scene_5",
            6: "scene_6",
            7: "scene_7",
            8: "scene_8",
        }

    @property
    def current_option(self) -> str | None:
        """Return the current effect option."""
        # Use optimistic option if set
        if self._optimistic_option is not None:
            return self._optimistic_option

        # Get current effect from component 18
        components = self.device_data.get("components", {})
        component_data = components.get(str(self.EFFECT_COMPONENT), {})
        effect_value = component_data.get("reportedValue", component_data.get("desiredValue", 0))

        return self._value_to_effect.get(effect_value, "static_color")

    async def async_select_option(self, option: str) -> None:
        """Select new effect option."""
        if option not in self._effect_mapping:
            return

        effect_value = self._effect_mapping[option]

        try:
            # Set optimistic option immediately
            self._optimistic_option = option
            self.async_write_ha_state()

            # Small delay for UI update

            await asyncio.sleep(UI_UPDATE_DELAY)

            _LOGGER.debug(
                "Setting light effect for %s: component %s = %s",
                self._device_id,
                self.EFFECT_COMPONENT,
                effect_value,
            )

            # Send command to API (component 18) using control_device_component
            # which properly updates local state
            success = await self._api.control_device_component(self._device_id, self.EFFECT_COMPONENT, effect_value)

            _LOGGER.debug("Light effect API call result: %s", success)

            if success:
                # Wait for device to process the command
                await asyncio.sleep(COMMAND_CONFIRMATION_DELAY)
                await self.coordinator.async_request_refresh()

        except Exception as err:
            _LOGGER.error("Failed to set light effect: %s", err)
            raise
        finally:
            # Clear optimistic option
            self._optimistic_option = None
            self.async_write_ha_state()

    @property
    def icon(self) -> str:
        """Return the icon for the entity."""
        return "mdi:palette"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        components = self.device_data.get("components", {})
        component_data = components.get(str(self.EFFECT_COMPONENT), {})

        return {
            "device_id": self._device_id,
            "effect_component": self.EFFECT_COMPONENT,
            "reported_value": component_data.get("reportedValue"),
            "desired_value": component_data.get("desiredValue"),
            "optimistic_option": self._optimistic_option,
        }


class FluidraChlorinatorScheduleSpeedSelect(FluidraPoolControlEntity, SelectEntity):
    """Select entity for chlorinator schedule speed (S1/S2/S3)."""

    __slots__ = ("_schedule_id", "_optimistic_option", "_speed_mapping", "_value_to_speed")

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api,
        pool_id: str,
        device_id: str,
        schedule_id: str,
    ) -> None:
        """Initialize the chlorinator schedule speed select."""
        super().__init__(coordinator, api, pool_id, device_id)
        self._schedule_id = schedule_id
        self._optimistic_option = None

        self._attr_translation_key = "chlorinator_schedule_speed"
        self._attr_translation_placeholders = {"schedule_id": schedule_id}
        self._attr_unique_id = f"fluidra_{self._device_id}_schedule_{schedule_id}_speed"
        self._attr_entity_category = EntityCategory.CONFIG

        # Speed options: S1, S2, S3 (mapped to operationName 1, 2, 3)
        self._attr_options = ["S1", "S2", "S3"]

        # Mapping options → operationName values
        self._speed_mapping = {"S1": "1", "S2": "2", "S3": "3"}
        self._value_to_speed = {"1": "S1", "2": "S2", "3": "S3"}

    def _get_schedule_data(self) -> dict | None:
        """Get schedule data from coordinator."""
        try:
            device_data = self.device_data
            if "schedule_data" in device_data:
                schedules = device_data["schedule_data"]
                for schedule in schedules:
                    schedule_id = schedule.get("id")
                    if str(schedule_id) == str(self._schedule_id):
                        return schedule
        except Exception:
            _LOGGER.debug("Failed to get schedule data for %s", self._device_id)
        return None

    @property
    def available(self) -> bool:
        """Return True if the schedule exists."""
        return self._get_schedule_data() is not None

    @property
    def current_option(self) -> str | None:
        """Return the current speed option."""
        if self._optimistic_option is not None:
            return self._optimistic_option

        schedule = self._get_schedule_data()
        if schedule:
            operation = schedule.get("startActions", {}).get("operationName", "1")
            return self._value_to_speed.get(str(operation), "S1")
        return "S1"

    async def async_select_option(self, option: str) -> None:
        """Select new speed option."""
        if option not in self._speed_mapping:
            return

        try:
            # Set optimistic option immediately
            self._optimistic_option = option
            self.async_write_ha_state()

            await asyncio.sleep(UI_UPDATE_DELAY)

            # Get all current schedule data
            device_data = self.device_data
            if "schedule_data" not in device_data:
                return

            current_schedules = device_data["schedule_data"]
            if not current_schedules:
                return

            # Get schedule component for this device
            schedule_component = DeviceIdentifier.get_feature(device_data, "schedule_component", 258)

            # Create updated schedule list
            updated_schedules = []
            for sched in current_schedules:
                start_time = sched.get("startTime", "00 00 * * 1,2,3,4,5,6,7")
                end_time = sched.get("endTime", "00 01 * * 1,2,3,4,5,6,7")

                # If this is the schedule we're updating, use the new speed
                operation_name = (
                    self._speed_mapping[option]
                    if str(sched.get("id")) == str(self._schedule_id)
                    else str(sched.get("startActions", {}).get("operationName", "1"))
                )

                # DM24049704 chlorinator uses different format
                if schedule_component == 258:
                    # Format: id starts at 1, groupId always 1, CRON with "00" padding
                    scheduler = {
                        "id": sched.get("id"),
                        "groupId": 1,  # App always uses groupId=1 for all schedules
                        "enabled": True,
                        "startTime": self._format_cron_time(start_time),
                        "endTime": self._format_cron_time(end_time),
                        "startActions": {"operationName": operation_name},
                    }
                else:
                    scheduler = {
                        "id": sched.get("id"),
                        "groupId": sched.get("id"),
                        "enabled": sched.get("enabled", False),
                        "startTime": start_time,
                        "endTime": end_time,
                        "startActions": {"operationName": operation_name},
                    }
                updated_schedules.append(scheduler)

            # Send update to API with specific component
            success = await self._api.set_schedule(self._device_id, updated_schedules, component_id=schedule_component)
            if success:
                # Keep optimistic value - it will be cleared when coordinator confirms
                await asyncio.sleep(COMMAND_CONFIRMATION_DELAY)
                await self.coordinator.async_request_refresh()
                # Clear optimistic only after successful refresh
                self._optimistic_option = None
                self.async_write_ha_state()
            else:
                # API failed - clear optimistic and revert
                self._optimistic_option = None
                self.async_write_ha_state()

        except Exception as err:
            _LOGGER.error("Failed to set schedule speed: %s", err)
            self._optimistic_option = None
            self.async_write_ha_state()

    def _format_cron_time(self, cron_time: str) -> str:
        """Format CRON time to match official app format (00 05 * * 1,2,3,4,5,6,7)."""
        if not cron_time:
            return "00 00 * * 1,2,3,4,5,6,7"

        parts = cron_time.split()
        if len(parts) >= 5:
            # Pad minute and hour with leading zeros
            minute = parts[0].zfill(2)
            hour = parts[1].zfill(2)
            # Keep days as 1,2,3,4,5,6,7 (not *)
            days = parts[4] if parts[4] != "*" else "1,2,3,4,5,6,7"
            return f"{minute} {hour} * * {days}"

        return cron_time

    @property
    def icon(self) -> str:
        """Return the icon for the entity."""
        current = self.current_option
        if current == "S1":
            return "mdi:speedometer-slow"
        if current == "S2":
            return "mdi:speedometer-medium"
        return "mdi:speedometer"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        schedule = self._get_schedule_data()
        attrs = {
            "schedule_id": self._schedule_id,
            "device_id": self._device_id,
            "available_speeds": self._attr_options,
        }

        if schedule:
            attrs.update(
                {
                    "start_time": schedule.get("startTime", ""),
                    "end_time": schedule.get("endTime", ""),
                    "enabled": schedule.get("enabled", False),
                    "state": schedule.get("state", "UNKNOWN"),
                }
            )

        return attrs
