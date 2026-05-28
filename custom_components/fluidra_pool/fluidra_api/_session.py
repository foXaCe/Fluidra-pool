"""HTTP session + request mixin (circuit breaker, rate limiter, retries)."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import aiohttp

from ..api_resilience import (
    BACKOFF_MULTIPLIER,
    CIRCUIT_BREAKER_TIMEOUT,
    INITIAL_BACKOFF,
    MAX_BACKOFF,
    MAX_RETRIES,
    FluidraCircuitBreakerError,
    FluidraConnectionError,
)
from ..const import DEFAULT_TIMEOUT
from ._constants import MAX_REFRESH_ATTEMPTS, RETRYABLE_STATUSES
from ._helpers import parse_json, parse_retry_after

if TYPE_CHECKING:
    pass

_LOGGER = logging.getLogger(__name__)


class SessionMixin:
    """Manages the aiohttp session and centralised HTTP request loop."""

    async def _get_session(self) -> aiohttp.ClientSession:
        """Return the shared or owned aiohttp session, creating it safely once."""
        if self._session is not None:
            return self._session

        async with self._session_lock:
            if self._session is not None:
                return self._session

            if self._hass is not None:
                from homeassistant.helpers.aiohttp_client import (
                    async_get_clientsession,
                )

                self._session = async_get_clientsession(self._hass)
                self._owns_session = False
            else:
                timeout = aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT)
                self._session = aiohttp.ClientSession(timeout=timeout)
                self._owns_session = True

            return self._session

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
    ) -> tuple[int, Any, str]:
        """Execute HTTP request with circuit breaker, rate limiting, and retry.

        Returns a tuple ``(status, parsed_body_or_None, raw_text)`` so callers
        never hold an unclosed ClientResponse. Body is consumed inside ``async
        with`` to guarantee release.

        Retries on transient network errors AND on HTTP 429/5xx responses.
        Centralises 401/403 token refresh so call-sites don't need to recurse.
        """
        if not skip_circuit_breaker and not self._circuit_breaker.can_execute():
            raise FluidraCircuitBreakerError(f"Circuit breaker open, retry after {CIRCUIT_BREAKER_TIMEOUT}s")

        if not self._rate_limiter.can_execute():
            wait_time = self._rate_limiter.wait_time()
            _LOGGER.debug("Rate limited, waiting %.1fs", wait_time)
            await asyncio.sleep(wait_time)

        self._rate_limiter.record_request()

        session = await self._get_session()
        request_headers = dict(headers) if headers else {}

        last_error: Exception | None = None
        backoff = INITIAL_BACKOFF
        refresh_attempts = 0
        request_timeout = aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT)

        for attempt in range(MAX_RETRIES + 1):
            try:
                async with session.request(
                    method.upper(),
                    url,
                    headers=request_headers,
                    json=json_data,
                    params=params,
                    timeout=request_timeout,
                ) as response:
                    status = response.status
                    raw_text = await response.text()

                if status in (401, 403) and not skip_auth_refresh and refresh_attempts < MAX_REFRESH_ATTEMPTS:
                    refresh_attempts += 1
                    _LOGGER.debug("Got %d, refreshing token and retrying", status)
                    if await self.force_refresh_token():
                        if "Authorization" in request_headers:
                            request_headers["Authorization"] = f"Bearer {self.access_token}"
                        continue
                    return status, parse_json(raw_text), raw_text

                if status in RETRYABLE_STATUSES and attempt < MAX_RETRIES:
                    # Don't record_failure per-retry: a request that eventually
                    # succeeds shouldn't push the circuit breaker toward open
                    # state (Issue #64). Only the *final* failure counts.
                    retry_after = parse_retry_after(response) if status == 429 else None
                    sleep_for = retry_after if retry_after is not None else backoff
                    _LOGGER.debug(
                        "HTTP %d on attempt %d/%d, sleeping %.1fs",
                        status,
                        attempt + 1,
                        MAX_RETRIES + 1,
                        sleep_for,
                    )
                    last_error = FluidraConnectionError(f"HTTP {status}")
                    await asyncio.sleep(sleep_for)
                    backoff = min(backoff * BACKOFF_MULTIPLIER, MAX_BACKOFF)
                    continue

                if 200 <= status < 300 and not skip_circuit_breaker:
                    self._circuit_breaker.record_success()

                return status, parse_json(raw_text), raw_text

            except (aiohttp.ClientError, TimeoutError) as err:
                last_error = err
                if attempt < MAX_RETRIES:
                    _LOGGER.debug(
                        "Request failed (attempt %d/%d): %s, retrying in %.1fs",
                        attempt + 1,
                        MAX_RETRIES + 1,
                        err,
                        backoff,
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * BACKOFF_MULTIPLIER, MAX_BACKOFF)
                else:
                    _LOGGER.warning(
                        "Request failed after %d attempts: %s",
                        MAX_RETRIES + 1,
                        err,
                    )

        # All retries exhausted — record exactly one failure for the circuit breaker.
        if not skip_circuit_breaker:
            self._circuit_breaker.record_failure()
        raise FluidraConnectionError(f"Request failed after {MAX_RETRIES + 1} attempts: {last_error}")

    async def close(self) -> None:
        """Close the API connection, but only if we own the session."""
        if self._session and self._owns_session:
            try:
                await self._session.close()
            except (aiohttp.ClientError, OSError):
                _LOGGER.debug("Failed to close API session")
        self._session = None
        self._owns_session = False
