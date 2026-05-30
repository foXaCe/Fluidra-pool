"""HTTP session + request mixin (circuit breaker, rate limiter, retries)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

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
from ._base import FluidraAPIBase
from ._constants import MAX_REFRESH_ATTEMPTS, RETRYABLE_STATUSES
from ._helpers import parse_json, parse_retry_after

_LOGGER = logging.getLogger(__name__)


class SessionMixin(FluidraAPIBase):
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
        # Track transient (network / retryable-status) retries separately from auth
        # refreshes so a token refresh on the final transient attempt still gets an
        # iteration to re-send instead of falling through to a spurious failure.
        transient_attempts = 0
        request_timeout = aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT)

        for _attempt in range(MAX_RETRIES + 1 + MAX_REFRESH_ATTEMPTS):
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

                if status in RETRYABLE_STATUSES and transient_attempts < MAX_RETRIES:
                    # Don't record_failure per-retry: a request that eventually
                    # succeeds shouldn't push the circuit breaker toward open
                    # state (Issue #64). Only the *final* failure counts.
                    retry_after = parse_retry_after(response) if status == 429 else None
                    sleep_for = retry_after if retry_after is not None else backoff
                    _LOGGER.debug(
                        "HTTP %d on attempt %d/%d, sleeping %.1fs",
                        status,
                        transient_attempts + 1,
                        MAX_RETRIES + 1,
                        sleep_for,
                    )
                    last_error = FluidraConnectionError(f"HTTP {status}")
                    transient_attempts += 1
                    await asyncio.sleep(sleep_for)
                    backoff = min(backoff * BACKOFF_MULTIPLIER, MAX_BACKOFF)
                    continue

                if not skip_circuit_breaker:
                    if 200 <= status < 300:
                        self._circuit_breaker.record_success()
                    elif status in RETRYABLE_STATUSES:
                        # Retries exhausted on a persistent 5xx/429: count exactly
                        # one failure so the breaker can open for sustained
                        # HTTP-level outages, not only raw network/timeout errors.
                        # Reaching here with a retryable status means
                        # transient_attempts >= MAX_RETRIES (the retry branch above
                        # would otherwise have continued).
                        self._circuit_breaker.record_failure()

                return status, parse_json(raw_text), raw_text

            except (aiohttp.ClientError, TimeoutError) as err:
                last_error = err
                if transient_attempts < MAX_RETRIES:
                    _LOGGER.debug(
                        "Request failed (attempt %d/%d): %s, retrying in %.1fs",
                        transient_attempts + 1,
                        MAX_RETRIES + 1,
                        err,
                        backoff,
                    )
                    transient_attempts += 1
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * BACKOFF_MULTIPLIER, MAX_BACKOFF)
                else:
                    _LOGGER.warning(
                        "Request failed after %d attempts: %s",
                        MAX_RETRIES + 1,
                        err,
                    )
                    break

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
