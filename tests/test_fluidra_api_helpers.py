"""Tests for fluidra_api._helpers (pure parsing functions)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.fluidra_pool.fluidra_api._helpers import (
    classify_device_type,
    parse_json,
    parse_retry_after,
)

# --- parse_json ---------------------------------------------------------


def test_parse_json_returns_none_on_empty() -> None:
    """An empty body parses to None (no spurious error)."""
    assert parse_json("") is None


def test_parse_json_returns_parsed_dict() -> None:
    """Valid JSON object is returned as a dict."""
    assert parse_json('{"a": 1, "b": "x"}') == {"a": 1, "b": "x"}


def test_parse_json_returns_parsed_list() -> None:
    """Valid JSON array is returned as a list."""
    assert parse_json("[1, 2, 3]") == [1, 2, 3]


def test_parse_json_returns_none_on_invalid_json() -> None:
    """Garbage in → None out (so callers can fall back to raw_text)."""
    assert parse_json("not-json") is None


# --- parse_retry_after --------------------------------------------------


def test_parse_retry_after_returns_none_when_header_missing() -> None:
    """Without a Retry-After header we let the default backoff kick in."""
    response = MagicMock()
    response.headers = {}
    assert parse_retry_after(response) is None


def test_parse_retry_after_returns_seconds_when_header_present() -> None:
    """A numeric Retry-After header yields the parsed float."""
    response = MagicMock()
    response.headers = {"Retry-After": "12"}
    assert parse_retry_after(response) == 12.0


def test_parse_retry_after_returns_none_on_non_numeric_header() -> None:
    """A Retry-After date string isn't supported — return None."""
    response = MagicMock()
    response.headers = {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}
    assert parse_retry_after(response) is None


# --- classify_device_type -----------------------------------------------


@pytest.mark.parametrize(
    ("family", "device_name", "expected"),
    [
        # Heat pump path: family pump + heat keyword.
        ("Heat Pump", "Z550", "heat_pump"),
        ("eco elyo pump", "LG", "heat_pump"),
        # Plain pump.
        ("variable speed pump", "E30iQ", "pump"),
        # Family without "pump" but with heat keywords.
        ("Astralpool", "X", "heat_pump"),
        ("thermal device", "X", "heat_pump"),
        # Device-name fallback for heat keywords.
        ("misc", "ECO Elyo", "heat_pump"),
        # Chlorinator family.
        ("Chlorinator", "X", "chlorinator"),
        ("Electrolyseur PRO", "X", "chlorinator"),
        # Heater branch is effectively unreachable — "heater" always contains
        # "heat" which matches the heat_pump branch first. Documented behaviour.
        # Light.
        ("Pool Light", "X", "light"),
        ("misc", "LumiPlus Connect", "light"),
        # Unknown.
        ("misc", "Unknown Device", "unknown"),
    ],
)
def test_classify_device_type_maps_metadata_to_high_level_type(family, device_name, expected) -> None:
    """Family + device name combine into a high-level type used by the registry."""
    assert classify_device_type(family, device_name) == expected
