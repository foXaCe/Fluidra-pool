"""Chlorinator device configurations.

Each model lives in its own DeviceConfig because of subtle component-mapping
differences across the CC/LC/DM/NS lineups.
"""

from __future__ import annotations

from ..types import DeviceConfig

CHLORINATOR_CONFIGS: dict[str, DeviceConfig] = {
    "chlorinator": DeviceConfig(
        device_type="chlorinator",
        identifier_patterns=["*.nn_*"],  # Bridged devices.
        family_patterns=["chlorinator"],
        components_range=25,  # Scan only basic components; specific ones added below.
        required_components=[0, 1, 2, 3],
        entities=["switch", "select", "number", "sensor_info"],
        features={
            "chlorination_level": {"write": 4, "read": 164},  # Component 4 (write) / 164 (read).
            "mode_control": True,  # Component 20: 0=OFF, 1=ON, 2=AUTO.
            "ph_setpoint": {"write": 8, "read": 172},  # Component 8 (write) / 172 (read).
            "orp_setpoint": {"write": 11, "read": 177},  # Component 11 (write) / 177 (read).
            "boost_mode": 245,  # Component 245.
            "sensors": {
                "ph": 172,  # pH reading.
                "orp": 177,  # ORP/Redox (mV).
                "free_chlorine": 178,  # Free chlorine (mg/l).
                "temperature": 183,  # Pool temperature (°C * 10).
                "salinity": 185,  # Salinity (g/L * 100).
            },
            # Avoids scanning 0-300.
            "specific_components": [4, 8, 11, 20, 164, 172, 177, 178, 183, 185, 245],
        },
        priority=80,  # High to avoid confusion with pumps.
    ),
    "cc25052635_chlorinator": DeviceConfig(
        device_type="chlorinator",
        # Zodiac GenSalt OE iQ pH 12 Evo — Issue #73.
        # Cloud id CC25052635.nn_1, unit serial 2600000811569.
        # Same Zodiac OE iQ tecnoLC2 layout as lc25050627 (c10 level, c16 pH
        # setpoint, c165 pH, c172 temperature ×10, c174 salinity ×100) — confirmed
        # here by the reporter's diagnostics: c172 = 290 = 29.0°C (the generic
        # config wrongly read c172 as pH → 2.9). This Evo variant additionally
        # exposes ORP: the app setpoint 690 mV matches c20; the measured value is
        # mapped to c177 (755 in the dump) and c170 is also scanned, to confirm.
        identifier_patterns=["CC25052635*"],
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "number", "sensor_info"],
        features={
            "on_off_component": 0,
            "chlorination_level": 10,
            "ph_setpoint": 16,
            "boost_mode": 103,
            "skip_mode_select": True,
            "sensors": {
                "ph": 165,  # pH measured (÷100).
                "orp": 177,  # ORP measured (mV) — to confirm vs c170.
                "temperature": 172,  # Water temperature (°C × 10) — confirmed 290 = 29.0°C.
                "salinity": 174,  # Salinity (g/L × 100).
            },
            "specific_components": [0, 10, 16, 20, 103, 165, 170, 172, 174, 177],
        },
        priority=90,
    ),
    "cc24033907_chlorinator": DeviceConfig(
        device_type="chlorinator",
        identifier_patterns=["CC24033907*"],
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "number", "sensor_info"],
        features={
            "chlorination_level": 10,  # Component 10 (0-100%, values rounded to multiples of 10).
            "ph_setpoint": 16,  # Component 16 (÷100).
            "orp_setpoint": 20,  # Component 20 (mV).
            "boost_mode": 103,
            "skip_mode_select": True,
            "sensors": {
                "ph": 165,  # pH measured value (÷100) — e.g. 712 = 7.12 pH.
                "orp": 170,  # ORP measured value (mV) — e.g. 779 mV.
                "temperature": 172,  # °C × 10 — e.g. 136 = 13.6°C.
                "salinity": 174,  # Salinity (g/L × 100).
            },
            "specific_components": [10, 16, 20, 103, 165, 170, 172, 174],
        },
        priority=85,
    ),
    "lc24008313_chlorinator": DeviceConfig(
        device_type="chlorinator",
        identifier_patterns=["LC24008313*"],  # Blauswim chlorinator (I.D. Electroquimica/Fluidra).
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "number", "sensor_info"],
        features={
            "chlorination_level": 10,
            "ph_setpoint": 16,
            "orp_setpoint": 20,
            "boost_mode": 103,
            "skip_mode_select": True,
            "sensors": {
                "ph": 165,  # e.g. 731 = 7.31 pH.
                "orp": 170,  # e.g. 688 mV.
                "temperature": 172,  # 201 → 20.1°C.
                "salinity": 174,  # 536 → 5.36 g/L.
            },
            "specific_components": [10, 16, 20, 103, 165, 170, 172, 174],
        },
        priority=86,
    ),
    "lc24019518_chlorinator": DeviceConfig(
        device_type="chlorinator",
        identifier_patterns=["LC24019518*"],  # Issue #21 — jaf69.
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "number", "sensor_info"],
        features={
            "chlorination_level": 10,
            "ph_setpoint": 16,
            "orp_setpoint": 20,
            "boost_mode": 103,
            "skip_mode_select": True,
            "sensors": {
                "ph": 165,
                "orp": 170,
                "temperature": 172,
                "salinity": 174,
            },
            "specific_components": [10, 16, 20, 103, 165, 170, 172, 174],
        },
        priority=86,
    ),
    "lc24013306_chlorinator": DeviceConfig(
        device_type="chlorinator",
        identifier_patterns=["LC24013306*"],  # Irripool iSALT — Issue #31.
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "number", "sensor_info"],
        features={
            "chlorination_level": 10,
            "ph_setpoint": 16,
            "orp_setpoint": 20,
            "boost_mode": 103,
            "skip_mode_select": True,
            "sensors": {
                "ph": 165,
                "orp": 170,
                "temperature": 172,
                "salinity": 174,
            },
            "specific_components": [10, 16, 20, 103, 165, 170, 172, 174],
        },
        priority=86,
    ),
    "cc25024927_chlorinator": DeviceConfig(
        device_type="chlorinator",
        # AstralPool Clear Connect Escalable (model 77020) — Issue #70 (@VICTOR28N).
        # Bridge CC25024927 with child device CC25024927.nn_1.
        # Mapping inferred from the tecnoLC2 family (same as LC25000122 / CC24009711);
        # pending diagnostic dump confirmation.
        identifier_patterns=["CC25024927.nn_*"],
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "number", "sensor_info"],
        features={
            "chlorination_level": 10,  # Component 10 (0-100%).
            "ph_setpoint": 16,  # Component 16 (÷100).
            "orp_setpoint": 20,  # Component 20 (mV).
            "skip_mode_select": True,
            "sensors": {
                "ph": 165,  # pH measured (÷100).
                "orp": 170,  # ORP measured (mV).
                "temperature": 172,  # Pool temperature (°C × 10).
                "salinity": 174,  # Salinity (g/L × 100).
            },
            "specific_components": [10, 16, 20, 165, 170, 172, 174],
        },
        priority=88,
    ),
    "cc24009711_chlorinator": DeviceConfig(
        device_type="chlorinator",
        # AstralPool Clear Connect Scalable 21 G/H (tecnoLC2) — Issue #55.
        # Bridge CC24009711 with child device CC24009711.nn_1.
        # Mapping confirmed by @smartincervera (same as LC25000122 / LC24026011).
        identifier_patterns=["CC24009711.nn_*"],
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "number", "sensor_info"],
        features={
            "chlorination_level": 10,
            "ph_setpoint": 16,
            "orp_setpoint": 20,
            "skip_mode_select": True,
            "sensors": {
                "ph": 165,  # e.g. 751 = 7.51 pH.
                "orp": 170,  # e.g. 657 mV.
                "temperature": 172,  # 236 = 23.6°C.
                "salinity": 174,  # 327 = 3.27 g/L.
            },
            "specific_components": [10, 16, 20, 165, 170, 172, 174],
        },
        priority=88,
    ),
    "cc25064524_chlorinator": DeviceConfig(
        device_type="chlorinator",
        # Astralpool Clear Connect 12 (tecnoLC2 minimal model) — Issue #55.
        # Bridge CC25064524 with child device CC25064524.nn_1.
        # Confirmed by @eabin: salinity on component 174 (e.g. 634 → 6.34 g/L).
        # No pH/ORP probes on the base model — components 13-20 are null.
        identifier_patterns=["CC25064524.nn_*"],
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "number", "sensor_info"],
        features={
            "chlorination_level": 10,
            "skip_mode_select": True,
            "sensors": {
                "temperature": 172,
                "salinity": 174,
            },
            # Keep 165/170 in scan in case the user adds pH/ORP probes later.
            "specific_components": [10, 16, 20, 165, 170, 172, 174],
        },
        priority=87,
    ),
    "cc25102423_chlorinator": DeviceConfig(
        device_type="chlorinator",
        # Astralpool Clear Connect Evo21 (tecnoLC2) — Issue #63 (analysis by @baracouda57).
        # Mapping matches the tecnoLC2 family (LC25000122 / LC24026011 / CC24009711).
        identifier_patterns=["CC25102423.nn_*"],
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "number", "sensor_info"],
        features={
            "chlorination_level": 10,
            "ph_setpoint": 16,
            "orp_setpoint": 20,
            "skip_mode_select": True,
            "sensors": {
                "ph": 165,  # Confirmed by user (7.5).
                "orp": 170,
                "temperature": 172,  # Confirmed (17.6°C).
                "salinity": 174,
            },
            "specific_components": [10, 16, 20, 165, 170, 172, 174],
        },
        priority=88,
    ),
    "cc25019224_chlorinator": DeviceConfig(
        device_type="chlorinator",
        # Astralpool Clear Connect 12 G/H (tecnoLC2) — Issue #66.
        # Full mapping confirmed by @alapedra's v2.35.1 diagnostics.
        identifier_patterns=["CC25019224.nn_*"],
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "number", "sensor_info"],
        features={
            "chlorination_level": 10,
            "ph_setpoint": 16,
            "orp_setpoint": 20,
            "skip_mode_select": True,
            "sensors": {
                "ph": 165,  # 721 = 7.21 pH.
                "orp": 170,  # 654 mV.
                "temperature": 172,  # 260 = 26.0°C.
                "salinity": 174,  # 627 = 6.27 g/L.
            },
            "specific_components": [10, 16, 20, 165, 170, 172, 174],
        },
        priority=87,
    ),
    "lc25012727_chlorinator": DeviceConfig(
        device_type="chlorinator",
        # KLINWASS MARK SALT 12 GR/H (tecnoLC2) — Issue #55 (confirmed by @FernandoArnanz).
        # No ORP / free-chlorine probes on this model.
        identifier_patterns=["LC25012727.nn_*"],
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "number", "sensor_info"],
        features={
            "chlorination_level": 10,
            "ph_setpoint": 16,
            "skip_mode_select": True,
            "sensors": {
                "ph": 165,  # 719 = 7.19 pH.
                "temperature": 172,  # 206 = 20.6°C.
                "salinity": 174,  # 620 = 6.20 g/L.
            },
            "specific_components": [10, 16, 165, 172, 174],
        },
        priority=87,
    ),
    "cc25019007_chlorinator": DeviceConfig(
        device_type="chlorinator",
        # Zodiac OE iQ 12 (tecnoLC2) — Issue #55 follow-up.
        # Mapping by analogy with the tecnoLC2 family.
        identifier_patterns=["CC25019007.nn_*"],
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "number", "sensor_info"],
        features={
            "chlorination_level": 10,
            "ph_setpoint": 16,
            "orp_setpoint": 20,
            "skip_mode_select": True,
            "sensors": {
                "ph": 165,
                "orp": 170,
                "temperature": 172,
                "salinity": 174,
            },
            "specific_components": [10, 16, 20, 165, 170, 172, 174],
        },
        priority=87,
    ),
    "lc24026011_chlorinator": DeviceConfig(
        device_type="chlorinator",
        # IrriPool iSalt tecnoLC2 bridge — Issue #58 (confirmed by @flyman1664 on Issue #53).
        identifier_patterns=["LC24026011.nn_*"],
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "number", "sensor_info"],
        features={
            "chlorination_level": 10,
            "ph_setpoint": 16,  # 740 = 7.40 pH.
            "orp_setpoint": 20,  # 710 mV.
            "skip_mode_select": True,
            "sensors": {
                "ph": 165,
                "orp": 170,
                "temperature": 172,  # 204 = 20.4°C.
                "salinity": 174,
            },
            "specific_components": [10, 16, 20, 165, 170, 172, 174],
        },
        priority=87,
    ),
    "lc25000122_chlorinator": DeviceConfig(
        device_type="chlorinator",
        # IrriPool iSalt tecnoLC2 bridge — Issue #53.
        identifier_patterns=["LC25000122.nn_*"],
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "number", "sensor_info"],
        features={
            "chlorination_level": 10,
            "ph_setpoint": 16,
            "orp_setpoint": 20,
            "skip_mode_select": True,
            "sensors": {
                "ph": 165,  # 731 = 7.31 pH.
                "orp": 170,  # 727 mV.
                "temperature": 172,  # 188 = 18.8°C.
                "salinity": 174,  # 589 = 5.89 g/L.
            },
            "specific_components": [10, 16, 20, 165, 170, 172, 174],
        },
        priority=87,
    ),
    "lc24015802_chlorinator": DeviceConfig(
        device_type="chlorinator",
        identifier_patterns=["LC24015802.nn_*"],  # Tecno LC2 bridge child.
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "number", "sensor_info"],
        features={
            "chlorination_level": 10,
            "ph_setpoint": 16,
            "orp_setpoint": 20,
            "boost_mode": 103,
            "skip_mode_select": True,
            "sensors": {
                "ph": 165,
                "orp": 170,
                "temperature": 172,
                "salinity": 174,
            },
            "specific_components": [10, 16, 20, 103, 165, 170, 172, 174],
        },
        priority=87,
    ),
    "lc24056317_chlorinator": DeviceConfig(
        device_type="chlorinator",
        identifier_patterns=["LC24056317*"],  # Gre chlorinator (I.D. Electroquimica/Fluidra) — Issue #28.
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "number", "sensor_info"],
        features={
            "chlorination_level": 10,
            "ph_setpoint": 16,
            "boost_mode": 103,
            "skip_mode_select": True,
            "sensors": {
                "ph": 165,
                "temperature": 172,
                "salinity": 174,
            },
            "specific_components": [10, 16, 103, 165, 172, 174],
        },
        priority=86,
    ),
    "lc25007119_chlorinator": DeviceConfig(
        device_type="chlorinator",
        identifier_patterns=["LC25007119*"],
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "number", "sensor_info"],
        features={
            "chlorination_level": 10,
            "ph_setpoint": 16,
            "orp_setpoint": 20,
            "boost_mode": 103,
            "skip_mode_select": True,
            "sensors": {
                "ph": 165,
                "orp": 170,
                "temperature": 172,
                "salinity": 174,
            },
            "specific_components": [10, 16, 20, 103, 165, 170, 172, 174],
        },
        priority=86,
    ),
    "cc24018202_chlorinator": DeviceConfig(
        device_type="chlorinator",
        identifier_patterns=["CC24018202*"],
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "number", "sensor_info"],
        features={
            "chlorination_level": 10,
            "ph_setpoint": 16,
            "orp_setpoint": 20,
            "boost_mode": 103,
            "skip_mode_select": True,
            "sensors": {
                "ph": 165,  # 727 = 7.27 pH.
                "orp": 170,  # 768 mV.
                "temperature": 172,  # 255 = 25.5°C.
                "salinity": 174,
                "free_chlorine": 178,
            },
            "specific_components": [10, 16, 20, 103, 165, 170, 172, 174, 178],
        },
        priority=87,
    ),
    "cc25113623_chlorinator": DeviceConfig(
        device_type="chlorinator",
        identifier_patterns=["CC25113623*"],
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "number", "sensor_info"],
        features={
            "chlorination_level": 10,
            "ph_setpoint": 16,
            "orp_setpoint": 20,
            "boost_mode": 103,
            "skip_mode_select": True,
            "sensors": {
                "ph": 165,  # 716 = 7.16 pH.
                "orp": 170,  # 681 mV.
                "temperature": 172,  # 291 → 29.1°C.
                "salinity": 174,  # 570 → 5.70 g/L.
            },
            "specific_components": [10, 16, 20, 103, 165, 170, 172, 174],
        },
        priority=87,
    ),
    "cc24021110_chlorinator": DeviceConfig(
        device_type="chlorinator",
        identifier_patterns=["CC24021110*"],
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "number", "sensor_info"],
        features={
            "chlorination_level": 10,
            "ph_setpoint": 16,
            "orp_setpoint": 20,
            "boost_mode": 103,
            "skip_mode_select": True,
            "sensors": {
                "ph": 165,  # 741 = 7.41 pH.
                "orp": 170,  # 791 mV.
                "temperature": 172,  # 216 → 21.6°C.
                "salinity": 174,
            },
            "specific_components": [10, 16, 20, 103, 165, 170, 172, 174],
        },
        priority=88,
    ),
    "cc24042517_chlorinator": DeviceConfig(
        device_type="chlorinator",
        identifier_patterns=["CC24042517*"],  # AstralPool Clear Connect Evo 21g — Issue #51.
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "number", "sensor_info"],
        features={
            "chlorination_level": 10,
            "ph_setpoint": 16,
            "orp_setpoint": 20,
            "boost_mode": 103,
            "skip_mode_select": True,
            "sensors": {
                "ph": 165,  # 680 = 6.80 pH.
                "orp": 170,  # 692 mV.
                "temperature": 172,  # 190 = 19.0°C.
                "salinity": 174,  # 432 = 4.32 g/L.
                "free_chlorine": 178,  # mg/L ÷ 100.
            },
            "specific_components": [10, 16, 20, 103, 165, 170, 172, 174, 178],
        },
        priority=88,
    ),
    "cc25002928_chlorinator": DeviceConfig(
        device_type="chlorinator",
        identifier_patterns=["CC25002928*"],  # Energy Connect 21 Scalable.
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "number", "sensor_info"],
        features={
            "chlorination_level": 10,  # 40 = 40%.
            "ph_setpoint": 16,  # 720 = 7.20 pH.
            "orp_setpoint": 20,  # 720 mV.
            "boost_mode": 103,
            "skip_mode_select": True,
            "sensors": {
                "ph": 165,  # 719 = 7.19 pH.
                "orp": 170,  # 729 mV.
                "temperature": 172,  # 195 = 19.5°C.
                "salinity": 174,  # 566 = 5.66 g/L.
            },
            "specific_components": [10, 16, 20, 103, 165, 170, 172, 174],
        },
        priority=89,
    ),
    "cc25013923_chlorinator": DeviceConfig(
        device_type="chlorinator",
        identifier_patterns=["CC25013923*"],  # joaopg — Issue #14.
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "number", "sensor_info"],
        features={
            "chlorination_level": 10,  # 70 = 70%.
            "ph_setpoint": 16,  # 720 = 7.20 pH.
            "orp_setpoint": 20,  # 650 mV.
            "boost_mode": 103,
            "skip_mode_select": True,
            "sensors": {
                "ph": 165,  # 720 = 7.20 pH.
                "orp": 170,  # 733 mV.
                "temperature": 172,  # 117 = 11.7°C.
                "salinity": 174,  # 244 = 2.44 g/L.
            },
            "specific_components": [10, 16, 20, 103, 165, 170, 172, 174],
        },
        priority=90,
    ),
    "cc25005502_chlorinator": DeviceConfig(
        device_type="chlorinator",
        identifier_patterns=["CC25005502*"],  # alextoro82 — Issue #15.
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "number", "sensor_info"],
        features={
            "chlorination_level": 10,
            "boost_mode": 103,
            "skip_mode_select": True,
            "skip_ph_orp": True,  # No pH/ORP probes.
            "skip_firmware": True,  # Firmware value not meaningful for this model.
            "sensors": {
                "temperature": 172,  # 66 = 6.6°C.
                "salinity": 174,  # 340 = 3.40 g/L.
                "chlorination_actual": 154,  # 70 = 70%.
            },
            "specific_components": [10, 103, 154, 172, 174],
        },
        priority=91,
    ),
    "cc_energy_connect_bridged_chlorinator": DeviceConfig(
        device_type="chlorinator",
        # Energy Connect bridged tecnoLC2 devices — Issue #36.
        # Confirmed on: CC24054221 (cortalys), CC24041107 (StenGarny).
        identifier_patterns=["CC24054221*", "CC24041107*"],
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "number", "sensor_info"],
        features={
            "on_off_component": 0,
            "chlorination_level": 10,
            "ph_setpoint": 157,  # Component 157 (÷10, e.g., 72 → 7.2 pH).
            "ph_setpoint_divisor": 10,  # This device uses ÷10 (not ÷100).
            "skip_mode_select": True,
            "skip_ph_orp": True,
            "sensors": {
                "ph": 165,  # 686 → 6.86 pH.
                "temperature": 172,  # 136 → 13.6°C.
                "salinity": 160,  # 3580 → 3.58 g/L.
                "chlorination_actual": 154,  # 60 = 60%.
            },
            "sensor_divisors": {
                "salinity": 1000,  # Reports salinity in mg/L (÷1000 for g/L).
            },
            "specific_components": [0, 10, 152, 154, 157, 160, 165, 172],
        },
        priority=93,
    ),
    "cc24058902_chlorinator": DeviceConfig(
        device_type="chlorinator",
        identifier_patterns=["CC24058902*"],  # Issue #35 — Enkil13.
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "number", "sensor_info"],
        features={
            "chlorination_level": 10,
            "ph_setpoint": 16,
            "orp_setpoint": 20,
            "boost_mode": 103,
            "skip_mode_select": True,
            "sensors": {
                "ph": 165,
                "orp": 177,  # c177 for this model.
                "free_chlorine": 178,
                "temperature": 172,
                "salinity": 174,
            },
            "specific_components": [10, 16, 20, 103, 165, 172, 174, 177, 178],
        },
        priority=93,
    ),
    "cc24068402_chlorinator": DeviceConfig(
        device_type="chlorinator",
        identifier_patterns=["CC24068402*"],  # Energy Connect tecnoLC2 — Issue #33.
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "number", "sensor_info"],
        features={
            "chlorination_level": 10,
            "ph_setpoint": 16,
            "orp_setpoint": 20,
            "boost_mode": 103,
            "skip_mode_select": True,
            "sensors": {
                "ph": 165,  # 680 = 6.80 pH.
                "orp": 170,
                "temperature": 172,  # 144 = 14.4°C.
                "salinity": 174,
                "chlorination_actual": 154,
            },
            "specific_components": [10, 16, 20, 103, 154, 165, 170, 172, 174],
        },
        priority=92,
    ),
    "cc24017504_chlorinator": DeviceConfig(
        device_type="chlorinator",
        identifier_patterns=["CC24017504*"],  # Energy Connect tecnoLC2 (with pH/ORP) — nicolasp.
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "number", "sensor_info"],
        features={
            "chlorination_level": 10,  # 80 = 80%.
            "ph_setpoint": 16,  # 760 = 7.60 pH.
            "orp_setpoint": 20,  # 700 mV.
            "boost_mode": 103,
            "skip_mode_select": True,
            "sensors": {
                "ph": 165,  # 760 = 7.60 pH.
                "orp": 170,  # 669 mV.
                "temperature": 172,  # 138 = 13.8°C.
                "salinity": 174,  # 480 = 4.80 g/L.
            },
            "specific_components": [10, 16, 20, 103, 165, 170, 172, 174],
        },
        priority=92,
    ),
    "cc24000304_chlorinator": DeviceConfig(
        device_type="chlorinator",
        # Energy Connect tecnoLC2 without pH/ORP probes — Issue #52, corrected in #72
        # by @Srekcah007 after testing: uses the standard tecnoLC2 layout, not the
        # 164/185 variant first assumed. Matches both the bridge and the bridged child.
        identifier_patterns=["CC24000304*"],
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "number", "sensor_info"],
        features={
            "chlorination_level": 10,
            "boost_mode": 103,
            "skip_mode_select": True,
            "sensors": {
                "temperature": 172,
                "salinity": 174,
            },
            # Keep 165/170 in scan in case the user adds pH/ORP probes later.
            "specific_components": [10, 16, 20, 165, 170, 172, 174],
        },
        priority=88,
    ),
    "cc24042711_chlorinator": DeviceConfig(
        device_type="chlorinator",
        identifier_patterns=["CC24042711*"],  # tecnoLC2 (AstralPool Clear Connect non-scalable) — Issue #25.
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "number", "sensor_info"],
        features={
            "chlorination_level": 10,
            "boost_mode": 103,
            "skip_mode_select": True,
            "skip_ph_orp": True,
            "sensors": {
                "temperature": 172,  # 144 = 14.4°C.
                "salinity": 174,  # 310 = 3.10 g/L.
                "chlorination_actual": 154,
            },
            "specific_components": [10, 103, 154, 172, 174],
        },
        priority=92,
    ),
    "dm24049704_chlorinator": DeviceConfig(
        device_type="chlorinator",
        identifier_patterns=["DM24049704*"],  # Domotic S2 chlorinator (SheepPool).
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "select", "number", "sensor_info", "time"],
        features={
            "chlorination_level": 4,
            "mode_control": True,  # Component 20: 0=OFF, 1=ON, 2=AUTO.
            "ph_setpoint": 8,  # 740 = 7.40 pH.
            "orp_setpoint": 11,  # 690 mV.
            "boost_mode": 245,
            "skip_signal": True,  # Component 2 is not RSSI for this device.
            "skip_firmware": True,  # Component 3 is not firmware for this device.
            "schedules": True,
            "schedule_component": 258,
            "schedule_count": 3,
            "sensors": {
                "ph": 172,  # 738 = 7.38 pH.
                "orp": 177,  # 740 mV.
                "temperature": 183,  # 42 = 4.2°C.
                "salinity": 185,
            },
            "specific_components": [4, 8, 11, 20, 172, 177, 183, 185, 245, 258],
        },
        priority=92,
    ),
    "lc25050627_chlorinator": DeviceConfig(
        device_type="chlorinator",
        # LC25050627 — bridged chlorinator (tecnoLC2 family).
        # Mapping confirmed by full component scan (Issue #XX).
        # c0   = ON/OFF switch.
        # c10  = chlorination level (0-100%).
        # c16  = pH setpoint (÷100).
        # c165 = pH measured (÷100) — e.g. 720 = 7.20 pH.
        # c172 = temperature (°C × 10) — e.g. 284 = 28.4°C.
        # c174 = salinity (g/L × 100) — e.g. 536 = 5.36 g/L.
        # No ORP probe on this model (c20/c170 are None).
        identifier_patterns=["LC25050627*"],
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "number", "sensor_info"],
        features={
            "on_off_component": 0,
            "chlorination_level": 10,
            "ph_setpoint": 16,
            "boost_mode": 103,
            "skip_mode_select": True,
            "sensors": {
                "ph": 165,
                "temperature": 172,
                "salinity": 174,
            },
            "specific_components": [0, 10, 16, 103, 165, 172, 174],
        },
        priority=90,
    ),
    "ns25_exo_chlorinator": DeviceConfig(
        device_type="chlorinator",
        identifier_patterns=["NS*"],
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "select", "number", "sensor_info", "time"],
        features={
            "chlorination_level": 38,  # Production percentage (0-100%).
            "chlorination_max": 100,  # EXO uses 0-100% range.
            "chlorination_step": 5,  # Step 5% for EXO.
            # boost_mode: NOT supported on EXO (c14 unreadable + API 403 on write).
            "mode_control": True,
            "mode_component": 13,
            "mode_mapping": {0: "off", 1: "auto", 2: "on"},  # EXO: 1=AUTO (confirmed).
            "orp_setpoint": 39,  # mV — e.g. 770.
            "ph_setpoint": 40,  # ÷10 — pH setpoint (72 = 7.2 target).
            "ph_setpoint_divisor": 10,  # EXO uses ÷10 (not ÷100 like CC chlorinators).
            "schedules": True,
            "schedule_count": 4,
            "schedule_component": 20,
            "schedule_output_type": "output",  # pump/aux1/aux2.
            "exo_mode": True,
            # on_off_component removed — mode select (AUTO/ON/OFF) replaces ON/OFF switch.
            "sensor_divisors": {
                "salinity": 1000,  # EXO reports salinity in mg/L (2750 = 2.75 g/L).
                "ph": 10,  # EXO reports pH * 10 (69 = 6.9 pH).
                "temperature": 1,  # EXO c64 is direct °C (14 = 14°C).
            },
            "sensors": {
                "ph": 62,  # ÷10 — 69 = 6.9 pH.
                "orp": 63,  # mV — 738 = 738 mV.
                "temperature": 64,  # Direct °C — 14 = 14°C.
                "salinity": 36,  # ÷1000 for g/L — 2750 = 2.75 g/L.
            },
            "specific_components": [9, 13, 14, 15, 17, 20, 35, 36, 38, 39, 40, 62, 63, 64],
        },
        priority=85,
    ),
}
