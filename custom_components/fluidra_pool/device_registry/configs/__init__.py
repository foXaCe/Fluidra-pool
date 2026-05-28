"""Device configuration registry — assembled from per-family submodules."""

from __future__ import annotations

from ..types import DeviceConfig
from .chlorinators import CHLORINATOR_CONFIGS
from .generic import GENERIC_CONFIGS
from .heat_pumps import HEAT_PUMP_CONFIGS
from .probes import PROBE_CONFIGS
from .pumps import PUMP_CONFIGS

DEVICE_CONFIGS: dict[str, DeviceConfig] = {
    **HEAT_PUMP_CONFIGS,
    **CHLORINATOR_CONFIGS,
    **PROBE_CONFIGS,
    **PUMP_CONFIGS,
    **GENERIC_CONFIGS,
}

__all__ = ["DEVICE_CONFIGS"]
