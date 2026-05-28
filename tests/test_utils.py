"""Tests for the CRON / masking helpers in ``utils``."""

from __future__ import annotations

import pytest

from custom_components.fluidra_pool.utils import (
    DEFAULT_CRON_ALL_DAYS,
    build_cron_expression,
    convert_cron_days,
    extract_cron_days,
    mask_device_id,
    mask_email,
    normalize_mobile_days,
    parse_cron_time,
)

# --- CRON conversion ---


def test_convert_cron_days_maps_sunday_zero_to_seven() -> None:
    """HA Sunday (0) is rewritten as Fluidra Sunday (7) and the rest is preserved."""
    assert convert_cron_days("00 08 * * 0,1,2,3,4,5,6") == "00 08 * * 1,2,3,4,5,6,7"


def test_convert_cron_days_sorts_output() -> None:
    """Days are returned in ascending order even when the input is not sorted."""
    assert convert_cron_days("30 18 * * 5,0,2") == "30 18 * * 2,5,7"


@pytest.mark.parametrize("empty", ["", None])
def test_convert_cron_days_empty_returns_default(empty) -> None:
    """An empty CRON expression falls back to the all-days default."""
    assert convert_cron_days(empty) == DEFAULT_CRON_ALL_DAYS


def test_convert_cron_days_passthrough_when_unparsable() -> None:
    """Garbage day fields are returned as-is rather than raising."""
    assert convert_cron_days("00 08 * * not-a-day") == "00 08 * * not-a-day"


# --- normalize_mobile_days ---


def test_normalize_mobile_days_defaults_for_empty_iterables() -> None:
    """Empty / None inputs fall back to the full Mon-Sun mobile range."""
    assert normalize_mobile_days(None) == [1, 2, 3, 4, 5, 6, 7]
    assert normalize_mobile_days([]) == [1, 2, 3, 4, 5, 6, 7]


def test_normalize_mobile_days_dedupes_and_filters_out_of_range() -> None:
    """Duplicates collapse to a single day; values outside 1-7 are dropped."""
    assert normalize_mobile_days([1, 1, 2, 9, -3]) == [1, 2]


# --- extract_cron_days ---


def test_extract_cron_days_returns_all_days_when_wildcard() -> None:
    """A wildcard day field maps to the full week."""
    assert extract_cron_days("00 08 * * *") == {1, 2, 3, 4, 5, 6, 7}


def test_extract_cron_days_skips_invalid_entries() -> None:
    """Mixed valid/invalid day tokens drop the invalid ones silently."""
    assert extract_cron_days("00 08 * * 1,abc,5") == {1, 5}


def test_extract_cron_days_short_cron_returns_default() -> None:
    """A truncated CRON expression (no day field) falls back to all days."""
    assert extract_cron_days("00 08") == {1, 2, 3, 4, 5, 6, 7}


# --- parse_cron_time ---


@pytest.mark.parametrize(
    ("cron", "expected"),
    [
        ("30 08 * * 1,2", (8, 30)),
        ("00 00 * * *", (0, 0)),
        ("59 23 * * 1", (23, 59)),
    ],
)
def test_parse_cron_time_extracts_hours_and_minutes(cron, expected) -> None:
    """The hour/minute pair is read from the first two CRON fields."""
    assert parse_cron_time(cron) == expected


@pytest.mark.parametrize("invalid", ["", None, "garbage", "30"])
def test_parse_cron_time_invalid_returns_none(invalid) -> None:
    """Unparsable inputs return None rather than raising."""
    assert parse_cron_time(invalid) is None


# --- build_cron_expression ---


def test_build_cron_expression_pads_hour_and_minute() -> None:
    """Hour and minute are zero-padded so the format is stable."""
    assert build_cron_expression(8, 5) == "05 08 * * 1,2,3,4,5,6,7"


def test_build_cron_expression_accepts_custom_days() -> None:
    """A custom day list is forwarded untouched as the day field."""
    assert build_cron_expression(18, 30, "1,3,5") == "30 18 * * 1,3,5"


# --- masking helpers (used in logs / diagnostics) ---


@pytest.mark.parametrize(
    ("email", "expected"),
    [
        (None, "***"),
        ("", "***"),
        ("ab", "***"),
        ("foxace@gmail.com", "fox***"),
    ],
)
def test_mask_email_keeps_first_three_chars(email, expected) -> None:
    """Emails are reduced to a 3-char prefix to avoid leaking PII in logs."""
    assert mask_email(email) == expected


@pytest.mark.parametrize(
    ("device_id", "expected"),
    [
        (None, "***"),
        ("", "***"),
        ("ABC", "***"),
        ("LE24500883", "LE2***883"),
    ],
)
def test_mask_device_id_keeps_first_and_last_chars(device_id, expected) -> None:
    """Device IDs keep a recognisable prefix+suffix so support tickets stay matchable."""
    assert mask_device_id(device_id) == expected
