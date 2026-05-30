"""Tests for _process_component_state branches not exercised by test_coordinator.py.

Targets the per-component switch (components 1, 2, 3, 4, 5, 11, 14, 16, 17, 19, 21,
28, 37, 40, 61, 62, 67, light schedule, custom schedule_component).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
import pytest

from custom_components.fluidra_pool.coordinator import FluidraDataUpdateCoordinator


@pytest.fixture
def coordinator(hass: HomeAssistant, mock_api: AsyncMock) -> FluidraDataUpdateCoordinator:
    """Coordinator with mocked API + no config entry (cleanup paths skipped)."""
    return FluidraDataUpdateCoordinator(hass, mock_api)


def _pinned_device(
    device_id: str = "TEST-1",
    *,
    family: str = "",
    device_type: str = "pump",
    features: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a device dict with identify_device pinned to fixed features."""
    return {
        "device_id": device_id,
        "name": "Device",
        "family": family,
        "type": device_type,
        "model": "",
        "online": True,
        "components": {},
        "_identify_cache": {
            "key": (device_id, family, "", device_type, ""),
            "config": SimpleNamespace(
                device_type=device_type,
                features=features or {},
                components_range=25,
                required_components=[0, 1, 2, 3],
                entities=[],
            ),
        },
    }


# --- Components 1, 2, 3, 4, 5 (basic device info) -----------------------


async def test_component_1_sets_part_numbers(coordinator: FluidraDataUpdateCoordinator) -> None:
    device = _pinned_device()
    coordinator._process_component_state(device, "pool_001", 1, {"reportedValue": "PN-123"})
    assert device["part_numbers_component"] == "PN-123"


async def test_component_2_sets_signal_strength_unless_skipped(coordinator: FluidraDataUpdateCoordinator) -> None:
    """RSSI normally lands on component 2."""
    device = _pinned_device()
    coordinator._process_component_state(device, "pool_001", 2, {"reportedValue": -65})
    assert device["signal_strength_component"] == -65


async def test_component_2_skipped_for_devices_with_skip_signal_flag(coordinator: FluidraDataUpdateCoordinator) -> None:
    """DM24049704 sets skip_signal=True because component 2 isn't RSSI for it."""
    device = _pinned_device(features={"skip_signal": True})
    coordinator._process_component_state(device, "pool_001", 2, {"reportedValue": 42})
    assert "signal_strength_component" not in device


async def test_component_3_sets_firmware_unless_skipped(coordinator: FluidraDataUpdateCoordinator) -> None:
    device = _pinned_device()
    coordinator._process_component_state(device, "pool_001", 3, {"reportedValue": "1.2.3"})
    assert device["firmware_version_component"] == "1.2.3"


async def test_component_3_skipped_for_devices_with_skip_firmware_flag(
    coordinator: FluidraDataUpdateCoordinator,
) -> None:
    """CC25005502/DM24049704 set skip_firmware=True."""
    device = _pinned_device(features={"skip_firmware": True})
    coordinator._process_component_state(device, "pool_001", 3, {"reportedValue": "garbage"})
    assert "firmware_version_component" not in device


async def test_component_4_sets_hardware_errors(coordinator: FluidraDataUpdateCoordinator) -> None:
    device = _pinned_device()
    coordinator._process_component_state(device, "pool_001", 4, {"reportedValue": 0})
    assert device["hardware_errors_component"] == 0


async def test_component_5_sets_comm_errors(coordinator: FluidraDataUpdateCoordinator) -> None:
    device = _pinned_device()
    coordinator._process_component_state(device, "pool_001", 5, {"reportedValue": 2})
    assert device["comm_errors_component"] == 2


# --- Z260iQ specific paths (component 0 + 14 + 28 + 67) -----------------


async def test_component_0_records_running_hours_for_z260iq(coordinator: FluidraDataUpdateCoordinator) -> None:
    """Z260iQ overloads component 0 as running hours."""
    device = _pinned_device(features={"z260iq_mode": True})
    coordinator._process_component_state(device, "pool_001", 0, {"reportedValue": 1234})
    assert device["running_hours"] == 1234


async def test_component_0_running_hours_garbage_value_ignored(coordinator: FluidraDataUpdateCoordinator) -> None:
    device = _pinned_device(features={"z260iq_mode": True})
    coordinator._process_component_state(device, "pool_001", 0, {"reportedValue": "not-a-number"})
    assert "running_hours" not in device


async def test_component_14_sets_z260iq_mode_value(coordinator: FluidraDataUpdateCoordinator) -> None:
    device = _pinned_device(features={"z260iq_mode": True})
    coordinator._process_component_state(device, "pool_001", 14, {"reportedValue": 3})
    assert device["z260iq_mode_value"] == 3
    assert device["component_14_data"] == {"reportedValue": 3}


async def test_component_28_no_flow_alarm_for_z260iq(coordinator: FluidraDataUpdateCoordinator) -> None:
    device = _pinned_device(features={"z260iq_mode": True})
    coordinator._process_component_state(device, "pool_001", 28, {"reportedValue": 1})
    assert device["no_flow_alarm"] is True


async def test_component_28_no_flow_alarm_zero_means_ok(coordinator: FluidraDataUpdateCoordinator) -> None:
    device = _pinned_device(features={"z260iq_mode": True})
    coordinator._process_component_state(device, "pool_001", 28, {"reportedValue": 0})
    assert device["no_flow_alarm"] is False


async def test_component_67_air_temperature_for_z260iq(coordinator: FluidraDataUpdateCoordinator) -> None:
    """Component 67 is air temperature ×10 for Z260iQ."""
    device = _pinned_device(features={"z260iq_mode": True})
    coordinator._process_component_state(device, "pool_001", 67, {"reportedValue": 245})
    assert device["air_temperature"] == 24.5


async def test_component_67_out_of_range_temperature_ignored(coordinator: FluidraDataUpdateCoordinator) -> None:
    device = _pinned_device(features={"z260iq_mode": True})
    coordinator._process_component_state(device, "pool_001", 67, {"reportedValue": 9999})
    # 999.9°C is out of the [-30, 60] sanity range.
    assert "air_temperature" not in device


# --- Z550 specific paths (16, 17, 21, 37, 40, 61) -----------------------


async def test_component_16_z550_mode_reported(coordinator: FluidraDataUpdateCoordinator) -> None:
    device = _pinned_device(features={"z550_mode": True})
    coordinator._process_component_state(device, "pool_001", 16, {"reportedValue": 2})
    assert device["z550_mode_reported"] == 2


async def test_component_17_z550_preset_reported(coordinator: FluidraDataUpdateCoordinator) -> None:
    device = _pinned_device(features={"z550_mode": True})
    coordinator._process_component_state(device, "pool_001", 17, {"reportedValue": 1})
    assert device["z550_preset_reported"] == 1


async def test_component_21_z550_on_off_mirrored_to_heat_pump_reported(
    coordinator: FluidraDataUpdateCoordinator,
) -> None:
    """For Z550, component 21 is the ON/OFF state — mirror to is_heating."""
    device = _pinned_device(features={"z550_mode": True})
    coordinator._process_component_state(device, "pool_001", 21, {"reportedValue": 1})
    assert device["heat_pump_reported"] == 1
    assert device["is_heating"] is True


async def test_component_21_falls_back_to_network_status_for_non_z550(
    coordinator: FluidraDataUpdateCoordinator,
) -> None:
    """Without z550_mode, component 21 is just network status."""
    device = _pinned_device()
    coordinator._process_component_state(device, "pool_001", 21, {"reportedValue": "connected"})
    assert device["network_status_component"] == "connected"
    assert "is_heating" not in device


async def test_component_37_z550_water_temperature(coordinator: FluidraDataUpdateCoordinator) -> None:
    device = _pinned_device(features={"z550_mode": True})
    coordinator._process_component_state(device, "pool_001", 37, {"reportedValue": 285})
    assert device["water_temperature"] == 28.5


async def test_component_37_out_of_range_ignored(coordinator: FluidraDataUpdateCoordinator) -> None:
    device = _pinned_device(features={"z550_mode": True})
    coordinator._process_component_state(device, "pool_001", 37, {"reportedValue": 9999})
    assert "water_temperature" not in device


async def test_component_40_z550_air_temperature(coordinator: FluidraDataUpdateCoordinator) -> None:
    device = _pinned_device(features={"z550_mode": True})
    coordinator._process_component_state(device, "pool_001", 40, {"reportedValue": 215})
    assert device["air_temperature"] == 21.5


async def test_component_40_light_schedule_data(coordinator: FluidraDataUpdateCoordinator) -> None:
    """For lights, component 40 stores schedule data."""
    device = _pinned_device(device_type="light")
    schedule = [{"id": 1, "startTime": "0 8 * * 1,2,3", "enabled": True}]
    coordinator._process_component_state(device, "pool_001", 40, {"reportedValue": schedule})
    assert device["schedule_data"] == schedule


@pytest.mark.parametrize(
    ("state_value", "expected_action"),
    [
        (0, "idle"),
        (2, "heating"),
        (3, "cooling"),
        (11, "no_flow"),
        (7, "idle"),  # Unknown state → idle.
    ],
)
async def test_component_61_z550_state_mapped_to_hvac_action(
    coordinator: FluidraDataUpdateCoordinator, state_value: int, expected_action: str
) -> None:
    device = _pinned_device(features={"z550_mode": True})
    coordinator._process_component_state(device, "pool_001", 61, {"reportedValue": state_value})
    assert device["z550_state_reported"] == state_value
    assert device["hvac_action"] == expected_action


# --- Heat-pump generic paths (components 13, 15, 19, 62, 65) ------------


async def test_component_13_heat_pump_excludes_z550(coordinator: FluidraDataUpdateCoordinator) -> None:
    """Z550 uses component 21 for ON/OFF, not 13."""
    device = _pinned_device(device_type="heat_pump", features={"z550_mode": True})
    coordinator._process_component_state(device, "pool_001", 13, {"reportedValue": 1})
    # Z550 path should NOT set heat_pump_reported via component 13.
    assert "heat_pump_reported" not in device


async def test_component_15_heat_pump_target_temperature(coordinator: FluidraDataUpdateCoordinator) -> None:
    """Component 15 raw 290 → 29.0°C target."""
    device = _pinned_device(device_type="heat_pump")
    coordinator._process_component_state(device, "pool_001", 15, {"reportedValue": 290})
    assert device["target_temperature"] == 29.0


async def test_component_15_falls_back_to_desired_value(coordinator: FluidraDataUpdateCoordinator) -> None:
    """When reportedValue is missing, desiredValue is used for the speed and temp."""
    device = _pinned_device(device_type="heat_pump")
    coordinator._process_component_state(device, "pool_001", 15, {"reportedValue": None, "desiredValue": 285})
    assert device["target_temperature"] == 28.5


async def test_component_19_heat_pump_water_temperature(coordinator: FluidraDataUpdateCoordinator) -> None:
    """Component 19 raw 235 → 23.5°C water temp (heat pump)."""
    device = _pinned_device(device_type="heat_pump")
    coordinator._process_component_state(device, "pool_001", 19, {"reportedValue": 235})
    assert device["water_temperature"] == 23.5


async def test_component_19_out_of_range_ignored(coordinator: FluidraDataUpdateCoordinator) -> None:
    device = _pinned_device(device_type="heat_pump")
    coordinator._process_component_state(device, "pool_001", 19, {"reportedValue": 9999})
    assert "water_temperature" not in device


async def test_component_62_heat_pump_water_temp(coordinator: FluidraDataUpdateCoordinator) -> None:
    """Components 62 and 65 are alternate water-temp paths used by some heat pumps."""
    device = _pinned_device(device_type="heat_pump")
    coordinator._process_component_state(device, "pool_001", 62, {"reportedValue": 215})
    assert device["water_temperature"] == 21.5


async def test_component_65_does_not_override_existing_water_temp(coordinator: FluidraDataUpdateCoordinator) -> None:
    """If water_temperature is already set (e.g. from component 19), 65 doesn't clobber."""
    device = _pinned_device(device_type="heat_pump")
    device["water_temperature"] = 24.0
    coordinator._process_component_state(device, "pool_001", 65, {"reportedValue": 999})
    assert device["water_temperature"] == 24.0


# --- Component 11 — auto-mode speed calculation -------------------------


async def test_component_11_speed_zero_when_not_running(coordinator: FluidraDataUpdateCoordinator) -> None:
    device = _pinned_device(device_type="pump")
    device["is_running"] = False
    coordinator._process_component_state(device, "pool_001", 11, {"reportedValue": 1})
    assert device["speed_percent"] == 0


@pytest.mark.parametrize(
    ("speed_level", "expected_percent"),
    [(0, 45), (1, 65), (2, 100), (99, 0)],
)
async def test_component_11_manual_mode_maps_level_to_percent(
    coordinator: FluidraDataUpdateCoordinator, speed_level: int, expected_percent: int
) -> None:
    device = _pinned_device(device_type="pump")
    device["is_running"] = True
    device["auto_mode_enabled"] = False
    coordinator._process_component_state(device, "pool_001", 11, {"reportedValue": speed_level})
    assert device["speed_percent"] == expected_percent


# --- Custom schedule_component (DM24049704 = 258) -----------------------


async def test_custom_schedule_component_parses_programs_format(coordinator: FluidraDataUpdateCoordinator) -> None:
    """DM24049704 schedule on component 258 is decoded from programs/slots."""
    device = _pinned_device(device_type="chlorinator", features={"schedule_component": 258})
    raw = {
        "dayPrograms": {"monday": 1},
        "programs": [{"id": 1, "slots": [{"id": 0, "start": 5 * 256, "end": 6 * 256, "mode": 1}]}],
    }
    coordinator._process_component_state(device, "pool_001", 258, {"reportedValue": raw})
    assert len(device["schedule_data"]) == 1
    assert device["schedule_data"][0]["startTime"] == "0 5 * * 1"


async def test_custom_schedule_component_accepts_list_format(coordinator: FluidraDataUpdateCoordinator) -> None:
    """A list-shaped schedule is forwarded as-is for custom schedule_component."""
    device = _pinned_device(device_type="chlorinator", features={"schedule_component": 258})
    schedule = [{"id": 1, "startTime": "0 8 * * 1", "enabled": True}]
    coordinator._process_component_state(device, "pool_001", 258, {"reportedValue": schedule})
    assert device["schedule_data"] == schedule


async def test_custom_schedule_component_falls_back_to_empty(coordinator: FluidraDataUpdateCoordinator) -> None:
    """Garbage value yields an empty schedule list."""
    device = _pinned_device(device_type="chlorinator", features={"schedule_component": 258})
    coordinator._process_component_state(device, "pool_001", 258, {"reportedValue": "garbage"})
    assert device["schedule_data"] == []


# --- _track_schedule_count + _refresh_pool integration -------------------


async def test_process_component_20_tracks_schedule_count_for_pump(
    coordinator: FluidraDataUpdateCoordinator,
) -> None:
    """A pump schedule update bumps _previous_schedule_entities."""
    device = _pinned_device(device_type="pump")
    coordinator._process_component_state(device, "pool_001", 20, {"reportedValue": [{"id": 1}, {"id": 2}, {"id": 3}]})
    assert coordinator._previous_schedule_entities["pool_001_TEST-1"] == 3


async def test_chlorinator_schedule_on_component_20_tracked(coordinator: FluidraDataUpdateCoordinator) -> None:
    """EXO chlorinators ship schedule data inside component 20 (as list)."""
    device = _pinned_device(device_type="chlorinator")
    schedule = [{"id": 1}, {"id": 2}]
    coordinator._process_component_state(device, "pool_001", 20, {"reportedValue": schedule})
    assert coordinator._previous_schedule_entities["pool_001_TEST-1"] == 2


# --- Blue Connect info-component layout (Issue #69) ----------------------


async def test_blue_connect_component_0_is_signal_not_device_id(
    coordinator: FluidraDataUpdateCoordinator,
) -> None:
    """With the Blue Connect layout, component 0 is the RSSI, not the device id."""
    device = _pinned_device(features={"info_layout": "blue_connect"})
    coordinator._process_component_state(device, "pool_001", 0, {"reportedValue": -44})
    assert device["signal_strength_component"] == -44
    assert "device_id_component" not in device


async def test_blue_connect_component_1_is_device_id(coordinator: FluidraDataUpdateCoordinator) -> None:
    """With the Blue Connect layout, component 1 holds the serial / device id."""
    device = _pinned_device(features={"info_layout": "blue_connect"})
    coordinator._process_component_state(device, "pool_001", 1, {"reportedValue": "QX25002362"})
    assert device["device_id_component"] == "QX25002362"
    assert "part_numbers_component" not in device


async def test_blue_connect_component_2_is_hardware_uid_not_signal(
    coordinator: FluidraDataUpdateCoordinator,
) -> None:
    """With the Blue Connect layout, component 2 is the hardware UID, not RSSI."""
    device = _pinned_device(features={"info_layout": "blue_connect"})
    coordinator._process_component_state(device, "pool_001", 2, {"reportedValue": "AXR080700451258659"})
    assert device["part_numbers_component"] == "AXR080700451258659"
    assert "signal_strength_component" not in device


async def test_standard_layout_unchanged_without_info_layout(
    coordinator: FluidraDataUpdateCoordinator,
) -> None:
    """Devices without info_layout keep the default slot mapping."""
    device = _pinned_device()
    coordinator._process_component_state(device, "pool_001", 0, {"reportedValue": "DEV-ID"})
    coordinator._process_component_state(device, "pool_001", 2, {"reportedValue": -65})
    assert device["device_id_component"] == "DEV-ID"
    assert device["signal_strength_component"] == -65
