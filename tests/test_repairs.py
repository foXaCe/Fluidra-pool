"""Tests for the repair-issue helpers and fix-flow factory."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.components.repairs import ConfirmRepairFlow
from homeassistant.core import HomeAssistant
import pytest

from custom_components.fluidra_pool.const import DOMAIN
from custom_components.fluidra_pool.repairs import (
    async_create_connection_issue,
    async_create_firmware_update_issue,
    async_create_fix_flow,
    async_create_offline_device_issue,
    async_delete_connection_issue,
    async_delete_firmware_update_issue,
    async_delete_offline_device_issue,
)


@pytest.mark.parametrize(
    ("creator", "deleter", "expected_id"),
    [
        (
            lambda hass: async_create_offline_device_issue(hass, "LE24500883"),
            lambda hass: async_delete_offline_device_issue(hass, "LE24500883"),
            "offline_device_LE24500883",
        ),
        (
            lambda hass: async_create_firmware_update_issue(hass, "LE24500883", "3.9.1"),
            lambda hass: async_delete_firmware_update_issue(hass, "LE24500883"),
            "firmware_update_LE24500883",
        ),
        (
            lambda hass: async_create_connection_issue(hass),
            lambda hass: async_delete_connection_issue(hass),
            "connection_error",
        ),
    ],
)
def test_issue_helpers_create_and_delete(
    hass: HomeAssistant,
    creator,
    deleter,
    expected_id: str,
) -> None:
    """Each helper pair delegates to the issue registry with the right issue id."""
    with (
        patch("custom_components.fluidra_pool.repairs.ir.async_create_issue") as create_mock,
        patch("custom_components.fluidra_pool.repairs.ir.async_delete_issue") as delete_mock,
    ):
        creator(hass)
        deleter(hass)

    create_mock.assert_called_once()
    create_args = create_mock.call_args
    assert create_args.args[0] is hass
    assert create_args.args[1] == DOMAIN
    assert create_args.args[2] == expected_id

    delete_mock.assert_called_once_with(hass, DOMAIN, expected_id)


def test_offline_device_issue_uses_warning_severity(hass: HomeAssistant) -> None:
    """Offline-device repair is a non-fixable warning so the UI shows it as info."""
    with patch("custom_components.fluidra_pool.repairs.ir.async_create_issue") as create_mock:
        async_create_offline_device_issue(hass, "LE24500883")

    kwargs = create_mock.call_args.kwargs
    assert kwargs["is_fixable"] is False
    assert kwargs["translation_key"] == "offline_device"
    assert kwargs["translation_placeholders"] == {"device": "LE24500883"}


def test_firmware_update_issue_carries_version_placeholder(hass: HomeAssistant) -> None:
    """Firmware-update repair surfaces both the device id and the new version."""
    with patch("custom_components.fluidra_pool.repairs.ir.async_create_issue") as create_mock:
        async_create_firmware_update_issue(hass, "LE24500883", "3.9.1")

    placeholders = create_mock.call_args.kwargs["translation_placeholders"]
    assert placeholders == {"device": "LE24500883", "version": "3.9.1"}


def test_connection_issue_is_fixable_error(hass: HomeAssistant) -> None:
    """Connection-error repair is the only fixable one — backed by ConfirmRepairFlow."""
    with patch("custom_components.fluidra_pool.repairs.ir.async_create_issue") as create_mock:
        async_create_connection_issue(hass)

    kwargs = create_mock.call_args.kwargs
    assert kwargs["is_fixable"] is True
    assert kwargs["translation_key"] == "connection_error"


async def test_async_create_fix_flow_returns_confirm_flow(hass: HomeAssistant) -> None:
    """The fix-flow factory returns a ConfirmRepairFlow for the fixable issue."""
    flow = await async_create_fix_flow(hass, "connection_error", None)
    assert isinstance(flow, ConfirmRepairFlow)
