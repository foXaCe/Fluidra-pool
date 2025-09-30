"""Data update coordinator for Fluidra Pool integration."""
import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .fluidra_api import FluidraPoolAPI
from .device_registry import DeviceIdentifier

_LOGGER = logging.getLogger(__name__)

UPDATE_INTERVAL = timedelta(seconds=30)


class FluidraDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the Fluidra Pool API."""

    def __init__(self, hass: HomeAssistant, api: FluidraPoolAPI) -> None:
        """Initialize."""
        self.api = api
        self._optimistic_entities = set()  # Entités avec état optimiste actif
        self._previous_schedule_entities = {}  # Track scheduler entities per device for cleanup
        super().__init__(
            hass,
            _LOGGER,
            name="fluidra_pool",
            update_interval=UPDATE_INTERVAL,
        )

    def register_optimistic_entity(self, entity_id: str):
        """Enregistrer une entité comme ayant un état optimiste actif."""
        self._optimistic_entities.add(entity_id)

    def unregister_optimistic_entity(self, entity_id: str):
        """Désenregistrer une entité de l'état optimiste."""
        self._optimistic_entities.discard(entity_id)

    def has_optimistic_entities(self) -> bool:
        """Vérifier si des entités ont un état optimiste actif."""
        return len(self._optimistic_entities) > 0

    async def _cleanup_schedule_sensor_if_empty(self, pool_id: str, device_id: str, schedule_data: list):
        """Clean up schedule sensor entity if no schedules remain."""
        try:
            from homeassistant.helpers import entity_registry as er

            # If no schedules remain, we can consider removing the sensor entity
            if len(schedule_data) == 0:
                entity_registry = er.async_get(self.hass)

                # The unique_id format for schedule sensor is: fluidra_pool_{pool_id}_{device_id}_sensor_schedules
                expected_unique_id = f"fluidra_pool_{pool_id}_{device_id}_sensor_schedules"

                # Look for the schedule sensor entity
                for entity_id, entry in entity_registry.entities.items():
                    if (entry.platform == "fluidra_pool" and
                        entry.unique_id == expected_unique_id):

                        _LOGGER.info(f"🗑️ Removing empty schedule sensor entity: {entity_id} (no schedules remaining)")
                        entity_registry.async_remove(entity_id)
                        break

        except Exception as e:
            _LOGGER.error(f"Error cleaning up schedule sensor for device {device_id}: {e}")

    def _calculate_auto_speed_from_schedules(self, device: dict) -> int:
        """Calculate current speed based on active schedules in auto mode."""
        try:
            from datetime import datetime, time

            schedule_data = device.get("schedule_data", [])
            if not schedule_data:
                return 0

            now = datetime.now()
            current_time = now.time()
            current_weekday = now.weekday()  # 0 = Monday, 6 = Sunday

            # Mapping operationName to percentage (from mitmproxy capture)
            operation_to_percent = {
                "0": 45,   # Faible
                "1": 65,   # Moyenne
                "2": 100   # Élevée
            }

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

            def _parse_cron_days(cron_time: str):
                """Parse cron days format."""
                try:
                    parts = cron_time.split()
                    if len(parts) >= 5:
                        days_str = parts[4]
                        if days_str == "*":
                            return list(range(7))  # All days
                        days = []
                        for day in days_str.split(','):
                            day_num = int(day.strip())
                            # Convert from cron format (0=Sunday) to Python format (0=Monday)
                            if day_num == 0:  # Sunday
                                days.append(6)
                            else:  # Monday-Saturday
                                days.append(day_num - 1)
                        return days
                except (ValueError, IndexError):
                    pass
                return []

            # Check each schedule to see if it's currently active
            for schedule in schedule_data:
                if not schedule.get("enabled", False):
                    continue

                start_time_obj = _parse_cron_time(schedule.get("startTime", ""))
                end_time_obj = _parse_cron_time(schedule.get("endTime", ""))
                schedule_days = _parse_cron_days(schedule.get("startTime", ""))

                if start_time_obj and end_time_obj and current_weekday in schedule_days:
                    # Check if current time is within this schedule
                    if start_time_obj <= current_time <= end_time_obj:
                        operation = schedule.get("startActions", {}).get("operationName", "0")
                        speed_percent = operation_to_percent.get(operation, 0)
                        return speed_percent

            return 0

        except Exception as e:
            _LOGGER.error(f"Error calculating auto speed from schedules: {e}")
            return 0

    async def _async_update_data(self):
        """Update data via library using real-time polling."""
        try:
            # Si des entités ont un état optimiste actif, réduire les mises à jour pour éviter les conflits
            if self.has_optimistic_entities():
                # Retourner les données actuelles sans nouveau polling intensif
                current_data = getattr(self, 'data', None)
                if current_data:
                    return current_data

            # Vérification proactive du token avant chaque polling
            if not await self.api.ensure_valid_token():
                raise UpdateFailed("Token refresh failed")

            # D'abord, récupérer la structure des pools
            pools = await self.api.get_pools()

            # Pour chaque pool, faire le polling temps réel des devices
            for pool in pools:
                pool_id = pool["id"]

                # Récupération des détails spécifiques de la piscine
                pool_details = await self.api.get_pool_details(pool_id)
                if pool_details:
                    # Sauvegarder les devices existants avant la mise à jour
                    existing_devices = pool.get("devices", [])
                    # Mise à jour des données piscine avec les détails de l'API
                    pool.update(pool_details)
                    # Restaurer les devices pour ne pas les écraser
                    pool["devices"] = existing_devices

                # Polling télémétrie qualité de l'eau
                water_quality = await self.api.poll_water_quality(pool_id)
                if water_quality:
                    pool["water_quality"] = water_quality

                # Pour chaque device du pool, faire le polling temps réel
                for device in pool.get("devices", []):
                    device_id = device.get("device_id")
                    if device_id:
                        # Polling de l'état du device
                        device_status = await self.api.poll_device_status(pool_id, device_id)
                        if device_status:
                            device["status"] = device_status
                            device["connectivity"] = device_status.get("connectivity", {})

                        # Initialiser la structure components si elle n'existe pas
                        if "components" not in device:
                            device["components"] = {}

                        # Polling intensif de TOUS les components
                        # Utiliser le registry pour déterminer la plage de composants à scanner
                        component_range = DeviceIdentifier.get_components_range(device)

                        for component_id in range(0, component_range):
                            component_state = await self.api.get_component_state(device_id, component_id)
                            if component_state and isinstance(component_state, dict):
                                reported_value = component_state.get("reportedValue")

                                # Stocker TOUTES les données de component dans la structure components
                                device["components"][str(component_id)] = component_state

                                if component_id == 0:  # Device ID
                                    device["device_id_component"] = reported_value
                                elif component_id == 1:  # Part Numbers
                                    device["part_numbers_component"] = reported_value
                                elif component_id == 2:  # Signal Strength
                                    device["signal_strength_component"] = reported_value
                                elif component_id == 3:  # Firmware Version
                                    device["firmware_version_component"] = reported_value
                                elif component_id == 4:  # Hardware Errors
                                    device["hardware_errors_component"] = reported_value
                                elif component_id == 5:  # Communication Errors
                                    device["comm_errors_component"] = reported_value
                                elif component_id == 9:  # Pompe
                                    device["pump_reported"] = reported_value
                                    device["pump_desired"] = component_state.get("desiredValue")
                                    device["is_running"] = bool(reported_value)
                                elif component_id == 10:  # Mode auto
                                    device["auto_reported"] = reported_value
                                    device["auto_desired"] = component_state.get("desiredValue")
                                    device["auto_mode_enabled"] = bool(reported_value)
                                elif component_id == 11:  # Vitesse levels (utilisé en auto ET manuel)
                                    device["speed_level_reported"] = reported_value
                                    device["speed_level_desired"] = component_state.get("desiredValue")

                                    # Component 11 : Vitesse en mode MANUEL uniquement
                                    if not device.get("is_running", False):
                                        # Pompe arrêtée = toujours 0%
                                        device["speed_percent"] = 0
                                    else:
                                        auto_mode = device.get("auto_mode_enabled", False)
                                        if auto_mode:
                                            # MODE AUTO : Calculer à partir des schedules actifs
                                            current_speed = self._calculate_auto_speed_from_schedules(device)
                                            device["speed_percent"] = current_speed
                                        else:
                                            # MODE MANUEL : Utiliser Component 11
                                            if reported_value == 0:
                                                device["speed_percent"] = 45  # Faible
                                            elif reported_value == 1:
                                                device["speed_percent"] = 65  # Moyenne
                                            elif reported_value == 2:
                                                device["speed_percent"] = 100 # Élevée
                                            else:
                                                device["speed_percent"] = 0   # Défaut
                                elif component_id == 15:  # Température de référence pour pompes à chaleur
                                    device["component_15_speed"] = reported_value or component_state.get("desiredValue") or 0

                                    # Pour les pompes à chaleur, component 15 peut contenir la température × 10
                                    if device.get("type", "").lower() == "heat_pump" and reported_value:
                                        try:
                                            # Convertir la valeur brute en température (diviser par 10)
                                            temp_value = float(reported_value) / 10.0
                                            # Valider la plage de température (10-50°C)
                                            if 10.0 <= temp_value <= 50.0:
                                                device["target_temperature"] = temp_value
                                        except (ValueError, TypeError):
                                            _LOGGER.warning(f"⚠️ Invalid temperature value in component 15: {reported_value}")
                                    # Note: Component 15 n'est plus utilisé pour le mode manuel, remplacé par Component 13
                                elif component_id == 19:  # Température de l'eau (pour pompes à chaleur)
                                    device["timezone_component"] = reported_value
                                    # Component 19 peut contenir la température de l'eau de la piscine × 10
                                    if device.get("type", "").lower() == "heat_pump" and reported_value:
                                        try:
                                            # Convertir la valeur brute en température (diviser par 10)
                                            water_temp_value = float(reported_value) / 10.0
                                            # Valider la plage de température (5-35°C pour l'eau)
                                            if 5.0 <= water_temp_value <= 35.0:
                                                device["water_temperature"] = water_temp_value
                                                _LOGGER.info(f"🌡️ Heat pump {device_id} water temperature from component 19: {water_temp_value}°C")
                                        except (ValueError, TypeError):
                                            pass
                                elif component_id == 20:  # Programmations
                                    schedule_data = reported_value if isinstance(reported_value, list) else []
                                    device["schedule_data"] = schedule_data

                                    # Track current scheduler count for this device
                                    current_schedule_count = len(schedule_data)
                                    device_key = f"{pool_id}_{device_id}"

                                    # Check if we had schedulers before and now have fewer (indicating deletion)
                                    previous_count = self._previous_schedule_entities.get(device_key, 0)
                                    if previous_count > 0 and current_schedule_count < previous_count:
                                        _LOGGER.info(f"🗑️ Schedule count decreased for device {device_id}: {previous_count} → {current_schedule_count}")
                                        # Trigger entity registry cleanup for this device's schedule sensor
                                        await self._cleanup_schedule_sensor_if_empty(pool_id, device_id, schedule_data)

                                    # Update tracking count
                                    self._previous_schedule_entities[device_key] = current_schedule_count
                                elif component_id == 21:  # Network Status
                                    device["network_status_component"] = reported_value
                                elif component_id == 13:  # Component 13 - État pompe à chaleur
                                    device["component_13_data"] = component_state

                                    # Pour les pompes à chaleur, component 13 indique l'état de chauffage
                                    if device.get("type", "").lower() == "heat_pump":
                                        device["heat_pump_reported"] = reported_value
                                        device["is_heating"] = bool(reported_value)
                                elif component_id == 14:  # Component 14 exploration
                                    device["component_14_data"] = component_state
                                elif component_id in [62, 65]:  # Température de l'eau alternative
                                    # Components 62 et 65 peuvent aussi contenir la température de l'eau × 10
                                    if device.get("type", "").lower() == "heat_pump" and reported_value:
                                        try:
                                            water_temp_value = float(reported_value) / 10.0
                                            if 5.0 <= water_temp_value <= 35.0:
                                                # Utiliser seulement si pas déjà défini par component 19
                                                if "water_temperature" not in device:
                                                    device["water_temperature"] = water_temp_value
                                                    _LOGGER.info(f"🌡️ Heat pump {device_id} water temperature from component {component_id}: {water_temp_value}°C")
                                        except (ValueError, TypeError):
                                            pass
                                    device[f"component_{component_id}_data"] = component_state
                                else:  # TOUS les autres components - exploration intensive
                                    device[f"component_{component_id}_data"] = component_state

            return {pool['id']: pool for pool in pools}

        except Exception as err:
            _LOGGER.error(f"Error updating Fluidra Pool data: {err}")
            raise UpdateFailed(f"Error communicating with API: {err}") from err