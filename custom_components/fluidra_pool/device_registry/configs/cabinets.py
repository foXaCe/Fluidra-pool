"""Cabinet configurations (AstralPool Command Connect, Issue #210)."""

from __future__ import annotations

from ..types import DeviceConfig

CABINET_CONFIGS: dict[str, DeviceConfig] = {
    "command_connect_cabinet": DeviceConfig(
        device_type="cabinet",
        # One API device (behind an iQBridge ZB gateway) driving several
        # independent outputs, each with its own on/off and auto-mode register.
        # Component map verified on hardware by @efgonzalez (Issue #210):
        #   c13 filtration pump on/off, c24 pool lights on/off,
        #   c15 pump auto mode, c26 lights auto mode,
        #   c35/c36 the two schedulers (r1/r2) — not written by this
        #   integration yet; c16 packs schedule times as a string.
        family_patterns=["Cabinets"],
        model_patterns=["Command Connect"],
        components_range=40,
        required_components=[13, 24],
        entities=["switch"],
        features={
            # Component registers (verified on hardware, Issue #210); the
            # toggle_switches list below drives one boolean switch per key.
            "cabinet_pump": 13,
            "cabinet_lights": 24,
            "cabinet_pump_auto_mode": 15,
            "cabinet_lights_auto_mode": 26,
            "specific_components": [13, 15, 24, 26],
            # ⚠ The cabinet silently ignores integer writes: PUT {"desiredValue": 1}
            # returns HTTP 200 and does nothing, while true/false applies within
            # ~5-10 s (Issue #210). Every control entity for this profile must go
            # through a boolean write path — never 1/0.
            "boolean_writes": True,
            "toggle_switches": [
                ("cabinet_pump", "cabinet_pump", "mdi:pump"),
                ("cabinet_lights", "cabinet_lights", "mdi:string-lights"),
                ("cabinet_pump_auto_mode", "cabinet_pump_auto_mode", "mdi:calendar-clock"),
                ("cabinet_lights_auto_mode", "cabinet_lights_auto_mode", "mdi:calendar-clock"),
            ],
        },
        priority=85,
    ),
}
