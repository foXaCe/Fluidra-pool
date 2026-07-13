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
    "victoria_smart_connect_pump": DeviceConfig(
        device_type="pump",
        # Fluidra Victoria Smart Connect VS (variable-speed, "connected") — Issue #144
        # (@MiguelCosta). Fell back to the generic pump profile, which only scans
        # c0-c3 + c9/c10. A capture taken while the pump ran at 100 % under a schedule
        # still showed c9 = 0 AND c10 = 0 — so, unlike the E30iQ, this pump reports
        # neither on/off on c9 nor auto on c10; its running state and speed live on
        # components the narrow generic scan never fetches. This profile widens the
        # scan (diagnostic round) so the next running capture reveals the real speed /
        # state registers, after which the control/sensor mapping is completed. Matched
        # by model so every Victoria Smart Connect unit is covered. Kept unverified
        # until the registers are confirmed.
        model_patterns=["Victoria Smart Connect"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "switch_auto", "sensor_info"],
        features={
            "auto_mode": True,
            # Diagnostic window: fetch the full VS-pump register range so the next
            # running capture reveals which components carry speed / state / schedule.
            "specific_components": [9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24],
        },
        priority=50,
        verified=False,
    ),
}
