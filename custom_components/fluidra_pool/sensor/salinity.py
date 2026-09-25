"""Cell Guard salinity validity, source timestamps and restart persistence."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
import math
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import RestoreSensor, SensorDeviceClass, SensorEntity, SensorExtraStoredData
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.util import dt as dt_util

from ..device_registry import DeviceIdentifier
from .chlorinator import FluidraChlorinatorSensor

if TYPE_CHECKING:
    from ..coordinator import FluidraDataUpdateCoordinator
    from ..fluidra_api import FluidraPoolAPI

_STORAGE_KEY = "cellguard_salinity"
_STORAGE_VERSION = 1
_STATUSES = ["valid", "not_producing", "low_production", "production_unknown", "awaiting_reading", "no_flow", "offline"]


def _finite_number(value: Any) -> float | None:
    """Parse numbers without accepting booleans or non-finite values."""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _timestamp(value: Any) -> datetime | None:
    """Parse the observed epoch-second timestamps or an ISO time with a zone.

    Never substitute the time of the HA poll for a missing source timestamp.
    A naive ISO time has no documented timezone and cannot establish ordering.
    """
    parsed: datetime | None
    if isinstance(value, datetime):
        parsed = value
    else:
        numeric = _finite_number(value)
        if numeric is not None:
            if numeric <= 0:
                return None
            try:
                return datetime.fromtimestamp(numeric, UTC)
            except (OverflowError, OSError, ValueError):
                return None
        if not isinstance(value, str):
            return None
        try:
            parsed = dt_util.parse_datetime(value)
        except (ValueError, OverflowError):
            return None
    if parsed is None or parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _latest(*values: datetime | None) -> datetime | None:
    """Return the newest known timestamp."""
    return max((value for value in values if value is not None), default=None)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


@dataclass
class CellGuardSalinityStoredData(SensorExtraStoredData):
    """Keep a qualified last reading even when the displayed state is unknown."""

    salinity_data: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {**super().as_dict(), _STORAGE_KEY: self.salinity_data}


class FluidraCellGuardSalinitySensor(FluidraChlorinatorSensor, RestoreSensor):
    """Opt-in salinity sensor gated by actual Cell Guard production.

    The Cell Guard manual requires at least 30% production for its salinity
    test. A sufficient production value is necessary, not sufficient: a zero,
    absent or cached salinity report does not become a new measurement.
    """

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api: FluidraPoolAPI,
        pool_id: str,
        device_id: str,
        component_id: int,
    ) -> None:
        super().__init__(coordinator, api, pool_id, device_id, "salinity", component_id)
        minimum = _finite_number(DeviceIdentifier.get_feature(self.device_data, "salinity_min_production", 30))
        self._minimum_production = minimum if minimum is not None and 0 < minimum <= 100 else 30.0
        self._measurement_status = "awaiting_reading"
        self._actual_production: float | None = None
        self._last_salinity_ts: datetime | None = None
        self._last_production_ts: datetime | None = None
        self._invalidated_through: datetime | None = None
        self._eligible_since: datetime | None = None
        self._production_was_eligible = False
        self._current_poll_trustworthy = False
        self._status_ready = False
        self._status_listeners: set[Callable[[], None]] = set()

    @property
    def measurement_status(self) -> str:
        """Return why the current report is usable, or why the value is held."""
        return self._measurement_status

    @property
    def status_available(self) -> bool:
        """The companion needs an active numeric entity to evaluate snapshots."""
        return self._status_ready

    @callback
    def async_add_status_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Subscribe after the numeric sensor has evaluated a coordinator update."""
        self._status_listeners.add(listener)

        @callback
        def remove() -> None:
            self._status_listeners.discard(listener)

        return remove

    @callback
    def _notify_status_listeners(self) -> None:
        for listener in tuple(self._status_listeners):
            listener()

    def _poll_is_trustworthy(self) -> bool:
        return self._current_poll_trustworthy

    def _invalidate_reading(self, status: str, production_ts: datetime | None = None) -> None:
        """Require a newer salt report after a period when measurement is invalid."""
        self._measurement_status = status
        self._invalidated_through = _latest(self._invalidated_through, self._last_salinity_ts, production_ts)

    def _update_last_known_value(self) -> None:
        """Evaluate one complete coordinator snapshot without re-dating cache hits."""
        device = self.device_data
        components = device.get("components", {})
        salt = components.get(str(self._resolved_component_id), {})
        sensors = DeviceIdentifier.get_feature(device, "sensors", {})
        production_id = sensors.get("chlorination_actual")
        production = components.get(str(production_id), {}) if isinstance(production_id, int) else {}
        salt_ts = _timestamp(salt.get("ts"))
        production_ts = _timestamp(production.get("ts"))
        previous_salt_ts = self._last_salinity_ts
        previous_production_ts = self._last_production_ts
        salt_advanced = salt_ts is not None and previous_salt_ts is not None and salt_ts > previous_salt_ts
        production_advanced = (
            production_ts is not None and previous_production_ts is not None and production_ts > previous_production_ts
        )
        self._current_poll_trustworthy = (
            self.coordinator.last_update_success
            and bool(device)
            and bool(components)
            and (device.get("online") is not False or salt_advanced or production_advanced)
        )
        self._last_salinity_ts = _latest(previous_salt_ts, salt_ts)
        self._last_production_ts = _latest(previous_production_ts, production_ts)

        actual = _finite_number(production.get("reportedValue"))
        if actual is None or not 0 <= actual <= 100:
            actual = None
        if production_ts is not None and previous_production_ts is not None and production_ts < previous_production_ts:
            actual = None  # An out-of-order snapshot cannot re-enable measurement.
        self._actual_production = actual
        was_eligible = self._production_was_eligible
        self._production_was_eligible = actual is not None and actual >= self._minimum_production

        if not self._current_poll_trustworthy:
            self._production_was_eligible = False
            self._invalidate_reading("offline")
            return
        if self._no_flow_reported():
            self._production_was_eligible = False
            self._invalidate_reading("no_flow")
            return
        if actual is None:
            self._invalidate_reading("production_unknown")
            return
        if actual == 0:
            self._invalidate_reading("not_producing", production_ts)
            return
        if actual < self._minimum_production:
            self._invalidate_reading("low_production", production_ts)
            return

        if not was_eligible:
            # A source report from before production resumed must stay historical.
            self._eligible_since = production_ts
        raw = _finite_number(salt.get("reportedValue"))
        divisor = _finite_number(self._divisor)
        value = raw / divisor if raw is not None and divisor is not None and divisor > 0 else None
        if value is None or not math.isfinite(value) or value <= 0 or salt_ts is None:
            self._invalidate_reading("awaiting_reading")
            return
        if (
            (previous_salt_ts is not None and salt_ts < previous_salt_ts)
            or (self._invalidated_through is not None and salt_ts <= self._invalidated_through)
            or (self._eligible_since is not None and salt_ts < self._eligible_since)
        ):
            self._measurement_status = "awaiting_reading"
            return
        if self._last_known_at is not None and salt_ts <= self._last_known_at:
            # Repeated reports keep their original timestamp; a changed value at
            # the same/older timestamp is not a newly qualified measurement.
            self._measurement_status = (
                "valid" if salt_ts == self._last_known_at and value == self._last_known_value else "awaiting_reading"
            )
            return
        self._last_known_value = value
        self._last_known_at = salt_ts
        self._measurement_status = "valid"

    @callback
    def _handle_coordinator_update(self) -> None:
        self._update_last_known_value()
        self.async_write_ha_state()
        self._notify_status_listeners()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (restored := await self.async_get_last_extra_data()) is not None:
            self._restore_extra_data(restored.as_dict())
        self._update_last_known_value()
        self._status_ready = True
        self._notify_status_listeners()

    async def async_will_remove_from_hass(self) -> None:
        self._status_ready = False
        self._notify_status_listeners()
        await super().async_will_remove_from_hass()

    def _restore_extra_data(self, restored: dict[str, Any]) -> None:
        """Restore only versioned, qualified values, never legacy display state."""
        data = restored.get(_STORAGE_KEY)
        if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != _STORAGE_VERSION:
            return
        if restored.get("native_unit_of_measurement") != self.native_unit_of_measurement:
            return
        value = _finite_number(data.get("last_known_value"))
        measured_at = _timestamp(data.get("last_known_at"))
        if value is not None and value > 0 and measured_at is not None:
            if self._last_known_at is None or measured_at > self._last_known_at:
                self._last_known_value = value
                self._last_known_at = measured_at
        self._last_salinity_ts = _latest(
            self._last_salinity_ts, _timestamp(data.get("last_salinity_ts")), self._last_known_at
        )
        self._last_production_ts = _latest(self._last_production_ts, _timestamp(data.get("last_production_ts")))
        # Restored values remain historical until a newer qualifying report.
        self._invalidated_through = _latest(
            self._invalidated_through, _timestamp(data.get("invalidated_through")), self._last_salinity_ts
        )

    @property
    def extra_restore_state_data(self) -> CellGuardSalinityStoredData:
        return CellGuardSalinityStoredData(
            self.native_value,
            self.native_unit_of_measurement,
            {
                "version": _STORAGE_VERSION,
                "last_known_value": self._last_known_value,
                "last_known_at": _iso(self._last_known_at),
                "last_salinity_ts": _iso(self._last_salinity_ts),
                "last_production_ts": _iso(self._last_production_ts),
                "invalidated_through": _iso(self._invalidated_through),
            },
        )

    @property
    def native_value(self) -> float | None:
        if self._measurement_status == "no_flow":
            return None
        return self._last_known_value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attributes = super().extra_state_attributes
        attributes.update(
            {
                "measurement_status": self._measurement_status,
                "is_current_reading": self._measurement_status == "valid",
                "actual_production": self._actual_production,
                "minimum_production": self._minimum_production,
                "timestamp_source": "component" if self._last_known_at is not None else None,
                "low_production": self._actual_production is not None
                and self._actual_production < self._minimum_production,
            }
        )
        return attributes


class FluidraSalinityStatusSensor(SensorEntity):
    """Explain the numeric salinity state using the same evaluated snapshot."""

    _attr_has_entity_name = True
    _attr_translation_key = "chlorinator_salinity_status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = _STATUSES
    _attr_icon = "mdi:water-check"
    _attr_should_poll = False

    def __init__(self, salinity_sensor: FluidraCellGuardSalinitySensor) -> None:
        self._salinity_sensor = salinity_sensor
        self._attr_unique_id = f"fluidra_{salinity_sensor._device_id}_salinity_status"

    @property
    def device_info(self) -> DeviceInfo:
        return self._salinity_sensor.device_info

    @property
    def native_value(self) -> str:
        return self._salinity_sensor.measurement_status

    @property
    def available(self) -> bool:
        """A disabled or removed source must not look as if it is still polling."""
        return self._salinity_sensor.status_available

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._salinity_sensor.async_add_status_listener(self._handle_salinity_update))

    @callback
    def _handle_salinity_update(self) -> None:
        self.async_write_ha_state()
