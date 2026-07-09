"""Chlorinator device configurations.

Each model lives in its own DeviceConfig because of subtle component-mapping
differences across the CC/LC/DM/NS lineups.
"""

from __future__ import annotations

from typing import Any

from ..types import DeviceConfig


def _standard_tecnolc2(
    identifier_patterns: list[str],
    *,
    priority: int,
    boost_mode: int | None = None,
    free_chlorine: int | None = None,
    cell_production_state: int | None = None,
) -> DeviceConfig:
    """Build a DeviceConfig for the standard tecnoLC2 chlorinator layout.

    Shared by many rebadged tecnoLC2 units (AstralPool Clear Connect, Zodiac,
    IrriPool/Irripool/Irrijardin iSalt, KLINWASS, ...) that expose the same
    component mapping: c10 = chlorination level (0-100%), c16 = pH setpoint
    (÷100), c20 = ORP setpoint (mV), c103 = boost mode (when present),
    c165 = pH measured (÷100), c170 = ORP measured (mV), c172 = water
    temperature (°C × 10), c174 = salinity (g/L × 100), c178 = free chlorine
    (mg/L, when present), c154 = cell actual-production register (when
    present — drives the `chlorinator_producing` binary sensor, Issue #109).
    The OFF/ON/AUTO mode select does not drive these units (skip_mode_select).
    """
    features: dict[str, Any] = {
        "chlorination_level": 10,
        "ph_setpoint": 16,
        "orp_setpoint": 20,
    }
    if boost_mode is not None:
        features["boost_mode"] = boost_mode
    features["skip_mode_select"] = True
    sensors = {
        "ph": 165,
        "orp": 170,
        "temperature": 172,
        "salinity": 174,
    }
    if free_chlorine is not None:
        sensors["free_chlorine"] = free_chlorine
    features["sensors"] = sensors
    if cell_production_state is not None:
        features["cell_production_state"] = cell_production_state

    specific_components = [10, 16, 20]
    if boost_mode is not None:
        specific_components.append(boost_mode)
    specific_components.extend([165, 170, 172, 174])
    if free_chlorine is not None:
        specific_components.append(free_chlorine)
    if cell_production_state is not None:
        specific_components.append(cell_production_state)
    features["specific_components"] = specific_components

    return DeviceConfig(
        device_type="chlorinator",
        identifier_patterns=identifier_patterns,
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "number", "sensor_info"],
        features=features,
        priority=priority,
    )


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
            # Mode on component 20: 0=OFF, 1=ON, 2=AUTO.
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
        verified=False,
    ),
    "cc25052635_chlorinator": DeviceConfig(
        device_type="chlorinator",
        # Zodiac GenSalt OE iQ pH 12 Evo (tecnoLC2) — Issue #73.
        # The Fluidra API exposes no model field (name/family/model are all the
        # generic "Chlorinator"/"Chlorinators" and comp7 is empty), so units are
        # matched by their cloud serial. Add new ones here as they are reported.
        # Mapping confirmed by several users: c165 = pH, c172 = water temperature
        # (×10 — the generic config wrongly read c172 as pH → 2.9), c174 = salinity,
        # c170 = ORP measured (matches the app; c177 is a close but uncalibrated raw
        # value, ~50 mV off), c20 = ORP setpoint.
        # CC26028741 (Issue #116, @elefantomas) is the same GenSalt OE iQ pH 12 Evo that
        # fell back to the generic profile (read c172 water temperature as pH → 3.07, and
        # missed c165/c170/c174 so salinity/temperature showed 0).
        identifier_patterns=["CC25052635*", "CC25046312*", "CC26028741*"],
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
                "orp": 170,  # ORP measured (mV) — matches the app (c177 is uncalibrated).
                "temperature": 172,  # Water temperature (°C × 10).
                "salinity": 174,  # Salinity (g/L × 100).
            },
            "specific_components": [0, 10, 16, 20, 103, 165, 170, 172, 174],
        },
        priority=90,
    ),
    "cc25051112_chlorinator": DeviceConfig(
        device_type="chlorinator",
        # Zodiac GenSalt OE iQ pH 25 IVO (tecnoLC2) — Issue #80 (@lolo31370).
        # Same tecnoLC2 layout as the GenSalt OE iQ pH 12 Evo (cc25052635), but this
        # variant also exposes an ORP setpoint on c20 (diagnostics show c20 = 750,
        # matching the app's 750 mV target). c172 = water temperature (×10, 21.3 °C —
        # the generic config wrongly read c172 as pH 2.13); c165 = pH (7.5); c170 = ORP
        # measured (663 mV, matches the app — c177 = 725 is the uncalibrated raw value);
        # c174 = salinity (3.7 g/L); c10 = chlorination level (100 %).
        identifier_patterns=["CC25051112*"],
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "number", "sensor_info"],
        features={
            "on_off_component": 0,
            "chlorination_level": 10,
            "ph_setpoint": 16,
            "orp_setpoint": 20,
            "boost_mode": 103,
            "skip_mode_select": True,
            "sensors": {
                "ph": 165,  # pH measured (÷100).
                "orp": 170,  # ORP measured (mV) — matches the app (c177 is uncalibrated).
                "temperature": 172,  # Water temperature (°C × 10).
                "salinity": 174,  # Salinity (g/L × 100).
            },
            "specific_components": [0, 10, 16, 20, 103, 165, 170, 172, 174],
        },
        priority=90,
    ),
    "cc25016001_chlorinator": DeviceConfig(
        device_type="chlorinator",
        # Zodiac Ei2 iQ (tecnoLC2, salt-only — no pH/ORP probe) — Issue #84 (@Felix62-byte).
        # Pump-running diagnostics disproved the early legacy-layout guess: c185 stays 0
        # while the app shows 4.4 g/L, and c4 stays 70 while the app shows 60 % — so
        # neither is salinity/chlorination. As a tecnoLC2 unit it almost certainly uses
        # the standard layout (chlorination c10, salinity c174 ×100), which no profile had
        # ever fetched. c172 = water temperature is confirmed (290 → 29.0 °C). The scan is
        # widened this round so the next capture reveals the real components if c10/c174
        # turn out empty. No pH/ORP probes on this salt-only cell.
        identifier_patterns=["CC25016001.nn_*"],
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["number", "sensor_info"],
        features={
            "chlorination_level": 10,  # Standard tecnoLC2 — to confirm (c4 was a stale 70 %).
            "skip_mode_select": True,  # The OFF/ON/AUTO mode select does not drive this unit.
            "sensors": {
                "temperature": 172,  # Water temperature (°C × 10) — confirmed (29.0 °C).
                "salinity": 174,  # Standard tecnoLC2 salinity (g/L × 100) — to confirm (c185 was 0).
            },
            # Widened for diagnosis: standard tecnoLC2 set + the legacy candidates, so the
            # next pump-running capture pinpoints salinity/chlorination if c10/c174 are empty.
            "specific_components": [4, 10, 16, 20, 103, 164, 165, 170, 172, 174, 177, 178, 183, 185],
        },
        priority=85,
    ),
    "cc24047102_chlorinator": _standard_tecnolc2(
        # AstralPool Energy Connect (tecnoLC2) — Issue #85 (@Goetz67, @pitch110).
        # Standard tecnoLC2 layout, validated against the Fluidra app with the pump
        # running: c172 = water temperature (×10, 24.6 °C — the generic config wrongly
        # read it as pH 2.46), c165 = pH, c170 = ORP, c174 = salinity. chlorination /
        # boost / salinity only report while the unit is running.
        # CC25010924 — same Energy Connect (pH + ORP), pending @pitch110's confirmation.
        # CC25008731 (Issue #117, @yannickuhrig1) — same standard tecnoLC2 layout: the
        # generic profile read c172 (28.8 °C) as pH 2.88 and left pH/ORP equal to their
        # setpoints. Confirmed against the app: c172 = water temperature, c165 = pH (7.1),
        # c170 = calibrated ORP (743 mV — c177 = 765 is the uncalibrated raw value).
        # CC25017029 (Issue #121, @luiscosta1979) — same generic fallback: diagnostics
        # show c172 = 254 read as pH 2.54, while the API status_data.waterTemperature
        # confirms 25.4 °C, and c183 (generic temperature slot) reads 0. Same layout.
        ["CC24047102*", "CC25010924*", "CC25008731*", "CC25017029*"],
        priority=88,
        boost_mode=103,
    ),
    "cc24033907_chlorinator": _standard_tecnolc2(
        ["CC24033907*"],
        priority=85,
        boost_mode=103,
    ),
    # Ducere 21 (tecnoLC2) — Issue #125 (@onslope). The unit fell back to the
    # generic profile whose legacy layout read c172 (water temperature) as pH.
    # Mapping supplied and validated by the reporter: standard tecnoLC2 layout
    # + boost on c103, free chlorine on c178 (probe-dependent — may stay empty)
    # and the cell actual-production register on c154 (producing binary sensor).
    "lc24008202_chlorinator": _standard_tecnolc2(
        ["LC24008202*"],
        priority=90,
        boost_mode=103,
        free_chlorine=178,
        cell_production_state=154,
    ),
    "lc24008313_chlorinator": _standard_tecnolc2(
        ["LC24008313*"],  # Blauswim chlorinator (I.D. Electroquimica/Fluidra).
        priority=86,
        boost_mode=103,
    ),
    "lc24019518_chlorinator": _standard_tecnolc2(
        ["LC24019518*"],  # Issue #21 — jaf69.
        priority=86,
        boost_mode=103,
    ),
    "lc24013306_chlorinator": _standard_tecnolc2(
        # Irripool iSALT (tecnoLC2) — Issues #31, #73.
        # LC24009805 (@guilhem069) is the same Irripool iSalt; it fell back to the
        # generic profile, which read the water temperature (c172 = 32.2 °C) as pH and
        # the temperature from c183 (= 0 °C). Same standard tecnoLC2 layout below.
        ["LC24013306*", "LC24009805*"],
        priority=86,
        boost_mode=103,
    ),
    "lc24004804_chlorinator": _standard_tecnolc2(
        # Irrijardin iSalt — Issue #87 (@Math43). Same iSalt OEM cell as the Irripool
        # iSALT (lc24013306), rebadged by a different retailer, so it uses the standard
        # tecnoLC2 layout. Mapping verified by the reporter against his own integration:
        # c10 chlorination, c16 pH setpoint, c165 pH, c172 water temperature, c174 salinity.
        # ORP (c170) is kept to match the sibling iSalt profiles; if this unit has no ORP
        # probe it will simply read 0 — drop "orp"/"orp_setpoint" if the reporter confirms.
        ["LC24004804*"],
        priority=86,
        boost_mode=103,
    ),
    "lc25024524_chlorinator": _standard_tecnolc2(
        # tecnoLC2 chlorinator — Issue #73 (@Ausstriken). LC25024524.nn_1 fell back to the
        # generic profile, which read c172 (water temperature) as pH (÷100 → 3.16). Standard
        # tecnoLC2 layout (same as the LC iSALT siblings), confirmed against the Fluidra app:
        # c165 = pH (7.3), c170 = ORP (659 mV — c177 is the uncalibrated raw value),
        # c172 = water temperature (×10, 31.6 °C), c174 = salinity (5.4 g/L).
        ["LC25024524*"],
        priority=86,
        boost_mode=103,
    ),
    "cc25024927_chlorinator": _standard_tecnolc2(
        # AstralPool Clear Connect Escalable (model 77020) — Issue #70 (@VICTOR28N).
        # Bridge CC25024927 with child device CC25024927.nn_1.
        # Mapping inferred from the tecnoLC2 family (same as LC25000122 / CC24009711);
        # pending diagnostic dump confirmation.
        ["CC25024927.nn_*"],
        priority=88,
    ),
    "cc25011632_chlorinator": _standard_tecnolc2(
        # AstralPool Clear Connect (tecnoLC2) — Issue #123 (@josgaming).
        # CC25011632.nn_1 fell back to the generic *.nn_* profile, whose legacy layout
        # both mis-reads the sensors (c172 = 263 read as pH 2.63 instead of 26.3 °C water
        # temperature) AND shares component IDs between setpoint read-back and measurement
        # (ph_setpoint.read = sensors.ph = 172, orp_setpoint.read = sensors.orp = 177), so
        # moving the "Consigne pH/ORP" slider overwrote the pH/ORP sensor values. This is
        # the standard tecnoLC2 layout (same as CC24009711 / LC25000122): setpoints live on
        # c16/c20 — distinct from the c165/c170 measurements — so the collision disappears.
        # pH/ORP/salinity components aren't in the generic scan, so they weren't in the
        # diagnostics; mapping inferred from the family, pending the reporter's confirmation.
        ["CC25011632.nn_*"],
        priority=88,
    ),
    "cc24009711_chlorinator": _standard_tecnolc2(
        # AstralPool Clear Connect Scalable 21 G/H (tecnoLC2) — Issue #55.
        # Bridge CC24009711 with child device CC24009711.nn_1.
        # Mapping confirmed by @smartincervera (same as LC25000122 / LC24026011).
        ["CC24009711.nn_*"],
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
    "cc26010842_chlorinator": DeviceConfig(
        device_type="chlorinator",
        # Zodiac Ei2 iQ 20 pH Evo (tecnoLC2, pH-only — no ORP probe) — Issue #104
        # (@terminator1992). Fell back to the legacy generic profile which read
        # c172 (water temperature, 289 → 28.9 °C, matches the app) as pH AND as
        # the pH-setpoint read-back, so pH mirrored the setpoint (2.89). v2.45.1
        # diagnostics confirmed: c20 = null (no ORP setpoint register on this
        # pH-only unit), c177/c178/c183/c185 all 0 (legacy slots empty),
        # disinfection saltLowLevel with pH-minus dosing only. Standard tecnoLC2
        # layout minus everything ORP: pH c165, temperature c172, salinity c174,
        # setpoint c16, chlorination c10.
        identifier_patterns=["CC26010842*"],
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "number", "sensor_info"],
        features={
            "chlorination_level": 10,
            "ph_setpoint": 16,
            "skip_mode_select": True,
            "sensors": {
                "ph": 165,
                "temperature": 172,  # 289 = 28.9 °C — confirmed against the app.
                "salinity": 174,
            },
            # c9/c13/c14/c103/c154 widen the scan like the sibling Evo profile
            # (cc25102423): same Ei2 iQ Evo mainboard family, so the CLE/COU
            # production registers (Issue #104) should surface in the next
            # diagnostics capture without creating any entity yet.
            "specific_components": [9, 10, 13, 14, 16, 103, 154, 165, 172, 174],
        },
        priority=90,
    ),
    "cc25102423_chlorinator": DeviceConfig(
        device_type="chlorinator",
        # tecnoLC2 "Evo" profile, with ORP + ORP setpoint — Issues #63, #73, #104.
        # The same layout ships under several rebadged names; the API exposes no
        # model field, so add serials as users confirm them:
        #   CC25102423 / CC25106623 — Astralpool Clear Connect Evo21 (@DarkSuperT)
        #   CC25066724              — Astralpool Clear Connect Evo12 (@valentinval90)
        #   LC26033146              — IBASEL Evoflex 30 7g/H (@FoxP)
        #   CC25021136              — Zodiac Ei2 iQ Evo (@crdo78) — confirmed c165/170/172/174
        identifier_patterns=["CC25102423.nn_*", "CC25066724*", "CC25106623*", "LC26033146*", "CC25021136*"],
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
            # c9/c13/c14/c103/c154 widen the scan to locate the production-mode
            # registers on the Zodiac Ei2 iQ Evo (Issue #104, @crdo78): this layout
            # has no 0/1/2 Auto/Manual/Off selector (c20 is the ORP setpoint, 750 mV),
            # but two binary mainboard features — CLE (External Chlorine Control, a
            # remote on/off dry-contact) and COU/COV (Pool Cover, low-production mode).
            # They aren't polled yet, so they never reached the diagnostics. Keeping
            # them in the scan surfaces the real registers in the next capture pair
            # (toggled once with CLE/COU ON, once OFF) so they can be mapped reliably.
            "specific_components": [9, 10, 13, 14, 16, 20, 103, 154, 165, 170, 172, 174],
        },
        priority=88,
    ),
    "cc25019224_chlorinator": DeviceConfig(
        device_type="chlorinator",
        # Astralpool Clear Connect 12 (G/H) (tecnoLC2) — Issues #66, #81.
        # Full mapping confirmed by @alapedra's v2.35.1 diagnostics.
        # CC25009932 — same model with ORP + pH probes (@christian123125, #81).
        identifier_patterns=["CC25019224.nn_*", "CC25009932*"],
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
            # c154 is the cell's actual-production register (Issue #109): a
            # resting/producing capture pair confirmed it flips 0 (idle) → 100
            # (producing) with the ORP hysteresis, while c10 stays at the
            # configured level (100 %) and c9/c103 don't move. Exposed as the
            # `binary_sensor.chlorinator_producing` running state.
            "cell_production_state": 154,
            "specific_components": [9, 10, 16, 20, 103, 154, 165, 170, 172, 174],
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
    "lc24009904_chlorinator": _standard_tecnolc2(
        # KLINWASS (tecnoLC2 with pH + ORP probes) — Issue #82.
        # Was falling back to the generic config, which read c172 (water
        # temperature ×10) as pH ÷100 → wrong pH 4.27, and showed each sensor
        # equal to its setpoint. Diagnostics confirm the standard tecnoLC2
        # layout: c20 = ORP setpoint (600, matches the app), c172 = water
        # temperature ×10 (42.7 °C, matches status_data 41.7 °C). Same mapping
        # as cc25019007 / lc24026011 / lc25000122.
        ["LC24009904.nn_*"],
        priority=87,
    ),
    "cc25019007_chlorinator": _standard_tecnolc2(
        # Zodiac OE iQ 12 (tecnoLC2) — Issue #55 follow-up.
        # Mapping by analogy with the tecnoLC2 family.
        ["CC25019007.nn_*"],
        priority=87,
    ),
    "lc24026011_chlorinator": _standard_tecnolc2(
        # IrriPool iSalt tecnoLC2 bridge — Issue #58 (confirmed by @flyman1664 on Issue #53).
        ["LC24026011.nn_*"],
        priority=87,
    ),
    "lc25000122_chlorinator": _standard_tecnolc2(
        # IrriPool iSalt tecnoLC2 bridge — Issue #53.
        ["LC25000122.nn_*"],
        priority=87,
    ),
    "lc24015802_chlorinator": _standard_tecnolc2(
        ["LC24015802.nn_*"],  # Tecno LC2 bridge child.
        priority=87,
        boost_mode=103,
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
    "lc25007119_chlorinator": _standard_tecnolc2(
        ["LC25007119*"],
        priority=86,
        boost_mode=103,
    ),
    "cc24018506_chlorinator": DeviceConfig(
        device_type="chlorinator",
        # AstralPool / Fluidra Energy Connect (tecnoLC2, firmware 40) — Issue #129
        # (@luistf76). Fell back to the generic *.nn_* profile, which read c172
        # (water temperature, 315 → 31.5 °C) as pH and c177 (raw, uncalibrated
        # ORP, 676) as ORP. The reporter cross-checked every register against the
        # Fluidra app on the live unit:
        #   c170 = 597 → calibrated ORP (matches the app; c177 is ~79 mV high)
        #   c172 = 315 → water temperature 31.5 °C
        #   c16  = 740 → pH setpoint 7.40   c20 = 710 → ORP setpoint 710 mV
        #   c4/c164     → chlorination level write/read (legacy slots; no c10)
        # This bridge exposes no live pH-measured component (c165 absent; c8 is
        # only the pH-setpoint write echo, dropping to 0 when the dosing pump is
        # idle) and no live salinity (c185 stays 0; the app reads it from a
        # different endpoint), so neither sensor is mapped rather than surfacing a
        # misleading value. c20 is the ORP setpoint, not a 0/1/2 mode register, so
        # the mode select is skipped (it would map 710 → "off" and clobber the
        # setpoint on write).
        identifier_patterns=["CC24018506*"],
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "number", "sensor_info"],
        features={
            "chlorination_level": {"write": 4, "read": 164},
            "ph_setpoint": {"write": 8, "read": 16},
            "orp_setpoint": {"write": 11, "read": 20},
            "boost_mode": 245,
            "skip_mode_select": True,
            "sensors": {
                "orp": 170,  # Calibrated ORP — matches the app (c177 is raw).
                "temperature": 172,  # 315 = 31.5 °C.
            },
            "specific_components": [4, 8, 11, 16, 20, 164, 170, 172, 245],
        },
        priority=90,
    ),
    "cc24018202_chlorinator": _standard_tecnolc2(
        ["CC24018202*"],
        priority=87,
        boost_mode=103,
        free_chlorine=178,
    ),
    "cc25113623_chlorinator": _standard_tecnolc2(
        ["CC25113623*"],
        priority=87,
        boost_mode=103,
    ),
    "cc24021110_chlorinator": _standard_tecnolc2(
        ["CC24021110*"],
        priority=88,
        boost_mode=103,
    ),
    "cc24042517_chlorinator": _standard_tecnolc2(
        ["CC24042517*"],  # AstralPool Clear Connect Evo 21g — Issue #51.
        priority=88,
        boost_mode=103,
        free_chlorine=178,
    ),
    "cc25002928_chlorinator": _standard_tecnolc2(
        ["CC25002928*"],  # Energy Connect 21 Scalable.
        priority=89,
        boost_mode=103,
    ),
    "cc25013923_chlorinator": _standard_tecnolc2(
        ["CC25013923*"],  # joaopg — Issue #14.
        priority=90,
        boost_mode=103,
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
            # No pH/ORP probes.
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
    "cc24017504_chlorinator": _standard_tecnolc2(
        ["CC24017504*"],  # Energy Connect tecnoLC2 (with pH/ORP) — nicolasp.
        priority=92,
        boost_mode=103,
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
            "sensors": {
                "temperature": 172,  # 144 = 14.4°C.
                "salinity": 174,  # 310 = 3.10 g/L.
                "chlorination_actual": 154,
            },
            "specific_components": [10, 103, 154, 172, 174],
        },
        priority=92,
    ),
    "dm24008702_chlorinator": DeviceConfig(
        device_type="chlorinator",
        # Neolysis Connect (thing type domoticS2, bridged) — Issue #141 (@overcraft47).
        # Surfaced by the unverified-profile repair issue: the unit matched the generic
        # catch-all, whose *legacy* layout is in fact the right one for this domoticS2
        # family (c172 = pH, c177 = ORP, c183 = temperature, c185 = salinity) — unlike
        # the tecnoLC2 units. Same family as the SheepPool (DM24049704) but a distinct
        # layout: chlorination has split write/read registers (c4 desired / c164
        # reported) and there is no schedule component.
        # Reporter's component dump: c8 = pH setpoint (720 → 7.20), c11 = ORP setpoint
        # (700 mV), c172 = pH measured (722 → 7.22), c177 = ORP measured (539 mV),
        # c183 = water temperature (298 → 29.8 °C), c185 = salinity (316 → 3.16 g/L),
        # c178 = free chlorine (null — no probe fitted), c245 = boost.
        # NB: setpoints are read on c8/c11 (the actual targets), not on the measured
        # c172/c177 the catch-all wrongly reused — so the number entities show 7.20 /
        # 700, not the live readings.
        identifier_patterns=["DM24008702*"],
        family_patterns=["chlorinator"],
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=["switch", "select", "number", "sensor_info"],
        features={
            "chlorination_level": {"write": 4, "read": 164},  # c4 desired / c164 reported.
            # Mode on component 20: 0=OFF, 1=ON, 2=AUTO.
            "ph_setpoint": 8,  # c8 = 720 → 7.20 pH (target, distinct from measured c172).
            "orp_setpoint": 11,  # c11 = 700 mV (target, distinct from measured c177).
            "boost_mode": 245,
            "sensors": {
                "ph": 172,  # pH measured (÷100) — 722 → 7.22.
                "orp": 177,  # ORP measured (mV) — 539.
                "free_chlorine": 178,  # mg/L — null until a probe is fitted.
                "temperature": 183,  # Water temperature (°C × 10) — 298 → 29.8.
                "salinity": 185,  # Salinity (g/L × 100) — 316 → 3.16.
            },
            "specific_components": [4, 8, 11, 20, 164, 172, 177, 178, 183, 185, 245],
        },
        priority=90,
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
            # Mode on component 20: 0=OFF, 1=ON, 2=AUTO.
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
        # Gre SWGA chlorinator (tecnoLC2, no ORP probe) — PR #71 + Issue #76.
        # The same model carries a different cloud serial per unit (LC25050627,
        # LC24076417 / SWGA40, ...); add new ones here as they are reported.
        # c0   = ON/OFF switch.
        # c10  = chlorination level (0-100%).
        # c16  = pH setpoint (÷100).
        # c165 = pH measured (÷100) — e.g. 720 = 7.20 pH.
        # c172 = temperature (°C × 10) — e.g. 284 = 28.4°C (SWGA40: 218 = 21.8°C).
        # c174 = salinity (g/L × 100) — e.g. 536 = 5.36 g/L.
        # No ORP probe on this model (c20/c170/c177 are None/0).
        identifier_patterns=["LC25050627*", "LC24076417*"],
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
