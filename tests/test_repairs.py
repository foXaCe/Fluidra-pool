"""Tests for the repair-issue helpers, fix-flow factory and coordinator wiring."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.components.repairs import ConfirmRepairFlow
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
import pytest

from custom_components.fluidra_pool.const import CONNECTION_ISSUE_THRESHOLD, DOMAIN
from custom_components.fluidra_pool.coordinator import FluidraDataUpdateCoordinator
from custom_components.fluidra_pool.repairs import (
    async_create_connection_issue,
    async_create_fix_flow,
    async_create_unverified_profile_issue,
    async_delete_connection_issue,
    async_delete_unverified_profile_issue,
)


@pytest.fixture
def coordinator(hass: HomeAssistant, mock_api: AsyncMock) -> FluidraDataUpdateCoordinator:
    """Create a coordinator with mock API."""
    return FluidraDataUpdateCoordinator(hass, mock_api)


def test_connection_issue_create_and_delete(hass: HomeAssistant) -> None:
    """The helper pair delegates to the issue registry with the right issue id."""
    with (
        patch("custom_components.fluidra_pool.repairs.ir.async_create_issue") as create_mock,
        patch("custom_components.fluidra_pool.repairs.ir.async_delete_issue") as delete_mock,
    ):
        async_create_connection_issue(hass)
        async_delete_connection_issue(hass)

    create_mock.assert_called_once()
    create_args = create_mock.call_args
    assert create_args.args[0] is hass
    assert create_args.args[1] == DOMAIN
    assert create_args.args[2] == "connection_error"

    delete_mock.assert_called_once_with(hass, DOMAIN, "connection_error")


def test_connection_issue_is_fixable_error(hass: HomeAssistant) -> None:
    """Connection-error repair is fixable — backed by ConfirmRepairFlow."""
    with patch("custom_components.fluidra_pool.repairs.ir.async_create_issue") as create_mock:
        async_create_connection_issue(hass)

    kwargs = create_mock.call_args.kwargs
    assert kwargs["is_fixable"] is True
    assert kwargs["translation_key"] == "connection_error"


async def test_async_create_fix_flow_returns_confirm_flow(hass: HomeAssistant) -> None:
    """The fix-flow factory returns a ConfirmRepairFlow for the fixable issue."""
    flow = await async_create_fix_flow(hass, "connection_error", None)
    assert isinstance(flow, ConfirmRepairFlow)


def test_unverified_profile_issue_create_and_delete(hass: HomeAssistant) -> None:
    """The helper pair delegates to the issue registry with the per-device issue id."""
    with (
        patch("custom_components.fluidra_pool.repairs.ir.async_create_issue") as create_mock,
        patch("custom_components.fluidra_pool.repairs.ir.async_delete_issue") as delete_mock,
    ):
        async_create_unverified_profile_issue(hass, "D1", "My Chlorinator")
        async_delete_unverified_profile_issue(hass, "D1")

    create_mock.assert_called_once()
    create_args = create_mock.call_args
    assert create_args.args[0] is hass
    assert create_args.args[1] == DOMAIN
    assert create_args.args[2] == "unverified_profile_D1"

    kwargs = create_mock.call_args.kwargs
    assert kwargs["is_fixable"] is False
    assert kwargs["severity"] == ir.IssueSeverity.WARNING
    assert kwargs["translation_key"] == "unverified_device_profile"
    assert kwargs["translation_placeholders"] == {"device_name": "My Chlorinator"}

    delete_mock.assert_called_once_with(hass, DOMAIN, "unverified_profile_D1")


class TestCoordinatorConnectionIssue:
    """The coordinator raises/clears the connection issue on persistent failures."""

    def test_issue_created_at_threshold_only(self, coordinator: FluidraDataUpdateCoordinator) -> None:
        with patch("custom_components.fluidra_pool.coordinator.coordinator.async_create_connection_issue") as create:
            for _ in range(CONNECTION_ISSUE_THRESHOLD - 1):
                coordinator._note_update_failure()
            create.assert_not_called()

            coordinator._note_update_failure()
            create.assert_called_once_with(coordinator.hass)

            # Further failures must not re-create (no repair spam).
            coordinator._note_update_failure()
            create.assert_called_once()

    def test_success_clears_issue_and_resets_streak(self, coordinator: FluidraDataUpdateCoordinator) -> None:
        with (
            patch("custom_components.fluidra_pool.coordinator.coordinator.async_create_connection_issue"),
            patch("custom_components.fluidra_pool.coordinator.coordinator.async_delete_connection_issue") as delete,
        ):
            for _ in range(CONNECTION_ISSUE_THRESHOLD):
                coordinator._note_update_failure()

            coordinator._handle_update_success()
            delete.assert_called_once_with(coordinator.hass)
            assert coordinator._consecutive_update_failures == 0

    def test_success_below_threshold_does_not_touch_registry(self, coordinator: FluidraDataUpdateCoordinator) -> None:
        with (
            patch("custom_components.fluidra_pool.coordinator.coordinator.async_create_connection_issue"),
            patch("custom_components.fluidra_pool.coordinator.coordinator.async_delete_connection_issue") as delete,
        ):
            coordinator._note_update_failure()
            coordinator._handle_update_success()

            delete.assert_not_called()
            assert coordinator._consecutive_update_failures == 0
