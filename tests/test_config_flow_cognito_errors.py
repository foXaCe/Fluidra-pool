"""Tests for the Cognito error-type → config-flow message mapping.

Every Cognito rejection used to surface as the single "check your email and
password" message, so a user whose account was unconfirmed, rate-limited or
simply absent from the EMEA pool had no way to know (Issue #201). These tests
pin the extraction of the Cognito ``__type``, the mapping table, the fallback
for unknown types, and the fact that the raw Cognito body never reaches a
displayed string.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest

from custom_components.fluidra_pool.api_resilience import FluidraAuthError, FluidraMFARequired
from custom_components.fluidra_pool.config_flow import (
    COGNITO_ERROR_KEYS,
    cognito_error_key,
)
from custom_components.fluidra_pool.const import DOMAIN
from custom_components.fluidra_pool.fluidra_api._auth import AuthMixin, cognito_error_type

_PATCH_TARGET = "custom_components.fluidra_pool.config_flow.FluidraPoolAPI"
_COMPONENT_DIR = Path(__file__).parent.parent / "custom_components" / "fluidra_pool"
_TRANSLATION_FILES = [
    _COMPONENT_DIR / "strings.json",
    *sorted((_COMPONENT_DIR / "translations").glob("*.json")),
]


class _FakeAPI(AuthMixin):
    """Stub exposing only what the Cognito auth path touches."""

    def __init__(self) -> None:
        self.email = "user@example.com"
        self.password = "pwd"
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self.token_expires_at: int | None = None
        self._last_token_store: float = 0.0
        self._on_token_persist = None
        self._token_lock = asyncio.Lock()
        self._request = AsyncMock()


def _cognito_failure(error_type: str, message: str = "Some Cognito detail") -> tuple[int, dict, str]:
    """Build a realistic Cognito error triple as ``_request`` returns it."""
    body = {"__type": error_type, "message": message}
    return 400, body, json.dumps(body)


# --- cognito_error_type --------------------------------------------------


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"__type": "NotAuthorizedException"}, "NotAuthorizedException"),
        # Cognito sometimes qualifies the type with its service namespace.
        ({"__type": "com.amazonaws.cognito.idp#UserNotFoundException"}, "UserNotFoundException"),
        ({"__type": "  UserNotConfirmedException  "}, "UserNotConfirmedException"),
        ({"__type": ""}, None),
        ({"__type": "#"}, None),
        ({"__type": 42}, None),
        ({"message": "no type at all"}, None),
        ({}, None),
    ],
)
def test_cognito_error_type_extraction(payload: dict, expected: str | None) -> None:
    """The type name is extracted, unqualified, or None when unusable."""
    assert cognito_error_type(payload, json.dumps(payload)) == expected


def test_cognito_error_type_falls_back_to_raw_text() -> None:
    """When the parsed body isn't a dict, the raw text is parsed instead."""
    assert cognito_error_type(None, '{"__type": "LimitExceededException"}') == "LimitExceededException"


@pytest.mark.parametrize("raw_text", ["", "not json at all", "[1, 2, 3]", '"a string"'])
def test_cognito_error_type_returns_none_on_unparseable_body(raw_text: str) -> None:
    """A body that isn't a JSON object never raises — it just yields None."""
    assert cognito_error_type(None, raw_text) is None


# --- cognito_error_key ---------------------------------------------------


@pytest.mark.parametrize(
    ("error_code", "expected"),
    [
        ("UserNotFoundException", "account_not_found"),
        ("UserNotConfirmedException", "account_not_confirmed"),
        ("PasswordResetRequiredException", "password_reset_required"),
        ("TooManyRequestsException", "too_many_attempts"),
        ("TooManyFailedAttemptsException", "too_many_attempts"),
        ("LimitExceededException", "too_many_attempts"),
        # NotAuthorizedException really does mean "check your credentials":
        # Cognito returns it for a wrong password and for an account that isn't
        # in this user pool at all.
        ("NotAuthorizedException", "invalid_auth"),
        # Unknown / absent types degrade to today's generic message.
        ("SomeFutureCognitoException", "invalid_auth"),
        ("", "invalid_auth"),
        (None, "invalid_auth"),
    ],
)
def test_cognito_error_key_mapping(error_code: str | None, expected: str) -> None:
    """Each known type maps to its own message key; anything else falls back."""
    assert cognito_error_key(error_code) == expected


@pytest.mark.parametrize("translation_file", _TRANSLATION_FILES, ids=lambda p: p.name)
def test_every_mapped_key_is_translated(translation_file: Path) -> None:
    """A mapped key with no translation would render as a raw key in the UI."""
    errors = json.loads(translation_file.read_text(encoding="utf-8"))["config"]["error"]
    for key in {*COGNITO_ERROR_KEYS.values(), "invalid_auth"}:
        assert errors.get(key), f"{key} missing or empty in {translation_file.name}"


def test_french_strings_are_actually_translated() -> None:
    """French is mandatory here — and a copy of the English string isn't one."""
    fr = json.loads((_COMPONENT_DIR / "translations" / "fr.json").read_text(encoding="utf-8"))["config"]["error"]
    en = json.loads((_COMPONENT_DIR / "translations" / "en.json").read_text(encoding="utf-8"))["config"]["error"]
    for key in COGNITO_ERROR_KEYS.values():
        assert fr[key] != en[key], f"{key} is still the English string in fr.json"


# --- the API layer attaches the type -------------------------------------


async def test_initial_auth_attaches_cognito_error_code() -> None:
    """A Cognito rejection carries its ``__type`` on the raised exception."""
    api = _FakeAPI()
    api._request.return_value = _cognito_failure("PasswordResetRequiredException")

    with pytest.raises(FluidraAuthError) as excinfo:
        await api._cognito_initial_auth()

    assert excinfo.value.error_code == "PasswordResetRequiredException"


async def test_initial_auth_error_message_never_leaks_the_cognito_body() -> None:
    """The displayed/raised message carries the status only, never the body."""
    api = _FakeAPI()
    api._request.return_value = _cognito_failure("NotAuthorizedException", "Incorrect username or password.")

    with pytest.raises(FluidraAuthError) as excinfo:
        await api._cognito_initial_auth()

    assert "Incorrect username or password" not in str(excinfo.value)
    assert "__type" not in str(excinfo.value)
    assert str(excinfo.value) == "Cognito auth failed with status 400"


async def test_initial_auth_error_code_is_none_without_a_cognito_body() -> None:
    """An empty/garbled body leaves error_code None, so the flow stays generic."""
    api = _FakeAPI()
    api._request.return_value = (503, None, "<html>gateway error</html>")

    with pytest.raises(FluidraAuthError) as excinfo:
        await api._cognito_initial_auth()

    assert excinfo.value.error_code is None
    assert cognito_error_key(excinfo.value.error_code) == "invalid_auth"


async def test_mfa_failure_attaches_cognito_error_code() -> None:
    """The MFA step reports its Cognito type the same way the login step does."""
    api = _FakeAPI()
    api._request.return_value = _cognito_failure("TooManyRequestsException")

    with pytest.raises(FluidraAuthError) as excinfo:
        await api._cognito_respond_to_mfa("123456", "session-token")

    assert excinfo.value.error_code == "TooManyRequestsException"


# --- end-to-end through the config flow ----------------------------------


@pytest.mark.parametrize(
    ("error_code", "expected_key"),
    [
        ("UserNotFoundException", "account_not_found"),
        ("UserNotConfirmedException", "account_not_confirmed"),
        ("PasswordResetRequiredException", "password_reset_required"),
        ("TooManyRequestsException", "too_many_attempts"),
        ("NotAuthorizedException", "invalid_auth"),
        ("SomeFutureCognitoException", "invalid_auth"),
        (None, "invalid_auth"),
    ],
)
async def test_user_flow_surfaces_the_matching_error(
    hass: HomeAssistant,
    mock_api: AsyncMock,
    error_code: str | None,
    expected_key: str,
) -> None:
    """The form shows the message matching the Cognito type, not a catch-all."""
    mock_api.initial_auth.side_effect = FluidraAuthError("Cognito auth failed with status 400", error_code)

    with patch(_PATCH_TARGET, return_value=mock_api):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data={CONF_EMAIL: "test@example.com", CONF_PASSWORD: "whatever"},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected_key}


@pytest.mark.parametrize(
    ("error_code", "expected_key"),
    [
        ("TooManyRequestsException", "too_many_attempts"),
        ("CodeMismatchException", "invalid_mfa_code"),
        ("ExpiredCodeException", "invalid_mfa_code"),
        (None, "invalid_mfa_code"),
    ],
)
async def test_mfa_step_distinguishes_rate_limiting_from_a_bad_code(
    hass: HomeAssistant,
    mock_api: AsyncMock,
    error_code: str | None,
    expected_key: str,
) -> None:
    """A rate-limited MFA attempt must not be reported as a wrong code."""
    mock_api.initial_auth.side_effect = FluidraMFARequired("SOFTWARE_TOKEN_MFA", "session-token")
    mock_api.respond_to_mfa.side_effect = FluidraAuthError("MFA verification failed with status 400", error_code)

    with patch(_PATCH_TARGET, return_value=mock_api):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data={CONF_EMAIL: "test@example.com", CONF_PASSWORD: "whatever"},
        )
        assert result["step_id"] == "mfa"
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"mfa_code": "000000"})

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected_key}
