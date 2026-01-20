"""Test config flow for Fluidra Pool integration."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest

from custom_components.fluidra_pool.config_flow import FluidraPoolConfigFlow
from custom_components.fluidra_pool.fluidra_api import FluidraPoolAPI


@pytest.fixture
def mock_api():
    """Create mock API."""
    api = AsyncMock(spec=FluidraPoolAPI)
    api.authenticate = AsyncMock()
    api.get_pools = AsyncMock(return_value=[{"id": "test_pool", "name": "Test Pool"}])
    api.close = AsyncMock()
    return api


@pytest.fixture
def config_flow(hass: HomeAssistant) -> FluidraPoolConfigFlow:
    """Create config flow."""
    flow = FluidraPoolConfigFlow()
    flow.hass = hass
    return flow


async def test_user_flow_success(hass: HomeAssistant, config_flow, mock_api) -> None:
    """Test successful user flow."""
    result = await config_flow.async_step_user()
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    # Mock successful API calls
    with pytest.MonkeyPatch().context() as m:
        m.setattr("custom_components.fluidra_pool.config_flow.FluidraPoolAPI", lambda email, password: mock_api)

        result = await config_flow.async_step_user({CONF_EMAIL: "test@example.com", CONF_PASSWORD: "test_password"})

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "test@example.com"
    assert result["data"][CONF_EMAIL] == "test@example.com"
    assert result["data"][CONF_PASSWORD] == "test_password"


async def test_user_flow_invalid_auth(hass: HomeAssistant, config_flow, mock_api) -> None:
    """Test user flow with invalid authentication."""
    mock_api.authenticate.side_effect = Exception("Authentication failed")

    with pytest.MonkeyPatch().context() as m:
        m.setattr("custom_components.fluidra_pool.config_flow.FluidraPoolAPI", lambda email, password: mock_api)

        result = await config_flow.async_step_user({CONF_EMAIL: "test@example.com", CONF_PASSWORD: "wrong_password"})

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_user_flow_cannot_connect(hass: HomeAssistant, config_flow, mock_api) -> None:
    """Test user flow with connection error."""
    mock_api.authenticate.side_effect = Exception("Connection failed")

    with pytest.MonkeyPatch().context() as m:
        m.setattr("custom_components.fluidra_pool.config_flow.FluidraPoolAPI", lambda email, password: mock_api)

        result = await config_flow.async_step_user({CONF_EMAIL: "test@example.com", CONF_PASSWORD: "test_password"})

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_reauth_flow_success(hass: HomeAssistant, config_flow, mock_api) -> None:
    """Test successful reauthentication flow."""
    # Mock existing config entry
    mock_entry = Mock()
    mock_entry.data = {CONF_EMAIL: "test@example.com", CONF_PASSWORD: "old_password"}

    with pytest.MonkeyPatch().context() as m:
        m.setattr("custom_components.fluidra_pool.config_flow.FluidraPoolAPI", lambda email, password: mock_api)
        m.setattr(config_flow, "_get_reauth_entry", lambda: mock_entry)

        result = await config_flow.async_step_reauth_confirm({CONF_PASSWORD: "new_password"})

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
