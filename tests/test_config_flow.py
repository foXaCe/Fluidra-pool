"""Tests for Fluidra Pool config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.fluidra_pool.const import DOMAIN


async def test_user_flow_shows_form(hass: HomeAssistant) -> None:
    """Test that the user flow shows a form initially."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_user_flow_success(hass: HomeAssistant, mock_api: AsyncMock) -> None:
    """Test successful user flow creates an entry."""
    with patch(
        "custom_components.fluidra_pool.config_flow.FluidraPoolAPI",
        return_value=mock_api,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data={CONF_EMAIL: "test@example.com", CONF_PASSWORD: "test_password"},
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Fluidra Pool (test@example.com)"
    assert result["data"][CONF_EMAIL] == "test@example.com"
    assert result["data"][CONF_PASSWORD] == "test_password"


async def test_user_flow_invalid_auth(hass: HomeAssistant, mock_api: AsyncMock) -> None:
    """Test user flow with invalid authentication."""
    mock_api.authenticate.side_effect = Exception("401 Unauthorized")

    with patch(
        "custom_components.fluidra_pool.config_flow.FluidraPoolAPI",
        return_value=mock_api,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data={CONF_EMAIL: "test@example.com", CONF_PASSWORD: "wrong"},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_user_flow_cannot_connect(hass: HomeAssistant, mock_api: AsyncMock) -> None:
    """Test user flow with connection error."""
    mock_api.authenticate.side_effect = Exception("Connection timeout")

    with patch(
        "custom_components.fluidra_pool.config_flow.FluidraPoolAPI",
        return_value=mock_api,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data={CONF_EMAIL: "test@example.com", CONF_PASSWORD: "test"},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_flow_unknown_error(hass: HomeAssistant, mock_api: AsyncMock) -> None:
    """Test user flow with unknown error."""
    mock_api.authenticate.side_effect = Exception("Something weird happened")

    with patch(
        "custom_components.fluidra_pool.config_flow.FluidraPoolAPI",
        return_value=mock_api,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data={CONF_EMAIL: "test@example.com", CONF_PASSWORD: "test"},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "unknown"}
