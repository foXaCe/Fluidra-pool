"""Typed base declaring shared state and cross-mixin methods.

The concrete :class:`FluidraPoolAPI` is assembled from independent mixins that
read shared state (tokens, HTTP session, resilience helpers) and call methods
defined on *sibling* mixins. Declaring those references here — guarded by
``TYPE_CHECKING`` so there is zero runtime cost and no impact on the MRO — lets a
type checker resolve them instead of flagging "has no attribute" on each mixin.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Callable

    import aiohttp
    from homeassistant.core import HomeAssistant

    from ..api_resilience import CircuitBreaker, RateLimiter


class FluidraAPIBase:
    """Declare shared attributes and cross-mixin methods for type checkers only."""

    if TYPE_CHECKING:
        # --- Shared state (assigned in FluidraPoolAPI.__init__) ---
        email: str
        password: str | None
        _hass: HomeAssistant | None
        _session: aiohttp.ClientSession | None
        _owns_session: bool
        _session_lock: asyncio.Lock
        access_token: str | None
        refresh_token: str | None
        id_token: str | None
        token_expires_at: int | None
        _on_token_persist: Callable[[str], None] | None
        user_pools: list[dict[str, Any]]
        devices: list[dict[str, Any]]
        _pools: list[dict[str, Any]]
        _circuit_breaker: CircuitBreaker
        _rate_limiter: RateLimiter
        _token_lock: asyncio.Lock

        # --- Cross-mixin methods (defined on sibling mixins) ---
        async def _request(
            self,
            method: str,
            url: str,
            *,
            headers: dict[str, str] | None = None,
            json_data: Any = None,
            params: dict[str, Any] | None = None,
            skip_circuit_breaker: bool = False,
            skip_auth_refresh: bool = False,
        ) -> tuple[int, Any, str]: ...

        def _build_auth_headers(self) -> dict[str, str]: ...

        async def ensure_valid_token(self) -> bool: ...

        async def force_refresh_token(self) -> bool: ...

        async def async_update_data(self) -> None: ...

        def get_device_by_id(self, device_id: str) -> dict[str, Any] | None: ...

        async def control_device_component(
            self, device_id: str, component_id: int, value: int | str | dict[str, Any]
        ) -> bool: ...
