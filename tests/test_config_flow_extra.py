"""Extra tests for the Fluidra Pool config flow.

Covers the OptionsFlow and the reauth / reauth_confirm / reconfigure / mfa
steps. The user step is already covered by tests/test_config_flow.py and is
intentionally NOT duplicated here.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_RECONFIGURE
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fluidra_pool.api_resilience import (
    FluidraAuthError,
    FluidraConnectionError,
    FluidraError,
    FluidraMFARequired,
)
from custom_components.fluidra_pool.const import (
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

EMAIL = "user@example.com"
PASSWORD = "secret"

_PATCH_TARGET = "custom_components.fluidra_pool.config_flow.FluidraPoolAPI"


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    """Create a config entry already added to hass."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD},
        unique_id=EMAIL.lower(),
        title=f"Fluidra Pool ({EMAIL})",
    )
    entry.add_to_hass(hass)
    return entry


# --------------------------------------------------------------------------- #
# Options flow
# --------------------------------------------------------------------------- #


async def test_options_flow_shows_form(hass: HomeAssistant) -> None:
    """Options flow init shows a form pre-filled with the default scan interval."""
    entry = _entry(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"


async def test_options_flow_create_entry(hass: HomeAssistant) -> None:
    """Submitting the options form stores the scan interval."""
    entry = _entry(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={CONF_SCAN_INTERVAL: 120},
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_SCAN_INTERVAL: 120}
    assert entry.options[CONF_SCAN_INTERVAL] == 120


async def test_options_flow_default_uses_existing_option(hass: HomeAssistant) -> None:
    """The form default reflects an already-stored option, not the constant default."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD},
        options={CONF_SCAN_INTERVAL: 300},
        unique_id=EMAIL.lower(),
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM

    # The schema default should be the stored option (300), not DEFAULT_SCAN_INTERVAL.
    assert DEFAULT_SCAN_INTERVAL != 300  # guard: keeps the assertion meaningful
    schema = result["data_schema"].schema
    scan_key = next(k for k in schema if k == CONF_SCAN_INTERVAL)
    assert scan_key.default() == 300


async def test_options_flow_rejects_out_of_range(hass: HomeAssistant) -> None:
    """Values outside the [30, 1800] range are rejected by the schema."""
    import pytest
    import voluptuous as vol

    entry = _entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)

    with pytest.raises(vol.Invalid):
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={CONF_SCAN_INTERVAL: 10},
        )


# --------------------------------------------------------------------------- #
# Reauth flow
# --------------------------------------------------------------------------- #


async def _start_reauth(hass: HomeAssistant, entry: MockConfigEntry) -> dict:
    """Start a reauth flow and return the confirmation form result."""
    return await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": SOURCE_REAUTH,
            "entry_id": entry.entry_id,
            "unique_id": entry.unique_id,
        },
        data=entry.data,
    )


async def test_reauth_shows_confirm_form(hass: HomeAssistant) -> None:
    """Reauth flow lands on the reauth_confirm form with email pre-filled."""
    entry = _entry(hass)
    result = await _start_reauth(hass, entry)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["description_placeholders"]["email"] == EMAIL


async def test_reauth_confirm_success(hass: HomeAssistant, mock_api: AsyncMock) -> None:
    """A successful reauth updates the entry and aborts with reauth_successful."""
    entry = _entry(hass)
    result = await _start_reauth(hass, entry)

    mock_api.initial_auth = AsyncMock(return_value=None)

    with patch(_PATCH_TARGET, return_value=mock_api):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_EMAIL: EMAIL, CONF_PASSWORD: "new-password"},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "new-password"


async def test_reauth_confirm_invalid_auth(hass: HomeAssistant, mock_api: AsyncMock) -> None:
    """Bad credentials in reauth_confirm surface invalid_auth."""
    entry = _entry(hass)
    result = await _start_reauth(hass, entry)

    mock_api.initial_auth = AsyncMock(side_effect=FluidraAuthError("401"))

    with patch(_PATCH_TARGET, return_value=mock_api):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_EMAIL: EMAIL, CONF_PASSWORD: "wrong"},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {"base": "invalid_auth"}


async def test_reauth_confirm_cannot_connect(hass: HomeAssistant, mock_api: AsyncMock) -> None:
    """A connection error in reauth_confirm surfaces cannot_connect."""
    entry = _entry(hass)
    result = await _start_reauth(hass, entry)

    mock_api.initial_auth = AsyncMock(side_effect=FluidraConnectionError("timeout"))

    with patch(_PATCH_TARGET, return_value=mock_api):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_EMAIL: EMAIL, CONF_PASSWORD: "x"},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {"base": "cannot_connect"}


async def test_reauth_via_mfa_success(hass: HomeAssistant, mock_api: AsyncMock) -> None:
    """Reauth that requires MFA completes through the mfa step and updates the entry."""
    entry = _entry(hass)
    result = await _start_reauth(hass, entry)

    mock_api.initial_auth = AsyncMock(side_effect=FluidraMFARequired("SOFTWARE_TOKEN_MFA", "sess-123"))
    mock_api.respond_to_mfa = AsyncMock(return_value=None)
    mock_api.refresh_token = "refresh-token-abc"

    with patch(_PATCH_TARGET, return_value=mock_api):
        # Submitting credentials triggers MFA -> mfa form.
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_EMAIL: EMAIL, CONF_PASSWORD: "new-password"},
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "mfa"

        # Submitting the code completes reauth.
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"mfa_code": "000000"},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "new-password"
    assert entry.data["refresh_token"] == "refresh-token-abc"


async def test_mfa_invalid_code(hass: HomeAssistant, mock_api: AsyncMock) -> None:
    """An invalid MFA code re-shows the mfa form with invalid_mfa_code."""
    entry = _entry(hass)
    result = await _start_reauth(hass, entry)

    mock_api.initial_auth = AsyncMock(side_effect=FluidraMFARequired("SOFTWARE_TOKEN_MFA", "sess-xyz"))
    mock_api.respond_to_mfa = AsyncMock(side_effect=FluidraAuthError("bad code"))

    with patch(_PATCH_TARGET, return_value=mock_api):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_EMAIL: EMAIL, CONF_PASSWORD: "new-password"},
        )
        assert result["step_id"] == "mfa"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"mfa_code": "999999"},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "mfa"
    assert result["errors"] == {"base": "invalid_mfa_code"}


# --------------------------------------------------------------------------- #
# Reconfigure flow
# --------------------------------------------------------------------------- #


async def _start_reconfigure(hass: HomeAssistant, entry: MockConfigEntry) -> dict:
    """Start a reconfigure flow and return the form result."""
    return await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )


async def test_reconfigure_shows_form(hass: HomeAssistant) -> None:
    """Reconfigure flow shows its form pre-filled with the existing email."""
    entry = _entry(hass)
    result = await _start_reconfigure(hass, entry)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    assert result["description_placeholders"]["email"] == EMAIL


async def test_reconfigure_success(hass: HomeAssistant, mock_api: AsyncMock) -> None:
    """A successful reconfigure updates credentials and aborts."""
    entry = _entry(hass)
    result = await _start_reconfigure(hass, entry)

    mock_api.initial_auth = AsyncMock(return_value=None)

    with patch(_PATCH_TARGET, return_value=mock_api):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_EMAIL: EMAIL, CONF_PASSWORD: "changed-pass"},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_PASSWORD] == "changed-pass"


async def test_reconfigure_invalid_auth(hass: HomeAssistant, mock_api: AsyncMock) -> None:
    """Bad credentials in reconfigure surface invalid_auth."""
    entry = _entry(hass)
    result = await _start_reconfigure(hass, entry)

    mock_api.initial_auth = AsyncMock(side_effect=FluidraAuthError("401"))

    with patch(_PATCH_TARGET, return_value=mock_api):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_EMAIL: EMAIL, CONF_PASSWORD: "wrong"},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    assert result["errors"] == {"base": "invalid_auth"}


async def test_reconfigure_cannot_connect(hass: HomeAssistant, mock_api: AsyncMock) -> None:
    """A connection error in reconfigure surfaces cannot_connect."""
    entry = _entry(hass)
    result = await _start_reconfigure(hass, entry)

    mock_api.initial_auth = AsyncMock(side_effect=FluidraConnectionError("timeout"))

    with patch(_PATCH_TARGET, return_value=mock_api):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_EMAIL: EMAIL, CONF_PASSWORD: "x"},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    assert result["errors"] == {"base": "cannot_connect"}


async def test_reconfigure_via_mfa_success(hass: HomeAssistant, mock_api: AsyncMock) -> None:
    """Reconfigure that requires MFA completes through the mfa step."""
    entry = _entry(hass)
    result = await _start_reconfigure(hass, entry)

    mock_api.initial_auth = AsyncMock(side_effect=FluidraMFARequired("SOFTWARE_TOKEN_MFA", "sess-r"))
    mock_api.respond_to_mfa = AsyncMock(return_value=None)
    mock_api.refresh_token = "rt-reconfig"

    with patch(_PATCH_TARGET, return_value=mock_api):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_EMAIL: EMAIL, CONF_PASSWORD: "new-pass"},
        )
        assert result["step_id"] == "mfa"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"mfa_code": "123456"},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_PASSWORD] == "new-pass"
    assert entry.data["refresh_token"] == "rt-reconfig"


async def test_reconfigure_changed_email_success(hass: HomeAssistant, mock_api: AsyncMock) -> None:
    """Reconfiguring with a new (unused) email updates the unique_id and aborts."""
    entry = _entry(hass)
    result = await _start_reconfigure(hass, entry)

    new_email = "fresh@example.com"
    mock_api.initial_auth = AsyncMock(return_value=None)

    with (
        patch(_PATCH_TARGET, return_value=mock_api),
        patch(
            "custom_components.fluidra_pool.async_setup_entry",
            return_value=True,
        ),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_EMAIL: new_email, CONF_PASSWORD: "pw"},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_EMAIL] == new_email
    # The entry's unique_id is updated to the new email on reconfigure.
    assert entry.unique_id == new_email.lower()


async def test_reconfigure_changed_email_via_mfa(hass: HomeAssistant, mock_api: AsyncMock) -> None:
    """Reconfigure with a new email through MFA sets the new unique_id."""
    entry = _entry(hass)
    result = await _start_reconfigure(hass, entry)

    new_email = "another@example.com"
    mock_api.initial_auth = AsyncMock(side_effect=FluidraMFARequired("SOFTWARE_TOKEN_MFA", "sess-c"))
    mock_api.respond_to_mfa = AsyncMock(return_value=None)
    mock_api.refresh_token = "rt-new-email"

    with (
        patch(_PATCH_TARGET, return_value=mock_api),
        patch(
            "custom_components.fluidra_pool.async_setup_entry",
            return_value=True,
        ),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_EMAIL: new_email, CONF_PASSWORD: "pw"},
        )
        assert result["step_id"] == "mfa"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"mfa_code": "424242"},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_EMAIL] == new_email
    # The entry's unique_id is updated to the new email on reconfigure (via MFA).
    assert entry.unique_id == new_email.lower()


# --------------------------------------------------------------------------- #
# Unknown-error paths (FluidraError) in _test_credentials and _verify_mfa
# --------------------------------------------------------------------------- #


async def test_reauth_confirm_unknown_error(hass: HomeAssistant, mock_api: AsyncMock) -> None:
    """A generic FluidraError during reauth auth surfaces the 'unknown' error."""
    entry = _entry(hass)
    result = await _start_reauth(hass, entry)

    mock_api.initial_auth = AsyncMock(side_effect=FluidraError("weird"))

    with patch(_PATCH_TARGET, return_value=mock_api):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_EMAIL: EMAIL, CONF_PASSWORD: "x"},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {"base": "unknown"}


async def test_mfa_unknown_error(hass: HomeAssistant, mock_api: AsyncMock) -> None:
    """A generic FluidraError while verifying the MFA code surfaces 'unknown'."""
    entry = _entry(hass)
    result = await _start_reauth(hass, entry)

    mock_api.initial_auth = AsyncMock(side_effect=FluidraMFARequired("SOFTWARE_TOKEN_MFA", "sess-u"))
    mock_api.respond_to_mfa = AsyncMock(side_effect=FluidraError("weird mfa"))

    with patch(_PATCH_TARGET, return_value=mock_api):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_EMAIL: EMAIL, CONF_PASSWORD: "new-password"},
        )
        assert result["step_id"] == "mfa"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"mfa_code": "111111"},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "mfa"
    assert result["errors"] == {"base": "unknown"}


async def test_mfa_cannot_connect(hass: HomeAssistant, mock_api: AsyncMock) -> None:
    """A connection error while verifying the MFA code surfaces 'cannot_connect'."""
    entry = _entry(hass)
    result = await _start_reauth(hass, entry)

    mock_api.initial_auth = AsyncMock(side_effect=FluidraMFARequired("SOFTWARE_TOKEN_MFA", "sess-cc"))
    mock_api.respond_to_mfa = AsyncMock(side_effect=FluidraConnectionError("down"))

    with patch(_PATCH_TARGET, return_value=mock_api):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_EMAIL: EMAIL, CONF_PASSWORD: "new-password"},
        )
        assert result["step_id"] == "mfa"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"mfa_code": "222222"},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "mfa"
    assert result["errors"] == {"base": "cannot_connect"}
