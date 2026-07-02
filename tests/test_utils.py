"""Tests for the CRON / masking helpers in ``utils``."""

from __future__ import annotations

import pytest

from custom_components.fluidra_pool.utils import (
    DEFAULT_CRON_ALL_DAYS,
    convert_cron_days,
    extract_cron_days,
    normalize_mobile_days,
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
