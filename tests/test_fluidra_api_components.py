"""Tests for fluidra_api/_components.py — get/set + local-state mirroring."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.fluidra_pool.api_resilience import (
    FluidraAuthError,
    FluidraCircuitBreakerError,
    FluidraConnectionError,
)
from custom_components.fluidra_pool.const import COMPONENT_AUTO_MODE, COMPONENT_PUMP_ONOFF
from custom_components.fluidra_pool.fluidra_api._components import ComponentsMixin


class _FakeAPI(ComponentsMixin):
    """Stub: only fields/methods that ComponentsMixin actually touches."""

    def __init__(self, *, devices: dict[str, dict] | None = None) -> None:
        self.access_token: str | None = "fake-token"
        self._request = AsyncMock()
        self._build_auth_headers = MagicMock(return_value={"Authorization": "Bearer fake-token"})
        self.ensure_valid_token = AsyncMock(return_value=True)
        self._devices = devices or {}

    def get_device_by_id(self, device_id: str) -> dict | None:
        return self._devices.get(device_id)


# --- get_component_state -------------------------------------------------


async def test_get_component_state_raises_when_not_authenticated() -> None:
    """Without an access token, get_component_state refuses to call the API."""
    api = _FakeAPI()
    api.access_token = None
    with pytest.raises(FluidraAuthError):
        await api.get_component_state("DEV-1", 9)


async def test_get_component_state_returns_dict_on_200() -> None:
    """A 200 response with a dict body is forwarded verbatim."""
    api = _FakeAPI()
    api._request.return_value = (200, {"reportedValue": 1, "desiredValue": 1}, "{}")
    result = await api.get_component_state("DEV-1", 9)
    assert result == {"reportedValue": 1, "desiredValue": 1}


async def test_get_component_state_returns_none_on_non_200() -> None:
    """A non-200 response yields None so callers can fall back."""
    api = _FakeAPI()
    api._request.return_value = (404, None, "")
    assert await api.get_component_state("DEV-1", 9) is None


async def test_get_component_state_returns_none_on_request_error() -> None:
    """Connection errors don't bubble up — return None."""
    api = _FakeAPI()
    api._request.side_effect = FluidraConnectionError("boom")
    assert await api.get_component_state("DEV-1", 9) is None


async def test_get_component_state_url_encodes_device_id() -> None:
    """Device IDs with reserved chars are URL-encoded (the bridged child format `LC*.nn_1`)."""
    api = _FakeAPI()
    api._request.return_value = (200, {}, "{}")
    await api.get_component_state("LC25000122.nn_1", 165)

    # The URL is the second positional arg of _request.
    called_url = api._request.call_args.args[1]
    assert "LC25000122.nn_1" in called_url
    assert "/components/165" in called_url


# --- control_device_component -------------------------------------------


async def test_control_device_component_raises_when_not_authenticated() -> None:
    """No token → AuthError before any HTTP call."""
    api = _FakeAPI()
    api.access_token = None
    with pytest.raises(FluidraAuthError):
        await api.control_device_component("DEV-1", 9, 1)


async def test_control_device_component_raises_when_token_refresh_fails() -> None:
    """If ensure_valid_token returns False, we raise rather than silently failing."""
    api = _FakeAPI()
    api.ensure_valid_token = AsyncMock(return_value=False)
    with pytest.raises(FluidraAuthError):
        await api.control_device_component("DEV-1", 9, 1)


async def test_control_device_component_returns_true_on_200() -> None:
    """A 200 PUT means the command succeeded."""
    api = _FakeAPI()
    api._request.return_value = (200, {"reportedValue": 1, "desiredValue": 1, "ts": 12345}, "{}")
    assert await api.control_device_component("DEV-1", 9, 1) is True


async def test_control_device_component_returns_false_on_circuit_breaker() -> None:
    """Circuit breaker open → return False (no crash)."""
    api = _FakeAPI()
    api._request.side_effect = FluidraCircuitBreakerError("open")
    assert await api.control_device_component("DEV-1", 9, 1) is False


async def test_control_device_component_returns_false_on_request_error() -> None:
    """Generic API failure → return False, callers handle the optimistic rollback."""
    api = _FakeAPI()
    api._request.side_effect = FluidraConnectionError("network")
    assert await api.control_device_component("DEV-1", 9, 1) is False


async def test_control_device_component_returns_false_on_non_200_status() -> None:
    """A 403 (e.g. unsupported component) yields False, not a raise."""
    api = _FakeAPI()
    api._request.return_value = (403, None, "forbidden")
    assert await api.control_device_component("DEV-1", 17, 6) is False


async def test_control_device_component_sends_desired_value_payload() -> None:
    """The PUT payload uses the canonical {desiredValue: ...} shape."""
    api = _FakeAPI()
    api._request.return_value = (200, {"reportedValue": 1, "desiredValue": 1, "ts": 1}, "{}")
    await api.control_device_component("DEV-1", 9, 1)
    call_kwargs = api._request.call_args.kwargs
    assert call_kwargs["json_data"] == {"desiredValue": 1}


# --- _update_device_state_from_response ---------------------------------


async def test_pump_onoff_response_mirrors_state_to_device_dict() -> None:
    """A successful PUT on component 9 mirrors is_running + components dict."""
    device: dict[str, Any] = {"device_id": "DEV-1", "components": {}}
    api = _FakeAPI(devices={"DEV-1": device})
    api._request.return_value = (
        200,
        {"reportedValue": 1, "desiredValue": 1, "ts": 999},
        "{}",
    )

    success = await api.control_device_component("DEV-1", COMPONENT_PUMP_ONOFF, 1)

    assert success is True
    assert device["is_running"] is True
    assert device["operation_mode"] == 1
    assert device["desired_state"] == 1
    assert device["last_updated"] == 999
    assert device["components"]["9"]["reportedValue"] == 1
    assert device["components"]["9"]["desiredValue"] == 1


async def test_auto_mode_response_mirrors_auto_mode_enabled() -> None:
    """A successful PUT on component 10 mirrors auto_mode_enabled."""
    device: dict[str, Any] = {"device_id": "DEV-1", "components": {}}
    api = _FakeAPI(devices={"DEV-1": device})
    api._request.return_value = (
        200,
        {"reportedValue": 1, "desiredValue": 1, "ts": 500},
        "{}",
    )

    await api.control_device_component("DEV-1", COMPONENT_AUTO_MODE, 1)

    assert device["auto_mode_enabled"] is True
    assert device["auto_mode_desired"] == 1


async def test_update_state_from_response_does_nothing_for_unknown_device() -> None:
    """A response for an unknown device id is a no-op (no KeyError)."""
    api = _FakeAPI(devices={})
    api._request.return_value = (200, {"reportedValue": 1, "desiredValue": 1, "ts": 1}, "{}")
    # No raise.
    success = await api.control_device_component("UNKNOWN", COMPONENT_PUMP_ONOFF, 1)
    assert success is True


# --- _update_device_state_fallback (when API returns non-dict) ----------


async def test_pump_onoff_fallback_when_response_is_not_a_dict() -> None:
    """If the server returns a non-dict body, we still mirror the requested value."""
    device: dict[str, Any] = {"device_id": "DEV-1", "components": {}}
    api = _FakeAPI(devices={"DEV-1": device})
    api._request.return_value = (200, None, "OK")

    success = await api.control_device_component("DEV-1", COMPONENT_PUMP_ONOFF, 1)

    assert success is True
    assert device["is_running"] is True
    assert device["operation_mode"] == 1
    # value=1 means we keep the previous speed_percent (50% default).
    assert device["speed_percent"] == 50


async def test_pump_onoff_fallback_value_zero_zeroes_speed() -> None:
    """Setting component 9 to 0 zeroes out speed_percent in the fallback path."""
    device: dict[str, Any] = {"device_id": "DEV-1", "components": {}, "speed_percent": 100}
    api = _FakeAPI(devices={"DEV-1": device})
    api._request.return_value = (200, None, "OK")

    await api.control_device_component("DEV-1", COMPONENT_PUMP_ONOFF, 0)

    assert device["is_running"] is False
    assert device["speed_percent"] == 0


# --- set_component_value family ------------------------------------------


async def test_set_component_value_routes_through_generic_setter() -> None:
    """set_component_value forwards to _set_component_generic with an int."""
    api = _FakeAPI()
    api._request.return_value = (200, None, "")
    assert await api.set_component_value("DEV-1", 18, 5) is True
    call_kwargs = api._request.call_args.kwargs
    assert call_kwargs["json_data"] == {"desiredValue": 5}


async def test_set_component_string_value_forwards_string_payload() -> None:
    """set_component_string_value sends the value as-is (LumiPlus ON/OFF: "1"/"0")."""
    api = _FakeAPI()
    api._request.return_value = (200, None, "")
    assert await api.set_component_string_value("DEV-1", 9, "1") is True
    assert api._request.call_args.kwargs["json_data"] == {"desiredValue": "1"}


async def test_set_component_json_value_forwards_dict_payload() -> None:
    """set_component_json_value sends a dict payload (RGBW colour)."""
    api = _FakeAPI()
    api._request.return_value = (200, None, "")
    payload = {"r": 255, "g": 0, "b": 128, "w": 0}
    assert await api.set_component_json_value("DEV-1", 45, payload) is True
    assert api._request.call_args.kwargs["json_data"] == {"desiredValue": payload}


async def test_set_component_generic_returns_false_on_non_200() -> None:
    """Server rejection (non-200) propagates as False."""
    api = _FakeAPI()
    api._request.return_value = (500, None, "")
    assert await api.set_component_value("DEV-1", 18, 5) is False


async def test_set_component_generic_returns_false_on_connection_error() -> None:
    """Connection failures degrade to False, never raise."""
    api = _FakeAPI()
    api._request.side_effect = FluidraConnectionError("boom")
    assert await api.set_component_value("DEV-1", 18, 5) is False


async def test_set_component_generic_requires_auth_token() -> None:
    """No access token → AuthError."""
    api = _FakeAPI()
    api.access_token = None
    with pytest.raises(FluidraAuthError):
        await api.set_component_value("DEV-1", 18, 5)
