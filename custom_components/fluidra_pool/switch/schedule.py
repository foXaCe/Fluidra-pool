"""Schedule enable/disable switch (one entity per schedule slot)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import aiohttp
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError

from ..api_resilience import FluidraError
from ..const import DOMAIN
from ..device_registry import DeviceIdentifier
from ..helpers import get_schedule_data
from ..utils import convert_cron_days
from .base import FluidraPoolSwitchEntity

if TYPE_CHECKING:
    from ..coordinator import FluidraDataUpdateCoordinator
    from ..fluidra_api import FluidraPoolAPI

_LOGGER = logging.getLogger(__name__)


class FluidraScheduleEnableSwitch(FluidraPoolSwitchEntity):
    """Switch for enabling/disabling existing schedules."""

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api: FluidraPoolAPI,
        pool_id: str,
        device_id: str,
        schedule_id: str,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, api, pool_id, device_id)
        self._schedule_id = schedule_id

        self._attr_translation_key = "schedule_enable"
        self._attr_translation_placeholders = {"schedule_id": schedule_id}
        self._attr_unique_id = f"fluidra_{self._device_id}_schedule_{schedule_id}_enabled"
        self._attr_entity_category = EntityCategory.CONFIG

    @property
    def unique_id(self) -> str:
        """Return unique ID."""
        return self._attr_unique_id or f"{DOMAIN}_{self._pool_id}_{self._device_id}_schedule_{self._schedule_id}"

    @property
    def icon(self) -> str:
        """Return the icon of the switch."""
        if self.is_on:
            return "mdi:calendar-clock"
        return "mdi:calendar-outline"

    def _get_schedule_data(self) -> dict[str, Any] | None:
        """Get schedule data from coordinator."""
        try:
            return get_schedule_data(self.device_data, self._schedule_id)
        except (aiohttp.ClientError, TimeoutError, FluidraError, ValueError, TypeError, KeyError, AttributeError):
            _LOGGER.debug("Failed to get schedule data for %s", self._device_id)
            return None

    def _get_schedule_component(self) -> int:
        """Get the schedule component used by this device."""
        value: int = DeviceIdentifier.get_feature(self.device_data, "schedule_component", 20)
        return value

    @property
    def available(self) -> bool:
        """Return True if the device/coordinator are healthy and the schedule exists."""
        return super().available and self._get_schedule_data() is not None

    @property
    def is_on(self) -> bool:
        """Return true if the schedule is enabled using optimistic UI."""
        schedule = self._get_schedule_data()
        if self._pending_state is not None:
            # Drop the optimistic state as soon as the server has caught up,
            # or after 15 s as a safety net (the coordinator debounces refresh
            # by 1.5 s and a full poll can take a few seconds on top).
            if (
                schedule and bool(schedule.get("enabled", False)) == self._pending_state
            ) or self._pending_state_expired(15):
                self._clear_pending_state()
            else:
                return self._pending_state

        if schedule:
            value: bool = schedule.get("enabled", False)
            return value
        return False

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the schedule using exact mobile app format with optimistic UI."""
        self._ensure_pool_writable()
        try:
            self._set_pending_state(True)
            device_data = self.device_data
            if "schedule_data" not in device_data:
                self._clear_pending_state()
                return

            current_schedules = device_data["schedule_data"]
            if not current_schedules:
                self._clear_pending_state()
                return
            schedule_component = self._get_schedule_component()

            updated_schedules = []
            for sched in current_schedules:
                start_time = convert_cron_days(sched.get("startTime", ""))
                end_time = convert_cron_days(sched.get("endTime", ""))

                scheduler = {
                    "id": sched.get("id"),
                    "groupId": sched.get("id"),
                    "enabled": True if str(sched.get("id")) == str(self._schedule_id) else sched.get("enabled", False),
                    "startTime": start_time,
                    "endTime": end_time,
                    "startActions": {"operationName": str(sched.get("startActions", {}).get("operationName", "0"))},
                }
                updated_schedules.append(scheduler)

            # No padding — Fluidra fills the remaining slots; padding to 8 with
            # identical placeholder windows is rejected as "OVERLAP in sched" (Issue #105).
            success = await self._api.set_schedule(self._device_id, updated_schedules, component_id=schedule_component)
            if success:
                # Keep optimistic state until is_on observes server confirmation
                # or the 15 s safety timeout — clearing here flipped the UI back.
                await self.coordinator.async_request_refresh()
            else:
                self._clear_pending_state()
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="schedule_set_failed",
                    translation_placeholders={"device_id": self._device_id},
                )

        except HomeAssistantError:
            raise
        except (
            aiohttp.ClientError,
            TimeoutError,
            FluidraError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
        ) as err:
            _LOGGER.debug("Failed to enable schedule: %s", err)
            self._clear_pending_state()
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="schedule_set_failed",
                translation_placeholders={"device_id": self._device_id},
            ) from err

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the schedule using exact mobile app format with optimistic UI."""
        self._ensure_pool_writable()
        try:
            self._set_pending_state(False)
            device_data = self.device_data
            if "schedule_data" not in device_data:
                self._clear_pending_state()
                return

            current_schedules = device_data["schedule_data"]
            if not current_schedules:
                self._clear_pending_state()
                return
            schedule_component = self._get_schedule_component()

            updated_schedules = []
            for sched in current_schedules:
                start_time = convert_cron_days(sched.get("startTime", ""))
                end_time = convert_cron_days(sched.get("endTime", ""))

                scheduler = {
                    "id": sched.get("id"),
                    "groupId": sched.get("id"),
                    "enabled": False if str(sched.get("id")) == str(self._schedule_id) else sched.get("enabled", False),
                    "startTime": start_time,
                    "endTime": end_time,
                    "startActions": {"operationName": str(sched.get("startActions", {}).get("operationName", "0"))},
                }
                updated_schedules.append(scheduler)

            # No padding — see the OVERLAP-in-sched note in async_turn_on (Issue #105).
            success = await self._api.set_schedule(self._device_id, updated_schedules, component_id=schedule_component)
            if success:
                await self.coordinator.async_request_refresh()
            else:
                self._clear_pending_state()
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="schedule_set_failed",
                    translation_placeholders={"device_id": self._device_id},
                )

        except HomeAssistantError:
            raise
        except (
            aiohttp.ClientError,
            TimeoutError,
            FluidraError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
        ) as err:
            _LOGGER.debug("Failed to disable schedule: %s", err)
            self._clear_pending_state()
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="schedule_set_failed",
                translation_placeholders={"device_id": self._device_id},
            ) from err

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        schedule = self._get_schedule_data()
        attrs: dict[str, Any] = {
            "schedule_id": self._schedule_id,
            "device_id": self._device_id,
        }

        if schedule:
            attrs.update(
                {
                    "start_time": schedule.get("startTime", ""),
                    "end_time": schedule.get("endTime", ""),
                    "state": schedule.get("state", "IDLE"),
                    "start_action": schedule.get("startActions", {}),
                    "end_action": schedule.get("endActions", {}),
                }
            )

        attrs.update({"pending_action": self._pending_state is not None, "action_timestamp": self._last_action_time})

        return attrs
