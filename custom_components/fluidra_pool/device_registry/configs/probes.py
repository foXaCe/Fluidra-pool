"""Water-analyser probe configurations (sensor-only — no chlorination, no switch).

These devices measure water quality but don't actuate anything. They are mapped
as ``device_type="chlorinator"`` so the chlorinator sensor factory in
:mod:`custom_components.fluidra_pool.sensor` picks them up, but with no switch /
number entities (read-only).
"""

from __future__ import annotations

from ..types import DeviceConfig

PROBE_CONFIGS: dict[str, DeviceConfig] = {
    "blue_connect_silver": DeviceConfig(
        device_type="chlorinator",
        # Zodiac Blue Connect Silver — Issue #69.
        # Cloud SKU "WA000099", thingType "BC3" (component 7).
        # Confirmed by @LykkeConsult: comp 13 = pH (direct decimal),
        # comp 14 = ORP (mV, direct).
        # Temperature mapping not confirmed yet (comp 16 or 19 — pending diagnostics
        # with a known reference temp).
        identifier_patterns=["WA*"],
        family_patterns=["data collectors"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        # Sensor-only: no switch (probe doesn't actuate), no select (no mode),
        # no number (no chlorination level).
        entities=["sensor_info"],
        features={
            # No mode select (probe-only device).
            "skip_mode_select": True,
            # No chlorination_level — probe doesn't dose.
            "sensors": {
                "ph": 13,  # Decimal pH (7.3 = 7.3 pH).
                "orp": 14,  # ORP in mV (764 = 764 mV).
            },
            # Override default chlorinator divisors: Blue Connect reports decimal directly.
            "sensor_divisors": {
                "ph": 1,  # No ×100 scaling.
                "orp": 1,  # No scaling.
            },
            "specific_components": [13, 14, 16, 19],  # Include 16/19 in scan for future mapping.
        },
        priority=88,  # Above generic chlorinator (80); identifier_patterns disambiguates.
    ),
}
