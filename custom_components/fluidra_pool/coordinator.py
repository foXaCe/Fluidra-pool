"""Data update coordinator for Fluidra Pool integration."""

from datetime import timedelta
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN
from .device_registry import DeviceIdentifier
from .fluidra_api import FluidraPoolAPI

_LOGGER = logging.getLogger(__name__)

UPDATE_INTERVAL = timedelta(seconds=15)


class FluidraDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the Fluidra Pool API."""

    def __init__(self, hass: HomeAssistant, api: FluidraPoolAPI, config_entry=None) -> None:
        """Initialize."""
        self.api = api
        self.config_entry = config_entry  # Store config entry for device cleanup
        self._optimistic_entities = set()  # Entités avec état optimiste actif
        self._previous_schedule_entities = {}  # Track scheduler entities per device for cleanup
        self._first_update = True  # Skip heavy polling on first update for faster startup
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

    async def _cleanup_removed_devices(self, current_device_ids: set):
        """Remove devices and entities that no longer exist in Fluidra API."""
        if not self.config_entry:
            return  # Cannot cleanup without config entry

        try:
            device_registry = dr.async_get(self.hass)
            entity_registry = er.async_get(self.hass)

            # Get all devices for this integration
            devices_to_check = dr.async_entries_for_config_entry(device_registry, self.config_entry.entry_id)

            for device_entry in devices_to_check:
                # Extract device_id from identifiers
                device_id = None
                for identifier in device_entry.identifiers:
                    if identifier[0] == DOMAIN:
                        device_id = identifier[1]
                        break

                if not device_id:
                    continue

                # Skip pool devices (they are parent devices, not actual equipment)
                if device_entry.model == "Pool":
                    continue

                # If device_id not in current API data, remove it
                if device_id not in current_device_ids:
                    # First, remove all entities associated with this device
                    entities_to_remove = er.async_entries_for_device(
                        entity_registry, device_entry.id, include_disabled_entities=True
                    )

                    for entity_entry in entities_to_remove:
                        entity_registry.async_remove(entity_entry.entity_id)

                    # Then remove the device itself
                    device_registry.async_remove_device(device_entry.id)

        except Exception:
            pass

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
                    if entry.platform == "fluidra_pool" and entry.unique_id == expected_unique_id:
                        entity_registry.async_remove(entity_id)
                        break

        except Exception:
            pass

    def _parse_dm24049704_schedule_format(self, reported_value: dict) -> list:
        """Parse DM24049704 chlorinator schedule format (programs/slots) to standard format.

        The API returns component 258 in this format:
        {
            "dayPrograms": {"monday": 1, "tuesday": 1, ...},
            "programs": [{"id": 1, "slots": [{"id": 0, "start": 1280, "end": 1536, "mode": 3}]}]
        }

        Where time is encoded as: hours * 256 + minutes
        Mode 1=S1, 2=S2, 3=S3

        Returns standard schedule format:
        [{"id": 0, "startTime": "0 5 * * 1,2,3,4,5", "endTime": "0 6 * * 1,2,3,4,5",
          "startActions": {"operationName": "3"}, "enabled": True}]
        """
        try:
            if not isinstance(reported_value, dict):
                return []

            day_programs = reported_value.get("dayPrograms", {})
            programs = reported_value.get("programs", [])

            if not programs:
                return []

            # Map day names to CRON day numbers (1=Monday, 7=Sunday)
            day_name_to_cron = {
                "monday": 1,
                "tuesday": 2,
                "wednesday": 3,
                "thursday": 4,
                "friday": 5,
                "saturday": 6,
                "sunday": 7,
            }

            # Group days by program ID
            program_days = {}
            for day_name, program_id in day_programs.items():
                if program_id not in program_days:
                    program_days[program_id] = []
                cron_day = day_name_to_cron.get(day_name.lower())
                if cron_day:
                    program_days[program_id].append(cron_day)

            # Sort days for consistent output
            for days in program_days.values():
                days.sort()

            result = []
            schedule_id = 1  # DM24049704 uses IDs starting at 1

            for program in programs:
                program_id = program.get("id")
                slots = program.get("slots", [])
                days = program_days.get(program_id, [])

                if not days:
                    continue

                days_str = ",".join(str(d) for d in days)

                for slot in slots:
                    start_raw = slot.get("start", 0)
                    end_raw = slot.get("end", 0)
                    mode = slot.get("mode", 0)

                    # Skip empty slots (mode=0 with no time set)
                    if mode == 0 and start_raw == 0 and end_raw == 0:
                        continue

                    # Decode time: hours * 256 + minutes
                    start_hour = start_raw // 256
                    start_minute = start_raw % 256
                    end_hour = end_raw // 256
                    end_minute = end_raw % 256

                    # Create CRON format: "minute hour * * days"
                    start_cron = f"{start_minute} {start_hour} * * {days_str}"
                    end_cron = f"{end_minute} {end_hour} * * {days_str}"

                    result.append(
                        {
                            "id": schedule_id,
                            "groupId": schedule_id,  # groupId must match id for API
                            "startTime": start_cron,
                            "endTime": end_cron,
                            "startActions": {"operationName": str(mode)},
                            "enabled": True,
                        }
                    )
                    schedule_id += 1

            _LOGGER.debug("Parsed DM24049704 schedule: %s -> %s", reported_value, result)
            return result

        except Exception as e:
            _LOGGER.warning("Failed to parse DM24049704 schedule format: %s", e)
            return []

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
                "0": 45,  # Faible
                "1": 65,  # Moyenne
                "2": 100,  # Élevée
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
                        for day in days_str.split(","):
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
                        return operation_to_percent.get(operation, 0)

            return 0

        except Exception:
            return 0

    async def _async_update_data(self):
        """Update data via library using real-time polling."""
        try:
            # Si des entités ont un état optimiste actif, réduire les mises à jour pour éviter les conflits
            if self.has_optimistic_entities():
                # Retourner les données actuelles sans nouveau polling intensif
                current_data = getattr(self, "data", None)
                if current_data:
                    return current_data

            # Vérification proactive du token avant chaque polling
            if not await self.api.ensure_valid_token():
                raise UpdateFailed("Token refresh failed")

            # D'abord, récupérer la structure des pools
            pools = await self.api.get_pools()

            # Premier démarrage : scan minimal pour démarrer vite
            if self._first_update:
                self._first_update = False
                # Le prochain refresh normal (30s) fera le scan complet
                return {pool["id"]: pool for pool in pools}

            # Récupérer les données précédentes pour préserver les components
            previous_data = self.data if isinstance(self.data, dict) else {}

            # Pour chaque pool, faire le polling temps réel des devices
            for pool in pools:
                pool_id = pool["id"]

                # Récupérer les devices précédents avec leurs components
                prev_pool = previous_data.get(pool_id, {})
                prev_devices_by_id = {d.get("device_id"): d for d in prev_pool.get("devices", []) if d.get("device_id")}

                # Récupération des détails spécifiques de la piscine
                pool_details = await self.api.get_pool_details(pool_id)
                if pool_details:
                    # Sauvegarder les devices de l'API avant la mise à jour
                    api_devices = pool.get("devices", [])
                    # Mise à jour des données piscine avec les détails de l'API
                    pool.update(pool_details)
                    # Restaurer les devices
                    pool["devices"] = api_devices

                # Préserver les components des devices précédents
                for device in pool.get("devices", []):
                    device_id = device.get("device_id")
                    if device_id and device_id in prev_devices_by_id:
                        prev_device = prev_devices_by_id[device_id]
                        # Copier les components du précédent refresh
                        if "components" in prev_device:
                            device["components"] = dict(prev_device["components"])

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

                        # Polling des components
                        # Utiliser le registry pour déterminer la plage de composants à scanner
                        component_range = DeviceIdentifier.get_components_range(device)

                        # Check if device has specific components to scan (optimization for chlorinator)
                        specific_components = DeviceIdentifier.get_feature(device, "specific_components", [])

                        # Build list of components to scan
                        components_to_scan = list(range(0, component_range))
                        if specific_components:
                            # Add specific components, avoiding duplicates
                            components_to_scan.extend([c for c in specific_components if c not in components_to_scan])

                        for component_id in components_to_scan:
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
                                    if not DeviceIdentifier.has_feature(device, "skip_signal"):
                                        device["signal_strength_component"] = reported_value
                                elif component_id == 3:  # Firmware Version
                                    if not DeviceIdentifier.has_feature(device, "skip_firmware"):
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
                                        # MODE MANUEL : Utiliser Component 11
                                        elif reported_value == 0:
                                            device["speed_percent"] = 45  # Faible
                                        elif reported_value == 1:
                                            device["speed_percent"] = 65  # Moyenne
                                        elif reported_value == 2:
                                            device["speed_percent"] = 100  # Élevée
                                        else:
                                            device["speed_percent"] = 0  # Défaut
                                elif component_id == 15:  # Température de référence pour pompes à chaleur
                                    device["component_15_speed"] = (
                                        reported_value or component_state.get("desiredValue") or 0
                                    )

                                    # Pour les pompes à chaleur, component 15 peut contenir la température × 10
                                    # Use desiredValue if reportedValue is not available (Z550iQ+ uses desiredValue)
                                    temp_raw = reported_value or component_state.get("desiredValue")
                                    if device.get("type", "").lower() == "heat_pump" and temp_raw:
                                        try:
                                            # Convertir la valeur brute en température (diviser par 10)
                                            temp_value = float(temp_raw) / 10.0
                                            # Valider la plage de température (10-50°C)
                                            if 10.0 <= temp_value <= 50.0:
                                                device["target_temperature"] = temp_value
                                        except (ValueError, TypeError):
                                            pass
                                    # Note: Component 15 n'est plus utilisé pour le mode manuel, remplacé par Component 13
                                elif component_id == 19:  # Température de l'eau (pour pompes à chaleur)
                                    device["timezone_component"] = reported_value
                                    # Component 19 peut contenir la température de l'eau de la piscine × 10
                                    if device.get("type", "").lower() == "heat_pump" and reported_value:
                                        try:
                                            # Convertir la valeur brute en température (diviser par 10)
                                            water_temp_value = float(reported_value) / 10.0
                                            # Valider la plage de température (5-50°C pour l'eau, permettant le chauffage)
                                            if 5.0 <= water_temp_value <= 50.0:
                                                device["water_temperature"] = water_temp_value
                                        except (ValueError, TypeError):
                                            pass
                                elif component_id == 20:
                                    # Component 20 has different meanings:
                                    # - For pumps: schedules (list)
                                    # - For chlorinators: mode (0=OFF, 1=ON, 2=AUTO)
                                    device_type = device.get("type", "")

                                    if device_type == "chlorinator":
                                        # Chlorinator mode
                                        if isinstance(reported_value, int):
                                            device["mode_reported"] = reported_value
                                    else:
                                        # Pump schedules
                                        schedule_data = reported_value if isinstance(reported_value, list) else []
                                        device["schedule_data"] = schedule_data

                                        # Track current scheduler count for this device
                                        current_schedule_count = len(schedule_data)
                                        device_key = f"{pool_id}_{device_id}"

                                        # Check if we had schedulers before and now have fewer (indicating deletion)
                                        previous_count = self._previous_schedule_entities.get(device_key, 0)
                                        if previous_count > 0 and current_schedule_count < previous_count:
                                            # Trigger entity registry cleanup for this device's schedule sensor
                                            await self._cleanup_schedule_sensor_if_empty(
                                                pool_id, device_id, schedule_data
                                            )

                                        # Update tracking count
                                        self._previous_schedule_entities[device_key] = current_schedule_count
                                elif component_id == 21:
                                    # Component 21: Network Status for pumps, ON/OFF for Z550iQ+ heat pumps
                                    device["network_status_component"] = reported_value
                                    # Check if this is a Z550iQ+ heat pump (uses component 21 for ON/OFF)
                                    if DeviceIdentifier.has_feature(device, "z550_mode"):
                                        device["heat_pump_reported"] = reported_value
                                        device["is_heating"] = bool(reported_value)
                                elif component_id == 13:  # Component 13 - État pompe à chaleur
                                    device["component_13_data"] = component_state

                                    # Pour les pompes à chaleur (sauf Z550iQ+ qui utilise component 21)
                                    if device.get(
                                        "type", ""
                                    ).lower() == "heat_pump" and not DeviceIdentifier.has_feature(device, "z550_mode"):
                                        device["heat_pump_reported"] = reported_value
                                        device["is_heating"] = bool(reported_value)
                                elif component_id == 14:  # Component 14 exploration
                                    device["component_14_data"] = component_state
                                elif component_id == 16:  # Component 16 - Mode for Z550iQ+
                                    device["component_16_data"] = component_state
                                    if DeviceIdentifier.has_feature(device, "z550_mode"):
                                        # Z550iQ+ mode: 0=heating, 1=cooling, 2=auto
                                        device["z550_mode_reported"] = reported_value
                                elif component_id == 17:  # Component 17 - Preset mode for Z550iQ+
                                    device["component_17_data"] = component_state
                                    if DeviceIdentifier.has_feature(device, "z550_mode"):
                                        # Z550iQ+ preset: 0=silence, 1=smart, 2=boost
                                        device["z550_preset_reported"] = reported_value
                                elif component_id == 37:  # Component 37 - Water temp for Z550iQ+
                                    device["component_37_data"] = component_state
                                    if DeviceIdentifier.has_feature(device, "z550_mode") and reported_value:
                                        try:
                                            # Decidegrees: 290 = 29.0°C
                                            water_temp = float(reported_value) / 10.0
                                            if 0.0 <= water_temp <= 50.0:
                                                device["water_temperature"] = water_temp
                                        except (ValueError, TypeError):
                                            pass
                                elif component_id == 40:
                                    # Component 40: Light schedules OR Air temp for Z550iQ+
                                    device[f"component_{component_id}_data"] = component_state
                                    if DeviceIdentifier.has_feature(device, "z550_mode") and reported_value:
                                        try:
                                            # Decidegrees: 140 = 14.0°C
                                            air_temp = float(reported_value) / 10.0
                                            if -20.0 <= air_temp <= 60.0:
                                                device["air_temperature"] = air_temp
                                        except (ValueError, TypeError):
                                            pass
                                    else:
                                        # Light schedules handling
                                        config = DeviceIdentifier.identify_device(device)
                                        device_type = config.device_type if config else device.get("type", "")
                                        if device_type == "light":
                                            schedule_data = reported_value if isinstance(reported_value, list) else []
                                            device["schedule_data"] = schedule_data
                                            current_schedule_count = len(schedule_data)
                                            device_key = f"{pool_id}_{device_id}"
                                            previous_count = self._previous_schedule_entities.get(device_key, 0)
                                            if previous_count > 0 and current_schedule_count < previous_count:
                                                await self._cleanup_schedule_sensor_if_empty(
                                                    pool_id, device_id, schedule_data
                                                )
                                            self._previous_schedule_entities[device_key] = current_schedule_count
                                elif component_id == 61:  # Component 61 - State for Z550iQ+
                                    device["component_61_data"] = component_state
                                    if DeviceIdentifier.has_feature(device, "z550_mode"):
                                        # Z550iQ+ state: 0=idle, 2=heating, 3=cooling, 11=no flow
                                        device["z550_state_reported"] = reported_value
                                        if reported_value == 2:
                                            device["hvac_action"] = "heating"
                                        elif reported_value == 3:
                                            device["hvac_action"] = "cooling"
                                        elif reported_value == 11:
                                            device["hvac_action"] = "no_flow"
                                        else:
                                            device["hvac_action"] = "idle"
                                elif component_id in [62, 65]:  # Température de l'eau alternative
                                    # Components 62 et 65 peuvent aussi contenir la température de l'eau × 10
                                    if device.get("type", "").lower() == "heat_pump" and reported_value:
                                        try:
                                            water_temp_value = float(reported_value) / 10.0
                                            if 5.0 <= water_temp_value <= 50.0:
                                                # Utiliser seulement si pas déjà défini par component 19
                                                if "water_temperature" not in device:
                                                    device["water_temperature"] = water_temp_value
                                        except (ValueError, TypeError):
                                            pass
                                    device[f"component_{component_id}_data"] = component_state
                                else:
                                    # Check if this is a device-specific schedule component
                                    schedule_comp = DeviceIdentifier.get_feature(device, "schedule_component")
                                    if schedule_comp and component_id == schedule_comp:
                                        # Device-specific schedule component (e.g., 258 for DM24049704)
                                        # DM24049704 uses programs/slots format instead of list
                                        if isinstance(reported_value, dict) and "programs" in reported_value:
                                            schedule_data = self._parse_dm24049704_schedule_format(reported_value)
                                        elif isinstance(reported_value, list):
                                            schedule_data = reported_value
                                        else:
                                            schedule_data = []
                                        device["schedule_data"] = schedule_data

                                        current_schedule_count = len(schedule_data)
                                        device_key = f"{pool_id}_{device_id}"

                                        previous_count = self._previous_schedule_entities.get(device_key, 0)
                                        if previous_count > 0 and current_schedule_count < previous_count:
                                            await self._cleanup_schedule_sensor_if_empty(
                                                pool_id, device_id, schedule_data
                                            )

                                        self._previous_schedule_entities[device_key] = current_schedule_count
                                    # Store all other components for exploration
                                    device[f"component_{component_id}_data"] = component_state

            # Collect all current device IDs from API
            current_device_ids = set()
            for pool in pools:
                # Add pool ID itself
                current_device_ids.add(pool["id"])
                # Add all device IDs
                for device in pool.get("devices", []):
                    device_id = device.get("device_id")
                    if device_id:
                        current_device_ids.add(device_id)

            # Clean up devices that no longer exist in Fluidra API
            await self._cleanup_removed_devices(current_device_ids)

            return {pool["id"]: pool for pool in pools}

        except Exception as err:
            _LOGGER.error(f"Error updating Fluidra Pool data: {err}")
            raise UpdateFailed(f"Error communicating with API: {err}") from err
