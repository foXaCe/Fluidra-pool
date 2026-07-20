"""Device-level sensors for Fluidra Pool (per-device telemetry)."""

from __future__ import annotations

from datetime import time
import logging
from typing import TYPE_CHECKING, Any

import aiohttp
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import PERCENTAGE, UnitOfLength, UnitOfPower, UnitOfTemperature, UnitOfTime
from homeassistant.util import dt as dt_util

from ..api_resilience import FluidraError
from ..const import LUMIPLUS_COMPONENT_BRIGHTNESS
from ..device_registry import DeviceIdentifier
from ..helpers import parse_cron_time
from .base import FluidraPoolSensorEntity

if TYPE_CHECKING:
    from ..coordinator import FluidraDataUpdateCoordinator
    from ..fluidra_api import FluidraPoolAPI

_LOGGER = logging.getLogger(__name__)


class FluidraTemperatureSensor(FluidraPoolSensorEntity):
    """Temperature sensor for pool heaters and heat pumps."""

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api: FluidraPoolAPI,
        pool_id: str,
        device_id: str,
        sensor_type: str,
    ) -> None:
        """Initialize temperature sensor."""
        super().__init__(coordinator, api, pool_id, device_id, sensor_type)
        self._attr_device_class = SensorDeviceClass.TEMPERATURE
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        self._attr_suggested_display_precision = 1

        translation_map = {
            "current": "current_temperature",
            "target": "target_temperature",
            "water": "water_temperature",
            "air": "air_temperature",
        }
        self._attr_translation_key = translation_map.get(sensor_type, "current_temperature")

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
    def icon(self) -> str:
        """Return the icon of the sensor."""
        return "mdi:thermometer"


class FluidraLightBrightnessSensor(FluidraPoolSensorEntity):
    """Brightness sensor for pool lights."""

    _attr_translation_key = "brightness"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int | None:
        """Return the brightness percentage (0-100)."""
        device = self.device_data
        # Legacy / test-injected field, already a 0-100 percentage.
        if "brightness" in device:
            return device.get("brightness")
        # Otherwise read the LumiPlus brightness component (reportedValue is the
        # raw 0-100 percentage — the light entity scales it to HA's 0-255).
        components = device.get("components", {})
        comp = components.get(str(LUMIPLUS_COMPONENT_BRIGHTNESS))
        if not isinstance(comp, dict):
            return None
        reported = comp.get("reportedValue")
        if reported is None:
            return None
        try:
            return round(float(reported))
        except (TypeError, ValueError):
            return None

    @property
    def icon(self) -> str:
        """Return the icon of the sensor."""
        return "mdi:brightness-percent"


class FluidraRunningHoursSensor(FluidraPoolSensorEntity):
    """Running hours sensor for heat pumps (Z260iQ component 0 / Z550iQ+ component 60)."""

    _attr_translation_key = "running_hours"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_icon = "mdi:clock-outline"

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api: FluidraPoolAPI,
        pool_id: str,
        device_id: str,
    ) -> None:
        """Initialize running hours sensor."""
        super().__init__(coordinator, api, pool_id, device_id, "running_hours")

    @property
    def native_value(self) -> int | None:
        """Return the running hours (populated by the coordinator from the model's component)."""
        return self.device_data.get("running_hours")


class FluidraPumpSpeedSensor(FluidraPoolSensorEntity):
    """Speed sensor for pool pumps with mode detection."""

    _attr_translation_key = "speed_mode"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["stopped", "not_running", "running", "low", "medium", "high"]

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api: FluidraPoolAPI,
        pool_id: str,
        device_id: str,
    ) -> None:
        """Initialize the speed sensor."""
        super().__init__(coordinator, api, pool_id, device_id, "speed")

    @property
    def icon(self) -> str:
        """Return the icon for the entity."""
        speed_mode = self._get_speed_mode()
        if speed_mode in ["stopped", "not_running"]:
            return "mdi:pump-off"
        return "mdi:pump"

    def _get_speed_mode(self) -> str:
        """Get the current speed mode - returns state key."""
        is_running = self.device_data.get("is_running", False)
        pump_reported = self.device_data.get("pump_reported")
        if pump_reported is not None:
            is_running = bool(pump_reported)

        if not is_running:
            return "stopped"

        current_speed = self.device_data.get("speed_percent", 0)

        if current_speed == 0:
            # Victoria VS pumps don't publish the live output % while running
            # under a schedule (c21 stays 0 even though c22 power / c24 head show
            # the pump is turning), so a running pump would misleadingly read
            # "not_running". Report "running" instead (Issue #144).
            if DeviceIdentifier.has_feature(self.device_data, "victoria_vs_mode"):
                return "running"
            return "not_running"

        if current_speed <= 50:
            return "low"
        if current_speed <= 70:
            return "medium"
        return "high"

    @property
    def native_value(self) -> str:
        """Return the state of the sensor."""
        return self._get_speed_mode()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        is_running = self.device_data.get("is_running", False)
        pump_reported = self.device_data.get("pump_reported")
        if pump_reported is not None:
            is_running = bool(pump_reported)

        auto_mode = self.device_data.get("auto_mode_enabled", False)
        auto_reported = self.device_data.get("auto_reported")
        if auto_reported is not None:
            auto_mode = bool(auto_reported)

        current_speed = self.device_data.get("speed_percent", 0)
        speed_level = self.device_data.get("speed_level_reported")

        attrs: dict[str, Any] = {
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

        # Victoria VS pumps also report their mode and setpoint (Issue #144):
        # the target is either a speed % or a flow rate in m³/h depending on
        # setpoint_type ("SPEED" vs "FLOW").
        if "pump_setpoint_type" in self.device_data or "pump_mode" in self.device_data:
            attrs["pump_mode"] = self.device_data.get("pump_mode")
            attrs["setpoint_type"] = self.device_data.get("pump_setpoint_type")
            attrs["setpoint"] = self.device_data.get("pump_setpoint")

        return attrs


class FluidraPumpPowerSensor(FluidraPoolSensorEntity):
    """Electrical power reported by VS pumps that expose it (Victoria c22).

    Cross-checked against the pump's local HMI in Issue #144: exact at high
    speed (719 vs 720 W at 95 %), within a few tens of watts below — the pump
    reports factory performance-curve data rather than a metered value.
    """

    _attr_translation_key = "pump_power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api: FluidraPoolAPI,
        pool_id: str,
        device_id: str,
    ) -> None:
        """Initialize the pump power sensor."""
        super().__init__(coordinator, api, pool_id, device_id, "power")

    @property
    def native_value(self) -> int | None:
        """Return the reported pump power in watts."""
        return self.device_data.get("pump_power")


class FluidraPumpHeadSensor(FluidraPoolSensorEntity):
    """Hydraulic head reported by VS pumps that expose it (Victoria c24, cm → m)."""

    _attr_translation_key = "pump_head"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfLength.METERS
    _attr_suggested_display_precision = 1
    _attr_icon = "mdi:waves-arrow-up"

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api: FluidraPoolAPI,
        pool_id: str,
        device_id: str,
    ) -> None:
        """Initialize the pump head sensor."""
        super().__init__(coordinator, api, pool_id, device_id, "head")

    @property
    def native_value(self) -> float | None:
        """Return the reported hydraulic head in metres."""
        return self.device_data.get("pump_head")


class FluidraPumpScheduleSensor(FluidraPoolSensorEntity):
    """Sensor for displaying pump weekly schedules."""

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api: FluidraPoolAPI,
        pool_id: str,
        device_id: str,
    ) -> None:
        """Initialize the schedule sensor."""
        super().__init__(coordinator, api, pool_id, device_id, "schedules")
        self._attr_translation_key = "schedule_count"

    @property
    def icon(self) -> str:
        """Return the icon for the entity."""
        return "mdi:calendar-clock"

    def _parse_cron_time(self, cron_time: str) -> time | None:
        """Parse cron time format 'mm HH * * 0,1,2,3,4,5,6' to time object."""
        return parse_cron_time(cron_time)

    def _format_schedule_time(self, schedule: dict[str, Any]) -> str:
        """Format schedule time range for display."""
        start_time = self._parse_cron_time(schedule.get("startTime", ""))
        end_time = self._parse_cron_time(schedule.get("endTime", ""))

        if start_time and end_time:
            return f"{start_time.strftime('%H:%M')}-{end_time.strftime('%H:%M')}"
        return "N/A"

    def _get_operation_name(self, operation: str) -> str:
        """Convert operation name to readable format."""
        speed_map = {"0": "low (45%)", "1": "medium (65%)", "2": "high (100%)"}
        return speed_map.get(operation, "low (45%)")

    def _get_current_schedule(self, schedules: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Get currently active schedule based on current time."""
        now = dt_util.now().time()

        for schedule in schedules:
            if not schedule.get("enabled", False):
                continue

            start_time = self._parse_cron_time(schedule.get("startTime", ""))
            end_time = self._parse_cron_time(schedule.get("endTime", ""))

            if start_time and end_time and start_time <= now <= end_time:
                return schedule
        return None

    def _get_schedules_data(self) -> list[dict[str, Any]]:
        """Get schedules data from device data."""
        device_data = self.device_data

        if "schedule_data" in device_data:
            schedule_data: list[dict[str, Any]] = device_data["schedule_data"]
            return schedule_data
        return []

    @property
    def native_value(self) -> int | None:
        """Return the number of enabled schedules."""
        try:
            schedules = self._get_schedules_data()
            if not schedules:
                return 0
            return sum(1 for s in schedules if s.get("enabled", False))
        except (aiohttp.ClientError, TimeoutError, FluidraError, ValueError, TypeError, KeyError, AttributeError):
            _LOGGER.debug("Failed to get schedule state for %s", self._device_id)
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        attrs: dict[str, Any] = {}

        try:
            schedules = self._get_schedules_data()
            if schedules:
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

                current_schedule = self._get_current_schedule(schedules)
                if current_schedule:
                    attrs["current_schedule_id"] = current_schedule.get("id")
                    attrs["current_time_range"] = self._format_schedule_time(current_schedule)
                    attrs["current_mode"] = self._get_operation_name(
                        current_schedule.get("startActions", {}).get("operationName", "0")
                    )

        except (aiohttp.ClientError, TimeoutError, FluidraError, ValueError, TypeError, KeyError, AttributeError) as e:
            attrs["error"] = str(e)

        return attrs


class FluidraDeviceInfoSensor(FluidraPoolSensorEntity):
    """Sensor for displaying device information and diagnostics."""

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api: FluidraPoolAPI,
        pool_id: str,
        device_id: str,
    ) -> None:
        """Initialize the device info sensor."""
        super().__init__(coordinator, api, pool_id, device_id, "info")
        self._attr_translation_key = "device_info"
        self._attr_device_class = SensorDeviceClass.ENUM
        self._attr_options = [
            "online",
            "signal_excellent",
            "signal_very_good",
            "signal_good",
            "signal_low",
            "signal_very_low",
            "error",
        ]

    @property
    def icon(self) -> str:
        """Return the icon for the entity."""
        return "mdi:information-outline"

    def _get_device_info_data(self) -> dict[str, Any]:
        """Get device information from coordinator data."""
        device_data = self.device_data

        info_data = {}

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
        """Return the device info state as an enum key."""
        try:
            info_data = self._get_device_info_data()
            signal = info_data.get("signal_strength", 0)

            if signal and signal != 0 and isinstance(signal, (int, float)):
                if signal >= -50:
                    return "signal_excellent"
                if signal >= -60:
                    return "signal_very_good"
                if signal >= -70:
                    return "signal_good"
                if signal >= -80:
                    return "signal_low"
                return "signal_very_low"
            return "online"

        except (aiohttp.ClientError, TimeoutError, FluidraError, ValueError, TypeError, KeyError, AttributeError):
            _LOGGER.debug("Failed to get device info state for %s", self._device_id)
            return "error"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        attrs = {}

        try:
            info_data = self._get_device_info_data()

            if "device_id" in info_data:
                attrs["device_id"] = info_data["device_id"]
            if "part_numbers" in info_data:
                attrs["part_numbers"] = info_data["part_numbers"]

            if "signal_strength" in info_data:
                signal = info_data["signal_strength"]
                attrs["signal_strength_dbm"] = signal
                if signal and isinstance(signal, (int, float)):
                    if signal >= -50:
                        attrs["signal_quality"] = "excellent"
                    elif signal >= -60:
                        attrs["signal_quality"] = "very_good"
                    elif signal >= -70:
                        attrs["signal_quality"] = "good"
                    elif signal >= -80:
                        attrs["signal_quality"] = "low"
                    else:
                        attrs["signal_quality"] = "very_low"

            if "network_status" in info_data:
                network_status = info_data["network_status"]
                attrs["network_status"] = "connected" if network_status == 1 else "disconnected"

            if "firmware_version" in info_data:
                attrs["firmware_version"] = info_data["firmware_version"]

            if "hardware_errors" in info_data:
                attrs["hardware_error_count"] = info_data["hardware_errors"]
            if "comm_errors" in info_data:
                attrs["communication_error_count"] = info_data["comm_errors"]

            if "timezone" in info_data:
                attrs["timezone_info"] = info_data["timezone"]

            attrs["device_name"] = self.device_data.get("name", "Unknown")
            attrs["device_type"] = self.device_data.get("type", "unknown")
            attrs["manufacturer"] = self.device_data.get("manufacturer", "Fluidra")
            attrs["model"] = self.device_data.get("model", "Unknown")
            attrs["online"] = self.device_data.get("online", False)

        except (aiohttp.ClientError, TimeoutError, FluidraError, ValueError, TypeError, KeyError, AttributeError) as e:
            attrs["error"] = str(e)

        return attrs
