"""Chlorinator-related switches (main ON/OFF + boost mode)."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import aiohttp
from homeassistant.exceptions import HomeAssistantError

from ..api_resilience import FluidraError
from ..const import DOMAIN, OPTIMISTIC_ACTION_TIMEOUT, SWITCH_CONFIRMATION_DELAY
from ..device_registry import DeviceIdentifier
from .base import FluidraPoolSwitchEntity

if TYPE_CHECKING:
    from ..coordinator import FluidraDataUpdateCoordinator
    from ..fluidra_api import FluidraPoolAPI

_LOGGER = logging.getLogger(__name__)


class FluidraChlorinatorBoostSwitch(FluidraPoolSwitchEntity):
    """Switch for chlorinator boost mode."""

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api: FluidraPoolAPI,
        pool_id: str,
        device_id: str,
    ) -> None:
        """Initialize the boost mode switch."""
        super().__init__(coordinator, api, pool_id, device_id)

        self._attr_unique_id = f"fluidra_{self._device_id}_boost_mode"
        self._attr_translation_key = "boost_mode"
        self._attr_icon = "mdi:rocket-launch"

    @property
    def unique_id(self) -> str:
        """Return unique ID (override base class to use _attr_unique_id)."""
        return self._attr_unique_id or f"{DOMAIN}_{self._pool_id}_{self._device_id}_boost_mode"

    def _get_current_mode(self) -> str:
        """Get current chlorinator mode from mode component."""
        mode_comp = DeviceIdentifier.get_feature(self.device_data, "mode_component", 20)
        components = self.device_data.get("components", {})
        comp_data = components.get(str(mode_comp), {})
        mode_value = comp_data.get("reportedValue", 0)
        try:
            mode_value = int(mode_value)
        except (ValueError, TypeError):
            mode_value = 0
        mode_mapping = DeviceIdentifier.get_feature(self.device_data, "mode_mapping", None)
        if mode_mapping:
            return str({int(k): v for k, v in mode_mapping.items()}.get(mode_value, "off"))
        return {0: "off", 1: "on", 2: "auto"}.get(mode_value, "off")

    @property
    def available(self) -> bool:
        """Boost only available when mode is ON (or always if no mode select)."""
        base_available = super().available
        if not base_available:
            return False
        if DeviceIdentifier.has_feature(self.device_data, "skip_mode_select"):
            return True
        return self._get_current_mode() == "on"

    @property
    def is_on(self) -> bool:
        """Return true if boost mode is on using optimistic UI."""
        boost_component = DeviceIdentifier.get_feature(self.device_data, "boost_mode", 245)

        components = self.device_data.get("components", {})
        component_data = components.get(str(boost_component), {})
        boost_value = component_data.get("reportedValue", False)
        actual_state = bool(boost_value)

        if self._pending_state is not None:
            if actual_state == self._pending_state or self._pending_state_expired(10):
                self._clear_pending_state()
                return actual_state
            return self._pending_state

        return actual_state

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn boost mode on with optimistic UI."""
        boost_component = DeviceIdentifier.get_feature(self.device_data, "boost_mode", 245)

        mode_comp = DeviceIdentifier.get_feature(self.device_data, "mode_component", 20)
        mode_mapping = DeviceIdentifier.get_feature(self.device_data, "mode_mapping", None)
        if mode_mapping:
            on_value = {v: int(k) for k, v in mode_mapping.items()}.get("on", 1)
        else:
            on_value = 1

        try:
            self._set_pending_state(True)

            if (
                not DeviceIdentifier.has_feature(self.device_data, "skip_mode_select")
                and self._get_current_mode() != "on"
            ):
                await self._api.control_device_component(self._device_id, mode_comp, on_value)
                await asyncio.sleep(0.5)

            success = await self._api.control_device_component(self._device_id, boost_component, True)

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
            _LOGGER.debug("Failed to enable boost mode: %s", err)
            self._clear_pending_state()
            self.async_write_ha_state()
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="boost_set_failed") from err

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn boost mode off with optimistic UI."""
        boost_component = DeviceIdentifier.get_feature(self.device_data, "boost_mode", 245)

        try:
            self._set_pending_state(False)

            success = await self._api.control_device_component(self._device_id, boost_component, False)

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
            _LOGGER.debug("Failed to disable boost mode: %s", err)
            self._clear_pending_state()
            self.async_write_ha_state()
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="boost_set_failed") from err

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        boost_component = DeviceIdentifier.get_feature(self.device_data, "boost_mode", 245)

        return {
            "component": boost_component,
            "device_id": self._device_id,
            "current_mode": self._get_current_mode(),
            "pending_action": self._pending_state is not None,
        }


class FluidraChlorinatorSwitch(FluidraPoolSwitchEntity):
    """Switch for controlling chlorinator ON/OFF (e.g., Zodiac EXO iQ)."""

    _attr_translation_key = "chlorinator"

    @property
    def unique_id(self) -> str:
        """Return unique ID."""
        return f"{DOMAIN}_{self._pool_id}_{self._device_id}_chlorinator"

    @property
    def icon(self) -> str:
        """Return the icon of the switch."""
        if self.is_on:
            return "mdi:flask"
        return "mdi:flask-outline"

    def _actual_state(self) -> bool:
        """Return the polled on/off state (component → pump_reported → is_running)."""
        on_off_component = DeviceIdentifier.get_feature(self.device_data, "on_off_component", 9)
        components = self.device_data.get("components", {})
        comp_data = components.get(str(on_off_component), {})
        reported = comp_data.get("reportedValue")
        if reported is not None:
            return bool(reported)
        pump_reported = self.device_data.get("pump_reported")
        if pump_reported is not None:
            return bool(pump_reported)
        value: bool = self.device_data.get("is_running", False)
        return value

    @property
    def is_on(self) -> bool:
        """Return true if the chlorinator is on."""
        actual = self._actual_state()
        if self._pending_state is not None:
            if actual == self._pending_state or self._pending_state_expired(OPTIMISTIC_ACTION_TIMEOUT):
                self._clear_pending_state()
                return actual
            return self._pending_state
        return actual

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the chlorinator on."""
        try:
            self._set_pending_state(True)

            on_off_component = DeviceIdentifier.get_feature(self.device_data, "on_off_component", 9)
            success = await self._api.control_device_component(self._device_id, on_off_component, 1)

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
            _LOGGER.debug("Failed to turn on chlorinator: %s", err)
            self._clear_pending_state()
            self.async_write_ha_state()
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="chlorinator_set_failed") from err

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the chlorinator off."""
        try:
            self._set_pending_state(False)

            on_off_component = DeviceIdentifier.get_feature(self.device_data, "on_off_component", 9)
            success = await self._api.control_device_component(self._device_id, on_off_component, 0)

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
            _LOGGER.debug("Failed to turn off chlorinator: %s", err)
            self._clear_pending_state()
            self.async_write_ha_state()
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="chlorinator_set_failed") from err

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        on_off_component = DeviceIdentifier.get_feature(self.device_data, "on_off_component", 9)
        return {
            "component_id": on_off_component,
            "operation": "chlorinator_control",
            "device_id": self._device_id,
            "pending_action": self._pending_state is not None,
            "action_timestamp": self._last_action_time,
        }
