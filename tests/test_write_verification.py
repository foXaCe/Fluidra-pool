"""Tests for post-write verification (Issue #133).

The cloud confirms writes it silently discards, so these tests pin the only
thing that can tell the two apart: what the device reports on a later poll.
"""

from __future__ import annotations

from collections.abc import Callable
import logging
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from homeassistant.core import HomeAssistant
import pytest

from custom_components.fluidra_pool import write_verification
from custom_components.fluidra_pool.const import (
    WRITE_VERIFY_HISTORY_SIZE,
    WRITE_VERIFY_MAX_PENDING,
    WRITE_VERIFY_MIN_GRACE_SECONDS,
    WRITE_VERIFY_POLL_CYCLES,
)
from custom_components.fluidra_pool.coordinator import FluidraDataUpdateCoordinator
from custom_components.fluidra_pool.fluidra_api._components import ComponentsMixin
from custom_components.fluidra_pool.write_verification import (
    VERDICT_LOST,
    VERDICT_PROGRESSING,
    VERDICT_UNKNOWN,
    VERDICT_VERIFIED,
    WriteVerifier,
    normalize_component_value,
)


@pytest.fixture
def elapse(monkeypatch: pytest.MonkeyPatch) -> Callable[[float], None]:
    """Move the verifier's clock forward, so pending writes come due.

    The grace period is minutes long by design; the coordinator reads it from
    the real clock, so the tests that exercise that path move the clock instead
    of shortening the delay (which would stop testing the delay at all).
    """

    def _elapse(seconds: float = 3600.0) -> None:
        target = time.monotonic() + seconds
        monkeypatch.setattr(write_verification.time, "monotonic", lambda: target)

    return _elapse


def _armed(verifier: WriteVerifier, device_id: str = "LE24500883", component_id: int = 8) -> Any:
    """Return the single pending write, forced past its grace period."""
    due = verifier.due(now=float("inf"))
    assert len(due) == 1
    assert due[0].device_id == device_id
    assert due[0].component_id == component_id
    return due[0]


# --- value normalisation -------------------------------------------------


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (1, "1"),  # LumiPlus writes strings, the cloud reports numbers.
        (720, 720.0),
        (True, 1),
        ("7.2", 7.2),
    ],
)
def test_normalize_makes_equivalent_values_compare_equal(left: Any, right: Any) -> None:
    """Values that mean the same thing must not read as a lost write."""
    assert normalize_component_value(left) == normalize_component_value(right)


def test_normalize_keeps_non_numeric_strings() -> None:
    """A non-numeric register value is compared as a stripped string."""
    assert normalize_component_value("  AUTO ") == "AUTO"


# --- what gets tracked ---------------------------------------------------


def test_write_of_the_current_value_is_not_tracked() -> None:
    """Re-writing the value already reported leaves nothing to observe.

    "Unchanged" would be both the success and the failure signal, so a verdict
    here could only be a coin flip.
    """
    verifier = WriteVerifier()
    verifier.record("LE24500883", 8, 720, baseline=720)
    assert verifier.due(now=float("inf")) == []


def test_composite_payloads_are_not_tracked() -> None:
    """RGBW dicts come back re-encoded — no verdict beats a wrong one."""
    verifier = WriteVerifier()
    verifier.record("LE24500883", 12, {"red": 255, "green": 0}, baseline=None)
    assert verifier.due(now=float("inf")) == []


def test_write_without_a_known_baseline_is_still_tracked() -> None:
    """A first write after startup has no baseline but can still be verified."""
    verifier = WriteVerifier()
    verifier.record("LE24500883", 8, 730, baseline=None)
    pending = _armed(verifier)
    assert pending.desired == 730.0
    assert pending.baseline is None


def test_rewriting_a_component_supersedes_the_previous_pending_write() -> None:
    """Only the last value asked for is worth judging."""
    verifier = WriteVerifier()
    verifier.record("LE24500883", 8, 730, baseline=720)
    verifier.record("LE24500883", 8, 740, baseline=720)
    assert _armed(verifier).desired == 740.0


def test_write_without_a_device_id_is_not_tracked() -> None:
    """A write with no device to read back from cannot be judged."""
    verifier = WriteVerifier()
    verifier.record("", 8, 730, baseline=720)
    assert verifier.due(now=float("inf")) == []


def test_pending_writes_are_capped() -> None:
    """The pending map is a backstop, not a place to accumulate forever."""
    verifier = WriteVerifier()
    for component_id in range(WRITE_VERIFY_MAX_PENDING + 10):
        verifier.record("LE24500883", component_id, 1, baseline=0)
    assert len(verifier.due(now=float("inf"))) == WRITE_VERIFY_MAX_PENDING


def test_discard_forgets_a_pending_write() -> None:
    """A rejected re-write must not be judged against a write that never was."""
    verifier = WriteVerifier()
    verifier.record("LE24500883", 8, 730, baseline=720)
    verifier.discard("LE24500883", 8)
    assert verifier.due(now=float("inf")) == []


# --- the grace period ----------------------------------------------------


def test_a_fresh_write_is_not_due_yet() -> None:
    """Judging immediately would cry wolf on every command."""
    verifier = WriteVerifier()
    verifier.record("LE24500883", 8, 730, baseline=720)
    assert verifier.due() == []


def test_grace_scales_with_a_slow_poll_interval() -> None:
    """A slow scan interval must still get several cycles, not one."""
    verifier = WriteVerifier()
    verifier.set_poll_interval(300)
    assert verifier.grace_seconds == WRITE_VERIFY_POLL_CYCLES * 300


def test_grace_never_drops_below_the_floor() -> None:
    """A fast poll interval doesn't shrink the grace to a couple of seconds."""
    verifier = WriteVerifier()
    verifier.set_poll_interval(5)
    assert verifier.grace_seconds == WRITE_VERIFY_MIN_GRACE_SECONDS


@pytest.mark.parametrize("interval", [0, -30, None, "nope"])
def test_nonsense_poll_interval_leaves_the_grace_alone(interval: Any) -> None:
    """A bad interval must not disarm verification by zeroing its delay."""
    verifier = WriteVerifier()
    verifier.set_poll_interval(interval)
    assert verifier.grace_seconds == WRITE_VERIFY_MIN_GRACE_SECONDS


# --- the four verdicts ---------------------------------------------------


def test_device_reporting_the_requested_value_is_verified() -> None:
    """The nominal case: the write landed."""
    verifier = WriteVerifier()
    verifier.record("LE24500883", 8, 730, baseline=720)
    assert verifier.resolve(_armed(verifier), 730) == VERDICT_VERIFIED
    assert not verifier.lost_writes


def test_unchanged_value_is_a_lost_write() -> None:
    """The fake success of Issue #133: 200 + echo, and nothing moved."""
    verifier = WriteVerifier()
    verifier.record("LE24500883", 8, 730, baseline=720)
    assert verifier.resolve(_armed(verifier), 720) == VERDICT_LOST


def test_value_moving_towards_the_target_is_progressing_not_lost() -> None:
    """A setpoint climbing by steps is a write in flight, not a lost one."""
    verifier = WriteVerifier()
    verifier.record("LE24500883", 8, 740, baseline=720)
    assert verifier.resolve(_armed(verifier), 730) == VERDICT_PROGRESSING
    assert not verifier.lost_writes


def test_value_moving_away_from_the_target_is_unknown() -> None:
    """Something else wrote the register — a schedule, the app, the unit."""
    verifier = WriteVerifier()
    verifier.record("LE24500883", 8, 740, baseline=720)
    assert verifier.resolve(_armed(verifier), 700) == VERDICT_UNKNOWN
    assert not verifier.lost_writes


def test_non_numeric_register_that_changed_is_unknown() -> None:
    """A string register that moved somewhere else cannot be scored."""
    verifier = WriteVerifier()
    verifier.record("LE24500883", 9, "AUTO", baseline="OFF")
    assert verifier.resolve(_armed(verifier, component_id=9), "MANUAL") == VERDICT_UNKNOWN
    assert not verifier.lost_writes


def test_component_missing_from_the_poll_is_unknown() -> None:
    """No reading means no verdict — silence beats a wrong accusation."""
    verifier = WriteVerifier()
    verifier.record("LE24500883", 8, 730, baseline=720)
    assert verifier.resolve(_armed(verifier), None) == VERDICT_UNKNOWN
    assert not verifier.lost_writes


def test_resolving_drops_the_pending_write() -> None:
    """A judged write is not judged again on the next poll."""
    verifier = WriteVerifier()
    verifier.record("LE24500883", 8, 730, baseline=720)
    verifier.resolve(_armed(verifier), 720)
    assert verifier.due(now=float("inf")) == []


# --- how a lost write surfaces -------------------------------------------


def test_lost_write_warns_once_per_component(caplog: pytest.LogCaptureFixture) -> None:
    """A failing hourly automation must not fill the log with the same line."""
    verifier = WriteVerifier()
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            verifier.record("LE24500883", 8, 730, baseline=720)
            verifier.resolve(_armed(verifier), 720)

    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "not applied" in warnings[0].message
    # Every occurrence is still recorded for the diagnostics dump.
    assert len(verifier.lost_writes) == 3


def test_warning_is_rearmed_after_a_verified_write(caplog: pytest.LogCaptureFixture) -> None:
    """Once writes work again, a later regression must be reported again."""
    verifier = WriteVerifier()
    with caplog.at_level(logging.WARNING):
        verifier.record("LE24500883", 8, 730, baseline=720)
        verifier.resolve(_armed(verifier), 720)
        verifier.record("LE24500883", 8, 730, baseline=720)
        verifier.resolve(_armed(verifier), 730)
        verifier.record("LE24500883", 8, 740, baseline=730)
        verifier.resolve(_armed(verifier), 730)

    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 2


def test_lost_write_never_logs_a_raw_device_id(caplog: pytest.LogCaptureFixture) -> None:
    """Logs and diagnostics are pasted into public issues."""
    verifier = WriteVerifier()
    with caplog.at_level(logging.WARNING):
        verifier.record("LE24500883", 8, 730, baseline=720)
        verifier.resolve(_armed(verifier), 720)

    assert "LE24500883" not in caplog.text
    assert verifier.lost_writes[-1]["device_id"] == "LE2***883"


def test_lost_write_records_the_pool_access_level() -> None:
    """The dump must carry the context that explains the loss."""
    verifier = WriteVerifier()
    verifier.record("LE24500883", 8, 730, baseline=720)
    verifier.resolve(_armed(verifier), 720, access_level="viewer")

    entry = verifier.lost_writes[-1]
    assert entry["component_id"] == 8
    assert entry["desired_value"] == 730.0
    assert entry["reported_value"] == 720.0
    assert entry["access_level"] == "viewer"


def test_lost_write_history_is_bounded() -> None:
    """The diagnostics export must not grow without limit."""
    verifier = WriteVerifier()
    for index in range(WRITE_VERIFY_HISTORY_SIZE + 5):
        verifier.record("LE24500883", index, 730, baseline=720)
        verifier.resolve(verifier.due(now=float("inf"))[0], 720)
    assert len(verifier.lost_writes) == WRITE_VERIFY_HISTORY_SIZE


# --- the API write path arms verification --------------------------------


class _FakeAPI(ComponentsMixin):
    """Stub exposing only what ComponentsMixin touches."""

    def __init__(self, devices: dict[str, dict] | None = None) -> None:
        self.access_token: str | None = "fake-token"
        self._request = AsyncMock()
        self._build_auth_headers = MagicMock(return_value={})
        self.ensure_valid_token = AsyncMock(return_value=True)
        self._devices = devices or {}
        self.write_verifier = WriteVerifier()

    def get_device_by_id(self, device_id: str) -> dict | None:
        return self._devices.get(device_id)


async def test_control_write_baseline_is_the_value_from_before_the_write() -> None:
    """The response echo must never become the baseline.

    ``_update_device_state_from_response`` mirrors the echoed value into the
    local device dict; taking the baseline afterwards would compare 730 to 730
    and verify every discarded write.
    """
    device = {"components": {"8": {"reportedValue": 720}}}
    api = _FakeAPI({"LE24500883": device})
    api._request.return_value = (200, {"reportedValue": 730, "desiredValue": 730, "ts": 1}, "{}")

    assert await api.control_device_component("LE24500883", 8, 730) is True

    pending = _armed(api.write_verifier)
    assert pending.baseline == 720.0
    assert pending.desired == 730.0


async def test_rejected_control_write_arms_nothing() -> None:
    """The boost 404 is already reported — there is nothing to re-read."""
    api = _FakeAPI({"LE24500883": {"components": {"20": {"reportedValue": 0}}}})
    api._request.return_value = (404, None, "")

    assert await api.control_device_component("LE24500883", 20, 1) is False
    assert api.write_verifier.due(now=float("inf")) == []


async def test_generic_setter_arms_verification() -> None:
    """set_component_value writes go through the same silent-discard path."""
    api = _FakeAPI({"LE24500883": {"components": {"20": {"reportedValue": 0}}}})
    api._request.return_value = (200, {}, "{}")

    assert await api.set_component_value("LE24500883", 20, 5) is True
    assert _armed(api.write_verifier, component_id=20).desired == 5.0


async def test_generic_setter_arms_nothing_on_failure() -> None:
    """A non-200 from the generic setter is a plain failure, not a silent one."""
    api = _FakeAPI({"LE24500883": {"components": {"20": {"reportedValue": 0}}}})
    api._request.return_value = (500, None, "")

    assert await api.set_component_value("LE24500883", 20, 5) is False
    assert api.write_verifier.due(now=float("inf")) == []


# --- the coordinator judges on freshly polled data -----------------------


def _pools(reported: Any) -> list[dict[str, Any]]:
    """One pool, one device, one component reporting ``reported``."""
    return [
        {
            "id": "pool_001",
            "access_level": "viewer",
            "devices": [
                {
                    "device_id": "LE24500883",
                    "components": {"8": {"reportedValue": reported}},
                }
            ],
        }
    ]


async def test_coordinator_scales_the_grace_to_the_scan_interval(hass: HomeAssistant, mock_api: AsyncMock) -> None:
    """The verifier learns the poll cadence from the config entry options."""
    entry = MagicMock()
    entry.options = {"scan_interval": 600}
    FluidraDataUpdateCoordinator(hass, mock_api, config_entry=entry)
    assert mock_api.write_verifier.grace_seconds == WRITE_VERIFY_POLL_CYCLES * 600


async def test_coordinator_reports_a_lost_write_from_poll_data(
    hass: HomeAssistant,
    mock_api: AsyncMock,
    caplog: pytest.LogCaptureFixture,
    elapse: Callable[[float], None],
) -> None:
    """The verdict is read from the poll, not from the write response."""
    coordinator = FluidraDataUpdateCoordinator(hass, mock_api)
    mock_api.write_verifier.record("LE24500883", 8, 730, baseline=720)
    elapse(3600.0)

    with caplog.at_level(logging.WARNING):
        coordinator._verify_pending_writes(_pools(720))

    assert "not applied" in caplog.text
    assert coordinator.lost_writes[-1]["access_level"] == "viewer"


async def test_coordinator_stays_silent_when_the_write_landed(
    hass: HomeAssistant,
    mock_api: AsyncMock,
    caplog: pytest.LogCaptureFixture,
    elapse: Callable[[float], None],
) -> None:
    """An applied write must produce no warning at all."""
    coordinator = FluidraDataUpdateCoordinator(hass, mock_api)
    mock_api.write_verifier.record("LE24500883", 8, 730, baseline=720)
    elapse(3600.0)

    with caplog.at_level(logging.WARNING):
        coordinator._verify_pending_writes(_pools(730))

    assert "not applied" not in caplog.text
    assert coordinator.lost_writes == []


async def test_coordinator_leaves_a_write_still_within_its_grace_alone(
    hass: HomeAssistant, mock_api: AsyncMock
) -> None:
    """A poll landing seconds after the command must not judge it."""
    coordinator = FluidraDataUpdateCoordinator(hass, mock_api)
    mock_api.write_verifier.record("LE24500883", 8, 730, baseline=720)

    coordinator._verify_pending_writes(_pools(720))

    assert coordinator.lost_writes == []
    assert len(mock_api.write_verifier.due(now=float("inf"))) == 1


async def test_coordinator_does_not_accuse_an_offline_device(
    hass: HomeAssistant,
    mock_api: AsyncMock,
    caplog: pytest.LogCaptureFixture,
    elapse: Callable[[float], None],
) -> None:
    """An offline device serves components preserved from an earlier poll.

    "Unchanged" there says nothing about the write, and the user already sees
    the device unavailable — warning would be pure noise.
    """
    coordinator = FluidraDataUpdateCoordinator(hass, mock_api)
    mock_api.write_verifier.record("LE24500883", 8, 730, baseline=720)
    elapse(3600.0)

    pools = _pools(720)
    pools[0]["devices"][0]["online"] = False

    with caplog.at_level(logging.WARNING):
        coordinator._verify_pending_writes(pools)

    assert coordinator.lost_writes == []
    assert "not applied" not in caplog.text


async def test_coordinator_tolerates_an_api_without_a_verifier(hass: HomeAssistant, mock_api: AsyncMock) -> None:
    """Verification is a diagnostic layer — it must never sink a poll."""
    del mock_api.write_verifier
    coordinator = FluidraDataUpdateCoordinator(hass, mock_api)

    coordinator._verify_pending_writes(_pools(720))

    assert coordinator.lost_writes == []
