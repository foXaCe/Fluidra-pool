"""Identification by Fluidra's own family id (``thingType``).

The cloud labels every device with the family it belongs to — measured on the
operator's own hardware and cross-checked against the 124 configFiles the cloud
publishes (``API-DISCOVERY.md`` §9.5). Until now that label drove a single
special case (the tecnoLC2 rescue); these tests cover it as a general signal,
and pin what it must *not* do.
"""

from __future__ import annotations

from typing import Any

from custom_components.fluidra_pool.device_registry import DEVICE_CONFIGS, DeviceIdentifier


def _device(**overrides: Any) -> dict[str, Any]:
    device = {
        "device_id": "UNKNOWN-SERIAL-42",
        "name": "Device",
        "family": "",
        "model": "",
        "type": "",
        "components": {},
    }
    device.update(overrides)
    return device


# --- The signal works --------------------------------------------------------


def test_pump_recognised_by_family_id_alone() -> None:
    """A pump whose serial matches no pattern still lands on the E30iQ profile."""
    device = _device(thing_type="eppvs", type="pump")
    assert DeviceIdentifier.identify_device(device) is DEVICE_CONFIGS["e30iq_pump"]


def test_victoria_recognised_without_its_model_string() -> None:
    """Recognition no longer depends on the cloud spelling out the model."""
    device = _device(thing_type="mppvs", type="pump")
    assert DeviceIdentifier.identify_device(device) is DEVICE_CONFIGS["victoria_smart_connect_pump"]


def test_exo_recognised_beyond_the_ns_serials() -> None:
    """GenSalt OT iQ and Hydroxinator share the eXO family without an NS serial."""
    device = _device(thing_type="exr", type="chlorinator", family="Chlorinators")
    assert DeviceIdentifier.identify_device(device) is DEVICE_CONFIGS["ns25_exo_chlorinator"]


def test_family_id_read_from_the_status_tree_too() -> None:
    """Before discovery copies it, the label lives in the raw status entry."""
    device = _device(type="pump", status={"thingType": "eppvs"})
    assert DeviceIdentifier.identify_device(device) is DEVICE_CONFIGS["e30iq_pump"]


# --- What it must not do -----------------------------------------------------


def test_family_id_arriving_late_invalidates_the_cache() -> None:
    """The label can land after a first identification — the cache must not hide it.

    The device tree may omit ``thingType`` while the status tree, attached later
    in the same poll, carries it. The result is cached on the device dict, so the
    family id has to be part of the cache key or the generic answer sticks.
    """
    device = _device(type="pump")
    assert DeviceIdentifier.identify_device(device) is not DEVICE_CONFIGS["e30iq_pump"]

    device["status"] = {"thingType": "eppvs"}
    assert DeviceIdentifier.identify_device(device) is DEVICE_CONFIGS["e30iq_pump"]


def test_serial_match_still_wins_over_the_family_id() -> None:
    """A profile written for one unit stays more specific than its family.

    The tecnoLC2 family covers dozens of rebadged units with different register
    maps; a serial-specific profile must never be displaced by the family label.
    """
    device = _device(
        device_id="CC25052635.nn_1",
        type="chlorinator",
        family="Chlorinators",
        model="Chlorinator",
        thing_type="exr",  # deliberately the wrong family
    )
    assert DeviceIdentifier.identify_device(device) is DEVICE_CONFIGS["cc25052635_chlorinator"]


def test_unknown_family_id_changes_nothing() -> None:
    """A label no profile claims falls through to the usual catch-all."""
    device = _device(thing_type="brand-new-family", type="pump")
    assert DeviceIdentifier.identify_device(device) is DEVICE_CONFIGS["generic_pump"]


def test_frozen_heat_pump_rules_are_untouched() -> None:
    """Z250iQ/Z260iQ identification is a no-touch zone: no family id declared.

    Their cloud family ("amt") lumps Z250iQ, Z260iQ, PX25, PX26 and Eco Elyo
    together, while the profiles are separated by the component-7 signature —
    declaring the family here would blur exactly what that logic disambiguates.
    """
    for name in ("z250iq_heat_pump", "z260iq_heat_pump", "lg_heat_pump"):
        assert DEVICE_CONFIGS[name].thing_type_patterns == []


def test_ambiguous_heat_pump_family_is_not_declared() -> None:
    """ "nhpp" covers Z450, Z650iQ, Verti/Silent/Eco Elyo — too coarse to route on."""
    assert DEVICE_CONFIGS["z650iq_heat_pump"].thing_type_patterns == []


def test_declared_families_are_the_measured_ones() -> None:
    """Only labels confirmed against the cloud's own configFiles are declared."""
    declared = {
        name: config.thing_type_patterns for name, config in DEVICE_CONFIGS.items() if config.thing_type_patterns
    }
    assert declared == {
        "e30iq_pump": ["eppvs"],
        "victoria_smart_connect_pump": ["mppvs"],
        "ns25_exo_chlorinator": ["exr"],
        "z550iq_heat_pump": ["zs500"],
        "command_connect_cabinet": ["SRC"],
        "blue_connect_silver": ["BC3"],
    }


# --- The family id reaches the bug reports -----------------------------------


def test_unmapped_register_log_names_the_family(caplog: Any) -> None:
    """The one log line users paste into issues must carry the family id.

    Without it a maintainer sees register numbers with nothing to hang them on;
    with it, an unreported model can be matched to a known family straight away
    (README "Adding New Equipment").
    """
    import logging

    from custom_components.fluidra_pool.coordinator import FluidraDataUpdateCoordinator

    coordinator = FluidraDataUpdateCoordinator.__new__(FluidraDataUpdateCoordinator)
    coordinator._unmapped_logged = set()

    with caplog.at_level(logging.DEBUG, logger="custom_components.fluidra_pool.coordinator.coordinator"):
        coordinator._log_unmapped_components(
            "DEV-1", {11: {"reportedValue": 1}, 99: {"reportedValue": 7}}, {11}, "eppvs"
        )

    assert "thing_type=eppvs" in caplog.text
    assert "99" in caplog.text


def test_unmapped_register_log_says_unknown_when_absent(caplog: Any) -> None:
    """A device whose family the cloud never named still logs readably."""
    import logging

    from custom_components.fluidra_pool.coordinator import FluidraDataUpdateCoordinator

    coordinator = FluidraDataUpdateCoordinator.__new__(FluidraDataUpdateCoordinator)
    coordinator._unmapped_logged = set()

    with caplog.at_level(logging.DEBUG, logger="custom_components.fluidra_pool.coordinator.coordinator"):
        coordinator._log_unmapped_components("DEV-1", {99: {"reportedValue": 7}}, set(), "")

    assert "thing_type=unknown" in caplog.text


# --- Blue Connect (Issue #186) -----------------------------------------------


def test_blue_connect_recognised_without_serial_or_name() -> None:
    """A Blue Connect that matches neither WA* nor "gold" still reads correctly.

    Before this, such a unit fell to the chlorinator catch-all, which reads
    c12/c13/c14 as something other than temperature/pH/ORP.
    """
    device = _device(thing_type="BC3", type="chlorinator", family="Data collectors")
    assert DeviceIdentifier.identify_device(device) is DEVICE_CONFIGS["blue_connect_silver"]


def test_blue_connect_family_read_from_component_7() -> None:
    """The line publishes its family id on c7, not only on the device entry."""
    device = _device(
        type="chlorinator",
        family="Data collectors",
        components={"7": {"reportedValue": "BC3"}},
    )
    assert DeviceIdentifier.identify_device(device) is DEVICE_CONFIGS["blue_connect_silver"]


def test_named_gold_still_wins_over_the_shared_family() -> None:
    """ "BC3" covers Silver and Gold alike, so the richer profile must keep priority."""
    device = _device(
        name="Blue Connect Gold",
        thing_type="BC3",
        type="chlorinator",
        family="Data collectors",
    )
    assert DeviceIdentifier.identify_device(device) is DEVICE_CONFIGS["blue_connect_gold"]


def test_component_7_product_codes_do_not_mislabel_heat_pumps() -> None:
    """Heat pumps put a product code on c7; it must match no family pattern."""
    device = _device(
        device_id="LF12345",
        type="heat_pump",
        components={"7": {"reportedValue": "BXWAD"}},
    )
    config = DeviceIdentifier.identify_device(device)
    assert config is DEVICE_CONFIGS["z260iq_heat_pump"]
