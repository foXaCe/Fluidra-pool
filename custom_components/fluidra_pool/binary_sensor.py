"""Binary sensor platform for Fluidra Pool integration."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.util import dt as dt_util

from .const import DEVICE_TYPE_CHLORINATOR, DOMAIN, FluidraPoolConfigEntry
from .device_registry import DeviceIdentifier
from .entity import FluidraPoolEntity
from .platform_setup import async_setup_dynamic_platform

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import FluidraDataUpdateCoordinator
    from .fluidra_api import FluidraPoolAPI

PARALLEL_UPDATES = 0  # Coordinator handles all updates


class FluidraChlorinatorProducingBinarySensor(FluidraPoolEntity, BinarySensorEntity):
    """Binary sensor for the chlorinator cell active-production state.

    In ORP/CLI regulation the cell cycles on and off around the setpoint: the
    production register (configured via the ``cell_production_state`` feature)
    reads ``0`` when the cell is idle and a non-zero production percentage when
    it is actively producing chlorine (Issue #109).
    """

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api: FluidraPoolAPI,
        pool_id: str,
        device_id: str,
        component_id: int,
    ) -> None:
        """Initialize the chlorinator producing binary sensor."""
        super().__init__(coordinator, pool_id, device_id)
        self._api = api
        self._component_id = component_id

        self._attr_unique_id = f"fluidra_{self._device_id}_producing"
        self._attr_translation_key = "chlorinator_producing"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        device_name = self.device_data.get("name") or f"Chlorinator {self._device_id}"
        firmware = self.device_data.get("firmware_version_component")
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=device_name,
            manufacturer=self.device_data.get("manufacturer", "Fluidra"),
            model="Chlorinator",
            sw_version=str(firmware) if firmware is not None else None,
            via_device=(DOMAIN, self._pool_id),
        )

    @property
    def available(self) -> bool:
        """Return True if entity is available.

        Mirrors the chlorinator sensors: bridged children can report
        ``online=False`` even while polling succeeds, so use the presence of
        fresh component data as the availability signal instead (Issue #63).
        """
        return self.coordinator.last_update_success and bool(self.device_data.get("components"))

    @property
    def is_on(self) -> bool | None:
        """Return True when the cell is actively producing."""
        components = self.device_data.get("components", {})
        component_data = components.get(str(self._component_id), {})
        raw_value = component_data.get("reportedValue")

        if raw_value is None:
            return None

        try:
            return float(raw_value) > 0
        except (ValueError, TypeError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        components = self.device_data.get("components", {})
        component_data = components.get(str(self._component_id), {})

        return {
            "component_id": self._component_id,
            "raw_value": component_data.get("reportedValue"),
            "device_id": self._device_id,
        }


class FluidraChlorinatorAlarmBinarySensor(FluidraPoolEntity, BinarySensorEntity):
    """Binary sensor for active chlorinator alarms (e.g. "PUMPSTOP PH").

    Fluidra's cloud reports per-device alarms in the raw ``status.alarms``
    array returned by ``GET .../generic/devices?format=tree`` — not in any of
    the numbered ``specific_components`` the rest of the integration scans,
    so they are otherwise invisible to Home Assistant. Each entry has an
    ``errorCode``, a ``default.title``/``default.text`` pair, and a boolean
    ``value`` marking whether that specific alarm is currently active. The
    coordinator copies this list verbatim onto ``device["alarms"]``.
    """

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        pool_id: str,
        device_id: str,
    ) -> None:
        """Initialize the chlorinator alarm binary sensor."""
        super().__init__(coordinator, pool_id, device_id)

        self._attr_unique_id = f"fluidra_{self._device_id}_alarm"
        self._attr_translation_key = "chlorinator_alarm"

        # Last alarm state confirmed on a trustworthy (online) poll, frozen
        # while the device stays offline. In-memory only — reset to None on
        # every integration reload/HA restart until the next trustworthy
        # poll (see _update_last_known).
        self._last_known_state: bool | None = None
        self._last_known_at: datetime | None = None

    @property
    def available(self) -> bool:
        """Return True if entity is available.

        Mirrors the chlorinator measurement sensors, not the control
        entities: bridged children can report ``online=False`` on a
        transient MQTT_KEEP_ALIVE_TIMEOUT while polling keeps succeeding
        (Issue #63) — the alarm state is diagnostic information like
        pH/ORP/temperature, not a control surface, so it should keep
        showing its last known value through those blips rather than
        disappear exactly when an operator might want to check it.
        """
        return self.coordinator.last_update_success and bool(self.device_data)

    def _active_alarms(self) -> list[dict[str, Any]]:
        """Return raw alarm entries currently active, filtering out malformed ones."""
        alarms = self.device_data.get("alarms") or []
        valid = []
        for alarm in alarms:
            if not isinstance(alarm, dict) or not alarm.get("value"):
                continue
            default = alarm.get("default")
            if default is not None and not isinstance(default, dict):
                continue
            valid.append(alarm)
        return valid

    @staticmethod
    def _flatten_alarm(alarm: dict[str, Any]) -> dict[str, Any]:
        """Flatten a raw alarm entry to the {error_code, title, text} shape."""
        default = alarm.get("default") or {}
        return {
            "error_code": alarm.get("errorCode"),
            "title": default.get("title"),
            "text": default.get("text"),
        }

    def _poll_is_trustworthy(self) -> bool:
        """Return True when this poll's ``alarms[]`` content can be trusted.

        False whenever the coordinator update itself failed, or the device
        reports ``online=False`` — Fluidra's cloud can keep serving a cached
        ``alarms[]`` snapshot for a disconnected device (confirmed
        2026-07-20 — PUMPSTOP PH stayed reported ``True`` for 8+ hours after
        the chlorinator was physically powered off, alongside a frozen ORP
        reading).
        """
        return self.coordinator.last_update_success and self.device_data.get("online") is not False

    def _update_last_known(self) -> None:
        """Snapshot the confirmed alarm state, once per trustworthy poll.

        Kept separate from ``_handle_coordinator_update`` so it can be
        exercised in tests without a real ``hass``/entity_id (which
        ``async_write_ha_state`` — called by the base
        ``_handle_coordinator_update`` — requires).
        """
        if self._poll_is_trustworthy():
            self._last_known_state = bool(self._active_alarms())
            self._last_known_at = dt_util.utcnow()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator.

        Runs once per coordinator refresh (unlike ``is_on``/
        ``extra_state_attributes``, which HA may read multiple times per
        update), so this is the right place for the last-known-good
        bookkeeping rather than a side effect inside those properties.
        """
        self._update_last_known()
        super()._handle_coordinator_update()

    @property
    def is_on(self) -> bool | None:
        """Return True when at least one alarm is active.

        Returns None (unknown) whenever the current poll can't be trusted
        (see ``_poll_is_trustworthy``) — neither True nor False can be
        trusted from stale data, not even False, since that could hide an
        alarm that was genuinely active in the last real read.
        """
        if not self._poll_is_trustworthy():
            return None
        return bool(self._active_alarms())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes.

        Only the first active alarm is surfaced as top-level ``error_code``/
        ``title``/``text`` attributes (matching how the official app
        highlights one alarm at a time, and kept for dashboard
        compatibility); ``active_alarms`` carries the full flattened list so
        multiple simultaneous alarms can be identified (follow-up to PR
        #170), and ``active_alarm_count`` tells you its length.

        ``active_alarms`` is always present, defaulting to ``[]`` — like
        ``last_known_*``/``device_offline`` below — so the attribute's shape
        never changes between updates.

        ``last_known_*`` and ``device_offline`` are always present so a
        dashboard can show something better than a bare "unknown" while the
        device is offline — e.g. "last known: no alarm, confirmed 12 min ago"
        — since ``is_on`` deliberately refuses to guess from stale data.
        """
        active = self._active_alarms()
        attributes: dict[str, Any] = {
            "device_id": self._device_id,
            "active_alarm_count": len(active),
            "active_alarms": [self._flatten_alarm(alarm) for alarm in active],
            "device_offline": self.device_data.get("online") is False,
            "last_known_state": self._last_known_state,
            "last_known_at": self._last_known_at.isoformat() if self._last_known_at else None,
        }
        if active:
            first = self._flatten_alarm(active[0])
            attributes["error_code"] = first["error_code"]
            attributes["title"] = first["title"]
            attributes["text"] = first["text"]
        return attributes


class FluidraPumpSpeedInputBinarySensor(FluidraPoolEntity, BinarySensorEntity):
    """Speed-preset dry-contact digital input on a Victoria VS pump (Issue #144).

    These physical input terminals — Low (c29), Medium (c28), High (c27) — read
    active only when an external relay is wired to them (e.g. an ice-guard
    interlock forcing the pump on). Exposed as diagnostic binary sensors so users
    can automate on them. Decoded by the coordinator into ``pump_speed_input_*``.
    """

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api: FluidraPoolAPI,
        pool_id: str,
        device_id: str,
        tier: str,
    ) -> None:
        """Initialize the speed-input binary sensor for a given tier."""
        super().__init__(coordinator, pool_id, device_id)
        self._api = api
        self._tier = tier
        self._attr_unique_id = f"{DOMAIN}_{pool_id}_{device_id}_speed_input_{tier}"
        self._attr_translation_key = f"speed_input_{tier}"

    @property
    def is_on(self) -> bool | None:
        """Return True when this dry-contact input is active."""
        value = self.device_data.get(f"pump_speed_input_{self._tier}")
        return bool(value) if value is not None else None


class FluidraHeatPumpAlarmBinarySensor(FluidraPoolEntity, BinarySensorEntity):
    """A heat-pump fault reported by the unit itself (Issue #139).

    Currently the Z260iQ family's error E13 — intake air above ~43 °C, which makes
    the unit refuse to run (identified on a Z250iQ by @Kal42). Exposed as a problem
    sensor so it can drive a notification instead of the pump silently not heating.
    """

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api: FluidraPoolAPI,
        pool_id: str,
        device_id: str,
        alarm_key: str,
    ) -> None:
        """Initialize the heat-pump alarm sensor."""
        super().__init__(coordinator, pool_id, device_id)
        self._api = api
        self._alarm_key = alarm_key
        self._attr_unique_id = f"{DOMAIN}_{pool_id}_{device_id}_{alarm_key}"
        self._attr_translation_key = alarm_key

    @property
    def is_on(self) -> bool | None:
        """Return True while the fault is active, None before it's been reported."""
        value = self.device_data.get(self._alarm_key)
        return bool(value) if value is not None else None


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: FluidraPoolConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Fluidra Pool binary sensors, including devices added later."""
    coordinator = config_entry.runtime_data.coordinator

    def _build(pool_id: str, device: dict[str, Any]) -> list[BinarySensorEntity]:
        """Create binary sensors for one device."""
        entities: list[BinarySensorEntity] = []
        device_id = device["device_id"]

        production_component = DeviceIdentifier.get_feature(device, "cell_production_state")
        if production_component is not None:
            entities.append(
                FluidraChlorinatorProducingBinarySensor(
                    coordinator,
                    coordinator.api,
                    pool_id,
                    device_id,
                    production_component,
                )
            )

        # Alarms live in the raw status tree (device["alarms"]), not in any
        # specific_components feature, so every chlorinator gets this sensor
        # regardless of which feature registers its profile declares.
        config = DeviceIdentifier.identify_device(device)
        device_type = config.device_type if config else device.get("type", "")
        if device_type == DEVICE_TYPE_CHLORINATOR:
            entities.append(
                FluidraChlorinatorAlarmBinarySensor(
                    coordinator,
                    pool_id,
                    device_id,
                )
            )

        # Z260iQ-family faults reported on their own registers (Issue #139).
        # E03 no-flow (c28) was only ever an attribute on the climate entity, so it
        # couldn't drive an automation — @Kal42 rightly flagged it as missing.
        # Z650iQ uses c39 for compressor hours (not the air-temp alarm), so
        # that alarm key is skipped for it — the coordinator never populates
        # "air_temperature_alarm" for this family, which would otherwise leave
        # a permanently-off, meaningless binary_sensor entity.
        if DeviceIdentifier.has_feature(device, "z260iq_mode"):
            alarm_keys = ["no_flow_alarm"]
            if not DeviceIdentifier.has_feature(device, "z650iq_mode"):
                alarm_keys.append("air_temperature_alarm")
            entities.extend(
                FluidraHeatPumpAlarmBinarySensor(
                    coordinator,
                    coordinator.api,
                    pool_id,
                    device_id,
                    alarm_key,
                )
                for alarm_key in alarm_keys
            )

        # Victoria VS speed-preset dry-contact inputs (Issue #144).
        speed_inputs = DeviceIdentifier.get_feature(device, "speed_input_components")
        if isinstance(speed_inputs, dict):
            for tier in ("low", "medium", "high"):
                if tier in speed_inputs:
                    entities.append(
                        FluidraPumpSpeedInputBinarySensor(
                            coordinator,
                            coordinator.api,
                            pool_id,
                            device_id,
                            tier,
                        )
                    )

        return entities

    await async_setup_dynamic_platform(config_entry, async_add_entities, _build)
