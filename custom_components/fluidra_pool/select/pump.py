"""Pump speed select (low/medium/high)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Any

import aiohttp
from homeassistant.components.select import SelectEntity
from homeassistant.exceptions import HomeAssistantError

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

    @property
    def available(self) -> bool:
        """Return True if entity is available (and not auto-mode-controlled)."""
        auto_reported = self.device_data.get("auto_reported")
        if auto_reported is not None:
            auto_mode_enabled = bool(auto_reported)
        else:
            auto_mode_enabled = self.device_data.get("auto_mode_enabled", False)

        if auto_mode_enabled:
            return False

        return self.coordinator.last_update_success and self.device_data.get("online", False)

    @property
    def current_option(self) -> str | None:
        """Return the current speed option."""
        if self._optimistic_option is not None:
            return self._optimistic_option

        is_running = self.device_data.get("is_running", False)

        if not is_running:
            return "stopped"

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
        if option not in self._speed_mapping:
            return

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
                await self._api.control_device_component(self._device_id, COMPONENT_PUMP_ONOFF, 1)
                success = await self._api.control_device_component(self._device_id, component, value)

            if success:
                await asyncio.sleep(COMMAND_CONFIRMATION_DELAY)
                await self.coordinator.async_request_refresh()

        except (aiohttp.ClientError, TimeoutError, FluidraError) as err:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="pump_speed_set_failed") from err
        finally:
            self._optimistic_option = None
            self.async_write_ha_state()

    @property
    def icon(self) -> str:
        """Return the icon for the entity."""
        auto_reported = self.device_data.get("auto_reported")
        if auto_reported is not None:
            auto_mode_enabled = bool(auto_reported)
        else:
            auto_mode_enabled = self.device_data.get("auto_mode_enabled", False)

        return "mdi:autorenew" if auto_mode_enabled else "mdi:pump"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        current_percent = self.device_data.get("speed_percent", 0)

        auto_reported = self.device_data.get("auto_reported")
        if auto_reported is not None:
            auto_mode_enabled = bool(auto_reported)
        else:
            auto_mode_enabled = self.device_data.get("auto_mode_enabled", False)

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

        if auto_mode_enabled:
            attrs["control_status"] = "Contrôlé par le mode automatique"
            attrs["manual_control_disabled"] = True
        else:
            attrs["control_status"] = "Contrôle manuel disponible"
            attrs["manual_control_disabled"] = False

        return attrs
