"""Post-write verification: catch control writes the cloud accepted but dropped.

The Fluidra cloud answers a control write with ``HTTP 200`` and a
``reportedValue`` that echoes the ``desiredValue`` we just sent — *even when it
never applies it*. @luistf76 captured exactly that on a read-only (viewer)
account in Issue #133::

    Control component 8 SUCCESS: HTTP 200, desiredValue=730,
    response={"id":8,"reportedValue":730,"desiredValue":730,"ts":...}

…and the setpoint never moved. So the write response carries no information:
success and silent discard are byte-identical. The only thing that can tell
them apart is re-reading the component through the normal poll a few cycles
later and looking at what the device actually reports.

This module does exactly that, and nothing else. It never blocks a command and
never decides who is allowed to write — the permission model is still unknown
(Issue #133 "Still open"), and a wrong guess would break a legitimate owner.
It observes, then warns. That also makes it wider than the permission story:
an offline device, a hardware refusal, or a schedule resetting a setpoint all
produce the same symptom and are all caught by the same observation.

Verdicts, decided once the grace period has elapsed:

``verified``
    The device reports the value we asked for. Nothing to say.
``progressing``
    The value moved *towards* the target without reaching it yet — a setpoint
    climbing by steps is not a lost write.
``unknown``
    The component vanished from the poll, or the value moved somewhere we did
    not ask for (a schedule, the official app, the unit itself). Not our call
    to make — stay silent.
``lost``
    Nothing moved at all. This is the fake success. Warn.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import logging
import time
from typing import Any

from .const import (
    WRITE_VERIFY_HISTORY_SIZE,
    WRITE_VERIFY_MAX_PENDING,
    WRITE_VERIFY_MIN_GRACE_SECONDS,
    WRITE_VERIFY_POLL_CYCLES,
)
from .utils import mask_device_id

_LOGGER = logging.getLogger(__name__)

VERDICT_VERIFIED = "verified"
VERDICT_PROGRESSING = "progressing"
VERDICT_UNKNOWN = "unknown"
VERDICT_LOST = "lost"


def normalize_component_value(value: Any) -> Any:
    """Make a written value and a reported value comparable.

    The two sides do not agree on type: LumiPlus writes ``"1"``/``"0"`` as
    strings and the cloud reports them back as numbers, and integers come back
    as floats on some registers. Numeric-looking values are therefore compared
    as floats, everything else as-is (strings stripped).
    """
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        try:
            return float(stripped)
        except ValueError:
            return stripped
    return value


@dataclass(slots=True)
class PendingWrite:
    """A control write waiting for the device to confirm it by itself."""

    device_id: str
    component_id: int
    desired: Any
    baseline: Any
    sent_at: float
    due_at: float


class WriteVerifier:
    """Track control writes and judge them against the next polls.

    One instance per API client. Writes are recorded at the single choke point
    where every component write goes out, and judged by the coordinator at the
    end of a poll cycle, from the freshly fetched state — never from the write
    response, which is the very thing that cannot be trusted.
    """

    def __init__(self) -> None:
        """Initialize an empty verifier with the default grace period."""
        self._pending: dict[tuple[str, int], PendingWrite] = {}
        self._grace_seconds: float = float(WRITE_VERIFY_MIN_GRACE_SECONDS)
        # Keys already warned about — a failing automation writing every hour
        # must not fill the log. Cleared as soon as a write is verified again.
        self._warned: set[tuple[str, int]] = set()
        # Newest last; feeds the diagnostics export so a user can hand over a
        # measured trace instead of a description (Issues #133, #195).
        self.lost_writes: deque[dict[str, Any]] = deque(maxlen=WRITE_VERIFY_HISTORY_SIZE)

    @property
    def grace_seconds(self) -> float:
        """Return the delay a write gets before it is judged."""
        return self._grace_seconds

    def set_poll_interval(self, seconds: float) -> None:
        """Scale the grace period to the poll cadence.

        Judging after a fixed wall-clock delay would give a single poll cycle to
        an entry configured with a slow scan interval, and call a perfectly
        normal in-flight write lost.
        """
        try:
            interval = float(seconds)
        except (TypeError, ValueError):
            return
        if interval <= 0:
            return
        self._grace_seconds = max(float(WRITE_VERIFY_MIN_GRACE_SECONDS), WRITE_VERIFY_POLL_CYCLES * interval)

    def record(self, device_id: str, component_id: int, desired: Any, baseline: Any) -> None:
        """Arm verification for a write that the cloud just accepted.

        ``baseline`` must be the value reported *before* the write: the response
        overwrites the local mirror with its own echo, and comparing against
        that echo would verify every write, including the discarded ones.
        """
        if not device_id:
            return
        desired_value = normalize_component_value(desired)
        if isinstance(desired_value, dict) or isinstance(desired, dict):
            # Composite payloads (LumiPlus RGBW) are re-encoded by the cloud into
            # a shape we cannot reliably diff — no verdict beats a wrong one.
            return
        baseline_value = normalize_component_value(baseline)
        if baseline is not None and baseline_value == desired_value:
            # Writing the value the device already reports leaves nothing to
            # observe: "unchanged" would be both the success and the failure.
            return

        key = (device_id, int(component_id))
        now = time.monotonic()
        self._pending[key] = PendingWrite(
            device_id=device_id,
            component_id=int(component_id),
            desired=desired_value,
            baseline=baseline_value,
            sent_at=now,
            due_at=now + self._grace_seconds,
        )
        while len(self._pending) > WRITE_VERIFY_MAX_PENDING:
            self._pending.pop(next(iter(self._pending)))

    def discard(self, device_id: str, component_id: int) -> None:
        """Forget a pending write (e.g. superseded by a failed re-write)."""
        self._pending.pop((device_id, int(component_id)), None)

    def due(self, now: float | None = None) -> list[PendingWrite]:
        """Return the writes whose grace period has elapsed."""
        current = time.monotonic() if now is None else now
        return [pending for pending in self._pending.values() if pending.due_at <= current]

    def resolve(self, pending: PendingWrite, reported: Any, access_level: str | None = None) -> str:
        """Judge one due write against the freshly polled value and drop it."""
        key = (pending.device_id, pending.component_id)
        self._pending.pop(key, None)

        verdict = self._verdict(pending, reported)

        if verdict == VERDICT_LOST:
            self._report_lost(pending, reported, access_level)
        else:
            self._warned.discard(key)
            _LOGGER.debug(
                "Write verification for component %s on %s: %s (wanted %s, reports %s)",
                pending.component_id,
                mask_device_id(pending.device_id),
                verdict,
                pending.desired,
                reported,
            )
        return verdict

    @staticmethod
    def _verdict(pending: PendingWrite, reported: Any) -> str:
        """Classify a due write. See the module docstring for the four cases."""
        if reported is None:
            return VERDICT_UNKNOWN
        current = normalize_component_value(reported)
        if current == pending.desired:
            return VERDICT_VERIFIED
        if pending.baseline is not None and current == pending.baseline:
            return VERDICT_LOST
        if (
            pending.baseline is not None
            and isinstance(current, float)
            and isinstance(pending.baseline, float)
            and isinstance(pending.desired, float)
        ):
            # Moved, but did it move our way? A setpoint climbing towards the
            # target by steps is a write in progress, not a lost one.
            moved_towards = abs(current - pending.desired) < abs(pending.baseline - pending.desired)
            return VERDICT_PROGRESSING if moved_towards else VERDICT_UNKNOWN
        return VERDICT_UNKNOWN

    def _report_lost(self, pending: PendingWrite, reported: Any, access_level: str | None) -> None:
        """Record a lost write and warn about it once per component."""
        key = (pending.device_id, pending.component_id)
        elapsed = round(time.monotonic() - pending.sent_at, 1)
        self.lost_writes.append(
            {
                "device_id": mask_device_id(pending.device_id),
                "component_id": pending.component_id,
                "desired_value": pending.desired,
                "reported_value": normalize_component_value(reported),
                "seconds_elapsed": elapsed,
                "access_level": access_level,
            }
        )
        if key in self._warned:
            return
        self._warned.add(key)
        _LOGGER.warning(
            "Command not applied: component %s on device %s was set to %s and accepted "
            "by the Fluidra cloud, but the device still reports %s after %s s. The write "
            "was silently discarded — common causes are a read-only (shared) account, a "
            "device that is offline, or a schedule overriding the setting%s",
            pending.component_id,
            mask_device_id(pending.device_id),
            pending.desired,
            normalize_component_value(reported),
            elapsed,
            f" (this pool's access level is '{access_level}')" if access_level else "",
        )
