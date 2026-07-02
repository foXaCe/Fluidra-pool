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
from ._base import FluidraAPIBase

_LOGGER = logging.getLogger(__name__)


class CommandsMixin(FluidraAPIBase):
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

    async def enable_auto_mode(self, device_id: str) -> bool:
        """Enable auto mode.

        The pump only accepts (and reports) the auto-mode command once it is
        powered on. A device in standby silently ignores the component-10 write —
        the API returns 200 but reportedValue stays 0 and the toggle snaps back
        off (the official app shows "equipment off, turn it on to start receiving
        data"). So power the pump on first, then enable auto mode.
        """
        if not await self.control_device_component(device_id, COMPONENT_PUMP_ONOFF, 1):
            return False
        await asyncio.sleep(PUMP_START_DELAY)
        return await self.control_device_component(device_id, COMPONENT_AUTO_MODE, 1)

    async def disable_auto_mode(self, device_id: str) -> bool:
        """Disable auto mode."""
        return await self.control_device_component(device_id, COMPONENT_AUTO_MODE, 0)
