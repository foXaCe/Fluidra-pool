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
        assert config is not None
        assert config.device_type == "pump"

    def test_fallback_to_generic_heat_pump(self):
        device = {
            "device_id": "UNKNOWN",
            "name": "",
            "family": "",
            "model": "",
            "type": "heat_pump",
        }
        config = DeviceIdentifier.identify_device(device)
        assert config is not None
        assert config.device_type == "heat_pump"

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
        assert config.features.get("mode_control") is True
        assert config.features.get("mode_component") == 13
        assert config.features.get("schedules") is True
        assert config.features.get("orp_setpoint") == 39
        assert config.features.get("ph_setpoint") == 40
        assert config.features.get("ph_setpoint_divisor") == 10
        assert config.features["sensors"]["ph"] == 62
        assert config.features["sensors"]["orp"] == 63
        assert config.features["sensors"]["temperature"] == 64
        assert config.features["sensors"]["salinity"] == 36

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
