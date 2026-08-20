"""Issue #195 — the GenSalt OE iQ mode select must never be created.

The unit (@albo-yo, serial ``CC26009948.nn_1``) is a tecnoLC2 GenSalt OE iQ with
an ORP kit: it has no OFF/ON/AUTO mode register, and the generic chlorinator
profile mapped the mode select onto c20 — the ORP setpoint. Writing "On" wrote
``1`` into a 700 mV setpoint, and reading it back fell through to "Off".

The 2.79.1 fix routed tecnoLC2 units by ``thingType``, but only once the status
tree had been attached to the device — which happens on the *second* poll, long
after the platforms have built their entities from the discovery snapshot. These
tests walk the real chain (discovery tree -> first refresh -> platform setup)
instead of pinning ``device["status"]`` by hand, which is what let the earlier
test stay green while the user-visible defect remained.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from custom_components.fluidra_pool.device_registry import DEVICE_CONFIGS, DeviceIdentifier
from custom_components.fluidra_pool.fluidra_api._devices import DevicesMixin
from custom_components.fluidra_pool.select import FluidraChlorinatorModeSelect
from custom_components.fluidra_pool.select import async_setup_entry as select_setup

POOL_ID = "pool_7642"


class _DiscoveryAPI(DevicesMixin):
    """Only what :class:`DevicesMixin` touches, wired to a canned tree response."""

    def __init__(self, tree: list[dict[str, Any]]) -> None:
        self.access_token: str | None = "fake-token"
        self._build_auth_headers = MagicMock(return_value={"Authorization": "Bearer fake-token"})
        self.ensure_valid_token = AsyncMock(return_value=True)
        self.user_pools: list[dict[str, Any]] = []
        self.devices: list[dict[str, Any]] = []
        self._pools: list[dict[str, Any]] = []
        self._request = AsyncMock(
            side_effect=[
                (200, [{"id": POOL_ID, "name": "maison"}], "[]"),
                (200, tree, "[]"),
            ]
        )


def _gensalt_tree(serial: str) -> list[dict[str, Any]]:
    """The Fluidra ``format=tree`` payload for this unit, as captured in #195.

    The chlorinator sits behind a bridge, and ``thingType`` is a *top-level* key
    of the tree entry — the same dict that later becomes ``device["status"]``.
    No ``components`` yet: registers are only scanned on a later poll.
    """
    return [
        {
            "id": "BRIDGE-1",
            "info": {"name": "Bridge", "family": "Bridges"},
            "type": "connected",
            "devices": [
                {
                    "id": serial,
                    "info": {"name": "Chlorinator", "family": "Chlorinators"},
                    "type": "connected",
                    "thingType": "tecnoLC2",
                    "poolId": POOL_ID,
                }
            ],
        }
    ]


async def _discover_then_first_refresh(serial: str) -> tuple[_DiscoveryAPI, dict[str, Any]]:
    """Run discovery + the coordinator's fast first refresh; return api + device."""
    api = _DiscoveryAPI(_gensalt_tree(serial))
    await api.async_update_data()
    # What FluidraPoolCoordinator._async_update_data does while _first_update is
    # True: get_pools() and nothing else — no _refresh_pool, so no status tree.
    await api.get_pools()
    device = api.cached_pools[0]["devices"][0]
    assert "status" not in device, "the fast first refresh must not have attached a status tree"
    return api, device


async def _run_select_setup(api: _DiscoveryAPI) -> list[Any]:
    """Call the select platform exactly as Home Assistant does after first refresh."""
    coordinator = MagicMock()
    coordinator.api = api
    coordinator.data = {POOL_ID: api.cached_pools[0]}
    coordinator.last_update_success = True
    entry = SimpleNamespace(
        runtime_data=SimpleNamespace(coordinator=coordinator),
        async_on_unload=lambda _unsub: None,
    )
    added: list[Any] = []
    await select_setup(MagicMock(), entry, MagicMock(side_effect=lambda ents, *a, **k: added.extend(ents)))
    return added


async def test_unknown_tecnolc2_serial_gets_no_mode_select_through_the_real_chain() -> None:
    """An unlisted tecnoLC2 serial must be routed by ``thingType`` at setup time.

    This is the systemic hole: the discovery tree carries ``thingType`` and the
    integration throws it away, so the platform sees the generic chlorinator
    profile and builds a mode select that writes to the ORP setpoint.
    """
    api, device = await _discover_then_first_refresh("CC29999999.nn_1")

    config = DeviceIdentifier.identify_device(device)
    assert config is DEVICE_CONFIGS["tecnolc2_signature"], "thingType did not survive discovery"
    assert DeviceIdentifier.has_feature(device, "skip_mode_select")

    added = await _run_select_setup(api)
    assert not [e for e in added if isinstance(e, FluidraChlorinatorModeSelect)]


async def test_cc26009948_matches_the_gensalt_orp_profile_without_a_status_tree() -> None:
    """@albo-yo's serial is profiled from the discovery snapshot alone (#195)."""
    _api, device = await _discover_then_first_refresh("CC26009948.nn_1")

    config = DeviceIdentifier.identify_device(device)
    assert config is DEVICE_CONFIGS["cc25051112_chlorinator"]
    assert DeviceIdentifier.has_feature(device, "skip_mode_select")
    # c20 = 700 mV alongside c170 = 710 mV measured: this unit has the ORP setpoint.
    assert DeviceIdentifier.get_feature(device, "orp_setpoint") == 20


async def test_cc26009948_gets_no_mode_select_at_setup() -> None:
    """The end-to-end guard for the reported defect."""
    api, _device = await _discover_then_first_refresh("CC26009948.nn_1")

    added = await _run_select_setup(api)
    assert not [e for e in added if isinstance(e, FluidraChlorinatorModeSelect)]
