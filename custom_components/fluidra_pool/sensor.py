"""Sensor platform for Fluidra Pool integration."""

from datetime import datetime, time
import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, FluidraPoolConfigEntry
from .coordinator import FluidraDataUpdateCoordinator
from .device_registry import DeviceIdentifier

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: FluidraPoolConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Fluidra Pool sensor entities."""
    coordinator = config_entry.runtime_data.coordinator

    entities = []

    # Use cached pools data instead of API call for faster startup
    pools = coordinator.api._pools or await coordinator.api.get_pools()
    for pool in pools:
        for device in pool["devices"]:
            device_id = device.get("device_id")

            if not device_id:
                continue

            # Use device registry to determine which sensors to create
            if DeviceIdentifier.should_create_entity(device, "sensor_info"):
                entities.append(FluidraDeviceInfoSensor(coordinator, coordinator.api, pool["id"], device_id))

            if DeviceIdentifier.should_create_entity(device, "sensor_schedule"):
                entities.append(FluidraPumpScheduleSensor(coordinator, coordinator.api, pool["id"], device_id))

            if DeviceIdentifier.should_create_entity(device, "sensor_speed"):
                entities.append(FluidraPumpSpeedSensor(coordinator, coordinator.api, pool["id"], device_id))

            if DeviceIdentifier.should_create_entity(device, "sensor_temperature"):
                # Temperature sensors for heaters
                if "current_temperature" in device:
                    entities.append(
                        FluidraTemperatureSensor(coordinator, coordinator.api, pool["id"], device_id, "current")
                    )
                if "target_temperature" in device:
                    entities.append(
                        FluidraTemperatureSensor(coordinator, coordinator.api, pool["id"], device_id, "target")
                    )
                # Z550iQ+ heat pump specific temperature sensors
                if DeviceIdentifier.has_feature(device, "z550_mode"):
                    # Water temperature sensor
                    entities.append(
                        FluidraTemperatureSensor(coordinator, coordinator.api, pool["id"], device_id, "water")
                    )
                    # Air temperature sensor
                    entities.append(
                        FluidraTemperatureSensor(coordinator, coordinator.api, pool["id"], device_id, "air")
                    )

            if DeviceIdentifier.should_create_entity(device, "sensor_brightness"):
                # Brightness sensor for lights
                if "brightness" in device:
                    entities.append(FluidraLightBrightnessSensor(coordinator, coordinator.api, pool["id"], device_id))

            # Chlorinator sensors
            device_type = device.get("type", "")
            if device_type == "chlorinator":
                skip_ph_orp = DeviceIdentifier.has_feature(device, "skip_ph_orp")

                # Get sensor components from device registry
                sensors_config = DeviceIdentifier.get_feature(device, "sensors", {})

                # pH, ORP, Free chlorine sensors (skip for CC24033907)
                if not skip_ph_orp:
                    # pH sensor (component 172)
                    ph_component = sensors_config.get("ph", 172)
                    entities.append(
                        FluidraChlorinatorSensor(
                            coordinator, coordinator.api, pool["id"], device_id, "ph", ph_component
                        )
                    )
                    # ORP sensor (component 177)
                    orp_component = sensors_config.get("orp", 177)
                    entities.append(
                        FluidraChlorinatorSensor(
                            coordinator, coordinator.api, pool["id"], device_id, "orp", orp_component
                        )
                    )
                    # Free chlorine sensor (component 178) - only if configured
                    if "free_chlorine" in sensors_config:
                        free_chlorine_component = sensors_config.get("free_chlorine")
                        entities.append(
                            FluidraChlorinatorSensor(
                                coordinator,
                                coordinator.api,
                                pool["id"],
                                device_id,
                                "free_chlorine",
                                free_chlorine_component,
                            )
                        )

                # Temperature sensor - get from device registry (183 for generic, 21 for CC24033907)
                temp_component = sensors_config.get("temperature", 183)
                entities.append(
                    FluidraChlorinatorSensor(
                        coordinator, coordinator.api, pool["id"], device_id, "temperature", temp_component
                    )
                )
                # Salinity sensor (component 185) - all chlorinators
                salinity_component = sensors_config.get("salinity", 185)
                entities.append(
                    FluidraChlorinatorSensor(
                        coordinator, coordinator.api, pool["id"], device_id, "salinity", salinity_component
                    )
                )

                # Chlorination actual sensor (optional - only for models that have it)
                if "chlorination_actual" in sensors_config:
                    chlorination_actual_component = sensors_config.get("chlorination_actual")
                    entities.append(
                        FluidraChlorinatorSensor(
                            coordinator,
                            coordinator.api,
                            pool["id"],
                            device_id,
                            "chlorination_actual",
                            chlorination_actual_component,
                        )
                    )

                # Count sensors dynamically
                sensor_count = 2  # Base: temperature + salinity
                if not skip_ph_orp:
                    sensor_count += 2  # pH + ORP
                    if "free_chlorine" in sensors_config:
                        sensor_count += 1  # Free chlorine (optional)

        # Sensors spécifiques à la piscine (pas liés aux devices)
        entities.append(FluidraPoolWeatherSensor(coordinator, coordinator.api, pool["id"]))
        entities.append(FluidraPoolStatusSensor(coordinator, coordinator.api, pool["id"]))
        entities.append(FluidraPoolLocationSensor(coordinator, coordinator.api, pool["id"]))
        entities.append(FluidraPoolWaterQualitySensor(coordinator, coordinator.api, pool["id"]))

    async_add_entities(entities)


class FluidraPoolSensorEntity(CoordinatorEntity, SensorEntity):
    """Base class for Fluidra Pool sensor entities."""

    _attr_has_entity_name = True  # 🥉 OBLIGATOIRE (Bronze)

    def __init__(self, coordinator, api, pool_id: str, device_id: str, sensor_type: str = ""):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._api = api
        self._pool_id = pool_id
        self._device_id = device_id
        self._sensor_type = sensor_type

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
        suffix = f"_{self._sensor_type}" if self._sensor_type else ""
        return f"{DOMAIN}_{self._pool_id}_{self._device_id}_sensor{suffix}"

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


class FluidraTemperatureSensor(FluidraPoolSensorEntity):
    """Temperature sensor for pool heaters and heat pumps."""

    def __init__(self, coordinator, api, pool_id: str, device_id: str, sensor_type: str):
        """Initialize temperature sensor."""
        super().__init__(coordinator, api, pool_id, device_id, sensor_type)
        # 🥉 Utiliser device_class au lieu de translation_key quand possible (Bronze)
        self._attr_device_class = SensorDeviceClass.TEMPERATURE
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        self._attr_suggested_display_precision = 1

    @property
    def name(self) -> str | None:
        """Return the name of the sensor."""
        # 🥉 Utiliser translation_key et device_class si possible (Bronze)
        if self._sensor_type == "current":
            return None  # HA utilisera "Current Temperature"
        elif self._sensor_type == "target":
            return None  # HA utilisera "Target Temperature"
        elif self._sensor_type == "water":
            return None  # HA utilisera "Water Temperature"
        elif self._sensor_type == "air":
            return None  # HA utilisera "Air Temperature"
        return "Temperature"

    @property
    def native_value(self) -> float | None:
        """Return the state of the sensor."""
        if self._sensor_type == "current":
            return self.device_data.get("current_temperature")
        if self._sensor_type == "target":
            return self.device_data.get("target_temperature")
        if self._sensor_type == "water":
            return self.device_data.get("water_temperature")
        if self._sensor_type == "air":
            return self.device_data.get("air_temperature")
        return None

    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit of measurement."""
        return UnitOfTemperature.CELSIUS

    @property
    def device_class(self) -> SensorDeviceClass:
        """Return the device class."""
        return SensorDeviceClass.TEMPERATURE

    @property
    def state_class(self) -> SensorStateClass:
        """Return the state class."""
        return SensorStateClass.MEASUREMENT

    @property
    def icon(self) -> str:
        """Return the icon of the sensor."""
        return "mdi:thermometer"


class FluidraLightBrightnessSensor(FluidraPoolSensorEntity):
    """Brightness sensor for pool lights."""

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        device_name = self.device_data.get("name", f"Device {self._device_id}")
        pool_name = self.pool_data.get("name", "Pool")
        return f"{pool_name} {device_name} Brightness"

    @property
    def native_value(self) -> int | None:
        """Return the state of the sensor."""
        return self.device_data.get("brightness")

    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit of measurement."""
        return PERCENTAGE

    @property
    def state_class(self) -> SensorStateClass:
        """Return the state class."""
        return SensorStateClass.MEASUREMENT

    @property
    def icon(self) -> str:
        """Return the icon of the sensor."""
        return "mdi:brightness-percent"


class FluidraPumpSpeedSensor(FluidraPoolSensorEntity):
    """Speed sensor for pool pumps with mode detection."""

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api,
        pool_id: str,
        device_id: str,
    ) -> None:
        """Initialize the speed sensor."""
        super().__init__(coordinator, api, pool_id, device_id, "speed")

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        device_name = self.device_data.get("name")
        if not device_name:
            # Use model or generic pump name
            model = self.device_data.get("model", "Pump")
            device_name = f"{model} {self._device_id}"
        return f"{device_name} Speed"

    @property
    def icon(self) -> str:
        """Return the icon for the entity."""
        speed_mode = self._get_speed_mode()
        # Check for stopped states
        if speed_mode in ["stopped", "not_running"]:
            return "mdi:pump-off"
        return "mdi:pump"

    def _translate_speed_state(self, state_key: str) -> str:
        """Translate speed state to French."""
        translations = {
            "stopped": "Arrêtée",
            "not_running": "Pas en marche",
            "low": "Faible",
            "medium": "Moyenne",
            "high": "Élevée",
        }
        return translations.get(state_key, state_key)

    def _get_speed_mode(self) -> str:
        """Get the current speed mode - returns state key."""
        # État de la pompe
        is_running = self.device_data.get("is_running", False)
        pump_reported = self.device_data.get("pump_reported")
        if pump_reported is not None:
            is_running = bool(pump_reported)

        # État du mode auto
        self.device_data.get("auto_mode_enabled", False)
        auto_reported = self.device_data.get("auto_reported")
        if auto_reported is not None:
            bool(auto_reported)

        # Si pompe arrêtée - return state key
        if not is_running:
            return "stopped"

        # Récupérer le pourcentage actuel
        current_speed = self.device_data.get("speed_percent", 0)

        if current_speed == 0:
            return "not_running"

        # Mapper le pourcentage vers des états énumérés - return state keys
        if current_speed <= 50:
            return "low"
        if current_speed <= 70:
            return "medium"
        return "high"

    @property
    def native_value(self) -> str:
        """Return the state of the sensor."""
        state_key = self._get_speed_mode()
        return self._translate_speed_state(state_key)

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional state attributes."""
        # État de la pompe
        is_running = self.device_data.get("is_running", False)
        pump_reported = self.device_data.get("pump_reported")
        if pump_reported is not None:
            is_running = bool(pump_reported)

        # État du mode auto
        auto_mode = self.device_data.get("auto_mode_enabled", False)
        auto_reported = self.device_data.get("auto_reported")
        if auto_reported is not None:
            auto_mode = bool(auto_reported)

        # Vitesse et niveau
        current_speed = self.device_data.get("speed_percent", 0)
        speed_level = self.device_data.get("speed_level_reported")

        return {
            "pump_running": is_running,
            "auto_mode": auto_mode,
            "speed_percent": current_speed,
            "speed_level": speed_level,
            "pump_reported": pump_reported,
            "auto_reported": auto_reported,
            "raw_data": {
                "is_running": self.device_data.get("is_running"),
                "auto_mode_enabled": self.device_data.get("auto_mode_enabled"),
                "speed_percent": self.device_data.get("speed_percent"),
            },
        }


class FluidraPumpScheduleSensor(FluidraPoolSensorEntity):
    """Sensor for displaying pump weekly schedules."""

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api,
        pool_id: str,
        device_id: str,
    ) -> None:
        """Initialize the schedule sensor."""
        super().__init__(coordinator, api, pool_id, device_id, "schedules")
        self._attr_translation_key = "schedule_count"

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        device_name = self.device_data.get("name")
        if not device_name:
            # Use model or generic pump name
            model = self.device_data.get("model", "Pump")
            device_name = f"{model} {self._device_id}"
        return f"{device_name} Schedules"

    @property
    def icon(self) -> str:
        """Return the icon for the entity."""
        return "mdi:calendar-clock"

    def _translate_schedule_state(self, state_key: str) -> str:
        """Return schedule state text in English."""
        # Always return English for dynamic text
        # HA config.language returns system language, not user interface language
        translations = {
            "no_schedule": "No Schedule",
            "active": "Active",
            "active_schedules": "active schedules",
            "error": "Error",
            "low": "Low",
            "medium": "Medium",
            "high": "High",
        }
        return translations.get(state_key, state_key)

    def _parse_cron_time(self, cron_time: str) -> time | None:
        """Parse cron time format 'mm HH * * 0,1,2,3,4,5,6' to time object."""
        try:
            parts = cron_time.split()
            if len(parts) >= 2:
                minute = int(parts[0])
                hour = int(parts[1])
                return time(hour, minute)
        except Exception:
            pass
        return None

    def _format_schedule_time(self, schedule: dict) -> str:
        """Format schedule time range for display."""
        start_time = self._parse_cron_time(schedule.get("startTime", ""))
        end_time = self._parse_cron_time(schedule.get("endTime", ""))

        if start_time and end_time:
            return f"{start_time.strftime('%H:%M')}-{end_time.strftime('%H:%M')}"
        return "N/A"

    def _get_operation_name(self, operation: str) -> str:
        """Convert operation name to readable format."""
        speed_key = {"0": "low", "1": "medium", "2": "high"}.get(operation, "low")

        speed_name = self._translate_schedule_state(speed_key)
        return (
            f"{speed_name} (45%)"
            if operation == "0"
            else f"{speed_name} (65%)"
            if operation == "1"
            else f"{speed_name} (100%)"
        )

    def _get_current_schedule(self, schedules: list[dict]) -> dict | None:
        """Get currently active schedule based on current time."""
        now = datetime.now().time()

        for schedule in schedules:
            if not schedule.get("enabled", False):
                continue

            start_time = self._parse_cron_time(schedule.get("startTime", ""))
            end_time = self._parse_cron_time(schedule.get("endTime", ""))

            if start_time and end_time and start_time <= now <= end_time:
                return schedule
        return None

    def _get_schedules_data(self) -> list[dict]:
        """Get schedules data from device data."""
        # Chercher dans les données du coordinateur d'abord
        device_data = self.device_data

        if "schedule_data" in device_data:
            return device_data["schedule_data"]
        return []

    @property
    def native_value(self) -> str:
        """Return the state of the sensor."""
        try:
            schedules = self._get_schedules_data()
            if not schedules:
                return self._translate_schedule_state("no_schedule")

            # Vérifier s'il y a une programmation active maintenant
            current_schedule = self._get_current_schedule(schedules)
            if current_schedule:
                operation = current_schedule.get("startActions", {}).get("operationName", "0")
                time_range = self._format_schedule_time(current_schedule)
                mode = self._get_operation_name(operation)
                active_label = self._translate_schedule_state("active")
                return f"{active_label}: {time_range} - {mode}"

            # Compter les programmations actives
            enabled_count = sum(1 for s in schedules if s.get("enabled", False))
            active_schedules_label = self._translate_schedule_state("active_schedules")
            return f"{enabled_count} {active_schedules_label}"

        except Exception:
            pass
            return self._translate_schedule_state("error")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        attrs = {}

        try:
            schedules = self._get_schedules_data()
            if schedules:
                # Formater les programmations pour l'affichage
                formatted_schedules = []
                for schedule in schedules:
                    if schedule.get("enabled", False):
                        time_range = self._format_schedule_time(schedule)
                        operation = schedule.get("startActions", {}).get("operationName", "0")
                        mode = self._get_operation_name(operation)

                        formatted_schedules.append(
                            {
                                "id": schedule.get("id"),
                                "time": time_range,
                                "mode": mode,
                                "state": schedule.get("state", "IDLE"),
                            }
                        )

                attrs["schedules"] = formatted_schedules
                attrs["total_schedules"] = len(schedules)
                attrs["enabled_schedules"] = len(formatted_schedules)

                # Trouver le prochain schedule
                current_schedule = self._get_current_schedule(schedules)
                if current_schedule:
                    attrs["current_schedule_id"] = current_schedule.get("id")
                    attrs["current_mode"] = self._get_operation_name(
                        current_schedule.get("startActions", {}).get("operationName", "0")
                    )

        except Exception as e:
            attrs["error"] = str(e)

        return attrs


class FluidraDeviceInfoSensor(FluidraPoolSensorEntity):
    """Sensor for displaying device information and diagnostics."""

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api,
        pool_id: str,
        device_id: str,
    ) -> None:
        """Initialize the device info sensor."""
        super().__init__(coordinator, api, pool_id, device_id, "info")
        self._attr_translation_key = "device_info"

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        device_name = self.device_data.get("name")
        if not device_name:
            # Get device type for proper fallback name
            device_type = self.device_data.get("type", "device")
            device_name = f"{device_type.replace('_', ' ').title()} {self._device_id}"
        return f"{device_name} Information"

    @property
    def icon(self) -> str:
        """Return the icon for the entity."""
        return "mdi:information-outline"

    def _translate_device_info(self, key: str) -> str:
        """Return device info text in English (technical terms)."""
        # Always return English for technical dynamic text
        # HA config.language returns system language, not user interface language
        translations = {
            "unknown": "Unknown",
            "firmware": "Firmware",
            "signal": "Signal",
            "error": "Error",
            "excellent": "Excellent",
            "very_good": "Very Good",
            "good": "Good",
            "low": "Low",
            "very_low": "Very Low",
            "connected": "Connected",
            "disconnected": "Disconnected",
        }
        return translations.get(key, key)

    def _get_device_info_data(self) -> dict[str, Any]:
        """Get device information from coordinator data."""
        # Récupérer les informations du device depuis le coordinateur
        device_data = self.device_data

        # Extraire les components d'information
        info_data = {}

        # Ces données viennent du Component 0, 1, 2, 3, etc. via le coordinateur
        if "device_id_component" in device_data:
            info_data["device_id"] = device_data["device_id_component"]
        if "part_numbers_component" in device_data:
            info_data["part_numbers"] = device_data["part_numbers_component"]
        if "signal_strength_component" in device_data:
            info_data["signal_strength"] = device_data["signal_strength_component"]
        if "firmware_version_component" in device_data:
            info_data["firmware_version"] = device_data["firmware_version_component"]
        if "hardware_errors_component" in device_data:
            info_data["hardware_errors"] = device_data["hardware_errors_component"]
        if "comm_errors_component" in device_data:
            info_data["comm_errors"] = device_data["comm_errors_component"]
        if "timezone_component" in device_data:
            info_data["timezone"] = device_data["timezone_component"]
        if "network_status_component" in device_data:
            info_data["network_status"] = device_data["network_status_component"]

        return info_data

    @property
    def native_value(self) -> str:
        """Return the state of the sensor."""
        try:
            info_data = self._get_device_info_data()
            signal = info_data.get("signal_strength", 0)
            signal_label = self._translate_device_info("signal")

            # Only show signal (firmware not reliable across devices)
            if signal and signal != 0:
                return f"{signal_label}: {signal} dBm"
            return self._translate_device_info("online")

        except Exception:
            pass
            return self._translate_device_info("error")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        attrs = {}

        try:
            info_data = self._get_device_info_data()

            # Informations de base
            if "device_id" in info_data:
                attrs["device_id"] = info_data["device_id"]
            if "part_numbers" in info_data:
                attrs["part_numbers"] = info_data["part_numbers"]

            # Informations réseau
            if "signal_strength" in info_data:
                signal = info_data["signal_strength"]
                attrs["signal_strength_dbm"] = signal
                if signal and isinstance(signal, (int, float)):
                    # Convertir dBm en qualité de signal
                    if signal >= -50:
                        attrs["signal_quality"] = self._translate_device_info("excellent")
                    elif signal >= -60:
                        attrs["signal_quality"] = self._translate_device_info("very_good")
                    elif signal >= -70:
                        attrs["signal_quality"] = self._translate_device_info("good")
                    elif signal >= -80:
                        attrs["signal_quality"] = self._translate_device_info("low")
                    else:
                        attrs["signal_quality"] = self._translate_device_info("very_low")

            if "network_status" in info_data:
                network_status = info_data["network_status"]
                attrs["network_status"] = (
                    self._translate_device_info("connected")
                    if network_status == 1
                    else self._translate_device_info("disconnected")
                )

            # Informations système
            if "firmware_version" in info_data:
                attrs["firmware_version"] = info_data["firmware_version"]

            # Informations de diagnostic
            if "hardware_errors" in info_data:
                attrs["hardware_error_count"] = info_data["hardware_errors"]
            if "comm_errors" in info_data:
                attrs["communication_error_count"] = info_data["comm_errors"]

            # Configuration
            if "timezone" in info_data:
                attrs["timezone_info"] = info_data["timezone"]

            # Informations statiques du device
            attrs["device_name"] = self.device_data.get("name", "Unknown")
            attrs["device_type"] = self.device_data.get("type", "unknown")
            attrs["manufacturer"] = self.device_data.get("manufacturer", "Fluidra")
            attrs["model"] = self.device_data.get("model", "Unknown")
            attrs["online"] = self.device_data.get("online", False)

        except Exception as e:
            attrs["error"] = str(e)

        return attrs


# NOUVEAUX SENSORS SPÉCIFIQUES À LA PISCINE


class FluidraPoolSensorBase(CoordinatorEntity, SensorEntity):
    """Base class for pool-specific sensor entities."""

    def __init__(self, coordinator, api, pool_id: str, sensor_type: str = ""):
        """Initialize the pool sensor."""
        super().__init__(coordinator)
        self._api = api
        self._pool_id = pool_id
        self._sensor_type = sensor_type

    @property
    def pool_data(self) -> dict:
        """Get pool data from coordinator."""
        if self.coordinator.data is None:
            return {}
        return self.coordinator.data.get(self._pool_id, {})

    @property
    def unique_id(self) -> str:
        """Return unique ID."""
        suffix = f"_{self._sensor_type}" if self._sensor_type else ""
        return f"{DOMAIN}_{self._pool_id}_pool{suffix}"

    @property
    def device_info(self) -> dict:
        """Return device info for the pool."""
        pool_name = self.pool_data.get("name", f"Pool {self._pool_id}")
        return {
            "identifiers": {(DOMAIN, self._pool_id)},
            "name": pool_name,
            "manufacturer": "Fluidra",
            "model": "Pool System",
            "sw_version": "1.0",
        }

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.last_update_success


class FluidraPoolWeatherSensor(FluidraPoolSensorBase):
    """Sensor for weather temperature at pool location."""

    def __init__(self, coordinator, api, pool_id: str):
        """Initialize the pool weather sensor."""
        super().__init__(coordinator, api, pool_id, "weather")
        self._attr_translation_key = "weather_temperature"

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        pool_name = self.pool_data.get("name", f"Pool {self._pool_id}")
        return f"{pool_name} Weather Temperature"

    @property
    def native_value(self) -> float | None:
        """Return the weather temperature."""
        pool_data = self.pool_data

        # Chercher dans les données météo/status
        status_data = pool_data.get("status_data", {})
        weather = status_data.get("weather", {})
        if weather.get("status") == "ok":
            weather_value = weather.get("value")
            if weather_value is not None and isinstance(weather_value, dict):
                current = weather_value.get("current", {})
                if isinstance(current, dict) and "main" in current and "temp" in current["main"]:
                    # Convertir de Kelvin vers Celsius
                    temp_kelvin = current["main"]["temp"]
                    return round(temp_kelvin - 273.15, 1)

        return None

    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit of measurement."""
        return UnitOfTemperature.CELSIUS

    @property
    def device_class(self) -> SensorDeviceClass:
        """Return the device class."""
        return SensorDeviceClass.TEMPERATURE

    @property
    def state_class(self) -> SensorStateClass:
        """Return the state class."""
        return SensorStateClass.MEASUREMENT

    @property
    def icon(self) -> str:
        """Return the icon of the sensor."""
        return "mdi:thermometer"


class FluidraPoolStatusSensor(FluidraPoolSensorBase):
    """Sensor for overall pool status."""

    def __init__(self, coordinator, api, pool_id: str):
        """Initialize the pool status sensor."""
        super().__init__(coordinator, api, pool_id, "status")
        self._attr_translation_key = "pool_status"

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        pool_name = self.pool_data.get("name", f"Pool {self._pool_id}")
        return f"{pool_name} Status"

    @property
    def native_value(self) -> str:
        """Return the pool status."""
        pool_data = self.pool_data

        # Return state key for translation
        state = pool_data.get("state", "unknown")

        if state == "using":
            return "using"
        if state == "maintenance":
            return "maintenance"
        if state == "offline":
            return "offline"
        if state == "winterized":
            return "winterized"
        # État par défaut basé sur les données disponibles
        if pool_data.get("name"):
            return "connected"
        return "unknown_state"

    @property
    def icon(self) -> str:
        """Return the icon of the sensor."""
        pool_data = self.pool_data
        state = pool_data.get("state", "unknown")

        if state == "using":
            return "mdi:pool"
        if state == "maintenance":
            return "mdi:tools"
        if state == "offline":
            return "mdi:pool-off"
        if state == "winterized":
            return "mdi:snowflake"
        return "mdi:help-circle"

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional state attributes."""
        pool_data = self.pool_data
        attrs = {}

        # Informations générales
        attrs["pool_state"] = pool_data.get("state", "unknown")
        if "owner" in pool_data:
            attrs["owner_id"] = pool_data["owner"]

        # Caractéristiques de la piscine
        characteristics = pool_data.get("characteristics", {})
        if characteristics:
            attrs["shape"] = characteristics.get("shape")
            attrs["construction_year"] = characteristics.get("constructionYear")
            attrs["waterproof"] = characteristics.get("waterproof")
            attrs["ground"] = characteristics.get("ground")
            attrs["place"] = characteristics.get("place")
            attrs["pool_type"] = characteristics.get("type")

            dimensions = characteristics.get("dimensions", {})
            if "volume" in dimensions:
                attrs["volume_m3"] = dimensions["volume"]

        # Désinfection
        disinfection = pool_data.get("disinfection", {})
        if disinfection:
            method = disinfection.get("method", {})
            attrs["disinfection_type"] = method.get("type")
            attrs["disinfection_method"] = method.get("name")
            attrs["automatic_disinfection"] = disinfection.get("automatic", False)

        # Statistiques des équipements
        devices = pool_data.get("devices", [])
        attrs["total_devices"] = len(devices)

        # Types d'équipements
        device_types = {}
        for device in devices:
            device_type = device.get("type", "unknown")
            device_types[device_type] = device_types.get(device_type, 0) + 1
        attrs["device_types"] = device_types

        # Informations météo si disponibles
        status_data = pool_data.get("status_data", {})
        weather = status_data.get("weather", {})
        if weather.get("status") == "ok":
            weather_value = weather.get("value")
            if weather_value is not None:
                current = weather_value.get("current", {})
                if current:
                    attrs["weather_available"] = True
                    if "main" in current:
                        attrs["air_temperature"] = round(current["main"]["temp"] - 273.15, 1)
                        attrs["humidity"] = current["main"]["humidity"]
                        attrs["pressure"] = current["main"]["pressure"]
                    if "wind" in current:
                        attrs["wind_speed"] = current["wind"]["speed"]

        return attrs


class FluidraPoolLocationSensor(FluidraPoolSensorBase):
    """Sensor for pool location and geographic information."""

    def __init__(self, coordinator, api, pool_id: str):
        """Initialize the pool location sensor."""
        super().__init__(coordinator, api, pool_id, "location")
        self._attr_translation_key = "pool_location"

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        pool_name = self.pool_data.get("name", f"Pool {self._pool_id}")
        return f"{pool_name} Location"

    @property
    def native_value(self) -> str:
        """Return the pool location."""
        pool_data = self.pool_data

        # Chercher dans les données de géolocalisation
        geolocation = pool_data.get("geolocation", {})
        if geolocation:
            locality = geolocation.get("locality")
            country_code = geolocation.get("countryCode")

            if locality and country_code:
                return f"{locality}, {country_code}"
            if locality:
                return locality
            if country_code:
                return country_code

        return "Localisation inconnue"

    @property
    def icon(self) -> str:
        """Return the icon of the sensor."""
        return "mdi:map-marker"

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional state attributes."""
        pool_data = self.pool_data
        attrs = {}

        # Informations géographiques détaillées
        geolocation = pool_data.get("geolocation", {})
        if geolocation:
            attrs["latitude"] = geolocation.get("latitude")
            attrs["longitude"] = geolocation.get("longitude")
            attrs["locality"] = geolocation.get("locality")
            attrs["country_code"] = geolocation.get("countryCode")

        # Informations météo si disponibles
        status_data = pool_data.get("status_data", {})
        weather = status_data.get("weather", {})
        if weather.get("status") == "ok":
            weather_value = weather.get("value")
            if weather_value is not None:
                current = weather_value.get("current", {})
                if current:
                    attrs["weather_country"] = current.get("sys", {}).get("country")
                    attrs["timezone"] = weather_value.get("current", {}).get("timezone")

        return attrs


class FluidraPoolWaterQualitySensor(FluidraPoolSensorBase):
    """Sensor for pool water quality information."""

    def __init__(self, coordinator, api, pool_id: str):
        """Initialize the pool water quality sensor."""
        super().__init__(coordinator, api, pool_id, "water_quality")
        self._attr_translation_key = "water_quality"

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        pool_name = self.pool_data.get("name", f"Pool {self._pool_id}")
        return f"{pool_name} Water Quality"

    @property
    def native_value(self) -> str:
        """Return the water quality status."""
        pool_data = self.pool_data

        # Chercher dans les données de désinfection
        disinfection = pool_data.get("disinfection", {})
        if disinfection:
            automatic = disinfection.get("automatic", False)

            if automatic:
                return "auto"
            return "manual"

        return "not_configured"

    @property
    def icon(self) -> str:
        """Return the icon of the sensor."""
        return "mdi:water-check"

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional state attributes."""
        pool_data = self.pool_data
        attrs = {}

        # Informations de désinfection détaillées
        disinfection = pool_data.get("disinfection", {})
        if disinfection:
            method = disinfection.get("method", {})
            attrs["disinfection_type"] = method.get("type")
            attrs["disinfection_method"] = method.get("name")
            attrs["automatic_disinfection"] = disinfection.get("automatic", False)

        # Plages de qualité de l'eau
        water_quality_ranges = pool_data.get("waterQualitySensorRanges", {})
        if water_quality_ranges:
            # pH
            ph_data = water_quality_ranges.get("ph", {})
            if ph_data:
                attrs["ph_min"] = ph_data.get("minValue")
                attrs["ph_max"] = ph_data.get("maxValue")
                attrs["ph_unit"] = ph_data.get("unit")

            # Chlore
            chlorine_data = water_quality_ranges.get("chlorine", {})
            if chlorine_data:
                attrs["chlorine_min"] = chlorine_data.get("minValue")
                attrs["chlorine_max"] = chlorine_data.get("maxValue")
                attrs["chlorine_unit"] = chlorine_data.get("unit")

            # Salinité
            salinity_data = water_quality_ranges.get("salinity", {})
            if salinity_data:
                attrs["salinity_min"] = salinity_data.get("minValue")
                attrs["salinity_max"] = salinity_data.get("maxValue")
                attrs["salinity_unit"] = salinity_data.get("unit")

            # ORP (Potentiel d'oxydoréduction)
            orp_data = water_quality_ranges.get("orp", {})
            if orp_data:
                attrs["orp_min"] = orp_data.get("minValue")
                attrs["orp_max"] = orp_data.get("maxValue")
                attrs["orp_unit"] = orp_data.get("unit")

        # Qualité de l'eau actuelle si disponible
        water_quality = pool_data.get("water_quality", {})
        if water_quality:
            attrs["current_water_quality"] = water_quality

        # Ajout d'informations sur les caractéristiques de la piscine liées à l'eau
        characteristics = pool_data.get("characteristics", {})
        if characteristics:
            dimensions = characteristics.get("dimensions", {})
            if "volume" in dimensions:
                attrs["pool_volume_m3"] = dimensions["volume"]

            attrs["pool_type"] = characteristics.get("type")
            attrs["waterproof"] = characteristics.get("waterproof")

        return attrs


class FluidraChlorinatorSensor(CoordinatorEntity, SensorEntity):
    """Sensor for chlorinator measurements (pH, ORP, chlorine, temperature, salinity)."""

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api,
        pool_id: str,
        device_id: str,
        sensor_type: str,
        component_id: int,
    ) -> None:
        """Initialize the chlorinator sensor."""
        super().__init__(coordinator)
        self._api = api
        self._pool_id = pool_id
        self._device_id = device_id
        self._sensor_type = sensor_type
        self._component_id = component_id

        device_name = self.device_data.get("name") or f"Chlorinator {self._device_id}"

        # Sensor configuration based on type
        self._sensor_config = {
            "ph": {
                "name": f"{device_name} pH",
                "unit": None,
                "device_class": None,
                "state_class": SensorStateClass.MEASUREMENT,
                "icon": "mdi:ph",
                "divisor": 100,  # Component value is pH * 100 (720 = 7.20)
            },
            "orp": {
                "name": f"{device_name} ORP",
                "unit": "mV",
                "device_class": SensorDeviceClass.VOLTAGE,
                "state_class": SensorStateClass.MEASUREMENT,
                "icon": "mdi:lightning-bolt",
                "divisor": 1,
            },
            "free_chlorine": {
                "name": f"{device_name} Free Chlorine",
                "unit": "mg/L",
                "device_class": None,
                "state_class": SensorStateClass.MEASUREMENT,
                "icon": "mdi:test-tube",
                "divisor": 100,  # Component value is mg/L * 100
            },
            "temperature": {
                "name": f"{device_name} Water Temperature",
                "unit": UnitOfTemperature.CELSIUS,
                "device_class": SensorDeviceClass.TEMPERATURE,
                "state_class": SensorStateClass.MEASUREMENT,
                "icon": "mdi:thermometer",
                "divisor": 10,  # Component value is °C * 10
            },
            "salinity": {
                "name": f"{device_name} Salinity",
                "unit": "g/L",
                "device_class": None,
                "state_class": SensorStateClass.MEASUREMENT,
                "icon": "mdi:water-opacity",
                "divisor": 100,  # Component value is g/L * 100
            },
            "chlorination_actual": {
                "name": f"{device_name} Chlorination Actual",
                "unit": PERCENTAGE,
                "device_class": None,
                "state_class": SensorStateClass.MEASUREMENT,
                "icon": "mdi:percent",
                "divisor": 1,  # Component value is already percentage
            },
        }

        config = self._sensor_config.get(sensor_type, {})
        self._attr_name = config.get("name", f"{device_name} {sensor_type}")
        self._attr_unique_id = f"fluidra_{self._device_id}_{sensor_type}"
        self._attr_native_unit_of_measurement = config.get("unit")
        self._attr_device_class = config.get("device_class")
        self._attr_state_class = config.get("state_class")
        self._attr_icon = config.get("icon")
        self._divisor = config.get("divisor", 1)

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
    def device_info(self) -> dict[str, Any]:
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
        """Return the sensor value."""
        components = self.device_data.get("components", {})
        component_data = components.get(str(self._component_id), {})
        raw_value = component_data.get("reportedValue")

        if raw_value is None:
            return None

        try:
            # Apply divisor to get actual value
            return float(raw_value) / self._divisor
        except Exception:
            pass
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        components = self.device_data.get("components", {})
        component_data = components.get(str(self._component_id), {})

        return {
            "component_id": self._component_id,
            "sensor_type": self._sensor_type,
            "raw_value": component_data.get("reportedValue"),
            "divisor": self._divisor,
            "device_id": self._device_id,
        }
