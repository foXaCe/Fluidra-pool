"""Device registry for Fluidra Pool equipment types.

This package centralises device configurations to make adding new equipment easier
and reduce the risk of breaking existing devices.
"""

from __future__ import annotations

from .configs import DEVICE_CONFIGS
from .identifier import DeviceIdentifier
from .types import DeviceConfig

__all__ = ["DEVICE_CONFIGS", "DeviceConfig", "DeviceIdentifier"]
