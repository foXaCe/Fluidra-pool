"""Device registry for Fluidra Pool equipment types.

This module centralizes device configurations to make adding new equipment easier
and reduce the risk of breaking existing devices.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
import re
from typing import Any


@dataclass
class DeviceConfig:
    """Configuration for a specific device type."""

    device_type: str  # Type général: pump, heat_pump, heater, light
    identifier_patterns: list[str] = field(default_factory=list)  # Patterns d'identification (ex: ["LG*"])
    name_patterns: list[str] = field(default_factory=list)  # Patterns dans le nom
    family_patterns: list[str] = field(default_factory=list)  # Patterns dans la famille
    model_patterns: list[str] = field(default_factory=list)  # Patterns dans le modèle

    # Components to scan
    components_range: int = 25  # Number of components to scan
    required_components: list[int] = field(default_factory=list)  # Required components

    # Entités Home Assistant à créer
    entities: list[str] = field(default_factory=list)  # Types d'entités: switch, sensor, climate, etc.

    # Features spécifiques
    features: dict[str, Any] = field(default_factory=dict)

    # Priorité de détection (plus haut = vérifié en premier)
    priority: int = 0


# Configuration centralisée des équipements
DEVICE_CONFIGS: dict[str, DeviceConfig] = {
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
            "specific_components": [
                7,
                13,
                14,
                15,
                19,
            ],  # 7=signature (BXWAA), ON/OFF, target temp, current temp, water temp
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
            "specific_components": [
                7,
                13,
                14,
                15,
                19,
            ],  # 7=signature (for differentiation), ON/OFF, target temp, current temp, water temp
        },
        priority=95,
    ),
    "z260iq_heat_pump": DeviceConfig(
        device_type="heat_pump",
        identifier_patterns=["LF*"],
        family_patterns=["heat pump"],
        components_range=5,  # Minimal scan, specific components below
        required_components=[0, 1, 2, 3],
        entities=[
            "climate",
            "switch",
            "sensor_info",
            "sensor_temperature",
            "sensor_running_hours",
            "binary_sensor_no_flow",
        ],
        features={
            "z260iq_mode": True,  # Flag for Z260iQ-specific handling
            "preset_modes": True,
            "temperature_control": True,
            "hvac_modes": ["off", "heat", "cool", "heat_cool"],
            "skip_auto_mode": True,
            "skip_schedules": True,
            "min_temp": 7.0,
            "max_temp": 40.0,
            "temp_step": 1.0,
            # Component mappings:
            # - 0:  Running hours (raw integer, unit h) — read-only
            # - 13: ON/OFF (0=OFF, 1=ON)
            # - 14: Operation mode / preset (0=Smart Heating, 1=Smart Cooling,
            #        2=Smart H+C, 3=Boost Heating, 4=Silence Heating,
            #        5=Boost Cooling, 6=Silence Cooling) — same values as LG
            # - 15: Setpoint temperature (×0.1, e.g. 260=26.0°C)
            # - 17: Device status (0=OK, 7=Error) — read-only info
            # - 19: Water temperature (×0.1)
            # - 28: No-flow alarm (0=OK, 1=No Flow)
            # - 67: Air temperature (×0.1)
            # - 81: Min setpoint (15°C, informational)
            # - 82: Max setpoint (40°C, informational)
            "specific_components": [0, 7, 13, 14, 15, 17, 19, 28, 67, 81, 82],
        },
        priority=97,  # Higher than z250iq (95) and z550iq (96); component-7 check elevates further
    ),
    "z550iq_heat_pump": DeviceConfig(
        device_type="heat_pump",
        identifier_patterns=["LD*"],
        name_patterns=["z550", "z55"],
        family_patterns=["heat pump"],
        components_range=5,  # Minimal scan, specific components below
        required_components=[0, 1, 2, 3],
        entities=["climate", "switch", "sensor_info", "sensor_temperature"],
        features={
            "temperature_control": True,
            "preset_modes": True,  # Enable preset modes (silence, smart, boost)
            "hvac_modes": ["off", "heat", "cool", "auto"],
            "skip_auto_mode": True,
            "skip_schedules": True,
            "z550_mode": True,  # Flag for Z550iQ+ specific mode handling
            # Component mappings for Z550iQ+:
            # - 21: ON/OFF (0=OFF, 1=ON)
            # - 15: Temperature setpoint (decidegrees, 290=29.0°C)
            # - 16: Mode (0=heating, 1=cooling, 2=auto)
            # - 17: Preset mode (0=silence, 1=smart, 2=boost) - to be confirmed
            # - 37: Water temperature (decidegrees)
            # - 40: Air temperature (decidegrees)
            # - 61: State (0=idle, 2=heating, 3=cooling, 11=no flow)
            "on_off_component": 21,
            "setpoint_component": 15,
            "mode_component": 16,
            "preset_component": 17,  # Preset mode component - to be confirmed
            "water_temp_component": 37,
            "air_temp_component": 40,
            "state_component": 61,
            "specific_components": [15, 16, 17, 21, 37, 40, 61],
        },
        priority=96,  # Higher than z250iq
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
    "lc24019518_chlorinator": DeviceConfig(
        device_type="chlorinator",
        identifier_patterns=["LC24019518*"],  # Issue #21 - jaf69's chlorinator
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
                "ph": 165,  # pH measured value (÷100)
                "orp": 170,  # ORP/Redox measured value (mV)
                "temperature": 172,  # Pool temperature (°C * 10)
                "salinity": 174,  # Salinity (g/L * 100)
            },
            # Specific components for LC24019518
            "specific_components": [10, 16, 20, 103, 165, 170, 172, 174],
        },
        priority=86,  # Same priority as LC24008313 (similar model)
    ),
    "lc24013306_chlorinator": DeviceConfig(
        device_type="chlorinator",
        identifier_patterns=["LC24013306*"],  # Irripool iSALT chlorinator - Issue #31
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
                "ph": 165,  # pH measured value (÷100)
                "orp": 170,  # ORP/Redox measured value (mV)
                "temperature": 172,  # Pool temperature (°C * 10)
                "salinity": 174,  # Salinity (g/L * 100)
            },
            # Specific components for LC24013306
            "specific_components": [10, 16, 20, 103, 165, 170, 172, 174],
        },
        priority=86,  # Same priority as other LC chlorinators
    ),
    "cc24009711_chlorinator": DeviceConfig(
        device_type="chlorinator",
        # AstralPool Clear Connect Scalable 21 G/H (tecnoLC2) — Issue #55
        # Bridge CC24009711 with child device CC24009711.nn_1
        identifier_patterns=["CC24009711.nn_*"],
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "number", "sensor_info"],
        features={
            "chlorination_level": 10,  # Component 10 (0-100%) — e.g., 100 = 100%
            "orp_setpoint": 20,  # Component 20 (mV) — matches ORP target (e.g., 700)
            "skip_mode_select": True,  # No mode select on this model
            "sensors": {
                "ph": 16,  # pH measured (÷100) — e.g., 750 = 7.50 pH
                "orp": 177,  # ORP/Redox measured (mV)
                "temperature": 172,  # Pool temperature (°C × 10) — e.g., 236 = 23.6°C
            },
            # Specific components for CC24009711 (no salinity / free-chlorine exposed)
            "specific_components": [10, 16, 20, 172, 177],
        },
        priority=88,
    ),
    "lc25000122_chlorinator": DeviceConfig(
        device_type="chlorinator",
        # IrriPool iSalt tecnoLC2 bridge; child device ID is LC25000122.nn_1 (Issue #53)
        identifier_patterns=["LC25000122.nn_*"],
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "number", "sensor_info"],
        features={
            "chlorination_level": 10,  # Component 10 (0-100%) — e.g., 40 = 40%
            "ph_setpoint": 16,  # Component 16 (÷100) — e.g., 750 = 7.50 pH (confirmed setpoint)
            "orp_setpoint": 20,  # Component 20 (mV) — e.g., 740 mV
            "skip_mode_select": True,  # No mode select on this model
            "sensors": {
                # Measured values: components 165/170 if the device exposes them
                # (component 177 is NOT the measured ORP on this model — stays at 782
                # while the mobile app shows 733 mV). Keep only what we know is correct.
                "temperature": 172,  # Pool temperature (°C × 10) — e.g., 196 = 19.6°C
            },
            # Scan a broader range to discover where measured pH/ORP live (not in 0-24)
            "specific_components": [10, 16, 20, 165, 170, 172, 174, 177, 178, 183, 185],
        },
        priority=87,  # Higher than generic *.nn_* (80) and the LC2 family (86)
    ),
    "lc24015802_chlorinator": DeviceConfig(
        device_type="chlorinator",
        # LC24015802 is a Tecno LC2 bridge; its child device ID is LC24015802.nn_1
        identifier_patterns=["LC24015802.nn_*"],
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "number", "sensor_info"],
        features={
            "chlorination_level": 10,  # Component 10 (0-100%)
            "ph_setpoint": 16,  # Component 16 (÷100) — e.g., 740 = 7.40 pH
            "orp_setpoint": 20,  # Component 20 (mV)
            "boost_mode": 103,  # Component 103 (boolean: true/false)
            "skip_mode_select": True,  # No mode select (tecnoLC2 bridge style)
            "sensors": {
                "ph": 165,  # pH measured (÷100)
                "orp": 170,  # ORP/Redox measured (mV)
                "temperature": 172,  # Pool temperature (°C × 10)
                "salinity": 174,  # Salinity (g/L × 100)
            },
            # Same component set as iSALT LC24013306 (same tecnoLC2 thingType)
            "specific_components": [10, 16, 20, 103, 165, 170, 172, 174],
        },
        priority=87,  # Higher than generic *.nn_* (80) and other LC models (86)
    ),
    "lc24056317_chlorinator": DeviceConfig(
        device_type="chlorinator",
        identifier_patterns=["LC24056317*"],  # Gre chlorinator (I.D. Electroquimica/Fluidra) - Issue #28
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "number", "sensor_info"],  # No ORP on this model
        features={
            "chlorination_level": 10,  # Component 10 (0-100%)
            "ph_setpoint": 16,  # Component 16 (÷100)
            "boost_mode": 103,  # Component 103 (boolean: true/false)
            "skip_mode_select": True,  # No mode select available
            "sensors": {
                "ph": 165,  # pH measured value (÷100) - e.g., 731 = 7.31 pH
                "temperature": 172,  # Pool temperature (°C * 10) - e.g., 201 → 20.1°C
                "salinity": 174,  # Salinity (g/L * 100) - e.g., 536 → 5.36 g/L
            },
            # Specific components for LC24056317
            "specific_components": [10, 16, 103, 165, 172, 174],
        },
        priority=86,  # Same priority as other LC chlorinators
    ),
    "lc25007119_chlorinator": DeviceConfig(
        device_type="chlorinator",
        identifier_patterns=["LC25007119*"],
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
                "ph": 165,  # pH measured value (÷100)
                "orp": 170,  # ORP/Redox measured value (mV)
                "temperature": 172,  # Pool temperature (°C * 10)
                "salinity": 174,  # Salinity (g/L * 100)
            },
            # Specific components for LC25007119
            "specific_components": [10, 16, 20, 103, 165, 170, 172, 174],
        },
        priority=86,  # Same priority as LC24008313 (similar model)
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
    "cc24042517_chlorinator": DeviceConfig(
        device_type="chlorinator",
        identifier_patterns=["CC24042517*"],  # AstralPool Clear Connect Evo 21g (Issue #51)
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
                "ph": 165,  # pH measured value (÷100) - e.g., 680 = 6.80 pH
                "orp": 170,  # ORP/Redox measured value (mV) - e.g., 692 mV
                "temperature": 172,  # Pool temperature (°C × 10) - e.g., 190 = 19.0°C
                "salinity": 174,  # Salinity (g/L × 100) - e.g., 432 = 4.32 g/L
                "free_chlorine": 178,  # Free chlorine (mg/L ÷ 100)
            },
            # Specific components for CC24042517
            "specific_components": [10, 16, 20, 103, 165, 170, 172, 174, 178],
        },
        priority=88,  # Same level as other CC-specific models; identifier_patterns disambiguates
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
    "cc25013923_chlorinator": DeviceConfig(
        device_type="chlorinator",
        identifier_patterns=["CC25013923*"],  # Specific CC25013923 model (joaopg - Issue #14)
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "number", "sensor_info"],  # No select for mode, but sensors enabled
        features={
            "chlorination_level": 10,  # Component 10 (0-100%) - e.g., 70 = 70%
            "ph_setpoint": 16,  # Component 16 (÷100) - e.g., 720 = 7.20 pH
            "orp_setpoint": 20,  # Component 20 (mV) - e.g., 650 = 650 mV
            "boost_mode": 103,  # Component 103 (boolean: true/false)
            "skip_mode_select": True,  # No mode select available
            "sensors": {
                "ph": 165,  # pH measured value (÷100) - e.g., 720 = 7.20 pH
                "orp": 170,  # ORP/Redox measured value (mV) - e.g., 733 = 733 mV
                "temperature": 172,  # Pool temperature (°C × 10) - e.g., 117 = 11.7°C
                "salinity": 174,  # Salinity (g/L × 100) - e.g., 244 = 2.44 g/L
            },
            # Specific components for CC25013923
            "specific_components": [10, 16, 20, 103, 165, 170, 172, 174],
        },
        priority=90,  # Higher than CC25002928 for more specific match
    ),
    "cc25005502_chlorinator": DeviceConfig(
        device_type="chlorinator",
        identifier_patterns=["CC25005502*"],  # Specific CC25005502 model (alextoro82 - Issue #15)
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "number", "sensor_info"],  # No pH/ORP on this model
        features={
            "chlorination_level": 10,  # Component 10 (0-100%) - e.g., 80 = 80%
            "boost_mode": 103,  # Component 103 (boolean: true/false)
            "skip_mode_select": True,  # No mode select available
            "skip_ph_orp": True,  # No pH/ORP sensors on this model
            "skip_firmware": True,  # Firmware value not meaningful for this model
            "sensors": {
                "temperature": 172,  # Pool temperature (°C × 10) - e.g., 66 = 6.6°C
                "salinity": 174,  # Salinity (g/L × 100) - e.g., 340 = 3.40 g/L
                "chlorination_actual": 154,  # Actual chlorination production (%) - e.g., 70 = 70%
            },
            # Specific components for CC25005502 (no pH/ORP)
            "specific_components": [10, 103, 154, 172, 174],
        },
        priority=91,  # Higher than CC25013923 for more specific match
    ),
    "cc_energy_connect_bridged_chlorinator": DeviceConfig(
        device_type="chlorinator",
        # Energy Connect bridged tecnoLC2 devices - Issue #36
        # Confirmed on: CC24054221 (cortalys), CC24041107 (StenGarny)
        identifier_patterns=["CC24054221*", "CC24041107*"],
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "number", "sensor_info"],  # ON/OFF switch + sensors
        features={
            "on_off_component": 0,  # Component 0 = ON/OFF (1=ON, 0=OFF)
            "chlorination_level": 10,  # Component 10 (write, 0-100%)
            "ph_setpoint": 157,  # Component 157 (÷10, e.g., 72 → 7.2 pH)
            "ph_setpoint_divisor": 10,  # This device uses ÷10 (not ÷100)
            "skip_mode_select": True,  # No mode select
            "skip_ph_orp": True,  # No ORP probe
            "sensors": {
                "ph": 165,  # pH measured value (÷100) - e.g., 686 → 6.86 pH
                "temperature": 172,  # Pool temperature (÷10) - e.g., 136 → 13.6°C
                "salinity": 160,  # Salinity (÷1000) - e.g., 3580 → 3.58 g/L
                "chlorination_actual": 154,  # Current production (%) - e.g., 60 = 60%
            },
            "sensor_divisors": {
                "salinity": 1000,  # This device reports salinity in mg/L (÷1000 for g/L)
            },
            "specific_components": [0, 10, 152, 154, 157, 160, 165, 172],
        },
        priority=93,  # Higher than CC24068402
    ),
    "cc24058902_chlorinator": DeviceConfig(
        device_type="chlorinator",
        identifier_patterns=["CC24058902*"],  # Issue #35 - Enkil13
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
                "ph": 165,  # pH measured value (÷100)
                "orp": 177,  # ORP/Redox measured value (mV) - c177 for this model
                "free_chlorine": 178,  # Free chlorine (mg/L)
                "temperature": 172,  # Pool temperature (°C * 10)
                "salinity": 174,  # Salinity (g/L * 100)
            },
            # Specific components for CC24058902
            "specific_components": [10, 16, 20, 103, 165, 172, 174, 177, 178],
        },
        priority=93,  # Higher than CC24068402
    ),
    "cc24068402_chlorinator": DeviceConfig(
        device_type="chlorinator",
        identifier_patterns=["CC24068402*"],  # Energy Connect tecnoLC2 (with pH/ORP) - Issue #33
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "number", "sensor_info"],  # Sensors enabled
        features={
            "chlorination_level": 10,  # Component 10 (0-100%) - e.g., 100 = 100%
            "ph_setpoint": 16,  # Component 16 (÷100) - e.g., 740 = 7.40 pH
            "orp_setpoint": 20,  # Component 20 (mV)
            "boost_mode": 103,  # Component 103 (boolean: true/false)
            "skip_mode_select": True,  # No mode select available
            "sensors": {
                "ph": 165,  # pH measured value (÷100) - e.g., 680 = 6.80 pH
                "orp": 170,  # ORP/Redox measured value (mV)
                "temperature": 172,  # Pool temperature (°C * 10) - 144 = 14.4°C
                "salinity": 174,  # Salinity (g/L * 100)
                "chlorination_actual": 154,  # Actual production (%)
            },
            # Specific components for CC24068402
            "specific_components": [10, 16, 20, 103, 154, 165, 170, 172, 174],
        },
        priority=92,  # Higher than CC25005502 (91)
    ),
    "cc24017504_chlorinator": DeviceConfig(
        device_type="chlorinator",
        identifier_patterns=["CC24017504*"],  # Energy Connect tecnoLC2 (with pH/ORP) - nicolasp
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "number", "sensor_info"],  # Sensors enabled
        features={
            "chlorination_level": 10,  # Component 10 (0-100%) - e.g., 80 = 80%
            "ph_setpoint": 16,  # Component 16 (÷100) - e.g., 760 = 7.60 pH
            "orp_setpoint": 20,  # Component 20 (mV) - e.g., 700 = 700 mV
            "boost_mode": 103,  # Component 103 (boolean: true/false)
            "skip_mode_select": True,  # No mode select available
            "sensors": {
                "ph": 165,  # pH measured value (÷100) - e.g., 760 = 7.60 pH
                "orp": 170,  # ORP/Redox measured value (mV) - e.g., 669 mV
                "temperature": 172,  # Pool temperature (°C * 10) - e.g., 138 = 13.8°C
                "salinity": 174,  # Salinity (g/L * 100) - e.g., 480 = 4.80 g/L
            },
            # Specific components for CC24017504
            "specific_components": [10, 16, 20, 103, 165, 170, 172, 174],
        },
        priority=92,  # Same as CC24068402 (similar model)
    ),
    "cc24000304_chlorinator": DeviceConfig(
        device_type="chlorinator",
        # Basic Energy Connect tecnoLC2 without pH/ORP probes (Issue #52, @Srekcah007)
        # Matches both the bridge (CC24000304) and the bridged child (CC24000304.nn_1)
        identifier_patterns=["CC24000304*"],
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "number", "sensor_info"],  # No pH/ORP on this model
        features={
            "chlorination_level": 164,  # Component 164 (0-100%)
            "boost_mode": 245,  # Component 245 (boolean: true/false)
            "skip_mode_select": True,  # No mode select
            "skip_ph_orp": True,  # No pH/ORP probes
            "sensors": {
                "temperature": 172,  # Pool temperature (°C × 10)
                "salinity": 185,  # Salinity (g/L × 100)
            },
            # Specific components for CC24000304 (no pH/ORP)
            "specific_components": [164, 172, 185, 245],
        },
        priority=88,  # Higher than generic *.nn_* (80)
    ),
    "cc24042711_chlorinator": DeviceConfig(
        device_type="chlorinator",
        identifier_patterns=["CC24042711*"],  # tecnoLC2 (AstralPool Clear Connect non-scalable) - Issue #25
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "number", "sensor_info"],  # No pH/ORP on this model
        features={
            "chlorination_level": 10,  # Component 10 (0-100%, CC standard)
            "boost_mode": 103,  # Component 103 (boolean, CC standard)
            "skip_mode_select": True,  # No mode select on this model
            "skip_ph_orp": True,  # No pH/ORP probes
            "sensors": {
                "temperature": 172,  # Pool temp (÷10) — confirmed: 144 = 14.4°C
                "salinity": 174,  # Salinity (÷100, CC standard) — e.g., 310 = 3.10 g/L
                "chlorination_actual": 154,  # Actual production (%) — CC standard
            },
            # Specific components for CC24042711 (no pH/ORP)
            "specific_components": [10, 103, 154, 172, 174],
        },
        priority=92,  # Higher than CC25005502 (91)
    ),
    "dm24049704_chlorinator": DeviceConfig(
        device_type="chlorinator",
        identifier_patterns=["DM24049704*"],  # Domotic S2 chlorinator (SheepPool)
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "select", "number", "sensor_info", "time"],  # Full features + schedules
        features={
            "chlorination_level": 4,  # Component 4 (0-100%) - e.g., 100 = 100%
            "mode_control": True,  # Component 20: 0=OFF, 1=ON, 2=AUTO
            "ph_setpoint": 8,  # Component 8 (÷100) - e.g., 740 = 7.40 pH
            "orp_setpoint": 11,  # Component 11 (mV) - e.g., 690 = 690 mV
            "boost_mode": 245,  # Component 245 (boolean: true/false)
            "skip_signal": True,  # Component 2 is not RSSI for this device
            "skip_firmware": True,  # Component 3 is not firmware for this device
            "schedules": True,  # Has schedule support
            "schedule_component": 258,  # Component 258 for schedules
            "schedule_count": 3,  # 3 schedule slots
            "sensors": {
                "ph": 172,  # pH measured value (÷100) - e.g., 738 = 7.38 pH
                "orp": 177,  # ORP/Redox measured value (mV) - e.g., 740 mV
                "temperature": 183,  # Pool temperature (°C × 10) - e.g., 42 = 4.2°C
                "salinity": 185,  # Salinity (g/L) - e.g., 0 = 0 g/L
            },
            # Specific components for DM24049704 (Domotic S2)
            "specific_components": [4, 8, 11, 20, 172, 177, 183, 185, 245, 258],
        },
        priority=92,  # Higher than CC25005502 for specific match
    ),
    "ns25_exo_chlorinator": DeviceConfig(
        device_type="chlorinator",
        identifier_patterns=["NS*"],
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "select", "number", "sensor_info", "time"],
        features={
            "chlorination_level": 38,  # Component 38 (production percentage, 0-100%)
            "chlorination_max": 100,  # EXO uses 0-100% range (same as other chlorinators)
            "chlorination_step": 5,  # Step 5% for EXO
            # boost_mode: NOT supported on EXO (c14 unreadable + API 403 on write)
            "mode_control": True,  # AUTO/ON/OFF mode
            "mode_component": 13,  # Component 13 for mode
            "mode_mapping": {0: "off", 1: "auto", 2: "on"},  # EXO: 1=AUTO (confirmed)
            "orp_setpoint": 39,  # Component 39 (ORP setpoint in mV, e.g. 770 = 770 mV)
            "ph_setpoint": 40,  # Component 40 (÷10) - pH setpoint (72 = 7.2 target)
            "ph_setpoint_divisor": 10,  # EXO uses ÷10 (not ÷100 like CC chlorinators)
            "schedules": True,
            "schedule_count": 4,
            "schedule_component": 20,
            "schedule_output_type": "output",  # "output" = pump/aux1/aux2
            "exo_mode": True,  # Flag for EXO-specific handling
            # on_off_component: removed - mode select (AUTO/ON/OFF) replaces ON/OFF switch
            "sensor_divisors": {
                "salinity": 1000,  # EXO reports salinity in mg/L (2750 = 2.75 g/L)
                "ph": 10,  # EXO reports pH * 10 (69 = 6.9 pH)
                "temperature": 1,  # EXO c64 is direct °C (14 = 14°C)
            },
            "sensors": {
                "ph": 62,  # pH measured (÷10) - e.g., 69 = 6.9 pH
                "orp": 63,  # ORP (mV) - e.g., 738 = 738 mV
                "temperature": 64,  # Water temp (direct °C) - e.g., 14 = 14°C
                "salinity": 36,  # Salinity (÷1000 for g/L) - e.g., 2750 = 2.75 g/L
            },
            # Specific components for NS25 (Zodiac EXO iQ)
            "specific_components": [9, 13, 14, 15, 17, 20, 35, 36, 38, 39, 40, 62, 63, 64],
        },
        priority=85,
    ),
    "e30iq_pump": DeviceConfig(
        device_type="pump",
        identifier_patterns=["E30*", "LE*", "PUMP*"],
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
        entities=["switch", "sensor_brightness", "time", "select"],
        features={
            "specific_components": [18, 40, 45],  # Effect/Scene (18), Scheduler (40), RGBW color (45)
            "effect_select": 18,  # Component 18 for effect/scene selection
        },
        priority=20,
    ),
}


@lru_cache(maxsize=1024)
def _compile_wildcard_pattern(pattern_lower: str) -> re.Pattern[str]:
    """Compile a wildcard pattern (``*`` supported) into a case-insensitive regex."""
    regex = re.escape(pattern_lower).replace(r"\*", ".*")
    return re.compile(f"^{regex}$")


def _match(value: str, patterns: tuple[str, ...]) -> bool:
    """Pure-function equivalent of ``DeviceIdentifier._matches_pattern`` for caching."""
    if not value or not patterns:
        return False
    value_lower = value.lower()
    for pattern in patterns:
        pattern_lower = pattern.lower()
        if "*" in pattern_lower:
            if _compile_wildcard_pattern(pattern_lower).match(value_lower):
                return True
        elif pattern_lower in value_lower:
            return True
    return False


@lru_cache(maxsize=512)
def _identify_device_uncached(
    *,
    device_id: str,
    device_name: str,
    family: str,
    model: str,
    device_type_hint: str,
    comp7_value: str,
) -> DeviceConfig | None:
    """Resolve a :class:`DeviceConfig` from hashable primitives so lru_cache can memoise."""
    sorted_configs = sorted(DEVICE_CONFIGS.items(), key=lambda x: x[1].priority, reverse=True)

    best_match: DeviceConfig | None = None
    best_score = 0

    for config_name, config in sorted_configs:
        score = 0

        if _match(device_id, tuple(config.identifier_patterns)):
            score += 50
        if _match(device_name, tuple(config.name_patterns)):
            score += 30
        if _match(family, tuple(config.family_patterns)):
            score += 20
        if _match(model, tuple(config.model_patterns)):
            score += 20
        if config.device_type in device_type_hint:
            score += 10

        if config_name == "lg_heat_pump" and _match(comp7_value, ("BXWAA",)):
            score += 100

        if config_name == "z260iq_heat_pump":
            if _match(comp7_value, ("BXWAD",)):
                score += 100
            else:
                score = 0

        if score > best_score:
            best_score = score
            best_match = config

    if best_score < 10:
        if "heat_pump" in device_type_hint or "heat" in device_type_hint:
            return DEVICE_CONFIGS.get("generic_heat_pump")
        if "pump" in device_type_hint:
            return DEVICE_CONFIGS.get("generic_pump")
        if "heater" in device_type_hint:
            return DEVICE_CONFIGS.get("generic_heater")
        if "light" in device_type_hint:
            return DEVICE_CONFIGS.get("generic_light")

    return best_match


class DeviceIdentifier:
    """Helper to identify device type from device data."""

    @staticmethod
    def _matches_pattern(value: str, patterns: list[str] | tuple[str, ...]) -> bool:
        """Check if value matches any pattern (supports ``*`` wildcard)."""
        if not value or not patterns:
            return False

        value_lower = value.lower()
        for pattern in patterns:
            pattern_lower = pattern.lower()
            if "*" in pattern_lower:
                if _compile_wildcard_pattern(pattern_lower).match(value_lower):
                    return True
            elif pattern_lower in value_lower:
                return True
        return False

    @staticmethod
    def _check_component_signature(device: dict, component_id: int, value_patterns: list[str]) -> bool:
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
        Results are cached in-place on the device dict to avoid recomputation.
        """
        if not isinstance(device, dict):
            return None

        family = device.get("family", "")
        if family and "bridge" in family.lower():
            return None

        # Cache result on the device itself — the key includes the component-7
        # signature so a signature change (first vs subsequent polls) invalidates it.
        components = device.get("components") if isinstance(device.get("components"), dict) else {}
        comp7_value = ""
        if "7" in components and isinstance(components["7"], dict):
            comp7_value = str(components["7"].get("reportedValue", ""))

        cache_key = (
            device.get("device_id", ""),
            family,
            device.get("model", ""),
            device.get("type", ""),
            comp7_value,
        )
        cache = device.get("_identify_cache")
        if isinstance(cache, dict) and cache.get("key") == cache_key:
            return cache.get("config")

        result = _identify_device_uncached(
            device_id=str(cache_key[0]),
            device_name=device.get("name", ""),
            family=family,
            model=str(cache_key[2]),
            device_type_hint=str(cache_key[3]).lower(),
            comp7_value=comp7_value,
        )
        device["_identify_cache"] = {"key": cache_key, "config": result}
        return result

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
