"""Tests for Fluidra Pool data update coordinator."""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
import pytest

from custom_components.fluidra_pool.coordinator import FluidraDataUpdateCoordinator


@pytest.fixture
def coordinator(hass: HomeAssistant, mock_api: AsyncMock) -> FluidraDataUpdateCoordinator:
    """Create a coordinator with mock API."""
    return FluidraDataUpdateCoordinator(hass, mock_api)


class TestCoordinatorInit:
    """Test coordinator initialization."""

    async def test_creates_with_defaults(self, hass: HomeAssistant, mock_api: AsyncMock):
        coord = FluidraDataUpdateCoordinator(hass, mock_api)
        assert coord.api is mock_api
        assert coord.name == "fluidra_pool"
        assert coord._first_update is True

    async def test_creates_with_config_entry(self, hass: HomeAssistant, mock_api: AsyncMock):
        coord = FluidraDataUpdateCoordinator(hass, mock_api, config_entry=None)
        assert coord.config_entry is None


class TestOptimisticEntities:
    """Test optimistic entity tracking."""

    async def test_register_and_has_optimistic(self, coordinator: FluidraDataUpdateCoordinator):
        assert coordinator.has_optimistic_entities() is False
        coordinator.register_optimistic_entity("switch.pool_pump")
        assert coordinator.has_optimistic_entities() is True

    async def test_unregister_optimistic(self, coordinator: FluidraDataUpdateCoordinator):
        coordinator.register_optimistic_entity("switch.pool_pump")
        coordinator.unregister_optimistic_entity("switch.pool_pump")
        assert coordinator.has_optimistic_entities() is False

    async def test_unregister_nonexistent(self, coordinator: FluidraDataUpdateCoordinator):
        coordinator.unregister_optimistic_entity("switch.nonexistent")
        assert coordinator.has_optimistic_entities() is False


class TestGetPoolsFromData:
    """Test get_pools_from_data method."""

    async def test_returns_empty_when_no_data(self, coordinator: FluidraDataUpdateCoordinator):
        coordinator.data = None
        assert coordinator.get_pools_from_data() == []

    async def test_returns_pools_from_data(self, coordinator: FluidraDataUpdateCoordinator, mock_pool_data: dict):
        coordinator.data = mock_pool_data
        pools = coordinator.get_pools_from_data()
        assert len(pools) == 1
        assert pools[0]["id"] == "pool_001"


class TestAsyncUpdateData:
    """Test _async_update_data method."""

    async def test_first_update_returns_minimal_data(self, coordinator: FluidraDataUpdateCoordinator):
        """First update should return pool structure only for fast startup."""
        result = await coordinator._async_update_data()
        assert "pool_001" in result
        assert coordinator._first_update is False

    async def test_second_update_fetches_components(
        self, coordinator: FluidraDataUpdateCoordinator, mock_api: AsyncMock
    ):
        """Second update should fetch component details."""
        # First update
        await coordinator._async_update_data()
        # Second update
        await coordinator._async_update_data()
        assert mock_api.get_pool_details.called
        assert mock_api.poll_device_status.called

    async def test_skips_update_when_optimistic(self, coordinator: FluidraDataUpdateCoordinator):
        """Should skip API polling when optimistic entities exist."""
        # Set up existing data
        coordinator.data = {"pool_001": {"name": "cached"}}
        coordinator.register_optimistic_entity("switch.test")

        result = await coordinator._async_update_data()
        assert result == {"pool_001": {"name": "cached"}}

    async def test_raises_auth_failed_on_invalid_token(
        self, coordinator: FluidraDataUpdateCoordinator, mock_api: AsyncMock
    ):
        """Should raise ConfigEntryAuthFailed when token is invalid."""
        mock_api.ensure_valid_token.return_value = False

        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()

    async def test_raises_update_failed_on_error(self, coordinator: FluidraDataUpdateCoordinator, mock_api: AsyncMock):
        """Should raise UpdateFailed on API errors."""
        mock_api.ensure_valid_token.return_value = True
        mock_api.get_pools.side_effect = Exception("Network error")

        with pytest.raises(UpdateFailed, match="Error communicating with API"):
            await coordinator._async_update_data()


class TestProcessComponentState:
    """Test _process_component_state method."""

    async def test_component_0_sets_device_id(self, coordinator: FluidraDataUpdateCoordinator):
        device = {"device_id": "test", "type": "pump", "components": {}}
        coordinator._process_component_state(device, "pool_001", 0, {"reportedValue": "DEVICE_ID_123"})
        assert device["device_id_component"] == "DEVICE_ID_123"

    async def test_component_9_sets_pump_state(self, coordinator: FluidraDataUpdateCoordinator):
        device = {"device_id": "test", "type": "pump", "components": {}}
        coordinator._process_component_state(device, "pool_001", 9, {"reportedValue": 1, "desiredValue": 1})
        assert device["is_running"] is True
        assert device["pump_reported"] == 1

    async def test_component_9_pump_off(self, coordinator: FluidraDataUpdateCoordinator):
        device = {"device_id": "test", "type": "pump", "components": {}}
        coordinator._process_component_state(device, "pool_001", 9, {"reportedValue": 0, "desiredValue": 0})
        assert device["is_running"] is False

    async def test_component_10_auto_mode(self, coordinator: FluidraDataUpdateCoordinator):
        device = {"device_id": "test", "type": "pump", "components": {}}
        coordinator._process_component_state(device, "pool_001", 10, {"reportedValue": 1, "desiredValue": 1})
        assert device["auto_mode_enabled"] is True

    async def test_component_13_heat_pump_state(self, coordinator: FluidraDataUpdateCoordinator):
        device = {"device_id": "test", "type": "heat_pump", "components": {}}
        coordinator._process_component_state(device, "pool_001", 13, {"reportedValue": 1})
        assert device["is_heating"] is True

    async def test_component_15_temperature(self, coordinator: FluidraDataUpdateCoordinator):
        device = {"device_id": "test", "type": "heat_pump", "components": {}}
        coordinator._process_component_state(device, "pool_001", 15, {"reportedValue": 290})
        assert device["target_temperature"] == 29.0

    async def test_component_15_invalid_temp_ignored(self, coordinator: FluidraDataUpdateCoordinator):
        device = {"device_id": "test", "type": "heat_pump", "components": {}}
        coordinator._process_component_state(device, "pool_001", 15, {"reportedValue": 5})
        assert "target_temperature" not in device

    async def test_component_20_chlorinator_mode(self, coordinator: FluidraDataUpdateCoordinator):
        device = {"device_id": "test", "type": "chlorinator", "components": {}}
        coordinator._process_component_state(device, "pool_001", 20, {"reportedValue": 2})
        assert device["mode_reported"] == 2

    async def test_component_20_pump_schedule(self, coordinator: FluidraDataUpdateCoordinator):
        schedule = [{"id": 1, "startTime": "0 8 * * 1,2,3", "enabled": True}]
        device = {"device_id": "test", "type": "pump", "components": {}}
        coordinator._process_component_state(device, "pool_001", 20, {"reportedValue": schedule})
        assert device["schedule_data"] == schedule


class TestParseDM24049704Schedule:
    """Test DM24049704 schedule format parsing."""

    async def test_parse_valid_schedule(self, coordinator: FluidraDataUpdateCoordinator):
        reported_value = {
            "dayPrograms": {"monday": 1, "tuesday": 1, "wednesday": 1},
            "programs": [{"id": 1, "slots": [{"id": 0, "start": 1280, "end": 1536, "mode": 3}]}],
        }
        result = coordinator._parse_dm24049704_schedule_format(reported_value)
        assert len(result) == 1
        assert result[0]["enabled"] is True
        assert "startTime" in result[0]
        assert "endTime" in result[0]

    async def test_parse_empty_programs(self, coordinator: FluidraDataUpdateCoordinator):
        result = coordinator._parse_dm24049704_schedule_format({"dayPrograms": {}, "programs": []})
        assert result == []

    async def test_parse_non_dict_returns_empty(self, coordinator: FluidraDataUpdateCoordinator):
        assert coordinator._parse_dm24049704_schedule_format("not a dict") == []
        assert coordinator._parse_dm24049704_schedule_format(None) == []

    async def test_parse_skips_empty_slots(self, coordinator: FluidraDataUpdateCoordinator):
        reported_value = {
            "dayPrograms": {"monday": 1},
            "programs": [{"id": 1, "slots": [{"id": 0, "start": 0, "end": 0, "mode": 0}]}],
        }
        result = coordinator._parse_dm24049704_schedule_format(reported_value)
        assert result == []

    async def test_time_decoding(self, coordinator: FluidraDataUpdateCoordinator):
        """Test that time encoding hours*256+minutes is decoded correctly."""
        # 5:00 = 5*256 + 0 = 1280, 6:00 = 6*256 + 0 = 1536
        reported_value = {
            "dayPrograms": {"monday": 1},
            "programs": [{"id": 1, "slots": [{"id": 0, "start": 1280, "end": 1536, "mode": 1}]}],
        }
        result = coordinator._parse_dm24049704_schedule_format(reported_value)
        assert len(result) == 1
        assert "0 5" in result[0]["startTime"]
        assert "0 6" in result[0]["endTime"]


class TestCalculateAutoSpeed:
    """Test _calculate_auto_speed_from_schedules."""

    async def test_no_schedules_returns_zero(self, coordinator: FluidraDataUpdateCoordinator):
        device = {"schedule_data": []}
        assert coordinator._calculate_auto_speed_from_schedules(device) == 0

    async def test_no_schedule_data_returns_zero(self, coordinator: FluidraDataUpdateCoordinator):
        device = {}
        assert coordinator._calculate_auto_speed_from_schedules(device) == 0
