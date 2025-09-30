"""Device registry for Fluidra Pool equipment types.

This module centralizes device configurations to make adding new equipment easier
and reduce the risk of breaking existing devices.
"""

from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field


@dataclass
class DeviceConfig:
    """Configuration for a specific device type."""

    device_type: str  # Type général: pump, heat_pump, heater, light
    identifier_patterns: List[str] = field(default_factory=list)  # Patterns d'identification (ex: ["LG*"])
    name_patterns: List[str] = field(default_factory=list)  # Patterns dans le nom
    family_patterns: List[str] = field(default_factory=list)  # Patterns dans la famille
    model_patterns: List[str] = field(default_factory=list)  # Patterns dans le modèle

    # Composants à scanner
    components_range: int = 25  # Nombre de composants à scanner
    required_components: List[int] = field(default_factory=list)  # Composants obligatoires

    # Entités Home Assistant à créer
    entities: List[str] = field(default_factory=list)  # Types d'entités: switch, sensor, climate, etc.

    # Features spécifiques
    features: Dict[str, Any] = field(default_factory=dict)

    # Priorité de détection (plus haut = vérifié en premier)
    priority: int = 0


# Configuration centralisée des équipements
DEVICE_CONFIGS: Dict[str, DeviceConfig] = {

    "lg_heat_pump": DeviceConfig(
        device_type="heat_pump",
        identifier_patterns=["LG*"],
        name_patterns=["eco", "elyo"],
        family_patterns=["eco elyo"],
        model_patterns=["astralpool"],
        components_range=70,  # Extended range pour capturer températures
        required_components=[0, 1, 2, 3, 13, 14, 19],  # Components essentiels LG
        entities=["climate", "switch", "sensor_info"],
        features={
            "preset_modes": True,
            "temperature_control": True,
            "hvac_modes": ["off", "heat"],
            "skip_auto_mode": True,  # LG a ses propres modes smart
            "skip_schedules": True,  # Pas de schedules pour LG
        },
        priority=100,  # Haute priorité car très spécifique
    ),

    "e30iq_pump": DeviceConfig(
        device_type="pump",
        identifier_patterns=["E30*", "PUMP*"],  # Patterns génériques pompes
        components_range=25,  # Inclut component 20 (schedules) et 21 (network)
        required_components=[0, 1, 2, 3, 9, 10, 11, 20],  # Components essentiels E30iQ
        entities=["switch", "switch_auto", "select", "number", "sensor_speed", "sensor_schedule", "sensor_info", "time"],
        features={
            "auto_mode": True,
            "speed_control": True,
            "schedules": True,
            "schedule_count": 8,  # 8 programmations possibles
        },
        priority=50,  # Priorité moyenne
    ),

    "generic_heat_pump": DeviceConfig(
        device_type="heat_pump",
        components_range=70,
        required_components=[0, 1, 2, 3, 13],
        entities=["climate", "switch", "sensor_info"],
        features={
            "temperature_control": True,
            "hvac_modes": ["off", "heat"],
        },
        priority=30,  # Plus bas que LG
    ),

    "generic_pump": DeviceConfig(
        device_type="pump",
        components_range=25,
        required_components=[0, 1, 2, 3, 9],
        entities=["switch", "switch_auto", "sensor_info"],
        features={
            "auto_mode": True,
        },
        priority=10,  # Fallback générique
    ),

    "generic_heater": DeviceConfig(
        device_type="heater",
        components_range=25,
        entities=["switch", "sensor_temperature"],
        features={},
        priority=20,
    ),

    "generic_light": DeviceConfig(
        device_type="light",
        components_range=15,
        entities=["switch", "sensor_brightness"],
        features={},
        priority=20,
    ),
}


class DeviceIdentifier:
    """Helper to identify device type from device data."""

    @staticmethod
    def _matches_pattern(value: str, patterns: List[str]) -> bool:
        """Check if value matches any pattern (supports * wildcard)."""
        if not value or not patterns:
            return False

        value_lower = value.lower()
        for pattern in patterns:
            pattern_lower = pattern.lower()
            if "*" in pattern_lower:
                # Wildcard matching
                pattern_prefix = pattern_lower.replace("*", "")
                if pattern_lower.endswith("*") and value_lower.startswith(pattern_prefix):
                    return True
                elif pattern_lower.startswith("*") and value_lower.endswith(pattern_prefix):
                    return True
            elif pattern_lower in value_lower:
                return True
        return False

    @staticmethod
    def _check_component_signature(device: dict, component_id: int, value_patterns: List[str]) -> bool:
        """Check if a specific component contains expected values."""
        try:
            components = device.get("components", {})
            if isinstance(components, dict) and str(component_id) in components:
                component = components[str(component_id)]
                reported_value = str(component.get("reportedValue", ""))
                return DeviceIdentifier._matches_pattern(reported_value, value_patterns)
        except (AttributeError, TypeError):
            pass
        return False

    @staticmethod
    def identify_device(device: dict) -> Optional[DeviceConfig]:
        """Identify device type and return its configuration.

        Returns the best matching DeviceConfig based on priority and matching criteria.
        """
        if not isinstance(device, dict):
            return None

        device_id = device.get("device_id", "")
        device_name = device.get("name", "")
        family = device.get("family", "")
        model = device.get("model", "")
        device_type_hint = device.get("type", "").lower()

        # Sort configs by priority (highest first)
        sorted_configs = sorted(
            DEVICE_CONFIGS.items(),
            key=lambda x: x[1].priority,
            reverse=True
        )

        best_match = None
        best_score = 0

        for config_name, config in sorted_configs:
            score = 0

            # Check identifier patterns
            if DeviceIdentifier._matches_pattern(device_id, config.identifier_patterns):
                score += 50

            # Check name patterns
            if DeviceIdentifier._matches_pattern(device_name, config.name_patterns):
                score += 30

            # Check family patterns
            if DeviceIdentifier._matches_pattern(family, config.family_patterns):
                score += 20

            # Check model patterns
            if DeviceIdentifier._matches_pattern(model, config.model_patterns):
                score += 20

            # Check if device_type hint matches
            if config.device_type in device_type_hint:
                score += 10

            # Special case: LG heat pump signature (component 7 with BXWAA)
            if config_name == "lg_heat_pump":
                if DeviceIdentifier._check_component_signature(device, 7, ["BXWAA"]):
                    score += 100  # Very strong indicator

            # Update best match if this score is higher
            if score > best_score:
                best_score = score
                best_match = config

        # Fallback to generic configs if no strong match
        if best_score < 10:
            # Use device type hint for fallback
            if "heat_pump" in device_type_hint or "heat" in device_type_hint:
                return DEVICE_CONFIGS.get("generic_heat_pump")
            elif "pump" in device_type_hint:
                return DEVICE_CONFIGS.get("generic_pump")
            elif "heater" in device_type_hint:
                return DEVICE_CONFIGS.get("generic_heater")
            elif "light" in device_type_hint:
                return DEVICE_CONFIGS.get("generic_light")

        return best_match

    @staticmethod
    def should_create_entity(device: dict, entity_type: str) -> bool:
        """Check if a specific entity type should be created for this device."""
        config = DeviceIdentifier.identify_device(device)
        if not config:
            return False
        return entity_type in config.entities

    @staticmethod
    def get_components_range(device: dict) -> int:
        """Get the component scan range for this device."""
        config = DeviceIdentifier.identify_device(device)
        if not config:
            return 25  # Default
        return config.components_range

    @staticmethod
    def has_feature(device: dict, feature_name: str) -> bool:
        """Check if device supports a specific feature."""
        config = DeviceIdentifier.identify_device(device)
        if not config:
            return False
        return config.features.get(feature_name, False)

    @staticmethod
    def get_feature(device: dict, feature_name: str, default: Any = None) -> Any:
        """Get a feature value for this device."""
        config = DeviceIdentifier.identify_device(device)
        if not config:
            return default
        return config.features.get(feature_name, default)