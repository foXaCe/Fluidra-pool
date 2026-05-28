"""Stateless helpers used by the API client."""

from __future__ import annotations

import json
from typing import Any

import aiohttp


def parse_json(raw_text: str) -> Any:
    """Parse a response body as JSON; return None when it isn't JSON."""
    if not raw_text:
        return None
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        return None


def parse_retry_after(response: aiohttp.ClientResponse) -> float | None:
    """Return Retry-After header in seconds, or None if absent/invalid."""
    header = response.headers.get("Retry-After")
    if not header:
        return None
    try:
        return float(header)
    except ValueError:
        return None


def classify_device_type(family: str, device_name: str) -> str:
    """Classify a Fluidra device into a high-level type from its metadata."""
    family_lower = family.lower()
    device_name_lower = device_name.lower()

    if "pump" in family_lower and any(kw in family_lower for kw in ("heat", "eco", "elyo", "thermal")):
        return "heat_pump"
    if "pump" in family_lower:
        return "pump"
    if any(kw in family_lower for kw in ("heat", "thermal", "eco elyo", "astralpool")):
        return "heat_pump"
    if any(kw in device_name_lower for kw in ("heat", "thermal", "eco", "elyo")):
        return "heat_pump"
    if "chlorinator" in family_lower or "electrolyseur" in family_lower:
        return "chlorinator"
    if "heater" in family_lower:
        return "heater"
    if "light" in family_lower or "lumiplus" in device_name_lower:
        return "light"
    return "unknown"
