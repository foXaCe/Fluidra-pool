"""Auxiliary output selects (OFF/ON/AUTO) for the eXO iQ's Aux 1 / Aux 2."""

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
from ..const import CHLORINATOR_MODE_OPTIMISTIC_TIMEOUT, DOMAIN, UI_UPDATE_DELAY
from ..device_registry import DeviceIdentifier
from ..entity import FluidraPoolControlEntity

if TYPE_CHECKING:
    from ..coordinator import FluidraDataUpdateCoordinator
    from ..fluidra_api import FluidraPoolAPI

_LOGGER = logging.getLogger(__name__)

# Both aux registers share the same encoding, observed directly on an eXO iQ:
# c30 went 0 -> 1 when Aux 1's light was switched on, and c31 sat at 2 with
# Aux 2 on Auto and dropped to 0 when it was switched off (Issue #175).
AUX_VALUE_TO_OPTION = {0: "off", 1: "on", 2: "auto"}


class FluidraAuxOutputSelect(FluidraPoolControlEntity, SelectEntity):
    """Three-state select for one auxiliary output (OFF/ON/AUTO).

    The eXO iQ can drive each aux as a light, a backwash valve, heating or a
    plain relay; the assignment is made on the unit itself and only changes
    what the official app offers, not how the register behaves. So this stays
    a neutral three-state select rather than guessing at a light or a switch.
    """

    __slots__ = ("_aux_number", "_component", "_optimistic_option", "_optimistic_time")

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api: FluidraPoolAPI,
        pool_id: str,
        device_id: str,
        aux_number: str,
        component: int,
    ) -> None:
        """Initialize the select for the aux output on ``component``."""
        super().__init__(coordinator, api, pool_id, device_id)
        self._aux_number = aux_number
        self._component = component
        self._optimistic_option: str | None = None
        self._optimistic_time = 0.0

        self._attr_unique_id = f"fluidra_{self._device_id}_aux_{aux_number}"
        self._attr_translation_key = "aux_output"
        self._attr_translation_placeholders = {"number": aux_number}
        self._attr_options = ["off", "on", "auto"]

    def _get_api_option(self) -> str | None:
        """Return the option the device currently reports, if it reports one."""
        components = self.device_data.get("components", {})
        raw = components.get(str(self._component), {}).get("reportedValue")
        if raw is None:
            return None
        try:
            value = int(raw)
        except (ValueError, TypeError):
            _LOGGER.debug("Unparsable aux %s value %s on c%s", self._aux_number, raw, self._component)
            return None
        return AUX_VALUE_TO_OPTION.get(value)

    def _optimistic_expired(self) -> bool:
        """Return True once the optimistic value has outlived its timeout."""
        return time.time() - self._optimistic_time > CHLORINATOR_MODE_OPTIMISTIC_TIMEOUT

    @property
    def current_option(self) -> str | None:
        """Return the current option (optimistic until the API confirms)."""
        if self._optimistic_option is not None and not self._optimistic_expired():
            return self._optimistic_option
        return self._get_api_option()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Drop the optimistic value once the backend confirms it (or it expires)."""
        if self._optimistic_option is not None and (
            self._get_api_option() == self._optimistic_option or self._optimistic_expired()
        ):
            self._optimistic_option = None
        super()._handle_coordinator_update()

    async def async_select_option(self, option: str) -> None:
        """Drive the aux output to ``option``."""
        self._ensure_pool_writable()
        option_to_value = {v: k for k, v in AUX_VALUE_TO_OPTION.items()}
        if option not in option_to_value:
            return

        self._optimistic_option = option
        self._optimistic_time = time.time()
        self.async_write_ha_state()

        await asyncio.sleep(UI_UPDATE_DELAY)

        try:
            success = await self._api.control_device_component(
                self._device_id, self._component, option_to_value[option]
            )
        except (aiohttp.ClientError, TimeoutError, FluidraError) as err:
            self._optimistic_option = None
            self.async_write_ha_state()
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="aux_set_failed") from err

        if success:
            await self.coordinator.async_request_refresh()
        else:
            self._optimistic_option = None
            self.async_write_ha_state()
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="aux_set_failed")

    @property
    def icon(self) -> str:
        """Return the icon for the entity."""
        current = self.current_option
        if current == "on":
            return "mdi:toggle-switch-variant"
        if current == "auto":
            return "mdi:calendar-clock"
        return "mdi:toggle-switch-variant-off"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        attributes: dict[str, Any] = {
            "device_id": self._device_id,
            "component": self._component,
            "optimistic_option": self._optimistic_option,
        }
        # The unit reports what each output is wired to as a plain string
        # (e.g. 'Lighting', 'Other'); surface it so the entity is identifiable
        # without opening the app.
        label_components = DeviceIdentifier.get_feature(self.device_data, "aux_labels", {})
        label_component = label_components.get(self._aux_number)
        if label_component is not None:
            components = self.device_data.get("components", {})
            label = components.get(str(label_component), {}).get("reportedValue")
            if isinstance(label, str) and label:
                attributes["assigned_function"] = label
        return attributes
