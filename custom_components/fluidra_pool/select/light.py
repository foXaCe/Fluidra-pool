"""Light effect/scene select (LumiPlus Connect)."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

import aiohttp
from homeassistant.components.select import SelectEntity
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError

from ..api_resilience import FluidraError
from ..const import DOMAIN, OPTIMISTIC_ACTION_TIMEOUT, UI_UPDATE_DELAY
from ..entity import FluidraPoolControlEntity

if TYPE_CHECKING:
    from ..coordinator import FluidraDataUpdateCoordinator
    from ..fluidra_api import FluidraPoolAPI

_LOGGER = logging.getLogger(__name__)


class FluidraLightEffectSelect(FluidraPoolControlEntity, SelectEntity):
    """Select entity for LumiPlus Connect light effect/scene selection."""

    EFFECT_COMPONENT = 18

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api: FluidraPoolAPI,
        pool_id: str,
        device_id: str,
    ) -> None:
        """Initialize the light effect select."""
        super().__init__(coordinator, api, pool_id, device_id)
        self._optimistic_option: str | None = None
        self._optimistic_time: float = 0.0

        self._attr_unique_id = f"fluidra_{self._device_id}_effect"
        self._attr_translation_key = "light_effect"

        # Static color + 8 scenes as discovered via mitmproxy.
        self._attr_options = [
            "static_color",
            "scene_1",
            "scene_2",
            "scene_3",
            "scene_4",
            "scene_5",
            "scene_6",
            "scene_7",
            "scene_8",
        ]

        self._effect_mapping = {
            "static_color": 0,
            "scene_1": 1,
            "scene_2": 2,
            "scene_3": 3,
            "scene_4": 4,
            "scene_5": 5,
            "scene_6": 6,
            "scene_7": 7,
            "scene_8": 8,
        }

        self._value_to_effect = {
            0: "static_color",
            1: "scene_1",
            2: "scene_2",
            3: "scene_3",
            4: "scene_4",
            5: "scene_5",
            6: "scene_6",
            7: "scene_7",
            8: "scene_8",
        }

    def _get_reported_effect(self) -> str:
        """Read the effect option from coordinator component data."""
        components = self.device_data.get("components", {})
        component_data = components.get(str(self.EFFECT_COMPONENT), {})
        effect_value = component_data.get("reportedValue", component_data.get("desiredValue", 0))
        # LumiPlus components can report values as strings (the light platform writes
        # power as the string "1"); coerce so the int-keyed lookup doesn't silently
        # fall back to "static_color" for a real scene.
        try:
            effect_value = int(effect_value)
        except (ValueError, TypeError):
            effect_value = 0

        return self._value_to_effect.get(effect_value, "static_color")

    def _optimistic_expired(self) -> bool:
        """Return True once the optimistic value has outlived its timeout."""
        return time.time() - self._optimistic_time > OPTIMISTIC_ACTION_TIMEOUT

    @property
    def current_option(self) -> str | None:
        """Return the current effect option (optimistic until the API confirms).

        Pure getter: the optimistic value is dropped in
        ``_handle_coordinator_update`` (or once it expires), never here.
        """
        if self._optimistic_option is not None and not self._optimistic_expired():
            return self._optimistic_option
        return self._get_reported_effect()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Drop the optimistic value once the backend confirms it (or it expires)."""
        if self._optimistic_option is not None and (
            self._get_reported_effect() == self._optimistic_option or self._optimistic_expired()
        ):
            self._optimistic_option = None
        super()._handle_coordinator_update()

    async def async_select_option(self, option: str) -> None:
        """Select new effect option."""
        self._ensure_pool_writable()
        if option not in self._effect_mapping:
            return

        effect_value = self._effect_mapping[option]

        self._optimistic_option = option
        self._optimistic_time = time.time()
        self.async_write_ha_state()

        await asyncio.sleep(UI_UPDATE_DELAY)

        _LOGGER.debug(
            "Setting light effect for %s: component %s = %s",
            self._device_id,
            self.EFFECT_COMPONENT,
            effect_value,
        )

        try:
            success = await self._api.control_device_component(self._device_id, self.EFFECT_COMPONENT, effect_value)
        except (aiohttp.ClientError, TimeoutError, FluidraError) as err:
            self._optimistic_option = None
            self.async_write_ha_state()
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="light_set_failed") from err

        if not success:
            self._optimistic_option = None
            self.async_write_ha_state()
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="light_set_failed")

        await self.coordinator.async_request_refresh()

    @property
    def icon(self) -> str:
        """Return the icon for the entity."""
        return "mdi:palette"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        components = self.device_data.get("components", {})
        component_data = components.get(str(self.EFFECT_COMPONENT), {})

        return {
            "device_id": self._device_id,
            "effect_component": self.EFFECT_COMPONENT,
            "reported_value": component_data.get("reportedValue"),
            "desired_value": component_data.get("desiredValue"),
            "optimistic_option": self._optimistic_option,
        }
