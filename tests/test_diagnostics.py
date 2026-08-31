"""Tests for the diagnostics dump shape and redaction rules."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from custom_components.fluidra_pool.diagnostics import (
    REDACTED,
    _redact_component_data,
    _redact_devices_data,
    _redact_pools_data,
    async_get_config_entry_diagnostics,
)


def test_redact_component_data_passthroughs_non_dict_input() -> None:
    """A non-dict component value (e.g. legacy schedule list) is returned untouched."""
    assert _redact_component_data("9", [1, 2, 3]) == [1, 2, 3]


def test_redact_component_data_keeps_numeric_readings() -> None:
    """Numeric component readings (pH, ORP, temperature, …) stay in clear text."""
    redacted = _redact_component_data("9", {"reportedValue": 7.31, "desiredValue": 7.30})
    assert redacted == {"reportedValue": 7.31, "desiredValue": 7.30}


def test_redact_component_data_redacts_serial_in_identifier_slot() -> None:
    """Components 1, 2, 6, 8 carry device identifiers — redact their string values."""
    redacted = _redact_component_data("1", {"reportedValue": "QX25002362"})
    assert redacted["reportedValue"] == REDACTED


def test_redact_component_data_redacts_mac_uid_in_identifier_slot() -> None:
    """Component 2 hardware UID strings are redacted."""
    redacted = _redact_component_data("2", {"reportedValue": "AXR080700451258659"})
    assert redacted["reportedValue"] == REDACTED


def test_redact_component_data_redacts_ip_in_identifier_slot() -> None:
    """Component 8 IP addresses are redacted."""
    redacted = _redact_component_data("8", {"reportedValue": "192.168.210.131"})
    assert redacted["reportedValue"] == REDACTED


def test_redact_component_data_keeps_signal_strength_number_in_slot_0() -> None:
    """Component 0 numeric values (signal strength dBm) stay in clear."""
    redacted = _redact_component_data("0", {"reportedValue": -44})
    assert redacted["reportedValue"] == -44


def test_redact_component_data_pattern_redacts_ip_in_unexpected_slot() -> None:
    """Defensive: an IP that surfaces in a non-identifier slot is still redacted."""
    redacted = _redact_component_data("99", {"reportedValue": "10.0.0.42"})
    assert redacted["reportedValue"] == REDACTED


def test_redact_component_data_pattern_redacts_fluidra_serial_in_unexpected_slot() -> None:
    """Defensive: a Fluidra-style serial in any slot is redacted."""
    redacted = _redact_component_data("12", {"reportedValue": "LC25000122"})
    assert redacted["reportedValue"] == REDACTED


def test_redact_component_data_keeps_thingtype_signature() -> None:
    """Component 7 (thingType: BC3, BXWAA, …) stays in clear — useful for debugging."""
    redacted = _redact_component_data("7", {"reportedValue": "BC3"})
    assert redacted["reportedValue"] == "BC3"


def test_redact_pools_data_empty_returns_empty_dict() -> None:
    """Empty coordinator data produces an empty diagnostics block."""
    assert _redact_pools_data({}) == {}


def test_redact_pools_data_redacts_pool_id_in_key_but_keeps_values() -> None:
    """Pool IDs are hashed in the dict key; non-sensitive fields stay readable."""
    redacted = _redact_pools_data(
        {
            "real-pool-uuid-1234": {
                "id": "real-pool-uuid-1234",
                "name": "Family Pool",
                "devices": [],
                "water_quality": {"status": "ok"},
            }
        }
    )

    # The outer key no longer leaks the real UUID.
    assert "real-pool-uuid-1234" not in redacted
    pool_key = next(iter(redacted))
    assert pool_key.startswith("pool_")
    assert redacted[pool_key]["name"] == "Family Pool"
    assert redacted[pool_key]["water_quality"] == {"status": "ok"}


def test_redact_pools_data_redacts_inner_pool_id() -> None:
    """The pool id anonymised in the dict key must not also leak in clear in the value."""
    redacted = _redact_pools_data(
        {
            "real-pool-uuid-1234": {
                "id": "real-pool-uuid-1234",
                "name": "Family Pool",
                "devices": [],
            }
        }
    )
    pool_key = next(iter(redacted))
    assert redacted[pool_key]["id"] == REDACTED
    assert redacted[pool_key]["name"] == "Family Pool"


def test_redact_pools_data_handles_non_dict_pool_payloads() -> None:
    """A non-dict pool payload is forwarded as-is (defensive fallback)."""
    redacted = _redact_pools_data({"pool-abc": "unexpected"})
    pool_key = next(iter(redacted))
    assert redacted[pool_key] == "unexpected"


def test_redact_devices_data_replaces_real_id_with_index() -> None:
    """Each device gets an opaque \"_device_index\" instead of its real id."""
    redacted = _redact_devices_data(
        [
            {"device_id": "LE24500883", "name": "Pump", "components": {"9": {"reportedValue": 1}}},
            {"device_id": "CC24009711.nn_1", "name": "Chlorinator"},
        ]
    )

    assert redacted[0]["_device_index"] == 0
    assert redacted[0]["device_id"] == REDACTED
    # Component data shape is preserved with values kept in clear text.
    assert redacted[0]["components"]["9"]["reportedValue"] == 1
    assert redacted[1]["_device_index"] == 1
    assert redacted[1]["device_id"] == REDACTED


def test_redact_devices_data_keeps_non_dict_entries() -> None:
    """Non-dict device entries are not transformed (no crash on garbage data)."""
    assert _redact_devices_data(["not-a-dict"]) == ["not-a-dict"]


def test_redact_devices_data_empty_returns_empty_list() -> None:
    """An empty device list does not raise and returns an empty list."""
    assert _redact_devices_data([]) == []


def test_redact_devices_data_redacts_identifier_mirror_fields() -> None:
    """Coordinator-extracted identifier fields (e.g. signal_strength_component) are redacted."""
    redacted = _redact_devices_data(
        [
            {
                "device_id": "DEV-1",
                "part_numbers_component": "QX25002362",
                "signal_strength_component": "AXR080700451258659",
                "device_id_component": -44,  # Number — kept in clear (real signal strength).
                "components": {},
            }
        ]
    )
    assert redacted[0]["part_numbers_component"] == REDACTED
    assert redacted[0]["signal_strength_component"] == REDACTED
    # The coordinator names are misleading: comp 0 numeric (= signal) stays in clear.
    assert redacted[0]["device_id_component"] == -44


def test_redact_devices_data_redacts_serial_in_status() -> None:
    """The raw tree entry under device["status"]["id"] carries the serial — redact it."""
    redacted = _redact_devices_data(
        [
            {
                "device_id": "DEV-1",
                "status": {"id": "LE24500883", "connectivity": {"connected": True}},
                "components": {},
            }
        ]
    )
    assert redacted[0]["status"]["id"] == REDACTED
    # Telemetry nested in status stays readable for debugging.
    assert redacted[0]["status"]["connectivity"] == {"connected": True}


def test_redact_devices_data_redacts_bridge_children_ids_in_status() -> None:
    """Bridge child device ids nested under status["devices"] are also serials — redact them."""
    redacted = _redact_devices_data(
        [
            {
                "device_id": "DEV-1",
                "status": {"id": "LE24500883", "devices": [{"id": "CC25052635"}]},
                "components": {},
            }
        ]
    )
    assert redacted[0]["status"]["devices"][0]["id"] == REDACTED


def test_redact_devices_data_redacts_scalar_identifiers_via_pattern() -> None:
    """Defence in depth: a serial-looking string at the device level is redacted."""
    redacted = _redact_devices_data([{"device_id": "DEV-1", "alias_field": "QX25002362", "components": {}}])
    assert redacted[0]["alias_field"] == REDACTED


def test_redact_devices_data_redacts_alias_and_session_identifier() -> None:
    """`alias`, `bleAccessCode`, `sessionIdentifier` are redacted via TO_REDACT."""
    redacted = _redact_devices_data(
        [
            {
                "device_id": "DEV-1",
                "alias": "Svendborgvej",
                "bleAccessCode": "84FA7F971",
                "components": {},
                "connectivity": {"sessionIdentifier": "uuid-1234"},
            }
        ]
    )
    assert redacted[0]["alias"] == REDACTED
    assert redacted[0]["bleAccessCode"] == REDACTED
    assert redacted[0]["connectivity"]["sessionIdentifier"] == REDACTED


def test_redact_devices_data_keeps_telemetry_in_components() -> None:
    """Telemetry components (pH=13, ORP=14, …) stay in clear after the redaction pass."""
    redacted = _redact_devices_data(
        [
            {
                "device_id": "DEV-1",
                "components": {
                    "1": {"reportedValue": "QX25002362"},  # Serial — redacted.
                    "13": {"reportedValue": 7.3},  # pH — kept.
                    "14": {"reportedValue": 764},  # ORP — kept.
                },
            }
        ]
    )
    assert redacted[0]["components"]["1"]["reportedValue"] == REDACTED
    assert redacted[0]["components"]["13"]["reportedValue"] == 7.3
    assert redacted[0]["components"]["14"]["reportedValue"] == 764


async def test_async_get_config_entry_diagnostics_redacts_credentials_only() -> None:
    """The full diagnostics dump hides credentials but keeps telemetry readable."""
    coordinator = SimpleNamespace(
        data={
            "pool-abc": {
                "id": "pool-abc",
                "name": "Pool",
                "devices": [
                    {
                        "device_id": "LE24500883",
                        "components": {"7": {"reportedValue": 720}},
                    }
                ],
            }
        },
        last_update_success=True,
        update_interval=timedelta(seconds=30),
    )

    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.version = 1
    entry.domain = "fluidra_pool"
    entry.title = "Fluidra Pool"
    entry.data = {"email": "foxace@gmail.com", "password": "secret", "refresh_token": "rt"}
    entry.options = {}
    entry.runtime_data = SimpleNamespace(coordinator=coordinator)

    diagnostics = await async_get_config_entry_diagnostics(None, entry)

    # Credentials hidden.
    assert diagnostics["config_entry"]["data"]["email"] == REDACTED
    assert diagnostics["config_entry"]["data"]["password"] == REDACTED
    # Coordinator status preserved.
    assert diagnostics["coordinator"]["last_update_success"] is True
    assert diagnostics["coordinator"]["update_interval"] == "0:00:30"
    # Pool readings retained for debugging.
    pool_block = next(iter(diagnostics["pools"].values()))
    assert pool_block["devices"][0]["components"]["7"]["reportedValue"] == 720


async def test_diagnostics_carry_the_lost_writes_trace() -> None:
    """The dump must expose writes the cloud accepted and never applied.

    This is what turns "my setpoint keeps reverting" into something measurable
    for a bug report (Issues #133, #195), so it has to survive the redaction
    pass — the verifier already masks the device id.
    """
    lost = [{"device_id": "LE2***883", "component_id": 8, "desired_value": 730.0}]
    coordinator = SimpleNamespace(
        data={},
        last_update_success=True,
        update_interval=timedelta(seconds=30),
        lost_writes=lost,
    )

    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.version = 1
    entry.domain = "fluidra_pool"
    entry.title = "Fluidra Pool"
    entry.data = {}
    entry.options = {}
    entry.runtime_data = SimpleNamespace(coordinator=coordinator)

    diagnostics = await async_get_config_entry_diagnostics(None, entry)

    assert diagnostics["lost_writes"] == lost


async def test_diagnostics_report_no_lost_writes_when_none_were_seen() -> None:
    """A healthy account gets an empty list, not a missing key."""
    coordinator = SimpleNamespace(
        data={},
        last_update_success=True,
        update_interval=timedelta(seconds=30),
    )

    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.version = 1
    entry.domain = "fluidra_pool"
    entry.title = "Fluidra Pool"
    entry.data = {}
    entry.options = {}
    entry.runtime_data = SimpleNamespace(coordinator=coordinator)

    diagnostics = await async_get_config_entry_diagnostics(None, entry)

    assert diagnostics["lost_writes"] == []


async def test_diagnostics_dump_every_register_of_an_unverified_device() -> None:
    """A guessed profile must ship its whole register set, not just what it reads.

    The generic heat-pump profile polls components 0-3 plus 13/14/15, so the
    pools block can only ever contain those. That is exactly the device for which
    nobody knows the map yet: Issue #221's Z350iQ reads OFF because component 13
    is not its ON/OFF flag, and no export could show which register is.
    """
    device = {
        "device_id": "FE25000001",
        "name": "Z350iQ",
        "family": "Heat Pumps",
        "type": "heat_pump",
        "thing_type": "hpc",
        "components": {"13": {"reportedValue": 0}, "15": {"reportedValue": 280}},
    }
    api = SimpleNamespace(
        get_all_components=AsyncMock(
            return_value={
                1: {"reportedValue": "FE25000001"},
                13: {"reportedValue": 0},
                21: {"reportedValue": 1},
                37: {"reportedValue": 274},
            }
        )
    )
    coordinator = SimpleNamespace(
        data={"pool-abc": {"id": "pool-abc", "devices": [device]}},
        last_update_success=True,
        update_interval=timedelta(seconds=30),
        api=api,
    )

    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.version = 1
    entry.domain = "fluidra_pool"
    entry.title = "Fluidra Pool"
    entry.data = {}
    entry.options = {}
    entry.runtime_data = SimpleNamespace(coordinator=coordinator)

    diagnostics = await async_get_config_entry_diagnostics(None, entry)

    dumped = diagnostics["unverified_devices"]
    assert len(dumped) == 1
    assert dumped[0]["profile"] == "generic_heat_pump"
    # The family id is what places an unreported model on a register map.
    assert dumped[0]["thing_type"] == "hpc"
    assert dumped[0]["scanned_components"] == [13, 15]
    # Registers the profile never polls — the whole point of the block.
    assert dumped[0]["all_registers"]["21"]["reportedValue"] == 1
    assert dumped[0]["all_registers"]["37"]["reportedValue"] == 274
    # Redaction still applies: component 1 carries the serial.
    assert dumped[0]["all_registers"]["1"]["reportedValue"] == REDACTED
    assert dumped[0]["device_id"] != "FE25000001"


async def test_diagnostics_skip_the_register_dump_for_verified_devices() -> None:
    """A verified profile already documents its map — no extra cloud request."""
    device = {
        "device_id": "ZB25000001",
        "name": "Z650iQ",
        "family": "Heat Pumps",
        "type": "heat_pump",
        "components": {"10": {"reportedValue": 1}},
    }
    api = SimpleNamespace(get_all_components=AsyncMock(return_value={10: {"reportedValue": 1}}))
    coordinator = SimpleNamespace(
        data={"pool-abc": {"id": "pool-abc", "devices": [device]}},
        last_update_success=True,
        update_interval=timedelta(seconds=30),
        api=api,
    )

    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.version = 1
    entry.domain = "fluidra_pool"
    entry.title = "Fluidra Pool"
    entry.data = {}
    entry.options = {}
    entry.runtime_data = SimpleNamespace(coordinator=coordinator)

    diagnostics = await async_get_config_entry_diagnostics(None, entry)

    assert diagnostics["unverified_devices"] == []
    api.get_all_components.assert_not_awaited()


async def test_diagnostics_survive_a_refused_register_dump() -> None:
    """A cloud that refuses the bulk endpoint must not fail the whole download."""
    device = {
        "device_id": "FE25000001",
        "name": "Z350iQ",
        "family": "Heat Pumps",
        "type": "heat_pump",
        "thing_type": "hpc",
        "components": {},
    }
    api = SimpleNamespace(get_all_components=AsyncMock(side_effect=TimeoutError))
    coordinator = SimpleNamespace(
        data={"pool-abc": {"id": "pool-abc", "devices": [device]}},
        last_update_success=True,
        update_interval=timedelta(seconds=30),
        api=api,
    )

    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.version = 1
    entry.domain = "fluidra_pool"
    entry.title = "Fluidra Pool"
    entry.data = {}
    entry.options = {}
    entry.runtime_data = SimpleNamespace(coordinator=coordinator)

    diagnostics = await async_get_config_entry_diagnostics(None, entry)

    assert diagnostics["unverified_devices"] == []
    assert diagnostics["coordinator"]["last_update_success"] is True
