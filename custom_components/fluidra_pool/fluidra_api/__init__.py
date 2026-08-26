"""Fluidra Pool API wrapper for Home Assistant integration.

This package provides a simplified interface to the Fluidra Pool library
optimised for Home Assistant usage with real AWS Cognito authentication.
"""

from __future__ import annotations

from ._uiconfig import (
    SchedulerCapability,
    UiRegister,
    build_dynamic_scan_set,
    parse_scheduler_capabilities,
    parse_uiconfig,
)
from .client import FluidraPoolAPI

__all__ = [
    "FluidraPoolAPI",
    "SchedulerCapability",
    "UiRegister",
    "build_dynamic_scan_set",
    "parse_scheduler_capabilities",
    "parse_uiconfig",
]
