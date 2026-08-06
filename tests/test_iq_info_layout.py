"""The iQ heat-pump info layout: c0=hours, c1=RSSI, c2=IP, c3=serial, c4=firmware.

@Kal42's live Z250iQ register map (Issue #139) documents these slots explicitly.
Reading c2 as the RSSI — the standard Fluidra layout — hands the signal sensor an
IP address string, which is why it reported unknown on a real Z250iQ (Issue #183).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from custom_components.fluidra_pool.device_registry import DEVICE_CONFIGS, DeviceIdentifier

# As reported by a Z250iQ (Issue #139): the RSSI is negative dBm on c1 and c2
# carries a dotted-quad string that float() cannot parse.
Z250IQ_INFO = {0: 161, 1: -82, 2: "192.168.1.42", 3: "LF25012345", 4: "1.2.3"}


def _device(name: str = "Z250iQ") -> dict[str, Any]:
    return {
        "device_id": "LF25012345",
        "name": name,
        "family": "heat pump",
        "type": "heat_pump",
        "model": "",
        "components": {},
    }


def test_z250iq_reads_the_rssi_from_c1_not_c2() -> None:
    """The whole point of Issue #183: c2 is an IP address here, not a signal."""
    from custom_components.fluidra_pool.coordinator import FluidraDataUpdateCoordinator

    device = _device()
    for component_id, value in Z250IQ_INFO.items():
        FluidraDataUpdateCoordinator._process_component_state(
            MagicMock(), device, "pool-1", component_id, {"reportedValue": value}
        )

    assert device["signal_strength_component"] == -82
    assert device["signal_strength_component"] != Z250IQ_INFO[2]


def test_z250iq_maps_serial_firmware_and_hours() -> None:
    """The same layout shifts serial to c3 and firmware to c4."""
    from custom_components.fluidra_pool.coordinator import FluidraDataUpdateCoordinator

    device = _device()
    for component_id, value in Z250IQ_INFO.items():
        FluidraDataUpdateCoordinator._process_component_state(
            MagicMock(), device, "pool-1", component_id, {"reportedValue": value}
        )

    assert device["device_id_component"] == "LF25012345"
    assert device["firmware_version_component"] == "1.2.3"
    assert device["running_hours"] == 161


@pytest.mark.parametrize("profile", ["z250iq_heat_pump", "z260iq_heat_pump"])
def test_lf_profiles_declare_the_iq_layout(profile: str) -> None:
    assert DEVICE_CONFIGS[profile].features.get("info_layout") == "iq_heat_pump"


@pytest.mark.parametrize("name", ["Z250iQ", "Zodiac Z250iQ", "Z260iQ", "Heat pump"])
def test_lf_devices_resolve_to_a_profile_with_the_iq_layout(name: str) -> None:
    """Asserted through identification: LF* units split across two profiles."""
    config = DeviceIdentifier.identify_device(_device(name))
    assert config is not None
    assert config.features.get("info_layout") == "iq_heat_pump"
    assert "sensor_wifi_signal" in config.entities


def test_standard_layout_still_reads_the_rssi_from_c2() -> None:
    """Devices without the override must keep the default slots."""
    from custom_components.fluidra_pool.coordinator import FluidraDataUpdateCoordinator

    device = {
        "device_id": "DM24008702.nn_1",
        "name": "Chlorinator",
        "family": "Chlorinators",
        "type": "chlorinator",
        "model": "Chlorinator",
        "components": {},
    }
    for component_id, value in {0: "DM24008702", 2: -55, 3: "2.1.0"}.items():
        FluidraDataUpdateCoordinator._process_component_state(
            MagicMock(), device, "pool-1", component_id, {"reportedValue": value}
        )

    assert device["signal_strength_component"] == -55
    assert device["device_id_component"] == "DM24008702"
    assert device["firmware_version_component"] == "2.1.0"
