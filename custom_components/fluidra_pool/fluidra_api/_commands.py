"""High-level device commands (pump start/stop, heat-pump setpoint, auto mode)."""

from __future__ import annotations

import asyncio
import logging

from ..const import (
    COMPONENT_AUTO_MODE,
    COMPONENT_HEAT_PUMP_ONOFF,
    COMPONENT_HEAT_PUMP_SETPOINT,
    COMPONENT_PUMP_ONOFF,
    COMPONENT_PUMP_SPEED,
    PUMP_START_DELAY,
)
from ..device_registry import DeviceIdentifier

_LOGGER = logging.getLogger(__name__)


class CommandsMixin:
    """Convenience commands wrapping ``control_device_component`` calls."""

    async def set_heat_pump_temperature(self, device_id: str, temperature: float) -> bool:
        """Set heat pump target temperature on component 15 (setpoint × 10)."""
        temperature_value = int(temperature * 10)
        success = await self.control_device_component(device_id, COMPONENT_HEAT_PUMP_SETPOINT, temperature_value)
        if success:
            device = self.get_device_by_id(device_id)
            if device:
                device["target_temperature"] = temperature
        return success

    def _is_heat_pump(self, device_id: str) -> bool:
        """Return True if the device is a heat pump."""
        device = self.get_device_by_id(device_id)
        if not device:
            return False
        device_config = DeviceIdentifier.identify_device(device)
        return bool(device_config and device_config.device_type == "heat_pump")

    async def start_pump(self, device_id: str) -> bool:
        """Start pump using the correct component based on device type."""
        if self._is_heat_pump(device_id):
            return await self.control_device_component(device_id, COMPONENT_HEAT_PUMP_ONOFF, 1)

        start_success = await self.control_device_component(device_id, COMPONENT_PUMP_ONOFF, 1)

        if start_success:
            await asyncio.sleep(PUMP_START_DELAY)
            await self.control_device_component(device_id, COMPONENT_PUMP_SPEED, 0)
            return True

        return False

    async def stop_pump(self, device_id: str) -> bool:
        """Stop pump using the correct component based on device type."""
        if self._is_heat_pump(device_id):
            return await self.control_device_component(device_id, COMPONENT_HEAT_PUMP_ONOFF, 0)
        return await self.control_device_component(device_id, COMPONENT_PUMP_ONOFF, 0)

    async def set_pump_speed(self, device_id: str, speed_percent: int) -> bool:
        """Set pump speed. ``speed_percent`` snaps to the nearest API level."""
        if not 0 <= speed_percent <= 100:
            return False

        if speed_percent == 0:
            return await self.control_device_component(device_id, COMPONENT_PUMP_ONOFF, 0)

        if speed_percent <= 45:
            speed_level = 0
        elif speed_percent <= 65:
            speed_level = 1
        else:
            speed_level = 2

        return await self.control_device_component(device_id, COMPONENT_PUMP_SPEED, speed_level)

    async def enable_auto_mode(self, device_id: str) -> bool:
        """Enable auto mode."""
        return await self.control_device_component(device_id, COMPONENT_AUTO_MODE, 1)

    async def disable_auto_mode(self, device_id: str) -> bool:
        """Disable auto mode."""
        return await self.control_device_component(device_id, COMPONENT_AUTO_MODE, 0)
