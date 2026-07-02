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
        assert info["via_device"] == (DOMAIN, "pool_001")

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


def _availability_device(online_value) -> dict:
    device = {"device_id": "DEV-1", "name": "Pump"}
    if online_value != "absent":
        device["online"] = online_value
    return device


@pytest.mark.parametrize(
    ("online_value", "expected"),
    [(True, True), ("absent", True), (None, True), (False, False)],
)
def test_available_only_gates_on_explicit_offline(online_value, expected) -> None:
    """Missing/unknown connectivity must not read as offline; only online=False gates."""
    coordinator = MagicMock()
    coordinator.last_update_success = True
    coordinator.data = {"pool_1": {"devices": [_availability_device(online_value)]}}
    entity = FluidraPoolEntity(coordinator, "pool_1", "DEV-1")
    assert entity.available is expected


def test_available_false_when_device_vanished() -> None:
    """A device missing from coordinator data is unavailable."""
    coordinator = MagicMock()
    coordinator.last_update_success = True
    coordinator.data = {"pool_1": {"devices": []}}
    entity = FluidraPoolEntity(coordinator, "pool_1", "DEV-1")
    assert entity.available is False
