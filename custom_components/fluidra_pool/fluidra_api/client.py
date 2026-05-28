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
    """Wrapper for Fluidra Pool API for Home Assistant."""

    __slots__ = (
        "_circuit_breaker",
        "_hass",
        "_on_token_persist",
        "_owns_session",
        "_pools",
        "_rate_limiter",
        "_session",
        "_session_lock",
        "_token_lock",
        "access_token",
        "devices",
        "email",
        "id_token",
        "password",
        "refresh_token",
        "token_expires_at",
        "user_pools",
    )

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
        self.id_token: str | None = None
        self.token_expires_at: int | None = None

        self._on_token_persist: Callable[[str], None] | None = on_token_persist

        self.user_pools: list[dict[str, Any]] = []
        self.devices: list[dict[str, Any]] = []
        self._pools: list[dict[str, Any]] = []

        self._circuit_breaker: CircuitBreaker = CircuitBreaker()
        self._rate_limiter: RateLimiter = RateLimiter()

        self._token_lock: asyncio.Lock = asyncio.Lock()
