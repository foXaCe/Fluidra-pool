#!/usr/bin/env python3
"""Propose a candidate DeviceConfig profile from a Fluidra diagnostics dump.

SPIKE PROTOTYPE (Plan 009) — an offline authoring aid, NOT part of the runtime
integration. It is never imported by ``custom_components/fluidra_pool``.

Usage:
    python scripts/propose_device_profile.py dump.json --device-index 0 \\
        --anchor ph=7.2 --anchor temperature=28.5 --anchor orp=650

Given a diagnostics-shaped JSON dump and a handful of "anchors" — numeric
values the Fluidra app displays for one device (pH, ORP, temperature,
salinity, chlorination level, ...) — this scans the device's numeric
``reportedValue`` components and looks for the ones whose value matches an
anchor under a known scale (x1, /10, /100). It prints a *candidate* Python
block on stdout for a human to review, adapt, and paste into
``custom_components/fluidra_pool/device_registry/configs/chlorinators.py``.
It never writes to that file, and never talks to the network.

Source of truth for scales and component slots: ``_standard_tecnolc2()`` in
``custom_components/fluidra_pool/device_registry/configs/chlorinators.py``
(docstring + the "cc25052635_chlorinator" comment block in the same file):
  pH measured        c165, /100
  ORP measured       c170, x1 (raw mV) — c177 is a documented near-miss trap
  water temperature  c172, /10
  salinity           c174, /100
  chlorination level c10,  x1 (0-100%)
  pH setpoint        c16,  /100
  ORP setpoint       c20,  x1 (raw mV)

Known limitation (see plans/notes/009-profile-tool-findings.md): diagnostics
dumps redact ``device_id`` and the name/family/model fields are generic
("Chlorinator") across the whole tecnoLC2 lineup, so the real serial needed
for ``identifier_patterns`` cannot be recovered from a dump alone. Pass
``--serial-pattern`` with the serial the reporter pasted in the issue/app
(never derive it from the redacted dump) or the output leaves a TODO
placeholder for a human to fill in.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
from pathlib import Path
import sys
from typing import Any

# --- Known scales & standard layout (source of truth: chlorinators.py) -----

# Scales tried for every numeric component against every anchor.
SCALES: dict[str, float] = {"x1": 1.0, "/10": 10.0, "/100": 100.0}

# _standard_tecnolc2()'s fixed component slots — chlorinators.py:14-73.
STANDARD_LAYOUT: dict[str, int] = {
    "chlorination_level": 10,
    "ph_setpoint": 16,
    "orp_setpoint": 20,
    "ph": 165,
    "orp": 170,
    "temperature": 172,
    "salinity": 174,
}

# Documented traps (chlorinators.py comments) — surfaced when two components
# both score within the ambiguity window for the same anchor kind.
KNOWN_TRAPS: dict[str, tuple[int, int, str]] = {
    "orp": (
        170,
        177,
        "c170 is the calibrated ORP reading (matches the app); c177 is a close but "
        "uncalibrated raw value, historically 50-80 mV off (see the cc25052635_chlorinator "
        "and cc24018506_chlorinator comments in chlorinators.py).",
    ),
}

SENSOR_KINDS = ("ph", "orp", "temperature", "salinity", "free_chlorine")
CONTROL_KINDS = ("chlorination_level", "ph_setpoint", "orp_setpoint")

DEFAULT_TOLERANCE = 0.05  # Accept a candidate within 5% relative error.
DEFAULT_AMBIGUITY_WINDOW = 0.20  # Flag alternates within 20% as ambiguous.
DEFAULT_PRIORITY = 95


@dataclass
class Candidate:
    """A single (component, scale) reading scored against one anchor."""

    component_id: int
    scale_name: str
    scaled_value: float
    raw_value: float
    error: float


@dataclass
class AnchorResult:
    """The best candidate (and any ambiguous alternates) for one anchor."""

    kind: str
    anchor_value: float
    best: Candidate | None
    confident: bool
    alternates: list[Candidate] = field(default_factory=list)


# --- Loading -----------------------------------------------------------------


def load_devices(dump: Any) -> list[dict[str, Any]]:
    """Flatten a diagnostics-shaped dump into a list of device dicts.

    Accepts a few shapes so ad-hoc pastes work too:
      - a full diagnostics dump: {"pools": {pool_id: {"devices": [...]}}}
      - {"devices": [...]}
      - a bare list of device dicts
      - a single device dict (has a "components" key)
    """
    if isinstance(dump, list):
        return dump
    if isinstance(dump, dict):
        if "pools" in dump:
            devices: list[dict[str, Any]] = []
            for pool in dump["pools"].values():
                if isinstance(pool, dict):
                    devices.extend(pool.get("devices", []) or [])
            return devices
        if "devices" in dump:
            return list(dump["devices"])
        if "components" in dump:
            return [dump]
    raise ValueError("Unrecognised dump shape — expected a diagnostics dump, a devices list, or a single device dict.")


def numeric_components(device: dict[str, Any]) -> dict[int, float]:
    """Extract {component_id: reportedValue} for numeric, non-boolean readings."""
    components = device.get("components", {}) or {}
    result: dict[int, float] = {}
    for comp_id, comp_data in components.items():
        if not isinstance(comp_data, dict):
            continue
        value = comp_data.get("reportedValue")
        if isinstance(value, bool) or not isinstance(value, int | float):
            continue
        try:
            result[int(comp_id)] = float(value)
        except (TypeError, ValueError):
            continue
    return result


# --- Scoring -----------------------------------------------------------------


def score_anchor(
    kind: str,
    anchor_value: float,
    components: dict[int, float],
    tolerance: float,
    ambiguity_window: float,
) -> AnchorResult:
    """Score every numeric component against one anchor value across known scales."""
    per_component_best: list[Candidate] = []
    for comp_id, raw in components.items():
        best_for_component: Candidate | None = None
        for scale_name, divisor in SCALES.items():
            scaled = raw / divisor
            error = abs(scaled - anchor_value) / anchor_value if anchor_value else abs(scaled - anchor_value)
            if best_for_component is None or error < best_for_component.error:
                best_for_component = Candidate(comp_id, scale_name, scaled, raw, error)
        if best_for_component is not None:
            per_component_best.append(best_for_component)

    # Tie-break exact/near-exact ties in favour of the standard tecnoLC2 slot for this
    # kind, if any: e.g. an ORP setpoint (c20) can coincidentally equal the pH measured
    # value (c165) at a different scale — pick the one _standard_tecnolc2() actually uses.
    expected = STANDARD_LAYOUT.get(kind)
    per_component_best.sort(key=lambda c: (c.error, c.component_id != expected))
    if not per_component_best:
        return AnchorResult(kind, anchor_value, None, confident=False)

    best = per_component_best[0]
    alternates = [c for c in per_component_best[1:] if c.error <= ambiguity_window]
    return AnchorResult(kind, anchor_value, best, confident=best.error <= tolerance, alternates=alternates)


def standard_layout_verdict(
    results: dict[str, AnchorResult],
) -> tuple[bool, list[str], list[str]]:
    """Decide terse `_standard_tecnolc2()` call vs. explicit block.

    Returns (use_standard_call, matching_kinds, contradicting_kinds).
    A "contradiction" is an anchor whose confident best candidate lands on a
    DIFFERENT component than the one `_standard_tecnolc2()` hardcodes for
    that kind — proposing the terse call in that case would silently mismap
    that sensor/control, so any contradiction forces the explicit block.
    """
    matches: list[str] = []
    contradictions: list[str] = []
    for kind, result in results.items():
        expected = STANDARD_LAYOUT.get(kind)
        if expected is None or not result.confident or result.best is None:
            continue
        if result.best.component_id == expected:
            matches.append(kind)
        else:
            contradictions.append(kind)
    use_standard = len(matches) >= 2 and not contradictions
    return use_standard, matches, contradictions


# --- Rendering ---------------------------------------------------------------


def _serial_literal(serial_pattern: str | None) -> str:
    """Return a Python list-literal string for `identifier_patterns=...`."""
    if serial_pattern:
        return f'["{serial_pattern}"]'
    return '["<PASTE-SERIAL>*"]  # TODO: diagnostics redact device_id — paste the real serial from the issue/app (see --serial-pattern)'


def render_evidence(results: dict[str, AnchorResult]) -> list[str]:
    """Render the per-anchor scan results as a Python-comment block."""
    lines = ["# Evidence (scales/slots per _standard_tecnolc2() in chlorinators.py):"]
    for kind, result in results.items():
        if result.best is None:
            lines.append(
                f"#   {kind:<18} anchor={result.anchor_value!r:<10} -> no numeric component matched any known scale"
            )
            continue

        best = result.best
        expected = STANDARD_LAYOUT.get(kind)
        tag = ""
        if expected is not None:
            tag = (
                " [MATCHES standard slot]"
                if best.component_id == expected
                else f" [DIFFERS from standard slot {expected}]"
            )
        confidence = "" if result.confident else "  ** LOW CONFIDENCE — no candidate within tolerance **"
        lines.append(
            f"#   {kind:<18} anchor={result.anchor_value:<8g} -> component {best.component_id:<4} "
            f"({best.scale_name} of {best.raw_value:g} = {best.scaled_value:.3g}, error {best.error:.1%}){tag}{confidence}"
        )
        trap = KNOWN_TRAPS.get(kind)
        for alt in result.alternates:
            trap_note = ""
            if trap and {best.component_id, alt.component_id} == {trap[0], trap[1]}:
                trap_note = f"  KNOWN TRAP: {trap[2]}"
            lines.append(
                f"#       ambiguous alt: component {alt.component_id} "
                f"({alt.scale_name} of {alt.raw_value:g} = {alt.scaled_value:.3g}, error {alt.error:.1%}){trap_note}"
            )
    return lines


def render_standard_call(
    serial_pattern: str | None,
    device_key: str,
    priority: int,
    results: dict[str, AnchorResult],
    matches: list[str],
) -> str:
    """Render a terse `_standard_tecnolc2(...)` call candidate."""
    extra_kwargs: list[str] = []
    for kind in ("boost_mode", "free_chlorine", "cell_production_state"):
        result = results.get(kind)
        if result is not None and result.confident and result.best is not None:
            extra_kwargs.append(f"{kind}={result.best.component_id},")

    lines = [
        f'"{device_key}_chlorinator": _standard_tecnolc2(',
        f"    {_serial_literal(serial_pattern)},",
        f"    priority={priority},",
    ]
    lines.extend(f"    {kwarg}" for kwarg in extra_kwargs)
    lines.append("),")
    lines.append(
        f"# Standard-layout confidence: {len(matches)}/{len(results)} anchors matched "
        f"_standard_tecnolc2()'s fixed slots ({', '.join(matches) or 'none'})."
    )
    lines.append(
        "# NOTE: control/mode components NOT covered by an --anchor (mode select, boost, on/off, "
        "firmware quirks) are NOT validated by this tool. See cc24018506_chlorinator in "
        "chlorinators.py for a real device where the standard tecnoLC2 shape does NOT hold despite "
        "matching sensor anchors — always verify specific_components against the app before merging."
    )
    return "\n".join(lines)


def render_explicit_block(
    serial_pattern: str | None,
    device_key: str,
    priority: int,
    results: dict[str, AnchorResult],
    matches: list[str],
    contradictions: list[str],
) -> str:
    """Render a full, explicit DeviceConfig(...) candidate (model: cc25052635_chlorinator)."""
    sensors_lines = [
        f'"{kind}": {result.best.component_id},  # anchor={result.anchor_value:g} -> {result.best.scale_name} of {result.best.raw_value:g}'
        for kind in SENSOR_KINDS
        if (result := results.get(kind)) is not None and result.confident and result.best is not None
    ]
    control_lines = [
        f'"{kind}": {result.best.component_id},  # anchor={result.anchor_value:g}'
        for kind in CONTROL_KINDS
        if (result := results.get(kind)) is not None and result.confident and result.best is not None
    ]
    specific_components = sorted({r.best.component_id for r in results.values() if r.confident and r.best is not None})

    lines = [
        f'"{device_key}_chlorinator": DeviceConfig(',
        '    device_type="chlorinator",',
        f"    identifier_patterns={_serial_literal(serial_pattern)},",
        '    family_patterns=["chlorinator"],',
        "    components_range=25,",
        "    required_components=[0, 1, 2, 3],",
        '    entities=["switch", "number", "sensor_info"],',
        "    features={",
    ]
    if control_lines:
        lines.extend(f"        {line}" for line in control_lines)
    else:
        lines.append("        # TODO: no chlorination_level/ph_setpoint/orp_setpoint anchor confidently matched.")
    lines.append(
        '        "skip_mode_select": True,  # TODO: confirm — most tecnoLC2 units skip the OFF/ON/AUTO select.'
    )
    lines.append('        "sensors": {')
    lines.extend(f"            {line}" for line in sensors_lines)
    lines.append("        },")
    lines.append(
        f'        "specific_components": {specific_components},  # TODO: widen — includes only anchored components.'
    )
    lines.append("    },")
    lines.append(f"    priority={priority},")
    lines.append("),")
    lines.append(
        f"# Standard-layout check: {len(matches)} anchor(s) matched the standard slots "
        f"({', '.join(matches) or 'none'}); {len(contradictions)} contradicted them "
        f"({', '.join(contradictions) or 'none'})."
    )
    lines.append(
        "# This device does NOT look like a plain _standard_tecnolc2() unit — falling back to an "
        "explicit DeviceConfig (model: cc25052635_chlorinator in chlorinators.py). Cross-check every "
        "component listed here against the app, and widen specific_components for anything that had "
        "no matching --anchor."
    )
    return "\n".join(lines)


# --- CLI -----------------------------------------------------------------


def parse_anchor(raw: str) -> tuple[str, float]:
    if "=" not in raw:
        raise argparse.ArgumentTypeError(f"--anchor must be KIND=VALUE, got {raw!r}")
    kind, _, value = raw.partition("=")
    kind = kind.strip()
    try:
        return kind, float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--anchor value must be numeric: {raw!r}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("dump", type=Path, help="Path to a diagnostics-shaped JSON dump.")
    parser.add_argument(
        "--device-index", type=int, default=0, help="Index into the flattened devices list (default: 0)."
    )
    parser.add_argument(
        "--anchor",
        action="append",
        default=[],
        metavar="KIND=VALUE",
        help="An app-displayed value to match, e.g. ph=7.2, orp=650, temperature=28.5, salinity=5.4, "
        "chlorination_level=80, ph_setpoint=7.4, orp_setpoint=650, boost_mode=1. Repeatable.",
    )
    parser.add_argument(
        "--serial-pattern",
        default=None,
        help="Real device serial/pattern for identifier_patterns. Diagnostics redact device_id — pull "
        "this from the issue/app text, never from the dump.",
    )
    parser.add_argument(
        "--priority",
        type=int,
        default=DEFAULT_PRIORITY,
        help=f"Priority for the generated profile (default: {DEFAULT_PRIORITY}).",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=DEFAULT_TOLERANCE,
        help=f"Relative error to accept a candidate (default: {DEFAULT_TOLERANCE}).",
    )
    parser.add_argument(
        "--ambiguity-window",
        type=float,
        default=DEFAULT_AMBIGUITY_WINDOW,
        help=f"Relative error window to flag near-miss alternates as ambiguous (default: {DEFAULT_AMBIGUITY_WINDOW}).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        dump = json.loads(args.dump.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: could not read/parse {args.dump}: {exc}", file=sys.stderr)
        return 1

    try:
        devices = load_devices(dump)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if not devices:
        print("error: no devices found in dump", file=sys.stderr)
        return 1
    if not 0 <= args.device_index < len(devices):
        print(f"error: --device-index {args.device_index} out of range (0..{len(devices) - 1})", file=sys.stderr)
        return 1

    device = devices[args.device_index]
    components = numeric_components(device)
    if not components:
        print("error: device has no numeric components to scan", file=sys.stderr)
        return 1

    try:
        anchors = [parse_anchor(raw) for raw in args.anchor]
    except argparse.ArgumentTypeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not anchors:
        print("error: at least one --anchor KIND=VALUE is required", file=sys.stderr)
        return 1

    results = {
        kind: score_anchor(kind, value, components, args.tolerance, args.ambiguity_window) for kind, value in anchors
    }

    use_standard, matches, contradictions = standard_layout_verdict(results)

    device_key = (args.serial_pattern or "newdevice").split("*")[0].split(".")[0].lower() or "newdevice"

    print(
        f"# Device index {args.device_index} — device_id={device.get('device_id')!r}, family={device.get('family')!r}, model={device.get('model')!r}"
    )
    if device.get("device_id") in ("**REDACTED**", None):
        print(
            "# NOTE: device_id is redacted in this dump (diagnostics redact serials by design — "
            "see custom_components/fluidra_pool/diagnostics.py). Pass --serial-pattern with the "
            "real serial from the issue/app to fill in identifier_patterns."
        )
    print()
    print("\n".join(render_evidence(results)))
    print()
    if use_standard:
        print(render_standard_call(args.serial_pattern, device_key, args.priority, results, matches))
    else:
        print(render_explicit_block(args.serial_pattern, device_key, args.priority, results, matches, contradictions))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
