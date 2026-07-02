"""Tests for fluidra_api/_auth.py — Cognito auth, MFA, refresh, token lifecycle."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.fluidra_pool.api_resilience import (
    FluidraAuthError,
    FluidraConnectionError,
    FluidraMFARequired,
)
from custom_components.fluidra_pool.fluidra_api._auth import AuthMixin
from custom_components.fluidra_pool.fluidra_api._constants import FLUIDRA_USER_AGENT


class _FakeAPI(AuthMixin):
    """Stub: only what AuthMixin touches at runtime."""

    def __init__(
        self,
        *,
        password: str | None = "pwd",
        refresh_token: str | None = None,
        on_token_persist=None,
    ) -> None:
        self.email = "user@example.com"
        self.password = password
        self.refresh_token = refresh_token
        self.access_token: str | None = None
        self.token_expires_at: int | None = None
        self._last_token_store: float = 0.0
        self._on_token_persist = on_token_persist
        self._token_lock = asyncio.Lock()

        # Stubbed by individual tests.
        self._request = AsyncMock()
        self.async_update_data = AsyncMock()


def _ok_auth_result(*, with_refresh: bool = True) -> dict:
    result = {
        "AccessToken": "acc-1",
        "IdToken": "id-1",
        "ExpiresIn": 3600,
    }
    if with_refresh:
        result["RefreshToken"] = "ref-1"
    return result


# --- _store_tokens -------------------------------------------------------


def test_store_tokens_sets_access_id_refresh_and_expiry() -> None:
    """A complete AuthenticationResult lands in the right fields."""
    api = _FakeAPI()
    api._store_tokens(_ok_auth_result())
    assert api.access_token == "acc-1"
    assert api.refresh_token == "ref-1"
    assert api.token_expires_at is not None
    # Expiry should be a future timestamp with margin already subtracted.
    assert api.token_expires_at <= int(time.time()) + 3600


def test_store_tokens_keeps_existing_refresh_when_response_omits_it() -> None:
    """Cognito doesn't always reissue a refresh token on refresh — keep the old one."""
    api = _FakeAPI(refresh_token="old-refresh")
    api._store_tokens(_ok_auth_result(with_refresh=False))
    assert api.refresh_token == "old-refresh"


def test_store_tokens_raises_when_access_token_missing() -> None:
    """A malformed AuthenticationResult without AccessToken is a hard failure."""
    api = _FakeAPI()
    with pytest.raises(FluidraAuthError):
        api._store_tokens({"IdToken": "id-only", "ExpiresIn": 3600})


def test_store_tokens_fires_persist_callback_with_refresh_token() -> None:
    """If a callback is set, we notify it with the (new) refresh token."""
    persist = MagicMock()
    api = _FakeAPI(on_token_persist=persist)
    api._store_tokens(_ok_auth_result())
    persist.assert_called_once_with("ref-1")


# --- _build_auth_headers + is_token_expired ------------------------------


def test_build_auth_headers_uses_access_token() -> None:
    """Bearer header carries the current access token."""
    api = _FakeAPI()
    api.access_token = "abc"
    headers = api._build_auth_headers()
    assert headers["Authorization"] == "Bearer abc"
    assert headers["Content-Type"] == "application/json"


def test_is_token_expired_returns_true_when_no_expiry_set() -> None:
    """Without an expiry timestamp we conservatively report expired."""
    api = _FakeAPI()
    assert api.is_token_expired() is True


def test_is_token_expired_returns_false_for_future_expiry() -> None:
    """Future expiry → still valid."""
    api = _FakeAPI()
    api.token_expires_at = int(time.time()) + 3600
    assert api.is_token_expired() is False


def test_is_token_expired_returns_true_for_past_expiry() -> None:
    """Past expiry → expired."""
    api = _FakeAPI()
    api.token_expires_at = int(time.time()) - 1
    assert api.is_token_expired() is True


# --- refresh_access_token -----------------------------------------------


async def test_refresh_access_token_returns_false_without_refresh_token() -> None:
    """No refresh token means we can't refresh — short-circuit to False."""
    api = _FakeAPI()
    assert await api.refresh_access_token() is False
    api._request.assert_not_called()


async def test_refresh_access_token_returns_true_on_200_and_stores_tokens() -> None:
    """A successful refresh updates access_token + token_expires_at."""
    api = _FakeAPI(refresh_token="ref-old")
    api._request.return_value = (200, {"AuthenticationResult": _ok_auth_result()}, "{}")

    assert await api.refresh_access_token() is True
    assert api.access_token == "acc-1"


async def test_refresh_access_token_returns_false_on_non_200() -> None:
    """A 4xx/5xx during refresh is recoverable (we'll fall back to full auth)."""
    api = _FakeAPI(refresh_token="ref-old")
    api._request.return_value = (400, None, "")
    assert await api.refresh_access_token() is False


async def test_refresh_access_token_returns_false_on_connection_error() -> None:
    """Connection errors during refresh degrade to False."""
    api = _FakeAPI(refresh_token="ref-old")
    api._request.side_effect = FluidraConnectionError("network")
    assert await api.refresh_access_token() is False


async def test_refresh_access_token_returns_false_when_response_missing_access_token() -> None:
    """A 200 with an incomplete AuthenticationResult is a fail-safe False."""
    api = _FakeAPI(refresh_token="ref-old")
    api._request.return_value = (200, {"AuthenticationResult": {"IdToken": "id-only"}}, "{}")
    assert await api.refresh_access_token() is False


# --- _cognito_initial_auth -----------------------------------------------


async def test_cognito_initial_auth_succeeds_with_password() -> None:
    """USER_PASSWORD_AUTH yields tokens when Cognito returns AuthenticationResult."""
    api = _FakeAPI()
    api._request.return_value = (200, {"AuthenticationResult": _ok_auth_result()}, "{}")
    await api._cognito_initial_auth()
    assert api.access_token == "acc-1"


async def test_cognito_initial_auth_raises_when_password_missing() -> None:
    """Without a stored password we can't perform initial auth."""
    api = _FakeAPI(password=None)
    with pytest.raises(FluidraAuthError):
        await api._cognito_initial_auth()


async def test_cognito_initial_auth_raises_on_software_token_mfa_challenge() -> None:
    """SOFTWARE_TOKEN_MFA challenge → caller must handle via reauth flow."""
    api = _FakeAPI()
    api._request.return_value = (
        200,
        {"ChallengeName": "SOFTWARE_TOKEN_MFA", "Session": "abc"},
        "{}",
    )
    with pytest.raises(FluidraMFARequired) as exc_info:
        await api._cognito_initial_auth()
    assert exc_info.value.challenge_name == "SOFTWARE_TOKEN_MFA"
    assert exc_info.value.session == "abc"


async def test_cognito_initial_auth_raises_on_sms_mfa_challenge() -> None:
    """SMS_MFA challenge is handled the same way."""
    api = _FakeAPI()
    api._request.return_value = (
        200,
        {"ChallengeName": "SMS_MFA", "Session": "abc"},
        "{}",
    )
    with pytest.raises(FluidraMFARequired):
        await api._cognito_initial_auth()


async def test_cognito_initial_auth_raises_on_unexpected_challenge() -> None:
    """An unknown ChallengeName surfaces as a generic FluidraAuthError."""
    api = _FakeAPI()
    api._request.return_value = (
        200,
        {"ChallengeName": "NEW_PASSWORD_REQUIRED", "Session": "abc"},
        "{}",
    )
    with pytest.raises(FluidraAuthError):
        await api._cognito_initial_auth()


async def test_cognito_initial_auth_raises_on_non_200_status() -> None:
    """A non-200 from Cognito is a hard auth failure."""
    api = _FakeAPI()
    api._request.return_value = (400, None, "bad creds")
    with pytest.raises(FluidraAuthError):
        await api._cognito_initial_auth()


# --- _cognito_respond_to_mfa ---------------------------------------------


async def test_cognito_respond_to_mfa_succeeds_with_valid_code() -> None:
    """The MFA response yields tokens like initial auth."""
    api = _FakeAPI()
    api._request.return_value = (200, {"AuthenticationResult": _ok_auth_result()}, "{}")
    await api._cognito_respond_to_mfa("123456", "session-abc")
    assert api.access_token == "acc-1"


async def test_cognito_respond_to_mfa_raises_on_non_200() -> None:
    """A failed MFA verification raises FluidraAuthError."""
    api = _FakeAPI()
    api._request.return_value = (400, None, "wrong code")
    with pytest.raises(FluidraAuthError):
        await api._cognito_respond_to_mfa("000000", "session-abc")


async def test_cognito_respond_to_mfa_raises_when_no_access_token_in_response() -> None:
    """A 200 with empty AuthenticationResult is treated as auth failure."""
    api = _FakeAPI()
    api._request.return_value = (200, {"AuthenticationResult": {}}, "{}")
    with pytest.raises(FluidraAuthError):
        await api._cognito_respond_to_mfa("123456", "session-abc")


# --- _get_user_profile --------------------------------------------------


async def test_get_user_profile_returns_data_on_200() -> None:
    """Successful profile fetch returns the dict body."""
    api = _FakeAPI()
    api.access_token = "tok"
    api._request.return_value = (200, {"id": "user-1", "email": "x@y.com"}, "{}")
    profile = await api._get_user_profile()
    assert profile == {"id": "user-1", "email": "x@y.com"}


async def test_get_user_profile_returns_empty_on_non_200() -> None:
    """A 404/500 doesn't bubble — we degrade gracefully."""
    api = _FakeAPI()
    api.access_token = "tok"
    api._request.return_value = (500, None, "")
    assert await api._get_user_profile() == {}


async def test_get_user_profile_returns_empty_on_connection_error() -> None:
    """Profile is optional — connection errors degrade to empty dict."""
    api = _FakeAPI()
    api.access_token = "tok"
    api._request.side_effect = FluidraConnectionError("network")
    assert await api._get_user_profile() == {}


# --- authenticate (high-level orchestration) -----------------------------


async def test_authenticate_uses_refresh_token_first_when_available() -> None:
    """When a refresh token exists, try it before falling back to MFA-able auth."""
    api = _FakeAPI(refresh_token="ref-1")
    # Refresh succeeds → no initial auth call.
    api._request.side_effect = [
        (200, {"AuthenticationResult": _ok_auth_result()}, "{}"),  # refresh.
        (200, {"id": "user-1"}, "{}"),  # _get_user_profile.
    ]

    await api.authenticate()

    assert api.access_token == "acc-1"
    api.async_update_data.assert_awaited_once()


async def test_authenticate_falls_back_to_initial_auth_when_refresh_fails() -> None:
    """If the stored refresh token is invalid, we go through full auth."""
    api = _FakeAPI(refresh_token="ref-bad")
    api._request.side_effect = [
        (400, None, "invalid"),  # refresh fails.
        (200, {"AuthenticationResult": _ok_auth_result()}, "{}"),  # initial auth.
        (200, {"id": "user-1"}, "{}"),  # profile.
    ]

    await api.authenticate()

    assert api.access_token == "acc-1"


async def test_authenticate_propagates_mfa_required() -> None:
    """If initial auth triggers MFA, the exception bubbles up to the caller."""
    api = _FakeAPI()  # No refresh token.
    api._request.return_value = (200, {"ChallengeName": "SOFTWARE_TOKEN_MFA", "Session": "s"}, "{}")
    with pytest.raises(FluidraMFARequired):
        await api.authenticate()


async def test_authenticate_wraps_unexpected_exceptions_in_fluidra_auth_error() -> None:
    """Non-Fluidra exceptions get re-raised as FluidraAuthError."""
    api = _FakeAPI()
    # No refresh token, initial auth blows up with a KeyError (e.g. malformed JSON).
    api._request.return_value = (200, {"AuthenticationResult": {"BadKey": "no access"}}, "{}")
    with pytest.raises(FluidraAuthError):
        await api.authenticate()


# --- ensure_valid_token --------------------------------------------------


async def test_ensure_valid_token_returns_true_when_not_expired() -> None:
    """Fresh tokens don't trigger a refresh."""
    api = _FakeAPI()
    api.token_expires_at = int(time.time()) + 3600
    assert await api.ensure_valid_token() is True
    api._request.assert_not_called()


async def test_ensure_valid_token_refreshes_when_expired() -> None:
    """Expired token + valid refresh → refreshed silently."""
    api = _FakeAPI(refresh_token="ref-1")
    api.token_expires_at = int(time.time()) - 1
    api._request.return_value = (200, {"AuthenticationResult": _ok_auth_result()}, "{}")

    assert await api.ensure_valid_token() is True


async def test_ensure_valid_token_falls_back_to_initial_auth_when_refresh_fails() -> None:
    """If refresh fails but credentials are stored, attempt initial auth."""
    api = _FakeAPI(refresh_token="ref-bad")
    api.token_expires_at = int(time.time()) - 1
    api._request.side_effect = [
        (400, None, ""),  # refresh fails.
        (200, {"AuthenticationResult": _ok_auth_result()}, "{}"),  # initial auth.
    ]
    assert await api.ensure_valid_token() is True


async def test_ensure_valid_token_returns_false_when_no_password_for_reauth() -> None:
    """No stored password means we can't perform initial auth — caller must reauth."""
    api = _FakeAPI(password=None, refresh_token="ref-bad")
    api.token_expires_at = int(time.time()) - 1
    api._request.return_value = (400, None, "")  # refresh fails.
    assert await api.ensure_valid_token() is False


async def test_ensure_valid_token_returns_false_when_initial_auth_requires_mfa() -> None:
    """MFA during forced reauth → caller must handle via reauth flow."""
    api = _FakeAPI(refresh_token="ref-bad")
    api.token_expires_at = int(time.time()) - 1
    api._request.side_effect = [
        (400, None, ""),  # refresh fails.
        (200, {"ChallengeName": "SOFTWARE_TOKEN_MFA", "Session": "s"}, "{}"),
    ]
    assert await api.ensure_valid_token() is False


# --- force_refresh_token -------------------------------------------------


async def test_force_refresh_token_returns_true_on_successful_refresh() -> None:
    """Force-refresh path follows the same successful-refresh logic."""
    api = _FakeAPI(refresh_token="ref-1")
    api._request.return_value = (200, {"AuthenticationResult": _ok_auth_result()}, "{}")
    assert await api.force_refresh_token() is True


async def test_force_refresh_token_falls_back_to_initial_auth_on_refresh_failure() -> None:
    """If refresh fails, force_refresh_token tries initial auth like ensure_valid_token."""
    api = _FakeAPI(refresh_token="ref-bad")
    api._request.side_effect = [
        (400, None, ""),  # refresh fails.
        (200, {"AuthenticationResult": _ok_auth_result()}, "{}"),  # initial auth.
    ]
    assert await api.force_refresh_token() is True


# --- Phase-4 hardening ----------------------------------------------------


async def test_refresh_access_token_sends_user_agent() -> None:
    """The refresh call must carry the app User-Agent like every Cognito call."""
    api = _FakeAPI(refresh_token="ref-0")
    api._request.return_value = (200, {"AuthenticationResult": _ok_auth_result()}, "{}")

    assert await api.refresh_access_token() is True

    _, kwargs = api._request.await_args
    assert kwargs["headers"]["User-Agent"] == FLUIDRA_USER_AGENT


async def test_force_refresh_token_skips_when_another_task_already_refreshed() -> None:
    """Double-checked locking: a token stored while waiting on the lock is reused."""
    api = _FakeAPI(refresh_token="ref-0")
    api.access_token = "fresh-token"
    # Simulate a concurrent waiter that stored a token after we entered.
    api._last_token_store = time.monotonic() + 60

    assert await api.force_refresh_token() is True
    api._request.assert_not_awaited()
