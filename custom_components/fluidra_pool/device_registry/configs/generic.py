"""Generic fallback device configurations used when no specific match is found."""

from __future__ import annotations

from ..types import DeviceConfig

GENERIC_CONFIGS: dict[str, DeviceConfig] = {
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
        verified=False,
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
        verified=False,
    ),
    "generic_heater": DeviceConfig(
        device_type="heater",
        components_range=25,
        # A generic heater never satisfies the temperature-sensor conditions
        # (no target_temperature, no z550/z260 feature), so "sensor_temperature"
        # was a dead declaration that created no entity.
        entities=["switch"],
        features={},
        priority=20,
        verified=False,
    ),
    "generic_light": DeviceConfig(
        device_type="light",
        components_range=45,  # Include component 40 for light schedules.
        entities=["switch", "sensor_brightness", "time", "select"],
        features={
            # 18=effect/scene, 40=scheduler, 45=RGBW colour.
            "specific_components": [18, 40, 45],
            "effect_select": 18,
        },
        priority=20,
        verified=False,
    ),
}
