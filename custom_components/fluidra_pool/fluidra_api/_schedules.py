"""Device-schedule serialisation and PUT operations."""

from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass
import logging
import time
from typing import Any
from urllib.parse import quote

from ..api_resilience import FluidraAuthError, FluidraError
from ..const import COMPONENT_DM24049704_SCHEDULE, COMPONENT_SCHEDULE, SCHEDULE_WRITE_HOLD_SECONDS
from ..helpers import schedule_slots_for_write
from ..utils import CRON_DAY_TO_NAME, extract_cron_days
from ..write_verification import normalize_component_value
from ._base import FluidraAPIBase
from ._constants import CONNECTED_PARAMS, FLUIDRA_EMEA_BASE

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class PendingScheduleWrite:
    """The slot list we last PUT, still waiting for the poll to echo it back.

    A schedule write replaces the register's whole list, so the next write has to
    start from what we just sent — not from the poll cache, which keeps reporting
    the pre-write list until the device takes the change (5-10 s on the Command
    Connect, Issue #210). Re-reading the device instead would be worse, not
    better: inside that window the read *is* the old list.
    """

    slots: list[dict[str, Any]]
    echo: Any
    expires_at: float


class SchedulesMixin(FluidraAPIBase):
    """Schedule encoding (CRON ↔ programs/slots) + ``set_schedule`` / ``clear_schedule``."""

    def schedule_write_lock(self, device_id: str, component_id: int) -> asyncio.Lock:
        """Return the lock that serialises schedule writes to one register.

        Callers must hold it across *compose and PUT*, not just the PUT: the
        corruption reported on Issue #210 came from two writes composing from the
        same pre-write snapshot, each re-sending the other's field unchanged.
        """
        key = (str(device_id), int(component_id))
        lock = self._schedule_write_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._schedule_write_locks[key] = lock
        return lock

    def pending_schedule_slots(self, device_id: str, component_id: int) -> list[dict[str, Any]] | None:
        """Return the slot list we last PUT, or ``None`` once it is stale.

        Dropped as soon as the local mirror reports the value back (the write
        landed and the poll cache is authoritative again) or the hold elapses, so
        a change made in the Fluidra app is never masked for long.
        """
        key = (str(device_id), int(component_id))
        pending = self._pending_schedule_writes.get(key)
        if pending is None:
            return None
        if time.monotonic() >= pending.expires_at:
            del self._pending_schedule_writes[key]
            return None
        reported = self.reported_component_value(device_id, component_id)
        if reported is not None and normalize_component_value(reported) == normalize_component_value(pending.echo):
            del self._pending_schedule_writes[key]
            return None
        return copy.deepcopy(pending.slots)

    def _convert_schedules_to_dm24049704_format(self, schedules: list[dict[str, Any]]) -> dict[str, Any]:
        """Convert CRON-format schedules to DM24049704 programs/slots format.

        Input format (CRON):
        [{"id": 0, "startTime": "0 5 * * 1,2,3,4,5", "endTime": "0 6 * * 1,2,3,4,5",
          "startActions": {"operationName": "3"}, "enabled": True}]

        Output format (programs/slots):
        {
            "dayPrograms": {"monday": 1, ...},
            "programs": [{"id": 1, "slots": [{"id": 0, "start": 1280, "end": 1536, "mode": 3}]}]
        }

        Time encoding: hours * 256 + minutes.
        """
        day_slots: dict[int, list[tuple[int, int, int]]] = {day: [] for day in CRON_DAY_TO_NAME}

        for sched in schedules:
            if not sched.get("enabled", True):
                continue

            start_cron = sched.get("startTime", "")
            end_cron = sched.get("endTime", "")
            operation = sched.get("startActions", {}).get("operationName", "1")

            start_parts = start_cron.split() if start_cron else []
            end_parts = end_cron.split() if end_cron else []

            if len(start_parts) >= 2 and len(end_parts) >= 2:
                try:
                    start_minute = int(start_parts[0])
                    start_hour = int(start_parts[1])
                    end_minute = int(end_parts[0])
                    end_hour = int(end_parts[1])

                    start_encoded = start_hour * 256 + start_minute
                    end_encoded = end_hour * 256 + end_minute

                    mode = int(operation) if operation else 1
                    slot = (start_encoded, end_encoded, mode)

                    for day in extract_cron_days(start_cron):
                        day_slots.setdefault(day, []).append(slot)

                except (ValueError, IndexError) as err:
                    _LOGGER.warning("Failed to parse schedule: %s, error: %s", sched, err)
                    continue

        program_ids: dict[tuple[tuple[int, int, int], ...], int] = {}
        day_programs: dict[str, int] = {}
        programs: list[dict[str, Any]] = []
        next_program_id = 1

        for cron_day, day_name in CRON_DAY_TO_NAME.items():
            slots_key = tuple(day_slots.get(cron_day, []))
            if not slots_key:
                day_programs[day_name] = 0
                continue

            program_id = program_ids.get(slots_key)
            if program_id is None:
                program_id = next_program_id
                next_program_id += 1
                program_ids[slots_key] = program_id
                programs.append(
                    {
                        "id": program_id,
                        "slots": [
                            {"id": slot_id, "start": start, "end": end, "mode": mode}
                            for slot_id, (start, end, mode) in enumerate(slots_key)
                        ],
                    }
                )

            day_programs[day_name] = program_id

        return {
            "dayPrograms": day_programs,
            "programs": programs,
        }

    async def set_schedule(
        self, device_id: str, schedules: list[dict[str, Any]], component_id: int = COMPONENT_SCHEDULE
    ) -> bool:
        """Set device schedule using the mobile-app format."""
        if not self.access_token:
            raise FluidraAuthError("Not authenticated")

        if not await self.ensure_valid_token():
            raise FluidraAuthError("Token refresh failed")

        headers = self._build_auth_headers()
        headers["content-type"] = "application/json; charset=utf-8"

        url = f"{FLUIDRA_EMEA_BASE}/generic/devices/{quote(str(device_id), safe='')}/components/{int(component_id)}"
        desired_value: Any = schedule_slots_for_write(schedules)
        if int(component_id) == COMPONENT_DM24049704_SCHEDULE:
            desired_value = self._convert_schedules_to_dm24049704_format(schedules)
        payload = {"desiredValue": desired_value}

        # Baseline from the local mirror *before* the write: the HTTP response
        # echoes desiredValue even when the device drops it (Issue #133 / #210).
        baseline = self.reported_component_value(device_id, component_id)

        try:
            status, _, raw_text = await self._request(
                "PUT", url, headers=headers, json_data=payload, params=dict(CONNECTED_PARAMS)
            )
        except FluidraError as err:
            _LOGGER.error("set_schedule error: %s", err)
            # Only the verifier entry goes: there is no write to confirm. The
            # composition base left by the *previous* successful write must
            # survive — dropping it sends the next edit back to the poll cache,
            # which still reports the pre-write list, and that is exactly the
            # stale-field overwrite this register serialisation prevents (#210).
            self.write_verifier.discard(device_id, component_id)
            return False

        if status != 200:
            # Surface the rejection reason at WARNING so it reaches HA's system log
            # (the system_log buffer only retains WARNING+, so a DEBUG line was
            # invisible and a failed write gave no diagnostic info — Issue #89).
            # A rejected PUT did not change the device, so the base left by the
            # last successful write is still the right one to compose on.
            self.write_verifier.discard(device_id, component_id)
            _LOGGER.warning("set_schedule rejected by Fluidra (HTTP %s): %s", status, raw_text[:500])
            return False

        # Arm a later poll comparison — HTTP 200 alone proves nothing.
        self.write_verifier.record(device_id, component_id, desired_value, baseline)
        # Keep what we sent as the base for the next write on this register: the
        # poll cache will still report the pre-write list for several seconds.
        self._pending_schedule_writes[(str(device_id), int(component_id))] = PendingScheduleWrite(
            slots=copy.deepcopy(schedule_slots_for_write(schedules)),
            echo=desired_value,
            expires_at=time.monotonic() + SCHEDULE_WRITE_HOLD_SECONDS,
        )
        return True

    async def clear_schedule(self, device_id: str, component_id: int = COMPONENT_SCHEDULE) -> bool:
        """Clear all schedules for a device."""
        return await self.set_schedule(device_id, [], component_id=component_id)

    async def get_pool_schedulers(self, pool_id: str) -> list[dict[str, Any]] | None:
        """Fetch the pool's configured automations ("schedulers").

        ``GET /generic/pools/{pool_id}/schedulers`` — the only source of truth for
        what a schedule-driven run is doing (Issue #144, @renaatski): while a
        schedule executes, the device zeroes its setpoint registers and never
        publishes the target anywhere, so the name/target must come from here and
        be matched to the active entry.

        Returns the raw list of scheduler entries, or ``None`` when unavailable.
        """
        if not self.access_token:
            raise FluidraAuthError("Not authenticated")

        headers = self._build_auth_headers()
        url = f"{FLUIDRA_EMEA_BASE}/generic/pools/{quote(str(pool_id), safe='')}/schedulers"

        try:
            status, data, _ = await self._request("GET", url, headers=headers)
        except FluidraError as err:
            _LOGGER.debug("Scheduler fetch failed for pool %s: %s", pool_id, err)
            return None

        if status != 200:
            _LOGGER.debug("Scheduler fetch for pool %s returned HTTP %s", pool_id, status)
            return None

        if isinstance(data, list):
            return [entry for entry in data if isinstance(entry, dict)]
        if isinstance(data, dict) and isinstance(data.get("schedulers"), list):
            return [entry for entry in data["schedulers"] if isinstance(entry, dict)]
        return None
