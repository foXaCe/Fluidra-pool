"""Repair issues for Fluidra Pool integration."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN


def async_create_offline_device_issue(hass: HomeAssistant, device_id: str) -> None:
    """Create a repair issue for offline device."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"offline_device_{device_id}",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="offline_device",
        translation_placeholders={"device": device_id},
    )


def async_delete_offline_device_issue(hass: HomeAssistant, device_id: str) -> None:
    """Delete an offline device repair issue."""
    ir.async_delete_issue(hass, DOMAIN, f"offline_device_{device_id}")


def async_create_firmware_update_issue(hass: HomeAssistant, device_id: str, firmware_version: str) -> None:
    """Create a repair issue for outdated firmware."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"firmware_update_{device_id}",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="firmware_update_available",
        translation_placeholders={"device": device_id, "version": firmware_version},
    )


def async_delete_firmware_update_issue(hass: HomeAssistant, device_id: str) -> None:
    """Delete a firmware update repair issue."""
    ir.async_delete_issue(hass, DOMAIN, f"firmware_update_{device_id}")


def async_create_connection_issue(hass: HomeAssistant) -> None:
    """Create a repair issue for connection problems."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        "connection_error",
        is_fixable=True,
        severity=ir.IssueSeverity.ERROR,
        translation_key="connection_error",
    )


def async_delete_connection_issue(hass: HomeAssistant) -> None:
    """Delete the connection repair issue."""
    ir.async_delete_issue(hass, DOMAIN, "connection_error")
