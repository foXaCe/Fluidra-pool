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
            # No-flow is surfaced on the climate entity (no_flow_alarm attribute /
            # hvac_action "no_flow"), not as a separate binary_sensor: there is no
            # binary_sensor platform, so "binary_sensor_no_flow" was a dead token.
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
        entities=["climate", "switch", "sensor_info", "sensor_temperature", "sensor_running_hours"],
        features={
            "temperature_control": True,
            # preset_modes stay disabled: component 17 is a read-only status whose
            # values (0/1/8 seen in polling) don't match a silence/smart/boost scheme
            # and writes return HTTP 403 (Issues #56, #88). The climate entity no
            # longer advertises or writes presets for this unit.
            "hvac_modes": ["off", "heat", "cool", "auto"],
            "skip_auto_mode": True,
            "skip_schedules": True,
            "z550_mode": True,  # Flag for Z550iQ+ specific mode handling.
            # Component mappings for Z550iQ+:
            # - 21: ON/OFF (0=OFF, 1=ON)
            # - 15: Temperature setpoint (decidegrees, 290=29.0°C)
            # - 16: Mode (0=heating, 1=cooling, 2=auto)
            # - 17: Read-only status (writes return 403) — not a preset.
            # - 18: Water-flow indicator (raw exposed as an attribute; values TBC — #88)
            # - 37: Water temperature (decidegrees)
            # - 40: Air temperature (decidegrees)
            # - 60: Total running hours (raw integer h, matches status.totalRunningHours)
            # - 61: State (0=idle, 2=heating, 3=cooling, 11=no flow)
            "on_off_component": 21,
            "setpoint_component": 15,
            "mode_component": 16,
            "water_temp_component": 37,
            "air_temp_component": 40,
            "state_component": 61,
            "specific_components": [15, 16, 17, 18, 21, 37, 40, 60, 61],
        },
        priority=96,  # Higher than z250iq.
    ),
    "hpgic_gre_heat_pump": DeviceConfig(
        device_type="heat_pump",
        # Gre HPGIC full-inverter heat pump — Issue #92 (@sterubbg). Its cloud
        # serial is LG-prefixed (e.g. LG25363734), so it was matching the LG Eco
        # Elyo profile by coincidence (LG* → priority 100). Match it by model/name
        # instead and win the tie with a higher priority. No family_patterns: a
        # bare "heat pump" match would steal genuinely-unknown heat pumps from the
        # generic fallback. comp7 here is CXWAB (the LG Eco Elyo signature is
        # BXWAA). Confirmed layout from the reporter's diagnostics: c13 ON/OFF
        # (1=ON), c14 mode (0=Smart Heating), c15 target temperature (×10,
        # 300=30.0°C), c19 water temperature (×10). The scan is widened beyond the
        # narrow LG set to surface the remaining components — the app's "current
        # temperature" sits on one the LG scan never read.
        name_patterns=["hpgic"],
        model_patterns=["hpgic"],
        components_range=5,
        required_components=[0, 1, 2, 3],
        entities=["climate", "switch", "sensor_info"],
        features={
            "preset_modes": True,
            "temperature_control": True,
            "hvac_modes": ["off", "heat"],
            "skip_auto_mode": True,
            "skip_schedules": True,
            "specific_components": [7, 13, 14, 15, 17, 19, 28, 67, 81, 82],
        },
        priority=101,
    ),
}
