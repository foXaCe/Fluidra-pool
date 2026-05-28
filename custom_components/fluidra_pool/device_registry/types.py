"""Data classes shared by the device-registry submodules."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DeviceConfig:
    """Configuration for a specific device type."""

    device_type: str  # General type: pump, heat_pump, heater, light, chlorinator.
    identifier_patterns: list[str] = field(default_factory=list)  # Identifier patterns (e.g. ["LG*"]).
    name_patterns: list[str] = field(default_factory=list)  # Substrings matched against the device name.
    family_patterns: list[str] = field(default_factory=list)  # Substrings matched against the family field.
    model_patterns: list[str] = field(default_factory=list)  # Substrings matched against the model field.

    # Polling scope.
    components_range: int = 25  # Number of components to scan.
    required_components: list[int] = field(default_factory=list)  # Components that must be present.

    # Home Assistant entities to instantiate.
    entities: list[str] = field(default_factory=list)

    # Device-specific feature flags / component mappings.
    features: dict[str, Any] = field(default_factory=dict)

    # Higher priority is evaluated first when picking a match.
    priority: int = 0
