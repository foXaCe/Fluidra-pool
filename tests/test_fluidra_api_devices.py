"""Tests for fluidra_api/_devices.py — discovery, polling, pool helpers."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.fluidra_pool.api_resilience import (
    FluidraAuthError,
    FluidraCircuitBreakerError,
    FluidraConnectionError,
)
from custom_components.fluidra_pool.fluidra_api._devices import DevicesMixin


class _FakeAPI(DevicesMixin):
    """Stub: only what DevicesMixin touches."""

    def __init__(self) -> None:
        self.access_token: str | None = "fake-token"
        self._request = AsyncMock()
        self._build_auth_headers = MagicMock(return_value={"Authorization": "Bearer fake-token"})
        self.ensure_valid_token = AsyncMock(return_value=True)
        self.user_pools: list[dict[str, Any]] = []
        self.devices: list[dict[str, Any]] = []
        self._pools: list[dict[str, Any]] = []


# --- async_update_data ---------------------------------------------------


async def test_async_update_data_assigns_pools_and_devices_on_success() -> None:
    """The happy path: pools list + per-pool devices list populated atomically."""
    api = _FakeAPI()
    # First call returns the user pools, second call returns devices for pool_1.
    api._request.side_effect = [
        (200, [{"id": "pool_1", "name": "Pool One"}], "[]"),
        (200, [{"id": "DEV-1", "info": {"name": "Pump", "family": "pump"}, "type": "connected"}], "[]"),
    ]

    await api.async_update_data()

    assert api.user_pools == [{"id": "pool_1", "name": "Pool One"}]
    assert len(api.devices) == 1
    assert api.devices[0]["device_id"] == "DEV-1"
    assert api.devices[0]["pool_id"] == "pool_1"
    assert api.devices[0]["type"] == "pump"


async def test_async_update_data_handles_dict_envelope_pools_response() -> None:
    """If pools are wrapped in a {pools: [...]} envelope, we still extract them."""
    api = _FakeAPI()
    api._request.side_effect = [
        (200, {"pools": [{"id": "pool_1"}]}, "{}"),
        (200, [], "[]"),
    ]
    await api.async_update_data()
    assert api.user_pools == [{"id": "pool_1"}]


async def test_async_update_data_keeps_state_unchanged_on_request_error() -> None:
    """If the initial pools call fails, we don't wipe previous state."""
    api = _FakeAPI()
    api.user_pools = [{"id": "pool_old"}]
    api.devices = [{"device_id": "OLD-1"}]
    api._request.side_effect = FluidraConnectionError("network")

    await api.async_update_data()

    # Previous state preserved.
    assert api.user_pools == [{"id": "pool_old"}]
    assert api.devices == [{"device_id": "OLD-1"}]


async def test_async_update_data_skips_pool_devices_when_inner_call_fails() -> None:
    """A device discovery failure for one pool doesn't break the rest."""
    api = _FakeAPI()
    api._request.side_effect = [
        (200, [{"id": "pool_1"}, {"id": "pool_2"}], "[]"),
        FluidraConnectionError("fail for pool_1"),  # _discover_devices_for_pool raises.
        (200, [{"id": "DEV-2", "info": {"name": "Pump", "family": "pump"}, "type": "connected"}], "[]"),
    ]
    await api.async_update_data()
    # pool_2's devices were still discovered.
    assert any(d["device_id"] == "DEV-2" for d in api.devices)


# --- _discover_devices_for_pool ------------------------------------------


async def test_discover_devices_for_pool_returns_empty_on_non_200() -> None:
    """Non-200 from the devices endpoint yields no devices (not a crash)."""
    api = _FakeAPI()
    api._request.return_value = (404, None, "")
    devices = await api._discover_devices_for_pool("pool_1", {})
    assert devices == []


async def test_discover_devices_for_pool_handles_dict_envelope() -> None:
    """`{"devices": [...]}` envelope is unwrapped."""
    api = _FakeAPI()
    api._request.return_value = (
        200,
        {"devices": [{"id": "DEV-1", "info": {"name": "Pump", "family": "pump"}, "type": "connected"}]},
        "{}",
    )
    devices = await api._discover_devices_for_pool("pool_1", {})
    assert len(devices) == 1
    assert devices[0]["device_id"] == "DEV-1"


async def test_discover_devices_flattens_bridged_children() -> None:
    """A bridge device's children are surfaced individually with parent_id set."""
    api = _FakeAPI()
    api._request.return_value = (
        200,
        [
            {
                "id": "BRIDGE-1",
                "info": {"name": "Connect", "family": "bridge"},
                "type": "connected",
                "devices": [
                    {
                        "id": "BRIDGE-1.nn_1",
                        "info": {"name": "tecnoLC2", "family": "chlorinator"},
                        "type": "connected",
                    }
                ],
            }
        ],
        "[]",
    )

    devices = await api._discover_devices_for_pool("pool_1", {})

    # Only the child is surfaced, not the bridge itself.
    assert len(devices) == 1
    assert devices[0]["device_id"] == "BRIDGE-1.nn_1"
    assert devices[0]["parent_id"] == "BRIDGE-1"
    assert devices[0]["type"] == "chlorinator"


async def test_discover_devices_bridged_pump_child_is_variable_speed() -> None:
    """A pump behind a bridge gets variable_speed/pump_type so its speed-select is created."""
    api = _FakeAPI()
    api._request.return_value = (
        200,
        [
            {
                "id": "BRIDGE-1",
                "info": {"name": "Connect", "family": "bridge"},
                "type": "connected",
                "devices": [
                    {
                        "id": "BRIDGE-1.nn_1",
                        "info": {"name": "VSP Pump", "family": "pump"},
                        "type": "connected",
                    }
                ],
            }
        ],
        "[]",
    )

    devices = await api._discover_devices_for_pool("pool_1", {})

    assert devices[0]["type"] == "pump"
    assert devices[0]["variable_speed"] is True
    assert devices[0]["pump_type"] == "variable_speed"


async def test_discover_devices_bridged_non_pump_child_not_variable_speed() -> None:
    """A non-pump child is not flagged variable_speed."""
    api = _FakeAPI()
    api._request.return_value = (
        200,
        [
            {
                "id": "BRIDGE-1",
                "info": {"name": "Connect", "family": "bridge"},
                "type": "connected",
                "devices": [
                    {
                        "id": "BRIDGE-1.nn_1",
                        "info": {"name": "tecnoLC2", "family": "chlorinator"},
                        "type": "connected",
                    }
                ],
            }
        ],
        "[]",
    )

    devices = await api._discover_devices_for_pool("pool_1", {})

    assert devices[0]["variable_speed"] is False


async def test_discover_devices_marks_offline_when_connection_type_not_connected() -> None:
    """A `type` other than "connected" results in online=False."""
    api = _FakeAPI()
    api._request.return_value = (
        200,
        [{"id": "DEV-OFF", "info": {"name": "Pump", "family": "pump"}, "type": "offline"}],
        "[]",
    )
    devices = await api._discover_devices_for_pool("pool_1", {})
    assert devices[0]["online"] is False


# --- get_pools / cached_pools / get_*_by_id ------------------------------


async def test_get_pools_raises_when_not_authenticated() -> None:
    """No access token → AuthError."""
    api = _FakeAPI()
    api.access_token = None
    with pytest.raises(FluidraAuthError):
        await api.get_pools()


async def test_get_pools_returns_user_pools_with_devices_grouped_by_pool() -> None:
    """Devices are grouped under their matching pool by pool_id."""
    api = _FakeAPI()
    api.user_pools = [{"id": "pool_1", "name": "Pool One"}, {"id": "pool_2", "name": "Pool Two"}]
    api.devices = [
        {"device_id": "A", "pool_id": "pool_1"},
        {"device_id": "B", "pool_id": "pool_2"},
        {"device_id": "C", "pool_id": "pool_1"},
    ]

    pools = await api.get_pools()

    assert len(pools) == 2
    pool_1_devices = next(p["devices"] for p in pools if p["id"] == "pool_1")
    assert {d["device_id"] for d in pool_1_devices} == {"A", "C"}


async def test_get_pools_falls_back_to_default_pool_when_no_user_pools() -> None:
    """If we have devices but no user_pools, surface them under a synthetic `default` pool."""
    api = _FakeAPI()
    api.devices = [{"device_id": "A", "pool_id": "ignored"}]

    pools = await api.get_pools()

    assert len(pools) == 1
    assert pools[0]["id"] == "default"
    assert pools[0]["name"] == "Fluidra Pool"


async def test_cached_pools_returns_last_get_pools_result_without_api_call() -> None:
    """cached_pools is a snapshot — no _request invocation."""
    api = _FakeAPI()
    api._pools = [{"id": "cached"}]
    assert api.cached_pools == [{"id": "cached"}]
    api._request.assert_not_called()


async def test_get_pool_by_id_returns_matching_pool_or_none() -> None:
    """O(n) lookup in the cached pools list."""
    api = _FakeAPI()
    api._pools = [{"id": "pool_1"}, {"id": "pool_2"}]
    assert api.get_pool_by_id("pool_2") == {"id": "pool_2"}
    assert api.get_pool_by_id("missing") is None


async def test_get_device_by_id_searches_across_all_pools() -> None:
    """A device may live under any pool."""
    api = _FakeAPI()
    api._pools = [
        {"id": "pool_1", "devices": [{"device_id": "A"}]},
        {"id": "pool_2", "devices": [{"device_id": "B"}]},
    ]
    assert api.get_device_by_id("B") == {"device_id": "B"}
    assert api.get_device_by_id("missing") is None


# --- poll_device_status --------------------------------------------------


async def test_poll_device_status_returns_matching_device() -> None:
    """The matching device is returned from the pool's device list."""
    api = _FakeAPI()
    api._request.return_value = (
        200,
        [{"id": "DEV-1", "connectivity": {"connected": True}}],
        "[]",
    )
    result = await api.poll_device_status("pool_1", "DEV-1")
    assert result == {"id": "DEV-1", "connectivity": {"connected": True}}


async def test_poll_device_status_finds_bridged_child() -> None:
    """A bridged child (`.nn_*`) is found inside a parent's `devices` array."""
    api = _FakeAPI()
    api._request.return_value = (
        200,
        [
            {
                "id": "BRIDGE-1",
                "devices": [{"id": "BRIDGE-1.nn_1", "connectivity": {"connected": True}}],
            }
        ],
        "[]",
    )
    result = await api.poll_device_status("pool_1", "BRIDGE-1.nn_1")
    assert result == {"id": "BRIDGE-1.nn_1", "connectivity": {"connected": True}}


async def test_poll_device_status_handles_dict_envelope() -> None:
    """The tree endpoint may return a {"devices": [...]} envelope — accept it."""
    api = _FakeAPI()
    api._request.return_value = (
        200,
        {"devices": [{"id": "DEV-1", "connectivity": {"connected": True}}]},
        "{}",
    )
    result = await api.poll_device_status("pool_1", "DEV-1")
    assert result == {"id": "DEV-1", "connectivity": {"connected": True}}


async def test_poll_device_status_returns_none_when_circuit_breaker_open() -> None:
    """Circuit breaker raises → None (no further fallback)."""
    api = _FakeAPI()
    api._request.side_effect = FluidraCircuitBreakerError("open")
    assert await api.poll_device_status("pool_1", "DEV-1") is None


async def test_poll_device_status_returns_none_on_connection_error() -> None:
    """Connection errors are caught and surfaced as None."""
    api = _FakeAPI()
    api._request.side_effect = FluidraConnectionError("network")
    assert await api.poll_device_status("pool_1", "DEV-1") is None


async def test_poll_device_status_returns_none_when_response_not_list() -> None:
    """Defensive: a non-list response yields None."""
    api = _FakeAPI()
    api._request.return_value = (200, {"unexpected": "shape"}, "{}")
    assert await api.poll_device_status("pool_1", "DEV-1") is None


async def test_poll_device_status_raises_when_token_refresh_fails() -> None:
    """Token refresh failure surfaces as AuthError."""
    api = _FakeAPI()
    api.ensure_valid_token = AsyncMock(return_value=False)
    with pytest.raises(FluidraAuthError):
        await api.poll_device_status("pool_1", "DEV-1")


# --- poll_water_quality --------------------------------------------------


async def test_poll_water_quality_url_encodes_pool_id() -> None:
    """Pool IDs with reserved chars are URL-encoded in the path."""
    api = _FakeAPI()
    api._request.return_value = (200, {"ph": 7.2}, "{}")
    await api.poll_water_quality("pool with spaces/12")
    called_url = api._request.call_args.args[1]
    assert "pool%20with%20spaces%2F12" in called_url


async def test_poll_water_quality_returns_data_on_200() -> None:
    """A 200 with a dict payload is forwarded verbatim."""
    api = _FakeAPI()
    api._request.return_value = (200, {"ph": 7.2, "orp": 650}, "{}")
    assert await api.poll_water_quality("pool_1") == {"ph": 7.2, "orp": 650}


async def test_poll_water_quality_returns_none_on_request_error() -> None:
    """Connection errors degrade to None."""
    api = _FakeAPI()
    api._request.side_effect = FluidraConnectionError("network")
    assert await api.poll_water_quality("pool_1") is None


# --- get_pool_details ----------------------------------------------------


async def test_get_pool_details_merges_details_and_status() -> None:
    """Details endpoint + status endpoint are merged into one dict."""
    api = _FakeAPI()
    api._request.side_effect = [
        (200, {"name": "Pool One", "characteristics": {"shape": "rect"}}, "{}"),
        (200, {"weather": {"status": "ok"}}, "{}"),
    ]
    result = await api.get_pool_details("pool_1")
    assert result is not None
    assert result["name"] == "Pool One"
    assert result["status_data"] == {"weather": {"status": "ok"}}


async def test_get_pool_details_returns_none_when_both_endpoints_fail() -> None:
    """Both endpoints failing → None (caller knows there's nothing to merge)."""
    api = _FakeAPI()
    api._request.side_effect = [
        FluidraConnectionError("details"),
        FluidraConnectionError("status"),
    ]
    assert await api.get_pool_details("pool_1") is None


async def test_get_pool_details_partial_success_returns_what_we_have() -> None:
    """If only the details endpoint succeeds, we still return that data."""
    api = _FakeAPI()
    api._request.side_effect = [
        (200, {"name": "Pool One"}, "{}"),
        FluidraConnectionError("status fail"),
    ]
    result = await api.get_pool_details("pool_1")
    assert result == {"name": "Pool One"}


# --- get_user_pools ------------------------------------------------------


async def test_get_user_pools_returns_list_on_200() -> None:
    """The list response is forwarded as-is."""
    api = _FakeAPI()
    api._request.return_value = (200, [{"id": "pool_1"}], "[]")
    assert await api.get_user_pools() == [{"id": "pool_1"}]


async def test_get_user_pools_returns_none_on_non_list_response() -> None:
    """A dict-shaped response (legacy) is treated as no-data."""
    api = _FakeAPI()
    api._request.return_value = (200, {"pools": [{"id": "pool_1"}]}, "{}")
    assert await api.get_user_pools() is None


async def test_get_user_pools_returns_none_on_request_error() -> None:
    """Connection errors → None."""
    api = _FakeAPI()
    api._request.side_effect = FluidraConnectionError("boom")
    assert await api.get_user_pools() is None


async def test_get_user_pools_raises_when_not_authenticated() -> None:
    """No access token → AuthError."""
    api = _FakeAPI()
    api.access_token = None
    with pytest.raises(FluidraAuthError):
        await api.get_user_pools()
