"""Pure helper functions shared across Fluidra Pool platforms.

These functions take plain data in and return plain data out — no ``hass``,
no coordinator, no I/O — so they can be reused from any entity without
introducing coupling between platforms.
"""

from __future__ import annotations

from datetime import time
from typing import Any


def resolve_schedule_component(device_data: dict[str, Any], default: int = 20) -> int:
    """Return the schedule register the device is currently honouring.

    Most devices keep their schedules on one fixed register, declared as
    ``schedule_component``. The eXO iQ moves them depending on how its pump is
    configured — chlorination-only on c19, simple pump on c20, variable-speed on
    c21 — and Fluidra honours only the one matching the current configuration.

    Picking "whichever register has entries" does not work: stale schedules
    survive a configuration change, so two registers can be populated at once
    with one of them dead. A profile therefore declares which flags report the
    pump type, and the register follows from those (Issue #174, @Inervo).
    """
    # Imported here to keep this module free of package-level import cycles.
    from .device_registry import DeviceIdentifier

    mapping = DeviceIdentifier.get_feature(device_data, "schedule_component_map", None)
    if not isinstance(mapping, dict):
        component = DeviceIdentifier.get_feature(device_data, "schedule_component", default)
        return int(component) if component is not None else default

    components = device_data.get("components", {})

    def _flag(register: Any) -> bool:
        if register is None:
            return False
        return bool(components.get(str(register), {}).get("reportedValue"))

    if _flag(mapping.get("vs_flag")):
        return int(mapping["vs"])
    if _flag(mapping.get("simple_flag")):
        return int(mapping["simple"])
    return int(mapping["none"])


def get_schedule_data(device_data: dict[str, Any], schedule_id: Any) -> dict[str, Any] | None:
    """Return the schedule dict matching ``schedule_id`` in ``device_data``.

    Looks up ``device_data["schedule_data"]`` for an entry whose ``id`` matches
    ``schedule_id`` (compared as strings, since the API mixes int/str ids).
    Returns ``None`` if ``device_data`` is empty, has no schedules, or no
    schedule matches.
    """
    if not device_data:
        return None

    schedules = device_data.get("schedule_data")
    if not schedules:
        return None

    for schedule in schedules:
        if str(schedule.get("id")) == str(schedule_id):
            result: dict[str, Any] = schedule
            return result

    return None


def resolve_component_rw(cfg: int | dict[str, Any]) -> tuple[Any, Any]:
    """Resolve a component config that may be a plain int or a read/write dict.

    Device-registry component features are expressed either as a single int
    (the same component is used for reading and writing) or as a
    ``{"read": x, "write": y}`` dict (separate components). Returns a
    ``(read, write)`` tuple; when ``cfg`` is a dict, a side missing from it
    falls back to the other side.
    """
    if isinstance(cfg, dict):
        write_component = cfg.get("write", cfg.get("read"))
        read_component = cfg.get("read", write_component)
        return read_component, write_component
    return cfg, cfg


def parse_cron_time(cron_time: str) -> time | None:
    """Parse a CRON expression (``"mm HH * * days"``) into a :class:`~datetime.time`.

    Returns ``None`` for anything that does not carry a valid minute/hour pair
    (short string, non-numeric fields, out-of-range values, non-string input).
    """
    try:
        parts = cron_time.split()
        if len(parts) >= 2:
            minute = int(parts[0])
            hour = int(parts[1])
            return time(hour, minute)
    except (ValueError, TypeError, IndexError, AttributeError):
        pass
    return None


def determine_pool_access(pool: dict[str, Any], user_id: str | None) -> str:
    """Classify the account's access to a pool.

    Returns one of ``"owner"``, ``"viewer"``, ``"shared"`` or ``"unknown"``.
    The account owns the pool when its consumer id matches ``pool["owner"]``.
    Otherwise the level comes from the account's OWN contract, matched by id.
    ``"unknown"`` when the account can't be located in the pool at all.

    A ``"viewer"`` result matters because it *blocks* control writes: the Fluidra
    backend accepts writes from a viewer with an HTTP 200 that echoes the value
    but never persists it, so commands silently have no effect (Issue #129).
    Because it blocks, ``"viewer"`` is only ever returned on a positive
    per-account match — never inferred — so a legitimate owner is never locked
    out (Issue #166).
    """
    owner_id = pool.get("owner")
    if user_id and owner_id and owner_id == user_id:
        return "owner"

    contracts = pool.get("contracts")
    if not isinstance(contracts, list) or not contracts:
        return "unknown"

    if user_id:
        for contract in contracts:
            if isinstance(contract, dict) and contract.get("id") == user_id:
                level = contract.get("accessLevel")
                if isinstance(level, str):
                    return level

    # The account's id isn't among the contracts. Do NOT infer "viewer" from
    # "every contract is viewer-only" — an owner whose consumer id doesn't equal
    # pool.owner (Issue #166: multi-consumer/migrated accounts, or a user_id the
    # auth layer couldn't resolve) lands here too, and contracts[] then lists only
    # the viewers they shared *with*. The old inference returned the blocking
    # "viewer" verdict and locked those owners out of every control. Since viewer
    # blocks, an unmatched account is "unknown" (non-blocking) instead.
    return "unknown"
