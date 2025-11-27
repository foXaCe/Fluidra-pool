"""Device registry for Fluidra Pool equipment types.

This module centralizes device configurations to make adding new equipment easier
and reduce the risk of breaking existing devices.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List


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
        components_range=5,  # Minimal scan, specific components below
        required_components=[0, 1, 2, 3],
        entities=["climate", "switch", "sensor_info"],
        features={
            "preset_modes": True,
            "temperature_control": True,
            "hvac_modes": ["off", "heat"],
            "skip_auto_mode": True,
            "skip_schedules": True,
            "specific_components": [13, 14, 15, 19],  # ON/OFF, target temp, current temp, water temp
        },
        priority=100,
    ),
    "z250iq_heat_pump": DeviceConfig(
        device_type="heat_pump",
        identifier_patterns=["LF*"],
        name_patterns=["z250", "z25"],
        family_patterns=["heat pump"],
        components_range=5,  # Minimal scan, specific components below
        required_components=[0, 1, 2, 3],
        entities=["climate", "switch", "sensor_info"],
        features={
            "preset_modes": True,
            "temperature_control": True,
            "hvac_modes": ["off", "heat"],
            "skip_auto_mode": True,
            "skip_schedules": True,
            "specific_components": [13, 14, 15, 19],  # ON/OFF, target temp, current temp, water temp
        },
        priority=95,
    ),
    "chlorinator": DeviceConfig(
        device_type="chlorinator",
        identifier_patterns=["*.nn_*"],  # Bridged devices
        family_patterns=["chlorinator"],
        components_range=25,  # Scan only basic components, specific ones added below
        required_components=[0, 1, 2, 3],  # Basic device info
        entities=["switch", "select", "number", "sensor_info"],
        features={
            "chlorination_level": {"write": 4, "read": 164},  # Component 4 (write) / 164 (read)
            "mode_control": True,  # Component 20: 0=OFF, 1=ON, 2=AUTO
            "ph_setpoint": {"write": 8, "read": 172},  # Component 8 (write) / 172 (read)
            "orp_setpoint": {"write": 11, "read": 177},  # Component 11 (write) / 177 (read)
            "boost_mode": 245,  # Component 245
            "sensors": {
                "ph": 172,  # pH reading
                "orp": 177,  # ORP/Redox (mV)
                "free_chlorine": 178,  # Free chlorine (mg/l)
                "temperature": 183,  # Pool temperature (°C * 10)
                "salinity": 185,  # Salinity (g/L * 100)
            },
            # List specific components to scan for chlorinator (avoids scanning 0-300)
            "specific_components": [4, 8, 11, 20, 164, 172, 177, 178, 183, 185, 245],
        },
        priority=80,  # Haute priorité pour éviter confusion avec pumps
    ),
    "cc24033907_chlorinator": DeviceConfig(
        device_type="chlorinator",
        identifier_patterns=["CC24033907*"],  # Specific CC24033907 model
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "number", "sensor_info"],  # No select for mode, but sensors enabled
        features={
            "chlorination_level": 10,  # Component 10 (0-100%, values rounded to multiples of 10)
            "ph_setpoint": 16,  # Component 16 (÷100)
            "orp_setpoint": 20,  # Component 20 (mV)
            "boost_mode": 103,  # Component 103 (boolean)
            "skip_mode_select": True,  # No mode select available
            "sensors": {
                "ph": 165,  # pH measured value (÷100) - e.g., 712 = 7.12 pH
                "orp": 170,  # ORP/Redox measured value (mV) - e.g., 779 mV
                "temperature": 172,  # Pool temperature (°C * 10) - Component 172 for CC24033907 (e.g., 136 = 13.6°C)
                "salinity": 174,  # Salinity (g/L * 100) - Component 174 like LC24008313
            },
            # Specific components for CC24033907
            "specific_components": [10, 16, 20, 103, 165, 170, 172, 174],
        },
        priority=85,  # Higher than generic chlorinator
    ),
    "lc24008313_chlorinator": DeviceConfig(
        device_type="chlorinator",
        identifier_patterns=["LC24008313*"],  # Blauswim chlorinator (I.D. Electroquimica/Fluidra)
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "number", "sensor_info"],  # Sensors enabled
        features={
            "chlorination_level": 10,  # Component 10 (0-100%)
            "ph_setpoint": 16,  # Component 16 (÷100)
            "orp_setpoint": 20,  # Component 20 (mV)
            "boost_mode": 103,  # Component 103 (boolean: true/false)
            "skip_mode_select": True,  # No mode select available
            "sensors": {
                "ph": 165,  # pH measured value (÷100) - e.g., 731 = 7.31 pH
                "orp": 170,  # ORP/Redox measured value (mV) - e.g., 688 mV
                "temperature": 172,  # Pool temperature (°C * 10) - Component 172 = 201 → 20.1°C
                "salinity": 174,  # Salinity (g/L * 100) - Component 174 = 536 → 5.36 g/L
            },
            # Specific components for LC24008313
            "specific_components": [10, 16, 20, 103, 165, 170, 172, 174],
        },
        priority=86,  # Higher than CC24033907 for more specific match
    ),
    "cc24018202_chlorinator": DeviceConfig(
        device_type="chlorinator",
        identifier_patterns=["CC24018202*"],  # Specific CC24018202 model
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "number", "sensor_info"],  # Sensors enabled
        features={
            "chlorination_level": 10,  # Component 10 (0-100%)
            "ph_setpoint": 16,  # Component 16 (÷100)
            "orp_setpoint": 20,  # Component 20 (mV)
            "boost_mode": 103,  # Component 103 (boolean: true/false)
            "skip_mode_select": True,  # No mode select available
            "sensors": {
                "ph": 165,  # pH measured value (÷100) - e.g., 727 = 7.27 pH
                "orp": 170,  # ORP/Redox measured value (mV) - e.g., 768 mV
                "temperature": 172,  # Pool temperature (°C * 10) - e.g., 255 = 25.5°C
                "salinity": 174,  # Salinity (g/L * 100) - Component 174
                "free_chlorine": 178,  # Free chlorine (mg/L) - Component 178
            },
            # Specific components for CC24018202
            "specific_components": [10, 16, 20, 103, 165, 170, 172, 174, 178],
        },
        priority=87,  # Higher than LC24008313 for more specific match
    ),
    "cc25113623_chlorinator": DeviceConfig(
        device_type="chlorinator",
        identifier_patterns=["CC25113623*"],  # Specific CC25113623 model
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "number", "sensor_info"],  # No select for mode, but sensors enabled
        features={
            "chlorination_level": 10,  # Component 10 (0-100%, values rounded to multiples of 10)
            "ph_setpoint": 16,  # Component 16 (÷100)
            "orp_setpoint": 20,  # Component 20 (mV)
            "boost_mode": 103,  # Component 103 (boolean)
            "skip_mode_select": True,  # No mode select available
            "sensors": {
                "ph": 165,  # pH measured value (÷100) - e.g., 716 = 7.16 pH
                "orp": 170,  # ORP/Redox measured value (mV) - e.g., 681 = 681 mV
                "temperature": 172,  # Pool temperature (°C * 10) - Component 172 = 291 → 29.1°C
                "salinity": 174,  # Salinity (g/L * 100) - Component 174 = 570 → 5.70 g/L
            },
            # Specific components for CC25113623
            "specific_components": [10, 16, 20, 103, 165, 170, 172, 174],
        },
        priority=87,  # Higher than LC24008313 for more specific match
    ),
    "cc24021110_chlorinator": DeviceConfig(
        device_type="chlorinator",
        identifier_patterns=["CC24021110*"],  # Specific CC24021110 model
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "number", "sensor_info"],  # No select for mode, but sensors enabled
        features={
            "chlorination_level": 10,  # Component 10 (0-100%)
            "ph_setpoint": 16,  # Component 16 (÷100)
            "orp_setpoint": 20,  # Component 20 (mV)
            "boost_mode": 103,  # Component 103 (boolean: true/false)
            "skip_mode_select": True,  # No mode select available
            "sensors": {
                "ph": 165,  # pH measured value (÷100) - e.g., 741 = 7.41 pH
                "orp": 170,  # ORP/Redox measured value (mV) - e.g., 791 mV
                "temperature": 172,  # Pool temperature (°C * 10) - Component 172 = 216 → 21.6°C
                "salinity": 174,  # Salinity (g/L * 100) - Component 174 (may need adjustment)
            },
            # Specific components for CC24021110
            "specific_components": [10, 16, 20, 103, 165, 170, 172, 174],
        },
        priority=88,  # Higher than CC25113623 for more specific match
    ),
    "cc25002928_chlorinator": DeviceConfig(
        device_type="chlorinator",
        identifier_patterns=["CC25002928*"],  # Specific CC25002928 model (Energy Connect 21 Scalable)
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "number", "sensor_info"],  # No select for mode, but sensors enabled
        features={
            "chlorination_level": 10,  # Component 10 (0-100%) - e.g., 40 = 40%
            "ph_setpoint": 16,  # Component 16 (÷100) - e.g., 720 = 7.20 pH
            "orp_setpoint": 20,  # Component 20 (mV) - e.g., 720 = 720 mV
            "boost_mode": 103,  # Component 103 (boolean: true/false)
            "skip_mode_select": True,  # No mode select available
            "sensors": {
                "ph": 165,  # pH measured value (÷100) - e.g., 719 = 7.19 pH
                "orp": 170,  # ORP/Redox measured value (mV) - e.g., 729 = 729 mV
                "temperature": 172,  # Pool temperature (°C × 10) - e.g., 195 = 19.5°C
                "salinity": 174,  # Salinity (g/L × 100) - e.g., 566 = 5.66 g/L
            },
            # Specific components for CC25002928
            "specific_components": [10, 16, 20, 103, 165, 170, 172, 174],
        },
        priority=89,  # Higher than CC24021110 for more specific match
    ),
    "e30iq_pump": DeviceConfig(
        device_type="pump",
        identifier_patterns=["E30*", "PUMP*"],
        components_range=5,  # Minimal scan
        required_components=[0, 1, 2, 3],
        entities=[
            "switch",
            "switch_auto",
            "select",
            "number",
            "sensor_speed",
            "sensor_schedule",
            "sensor_info",
            "time",
        ],
        features={
            "auto_mode": True,
            "speed_control": True,
            "schedules": True,
            "schedule_count": 8,
            "specific_components": [9, 10, 11, 15, 20, 21],  # ON/OFF, auto, speed, speed%, schedules, network
        },
        priority=50,
    ),
    "generic_heat_pump": DeviceConfig(
        device_type="heat_pump",
        components_range=5,
        required_components=[0, 1, 2, 3],
        entities=["climate", "switch", "sensor_info"],
        features={
            "temperature_control": True,
            "hvac_modes": ["off", "heat"],
            "specific_components": [13, 14, 15],
        },
        priority=30,
    ),
    "generic_pump": DeviceConfig(
        device_type="pump",
        components_range=5,
        required_components=[0, 1, 2, 3],
        entities=["switch", "switch_auto", "sensor_info"],
        features={
            "auto_mode": True,
            "specific_components": [9, 10],
        },
        priority=10,
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
        components_range=45,  # Include component 40 for light schedules
        entities=["switch", "sensor_brightness", "time"],
        features={
            "specific_components": [40, 45],  # Scheduler (40), RGBW color (45)
        },
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

        import re

        value_lower = value.lower()
        for pattern in patterns:
            pattern_lower = pattern.lower()
            if "*" in pattern_lower:
                # Convert wildcard pattern to regex
                # Escape special regex chars except *
                regex_pattern = re.escape(pattern_lower).replace(r"\*", ".*")
                regex_pattern = f"^{regex_pattern}$"
                if re.match(regex_pattern, value_lower):
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
    def identify_device(device: dict) -> DeviceConfig | None:
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

        # Skip bridges - they are not controllable devices
        if family and "bridge" in family.lower():
            return None

        # Sort configs by priority (highest first)
        sorted_configs = sorted(DEVICE_CONFIGS.items(), key=lambda x: x[1].priority, reverse=True)

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
            if "pump" in device_type_hint:
                return DEVICE_CONFIGS.get("generic_pump")
            if "heater" in device_type_hint:
                return DEVICE_CONFIGS.get("generic_heater")
            if "light" in device_type_hint:
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
