"""Reader and parser for the cloud's own UI configuration (``/uiconfig``).

The Fluidra app does not hard-code a register map: it asks the cloud for a
``configFile`` describing every register of the device it is about to display —
which id to read, which to write, the factor to apply, how many decimals, the
bounds, the unit, and when to hide the block. That is the same information this
integration keeps by hand in ``device_registry/configs/``.

Reading it at runtime is what plan 013 Pass 3 is for. The endpoint is real and
answers, but it takes two query parameters the APK never spells out — ``appId``
and ``appVr`` — and rejects every value tried so far (``appId=iaqualink_plus``
gets past validation, ``appVr`` does not; see the plan for the full trace).
Until a capture of the official app supplies them, :meth:`get_device_uiconfig`
returns ``None`` on every call and nothing in the integration depends on it.

The parser below is useful regardless: the *shape of a register block* is known
from the APK's own literals, only the envelope around it is not, so it accepts
the plausible envelopes the way the bulk-components parser does.

A second, *working* source lives here too: ``GET /generic/devices/{id}`` answers
without any extra parameter and carries
``info.configuration.capabilities``, where the cloud states which register holds
this device's schedules — the very thing the profiles resolve by hand from the
pump-type flags (Issue #174). It is read for diagnostics only, so a mismatch
between what the cloud declares and what the profile resolved becomes visible
without changing any behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any
from urllib.parse import quote

from ..api_resilience import FluidraAuthError, FluidraError
from ..utils import mask_device_id
from ._base import FluidraAPIBase
from ._constants import CONNECTED_PARAMS, FLUIDRA_EMEA_BASE

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class UiRegister:
    """One register block of a device's UI configuration."""

    read_id: int
    write_id: int | None = None
    value_type: str | None = None
    factor: float = 1.0
    decimals: int = 0
    minimum: float | None = None
    maximum: float | None = None
    steps: float | None = None
    units: str | None = None

    def decode(self, raw: Any) -> float | None:
        """Turn a reported value into what the app would display.

        The factor is a display multiplier — a temperature reported as 213 with
        factor 0.1 is 21.3 °C — and ``decimals`` says how far to round. Returns
        None for anything that is not a number, like every other decoder here.
        """
        try:
            value = float(raw) * self.factor
        except (TypeError, ValueError):
            return None
        return round(value, self.decimals)

    def encode(self, value: float) -> float | int | None:
        """Turn a value the user picked into what the device expects.

        The inverse of :meth:`decode`. Whole results come back as ``int``: the
        write path sends ``desiredValue`` as-is, and a register whose factor
        makes it integral must not go out as ``700.0``.
        """
        if not self.factor:
            return None
        raw = value / self.factor
        rounded = round(raw)
        return rounded if abs(raw - rounded) < 1e-9 else raw


class UiConfigMixin(FluidraAPIBase):
    """Fetch and parse the cloud-served UI configuration of a device."""

    async def get_device_uiconfig(self, device_id: str) -> dict[int, UiRegister] | None:
        """Return the device's register map as served by the cloud.

        ``None`` whenever the endpoint refuses the request or answers something
        unrecognisable — the caller keeps its hand-written profile, which is the
        behaviour today and the reason this is safe to call at all.
        """
        if not self.access_token:
            raise FluidraAuthError("Not authenticated")

        headers = self._build_auth_headers()
        url = f"{FLUIDRA_EMEA_BASE}/generic/devices/{quote(str(device_id), safe='')}/uiconfig"

        try:
            status, data, _ = await self._request("GET", url, headers=headers, params=dict(CONNECTED_PARAMS))
        except FluidraError as err:
            _LOGGER.debug("UI config fetch failed for %s: %s", mask_device_id(device_id), err)
            return None

        if status != 200:
            # Expected until the appId/appVr pair is known: the endpoint answers
            # 400 (missing parameters) rather than 404. Debug, not a warning —
            # nothing depends on it, so this is not a degraded state.
            _LOGGER.debug(
                "UI config fetch for %s returned HTTP %s",
                mask_device_id(device_id),
                status,
            )
            return None

        return parse_uiconfig(data)

    async def get_device_capabilities(self, device_id: str) -> list[SchedulerCapability]:
        """Return the scheduler registers the cloud declares for this device.

        Unlike :meth:`get_device_uiconfig`, this endpoint answers today. It is
        deliberately *not* on the poll path: one request per device would buy
        nothing in normal operation, since the profiles already resolve the
        register. It is read when diagnostics are downloaded, where knowing
        which register the cloud itself points at settles a whole class of
        "my schedule writes land on the wrong slot" reports (Issue #174).

        An empty list means "the cloud said nothing", never "no schedules".
        """
        if not self.access_token:
            raise FluidraAuthError("Not authenticated")

        url = f"{FLUIDRA_EMEA_BASE}/generic/devices/{quote(str(device_id), safe='')}"
        try:
            status, data, _ = await self._request(
                "GET", url, headers=self._build_auth_headers(), params=dict(CONNECTED_PARAMS)
            )
        except FluidraError as err:
            _LOGGER.debug("Capability fetch failed for %s: %s", mask_device_id(device_id), err)
            return []

        if status != 200:
            _LOGGER.debug(
                "Capability fetch for %s returned HTTP %s",
                mask_device_id(device_id),
                status,
            )
            return []

        return parse_scheduler_capabilities(data)


def _coerce_register(entry: Any) -> UiRegister | None:
    """Build a UiRegister from one raw block, or None when it carries no read id."""
    if not isinstance(entry, dict):
        return None
    try:
        read_id = int(entry["readId"])
    except (KeyError, TypeError, ValueError):
        return None

    def number(key: str, default: float | None = None) -> float | None:
        raw = entry.get(key)
        if raw is None:
            return default
        try:
            return float(raw)
        except (TypeError, ValueError):
            return default

    write_raw = entry.get("writeId")
    try:
        write_id = int(write_raw) if write_raw is not None else None
    except (TypeError, ValueError):
        write_id = None

    decimals = number("decimals", 0)
    return UiRegister(
        read_id=read_id,
        write_id=write_id,
        value_type=entry.get("type") if isinstance(entry.get("type"), str) else None,
        factor=number("factor", 1.0) or 1.0,
        decimals=int(decimals) if decimals is not None else 0,
        minimum=number("min"),
        maximum=number("max"),
        steps=number("steps"),
        units=entry.get("units") if isinstance(entry.get("units"), str) else None,
    )


def parse_uiconfig(data: Any) -> dict[int, UiRegister] | None:
    """Normalise a ``/uiconfig`` payload into ``{read_id: UiRegister}``.

    The envelope is unconfirmed, so accept what it can plausibly be — a bare
    list of blocks, a ``configFile``/``components``/``registers`` wrapper around
    one, or a mapping keyed by register id — and return ``None`` for anything
    else rather than an empty map, which a caller would read as "this device has
    no registers".
    """
    entries: Any = data
    if isinstance(data, dict):
        for key in ("configFile", "components", "registers", "uiConfig"):
            nested = data.get(key)
            if isinstance(nested, (list, dict)):
                return parse_uiconfig(nested)

        registers: dict[int, UiRegister] = {}
        for key, value in data.items():
            register = _coerce_register(value)
            if register is not None:
                registers[register.read_id] = register
                continue
            # A mapping keyed by register id, whose blocks omit their own readId.
            if isinstance(value, dict):
                try:
                    keyed = int(key)
                except (TypeError, ValueError):
                    continue
                register = _coerce_register({"readId": keyed, **value})
                if register is not None:
                    registers[keyed] = register
        return registers or None

    if not isinstance(entries, list):
        return None

    registers = {}
    for entry in entries:
        register = _coerce_register(entry)
        if register is not None:
            registers[register.read_id] = register
    return registers or None


def build_dynamic_scan_set(profile_specific: list[int], uiconfig: dict[int, UiRegister] | None) -> list[int]:
    """Union the profile's scan set with the registers the cloud declares.

    The profile always wins on ordering and is never dropped: its list is the
    result of decoding real hardware, while the cloud's is a superset that
    includes registers nothing knows how to read yet.
    """
    scan_set = list(profile_specific)
    if not uiconfig:
        return scan_set
    scan_set.extend(read_id for read_id in sorted(uiconfig) if read_id not in scan_set)
    return scan_set


@dataclass(frozen=True, slots=True)
class SchedulerCapability:
    """One scheduler block the cloud declares for a device."""

    scheduler_id: str | None
    component_read: int | None
    component_write: int | None
    scheduler_type: str | None
    enabled: bool


def parse_scheduler_capabilities(payload: Any) -> list[SchedulerCapability]:
    """Pull the scheduler blocks out of a ``GET /devices/{id}`` payload.

    Returns an empty list for anything unexpected: this is diagnostic
    information, and a device that declares nothing is a normal answer.
    """
    if not isinstance(payload, dict):
        return []
    info = payload.get("info")
    if not isinstance(info, dict):
        return []
    configuration = info.get("configuration")
    if not isinstance(configuration, dict):
        return []
    capabilities = configuration.get("capabilities")
    if not isinstance(capabilities, dict):
        return []
    blocks = capabilities.get("schedulers")
    if not isinstance(blocks, list):
        return []

    def component(block: dict[str, Any], key: str) -> int | None:
        try:
            return int(block[key])
        except (KeyError, TypeError, ValueError):
            return None

    parsed: list[SchedulerCapability] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        parsed.append(
            SchedulerCapability(
                scheduler_id=block.get("id") if isinstance(block.get("id"), str) else None,
                component_read=component(block, "componentRead"),
                component_write=component(block, "componentWrite"),
                scheduler_type=block.get("type") if isinstance(block.get("type"), str) else None,
                enabled=bool(block.get("enabled", False)),
            )
        )
    return parsed
