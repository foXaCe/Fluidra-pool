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
        # Fluidra Victoria Smart Connect VS (variable-speed, "connected", thingType
        # mppvs) — Issue #144. Unlike the E30iQ (numeric c9 on/off + c10 auto), the
        # Victoria reports its state as *strings* on a different window, decoded from
        # five captures (@renaatski, running/stopped/flow/speed/95 %/100 %):
        #   c14 "RUNNING"/"NOT RUNNING", c16 "AUTO"/"QUICK FUNCTION",
        #   c17 setpoint value (% or m³/h), c18 "SPEED"/"FLOW", c21 live output %,
        #   c22 power (W, matches the HMI at high speed), c24 head (cm),
        #   c20 quick-function preset slot; c9/c10/c15 stay 0 (unused).
        # The read side is wired via the victoria_vs_mode coordinator branch; control
        # mirrors the app — an Auto-schedule toggle (c13) plus a Stop button (c15) —
        # and the dry-contact speed inputs (c27/c28/c29) are diagnostic binary sensors.
        # Verified on-device by @renaatski: on/off + auto behave correctly (the
        # remaining work — direct speed via /schedulers, activity/telemetry polish —
        # is additive, not a correctness gap).
        model_patterns=["Victoria Smart Connect"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        # No plain on/off "switch": the Victoria is schedule-driven, so control is
        # the auto-schedule toggle (switch_auto → c13) plus a momentary Stop button
        # (button_stop → c15) that halts the motor without disarming the schedule,
        # mirroring the app (Issue #144).
        entities=[
            "switch_auto",
            "button_stop",
            "sensor_speed",
            "sensor_power",
            "sensor_head",
            "sensor_flow",
            "sensor_info",
        ],
        features={
            "auto_mode": True,
            "victoria_vs_mode": True,
            # Speed-preset dry-contact inputs → diagnostic binary sensors so an
            # external relay (e.g. an ice-guard interlock) can drive HA automations.
            "speed_input_components": {"low": 29, "medium": 28, "high": 27},
            # Full VS-pump register window. Decoded (Issue #144): c14 state,
            # c16 mode, c17 setpoint, c18 speed/flow, c21 live %, c22 power (W),
            # c24 head (cm), c25 flow (m³/h), c27/c28/c29 speed-preset dry-contact
            # inputs (High/Medium/Low), c42/c43 min/max speed %, c44/c45 min/max
            # flow m³/h. c13/c23 still undeciphered (kept for diagnostics).
            "specific_components": [
                9,
                10,
                11,
                12,
                13,
                14,
                15,
                16,
                17,
                18,
                19,
                20,
                21,
                22,
                23,
                24,
                25,
                27,
                28,
                29,
                42,
                43,
                44,
                45,
            ],
        },
        priority=50,
        verified=True,
    ),
}
