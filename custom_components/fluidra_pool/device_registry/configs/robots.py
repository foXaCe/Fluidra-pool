"""Robotic cleaner configurations (Zodiac Freedom Lite, Issue #212)."""

from __future__ import annotations

from ..types import DeviceConfig

ROBOT_CONFIGS: dict[str, DeviceConfig] = {
    "freedom_lite_robot": DeviceConfig(
        device_type="robot",
        # Registers reported from the coordinator dump by @MDBENNANI (Issue #212):
        #   c25 automatic scheduling days as day-name strings
        #     (e.g. ['wednesday', 'saturday']),
        #   c26 battery charge in percent (e.g. 79).
        # The remaining registers of the dump (27-37: timestamps, counters,
        # firmware '1.1.3', run-duration blocks) are deliberately unmapped until
        # someone correlates them with an app toggle.
        identifier_patterns=["NLX*"],
        model_patterns=["Freedom Lite"],
        components_range=40,
        required_components=[25, 26],
        entities=["sensor_battery", "sensor_schedule_days"],
        features={
            "specific_components": [25, 26],
            "battery_component": 26,
            "schedule_days_component": 25,
        },
        priority=60,
    ),
}
