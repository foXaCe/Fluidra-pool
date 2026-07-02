"""Concrete :class:`FluidraPoolAPI` — assembled from per-concern mixins."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import aiohttp

from ..api_resilience import CircuitBreaker, RateLimiter
from ._auth import AuthMixin
from ._commands import CommandsMixin
from ._components import ComponentsMixin
from ._devices import DevicesMixin
from ._schedules import SchedulesMixin
from ._session import SessionMixin

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant


class FluidraPoolAPI(SessionMixin, AuthMixin, DevicesMixin, ComponentsMixin, CommandsMixin, SchedulesMixin):
    """Wrapper for Fluidra Pool API for Home Assistant.

    Deliberately not slotted: there is a single instance per config entry (no
    memory pressure) and tests monkeypatch methods on real instances.
    """

    def __init__(
        self,
        email: str,
        password: str | None,
        hass: HomeAssistant | None = None,
        refresh_token: str | None = None,
        on_token_persist: Callable[[str], None] | None = None,
    ) -> None:
        """Initialize the API wrapper."""
        self.email: str = email
        self.password: str | None = password
        self._hass: HomeAssistant | None = hass
        self._session: aiohttp.ClientSession | None = None
        self._owns_session: bool = False
        self._session_lock: asyncio.Lock = asyncio.Lock()

        self.access_token: str | None = None
        self.refresh_token: str | None = refresh_token
        self.token_expires_at: int | None = None
        # Monotonic stamp of the last successful token store — lets waiters on
        # the token lock detect that another task already refreshed (see
        # AuthMixin.force_refresh_token).
        self._last_token_store: float = 0.0

        self._on_token_persist: Callable[[str], None] | None = on_token_persist

        self.user_pools: list[dict[str, Any]] = []
        self.devices: list[dict[str, Any]] = []
        self._pools: list[dict[str, Any]] = []

        self._circuit_breaker: CircuitBreaker = CircuitBreaker()
        self._rate_limiter: RateLimiter = RateLimiter()

        self._token_lock: asyncio.Lock = asyncio.Lock()
