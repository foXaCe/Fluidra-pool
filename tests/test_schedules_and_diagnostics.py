"""Tests for schedule helpers and diagnostics redaction."""

from __future__ import annotations

from custom_components.fluidra_pool.diagnostics import REDACTED, _redact_component_data
from custom_components.fluidra_pool.fluidra_api import FluidraPoolAPI
from custom_components.fluidra_pool.utils import extract_cron_days, normalize_mobile_days


def test_normalize_mobile_days_maps_zero_to_sunday() -> None:
    """Legacy Sunday value 0 is normalized to Fluidra's mobile value 7."""
    assert normalize_mobile_days([0, 1, 6]) == [1, 6, 7]


def test_extract_cron_days_supports_ranges_and_legacy_sunday() -> None:
    """Cron day extraction returns mobile-app day numbers."""
    assert extract_cron_days("00 08 * * 1-5,0") == {1, 2, 3, 4, 5, 7}


def test_dm24049704_conversion_keeps_day_specific_programs() -> None:
    """Schedules with different day sets become separate DM24049704 programs."""
    api = FluidraPoolAPI("test@example.com", "password")

    converted = api._convert_schedules_to_dm24049704_format(
        [
            {
                "id": 1,
                "enabled": True,
                "startTime": "00 05 * * 1,2,3",
                "endTime": "00 06 * * 1,2,3",
                "startActions": {"operationName": "1"},
            },
            {
                "id": 2,
                "enabled": True,
                "startTime": "30 07 * * 4",
                "endTime": "45 08 * * 4",
                "startActions": {"operationName": "3"},
            },
        ]
    )

    assert converted["dayPrograms"]["monday"] == converted["dayPrograms"]["tuesday"]
    assert converted["dayPrograms"]["monday"] != converted["dayPrograms"]["thursday"]
    assert converted["dayPrograms"]["sunday"] == 0
    assert len(converted["programs"]) == 2
    assert converted["programs"][0]["slots"] == [{"id": 0, "start": 1280, "end": 1536, "mode": 1}]
    assert converted["programs"][1]["slots"] == [{"id": 0, "start": 1822, "end": 2093, "mode": 3}]


def test_diagnostics_keep_component_values_but_redact_sensitive_keys() -> None:
    """Component readings are kept in clear text; only sensitive keys are redacted.

    Component reported/desired values are water-quality telemetry (pH, ORP, etc.)
    and need to stay readable so users can self-debug device mappings. Sensitive
    keys (device_id, password, …) are still hidden, even when nested inside the
    component dict.
    """
    redacted = _redact_component_data(
        "9",  # Component 9: pump ON/OFF — not an identifier slot.
        {
            "reportedValue": 731,
            "desiredValue": {"r": 200, "device_id": "LE24500883"},
            "timestamp": "2026-05-08T12:00:00Z",
            "password": "should-never-leak",
        },
    )

    assert redacted["reportedValue"] == 731
    assert redacted["timestamp"] == "2026-05-08T12:00:00Z"
    assert redacted["password"] == REDACTED
    # Sensitive keys must still be redacted recursively inside nested dicts.
    assert redacted["desiredValue"]["r"] == 200
    assert redacted["desiredValue"]["device_id"] == REDACTED
