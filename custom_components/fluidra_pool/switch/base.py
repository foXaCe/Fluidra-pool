"""Base class for Fluidra Pool switch entities."""

from __future__ import annotations

import time

from homeassistant.components.switch import SwitchEntity

from ..const import DOMAIN
from ..entity import FluidraPoolControlEntity


class FluidraPoolSwitchEntity(FluidraPoolControlEntity, SwitchEntity):
    """Base class for Fluidra Pool switch entities."""

    __slots__ = ("_last_action_time", "_pending_state")

    def __init__(self, coordinator, api, pool_id: str, device_id: str):
        """Initialize the switch."""
        super().__init__(coordinator, api, pool_id, device_id)
        self._pending_state: bool | None = None
        self._last_action_time: float | None = None

    @property
    def unique_id(self) -> str:
        """Return unique ID."""
        return f"{DOMAIN}_{self._pool_id}_{self._device_id}"

    @property
    def assumed_state(self) -> bool:
        """Return True if state is assumed during command execution."""
        return self._pending_state is not None

    def _set_pending_state(self, state: bool) -> None:
        """Set pending state for optimistic UI updates."""
        self._pending_state = state
        self._last_action_time = time.time()
        self.async_write_ha_state()

    def _clear_pending_state(self) -> None:
        """Clear pending state after API confirmation."""
        self._pending_state = None
        self._last_action_time = None
        self.async_write_ha_state()

    def _pending_state_expired(self, timeout: float) -> bool:
        """Return True if a pending optimistic state has timed out."""
        return self._last_action_time is None or time.time() - self._last_action_time > timeout
