"""Heating-related switches (heater + heat pump)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp
from homeassistant.exceptions import HomeAssistantError

from ..api_resilience import FluidraError
from ..const import COMPONENT_PUMP_ONOFF, DOMAIN, OPTIMISTIC_ACTION_TIMEOUT, SWITCH_CONFIRMATION_DELAY
from ..device_registry import DeviceIdentifier
from .base import FluidraPoolSwitchEntity

_LOGGER = logging.getLogger(__name__)


class FluidraHeatPumpSwitch(FluidraPoolSwitchEntity):
    """Switch for controlling pool heat pumps (Astralpool Eco Elyo, etc.)."""

    _attr_translation_key = "heat_pump"

    @property
    def icon(self) -> str:
        """Return the icon of the switch."""
        if self.is_on:
            return "mdi:heat-pump"
        return "mdi:heat-pump-outline"

    @property
    def is_on(self) -> bool:
        """Return true if the heat pump is on using optimistic UI or real-time reported value."""
        actual = self._actual_state()
        if self._pending_state is not None:
            # Clear the optimistic state as soon as the poll confirms it (like the
            # sibling switches) or once it expires — not only on the timeout.
            if actual == self._pending_state or self._pending_state_expired(OPTIMISTIC_ACTION_TIMEOUT):
                self._clear_pending_state()
                return actual
            return self._pending_state
        return actual

    def _actual_state(self) -> bool:
        """Return the heat-pump on/off state from the freshly polled device data."""
        heat_pump_reported = self.device_data.get("heat_pump_reported")
        if heat_pump_reported is not None:
            return bool(heat_pump_reported)

        pump_reported = self.device_data.get("pump_reported")
        if pump_reported is not None:
            return bool(pump_reported)

        if self.device_data.get("is_running", False):
            return True

        value: bool = self.device_data.get("is_heating", False)
        return value

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the heat pump on using discovered API with optimistic UI."""
        self._ensure_pool_writable()
        try:
            self._set_pending_state(True)
            self.async_write_ha_state()

            # Z550iQ+ uses component 21 for ON/OFF
            if DeviceIdentifier.has_feature(self.device_data, "z550_mode"):
                _LOGGER.debug("Z550iQ+ turn ON: using component 21 for device %s", self._device_id)
                success = await self._api.control_device_component(self._device_id, 21, 1)
                _LOGGER.debug("Z550iQ+ turn ON result: %s", success)
            else:
                success = await self._api.start_pump(self._device_id)

            if success:
                # Keep optimistic state; is_on clears it once the poll confirms.
                await asyncio.sleep(SWITCH_CONFIRMATION_DELAY)
                await self.coordinator.async_request_refresh()
            else:
                self._clear_pending_state()
                self.async_write_ha_state()
                raise HomeAssistantError(translation_domain=DOMAIN, translation_key="heat_pump_set_failed")
        except HomeAssistantError:
            raise
        except (aiohttp.ClientError, TimeoutError, FluidraError, ValueError, TypeError, KeyError, AttributeError) as e:
            _LOGGER.error("Error turning on heat pump %s: %s", self._device_id, e)
            self._clear_pending_state()
            self.async_write_ha_state()
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="heat_pump_set_failed") from e

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the heat pump off using discovered API with optimistic UI."""
        self._ensure_pool_writable()
        try:
            self._set_pending_state(False)
            self.async_write_ha_state()

            if DeviceIdentifier.has_feature(self.device_data, "z550_mode"):
                _LOGGER.debug("Z550iQ+ turn OFF: using component 21 for device %s", self._device_id)
                success = await self._api.control_device_component(self._device_id, 21, 0)
                _LOGGER.debug("Z550iQ+ turn OFF result: %s", success)
            else:
                success = await self._api.stop_pump(self._device_id)

            if success:
                await asyncio.sleep(SWITCH_CONFIRMATION_DELAY)
                await self.coordinator.async_request_refresh()
            else:
                self._clear_pending_state()
                self.async_write_ha_state()
                raise HomeAssistantError(translation_domain=DOMAIN, translation_key="heat_pump_set_failed")
        except HomeAssistantError:
            raise
        except (aiohttp.ClientError, TimeoutError, FluidraError, ValueError, TypeError, KeyError, AttributeError) as e:
            _LOGGER.error("Error turning off heat pump %s: %s", self._device_id, e)
            self._clear_pending_state()
            self.async_write_ha_state()
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="heat_pump_set_failed") from e

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs: dict[str, Any] = {
            "component_id": 9,
            "operation": "heat_pump_control",
            "device_type": "heat_pump",
            "heat_pump_reported": self.device_data.get("heat_pump_reported"),
            "heat_pump_desired": self.device_data.get("heat_pump_desired"),
            "connectivity": self.device_data.get("connectivity", {}),
            "last_update": self.device_data.get("last_update"),
            "pending_action": self._pending_state is not None,
            "action_timestamp": self._last_action_time,
        }

        if "current_temperature" in self.device_data:
            attrs["current_temperature"] = self.device_data["current_temperature"]
        if "target_temperature" in self.device_data:
            attrs["target_temperature"] = self.device_data["target_temperature"]

        return attrs


class FluidraHeaterSwitch(FluidraPoolSwitchEntity):
    """Switch for controlling pool heaters."""

    _attr_has_entity_name = True
    _attr_translation_key = "heater"

    @property
    def unique_id(self) -> str:
        """Return unique ID."""
        return f"{DOMAIN}_{self._pool_id}_{self._device_id}_heater"

    @property
    def icon(self) -> str:
        """Return the icon of the switch."""
        return "mdi:heat-wave" if self.is_on else "mdi:snowflake"

    @property
    def is_on(self) -> bool:
        """Return true if the heater is on."""
        actual = bool(self.device_data.get("is_heating") or self.device_data.get("is_running"))
        if self._pending_state is not None:
            # Clear the optimistic state as soon as the poll confirms it (or expiry).
            if actual == self._pending_state or self._pending_state_expired(OPTIMISTIC_ACTION_TIMEOUT):
                self._clear_pending_state()
                return actual
            return self._pending_state
        return actual

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the heater on (component 9 = generic ON/OFF)."""
        self._ensure_pool_writable()
        self._set_pending_state(True)
        try:
            success = await self._api.control_device_component(self._device_id, COMPONENT_PUMP_ONOFF, 1)
        except (aiohttp.ClientError, TimeoutError, FluidraError) as err:
            self._clear_pending_state()
            self.async_write_ha_state()
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="heater_set_failed") from err
        if success:
            await asyncio.sleep(SWITCH_CONFIRMATION_DELAY)
            await self.coordinator.async_request_refresh()
        else:
            self._clear_pending_state()
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="heater_set_failed")

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the heater off (component 9 = generic ON/OFF)."""
        self._ensure_pool_writable()
        self._set_pending_state(False)
        try:
            success = await self._api.control_device_component(self._device_id, COMPONENT_PUMP_ONOFF, 0)
        except (aiohttp.ClientError, TimeoutError, FluidraError) as err:
            self._clear_pending_state()
            self.async_write_ha_state()
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="heater_set_failed") from err
        if success:
            await asyncio.sleep(SWITCH_CONFIRMATION_DELAY)
            await self.coordinator.async_request_refresh()
        else:
            self._clear_pending_state()
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="heater_set_failed")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs: dict[str, Any] = {}
        if "current_temperature" in self.device_data:
            attrs["current_temperature"] = self.device_data["current_temperature"]
        if "target_temperature" in self.device_data:
            attrs["target_temperature"] = self.device_data["target_temperature"]
        return attrs
