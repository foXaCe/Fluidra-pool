"""Heat-pump device configurations (LG Eco Elyo, Z250, Z260, Z550)."""

from __future__ import annotations

from ..types import DeviceConfig

HEAT_PUMP_CONFIGS: dict[str, DeviceConfig] = {
    "lg_heat_pump": DeviceConfig(
        device_type="heat_pump",
        identifier_patterns=["LG*"],
        name_patterns=["eco", "elyo"],
        family_patterns=["eco elyo"],
        model_patterns=["astralpool"],
        components_range=5,  # Minimal scan; specific components below.
        required_components=[0, 1, 2, 3],
        entities=["climate", "switch", "sensor_info"],
        features={
            "preset_modes": True,
            "temperature_control": True,
            "hvac_modes": ["off", "heat"],
            "skip_auto_mode": True,
            "skip_schedules": True,
            # 7=signature (BXWAA), 13=ON/OFF, 14=preset, 15=target temp, 19=water temp.
            "specific_components": [7, 13, 14, 15, 19],
        },
        priority=100,
    ),
    "z250iq_heat_pump": DeviceConfig(
        device_type="heat_pump",
        identifier_patterns=["LF*"],
        name_patterns=["z250", "z25"],
        family_patterns=["heat pump"],
        components_range=5,
        required_components=[0, 1, 2, 3],
        entities=["climate", "switch", "sensor_info"],
        features={
            "preset_modes": True,
            "temperature_control": True,
            "hvac_modes": ["off", "heat"],
            "skip_auto_mode": True,
            "skip_schedules": True,
            # 7=signature (for differentiation), 13=ON/OFF, 14=preset, 15=target temp, 19=water temp.
            "specific_components": [7, 13, 14, 15, 19],
        },
        priority=95,
    ),
    "z260iq_heat_pump": DeviceConfig(
        device_type="heat_pump",
        identifier_patterns=["LF*"],
        family_patterns=["heat pump"],
        components_range=5,
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
            "z260iq_mode": True,  # Flag for Z260iQ-specific handling.
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
        priority=97,  # Higher than z250iq (95) and z550iq (96); component-7 check elevates further.
    ),
    "z550iq_heat_pump": DeviceConfig(
        device_type="heat_pump",
        identifier_patterns=["LD*"],
        name_patterns=["z550", "z55"],
        family_patterns=["heat pump"],
        components_range=5,
        required_components=[0, 1, 2, 3],
        entities=["climate", "switch", "sensor_info", "sensor_temperature"],
        features={
            "temperature_control": True,
            # preset_modes disabled: the real preset component is unknown. Writing
            # component 17 returns HTTP 403 (Issue #56). Re-enable once diagnostics
            # from a real Z550iQ+ identify the correct component and value scheme.
            "hvac_modes": ["off", "heat", "cool", "auto"],
            "skip_auto_mode": True,
            "skip_schedules": True,
            "z550_mode": True,  # Flag for Z550iQ+ specific mode handling.
            # Component mappings for Z550iQ+:
            # - 21: ON/OFF (0=OFF, 1=ON)
            # - 15: Temperature setpoint (decidegrees, 290=29.0°C)
            # - 16: Mode (0=heating, 1=cooling, 2=auto)
            # - 17: Read-only status of some kind (value 6 reported; writes return 403)
            # - 37: Water temperature (decidegrees)
            # - 40: Air temperature (decidegrees)
            # - 61: State (0=idle, 2=heating, 3=cooling, 11=no flow)
            "on_off_component": 21,
            "setpoint_component": 15,
            "mode_component": 16,
            "water_temp_component": 37,
            "air_temp_component": 40,
            "state_component": 61,
            "specific_components": [15, 16, 17, 21, 37, 40, 61],
        },
        priority=96,  # Higher than z250iq.
    ),
}
