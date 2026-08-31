"""Pure helper functions shared across Fluidra Pool platforms.

These functions take plain data in and return plain data out — no ``hass``,
no coordinator, no I/O — so they can be reused from any entity without
introducing coupling between platforms.
"""

from __future__ import annotations

from datetime import time
import logging
from typing import Any, cast

from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN, EXO_LED_COLOURS_LUMIPLUS, EXO_LED_COLOURS_ZODIAC_NL

_LOGGER = logging.getLogger(__name__)


def link_to_pool(info: DeviceInfo, pool_id: str) -> DeviceInfo:
    """Attach ``info`` to its parent pool device and return it.

    HA 2026.9 dropped ``via_device`` from the ``DeviceInfo`` TypedDict in favour
    of ``via_device_id``, which carries the *registry id* of the parent instead
    of its identifiers. Two reasons keep us on the old key for now:

    - ``via_device_id`` needs a lookup (``async_get_device_id_by_identifier``)
      that does not exist at the HA floor declared in ``hacs.json``, and that
      raises when the pool device is not registered yet — which is exactly the
      state ``device_info`` is evaluated in for the first platform set up.
    - ``via_device`` stays accepted by the registry until HA 2027.8.

    So the key is written here, outside the TypedDict literal that no longer
    types it, and this is the single place to migrate once the floor allows it.
    """
    cast(dict[str, Any], info)["via_device"] = (DOMAIN, pool_id)
    return info


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


def get_aux_schedule_data(device_data: dict[str, Any], aux_number: Any, schedule_id: Any) -> dict[str, Any] | None:
    """Return the schedule dict for an auxiliary output (eXO iQ c22/c24).

    Aux schedules live on fixed registers independent of the pump type, so they
    are stored separately from the pump/chlorination ``schedule_data``, keyed by
    aux number (``device_data["aux_schedule_data"]["1"]`` etc.). Looks up an
    entry whose ``id`` matches ``schedule_id`` (string-compared like the main
    schedule lookup). Returns ``None`` when there is no data for that aux.
    """
    if not device_data:
        return None

    aux_schedules = device_data.get("aux_schedule_data") or {}
    schedules = aux_schedules.get(str(aux_number))
    if not schedules:
        return None

    for schedule in schedules:
        if str(schedule.get("id")) == str(schedule_id):
            result: dict[str, Any] = schedule
            return result

    return None


# Command Connect cabinet: all-days CRON field verified by @efgonzalez (Issue
# #210). Minutes first, local wall-clock — do not convert despite GMT0 on the
# bridge. Sunday/Monday anchor for day 0 is not confirmed; keep the captured
# string intact.
CABINET_SCHEDULE_ALL_DAYS = "0,1,2,3,4,5,6"


def get_cabinet_schedule_data(device_data: dict[str, Any], output: Any, schedule_id: Any) -> dict[str, Any] | None:
    """Return one schedule slot for a Command Connect cabinet output (c35/c36).

    Cabinet schedules live on fixed per-output registers (pump = c35 / r1,
    lights = c36 / r2), stored under ``device_data["cabinet_schedule_data"]``
    keyed by output name. Returns ``None`` when that output has no slots.
    """
    if not device_data:
        return None

    by_output = device_data.get("cabinet_schedule_data") or {}
    schedules = by_output.get(str(output))
    if not schedules:
        return None

    for schedule in schedules:
        if str(schedule.get("id")) == str(schedule_id):
            result: dict[str, Any] = schedule
            return result

    return None


def resolve_cabinet_schedule_component(device_data: dict[str, Any], output: Any, default: int = 35) -> int:
    """Return the schedule register for a Command Connect cabinet output."""
    from .device_registry import DeviceIdentifier

    mapping = DeviceIdentifier.get_feature(device_data, "cabinet_schedule_components", {}) or {}
    raw = mapping.get(str(output))
    if raw is None:
        return default
    return int(raw)


def build_cabinet_schedule_slot(
    schedule_id: int,
    start: time,
    end: time,
    *,
    enabled: bool = True,
    days: str = CABINET_SCHEDULE_ALL_DAYS,
) -> dict[str, Any]:
    """Build one cabinet schedule slot in the exact shape the app PUTs.

    Captured on hardware by @efgonzalez (Issue #210)::

        {"id": 1, "groupId": 1, "enabled": true,
         "startTime": "15 10 * * 0,1,2,3,4,5,6",
         "endTime": "30 18 * * 0,1,2,3,4,5,6",
         "startActions": {"operationName": "1"}}

    No ``state`` field — the device reports RUNNING/IDLE itself and rejects or
    ignores it on write. Times are local; ``days`` defaults to every day.
    """
    return {
        "id": schedule_id,
        "groupId": schedule_id,
        "enabled": enabled,
        "startTime": f"{start.minute} {start.hour} * * {days}",
        "endTime": f"{end.minute} {end.hour} * * {days}",
        "startActions": {"operationName": str(schedule_id)},
    }


def resolve_aux_schedule_component(device_data: dict[str, Any], aux_number: Any, default: int = 22) -> int:
    """Return the schedule register an auxiliary output is currently honouring.

    An eXO iQ aux has two schedule registers, not one: a plain on/off output
    (or one assigned to "Other") keeps its slots on c22/c24, while an output
    wired to a colour LED keeps them on c23/c25 and carries the colour as a
    componentAction (Issue #174, @Inervo -- capture of the official app). Seven
    schedulers are declared by the device, c19-c25, and c22/c23 and c24/c25 are
    the two aux pairs.

    Nothing on the device is known to name which of a pair is live: c90/c91
    report the assigned function as a plain string, but no capture shows them
    distinguishing a colour LED from a plain light. So the register that
    actually holds slots decides, which is safe here in a way it was not for the
    pump registers: the aux pair is selected by what the output is *wired to*,
    not by a setting the owner flips back and forth, so a stale populated
    register is far less likely than after a pump-type change.

    When both registers hold slots the choice is genuinely ambiguous. Rather
    than guess, keep the plain register (the pre-existing behaviour, so no
    entity moves under the owner) and log the assigned-function label at
    warning level so the next diagnostic pins the real discriminator.
    """
    # Imported here to keep this module free of package-level import cycles.
    from .device_registry import DeviceIdentifier

    plain_map = DeviceIdentifier.get_feature(device_data, "aux_schedule_components", {}) or {}
    plain = int(plain_map.get(str(aux_number), default))

    colour_map = DeviceIdentifier.get_feature(device_data, "aux_colour_schedule_components", {}) or {}
    colour_raw = colour_map.get(str(aux_number))
    if colour_raw is None:
        return plain
    colour = int(colour_raw)

    components = device_data.get("components", {})

    def _has_slots(register: int) -> bool:
        value = components.get(str(register), {}).get("reportedValue")
        return isinstance(value, list) and bool(value)

    plain_has = _has_slots(plain)
    colour_has = _has_slots(colour)

    if colour_has and not plain_has:
        return colour
    if plain_has and colour_has:
        labels = DeviceIdentifier.get_feature(device_data, "aux_labels", {}) or {}
        label_component = labels.get(str(aux_number))
        label = components.get(str(label_component), {}).get("reportedValue") if label_component else None
        _LOGGER.warning(
            "Aux %s has schedules on both c%s and c%s; keeping c%s. "
            "The device reports its assigned function as %r -- please report this "
            "on Issue #174 so the live register can be identified",
            aux_number,
            plain,
            colour,
            plain,
            label,
        )
    return plain


def describe_led_colour(colour_index: Any) -> dict[str, str] | None:
    """Return the candidate colour names for a raw aux-LED colour index.

    The eXO drives two LED families with incompatible colour tables -- LumiPlus
    runs 0-13, Zodiac NL runs 2-15 -- and nothing read from the device is known
    to say which one an aux is wired to. Naming the colour outright would be
    wrong for one family, so both candidates are surfaced and the raw index is
    kept authoritative. Returns ``None`` when the index is not an integer or
    matches neither table.
    """
    try:
        index = int(colour_index)
    except (TypeError, ValueError):
        return None

    candidates = {}
    if index in EXO_LED_COLOURS_LUMIPLUS:
        candidates["lumiplus"] = EXO_LED_COLOURS_LUMIPLUS[index]
    if index in EXO_LED_COLOURS_ZODIAC_NL:
        candidates["zodiac_nl"] = EXO_LED_COLOURS_ZODIAC_NL[index]
    return candidates or None


def _action_for_write(action: Any) -> Any:
    """Return one ``componentActions`` entry with its value under ``desiredValue``."""
    if not isinstance(action, dict):
        return action
    entry = {key: value for key, value in action.items() if key != "reportedValue"}
    if "desiredValue" not in entry and "reportedValue" in action:
        entry["desiredValue"] = action["reportedValue"]
    return entry


def schedule_slots_for_write(schedules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return ``schedules`` in the exact shape the Fluidra app PUTs.

    Every write path funnels through here so none of them can drift. Three
    things separate a slot as *read* from a slot as *written*, all three taken
    from @Inervo's capture of the official app (Issue #174):

    * ``startActions.componentActions`` entries carry ``desiredValue`` on the
      way out. Reads echo them as ``reportedValue``, and the integration used to
      send that back verbatim -- a key the backend's transform does not consume,
      which is consistent with writes landing mangled rather than rejected.
    * ``operationName`` is sent *alongside* ``componentActions``, not instead of
      it. All five captured bodies carry ``"operationName": "1"``, including the
      two that also carry a colour or an RPM.
    * ``state`` and ``endActions`` are runtime fields the device adds itself;
      the app's PUT body has neither.

    Fields the slot already carries are left alone, order included: a slot is
    otherwise passed through untouched, so a key this integration does not know
    about survives a round trip instead of being dropped.
    """
    normalised: list[dict[str, Any]] = []
    for slot in schedules:
        if not isinstance(slot, dict):
            continue
        entry = dict(slot)
        entry.pop("state", None)
        entry.pop("endActions", None)
        entry.setdefault("groupId", entry.get("id"))

        start_actions = entry.get("startActions")
        if isinstance(start_actions, dict):
            actions = dict(start_actions)
            component_actions = actions.get("componentActions")
            if isinstance(component_actions, list):
                actions["componentActions"] = [_action_for_write(action) for action in component_actions]
                actions.setdefault("operationName", "1")
            entry["startActions"] = actions
        normalised.append(entry)
    return normalised


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
