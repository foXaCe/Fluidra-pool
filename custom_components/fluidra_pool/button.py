"""Button platform for Fluidra Pool integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import aiohttp
from homeassistant.components.button import ButtonEntity
from homeassistant.exceptions import HomeAssistantError

from .api_resilience import FluidraError
from .const import DOMAIN, FluidraPoolConfigEntry
from .device_registry import DeviceIdentifier
from .entity import FluidraPoolControlEntity
from .platform_setup import async_setup_dynamic_platform

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0  # Coordinator handles all updates


class FluidraPumpStopButton(FluidraPoolControlEntity, ButtonEntity):
    """Stop button for Victoria VS pumps (Issue #144).

    Mirrors the app's dedicated Stop control: an immediate motor halt via the
    stop trigger (c15) that does NOT disarm the auto schedule (c13), so future
    scheduled blocks still run. It's a momentary action (not a toggle), and it's
    disabled while the pump is already stopped, matching the native UI.
    """

    _attr_translation_key = "pump_stop"
    _attr_icon = "mdi:pump-off"

    @property
    def unique_id(self) -> str:
        """Return unique ID."""
        return f"{DOMAIN}_{self._pool_id}_{self._device_id}_stop"

    @property
    def available(self) -> bool:
        """Disabled while the pump is already stopped (nothing to halt)."""
        return super().available and bool(self.device_data.get("is_running"))

    async def async_press(self) -> None:
        """Halt the pump motor without disarming the schedule."""
        self._ensure_pool_writable()
        try:
            success = await self._api.pause_pump(self._device_id)
        except (aiohttp.ClientError, TimeoutError, FluidraError) as err:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="pump_set_failed") from err
        if not success:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="pump_set_failed")
        await self.coordinator.async_request_refresh()


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: FluidraPoolConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Fluidra Pool button entities, including devices added later."""
    coordinator = config_entry.runtime_data.coordinator

    def _build(pool_id: str, device: dict[str, Any]) -> list[ButtonEntity]:
        """Create button entities for one device."""
        entities: list[ButtonEntity] = []
        device_id = device["device_id"]

        if DeviceIdentifier.should_create_entity(device, "button_stop"):
            entities.append(FluidraPumpStopButton(coordinator, coordinator.api, pool_id, device_id))

        return entities

    await async_setup_dynamic_platform(config_entry, async_add_entities, _build)
