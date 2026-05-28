"""Chlorinator mode select (OFF/ON/AUTO)."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from homeassistant.components.select import SelectEntity

from ..const import UI_UPDATE_DELAY
from ..device_registry import DeviceIdentifier
from ..entity import FluidraPoolControlEntity

if TYPE_CHECKING:
    from ..coordinator import FluidraDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


class FluidraChlorinatorModeSelect(FluidraPoolControlEntity, SelectEntity):
    """Select entity for chlorinator mode (OFF/ON/AUTO)."""

    __slots__ = ("_mode_mapping", "_optimistic_option", "_optimistic_time", "_value_to_mode")

    # Keep optimistic value until API confirms (or this many seconds pass).
    OPTIMISTIC_TIMEOUT = 120

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api,
        pool_id: str,
        device_id: str,
    ) -> None:
        """Initialize the chlorinator mode select."""
        super().__init__(coordinator, api, pool_id, device_id)
        self._optimistic_option: str | None = None
        self._optimistic_time = 0.0

        self._attr_unique_id = f"fluidra_{self._device_id}_mode"
        self._attr_translation_key = "chlorinator_mode"

        # Internal option keys in English (translated via strings.json).
        self._attr_options = ["off", "on", "auto"]

        # Default mapping is DM-style (0=off, 1=on, 2=auto); per-device override
        # via DeviceConfig.features["mode_mapping"].
        config_mapping = DeviceIdentifier.get_feature(self.device_data, "mode_mapping", None)
        if config_mapping:
            self._value_to_mode = {int(k): v for k, v in config_mapping.items()}
        else:
            self._value_to_mode = {0: "off", 1: "on", 2: "auto"}

        self._mode_mapping = {v: k for k, v in self._value_to_mode.items()}

    def _get_api_mode(self) -> str:
        """Read mode value directly from coordinator component data."""
        mode_comp = DeviceIdentifier.get_feature(self.device_data, "mode_component", 20)
        components = self.device_data.get("components", {})
        comp_data = components.get(str(mode_comp), {})
        mode_value = comp_data.get("reportedValue", self.device_data.get("mode_reported", 0))
        try:
            mode_value = int(mode_value)
        except (ValueError, TypeError):
            mode_value = 0
        return self._value_to_mode.get(mode_value, "off")

    @property
    def current_option(self) -> str | None:
        """Return the current mode option."""
        if self._optimistic_option is not None:
            api_mode = self._get_api_mode()
            if api_mode == self._optimistic_option:
                # Server caught up — drop the optimistic value.
                self._optimistic_option = None
                return api_mode
            if time.time() - self._optimistic_time > self.OPTIMISTIC_TIMEOUT:
                self._optimistic_option = None
                return api_mode
            return self._optimistic_option

        return self._get_api_mode()

    async def async_select_option(self, option: str) -> None:
        """Select new mode option."""
        if option not in self._mode_mapping:
            return

        mode_value = self._mode_mapping[option]
        mode_comp = DeviceIdentifier.get_feature(self.device_data, "mode_component", 20)

        self._optimistic_option = option
        self._optimistic_time = time.time()
        self.async_write_ha_state()

        await asyncio.sleep(UI_UPDATE_DELAY)

        success = await self._api.control_device_component(self._device_id, mode_comp, mode_value)

        if success:
            await self.coordinator.async_request_refresh()
        else:
            self._optimistic_option = None
            self.async_write_ha_state()

    @property
    def icon(self) -> str:
        """Return the icon for the entity."""
        current = self.current_option
        if current == "off":
            return "mdi:water-off"
        if current == "on":
            return "mdi:water"
        return "mdi:water-sync"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        mode_comp = DeviceIdentifier.get_feature(self.device_data, "mode_component", 20)
        return {
            "device_id": self._device_id,
            "mode_component": mode_comp,
            "optimistic_option": self._optimistic_option,
        }
