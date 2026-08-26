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
        #   c35 pump schedule (r1), c36 lights schedule (r2),
        #   c16 packs pump schedule times as a string — read-only here.
        #
        # Schedule semantics (also verified on his unit): a schedule is an
        # *armed window*, not a guaranteed stop. Ending the window has been
        # observed not to stop an output that was started manually; auto-mode
        # off only disarms the schedule and leaves the output where it was.
        # Times are local wall-clock despite the bridge reporting GMT0.
        family_patterns=["Cabinets"],
        model_patterns=["Command Connect"],
        # Cloud family id, until now recognised by its model string alone.
        thing_type_patterns=["SRC"],
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
            # Packed pump config string (last digit mirrors c15). Exposed as a
            # diagnostic sensor only — never written by this integration.
            "cabinet_packed_config": 16,
            "specific_components": [13, 15, 16, 24, 26, 35, 36],
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
            # Per-output schedule registers (r1 pump / r2 lights). One slot each
            # in the captures; format is the app's CRON list with local times,
            # days 0-6, no ``state`` field on write, clear with ``[]``.
            "cabinet_schedule_components": {"pump": 35, "lights": 36},
            "cabinet_schedule_count": 1,
            "schedule_local_time": True,
            "schedule_armed_window": True,
        },
        priority=85,
    ),
}
