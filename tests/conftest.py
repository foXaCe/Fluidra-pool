"""Shared test fixtures for Fluidra Pool integration."""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
from homeassistant.loader import DATA_CUSTOM_COMPONENTS
import pytest

from custom_components.fluidra_pool.fluidra_api import FluidraPoolAPI


@pytest.fixture(autouse=True)
def enable_custom_integrations(hass: HomeAssistant) -> None:
    """Enable custom integrations in the test environment."""
    hass.data.pop(DATA_CUSTOM_COMPONENTS, None)


@pytest.fixture
def mock_api() -> AsyncMock:
    """Create a mock FluidraPoolAPI."""
    api = AsyncMock(spec=FluidraPoolAPI)
    api.authenticate = AsyncMock(return_value=True)
    api.ensure_valid_token = AsyncMock(return_value=True)
    api.get_pools = AsyncMock(
        return_value=[
            {
                "id": "pool_001",
                "name": "My Pool",
                "devices": [
                    {
                        "device_id": "E30-001",
                        "name": "Pool Pump",
                        "family": "",
                        "model": "E30iQ",
                        "type": "pump",
                        "online": True,
                        "components": {},
                    },
                ],
            }
        ]
    )
    api.get_pool_details = AsyncMock(return_value={})
    api.poll_water_quality = AsyncMock(return_value={})
    api.poll_device_status = AsyncMock(return_value={"connectivity": {"online": True}})
    api.get_component_state = AsyncMock(return_value={"reportedValue": 0})
    api.set_component_value = AsyncMock(return_value=True)
    api.set_component_string_value = AsyncMock(return_value=True)
    api.close = AsyncMock()
    return api


@pytest.fixture
def mock_pool_data() -> dict:
    """Sample pool data structure as returned by coordinator."""
    return {
        "pool_001": {
            "id": "pool_001",
            "name": "My Pool",
            "devices": [
                {
                    "device_id": "E30-001",
                    "name": "Pool Pump",
                    "family": "",
                    "model": "E30iQ",
                    "type": "pump",
                    "online": True,
                    "components": {
                        "9": {"reportedValue": 1, "desiredValue": 1},
                        "10": {"reportedValue": 0, "desiredValue": 0},
                        "11": {"reportedValue": 1, "desiredValue": 1},
                    },
                    "is_running": True,
                    "auto_mode_enabled": False,
                    "speed_percent": 65,
                },
            ],
        },
    }
