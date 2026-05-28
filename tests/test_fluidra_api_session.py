"""Tests for fluidra_api/_session.py — HTTP request loop, retries, circuit breaker."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from custom_components.fluidra_pool.api_resilience import (
    CircuitBreaker,
    FluidraCircuitBreakerError,
    FluidraConnectionError,
    RateLimiter,
)
from custom_components.fluidra_pool.fluidra_api._session import SessionMixin


def _mock_response(*, status: int = 200, text: str = "{}", retry_after: str | None = None) -> MagicMock:
    """Build a mock aiohttp response with the given status, body and optional Retry-After."""
    resp = MagicMock()
    resp.status = status
    resp.headers = {"Retry-After": retry_after} if retry_after else {}
    resp.text = AsyncMock(return_value=text)
    return resp


def _mock_session(responses: list[MagicMock | Exception]) -> MagicMock:
    """Build a mock aiohttp session whose `.request(...)` cycles through `responses`.

    Each entry can be either a mock response object (returned via async with) or
    an exception type (raised when entering the context manager).
    """
    iterator = iter(responses)

    def _next_request(*_args: Any, **_kwargs: Any) -> AsyncMock:
        next_item = next(iterator)
        ctx = AsyncMock()
        if isinstance(next_item, Exception):
            ctx.__aenter__.side_effect = next_item
        else:
            ctx.__aenter__.return_value = next_item
        ctx.__aexit__.return_value = None
        return ctx

    session = MagicMock()
    session.request = MagicMock(side_effect=_next_request)
    return session


class _FakeAPI(SessionMixin):
    """Stub: SessionMixin needs a few stateful attributes + a circuit breaker."""

    def __init__(self, session: MagicMock | None = None) -> None:
        self._session = session
        self._owns_session = False
        self._session_lock = asyncio.Lock()
        self._hass = None
        self._circuit_breaker = CircuitBreaker()
        self._rate_limiter = RateLimiter()
        self.access_token: str | None = "fake-token"
        self.force_refresh_token = AsyncMock(return_value=True)


@pytest.fixture(autouse=True)
def _skip_sleep() -> Any:
    """Skip asyncio.sleep so retries don't actually wait."""
    with patch("custom_components.fluidra_pool.fluidra_api._session.asyncio.sleep", new=AsyncMock()):
        yield


# --- _request — happy path -----------------------------------------------


async def test_request_returns_status_body_and_text_on_200() -> None:
    """The happy path: tuple (status, parsed_body, raw_text)."""
    session = _mock_session([_mock_response(status=200, text='{"ok": true}')])
    api = _FakeAPI(session=session)

    status, body, raw = await api._request("GET", "https://example.com")

    assert status == 200
    assert body == {"ok": True}
    assert raw == '{"ok": true}'


async def test_request_records_circuit_breaker_success_on_2xx() -> None:
    """A 2xx closes the half-open circuit breaker."""
    api = _FakeAPI(session=_mock_session([_mock_response(status=204, text="")]))
    api._circuit_breaker.record_success = MagicMock(wraps=api._circuit_breaker.record_success)
    await api._request("GET", "https://example.com")
    api._circuit_breaker.record_success.assert_called_once()


async def test_request_passes_headers_and_payload_through() -> None:
    """Headers and JSON payload are forwarded to session.request."""
    session = _mock_session([_mock_response()])
    api = _FakeAPI(session=session)
    await api._request(
        "POST",
        "https://example.com",
        headers={"X-Custom": "yes"},
        json_data={"key": "value"},
    )
    call_kwargs = session.request.call_args.kwargs
    assert call_kwargs["headers"]["X-Custom"] == "yes"
    assert call_kwargs["json"] == {"key": "value"}


# --- _request — retry on transient errors -------------------------------


async def test_request_retries_on_429_then_succeeds() -> None:
    """A 429 leads to a retry; the next 200 returns successfully."""
    session = _mock_session(
        [
            _mock_response(status=429, text="rate limited", retry_after="0"),
            _mock_response(status=200, text='{"ok": true}'),
        ]
    )
    api = _FakeAPI(session=session)

    status, body, _ = await api._request("GET", "https://example.com")
    assert status == 200
    assert body == {"ok": True}
    assert session.request.call_count == 2


async def test_request_retries_on_5xx_status() -> None:
    """5xx triggers retry, just like 429."""
    session = _mock_session(
        [
            _mock_response(status=503, text="service unavailable"),
            _mock_response(status=200, text="{}"),
        ]
    )
    api = _FakeAPI(session=session)

    status, _, _ = await api._request("GET", "https://example.com")
    assert status == 200


async def test_request_retries_on_network_error() -> None:
    """aiohttp.ClientError triggers retry."""
    session = _mock_session(
        [
            aiohttp.ClientError("network blip"),
            _mock_response(status=200, text="{}"),
        ]
    )
    api = _FakeAPI(session=session)

    status, _, _ = await api._request("GET", "https://example.com")
    assert status == 200


async def test_request_retries_on_timeout_error() -> None:
    """TimeoutError triggers retry — uses Python builtin, not aiohttp."""
    session = _mock_session(
        [
            TimeoutError("timeout"),
            _mock_response(status=200, text="{}"),
        ]
    )
    api = _FakeAPI(session=session)

    status, _, _ = await api._request("GET", "https://example.com")
    assert status == 200


# --- _request — exhausted retries ---------------------------------------


async def test_request_returns_last_http_error_when_retries_exhausted() -> None:
    """A persistent HTTP 5xx falls through and returns the last status code (no raise)."""
    # The retry-loop branch only fires when `attempt < MAX_RETRIES`, so the LAST
    # attempt is allowed to fall through to the regular `return` branch.
    responses = [_mock_response(status=500, text="boom") for _ in range(10)]
    api = _FakeAPI(session=_mock_session(responses))

    status, _, raw = await api._request("GET", "https://example.com")
    assert status == 500
    assert raw == "boom"


async def test_request_raises_connection_error_when_network_keeps_failing() -> None:
    """Persistent network errors (aiohttp.ClientError) propagate as FluidraConnectionError."""
    responses: list[Any] = [aiohttp.ClientError("blip") for _ in range(10)]
    api = _FakeAPI(session=_mock_session(responses))

    with pytest.raises(FluidraConnectionError):
        await api._request("GET", "https://example.com")


async def test_request_records_single_circuit_breaker_failure_on_network_exhaustion() -> None:
    """Issue #64 regression guard: only ONE record_failure() across all retries."""
    responses: list[Any] = [aiohttp.ClientError("blip") for _ in range(10)]
    api = _FakeAPI(session=_mock_session(responses))
    api._circuit_breaker.record_failure = MagicMock(wraps=api._circuit_breaker.record_failure)

    with pytest.raises(FluidraConnectionError):
        await api._request("GET", "https://example.com")

    assert api._circuit_breaker.record_failure.call_count == 1


async def test_request_skip_circuit_breaker_does_not_record_failure() -> None:
    """skip_circuit_breaker=True (auth endpoints) doesn't touch the breaker even on exhaustion."""
    responses: list[Any] = [aiohttp.ClientError("blip") for _ in range(10)]
    api = _FakeAPI(session=_mock_session(responses))
    api._circuit_breaker.record_failure = MagicMock(wraps=api._circuit_breaker.record_failure)

    with pytest.raises(FluidraConnectionError):
        await api._request("GET", "https://example.com", skip_circuit_breaker=True)

    api._circuit_breaker.record_failure.assert_not_called()


# --- _request — circuit breaker open -----------------------------------


async def test_request_raises_circuit_breaker_error_when_breaker_open() -> None:
    """An open breaker short-circuits before any session.request call."""
    api = _FakeAPI(session=_mock_session([]))
    # Trip the breaker.
    api._circuit_breaker.can_execute = MagicMock(return_value=False)

    with pytest.raises(FluidraCircuitBreakerError):
        await api._request("GET", "https://example.com")


async def test_request_skip_circuit_breaker_bypasses_open_breaker() -> None:
    """skip_circuit_breaker=True ignores an open breaker (used by token refresh)."""
    api = _FakeAPI(session=_mock_session([_mock_response(status=200, text="{}")]))
    api._circuit_breaker.can_execute = MagicMock(return_value=False)

    status, _, _ = await api._request("GET", "https://example.com", skip_circuit_breaker=True)
    assert status == 200


# --- _request — 401/403 + token refresh ---------------------------------


async def test_request_refreshes_token_on_401_then_retries() -> None:
    """A 401 triggers force_refresh_token then retries with the new Bearer."""
    session = _mock_session(
        [
            _mock_response(status=401, text="expired"),
            _mock_response(status=200, text='{"ok": true}'),
        ]
    )
    api = _FakeAPI(session=session)
    api.access_token = "refreshed-token"

    headers_in = {"Authorization": "Bearer stale-token"}
    status, _, _ = await api._request("GET", "https://example.com", headers=headers_in)

    assert status == 200
    api.force_refresh_token.assert_awaited_once()
    # The second request used the refreshed Bearer.
    second_call_headers = session.request.call_args_list[1].kwargs["headers"]
    assert second_call_headers["Authorization"] == "Bearer refreshed-token"


async def test_request_returns_401_response_when_refresh_fails() -> None:
    """If token refresh itself fails, propagate the original 401 response."""
    session = _mock_session([_mock_response(status=401, text="expired")])
    api = _FakeAPI(session=session)
    api.force_refresh_token = AsyncMock(return_value=False)

    status, _, raw = await api._request("GET", "https://example.com")
    assert status == 401
    assert raw == "expired"


async def test_request_skip_auth_refresh_does_not_trigger_refresh_on_401() -> None:
    """skip_auth_refresh=True (auth endpoints) returns 401 verbatim, no refresh attempt."""
    session = _mock_session([_mock_response(status=401, text="bad creds")])
    api = _FakeAPI(session=session)

    status, _, _ = await api._request("GET", "https://example.com", skip_auth_refresh=True)
    assert status == 401
    api.force_refresh_token.assert_not_awaited()


async def test_request_does_not_refresh_when_refresh_attempts_exhausted() -> None:
    """Only ONE refresh attempt per request — a second 401 returns the response."""
    session = _mock_session(
        [
            _mock_response(status=401, text="expired"),
            _mock_response(status=401, text="still bad"),
        ]
    )
    api = _FakeAPI(session=session)

    status, _, raw = await api._request("GET", "https://example.com")
    assert status == 401
    assert raw == "still bad"
    api.force_refresh_token.assert_awaited_once()


# --- _request — Retry-After header --------------------------------------


async def test_request_honours_retry_after_header_on_429() -> None:
    """The Retry-After header drives the sleep duration on 429."""
    session = _mock_session(
        [
            _mock_response(status=429, text="slow down", retry_after="3"),
            _mock_response(status=200, text="{}"),
        ]
    )
    api = _FakeAPI(session=session)

    with patch(
        "custom_components.fluidra_pool.fluidra_api._session.asyncio.sleep", new_callable=AsyncMock
    ) as mock_sleep:
        await api._request("GET", "https://example.com")

    # Find a sleep call with the Retry-After value (3.0).
    sleep_durations = [c.args[0] for c in mock_sleep.await_args_list]
    assert 3.0 in sleep_durations


# --- close ---------------------------------------------------------------


async def test_close_closes_only_when_we_own_the_session() -> None:
    """An owned session is closed; a shared one (HA-provided) is not."""
    owned_session = MagicMock()
    owned_session.close = AsyncMock()
    api = _FakeAPI(session=owned_session)
    api._owns_session = True

    await api.close()

    owned_session.close.assert_awaited_once()
    assert api._session is None
    assert api._owns_session is False


async def test_close_does_not_close_borrowed_session() -> None:
    """A session borrowed from HA must NOT be closed by us."""
    shared_session = MagicMock()
    shared_session.close = AsyncMock()
    api = _FakeAPI(session=shared_session)
    api._owns_session = False

    await api.close()

    shared_session.close.assert_not_awaited()


async def test_close_is_a_noop_when_no_session() -> None:
    """Calling close before any session was created is safe."""
    api = _FakeAPI(session=None)
    await api.close()  # No raise.


async def test_close_swallows_aiohttp_errors() -> None:
    """A close-time aiohttp error doesn't propagate (best-effort cleanup)."""
    owned_session = MagicMock()
    owned_session.close = AsyncMock(side_effect=aiohttp.ClientError("already closed"))
    api = _FakeAPI(session=owned_session)
    api._owns_session = True

    await api.close()  # No raise.
    assert api._session is None


# --- _get_session --------------------------------------------------------


async def test_get_session_returns_existing_session_without_creating_new_one() -> None:
    """If a session is already attached, _get_session reuses it (no aiohttp init)."""
    existing = MagicMock()
    api = _FakeAPI(session=existing)
    assert await api._get_session() is existing


async def test_get_session_creates_owned_session_without_hass() -> None:
    """Outside HA we own the session (must close it ourselves)."""
    api = _FakeAPI(session=None)
    with patch(
        "custom_components.fluidra_pool.fluidra_api._session.aiohttp.ClientSession",
        return_value=MagicMock(),
    ) as mock_session_cls:
        session = await api._get_session()

    mock_session_cls.assert_called_once()
    assert session is not None
    assert api._owns_session is True
