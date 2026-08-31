"""Assertion helper for the parent-pool link on ``device_info``.

Which key carries that link depends on the running core: HA 2026.9 replaced
``via_device`` (the parent's identifiers) with ``via_device_id`` (its registry
id). The production code picks one; these tests check the *value* that lands
under whichever key the core declares, so they stay meaningful on both sides of
that change instead of pinning the key the current pin happens to use.
"""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo

from custom_components.fluidra_pool.const import DOMAIN

CORE_USES_VIA_DEVICE_ID = "via_device_id" in DeviceInfo.__annotations__


def assert_linked_to_pool(info: Any, pool_id: str, pool_device_id: str | None = None) -> None:
    """Assert ``info`` links to the pool, whichever key the core expects."""
    if CORE_USES_VIA_DEVICE_ID and pool_device_id is not None:
        assert info["via_device_id"] == pool_device_id
        assert "via_device" not in info
    else:
        assert info["via_device"] == (DOMAIN, pool_id)
        assert "via_device_id" not in info


# Registry id handed to the mock coordinators so the link they produce carries a
# real value to assert on, not a MagicMock attribute.
POOL_DEVICE_ID = "pool-registry-id"
