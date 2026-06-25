"""Schedule selects (pump speed-per-slot + chlorinator speed/output-per-slot)."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import aiohttp
from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError

from ..api_resilience import FluidraError
from ..const import COMMAND_CONFIRMATION_DELAY, DOMAIN, UI_UPDATE_DELAY
from ..device_registry import DeviceIdentifier
from ..entity import FluidraPoolControlEntity
from ..utils import convert_cron_days

if TYPE_CHECKING:
    from ..coordinator import FluidraDataUpdateCoordinator
    from ..fluidra_api import FluidraPoolAPI

_LOGGER = logging.getLogger(__name__)


class FluidraScheduleModeSelect(FluidraPoolControlEntity, SelectEntity):
    """Select entity for choosing schedule mode (speed level) for existing pump schedules."""

    __slots__ = ("_schedule_id",)

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api: FluidraPoolAPI,
        pool_id: str,
        device_id: str,
        schedule_id: str,
    ) -> None:
        """Initialize the schedule mode select."""
        super().__init__(coordinator, api, pool_id, device_id)
        self._schedule_id = schedule_id

        self._attr_translation_key = "schedule_mode"
        self._attr_translation_placeholders = {"schedule_id": schedule_id}
        self._attr_unique_id = f"fluidra_{self._device_id}_schedule_{schedule_id}_mode"
        self._attr_entity_category = EntityCategory.CONFIG

        # Speed options for schedules (using translation keys from schedule_mode.state).
        self._attr_options = ["0", "1", "2"]

    def _get_schedule_data(self) -> dict[str, Any] | None:
        """Get schedule data from coordinator."""
        try:
            device_data = self.device_data

            if "schedule_data" in device_data:
                schedules = device_data["schedule_data"]

                for schedule in schedules:
                    schedule_id = schedule.get("id")
                    if str(schedule_id) == str(self._schedule_id):
                        schedule_data: dict[str, Any] = schedule
                        return schedule_data

        except (aiohttp.ClientError, TimeoutError, FluidraError, ValueError, TypeError, KeyError, AttributeError):
            _LOGGER.debug("Failed to get schedule data for %s", self._device_id)
        return None

    @property
    def available(self) -> bool:
        """Return True if the device/coordinator are healthy and the schedule exists."""
        return super().available and self._get_schedule_data() is not None

    @property
    def current_option(self) -> str | None:
        """Return the current mode option."""
        schedule = self._get_schedule_data()
        if schedule:
            operation = schedule.get("startActions", {}).get("operationName", "0")
            return str(operation)
        return "0"

    async def async_select_option(self, option: str) -> None:
        """Select new mode option using exact mobile app format."""
        if option not in self._attr_options:
            return

        try:
            device_data = self.device_data
            if "schedule_data" not in device_data:
                return

            current_schedules = device_data["schedule_data"]
            if not current_schedules:
                return

            updated_schedules = []
            for sched in current_schedules:
                start_time = convert_cron_days(sched.get("startTime", ""))
                end_time = convert_cron_days(sched.get("endTime", ""))

                operation_name = (
                    option
                    if str(sched.get("id")) == str(self._schedule_id)
                    else str(sched.get("startActions", {}).get("operationName", "0"))
                )

                scheduler = {
                    "id": sched.get("id"),
                    "groupId": sched.get("id"),
                    "enabled": sched.get("enabled", False),
                    "startTime": start_time,
                    "endTime": end_time,
                    "startActions": {"operationName": operation_name},
                }
                updated_schedules.append(scheduler)

            # No padding — Fluidra fills the remaining slots; padding to 8 with
            # identical placeholder windows is rejected as "OVERLAP in sched" (Issue #105).
            success = await self._api.set_schedule(self._device_id, updated_schedules)
            if not success:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="schedule_set_rejected",
                    translation_placeholders={"device_id": self._device_id},
                )
            await self.coordinator.async_request_refresh()

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
            _LOGGER.error("Failed to update schedule mode for %s: %s", self._device_id, err)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="schedule_set_failed",
                translation_placeholders={"device_id": self._device_id},
            ) from err

    @property
    def icon(self) -> str:
        """Return the icon for the entity."""
        icons = {
            "0": "mdi:speedometer-slow",
            "1": "mdi:speedometer-medium",
            "2": "mdi:speedometer",
        }
        return icons.get(self.current_option or "", "mdi:speedometer")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        schedule = self._get_schedule_data()
        attrs = {
            "schedule_id": self._schedule_id,
            "device_id": self._device_id,
            "available_modes": self._attr_options,
        }

        if schedule:
            attrs.update(
                {
                    "start_time": schedule.get("startTime", ""),
                    "end_time": schedule.get("endTime", ""),
                    "enabled": schedule.get("enabled", False),
                    "state": schedule.get("state", "IDLE"),
                }
            )

        return attrs


class FluidraChlorinatorScheduleSpeedSelect(FluidraPoolControlEntity, SelectEntity):
    """Select entity for chlorinator schedule speed (S1/S2/S3) or output (pump/aux1/aux2)."""

    __slots__ = ("_optimistic_option", "_output_type", "_schedule_id", "_speed_mapping", "_value_to_speed")

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api: FluidraPoolAPI,
        pool_id: str,
        device_id: str,
        schedule_id: str,
    ) -> None:
        """Initialize the chlorinator schedule speed select."""
        super().__init__(coordinator, api, pool_id, device_id)
        self._schedule_id = schedule_id
        self._optimistic_option: str | None = None

        device_data = self.device_data
        self._output_type = DeviceIdentifier.get_feature(device_data, "schedule_output_type", "speed")

        if self._output_type == "output":
            # EXO iQ35: controls hardware outputs (pump/aux1/aux2).
            self._attr_translation_key = "chlorinator_schedule_output"
            self._attr_options = ["pump", "aux1", "aux2"]
            self._speed_mapping = {"pump": "1", "aux1": "2", "aux2": "3"}
            self._value_to_speed = {"1": "pump", "2": "aux1", "3": "aux2"}
        else:
            # DM24049704: controls speed levels (S1/S2/S3).
            self._attr_translation_key = "chlorinator_schedule_speed"
            self._attr_options = ["s1", "s2", "s3"]
            self._speed_mapping = {"s1": "1", "s2": "2", "s3": "3"}
            self._value_to_speed = {"1": "s1", "2": "s2", "3": "s3"}

        self._attr_translation_placeholders = {"schedule_id": schedule_id}
        self._attr_unique_id = f"fluidra_{self._device_id}_schedule_{schedule_id}_speed"
        self._attr_entity_category = EntityCategory.CONFIG

    def _get_schedule_data(self) -> dict[str, Any] | None:
        """Get schedule data from coordinator."""
        try:
            device_data = self.device_data
            if "schedule_data" in device_data:
                schedules = device_data["schedule_data"]
                for schedule in schedules:
                    schedule_id = schedule.get("id")
                    if str(schedule_id) == str(self._schedule_id):
                        schedule_data: dict[str, Any] = schedule
                        return schedule_data
        except (aiohttp.ClientError, TimeoutError, FluidraError, ValueError, TypeError, KeyError, AttributeError):
            _LOGGER.debug("Failed to get schedule data for %s", self._device_id)
        return None

    @property
    def available(self) -> bool:
        """Return True if the device/coordinator are healthy and the schedule exists."""
        return super().available and self._get_schedule_data() is not None

    @property
    def current_option(self) -> str | None:
        """Return the current speed/output option."""
        if self._optimistic_option is not None:
            return self._optimistic_option

        schedule = self._get_schedule_data()
        if schedule:
            start_actions = schedule.get("startActions", {})
            component_actions = start_actions.get("componentActions", [])
            if component_actions:
                # EXO format: componentActions[0].reportedValue.
                value = str(component_actions[0].get("reportedValue", 1))
            else:
                # DM format: operationName.
                value = str(start_actions.get("operationName", "1"))
            default = self._attr_options[0]
            return self._value_to_speed.get(value, default)
        return self._attr_options[0]

    async def async_select_option(self, option: str) -> None:
        """Select new speed option."""
        if option not in self._speed_mapping:
            return

        try:
            self._optimistic_option = option
            self.async_write_ha_state()

            await asyncio.sleep(UI_UPDATE_DELAY)

            device_data = self.device_data
            if "schedule_data" not in device_data:
                # Reset the optimistic option so current_option falls back to
                # coordinator data instead of sticking on the unconfirmed value.
                self._optimistic_option = None
                self.async_write_ha_state()
                return

            current_schedules = device_data["schedule_data"]
            if not current_schedules:
                self._optimistic_option = None
                self.async_write_ha_state()
                return

            schedule_component = DeviceIdentifier.get_feature(device_data, "schedule_component", 258)

            updated_schedules = []
            for sched in current_schedules:
                start_time = sched.get("startTime", "00 00 * * 1,2,3,4,5,6,7")
                end_time = sched.get("endTime", "00 01 * * 1,2,3,4,5,6,7")

                if str(sched.get("id")) == str(self._schedule_id):
                    operation_name = self._speed_mapping[option]
                else:
                    start_actions = sched.get("startActions", {})
                    component_actions = start_actions.get("componentActions", [])
                    if component_actions:
                        operation_name = str(component_actions[0].get("reportedValue", 1))
                    else:
                        operation_name = str(start_actions.get("operationName", "1"))

                if self._output_type == "output":
                    # EXO format: componentActions with reportedValue.
                    scheduler = {
                        "id": sched.get("id"),
                        "groupId": sched.get("groupId", sched.get("id")),
                        "state": sched.get("state", "IDLE"),
                        "enabled": sched.get("enabled", True),
                        "startTime": start_time,
                        "endTime": end_time,
                        "startActions": {"componentActions": [{"id": 0, "reportedValue": int(operation_name)}]},
                    }
                elif schedule_component == 258:
                    # DM24049704 format: operationName with CRON padding.
                    scheduler = {
                        "id": sched.get("id"),
                        "groupId": 1,  # App always uses groupId=1 for all schedules.
                        "enabled": True,
                        "startTime": self._format_cron_time(start_time),
                        "endTime": self._format_cron_time(end_time),
                        "startActions": {"operationName": operation_name},
                    }
                else:
                    scheduler = {
                        "id": sched.get("id"),
                        "groupId": sched.get("id"),
                        "enabled": sched.get("enabled", False),
                        "startTime": start_time,
                        "endTime": end_time,
                        "startActions": {"operationName": operation_name},
                    }
                updated_schedules.append(scheduler)

            success = await self._api.set_schedule(self._device_id, updated_schedules, component_id=schedule_component)
            if success:
                await asyncio.sleep(COMMAND_CONFIRMATION_DELAY)
                await self.coordinator.async_request_refresh()
                self._optimistic_option = None
                self.async_write_ha_state()
            else:
                self._optimistic_option = None
                self.async_write_ha_state()

        except (
            aiohttp.ClientError,
            TimeoutError,
            FluidraError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
        ) as err:
            _LOGGER.error("Failed to set schedule speed: %s", err)
            self._optimistic_option = None
            self.async_write_ha_state()

    def _format_cron_time(self, cron_time: str) -> str:
        """Format CRON time to match official app format (00 05 * * 1,2,3,4,5,6,7)."""
        if not cron_time:
            return "00 00 * * 1,2,3,4,5,6,7"

        parts = cron_time.split()
        if len(parts) >= 5:
            minute = parts[0].zfill(2)
            hour = parts[1].zfill(2)
            days = parts[4] if parts[4] != "*" else "1,2,3,4,5,6,7"
            return f"{minute} {hour} * * {days}"

        return cron_time

    @property
    def icon(self) -> str:
        """Return the icon for the entity."""
        current = self.current_option
        if current == "s1":
            return "mdi:speedometer-slow"
        if current == "s2":
            return "mdi:speedometer-medium"
        return "mdi:speedometer"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        schedule = self._get_schedule_data()
        attrs = {
            "schedule_id": self._schedule_id,
            "device_id": self._device_id,
            "available_speeds": self._attr_options,
        }

        if schedule:
            attrs.update(
                {
                    "start_time": schedule.get("startTime", ""),
                    "end_time": schedule.get("endTime", ""),
                    "enabled": schedule.get("enabled", False),
                    "state": schedule.get("state", "UNKNOWN"),
                }
            )

        return attrs
