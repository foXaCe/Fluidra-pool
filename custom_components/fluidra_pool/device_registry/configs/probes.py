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
        # Mapping confirmed against the Fluidra Pool app by @LykkeConsult:
        #  - comp 12 = water temperature (direct °C, e.g. 16 = 16°C)
        #  - comp 13 = pH (direct decimal, e.g. 7.3)
        #  - comp 14 = ORP (mV, direct, e.g. 764)
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
            # Blue Connect reorders the info components vs the Fluidra standard:
            # comp 0 = RSSI, comp 1 = serial, comp 2 = hardware UID (Issue #69).
            "info_layout": "blue_connect",
            # No chlorination_level — probe doesn't dose.
            "sensors": {
                "temperature": 12,  # Water temperature (direct °C).
                "ph": 13,  # Decimal pH (7.3 = 7.3 pH).
                "orp": 14,  # ORP in mV (764 = 764 mV).
            },
            # Override default chlorinator divisors: Blue Connect reports decimal directly.
            "sensor_divisors": {
                "temperature": 1,  # Direct °C (default chlorinator divisor is ×10).
                "ph": 1,  # No ×100 scaling.
                "orp": 1,  # No scaling.
            },
            "specific_components": [12, 13, 14],
        },
        priority=88,  # Above generic chlorinator (80); identifier_patterns disambiguates.
    ),
}
