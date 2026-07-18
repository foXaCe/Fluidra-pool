"""Tests for device registry and device identification."""

from __future__ import annotations

from custom_components.fluidra_pool.device_registry import (
    DEVICE_CONFIGS,
    DeviceConfig,
    DeviceIdentifier,
)


class TestDeviceConfig:
    """Test DeviceConfig dataclass."""

    def test_default_values(self):
        config = DeviceConfig(device_type="test")
        assert config.device_type == "test"
        assert config.identifier_patterns == []
        assert config.components_range == 25
        assert config.entities == []
        assert config.features == {}
        assert config.priority == 0

    def test_custom_values(self):
        config = DeviceConfig(
            device_type="pump",
            identifier_patterns=["VS*"],
            components_range=10,
            entities=["switch", "sensor"],
            features={"speed_control": True},
            priority=50,
        )
        assert config.device_type == "pump"
        assert config.identifier_patterns == ["VS*"]
        assert config.components_range == 10
        assert config.priority == 50


class TestDeviceConfigRegistry:
    """Test the DEVICE_CONFIGS registry."""

    def test_registry_not_empty(self):
        assert len(DEVICE_CONFIGS) > 0

    def test_all_configs_have_device_type(self):
        for name, config in DEVICE_CONFIGS.items():
            assert config.device_type, f"{name} has no device_type"

    def test_known_device_types(self):
        types = {c.device_type for c in DEVICE_CONFIGS.values()}
        assert "pump" in types
        assert "heat_pump" in types
        assert "chlorinator" in types

    def test_lg_heat_pump_config(self):
        config = DEVICE_CONFIGS["lg_heat_pump"]
        assert config.device_type == "heat_pump"
        assert "LG*" in config.identifier_patterns
        assert "climate" in config.entities

    def test_e30iq_pump_config(self):
        config = DEVICE_CONFIGS["e30iq_pump"]
        assert config.device_type == "pump"
        assert "switch" in config.entities
        assert config.features.get("speed_control") is True

    def test_chlorinator_config_has_sensors(self):
        config = DEVICE_CONFIGS["chlorinator"]
        assert "sensors" in config.features
        sensors = config.features["sensors"]
        assert "ph" in sensors
        assert "orp" in sensors
        assert "temperature" in sensors

    def test_z260iq_config_drops_dead_binary_sensor_token(self):
        """No binary_sensor platform exists, so the token must not be declared (device_registry-1)."""
        config = DEVICE_CONFIGS["z260iq_heat_pump"]
        assert "binary_sensor_no_flow" not in config.entities

    def test_generic_heater_config_drops_dead_temperature_token(self):
        """A generic heater never creates a temperature sensor; the token was dead (device_registry-3)."""
        config = DEVICE_CONFIGS["generic_heater"]
        assert config.entities == ["switch"]

    def test_cc25052635_zodiac_gensalt_oe_iq_uses_teclc2_layout(self):
        """Zodiac GenSalt OE iQ pH 12 Evo (CC25052635) maps sensors on the tecnoLC2 layout (Issue #73)."""
        config = DEVICE_CONFIGS["cc25052635_chlorinator"]
        assert config.device_type == "chlorinator"
        assert "CC25052635*" in config.identifier_patterns
        sensors = config.features["sensors"]
        # c172 is temperature here (confirmed 290 = 29.0°C), not pH as the generic config assumed.
        assert sensors["temperature"] == 172
        assert sensors["ph"] == 165
        assert sensors["salinity"] == 174
        assert sensors["orp"] == 170  # ORP is c170 (matches the app), not the raw c177

    def test_cc25052635_identifies_over_generic_chlorinator(self):
        """GenSalt OE iQ units match their dedicated config, not the generic *.nn_* one (Issue #73).

        Different units carry different cloud serials for the same model, so both
        reported serials must resolve to the dedicated profile.
        """
        for serial in ("CC25052635.nn_1", "CC25046312.nn_1", "CC26028741.nn_1"):
            device = {
                "device_id": serial,
                "name": "Chlorinator",
                "family": "Chlorinators",
                "type": "chlorinator",
                "model": "Chlorinator",
                "components": {"172": {"reportedValue": 290}},
            }
            config = DeviceIdentifier.identify_device(device)
            assert config is DEVICE_CONFIGS["cc25052635_chlorinator"], serial
            assert config.features["sensors"]["temperature"] == 172

    def test_cc25051112_gensalt_oe_iq_ph25_ivo_uses_teclc2_layout(self):
        """Zodiac GenSalt OE iQ pH 25 IVO (CC25051112) maps on tecnoLC2 + ORP setpoint (Issue #80)."""
        config = DEVICE_CONFIGS["cc25051112_chlorinator"]
        device = {
            "device_id": "CC25051112.nn_1",
            "name": "Chlorinator",
            "family": "Chlorinators",
            "type": "chlorinator",
            "model": "Chlorinator",
            "components": {"172": {"reportedValue": 213}},
        }
        assert DeviceIdentifier.identify_device(device) is config
        sensors = config.features["sensors"]
        assert sensors["ph"] == 165  # c172 (=21.3°C) is temperature, not pH as the generic config read
        assert sensors["orp"] == 170  # calibrated ORP (663 mV), not the raw c177 (725)
        assert sensors["temperature"] == 172
        assert sensors["salinity"] == 174
        assert config.features["orp_setpoint"] == 20  # this variant exposes the ORP setpoint (c20 = 750)

    def test_dm24008702_neolysis_connect_identifies_over_generic(self):
        """Neolysis Connect (DM24008702, domoticS2) resolves to its verified profile, not the catch-all (Issue #141)."""
        device = {
            "device_id": "DM24008702.nn_1",
            "name": "Chlorinator",
            "family": "Chlorinators",
            "type": "chlorinator",
            "model": "Chlorinator",
            "components": {"172": {"reportedValue": 722}},
        }
        config = DeviceIdentifier.identify_device(device)
        assert config is DEVICE_CONFIGS["dm24008702_chlorinator"]
        assert config.verified is True  # verified profile → no unverified-profile repair issue
        sensors = config.features["sensors"]
        assert sensors["ph"] == 172  # domoticS2 legacy layout: pH on c172 (not the tecnoLC2 c165)
        assert sensors["orp"] == 177
        assert sensors["temperature"] == 183
        assert sensors["salinity"] == 185
        # Setpoints read the target registers (c8/c11), not the measured c172/c177.
        assert config.features["ph_setpoint"] == 8
        assert config.features["orp_setpoint"] == 11
        assert config.features["chlorination_level"] == {"write": 4, "read": 164}

    def test_cc25021136_zodiac_ei2_iq_evo_uses_teclc2_layout(self):
        """Zodiac Ei2 iQ Evo (CC25021136) maps to the tecnoLC2 Evo profile, not the generic one (Issue #104)."""
        config = DEVICE_CONFIGS["cc25102423_chlorinator"]
        device = {
            "device_id": "CC25021136.nn_1",
            "name": "Chlorinator",
            "family": "Chlorinators",
            "type": "chlorinator",
            "model": "Chlorinator",
            "components": {"165": {"reportedValue": 684}, "170": {"reportedValue": 652}},
        }
        assert DeviceIdentifier.identify_device(device) is config
        sensors = config.features["sensors"]
        assert sensors["ph"] == 165  # 6.8, confirmed against the app
        assert sensors["orp"] == 170  # 652 mV
        assert sensors["temperature"] == 172  # 28.1 °C
        assert sensors["salinity"] == 174  # 5.0 g/L
        # Scan is widened to hunt the CLE / COU production-mode registers (Issue #104):
        # c20 is the ORP setpoint, so there is no 0/1/2 mode on this Evo layout.
        for candidate in (9, 13, 14, 103, 154):
            assert candidate in config.features["specific_components"]

    def test_lc24009805_irripool_isalt_uses_teclc2_layout(self):
        """Irripool iSalt LC24009805 maps to the Irripool iSALT tecnoLC2 profile (Issue #73)."""
        config = DEVICE_CONFIGS["lc24013306_chlorinator"]
        device = {
            "device_id": "LC24009805.nn_1",
            "name": "Chlorinator",
            "family": "Chlorinators",
            "type": "chlorinator",
            "model": "Chlorinator",
            "components": {"172": {"reportedValue": 322}},
        }
        assert DeviceIdentifier.identify_device(device) is config
        # c172 (=32.2°C) is temperature, not pH; temperature is no longer read from c183 (=0).
        assert config.features["sensors"]["temperature"] == 172
        assert config.features["sensors"]["ph"] == 165

    def test_lc24004804_irrijardin_isalt_uses_teclc2_layout(self):
        """Irrijardin iSalt (LC24004804) maps on the tecnoLC2 layout, like the Irripool iSALT (Issue #87)."""
        config = DEVICE_CONFIGS["lc24004804_chlorinator"]
        device = {
            "device_id": "LC24004804.nn_1",
            "name": "Chlorinator",
            "family": "Chlorinators",
            "type": "chlorinator",
            "model": "Chlorinator",
            "components": {"172": {"reportedValue": 215}},
        }
        assert DeviceIdentifier.identify_device(device) is config
        sensors = config.features["sensors"]
        assert sensors["ph"] == 165  # c172 is temperature, not pH (the generic bug)
        assert sensors["temperature"] == 172
        assert sensors["salinity"] == 174
        assert config.features["chlorination_level"] == 10

    def test_z250iq_promoted_to_full_z260iq_feature_set(self):
        """Z250iQ carries the full Z260iQ layout, validated by a live dump (Issue #139).

        The dump (@Kal42) confirmed every register during a real no-flow —
        c0 running hours, c14 modes (app shows heating AND cooling presets),
        c17 status, c28=1 no-flow, c81/c82 setpoint bounds — so the profile is
        promoted to z260iq_mode handling. Identification stays its own
        (LF* + z250/z25 name patterns, priority 95): a wrong-model promotion
        would silently break installs, so the match rules must not change.
        """
        config = DEVICE_CONFIGS["z250iq_heat_pump"]
        # Full Z260iQ feature set.
        assert config.features.get("z260iq_mode") is True
        assert "z250iq_mode" not in config.features  # dead flag removed
        assert config.features["hvac_modes"] == ["off", "heat", "cool", "heat_cool"]
        assert (config.features["min_temp"], config.features["max_temp"]) == (7.0, 40.0)
        assert "sensor_running_hours" in config.entities
        for component in (0, 17, 28, 81, 82):  # hours, status, no-flow, bounds
            assert component in config.features["specific_components"]
        # Air temp from Issue #131 is still wired.
        assert 67 in config.features["specific_components"]
        assert "sensor_temperature" in config.entities
        # Identification unchanged: an LF* serial named Z250iQ still matches this profile.
        device = {
            "device_id": "LF25001234",
            "name": "Z250iQ",
            "family": "Heat Pump",
            "type": "heat_pump",
            "model": "Z250iQ",
            "components": {},
        }
        assert DeviceIdentifier.identify_device(device) is config

    def test_cc24018506_energy_connect_calibrated_orp_no_fake_ph_salinity(self):
        """Energy Connect CC24018506 uses calibrated ORP (c170) and drops the fake pH/salinity (Issue #129)."""
        config = DEVICE_CONFIGS["cc24018506_chlorinator"]
        device = {
            "device_id": "CC24018506.nn_1",
            "name": "Chlorinator",
            "family": "Chlorinators",
            "type": "chlorinator",
            "model": "Chlorinator",
        }
        # Must win over the generic *.nn_* profile (priority 80).
        assert DeviceIdentifier.identify_device(device) is config
        assert config.priority > 80
        sensors = config.features["sensors"]
        # Calibrated ORP (c170), not the raw/uncalibrated c177 the generic profile used.
        assert sensors["orp"] == 170
        assert sensors["temperature"] == 172  # 315 = 31.5 °C, not read as pH.
        # No live pH-measured / salinity component on this bridge -> no misleading sensor.
        assert "ph" not in sensors
        assert "salinity" not in sensors
        # c20 is the ORP setpoint, not a mode register -> the broken mode select is skipped.
        assert config.features["skip_mode_select"] is True
        assert config.features["ph_setpoint"] == {"write": 8, "read": 16}
        assert config.features["orp_setpoint"] == {"write": 11, "read": 20}
        assert config.features["chlorination_level"] == {"write": 4, "read": 164}

    def test_cc26010842_ei2_iq_20_ph_evo_ph_only_layout(self):
        """Ei2 iQ 20 pH Evo CC26010842 maps on the pH-only tecnoLC2 layout (Issue #104)."""
        config = DEVICE_CONFIGS["cc26010842_chlorinator"]
        device = {
            "device_id": "CC26010842.nn_1",
            "name": "Chlorinator",
            "family": "Chlorinators",
            "type": "chlorinator",
            "model": "Chlorinator",
        }
        assert DeviceIdentifier.identify_device(device) is config
        sensors = config.features["sensors"]
        # c172 is water temperature (289 = 28.9 °C) — the generic profile read it as pH.
        assert sensors["ph"] == 165
        assert sensors["temperature"] == 172
        assert sensors["salinity"] == 174
        # pH-only unit: no ORP probe, no ORP setpoint (c20 = null in diagnostics).
        assert "orp" not in sensors
        assert "free_chlorine" not in sensors
        assert "orp_setpoint" not in config.features
        assert config.features["ph_setpoint"] == 16
        assert config.features["skip_mode_select"] is True
        # Widened scan shared with the sibling Evo profile (CLE/COU hunt).
        for component in (9, 13, 14, 103, 154):
            assert component in config.features["specific_components"]

    def test_lc24008202_ducere21_uses_tecnolc2_layout(self):
        """Ducere 21 LC24008202 maps on the tecnoLC2 layout, not the generic profile (Issue #125)."""
        config = DEVICE_CONFIGS["lc24008202_chlorinator"]
        device = {
            "device_id": "LC24008202.nn_1",
            "name": "Chlorinator",
            "family": "Chlorinators",
            "type": "chlorinator",
            "model": "Chlorinator",
        }
        assert DeviceIdentifier.identify_device(device) is config
        sensors = config.features["sensors"]
        # c172 is water temperature — the generic profile wrongly read it as pH.
        assert sensors["ph"] == 165
        assert sensors["orp"] == 170
        assert sensors["temperature"] == 172
        assert sensors["salinity"] == 174
        assert sensors["free_chlorine"] == 178
        assert config.features["ph_setpoint"] == 16
        assert config.features["orp_setpoint"] == 20
        assert config.features["boost_mode"] == 103
        # c154 drives the chlorinator_producing binary sensor (Issue #109).
        assert config.features["cell_production_state"] == 154
        assert 154 in config.features["specific_components"]
        assert config.features["skip_mode_select"] is True

    def test_lc25024524_uses_teclc2_layout(self):
        """tecnoLC2 chlorinator LC25024524 maps on the tecnoLC2 layout, not the generic profile (Issue #73)."""
        config = DEVICE_CONFIGS["lc25024524_chlorinator"]
        device = {
            "device_id": "LC25024524.nn_1",
            "name": "Chlorinator",
            "family": "Chlorinators",
            "type": "chlorinator",
            "model": "Chlorinator",
            "components": {"172": {"reportedValue": 316}},
        }
        assert DeviceIdentifier.identify_device(device) is config
        sensors = config.features["sensors"]
        # c172 (=31.6°C) is temperature, not pH as the generic config read (÷100 → 3.16).
        assert sensors["ph"] == 165
        assert sensors["orp"] == 170  # calibrated ORP (659 mV), not the raw c177 (720)
        assert sensors["temperature"] == 172
        assert sensors["salinity"] == 174
        assert config.features["ph_setpoint"] == 16
        # Same layout as the sibling Irripool iSALT profile.
        assert config.features["sensors"] == DEVICE_CONFIGS["lc24013306_chlorinator"].features["sensors"]

    def test_cc24047102_energy_connect_uses_teclc2_layout(self):
        """AstralPool Energy Connect serials map on the tecnoLC2 layout (Issues #85, #117, #121, #142)."""
        config = DEVICE_CONFIGS["cc24047102_chlorinator"]
        # CC25008731 (#117) / CC25017029 (#121) are the same layout — c172 = water
        # temperature, not pH as the generic profile read it (e.g. 254 → 25.4 °C, not pH 2.54).
        # CC25059122 (#142) is the Clear Connect EVO 12, a rebadged Energy Connect:
        # c172 = 239 matched the app's 23.9 °C exactly.
        for serial in ("CC24047102.nn_1", "CC25010924.nn_1", "CC25008731.nn_1", "CC25017029.nn_1", "CC25059122.nn_1"):
            device = {
                "device_id": serial,
                "name": "Chlorinator",
                "family": "Chlorinators",
                "type": "chlorinator",
                "model": "Chlorinator",
                "components": {"172": {"reportedValue": 246}},
            }
            assert DeviceIdentifier.identify_device(device) is config, serial
        sensors = config.features["sensors"]
        assert sensors["ph"] == 165  # c172 (=24.6°C) is temperature, not pH (generic read it as pH 2.46)
        assert sensors["orp"] == 170
        assert sensors["temperature"] == 172
        assert sensors["salinity"] == 174
        assert config.features["orp_setpoint"] == 20
        assert config.features["boost_mode"] == 103

    def test_cc25016001_ei2_iq_salt_only_no_phantom_sensors(self):
        """Zodiac Ei2 iQ (CC25016001) — salt-only legacy layout, no pH/ORP phantoms (Issue #84)."""
        config = DEVICE_CONFIGS["cc25016001_chlorinator"]
        device = {
            "device_id": "CC25016001.nn_1",
            "name": "Chlorinator",
            "family": "Chlorinators",
            "type": "chlorinator",
            "model": "Chlorinator",
            "components": {"4": {"reportedValue": 70}, "172": {"reportedValue": 289}},
        }
        assert DeviceIdentifier.identify_device(device) is config
        # This unit has no pH/ORP probe → those sensors and setpoints must not exist.
        sensors = config.features["sensors"]
        assert "ph" not in sensors
        assert "orp" not in sensors
        assert "free_chlorine" not in sensors
        assert "ph_setpoint" not in config.features
        assert "orp_setpoint" not in config.features
        # Standard tecnoLC2 chlorination on c10 (c4 was a stale 70 %, not the live level).
        assert config.features["chlorination_level"] == 10
        # c172 is water temperature (29.0 °C), not pH.
        assert sensors["temperature"] == 172
        assert sensors["salinity"] == 174  # standard tecnoLC2 salinity (c185 read 0 while running)
        # The scan must include the components where salinity/chlorination actually live.
        assert 174 in config.features["specific_components"]
        assert 10 in config.features["specific_components"]
        # No mode select / boost on this salt-only unit (they had no effect).
        assert config.features.get("skip_mode_select") is True
        assert "boost_mode" not in config.features
        assert "select" not in config.entities

    def test_lc24009904_klinwass_uses_teclc2_layout(self):
        """KLINWASS chlorinator (LC24009904) maps on the tecnoLC2 layout, no sensor==setpoint (Issue #82)."""
        config = DEVICE_CONFIGS["lc24009904_chlorinator"]
        device = {
            "device_id": "LC24009904.nn_1",
            "name": "Chlorinator",
            "family": "Chlorinators",
            "type": "chlorinator",
            "model": "Chlorinator",
            "components": {"172": {"reportedValue": 427}},
        }
        assert DeviceIdentifier.identify_device(device) is config
        sensors = config.features["sensors"]
        assert sensors["ph"] == 165  # c172 (=42.7°C) is temperature, not pH (generic read it as pH 4.27)
        assert sensors["orp"] == 170
        assert sensors["temperature"] == 172
        assert sensors["salinity"] == 174
        # The generic config's bug was sensor and setpoint sharing one component; they must differ here.
        assert sensors["ph"] != config.features["ph_setpoint"]
        assert sensors["orp"] != config.features["orp_setpoint"]  # c20 = ORP setpoint (600, matches the app)

    def test_astralpool_clear_connect_evo_serials_use_evo21_profile(self):
        """tecnoLC2 "Evo" units (Clear Connect Evo, IBASEL Evoflex) use the Evo profile (Issue #73)."""
        for serial in ("CC25066724.nn_1", "CC25106623.nn_1", "LC26033146.nn_1"):
            device = {
                "device_id": serial,
                "name": "Chlorinator",
                "family": "Chlorinators",
                "type": "chlorinator",
                "model": "Chlorinator",
                "components": {"172": {"reportedValue": 176}},
            }
            config = DeviceIdentifier.identify_device(device)
            assert config is DEVICE_CONFIGS["cc25102423_chlorinator"], serial
            assert config is not DEVICE_CONFIGS["cc25052635_chlorinator"], serial

    def test_cc25009932_clear_connect_12_uses_g_h_profile(self):
        """Astralpool Clear Connect 12 (CC25009932) matches the Clear Connect 12 profile (Issue #81)."""
        config = DEVICE_CONFIGS["cc25019224_chlorinator"]
        assert config.features["sensors"]["orp"] == 170  # calibrated ORP (c170), not the raw c177
        device = {
            "device_id": "CC25009932.nn_1",
            "name": "Chlorinator",
            "family": "Chlorinators",
            "type": "chlorinator",
            "model": "Chlorinator",
            "components": {"172": {"reportedValue": 241}},
        }
        assert DeviceIdentifier.identify_device(device) is config

    def test_cc25011632_clear_connect_no_setpoint_sensor_collision(self):
        """CC25011632 uses the tecnoLC2 layout so setpoints and measurements don't collide (Issue #123)."""
        config = DEVICE_CONFIGS["cc25011632_chlorinator"]
        device = {
            "device_id": "CC25011632.nn_1",
            "name": "Chlorinator",
            "family": "Chlorinators",
            "type": "chlorinator",
            "model": "Chlorinator",
            "components": {"172": {"reportedValue": 263}},  # 26.3 °C water temp, not pH 2.63.
        }
        assert DeviceIdentifier.identify_device(device) is config
        sensors = config.features["sensors"]
        assert sensors["ph"] == 165
        assert sensors["orp"] == 170
        assert sensors["temperature"] == 172
        assert sensors["salinity"] == 174
        # The generic profile shared IDs (ph_setpoint.read == sensors.ph == 172), which
        # let a setpoint write overwrite the measurement. The setpoints must now live on
        # distinct components from the measured sensors.
        assert config.features["ph_setpoint"] == 16
        assert config.features["orp_setpoint"] == 20
        assert config.features["ph_setpoint"] not in sensors.values()
        assert config.features["orp_setpoint"] not in sensors.values()

    def test_cc25019224_scans_production_state_candidates(self):
        """The Clear Connect 12 profile scans the cell-production candidates (Issue #109).

        The resting/producing diagnostics showed no 0/1 flip among the mapped
        components, so c9/c103/c154 are added to the scan to surface the real
        production register in the next capture pair (without creating entities).
        """
        config = DEVICE_CONFIGS["cc25019224_chlorinator"]
        specific = config.features["specific_components"]
        for candidate in (9, 103, 154):
            assert candidate in specific, candidate
        # No sensor/binary_sensor is mapped to them yet — scan only.
        assert "chlorination_actual" not in config.features["sensors"]

    def test_gre_swga_config_has_salinity_and_no_orp(self):
        """Gre SWGA chlorinators (incl. SWGA40) expose salinity, no ORP, matched per-serial (Issue #76)."""
        config = DEVICE_CONFIGS["lc25050627_chlorinator"]
        # No ORP probe on this model — salinity present, orp absent.
        assert config.features["sensors"] == {"ph": 165, "temperature": 172, "salinity": 174}
        for serial in ("LC25050627.nn_1", "LC24076417.nn_1"):
            device = {
                "device_id": serial,
                "name": "Chlorinator",
                "family": "Chlorinators",
                "type": "chlorinator",
                "model": "Chlorinator",
                "components": {"172": {"reportedValue": 218}},
            }
            assert DeviceIdentifier.identify_device(device) is config, serial

    def test_blue_connect_gold_distinguished_from_silver_by_name(self):
        """Blue Connect Gold gets its own salinity-aware profile, matched by product name (Issue #75)."""
        gold_config = DEVICE_CONFIGS["blue_connect_gold"]
        assert gold_config.features["sensors"]["salinity"] == 16  # confirmed via Issue #75 diagnostics
        assert gold_config.features["sensors"]["battery_voltage"] == 19  # Issue #138 (mV samples)
        assert 19 in gold_config.features["specific_components"]  # c19 must actually be polled
        gold = {
            "device_id": "QX25004412",  # QX serial, not WA — matched by name instead.
            "name": "Blue Connect Gold",
            "family": "Data collectors",
            "type": "unknown",
            "model": "Blue Connect Gold",
            "components": {"12": {"reportedValue": 21.7}},
        }
        assert DeviceIdentifier.identify_device(gold) is gold_config

        # A Silver (WA*, not named Gold) keeps the Silver profile, which has no salinity.
        silver = {
            "device_id": "WA000099",
            "name": "Chlorinator",
            "family": "Data collectors",
            "type": "unknown",
            "model": "Chlorinator",
            "components": {},
        }
        silver_config = DeviceIdentifier.identify_device(silver)
        assert silver_config is DEVICE_CONFIGS["blue_connect_silver"]
        assert "salinity" not in silver_config.features["sensors"]


class TestMatchesPattern:
    """Test DeviceIdentifier._matches_pattern."""

    def test_exact_substring_match(self):
        assert DeviceIdentifier._matches_pattern("eco elyo jabbour", ["eco"]) is True

    def test_wildcard_prefix(self):
        assert DeviceIdentifier._matches_pattern("LG12345", ["LG*"]) is True

    def test_wildcard_suffix(self):
        assert DeviceIdentifier._matches_pattern("test_LG", ["*LG"]) is True

    def test_wildcard_middle(self):
        assert DeviceIdentifier._matches_pattern("abc.nn_xyz", ["*.nn_*"]) is True

    def test_no_match(self):
        assert DeviceIdentifier._matches_pattern("ABCDEF", ["XY*"]) is False

    def test_case_insensitive(self):
        assert DeviceIdentifier._matches_pattern("lg12345", ["LG*"]) is True
        assert DeviceIdentifier._matches_pattern("LG12345", ["lg*"]) is True

    def test_empty_value(self):
        assert DeviceIdentifier._matches_pattern("", ["LG*"]) is False

    def test_empty_patterns(self):
        assert DeviceIdentifier._matches_pattern("test", []) is False

    def test_multiple_patterns_any_match(self):
        assert DeviceIdentifier._matches_pattern("E30-pump", ["VS*", "E30*", "LE*"]) is True


class TestIdentifyDevice:
    """Test DeviceIdentifier.identify_device."""

    def test_identify_lg_heat_pump(self):
        device = {
            "device_id": "LG12345678",
            "name": "Eco Elyo",
            "family": "eco elyo",
            "model": "astralpool",
            "type": "heat_pump",
        }
        config = DeviceIdentifier.identify_device(device)
        assert config is not None
        assert config.device_type == "heat_pump"

    def test_hpgic_gre_identified_over_lg_eco_elyo(self):
        """Gre HPGIC heat pump (LG-prefixed serial) matches its own profile, not LG Eco Elyo (Issue #92)."""
        device = {
            "device_id": "LG25363734.nn_1",  # LG-prefixed → would score 60 on lg_heat_pump
            "name": "HPGIC GRE",
            "family": "Heat Pumps",
            "model": "HPGIC GRE",
            "type": "heat_pump",
            "components": {"7": {"reportedValue": "CXWAB0103544325004"}},  # LG Eco Elyo is BXWAA
        }
        config = DeviceIdentifier.identify_device(device)
        assert config is DEVICE_CONFIGS["hpgic_gre_heat_pump"]
        # Confirmed mapping (c13/c14/c15/c19) + widened scan to surface the current-temp component.
        assert config.features["specific_components"] == [7, 13, 14, 15, 17, 19, 28, 67, 81, 82]

    def test_identify_pump_by_id(self):
        device = {
            "device_id": "E30-12345",
            "name": "My Pump",
            "family": "",
            "model": "",
            "type": "pump",
        }
        config = DeviceIdentifier.identify_device(device)
        assert config is not None
        assert config.device_type == "pump"

    def test_identify_victoria_smart_connect_pump(self):
        """Victoria Smart Connect VS matches its own profile by model, with the
        string-register decoder flag and the read-side sensors wired from the
        captures in Issue #144 (c14/c16/c17/c18/c21/c22/c24). Still unverified:
        the write path (start/stop, speed) is unknown."""
        device = {
            "device_id": "170125500054",  # numeric serial, no E30*/LE*/PUMP* prefix
            "name": "Victoria Smart Connect VS",
            "family": "Filtration Pumps",
            "model": "Victoria Smart Connect VS",
            "type": "pump",
        }
        config = DeviceIdentifier.identify_device(device)
        assert config is not None
        assert config.device_type == "pump"
        assert config.verified is False  # write path not yet confirmed
        assert config.features["victoria_vs_mode"] is True
        # Decoded read registers must all be scanned.
        for component in (14, 16, 17, 18, 20, 21, 22, 24):
            assert component in config.features["specific_components"]
        # Read-side entities: state/speed/power/head sensors.
        for entity in ("sensor_speed", "sensor_power", "sensor_head"):
            assert entity in config.entities

    def test_identify_chlorinator_bridged(self):
        device = {
            "device_id": "AB123.nn_456",
            "name": "Chlorinator",
            "family": "chlorinator",
            "model": "",
            "type": "chlorinator",
        }
        config = DeviceIdentifier.identify_device(device)
        assert config is not None
        assert config.device_type == "chlorinator"

    def test_identify_specific_chlorinator_by_prefix(self):
        device = {
            "device_id": "CC24033907-test",
            "name": "My Chlorinator",
            "family": "chlorinator",
            "model": "",
            "type": "chlorinator",
        }
        config = DeviceIdentifier.identify_device(device)
        assert config is not None
        assert config.device_type == "chlorinator"

    def test_identify_z550_heat_pump(self):
        device = {
            "device_id": "LD12345",
            "name": "Z550iQ",
            "family": "heat pump",
            "model": "",
            "type": "heat_pump",
        }
        config = DeviceIdentifier.identify_device(device)
        assert config is not None
        assert config.device_type == "heat_pump"
        assert config.features.get("z550_mode") is True

    def test_z550_heat_pump_exposes_running_hours_and_flow_scan(self):
        """Z550iQ+ scans the running-hours (60) and flow (18) components and exposes a running-hours sensor (Issue #88)."""
        config = DEVICE_CONFIGS["z550iq_heat_pump"]
        assert "sensor_running_hours" in config.entities
        specific = config.features["specific_components"]
        assert 60 in specific  # total running hours
        assert 18 in specific  # water-flow indicator
        # Presets stay disabled for this unit (component 17 is read-only).
        assert config.features.get("preset_modes") is not True

    def test_skip_bridge_devices(self):
        device = {
            "device_id": "BR123",
            "name": "Bridge",
            "family": "bridge",
            "model": "",
            "type": "bridge",
        }
        config = DeviceIdentifier.identify_device(device)
        assert config is None

    def test_fallback_to_generic_pump(self):
        device = {
            "device_id": "UNKNOWN123",
            "name": "Unknown",
            "family": "",
            "model": "",
            "type": "pump",
        }
        config = DeviceIdentifier.identify_device(device)
        # An unknown pump (type-only match) must fall through to the generic
        # config, not a device-specific one (e.g. e30iq_pump) — see Issue #1.
        assert config is DEVICE_CONFIGS["generic_pump"]

    def test_fallback_to_generic_heat_pump(self):
        device = {
            "device_id": "UNKNOWN",
            "name": "",
            "family": "",
            "model": "",
            "type": "heat_pump",
        }
        config = DeviceIdentifier.identify_device(device)
        # Must not resolve to lg_heat_pump (priority 100) on a bare type match.
        assert config is DEVICE_CONFIGS["generic_heat_pump"]

    def test_fallback_to_generic_light(self):
        device = {
            "device_id": "UNKNOWN",
            "name": "",
            "family": "",
            "model": "",
            "type": "light",
        }
        config = DeviceIdentifier.identify_device(device)
        assert config is not None
        assert config.device_type == "light"

    def test_identify_ns25_exo_chlorinator(self):
        device = {
            "device_id": "NS25003678",
            "name": "Zodiac EXO iQ 35",
            "family": "Chlorinators",
            "model": "",
            "type": "connected",
        }
        config = DeviceIdentifier.identify_device(device)
        assert config is not None
        assert config.device_type == "chlorinator"
        assert config.features.get("exo_mode") is True
        # on_off_component removed - mode select (AUTO/ON/OFF) replaces ON/OFF switch
        assert config.features.get("on_off_component") is None
        assert config.features.get("chlorination_level") == 38
        # boost_mode removed - API returns 403 + unreadable for EXO
        assert config.features.get("boost_mode") is None
        assert config.features.get("mode_component") == 13
        assert config.features.get("schedules") is True
        assert config.features.get("orp_setpoint") == 39
        assert config.features.get("ph_setpoint") == 40
        assert config.features.get("ph_setpoint_divisor") == 10
        assert config.features["sensors"]["ph"] == 62
        assert config.features["sensors"]["orp"] == 63
        assert config.features["sensors"]["temperature"] == 64
        # Salinity is not mapped: the eXO iQ exposes no live salinity over the API;
        # c36 was a static low-salt threshold, not a probe reading (Issue #143).
        assert "salinity" not in config.features["sensors"]

    def test_invalid_input_returns_none(self):
        assert DeviceIdentifier.identify_device(None) is None
        assert DeviceIdentifier.identify_device("not a dict") is None
        assert DeviceIdentifier.identify_device(42) is None

    def test_empty_device_no_match(self):
        device = {
            "device_id": "",
            "name": "",
            "family": "",
            "model": "",
            "type": "",
        }
        config = DeviceIdentifier.identify_device(device)
        assert config is None


class TestShouldCreateEntity:
    """Test DeviceIdentifier.should_create_entity."""

    def test_pump_has_switch(self):
        device = {"device_id": "E30-123", "name": "", "family": "", "model": "", "type": "pump"}
        assert DeviceIdentifier.should_create_entity(device, "switch") is True

    def test_pump_no_climate(self):
        device = {"device_id": "E30-123", "name": "", "family": "", "model": "", "type": "pump"}
        assert DeviceIdentifier.should_create_entity(device, "climate") is False

    def test_heat_pump_has_climate(self):
        device = {"device_id": "LG12345", "name": "Eco", "family": "eco elyo", "model": "", "type": "heat_pump"}
        assert DeviceIdentifier.should_create_entity(device, "climate") is True

    def test_unknown_device_returns_false(self):
        assert DeviceIdentifier.should_create_entity({}, "switch") is False


class TestGetComponentsRange:
    """Test DeviceIdentifier.get_components_range."""

    def test_default_for_unknown(self):
        assert DeviceIdentifier.get_components_range({}) == 25

    def test_pump_components_range(self):
        device = {"device_id": "E30-123", "name": "", "family": "", "model": "", "type": "pump"}
        assert DeviceIdentifier.get_components_range(device) == 5


class TestHasFeature:
    """Test DeviceIdentifier.has_feature and get_feature."""

    def test_has_feature_true(self):
        device = {"device_id": "E30-123", "name": "", "family": "", "model": "", "type": "pump"}
        assert DeviceIdentifier.has_feature(device, "speed_control") is True

    def test_has_feature_false(self):
        device = {"device_id": "E30-123", "name": "", "family": "", "model": "", "type": "pump"}
        assert DeviceIdentifier.has_feature(device, "temperature_control") is False

    def test_has_feature_unknown_device(self):
        assert DeviceIdentifier.has_feature({}, "anything") is False

    def test_get_feature_value(self):
        device = {"device_id": "E30-123", "name": "", "family": "", "model": "", "type": "pump"}
        assert DeviceIdentifier.get_feature(device, "schedule_count") == 8

    def test_get_feature_default(self):
        device = {"device_id": "E30-123", "name": "", "family": "", "model": "", "type": "pump"}
        assert DeviceIdentifier.get_feature(device, "nonexistent", "default_val") == "default_val"

    def test_get_feature_unknown_device(self):
        assert DeviceIdentifier.get_feature({}, "anything", 42) == 42


class TestCheckComponentSignature:
    """Test DeviceIdentifier._check_component_signature."""

    def test_matches_component_value(self):
        device = {
            "components": {
                "7": {"reportedValue": "BXWAA-something"},
            },
        }
        assert DeviceIdentifier._check_component_signature(device, 7, ["BXWAA"]) is True

    def test_no_match(self):
        device = {
            "components": {
                "7": {"reportedValue": "OTHER"},
            },
        }
        assert DeviceIdentifier._check_component_signature(device, 7, ["BXWAA"]) is False

    def test_missing_component(self):
        device = {"components": {}}
        assert DeviceIdentifier._check_component_signature(device, 7, ["BXWAA"]) is False

    def test_no_components(self):
        device = {}
        assert DeviceIdentifier._check_component_signature(device, 7, ["BXWAA"]) is False


class TestTecnoLC2Signature:
    """Signature fallback: unknown-serial tecnoLC2 chlorinators routed off the catch-all.

    tecnoLC2 units report a generic model/name, so an unknown serial lands on the
    domoticS2 catch-all, which misreads c172 (water temperature) as pH. When c8 (the
    domoticS2 pH setpoint) is blank and c172 is in the temperature band, the device is
    really tecnoLC2 and must get the standard tecnoLC2 registers (Issues #145/#149/#151/
    #152/#153...).
    """

    @staticmethod
    def _device(device_id, **components):
        return {
            "device_id": device_id,
            "name": "Chlorinator",
            "family": "Chlorinators",
            "model": "",
            "type": "chlorinator",
            "components": {str(k): {"reportedValue": v} for k, v in components.items()},
        }

    @staticmethod
    def _name(config):
        return next((n for n, c in DEVICE_CONFIGS.items() if c is config), None)

    def test_unknown_tecnolc2_routes_to_signature_profile(self):
        """Blank c8 + c172 in the temperature band -> standard tecnoLC2 profile, verified."""
        config = DeviceIdentifier.identify_device(self._device("CC29999999.nn_1", **{"8": 0, "172": 303}))
        assert self._name(config) == "tecnolc2_signature"
        assert config.verified is True
        assert config.features["sensors"] == {"ph": 165, "orp": 170, "temperature": 172, "salinity": 174}
        assert config.features["chlorination_level"] == 10

    def test_unknown_domotics2_stays_on_catch_all(self):
        """A real domoticS2 unit keeps its pH setpoint on c8, so it is never re-routed."""
        config = DeviceIdentifier.identify_device(self._device("DM99999999.nn_1", **{"8": 740, "172": 704}))
        assert self._name(config) == "chlorinator"
        assert config.verified is False

    def test_known_serial_is_not_overridden(self):
        """A device matching a dedicated serial profile keeps it, signature notwithstanding."""
        config = DeviceIdentifier.identify_device(self._device("CC25060723.nn_1", **{"8": 0, "172": 303}))
        assert self._name(config) == "cc25019224_chlorinator"

    def test_c172_in_ph_band_is_not_tecnolc2(self):
        """Blank c8 but c172 in the pH band (704 = 7.04) must not trigger the signature."""
        config = DeviceIdentifier.identify_device(self._device("CC29999997.nn_1", **{"8": 0, "172": 704}))
        assert self._name(config) == "chlorinator"

    def test_missing_c172_falls_back_to_catch_all(self):
        """No temperature reading yet -> no false positive, stays on the catch-all."""
        config = DeviceIdentifier.identify_device(self._device("CC29999998.nn_1", **{"8": 0}))
        assert self._name(config) == "chlorinator"

    def test_signature_activates_after_components_are_scanned(self):
        """The override is re-evaluated post-cache: it flips once c8/c172 are present."""
        device = self._device("CC29990002.nn_1")
        device["components"] = {}
        assert self._name(DeviceIdentifier.identify_device(device)) == "chlorinator"
        device["components"] = {"8": {"reportedValue": 0}, "172": {"reportedValue": 303}}
        assert self._name(DeviceIdentifier.identify_device(device)) == "tecnolc2_signature"
