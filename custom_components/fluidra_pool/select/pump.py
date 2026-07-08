"""Pump speed select (low/medium/high)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Any

import aiohttp
from homeassistant.components.select import SelectEntity
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from ..api_resilience import FluidraError
from ..const import (
    COMMAND_CONFIRMATION_DELAY,
    COMPONENT_PUMP_ONOFF,
    COMPONENT_PUMP_SPEED,
    DOMAIN,
    UI_UPDATE_DELAY,
)
from ..entity import FluidraPoolControlEntity

if TYPE_CHECKING:
    from ..coordinator import FluidraDataUpdateCoordinator
    from ..fluidra_api import FluidraPoolAPI

_LOGGER = logging.getLogger(__name__)


class FluidraPumpSpeedSelect(FluidraPoolControlEntity, SelectEntity):
    """Representation of a Fluidra pump speed select control."""

    __slots__ = (
        "_optimistic_option",
        "_percent_to_option",
        "_speed_mapping",
    )

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api: FluidraPoolAPI,
        pool_id: str,
        device_id: str,
    ) -> None:
        """Initialize the pump speed select."""
        super().__init__(coordinator, api, pool_id, device_id)
        self._optimistic_option: str | None = None

        self._attr_unique_id = f"fluidra_{self._device_id}_speed_level"
        self._attr_translation_key = "pump_speed"

        # Internal option keys translated via strings.json.
        self._attr_options = ["stopped", "low", "medium", "high"]

        # "stopped" = pump ON but no specific flow (natural state).
        self._speed_mapping = {
            "stopped": {"component": 9, "value": 1, "percent": 0, "keep_pump_on": True},
            "low": {"component": 11, "value": 0, "percent": 45},
            "medium": {"component": 11, "value": 1, "percent": 65},
            "high": {"component": 11, "value": 2, "percent": 100},
        }

        self._percent_to_option = {0: "stopped", 45: "low", 65: "medium", 100: "high"}

    def _auto_mode_enabled(self) -> bool:
        """Return True when the schedule-driven auto mode drives the pump."""
        auto_reported = self.device_data.get("auto_reported")
        if auto_reported is not None:
            return bool(auto_reported)
        return bool(self.device_data.get("auto_mode_enabled", False))

    @property
    def current_option(self) -> str | None:
        """Return the current speed option.

        The entity stays available in auto mode: the state is perfectly
        readable (the coordinator even derives the scheduled speed), only
        *manual writes* are rejected — see :meth:`async_select_option`.
        """
        if self._optimistic_option is not None:
            return self._optimistic_option

        is_running = self.device_data.get("is_running", False)

        if not is_running:
            return "stopped"

        if self._auto_mode_enabled():
            # In auto mode the effective speed comes from the schedule
            # calculation (speed_level_reported can be stale).
            current_percent = self.device_data.get("speed_percent", 0)
            if current_percent == 0:
                return "stopped"
            return self._percent_to_option.get(current_percent, "low")

        speed_level = self.device_data.get("speed_level_reported")
        if speed_level is not None:
            level_to_option = {0: "low", 1: "medium", 2: "high"}
            return level_to_option.get(speed_level, "low")

        current_percent = self.device_data.get("speed_percent", 0)

        if current_percent == 0:
            return "stopped"

        return self._percent_to_option.get(current_percent, "low")

    async def async_select_option(self, option: str) -> None:
        """Select new speed option."""
        self._ensure_pool_writable()
        if option not in self._speed_mapping:
            return

        if self._auto_mode_enabled():
            # Readable but not manually writable while schedules drive the pump.
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="pump_in_auto_mode",
            )

        speed_config = self._speed_mapping[option]
        component = speed_config["component"]
        value = speed_config["value"]

        try:
            self._optimistic_option = option
            self.async_write_ha_state()

            await asyncio.sleep(UI_UPDATE_DELAY)

            if option == "stopped":
                success = await self._api.control_device_component(self._device_id, COMPONENT_PUMP_ONOFF, 1)
                if success:
                    # Best-effort attempt to clear active speed; server may reject.
                    with contextlib.suppress(FluidraError, aiohttp.ClientError, TimeoutError):
                        await self._api.control_device_component(self._device_id, COMPONENT_PUMP_SPEED, -1)
            else:
                success = await self._api.control_device_component(self._device_id, COMPONENT_PUMP_ONOFF, 1)
                if success:
                    success = await self._api.control_device_component(self._device_id, component, value)

            if success:
                await asyncio.sleep(COMMAND_CONFIRMATION_DELAY)
                await self.coordinator.async_request_refresh()

        except (aiohttp.ClientError, TimeoutError, FluidraError) as err:
            self._optimistic_option = None
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="pump_speed_set_failed") from err
        finally:
            self._optimistic_option = None
            self.async_write_ha_state()

        if not success:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="pump_speed_set_failed")

    @property
    def icon(self) -> str:
        """Return the icon for the entity."""
        return "mdi:autorenew" if self._auto_mode_enabled() else "mdi:pump"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        current_percent = self.device_data.get("speed_percent", 0)
        auto_mode_enabled = self._auto_mode_enabled()

        attrs = {
            "speed_percent": current_percent,
            "pump_model": self.device_data.get("model", "E30iQ"),
            "pump_type": self.device_data.get("pump_type", "variable_speed"),
            "operation_mode": self.device_data.get("operation_mode", 0),
            "auto_mode": auto_mode_enabled,
            "online": self.device_data.get("online", False),
            "optimistic_option": self._optimistic_option,
            "using_optimistic": self._optimistic_option is not None,
        }

        # Stable machine-readable tokens (attribute values are not translatable
        # in HA; the previous hardcoded French strings broke non-FR setups).
        attrs["control_status"] = "auto" if auto_mode_enabled else "manual"
        attrs["manual_control_disabled"] = auto_mode_enabled

        return attrs
