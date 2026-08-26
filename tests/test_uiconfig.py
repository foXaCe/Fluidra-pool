"""Tests for the cloud UI-config reader and parser (plan 013, Pass 3).

The endpoint itself is not reachable yet — it demands an ``appId``/``appVr``
pair the APK never spells out — so the fetch path is tested for what it must
guarantee today: never raise, never claim a register map it does not have.
The parser is tested against the register-block shape the APK does document.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from custom_components.fluidra_pool.api_resilience import FluidraAuthError, FluidraError
from custom_components.fluidra_pool.fluidra_api import (
    FluidraPoolAPI,
    UiRegister,
    build_dynamic_scan_set,
    parse_scheduler_capabilities,
    parse_uiconfig,
)

DEVICE_ID = "TEST-DEV-001"

# One block per shape the doc's register table shows: a plain gauge, a setpoint
# with a write id and a factor, a bounded slider, and a string-typed register.
SAMPLE_BLOCKS = [
    {"readId": 172, "type": "number", "factor": 0.1, "decimals": 1, "min": 0, "max": 50, "units": "°C"},
    {"readId": 165, "writeId": 8, "type": "number", "factor": 0.01, "decimals": 2, "min": 0, "max": 14},
    {
        "readId": 20,
        "writeId": 20,
        "type": "number",
        "factor": 1,
        "decimals": 0,
        "min": 300,
        "max": 850,
        "steps": 10,
        "units": "mV",
    },
    {"readId": 253, "type": "number", "factor": 1, "decimals": 0, "units": "h"},
    {"readId": 6, "type": "string"},
]


def _api() -> FluidraPoolAPI:
    api = FluidraPoolAPI("user@example.invalid", None)
    api.access_token = "token"
    api._build_auth_headers = lambda: {}  # type: ignore[method-assign]
    return api


# --- Parser ------------------------------------------------------------------


def test_parses_a_bare_list_of_blocks() -> None:
    """The simplest envelope: the payload is the list itself."""
    registers = parse_uiconfig(SAMPLE_BLOCKS)
    assert registers is not None
    assert set(registers) == {172, 165, 20, 253, 6}
    assert registers[165].write_id == 8
    assert registers[20].steps == 10
    assert registers[172].units == "°C"


@pytest.mark.parametrize("wrapper", ["configFile", "components", "registers", "uiConfig"])
def test_parses_every_plausible_wrapper(wrapper: str) -> None:
    """The envelope is unconfirmed, so each plausible wrapper is accepted."""
    registers = parse_uiconfig({wrapper: SAMPLE_BLOCKS})
    assert registers is not None
    assert set(registers) == {172, 165, 20, 253, 6}


def test_parses_a_mapping_keyed_by_register_id() -> None:
    """A block may carry no readId of its own when the key already is one."""
    registers = parse_uiconfig({"172": {"factor": 0.1, "decimals": 1}, "253": {"units": "h"}})
    assert registers is not None
    assert set(registers) == {172, 253}
    assert registers[172].factor == 0.1


def test_unrecognisable_payloads_return_none() -> None:
    """None, not an empty map: an empty map reads as "this device has no registers"."""
    for payload in (None, "text", 42, [], {}, [{"no": "readId"}], {"nope": "value"}):
        assert parse_uiconfig(payload) is None


def test_malformed_blocks_are_skipped_not_fatal() -> None:
    """One bad block must not cost the whole map."""
    registers = parse_uiconfig([{"readId": "n/a"}, {"readId": 253, "factor": "x"}, *SAMPLE_BLOCKS])
    assert registers is not None
    assert 253 in registers
    assert registers[253].factor == 1.0  # unparsable factor falls back to neutral


# --- Value decoding ----------------------------------------------------------


def test_decode_applies_factor_and_rounds() -> None:
    """A temperature reported as 213 with factor 0.1 is 21.3 °C."""
    register = UiRegister(read_id=172, factor=0.1, decimals=1)
    assert register.decode(213) == 21.3


def test_decode_rejects_non_numeric() -> None:
    """Same contract as every other decoder in the integration."""
    register = UiRegister(read_id=172, factor=0.1, decimals=1)
    assert register.decode("n/a") is None
    assert register.decode(None) is None


def test_encode_is_the_inverse_and_keeps_whole_values_integral() -> None:
    """pH 7.00 goes out as 700, not 700.0 — desiredValue is sent as-is."""
    register = UiRegister(read_id=165, write_id=8, factor=0.01, decimals=2)
    encoded = register.encode(7.0)
    assert encoded == 700
    assert isinstance(encoded, int)


def test_encode_keeps_a_fractional_result_fractional() -> None:
    """A factor that does not divide cleanly must not be silently rounded away."""
    register = UiRegister(read_id=1, factor=3.0)
    assert register.encode(1.0) == pytest.approx(1 / 3)


def test_encode_refuses_a_zero_factor() -> None:
    """A zero factor would divide by zero rather than mean anything."""
    assert UiRegister(read_id=1, factor=0.0).encode(5) is None


# --- Scan set ----------------------------------------------------------------


def test_scan_set_is_the_union_profile_first() -> None:
    """The profile's list is the decoded truth; the cloud's is a superset."""
    registers = parse_uiconfig(SAMPLE_BLOCKS)
    assert build_dynamic_scan_set([10, 172], registers) == [10, 172, 6, 20, 165, 253]


def test_scan_set_unchanged_without_a_uiconfig() -> None:
    """No cloud map, no change — the behaviour every user has today."""
    assert build_dynamic_scan_set([10, 172], None) == [10, 172]
    assert build_dynamic_scan_set([10, 172], {}) == [10, 172]


# --- Fetch path --------------------------------------------------------------


async def test_fetch_requires_authentication() -> None:
    """Consistent with the other read paths."""
    api = FluidraPoolAPI("user@example.invalid", None)
    api.access_token = None
    with pytest.raises(FluidraAuthError):
        await api.get_device_uiconfig(DEVICE_ID)


async def test_fetch_returns_none_on_the_current_400() -> None:
    """Today's real answer: the endpoint rejects the request for want of appId."""
    api = _api()
    api._request = AsyncMock(  # type: ignore[method-assign]
        return_value=(400, {"message": "Missing required request parameters: [appId]"}, {})
    )
    assert await api.get_device_uiconfig(DEVICE_ID) is None


async def test_fetch_returns_none_on_transport_error() -> None:
    """A failing request leaves the caller on its hand-written profile."""
    api = _api()
    api._request = AsyncMock(side_effect=FluidraError("boom"))  # type: ignore[method-assign]
    assert await api.get_device_uiconfig(DEVICE_ID) is None


async def test_fetch_parses_a_successful_response() -> None:
    """The day the parameters are known, this is the whole path."""
    api = _api()
    api._request = AsyncMock(return_value=(200, {"configFile": SAMPLE_BLOCKS}, {}))  # type: ignore[method-assign]

    registers = await api.get_device_uiconfig(DEVICE_ID)

    assert registers is not None
    assert registers[165].write_id == 8


async def test_fetch_returns_none_on_a_200_it_cannot_read() -> None:
    """A 200 carrying an unknown envelope is not a register map."""
    api = _api()
    api._request = AsyncMock(return_value=(200, {"unexpected": "envelope"}, {}))  # type: ignore[method-assign]
    assert await api.get_device_uiconfig(DEVICE_ID) is None


def test_nothing_calls_the_endpoint_yet() -> None:
    """The reader is wired into the client but deliberately unused.

    It costs a request per device per poll if hooked up before the parameters
    are known, and every one of those would fail. This test is the guard on
    that promise, and the thing to delete when Pass 3 is finished.
    """
    import pathlib

    root = pathlib.Path("custom_components/fluidra_pool")
    callers = [
        path for path in root.rglob("*.py") if path.name != "_uiconfig.py" and "get_device_uiconfig" in path.read_text()
    ]
    assert callers == []


def test_client_exposes_the_reader() -> None:
    """The mixin is assembled into the concrete client."""
    assert hasattr(FluidraPoolAPI("user@example.invalid", None), "get_device_uiconfig")


def test_register_defaults_are_neutral() -> None:
    """A block that declares nothing must not distort the value it carries."""
    register: Any = UiRegister(read_id=99)
    assert register.factor == 1.0
    assert register.decimals == 0
    assert register.decode(42) == 42


# --- Scheduler capabilities (the endpoint that does answer) ------------------

CAPABILITY_PAYLOAD = {
    "id": DEVICE_ID,
    "info": {
        "name": "E30iQ",
        "configuration": {
            "capabilities": {
                "schedulers": [
                    {
                        "technologies": ["cloud"],
                        "componentWrite": 20,
                        "id": "pump",
                        "type": "minimal",
                        "componentRead": 20,
                        "enabled": True,
                    }
                ],
                "ota": {"enabled": True},
            }
        },
    },
}


def test_parses_the_scheduler_block() -> None:
    """The cloud states which register holds this device's schedules."""
    capabilities = parse_scheduler_capabilities(CAPABILITY_PAYLOAD)
    assert len(capabilities) == 1
    capability = capabilities[0]
    assert capability.scheduler_id == "pump"
    assert capability.component_read == 20
    assert capability.component_write == 20
    assert capability.scheduler_type == "minimal"
    assert capability.enabled is True


@pytest.mark.parametrize(
    "payload",
    [
        None,
        "text",
        {},
        {"info": "not a dict"},
        {"info": {"configuration": {}}},
        {"info": {"configuration": {"capabilities": {}}}},
        {"info": {"configuration": {"capabilities": {"schedulers": "nope"}}}},
    ],
)
def test_missing_capability_blocks_yield_an_empty_list(payload: Any) -> None:
    """A device that declares nothing is a normal answer, not an error."""
    assert parse_scheduler_capabilities(payload) == []


def test_malformed_scheduler_entries_are_skipped() -> None:
    """One unusable block must not cost the others."""
    payload = {
        "info": {
            "configuration": {
                "capabilities": {
                    "schedulers": [
                        "not a block",
                        {"id": "aux", "componentWrite": "n/a", "componentRead": None},
                        {"id": "pump", "componentWrite": 20, "componentRead": 20, "enabled": True},
                    ]
                }
            }
        }
    }
    capabilities = parse_scheduler_capabilities(payload)
    assert [c.scheduler_id for c in capabilities] == ["aux", "pump"]
    assert capabilities[0].component_write is None
    assert capabilities[0].enabled is False


async def test_capability_fetch_returns_the_parsed_blocks() -> None:
    """The happy path, against the shape a real device answers with."""
    api = _api()
    api._request = AsyncMock(return_value=(200, CAPABILITY_PAYLOAD, {}))  # type: ignore[method-assign]

    capabilities = await api.get_device_capabilities(DEVICE_ID)

    assert [c.component_write for c in capabilities] == [20]


async def test_capability_fetch_is_quiet_on_failure() -> None:
    """Diagnostics must never fail because of this call."""
    api = _api()
    api._request = AsyncMock(return_value=(500, {"message": "boom"}, {}))  # type: ignore[method-assign]
    assert await api.get_device_capabilities(DEVICE_ID) == []

    api._request = AsyncMock(side_effect=FluidraError("boom"))  # type: ignore[method-assign]
    assert await api.get_device_capabilities(DEVICE_ID) == []


async def test_capability_fetch_requires_authentication() -> None:
    """Same contract as every other read path."""
    api = FluidraPoolAPI("user@example.invalid", None)
    api.access_token = None
    with pytest.raises(FluidraAuthError):
        await api.get_device_capabilities(DEVICE_ID)


def test_capability_reader_is_not_on_the_poll_path() -> None:
    """One request per device per poll would buy nothing — diagnostics only."""
    import pathlib

    root = pathlib.Path("custom_components/fluidra_pool")
    callers = {
        path.name
        for path in root.rglob("*.py")
        if path.name != "_uiconfig.py" and "get_device_capabilities" in path.read_text()
    }
    assert callers == {"diagnostics.py"}
