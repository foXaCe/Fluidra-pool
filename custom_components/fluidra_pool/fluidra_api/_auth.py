"""AWS Cognito authentication flow + token lifecycle management."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import aiohttp

from ..api_resilience import (
    FluidraAuthError,
    FluidraCircuitBreakerError,
    FluidraConnectionError,
    FluidraError,
    FluidraMFARequired,
)
from ..utils import mask_email
from ._base import FluidraAPIBase
from ._constants import (
    COGNITO_CLIENT_ID,
    COGNITO_ENDPOINT,
    CONSUMER_PROFILE_ENDPOINT,
    FLUIDRA_USER_AGENT,
)

_LOGGER = logging.getLogger(__name__)


class AuthMixin(FluidraAPIBase):
    """Cognito sign-in, MFA, refresh-token rotation, and standard auth headers.

    Every Cognito call passes ``skip_circuit_breaker=True`` on purpose: the
    circuit breaker guards the Fluidra EMEA data plane, and Cognito is a
    different service on a different host — sharing the breaker would let an
    EMEA outage block re-authentication (and vice versa).
    """

    async def authenticate(self) -> None:
        """Authenticate via AWS Cognito.

        Tries the stored refresh token first to avoid MFA on every HA restart.
        Falls back to full credentials auth only when needed.
        """
        try:
            if self.refresh_token:
                if await self.refresh_access_token():
                    _LOGGER.info("Authenticated via stored refresh token (no MFA required)")
                    await self._get_user_profile()
                    await self.async_update_data()
                    return
                _LOGGER.warning("Stored refresh token expired or invalid, falling back to full auth")

            await self._cognito_initial_auth()
            await self._get_user_profile()
            await self.async_update_data()

        except FluidraError:
            raise
        except (aiohttp.ClientError, TimeoutError, json.JSONDecodeError, KeyError) as err:
            raise FluidraAuthError(f"Authentication failed: {type(err).__name__}") from err

    async def _cognito_initial_auth(self) -> None:
        """Perform initial AWS Cognito authentication."""
        if not self.password:
            raise FluidraAuthError("Password required for initial authentication")

        auth_payload = {
            "AuthFlow": "USER_PASSWORD_AUTH",
            "ClientId": COGNITO_CLIENT_ID,
            "AuthParameters": {"USERNAME": self.email, "PASSWORD": self.password},
        }

        headers = {
            "Content-Type": "application/x-amz-json-1.1; charset=utf-8",
            "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
            "User-Agent": FLUIDRA_USER_AGENT,
        }

        status, data, raw_text = await self._request(
            "POST",
            COGNITO_ENDPOINT,
            headers=headers,
            json_data=auth_payload,
            skip_circuit_breaker=True,
            skip_auth_refresh=True,
        )

        if status != 200 or data is None:
            _LOGGER.debug("Cognito auth failed body: %s", raw_text[:500])
            raise FluidraAuthError(f"Cognito auth failed with status {status}")

        auth_result = data.get("AuthenticationResult")
        if not auth_result:
            challenge_name = data.get("ChallengeName", "")
            session_token = data.get("Session", "")
            if challenge_name in ("SOFTWARE_TOKEN_MFA", "SMS_MFA"):
                raise FluidraMFARequired(challenge_name, session_token)
            raise FluidraAuthError(f"Unexpected Cognito challenge: {challenge_name or 'none'}")

        self._store_tokens(auth_result)

    async def _cognito_respond_to_mfa(
        self, code: str, session: str, challenge_name: str = "SOFTWARE_TOKEN_MFA"
    ) -> None:
        """Complete a Cognito MFA challenge with a TOTP or SMS code."""
        payload = {
            "ChallengeName": challenge_name,
            "ClientId": COGNITO_CLIENT_ID,
            "Session": session,
            "ChallengeResponses": {
                "USERNAME": self.email,
                f"{challenge_name}_CODE": code,
            },
        }

        headers = {
            "Content-Type": "application/x-amz-json-1.1; charset=utf-8",
            "X-Amz-Target": "AWSCognitoIdentityProviderService.RespondToAuthChallenge",
            "User-Agent": FLUIDRA_USER_AGENT,
        }

        status, data, raw_text = await self._request(
            "POST",
            COGNITO_ENDPOINT,
            headers=headers,
            json_data=payload,
            skip_circuit_breaker=True,
            skip_auth_refresh=True,
        )

        if status != 200 or data is None:
            _LOGGER.debug("MFA verification failed body: %s", raw_text[:500])
            raise FluidraAuthError(f"MFA verification failed with status {status}")

        auth_result = data.get("AuthenticationResult", {})
        self._store_tokens(auth_result)
        if not self.access_token:
            raise FluidraAuthError("Access token not received after MFA")

    async def initial_auth(self) -> None:
        """Run the raw Cognito credentials auth (public config-flow entry).

        Unlike :meth:`authenticate`, this performs no profile fetch or device
        discovery — the config flow only needs to validate credentials. Raises
        :class:`FluidraMFARequired` when the account has MFA enabled.
        """
        await self._cognito_initial_auth()

    async def respond_to_mfa(self, code: str, session: str, challenge_name: str = "SOFTWARE_TOKEN_MFA") -> None:
        """Complete a Cognito MFA challenge (public config-flow entry)."""
        await self._cognito_respond_to_mfa(code, session, challenge_name)

    def _store_tokens(self, auth_result: dict[str, Any]) -> None:
        """Persist freshly-minted tokens and notify the entry callback."""
        self.access_token = auth_result.get("AccessToken")
        new_refresh = auth_result.get("RefreshToken")
        if new_refresh:
            self.refresh_token = new_refresh

        expires_in = auth_result.get("ExpiresIn", 3600)
        margin = min(300, max(30, expires_in // 10))
        self.token_expires_at = int(time.time()) + expires_in - margin

        if not self.access_token:
            raise FluidraAuthError("Access token not received")
        self._last_token_store = time.monotonic()

        if self.refresh_token and self._on_token_persist:
            self._on_token_persist(self.refresh_token)

    async def _get_user_profile(self) -> dict[str, Any]:
        """Fetch user profile.

        Mirrors the official mobile app's login sequence (Cognito auth →
        consumers/me → discovery) so our traffic stays indistinguishable from
        the app's. We also keep the consumer id, which lets the coordinator tell
        whether the account owns a pool or only has shared/viewer access.
        Failures are ignored on purpose — the profile is not required to operate.
        """
        headers = self._build_auth_headers()

        try:
            status, data, _ = await self._request("GET", CONSUMER_PROFILE_ENDPOINT, headers=headers)
            if status == 200 and isinstance(data, dict):
                consumer_id = data.get("id")
                if isinstance(consumer_id, str):
                    self.user_id = consumer_id
                return data
        except FluidraError:
            _LOGGER.debug("Failed to get user profile, continuing anyway")
        return {}

    def _build_auth_headers(self) -> dict[str, str]:
        """Build standard authenticated headers."""
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": FLUIDRA_USER_AGENT,
        }

    def is_token_expired(self) -> bool:
        """Return True if the token is near or past its expiration margin."""
        if not self.token_expires_at:
            return True
        return int(time.time()) >= self.token_expires_at

    async def ensure_valid_token(self) -> bool:
        """Ensure the access token is valid, refreshing if needed.

        Returns True if token is valid/renewed, False if credentials are definitively
        invalid (MFA required). Raises for transient network errors.
        """
        if not self.is_token_expired():
            return True

        async with self._token_lock:
            if not self.is_token_expired():
                return True

            if await self.refresh_access_token():
                return True

            _LOGGER.warning(
                "Token refresh failed, attempting full re-authentication (email=%s)",
                mask_email(self.email),
            )
            if not self.password:
                _LOGGER.warning("No password stored, cannot re-authenticate; reauth flow required")
                return False
            try:
                await self._cognito_initial_auth()
                _LOGGER.info("Full re-authentication successful")
                return True
            except FluidraMFARequired:
                _LOGGER.warning("MFA required during token re-authentication, triggering reauth flow")
                return False
            except (FluidraConnectionError, FluidraCircuitBreakerError):
                raise

    async def force_refresh_token(self) -> bool:
        """Refresh credentials after the API rejects the current access token."""
        entered_at = time.monotonic()
        async with self._token_lock:
            # Double-checked locking: when several parallel requests hit a 401
            # at once, only the first waiter actually refreshes — the others
            # see a token stored after they entered and reuse it.
            if self._last_token_store > entered_at and self.access_token:
                return True
            if await self.refresh_access_token():
                return True

            _LOGGER.warning(
                "Forced token refresh failed, attempting full re-authentication (email=%s)",
                mask_email(self.email),
            )
            if not self.password:
                _LOGGER.warning("No password stored, cannot re-authenticate; reauth flow required")
                return False
            try:
                await self._cognito_initial_auth()
                _LOGGER.info("Full re-authentication successful after rejected token")
                return True
            except FluidraMFARequired:
                _LOGGER.warning("MFA required during forced token refresh, triggering reauth flow")
                return False
            except (FluidraConnectionError, FluidraCircuitBreakerError):
                raise

    async def refresh_access_token(self) -> bool:
        """Renew the access token with the stored refresh token."""
        if not self.refresh_token:
            _LOGGER.debug("No refresh token available, cannot refresh")
            return False

        refresh_payload = {
            "AuthFlow": "REFRESH_TOKEN_AUTH",
            "ClientId": COGNITO_CLIENT_ID,
            "AuthParameters": {"REFRESH_TOKEN": self.refresh_token},
        }

        headers = {
            "Content-Type": "application/x-amz-json-1.1; charset=utf-8",
            "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
            "User-Agent": FLUIDRA_USER_AGENT,
        }

        try:
            status, data, raw_text = await self._request(
                "POST",
                COGNITO_ENDPOINT,
                headers=headers,
                json_data=refresh_payload,
                skip_circuit_breaker=True,
                skip_auth_refresh=True,
            )
        except FluidraError as err:
            _LOGGER.warning("Token refresh failed (connection error): %s", err)
            return False

        if status == 200 and isinstance(data, dict):
            auth_result = data.get("AuthenticationResult", {})
            try:
                self._store_tokens(auth_result)
            except FluidraAuthError:
                return False
            return True

        _LOGGER.warning("Token refresh failed with status %d", status)
        _LOGGER.debug("Refresh body: %s", raw_text[:200])
        return False
