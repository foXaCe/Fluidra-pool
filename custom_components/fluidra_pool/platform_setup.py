"""Shared dynamic-devices setup skeleton for all entity platforms.

Every platform used to duplicate the same flow: create entities for the
devices of the cached discovery, then register a coordinator listener that
creates entities for devices appearing on later polls, without a reload
(quality-scale ``dynamic-devices``). Platforms provide only their
entity-builder callbacks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from homeassistant.core import callback

if TYPE_CHECKING:
    from collections.abc import Iterable

    from homeassistant.helpers.entity import Entity
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .const import FluidraPoolConfigEntry


class DeviceEntityBuilder(Protocol):
    """Build the platform's entities for one newly-seen device."""

    def __call__(self, pool_id: str, device: dict[str, Any]) -> Iterable[Entity]: ...


class PoolEntityBuilder(Protocol):
    """Build the platform's entities for one newly-seen pool."""

    def __call__(self, pool_id: str, pool: dict[str, Any]) -> Iterable[Entity]: ...


async def async_setup_dynamic_platform(
    config_entry: FluidraPoolConfigEntry,
    async_add_entities: AddEntitiesCallback,
    build_device_entities: DeviceEntityBuilder,
    build_pool_entities: PoolEntityBuilder | None = None,
) -> None:
    """Create entities now and for every device/pool that appears later."""
    coordinator = config_entry.runtime_data.coordinator
    known_devices: set[str] = set()
    known_pools: set[str] = set()

    @callback
    def _add_entities(pools: list[dict[str, Any]]) -> None:
        entities: list[Entity] = []
        for pool in pools:
            pool_id = pool["id"]
            for device in pool.get("devices", []):
                device_id = device.get("device_id")
                if not device_id:
                    continue
                key = f"{pool_id}_{device_id}"
                if key in known_devices:
                    continue
                known_devices.add(key)
                entities.extend(build_device_entities(pool_id, device))
            if build_pool_entities is not None and pool_id not in known_pools:
                known_pools.add(pool_id)
                entities.extend(build_pool_entities(pool_id, pool))
        if entities:
            async_add_entities(entities)

    # Initial setup from the cached discovery (fast startup, unchanged behaviour).
    pools = coordinator.api.cached_pools or await coordinator.api.get_pools()
    _add_entities(pools)

    # Add entities for devices that appear on later polls, without a reload.
    @callback
    def _on_coordinator_update() -> None:
        _add_entities(coordinator.get_pools_from_data())

    config_entry.async_on_unload(coordinator.async_add_listener(_on_coordinator_update))
