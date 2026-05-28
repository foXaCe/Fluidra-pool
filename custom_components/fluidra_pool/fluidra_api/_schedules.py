"""Device-schedule serialisation and PUT operations."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

from ..api_resilience import FluidraAuthError, FluidraError
from ..const import COMPONENT_SCHEDULE
from ..utils import CRON_DAY_TO_NAME, extract_cron_days
from ._constants import FLUIDRA_EMEA_BASE

_LOGGER = logging.getLogger(__name__)


class SchedulesMixin:
    """Schedule encoding (CRON ↔ programs/slots) + ``set_schedule`` / ``clear_schedule``."""

    def _convert_schedules_to_dm24049704_format(self, schedules: list[dict[str, Any]]) -> dict:
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

        url = (
            f"{FLUIDRA_EMEA_BASE}/generic/devices/{quote(str(device_id), safe='')}"
            f"/components/{int(component_id)}?deviceType=connected"
        )
        desired_value: Any = schedules
        if int(component_id) == 258:
            desired_value = self._convert_schedules_to_dm24049704_format(schedules)
        payload = {"desiredValue": desired_value}

        try:
            status, _, raw_text = await self._request("PUT", url, headers=headers, json_data=payload)
        except FluidraError as err:
            _LOGGER.error("set_schedule error: %s", err)
            return False

        if status != 200:
            _LOGGER.debug("set_schedule body: %s", raw_text[:500])
        return status == 200

    async def get_default_schedule(self) -> list[dict[str, Any]]:
        """Return a default schedule template."""
        return [
            {
                "id": 1,
                "groupId": 1,
                "enabled": True,
                "startTime": "08 30 * * 1,2,3,4,5,6,7",
                "endTime": "09 59 * * 1,2,3,4,5,6,7",
                "startActions": {"operationName": 1},
            },
        ]

    async def clear_schedule(self, device_id: str, component_id: int = COMPONENT_SCHEDULE) -> bool:
        """Clear all schedules for a device."""
        return await self.set_schedule(device_id, [], component_id=component_id)
