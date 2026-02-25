"""Tests for Fluidra Pool base entity classes."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.fluidra_pool.const import DOMAIN
from custom_components.fluidra_pool.entity import FluidraPoolControlEntity, FluidraPoolEntity


@pytest.fixture
def mock_coordinator(mock_pool_data: dict) -> MagicMock:
    """Create a mock coordinator with data."""
    coordinator = MagicMock()
    coordinator.data = mock_pool_data
    coordinator.last_update_success = True
    return coordinator


@pytest.fixture
def mock_api() -> AsyncMock:
    """Create a mock API."""
    return AsyncMock()


class TestFluidraPoolEntity:
    """Test FluidraPoolEntity base class."""

    def test_init(self, mock_coordinator: MagicMock):
        entity = FluidraPoolEntity(mock_coordinator, "pool_001", "E30-001")
        assert entity._pool_id == "pool_001"
        assert entity._device_id == "E30-001"

    def test_device_data_found(self, mock_coordinator: MagicMock):
        entity = FluidraPoolEntity(mock_coordinator, "pool_001", "E30-001")
        data = entity.device_data
        assert data["device_id"] == "E30-001"
        assert data["name"] == "Pool Pump"

    def test_device_data_not_found(self, mock_coordinator: MagicMock):
        entity = FluidraPoolEntity(mock_coordinator, "pool_001", "NONEXISTENT")
        assert entity.device_data == {}

    def test_device_data_no_coordinator_data(self, mock_coordinator: MagicMock):
        mock_coordinator.data = None
        entity = FluidraPoolEntity(mock_coordinator, "pool_001", "E30-001")
        assert entity.device_data == {}

    def test_pool_data_found(self, mock_coordinator: MagicMock):
        entity = FluidraPoolEntity(mock_coordinator, "pool_001", "E30-001")
        pool = entity.pool_data
        assert pool["id"] == "pool_001"
        assert "devices" in pool

    def test_pool_data_not_found(self, mock_coordinator: MagicMock):
        entity = FluidraPoolEntity(mock_coordinator, "nonexistent_pool", "E30-001")
        assert entity.pool_data == {}

    def test_pool_data_no_coordinator_data(self, mock_coordinator: MagicMock):
        mock_coordinator.data = None
        entity = FluidraPoolEntity(mock_coordinator, "pool_001", "E30-001")
        assert entity.pool_data == {}

    def test_device_info(self, mock_coordinator: MagicMock):
        entity = FluidraPoolEntity(mock_coordinator, "pool_001", "E30-001")
        info = entity.device_info
        assert (DOMAIN, "E30-001") in info["identifiers"]
        assert info["name"] == "Pool Pump"
        assert info["manufacturer"] == "Fluidra"
        assert (DOMAIN, "pool_001") == info["via_device"]

    def test_device_info_unknown_device(self, mock_coordinator: MagicMock):
        entity = FluidraPoolEntity(mock_coordinator, "pool_001", "UNKNOWN")
        info = entity.device_info
        assert "Device UNKNOWN" in info["name"]

    def test_available_true(self, mock_coordinator: MagicMock):
        entity = FluidraPoolEntity(mock_coordinator, "pool_001", "E30-001")
        assert entity.available is True

    def test_available_false_when_update_failed(self, mock_coordinator: MagicMock):
        mock_coordinator.last_update_success = False
        entity = FluidraPoolEntity(mock_coordinator, "pool_001", "E30-001")
        assert entity.available is False

    def test_available_false_when_offline(self, mock_coordinator: MagicMock):
        mock_coordinator.data["pool_001"]["devices"][0]["online"] = False
        entity = FluidraPoolEntity(mock_coordinator, "pool_001", "E30-001")
        assert entity.available is False

    def test_has_entity_name(self, mock_coordinator: MagicMock):
        entity = FluidraPoolEntity(mock_coordinator, "pool_001", "E30-001")
        assert entity._attr_has_entity_name is True


class TestFluidraPoolControlEntity:
    """Test FluidraPoolControlEntity class."""

    def test_init_with_api(self, mock_coordinator: MagicMock, mock_api: AsyncMock):
        entity = FluidraPoolControlEntity(mock_coordinator, mock_api, "pool_001", "E30-001")
        assert entity._api is mock_api
        assert entity._pool_id == "pool_001"
        assert entity._device_id == "E30-001"

    def test_inherits_device_data(self, mock_coordinator: MagicMock, mock_api: AsyncMock):
        entity = FluidraPoolControlEntity(mock_coordinator, mock_api, "pool_001", "E30-001")
        assert entity.device_data["device_id"] == "E30-001"

    def test_inherits_device_info(self, mock_coordinator: MagicMock, mock_api: AsyncMock):
        entity = FluidraPoolControlEntity(mock_coordinator, mock_api, "pool_001", "E30-001")
        info = entity.device_info
        assert (DOMAIN, "E30-001") in info["identifiers"]

    def test_inherits_available(self, mock_coordinator: MagicMock, mock_api: AsyncMock):
        entity = FluidraPoolControlEntity(mock_coordinator, mock_api, "pool_001", "E30-001")
        assert entity.available is True
