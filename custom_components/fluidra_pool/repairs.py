"""Repair issues for the Fluidra Pool integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.repairs import ConfirmRepairFlow, RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN


def async_create_connection_issue(hass: HomeAssistant) -> None:
    """Create a repair issue signalling a connection problem."""
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


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, Any] | None,
) -> RepairsFlow:
    """Create a repair flow for a fixable issue.

    Currently only the ``connection_error`` issue is fixable. The flow simply
    confirms the acknowledgement so Home Assistant can remove the issue after
    the next successful update.
    """
    return ConfirmRepairFlow()
