"""Light effect/scene select (LumiPlus Connect)."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import aiohttp
from homeassistant.components.select import SelectEntity
from homeassistant.exceptions import HomeAssistantError

from ..api_resilience import FluidraError
from ..const import COMMAND_CONFIRMATION_DELAY, DOMAIN, UI_UPDATE_DELAY
from ..entity import FluidraPoolControlEntity

if TYPE_CHECKING:
    from ..coordinator import FluidraDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


class FluidraLightEffectSelect(FluidraPoolControlEntity, SelectEntity):
    """Select entity for LumiPlus Connect light effect/scene selection."""

    EFFECT_COMPONENT = 18

    __slots__ = ("_effect_mapping", "_optimistic_option", "_value_to_effect")

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api,
        pool_id: str,
        device_id: str,
    ) -> None:
        """Initialize the light effect select."""
        super().__init__(coordinator, api, pool_id, device_id)
        self._optimistic_option: str | None = None

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

    @property
    def current_option(self) -> str | None:
        """Return the current effect option."""
        if self._optimistic_option is not None:
            return self._optimistic_option

        components = self.device_data.get("components", {})
        component_data = components.get(str(self.EFFECT_COMPONENT), {})
        effect_value = component_data.get("reportedValue", component_data.get("desiredValue", 0))

        return self._value_to_effect.get(effect_value, "static_color")

    async def async_select_option(self, option: str) -> None:
        """Select new effect option."""
        if option not in self._effect_mapping:
            return

        effect_value = self._effect_mapping[option]

        try:
            self._optimistic_option = option
            self.async_write_ha_state()

            await asyncio.sleep(UI_UPDATE_DELAY)

            _LOGGER.debug(
                "Setting light effect for %s: component %s = %s",
                self._device_id,
                self.EFFECT_COMPONENT,
                effect_value,
            )

            success = await self._api.control_device_component(self._device_id, self.EFFECT_COMPONENT, effect_value)

            _LOGGER.debug("Light effect API call result: %s", success)

            if success:
                await asyncio.sleep(COMMAND_CONFIRMATION_DELAY)
                await self.coordinator.async_request_refresh()

        except (
            aiohttp.ClientError,
            TimeoutError,
            FluidraError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
        ) as err:
            _LOGGER.error("Failed to set light effect: %s", err)
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="light_set_failed") from err
        finally:
            self._optimistic_option = None
            self.async_write_ha_state()

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
