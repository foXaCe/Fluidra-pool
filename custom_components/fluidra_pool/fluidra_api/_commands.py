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
    COMPONENT_VICTORIA_AUTO_SCHEDULE,
    COMPONENT_VICTORIA_QUICK_FUNCTION,
    COMPONENT_VICTORIA_STOP,
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

    def _is_victoria(self, device_id: str) -> bool:
        """Return True if the device uses the Victoria VS string-register write path."""
        device = self.get_device_by_id(device_id)
        return bool(device and DeviceIdentifier.get_feature(device, "victoria_vs_mode"))

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

    async def pause_pump(self, device_id: str) -> bool:
        """Pause a Victoria pump via the STOP trigger (c15=1).

        Mirrors the app's dedicated Stop button (Issue #144, @renaatski): it halts
        the motor immediately WITHOUT disarming the auto schedule (c13), so future
        scheduled blocks still trigger automatically. To fully park the pump (stop
        + disarm), disable the auto-mode switch (c13=0) instead.
        """
        return await self.control_device_component(device_id, COMPONENT_VICTORIA_STOP, 1)

    async def trigger_quick_function(self, device_id: str, preset_index: int) -> bool:
        """Trigger a Victoria quick-function / preset by index (component 20).

        The available indices are user-configured on the pump and must be read
        from the pool schedulers rather than hardcoded (Issue #144).
        """
        return await self.control_device_component(device_id, COMPONENT_VICTORIA_QUICK_FUNCTION, preset_index)

    async def enable_auto_mode(self, device_id: str) -> bool:
        """Enable auto mode.

        The pump only accepts (and reports) the auto-mode command once it is
        powered on. A device in standby silently ignores the component-10 write —
        the API returns 200 but reportedValue stays 0 and the toggle snaps back
        off (the official app shows "equipment off, turn it on to start receiving
        data"). So power the pump on first, then enable auto mode.
        """
        if self._is_victoria(device_id):
            # Victoria: the auto schedule is a single boolean on c13, no separate
            # power-on step (Issue #144).
            return await self.control_device_component(device_id, COMPONENT_VICTORIA_AUTO_SCHEDULE, 1)

        if not await self.control_device_component(device_id, COMPONENT_PUMP_ONOFF, 1):
            return False
        await asyncio.sleep(PUMP_START_DELAY)
        return await self.control_device_component(device_id, COMPONENT_AUTO_MODE, 1)

    async def disable_auto_mode(self, device_id: str) -> bool:
        """Disable auto mode."""
        if self._is_victoria(device_id):
            return await self.control_device_component(device_id, COMPONENT_VICTORIA_AUTO_SCHEDULE, 0)
        return await self.control_device_component(device_id, COMPONENT_AUTO_MODE, 0)
