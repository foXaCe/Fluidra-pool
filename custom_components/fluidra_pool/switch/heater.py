"""Heating-related switches (heater + heat pump)."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from ..api_resilience import FluidraError
from ..const import COMPONENT_PUMP_ONOFF, DOMAIN, OPTIMISTIC_ACTION_TIMEOUT
from ..device_registry import DeviceIdentifier
from .base import FluidraPoolSwitchEntity

_LOGGER = logging.getLogger(__name__)


class FluidraHeatPumpSwitch(FluidraPoolSwitchEntity):
    """Switch for controlling pool heat pumps (Astralpool Eco Elyo, etc.)."""

    def __init__(self, coordinator, api, pool_id: str, device_id: str):
        """Initialize the switch."""
        super().__init__(coordinator, api, pool_id, device_id)
        self._is_eco_elyo = False

    @property
    def name(self) -> str:
        """Return the name of the switch."""
        pool_name = self.pool_data.get("name", "Pool")
        device_name = self.device_data.get("name", "Heat Pump")

        device_config = DeviceIdentifier.identify_device(self.device_data)
        if device_config and "lg" in device_config.identifier_patterns[0].lower():
            return f"{pool_name} Eco Elyo"

        return f"{pool_name} {device_name}"

    @property
    def translation_key(self) -> str:
        """Return the translation key."""
        return "heat_pump"

    @property
    def icon(self) -> str:
        """Return the icon of the switch."""
        if self.is_on:
            return "mdi:heat-pump"
        return "mdi:heat-pump-outline"

    @property
    def is_on(self) -> bool:
        """Return true if the heat pump is on using optimistic UI or real-time reported value."""
        if self._pending_state is not None:
            if self._pending_state_expired(10):
                self._clear_pending_state()
            else:
                return self._pending_state

        heat_pump_reported = self.device_data.get("heat_pump_reported")
        if heat_pump_reported is not None:
            return bool(heat_pump_reported)

        pump_reported = self.device_data.get("pump_reported")
        if pump_reported is not None:
            return bool(pump_reported)

        if self.device_data.get("is_running", False):
            return True

        return self.device_data.get("is_heating", False)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the heat pump on using discovered API with optimistic UI."""
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
                # Keep optimistic state; the property auto-clears it after timeout.
                await self.coordinator.async_request_refresh()
            else:
                self._clear_pending_state()
                self.async_write_ha_state()
        except (aiohttp.ClientError, TimeoutError, FluidraError, ValueError, TypeError, KeyError, AttributeError) as e:
            _LOGGER.error("Error turning on heat pump %s: %s", self._device_id, e)
            self._clear_pending_state()
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the heat pump off using discovered API with optimistic UI."""
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
                await self.coordinator.async_request_refresh()
            else:
                self._clear_pending_state()
                self.async_write_ha_state()
        except (aiohttp.ClientError, TimeoutError, FluidraError, ValueError, TypeError, KeyError, AttributeError) as e:
            _LOGGER.error("Error turning off heat pump %s: %s", self._device_id, e)
            self._clear_pending_state()
            self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict:
        """Return extra state attributes."""
        attrs = {
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
        if self._pending_state is not None:
            if self._pending_state_expired(OPTIMISTIC_ACTION_TIMEOUT):
                self._clear_pending_state()
            else:
                return self._pending_state
        return bool(self.device_data.get("is_heating") or self.device_data.get("is_running"))

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the heater on (component 9 = generic ON/OFF)."""
        self._set_pending_state(True)
        success = await self._api.control_device_component(self._device_id, COMPONENT_PUMP_ONOFF, 1)
        if success:
            await self.coordinator.async_request_refresh()
        else:
            self._clear_pending_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the heater off (component 9 = generic ON/OFF)."""
        self._set_pending_state(False)
        success = await self._api.control_device_component(self._device_id, COMPONENT_PUMP_ONOFF, 0)
        if success:
            await self.coordinator.async_request_refresh()
        else:
            self._clear_pending_state()

    @property
    def extra_state_attributes(self) -> dict:
        """Return extra state attributes."""
        attrs: dict[str, Any] = {}
        if "current_temperature" in self.device_data:
            attrs["current_temperature"] = self.device_data["current_temperature"]
        if "target_temperature" in self.device_data:
            attrs["target_temperature"] = self.device_data["target_temperature"]
        return attrs
