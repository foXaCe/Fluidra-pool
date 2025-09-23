"""Sensor platform for Fluidra Pool integration."""

import logging
from datetime import datetime, time
from typing import Optional, List, Dict, Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature, PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import FluidraDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Fluidra Pool sensor entities."""
    coordinator: FluidraDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    entities = []

    pools = await coordinator.api.get_pools()
    for pool in pools:
        for device in pool["devices"]:
            device_type = device.get("type", "").lower()
            device_id = device.get("device_id")

            if not device_id:
                continue

            # Temperature sensors for heaters
            if ("heater" in device_type or "heat" in device_type) and "current_temperature" in device:
                entities.append(FluidraTemperatureSensor(coordinator, coordinator.api, pool["id"], device_id, "current"))

            if ("heater" in device_type or "heat" in device_type) and "target_temperature" in device:
                entities.append(FluidraTemperatureSensor(coordinator, coordinator.api, pool["id"], device_id, "target"))


            # Schedule sensor for pumps
            if "pump" in device_type:
                entities.append(FluidraPumpScheduleSensor(coordinator, coordinator.api, pool["id"], device_id))
                # Device info sensors
                entities.append(FluidraDeviceInfoSensor(coordinator, coordinator.api, pool["id"], device_id))
                # Speed sensor for pumps
                entities.append(FluidraPumpSpeedSensor(coordinator, coordinator.api, pool["id"], device_id))

            # Brightness sensor for lights
            if "light" in device_type and "brightness" in device:
                entities.append(FluidraLightBrightnessSensor(coordinator, coordinator.api, pool["id"], device_id))

        # Sensors spécifiques à la piscine (pas liés aux devices)
        entities.append(FluidraPoolWeatherSensor(coordinator, coordinator.api, pool["id"]))
        entities.append(FluidraPoolStatusSensor(coordinator, coordinator.api, pool["id"]))
        entities.append(FluidraPoolLocationSensor(coordinator, coordinator.api, pool["id"]))
        entities.append(FluidraPoolWaterQualitySensor(coordinator, coordinator.api, pool["id"]))

    async_add_entities(entities)




class FluidraPoolSensorEntity(CoordinatorEntity, SensorEntity):
    """Base class for Fluidra Pool sensor entities."""

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
    """Temperature sensor for pool heaters."""

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        temp_type = "Current" if self._sensor_type == "current" else "Target"
        device_name = self.device_data.get('name', f'Device {self._device_id}')
        pool_name = self.pool_data.get('name', 'Pool')
        return f"{pool_name} {device_name} {temp_type} Temperature"

    @property
    def native_value(self) -> Optional[float]:
        """Return the state of the sensor."""
        if self._sensor_type == "current":
            return self.device_data.get("current_temperature")
        elif self._sensor_type == "target":
            return self.device_data.get("target_temperature")
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
        device_name = self.device_data.get('name', f'Device {self._device_id}')
        pool_name = self.pool_data.get('name', 'Pool')
        return f"{pool_name} {device_name} Brightness"

    @property
    def native_value(self) -> Optional[int]:
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
        device_name = self.device_data.get("name") or f"E30iQ Pump {self._device_id}"
        return f"{device_name} Vitesse"

    @property
    def icon(self) -> str:
        """Return the icon for the entity."""
        speed_mode = self._get_speed_mode()
        if "Arrêtée" in speed_mode or "Pas en marche" in speed_mode:
            return "mdi:pump-off"
        elif "Auto" in speed_mode:
            return "mdi:autorenew"
        else:
            return "mdi:pump"

    def _get_speed_mode(self) -> str:
        """Get the current speed with percentage display."""
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

        # Si pompe arrêtée
        if not is_running:
            return "Arrêtée"

        # Récupérer le pourcentage actuel
        current_speed = self.device_data.get("speed_percent", 0)

        if current_speed == 0:
            return "Pas en marche"

        # Afficher simplement le pourcentage avec le mode
        if auto_mode:
            return f"{current_speed}%"
        else:
            return f"{current_speed}%"

    def _get_current_schedule_mode(self) -> str:
        """Get current speed mode from active schedule (more reliable than polling)."""
        try:
            # Récupérer les données des schedulers depuis le device
            schedule_data = self.device_data.get("schedule_data", [])
            if not schedule_data:
                return ""

            from datetime import datetime, time

            def _parse_cron_time(cron_time: str):
                """Parse cron time format to time object."""
                try:
                    parts = cron_time.split()
                    if len(parts) >= 2:
                        minute = int(parts[0])
                        hour = int(parts[1])
                        return time(hour, minute)
                except (ValueError, IndexError):
                    pass
                return None

            def _get_operation_name(operation: str) -> str:
                """Convert operation name to readable format."""
                operation_map = {
                    "0": "Faible",      # 45%
                    "1": "Moyenne",     # 65%
                    "2": "Élevée"       # 100%
                }
                return operation_map.get(operation, f"Mode {operation}")

            # Trouver le schedule actif maintenant
            now = datetime.now().time()
            for schedule in schedule_data:
                if not schedule.get("enabled", False):
                    continue

                start_time = _parse_cron_time(schedule.get("startTime", ""))
                end_time = _parse_cron_time(schedule.get("endTime", ""))

                if start_time and end_time:
                    if start_time <= now <= end_time:
                        operation = schedule.get("startActions", {}).get("operationName", "0")
                        mode = _get_operation_name(operation)
                        # Ajouter l'état du schedule pour debug
                        state = schedule.get("state", "IDLE")
                        if state == "RUNNING":
                            return f"{mode} (Active)"
                        else:
                            return mode

        except Exception as e:
            # En cas d'erreur, ne pas planter le sensor
            pass

        return ""

    @property
    def native_value(self) -> str:
        """Return the state of the sensor."""
        return self._get_speed_mode()

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

        attrs = {
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
            }
        }


        return attrs


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

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        device_name = self.device_data.get("name") or f"E30iQ Pump {self._device_id}"
        return f"{device_name} Programmations"

    @property
    def icon(self) -> str:
        """Return the icon for the entity."""
        return "mdi:calendar-clock"

    def _parse_cron_time(self, cron_time: str) -> Optional[time]:
        """Parse cron time format 'mm HH * * 0,1,2,3,4,5,6' to time object."""
        try:
            parts = cron_time.split()
            if len(parts) >= 2:
                minute = int(parts[0])
                hour = int(parts[1])
                return time(hour, minute)
        except (ValueError, IndexError):
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
        operation_map = {
            "0": "Faible (45%)",
            "1": "Moyenne (65%)",
            "2": "Élevée (100%)"
        }
        return operation_map.get(operation, f"Mode {operation}")

    def _get_current_schedule(self, schedules: List[dict]) -> Optional[dict]:
        """Get currently active schedule based on current time."""
        now = datetime.now().time()

        for schedule in schedules:
            if not schedule.get("enabled", False):
                continue

            start_time = self._parse_cron_time(schedule.get("startTime", ""))
            end_time = self._parse_cron_time(schedule.get("endTime", ""))

            if start_time and end_time:
                if start_time <= now <= end_time:
                    return schedule
        return None

    def _get_schedules_data(self) -> List[dict]:
        """Get schedules data from device data."""
        # Chercher dans les données du coordinateur d'abord
        device_data = self.device_data
        _LOGGER.debug(f"[SENSOR] Device data keys: {list(device_data.keys())}")

        if "schedule_data" in device_data:
            schedules = device_data["schedule_data"]
            _LOGGER.info(f"[SENSOR] ✅ Found {len(schedules)} schedules in schedule_data")
            for i, schedule in enumerate(schedules):
                _LOGGER.debug(f"[SENSOR] Schedule {i}: id={schedule.get('id')}, enabled={schedule.get('enabled')}")
            return schedules
        else:
            _LOGGER.warning(f"[SENSOR] ❌ No 'schedule_data' key found in device data")
            return []

    @property
    def native_value(self) -> str:
        """Return the state of the sensor."""
        try:
            schedules = self._get_schedules_data()
            if not schedules:
                return "Aucune programmation"

            # Vérifier s'il y a une programmation active maintenant
            current_schedule = self._get_current_schedule(schedules)
            if current_schedule:
                operation = current_schedule.get("startActions", {}).get("operationName", "0")
                time_range = self._format_schedule_time(current_schedule)
                mode = self._get_operation_name(operation)
                return f"Actif: {time_range} - {mode}"

            # Compter les programmations actives
            enabled_count = sum(1 for s in schedules if s.get("enabled", False))
            return f"{enabled_count} programmations actives"

        except Exception as e:
            _LOGGER.error(f"Error getting schedule state: {e}")
            return "Erreur"

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
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

                        formatted_schedules.append({
                            "id": schedule.get("id"),
                            "time": time_range,
                            "mode": mode,
                            "state": schedule.get("state", "IDLE")
                        })

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
            _LOGGER.error(f"Error getting schedule attributes: {e}")
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

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        device_name = self.device_data.get("name") or f"E30iQ Pump {self._device_id}"
        return f"{device_name} Informations"

    @property
    def icon(self) -> str:
        """Return the icon for the entity."""
        return "mdi:information-outline"

    def _get_device_info_data(self) -> Dict[str, Any]:
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

            # Afficher le firmware en état principal
            firmware = info_data.get("firmware_version", "Inconnu")
            signal = info_data.get("signal_strength", 0)

            if signal and signal != 0:
                return f"Firmware {firmware} (Signal: {signal} dBm)"
            else:
                return f"Firmware {firmware}"

        except Exception as e:
            _LOGGER.error(f"Error getting device info state: {e}")
            return "Erreur"

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
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
                if signal:
                    # Convertir dBm en qualité de signal
                    if signal >= -50:
                        attrs["signal_quality"] = "Excellent"
                    elif signal >= -60:
                        attrs["signal_quality"] = "Très bon"
                    elif signal >= -70:
                        attrs["signal_quality"] = "Bon"
                    elif signal >= -80:
                        attrs["signal_quality"] = "Faible"
                    else:
                        attrs["signal_quality"] = "Très faible"

            if "network_status" in info_data:
                network_status = info_data["network_status"]
                attrs["network_status"] = "Connecté" if network_status == 1 else "Déconnecté"

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
            attrs["device_name"] = self.device_data.get("name", "E30iQ")
            attrs["device_type"] = self.device_data.get("type", "pump")
            attrs["manufacturer"] = self.device_data.get("manufacturer", "Fluidra")
            attrs["model"] = self.device_data.get("model", "E30iQ")
            attrs["online"] = self.device_data.get("online", False)

        except Exception as e:
            _LOGGER.error(f"Error getting device info attributes: {e}")
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

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        pool_name = self.pool_data.get("name", f"Pool {self._pool_id}")
        return f"{pool_name} Température extérieure"

    @property
    def native_value(self) -> Optional[float]:
        """Return the weather temperature."""
        pool_data = self.pool_data

        # Chercher dans les données météo/status
        status_data = pool_data.get("status_data", {})
        weather = status_data.get("weather", {})
        if weather.get("status") == "ok":
            weather_value = weather.get("value", {})
            current = weather_value.get("current", {})
            if "main" in current and "temp" in current["main"]:
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

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        pool_name = self.pool_data.get("name", f"Pool {self._pool_id}")
        return f"{pool_name} Statut"

    @property
    def native_value(self) -> str:
        """Return the pool status."""
        pool_data = self.pool_data

        # Vérifier l'état de la piscine depuis l'API
        state = pool_data.get("state", "unknown")

        if state == "using":
            # Piscine en utilisation - vérifier les équipements
            devices = pool_data.get("devices", [])
            total_devices = len(devices)

            if total_devices > 0:
                return f"En service ({total_devices} équipement{'s' if total_devices > 1 else ''})"
            else:
                return "En service"
        elif state == "maintenance":
            return "En maintenance"
        elif state == "offline":
            return "Hors ligne"
        elif state == "winterized":
            return "Hivernage"
        else:
            # État par défaut basé sur les données disponibles
            if pool_data.get("name"):
                return "Connectée"
            else:
                return "État inconnu"

    @property
    def icon(self) -> str:
        """Return the icon of the sensor."""
        pool_data = self.pool_data
        state = pool_data.get("state", "unknown")

        if state == "using":
            return "mdi:pool"
        elif state == "maintenance":
            return "mdi:tools"
        elif state == "offline":
            return "mdi:pool-off"
        elif state == "winterized":
            return "mdi:snowflake"
        else:
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
            weather_value = weather.get("value", {})
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

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        pool_name = self.pool_data.get("name", f"Pool {self._pool_id}")
        return f"{pool_name} Localisation"

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
            elif locality:
                return locality
            elif country_code:
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
            weather_value = weather.get("value", {})
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

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        pool_name = self.pool_data.get("name", f"Pool {self._pool_id}")
        return f"{pool_name} Qualité de l'eau"

    @property
    def native_value(self) -> str:
        """Return the water quality status."""
        pool_data = self.pool_data

        # Chercher dans les données de désinfection
        disinfection = pool_data.get("disinfection", {})
        if disinfection:
            method = disinfection.get("method", {})
            disinfection_name = method.get("name", "Inconnu")
            automatic = disinfection.get("automatic", False)

            if automatic:
                return f"Auto - {disinfection_name}"
            else:
                return f"Manuel - {disinfection_name}"

        return "Non configuré"

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