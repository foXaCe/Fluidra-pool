"""Device-level sensors for Fluidra Pool (per-device telemetry)."""

from __future__ import annotations

from datetime import time, timedelta
import logging
from typing import TYPE_CHECKING, Any

import aiohttp
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import (
    PERCENTAGE,
    UnitOfLength,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolumeFlowRate,
)
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


class FluidraCompressorHoursSensor(FluidraPoolSensorEntity):
    """Compressor running hours for Z650iQ (component 39)."""

    _attr_translation_key = "compressor_running_hours"
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
        """Initialize compressor running hours sensor."""
        super().__init__(coordinator, api, pool_id, device_id, "compressor_hours")

    @property
    def native_value(self) -> int | None:
        """Return the compressor running hours."""
        return self.device_data.get("compressor_running_hours")


class FluidraCompressorModulationSensor(FluidraPoolSensorEntity):
    """Compressor modulation level for the Z650iQ (component 32), in percent.

    Reads 0 while the compressor is off and rises with the power draw
    otherwise, at a consistent ~13 W per unit whether the compressor is
    idling near 30 or driven to 92-93 under a Boost/max-load test — a range
    that fits a 0-100 percent scale rather than an arbitrary Hz figure.
    """

    _attr_translation_key = "compressor_modulation"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_icon = "mdi:gauge"

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api: FluidraPoolAPI,
        pool_id: str,
        device_id: str,
    ) -> None:
        """Initialize the compressor modulation sensor."""
        super().__init__(coordinator, api, pool_id, device_id, "compressor_modulation")

    @property
    def native_value(self) -> int | None:
        """Return the raw modulation level."""
        value: int | None = self.device_data.get("compressor_modulation")
        return value


class FluidraWifiSignalSensor(FluidraPoolSensorEntity):
    """WiFi signal strength sensor (RSSI in dBm)."""

    _attr_translation_key = "wifi_signal"
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "dBm"
    _attr_entity_category = None  # visible by default, not diagnostic-only

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api: FluidraPoolAPI,
        pool_id: str,
        device_id: str,
    ) -> None:
        """Initialize WiFi signal sensor."""
        super().__init__(coordinator, api, pool_id, device_id, "wifi_signal")

    @property
    def native_value(self) -> float | None:
        """Return the WiFi RSSI in dBm."""
        raw = self.device_data.get("signal_strength_component")
        if raw is None:
            return None
        try:
            return float(raw)
        except (ValueError, TypeError):
            return None


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
            # Victoria VS pumps don't publish the live output % while running under a
            # schedule or in FLOW mode (c21/c17 zero out), even though the pump is
            # turning — c25 flow / c22 power / c24 head stay live and prove it. So a
            # running pump would misleadingly read "not_running": report "running"
            # instead, rather than inventing a low/medium/high from a 0 % (Issue #144).
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


class FluidraPumpFlowSensor(FluidraPoolSensorEntity):
    """Water flow rate reported by VS pumps that expose it (Victoria c25, m³/h)."""

    _attr_translation_key = "pump_flow"
    _attr_device_class = SensorDeviceClass.VOLUME_FLOW_RATE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR
    _attr_suggested_display_precision = 1
    _attr_icon = "mdi:pump"

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api: FluidraPoolAPI,
        pool_id: str,
        device_id: str,
    ) -> None:
        """Initialize the pump flow sensor."""
        super().__init__(coordinator, api, pool_id, device_id, "flow")

    @property
    def native_value(self) -> float | None:
        """Return the reported flow rate in m³/h."""
        return self.device_data.get("pump_flow")


class FluidraPumpActivitySensor(FluidraPoolSensorEntity):
    """What a VS pump is doing right now, including transient phases (Issue #144).

    The Victoria cycles through PRIMING → CALIBRATION before settling into its run,
    and reports that on c14 (motor status) / c16 (operating mode). Those phases used
    to leak into the speed reading; here they get their own state so the speed sensor
    can stay a speed. Mapped from the pump's own strings, with the transient phases
    taking precedence over the steady-state mode.
    """

    _attr_translation_key = "pump_activity"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["stopped", "priming", "calibrating", "scheduled", "manual", "running", "unknown"]
    _attr_icon = "mdi:pump"

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api: FluidraPoolAPI,
        pool_id: str,
        device_id: str,
    ) -> None:
        """Initialize the pump activity sensor."""
        super().__init__(coordinator, api, pool_id, device_id, "activity")

    @property
    def native_value(self) -> str | None:
        """Return the current activity phase."""
        device = self.device_data
        status = str(device.get("component_14_data", {}).get("reportedValue") or "").strip().upper()
        mode = str(device.get("pump_mode") or "").strip().upper()

        # Nothing reported yet (first poll): unknown rather than a misleading
        # "stopped" — we genuinely don't know what the pump is doing.
        if not status and not mode:
            return None

        # Transient phases first — they describe what the pump is busy doing.
        if "PRIMING" in status or "PRIMING" in mode:
            return "priming"
        if "CALIBRATION" in status or "CALIBRATION" in mode:
            return "calibrating"

        if not device.get("is_running", False):
            return "stopped"
        if mode == "AUTO":
            return "scheduled"
        if "QUICK" in mode:
            return "manual"
        return "running"

    def _active_scheduler(self) -> dict[str, Any] | None:
        """Find the scheduler entry currently driving the pump, if any.

        Matched on c19 (the active entry's id) first, since that's the join key the
        app itself uses; otherwise on the ``running`` flag the backend flips on the
        entry's actions. Returns None when idle or when the pool's schedulers
        haven't been fetched (Issue #144).
        """
        schedulers = self.pool_data.get("schedulers")
        if not isinstance(schedulers, list):
            return None

        active_id = self.device_data.get("pump_active_schedule_id")
        if active_id:
            for entry in schedulers:
                if isinstance(entry, dict) and str(entry.get("id")) == str(active_id):
                    return entry

        for entry in schedulers:
            if not isinstance(entry, dict):
                continue
            actions = entry.get("actions")
            if isinstance(actions, list) and any(
                isinstance(action, dict) and action.get("running") for action in actions
            ):
                return entry
        return None

    @staticmethod
    def _scheduler_target(entry: dict[str, Any]) -> tuple[str | None, Any]:
        """Return the (mode, value) a scheduler entry commands.

        ``deviceActions[].id`` is 0 for a speed percentage and 1 for a flow rate in
        m³/h, with the value in ``arguments`` (Issue #144).
        """
        actions = entry.get("actions")
        if not isinstance(actions, list):
            return (None, None)
        for action in actions:
            if not isinstance(action, dict):
                continue
            for device_action in action.get("deviceActions", []) or []:
                if not isinstance(device_action, dict):
                    continue
                arguments = device_action.get("arguments")
                if not isinstance(arguments, list) or not arguments:
                    continue
                action_id = device_action.get("id")
                mode = {0: "SPEED", 1: "FLOW"}.get(action_id) if isinstance(action_id, int) else None
                if mode:
                    return (mode, arguments[0])
        return (None, None)

    @staticmethod
    def _schedule_remaining_seconds(entry: dict[str, Any]) -> int | None:
        """Seconds left in the running schedule, computed client-side.

        The device publishes no end time for a schedule, so this mirrors what the
        app does: take the entry's cron ``startTime`` plus its ``duration``
        (minutes). Overnight windows are handled by walking the start back a day
        when the computed end already passed. Returns None when the entry lacks
        usable timing or the result isn't plausible (Issue #144).
        """
        start = parse_cron_time(str(entry.get("startTime", "")))
        duration = entry.get("duration")
        if start is None or not isinstance(duration, (int, float)) or duration <= 0:
            return None

        now = dt_util.now()
        start_dt = now.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)
        end_dt = start_dt + timedelta(minutes=float(duration))
        if end_dt <= now:
            # The window likely began yesterday (overnight schedule).
            end_dt -= timedelta(days=1)
            if end_dt <= now:
                return None

        remaining = int((end_dt - now).total_seconds())
        # Guard against a mismatched entry: never report more than the window itself.
        return remaining if 0 < remaining <= duration * 60 else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the raw pump strings plus the active quick-function, when any."""
        device = self.device_data
        attrs: dict[str, Any] = {
            "motor_status": device.get("component_14_data", {}).get("reportedValue"),
            "operating_mode": device.get("pump_mode"),
        }

        # A schedule-driven run: the target exists only in the pool's /schedulers
        # config, so surface the matched entry's name and target (Issue #144).
        scheduler = self._active_scheduler()
        if scheduler is not None:
            attrs["schedule_name"] = scheduler.get("name")
            mode, value = self._scheduler_target(scheduler)
            if mode:
                attrs["schedule_mode"] = mode
                attrs["schedule_setpoint"] = value
            remaining = self._schedule_remaining_seconds(scheduler)
            if remaining is not None:
                attrs["schedule_remaining_seconds"] = remaining

        # c135 only reflects quick functions/presets — it goes stale during a
        # schedule-driven run, so only surface it while actually in QUICK FUNCTION
        # (Issue #144). Schedule runs get their name from /schedulers later.
        quick = device.get("pump_quick_function")
        if isinstance(quick, dict) and "QUICK" in str(device.get("pump_mode") or "").upper():
            attrs["quick_function"] = quick.get("name")
            attrs["quick_function_mode"] = quick.get("mode")
            attrs["quick_function_setpoint"] = quick.get("setpoint")
            expiry = device.get("pump_quick_function_expiry")
            if expiry:
                attrs["quick_function_ends_at"] = dt_util.utc_from_timestamp(expiry).isoformat()
                remaining = int(expiry - dt_util.utcnow().timestamp())
                attrs["quick_function_remaining_seconds"] = max(0, remaining)

        return attrs


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
        if "secondary_firmware_component" in device_data:
            info_data["secondary_firmware"] = device_data["secondary_firmware_component"]
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
