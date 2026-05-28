"""Pump device configurations (E30iQ + generic pump fallback)."""

from __future__ import annotations

from ..types import DeviceConfig

PUMP_CONFIGS: dict[str, DeviceConfig] = {
    "e30iq_pump": DeviceConfig(
        device_type="pump",
        identifier_patterns=["E30*", "LE*", "PUMP*"],
        components_range=5,
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
            # 9=ON/OFF, 10=auto, 11=speed, 15=speed%, 20=schedules, 21=network.
            "specific_components": [9, 10, 11, 15, 20, 21],
        },
        priority=50,
    ),
}
