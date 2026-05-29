"""Pump-related switches (main pump + auto mode)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from ..api_resilience import FluidraError
from ..const import DOMAIN, OPTIMISTIC_ACTION_TIMEOUT, SWITCH_CONFIRMATION_DELAY
from .base import FluidraPoolSwitchEntity

_LOGGER = logging.getLogger(__name__)


class FluidraPumpSwitch(FluidraPoolSwitchEntity):
    """Switch for controlling pool pumps (ON/OFF)."""

    _attr_translation_key = "pump"

    @property
    def icon(self) -> str:
        """Return the icon of the switch."""
        if self.is_on:
            return "mdi:pump"
        return "mdi:pump-off"

    @property
    def is_on(self) -> bool:
        """Return true if the pump is on using optimistic UI or real-time reported value."""
        pump_reported = self.device_data.get("pump_reported")
        actual = bool(pump_reported) if pump_reported is not None else self.device_data.get("is_running", False)
        if self._pending_state is not None:
            # Drop the optimistic state as soon as the poll confirms it (or it times
            # out) so we never flip back to a stale value mid-confirmation.
            if actual == self._pending_state or self._pending_state_expired(OPTIMISTIC_ACTION_TIMEOUT):
                self._clear_pending_state()
                return actual
            return self._pending_state
        return actual

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the pump on using discovered API with optimistic UI."""
        try:
            self._set_pending_state(True)

            success = await self._api.start_pump(self._device_id)
            if success:
                # Keep the optimistic state; is_on clears it once the poll confirms.
                await asyncio.sleep(SWITCH_CONFIRMATION_DELAY)
                await self.coordinator.async_request_refresh()
            else:
                self._clear_pending_state()
        except (
            aiohttp.ClientError,
            TimeoutError,
            FluidraError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
        ) as err:
            _LOGGER.debug("Failed to turn on pump: %s", err)
            self._clear_pending_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the pump off using discovered API with optimistic UI."""
        try:
            self._set_pending_state(False)

            success = await self._api.stop_pump(self._device_id)
            if success:
                await asyncio.sleep(SWITCH_CONFIRMATION_DELAY)
                await self.coordinator.async_request_refresh()
            else:
                self._clear_pending_state()
        except (
            aiohttp.ClientError,
            TimeoutError,
            FluidraError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
        ) as err:
            _LOGGER.debug("Failed to turn off pump: %s", err)
            self._clear_pending_state()

    @property
    def extra_state_attributes(self) -> dict:
        """Return extra state attributes."""
        return {
            "component_id": 9,
            "operation": "pump_control",
            "speed_percent": self.device_data.get("speed_percent", 0),
            "operation_mode": self.device_data.get("operation_mode", 0),
            "pump_reported": self.device_data.get("pump_reported"),
            "pump_desired": self.device_data.get("pump_desired"),
            "connectivity": self.device_data.get("connectivity", {}),
            "last_update": self.device_data.get("last_update"),
            "pending_action": self._pending_state is not None,
            "action_timestamp": self._last_action_time,
        }


class FluidraAutoModeSwitch(FluidraPoolSwitchEntity):
    """Switch for controlling pump auto mode (ON/OFF)."""

    _attr_translation_key = "auto_mode"

    @property
    def unique_id(self) -> str:
        """Return unique ID."""
        return f"{DOMAIN}_{self._pool_id}_{self._device_id}_auto_mode"

    @property
    def icon(self) -> str:
        """Return the icon of the switch."""
        if self.is_on:
            return "mdi:auto-mode"
        return "mdi:autorenew-off"

    @property
    def is_on(self) -> bool:
        """Return true if auto mode is on using optimistic UI or real-time reported value."""
        auto_reported = self.device_data.get("auto_reported")
        actual = bool(auto_reported) if auto_reported is not None else self.device_data.get("auto_mode_enabled", False)
        if self._pending_state is not None:
            if actual == self._pending_state or self._pending_state_expired(OPTIMISTIC_ACTION_TIMEOUT):
                self._clear_pending_state()
                return actual
            return self._pending_state
        return actual

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn auto mode on using discovered Component 10 with optimistic UI."""
        try:
            self._set_pending_state(True)

            success = await self._api.enable_auto_mode(self._device_id)
            if success:
                await asyncio.sleep(SWITCH_CONFIRMATION_DELAY)
                await self.coordinator.async_request_refresh()
            else:
                self._clear_pending_state()
        except (
            aiohttp.ClientError,
            TimeoutError,
            FluidraError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
        ) as err:
            _LOGGER.debug("Failed to enable auto mode: %s", err)
            self._clear_pending_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn auto mode off using discovered Component 10 with optimistic UI."""
        try:
            self._set_pending_state(False)

            success = await self._api.disable_auto_mode(self._device_id)
            if success:
                await asyncio.sleep(SWITCH_CONFIRMATION_DELAY)
                await self.coordinator.async_request_refresh()
            else:
                self._clear_pending_state()
        except (
            aiohttp.ClientError,
            TimeoutError,
            FluidraError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
        ) as err:
            _LOGGER.debug("Failed to disable auto mode: %s", err)
            self._clear_pending_state()

    @property
    def extra_state_attributes(self) -> dict:
        """Return extra state attributes."""
        return {
            "component_id": 10,
            "operation": "auto_mode_control",
            "function": "Mode automatique/programmé",
            "auto_reported": self.device_data.get("auto_reported"),
            "auto_desired": self.device_data.get("auto_desired"),
            "connectivity": self.device_data.get("connectivity", {}),
            "last_update": self.device_data.get("last_update"),
            "pending_action": self._pending_state is not None,
            "action_timestamp": self._last_action_time,
        }
