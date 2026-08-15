"""Chlorinator measurement sensors (pH, ORP, chlorine, temperature, salinity)."""

from __future__ import annotations

from datetime import datetime
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfConductivity,
    UnitOfElectricPotential,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.util import dt as dt_util

from ..const import DOMAIN
from ..device_registry import DeviceIdentifier
from ..entity import FluidraPoolEntity

if TYPE_CHECKING:
    from ..coordinator import FluidraDataUpdateCoordinator
    from ..fluidra_api import FluidraPoolAPI

_LOGGER = logging.getLogger(__name__)


class FluidraBoostRemainingSensor(FluidraPoolEntity, SensorEntity):
    """Minutes left on a running boost cycle (eXO iQ c51).

    The register counts down from the boost duration (1438 for a 24 h cycle)
    and sits at 0 whenever boost is off — 0 is a real reading here, not a
    missing one, so it is reported as-is rather than as unknown.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "boost_remaining"
    _attr_icon = "mdi:timer-sand"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api: FluidraPoolAPI,
        pool_id: str,
        device_id: str,
    ) -> None:
        """Initialize the boost countdown sensor."""
        super().__init__(coordinator, pool_id, device_id)
        self._api = api
        self._attr_unique_id = f"fluidra_{device_id}_boost_remaining"

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
        """Return True once the device has reported component data."""
        return self.coordinator.last_update_success and bool(self.device_data.get("components"))

    @property
    def native_value(self) -> int | None:
        """Return the remaining boost minutes."""
        component = DeviceIdentifier.get_feature(self.device_data, "boost_remaining", None)
        if component is None:
            return None
        components = self.device_data.get("components", {})
        raw = components.get(str(component), {}).get("reportedValue")
        if raw is None:
            return None
        try:
            return int(raw)
        except (ValueError, TypeError):
            _LOGGER.debug("Unparsable boost countdown value %s on component %s", raw, component)
            return None


class FluidraChlorinatorSensor(FluidraPoolEntity, SensorEntity):
    """Sensor for chlorinator measurements (pH, ORP, chlorine, temperature, salinity)."""

    _attr_has_entity_name = True

    # Sensor types whose native_value falls back to the last confirmed
    # reading instead of echoing whatever sits in device_data["components"]
    # verbatim -- either because the device has gone offline (component data
    # can be a frozen cloud-cached snapshot, confirmed 2026-08-10:
    # chlorination_actual and salinity both stayed at their last real values,
    # timestamp-for-timestamp identical to a direct live API call, for 12+
    # hours after the chlorinator was physically powered off) or, for
    # salinity specifically, because the probe temporarily can't measure
    # during low production (Issue #129). Every other sensor_type reads
    # device_data verbatim in native_value -- but still tracks
    # last_known_at (see _update_last_known_value), since the raw echo
    # already freezes on its own once the device stops reporting, so a
    # fallback there would produce the same displayed value through a
    # second code path rather than changing anything observable.
    _STALENESS_GUARDED_TYPES = frozenset({"salinity", "chlorination_actual"})

    # Sensor types where a reading of exactly 0 means "no real measurement"
    # rather than a genuine 0 -- a bare register with no probe attached
    # (Issue #111), or (salinity only) production too low for the probe to
    # measure (Issue #129). Excluded from the last-known snapshot so an
    # artifact 0 can't overwrite a real prior reading with a fake one.
    # chlorination_actual/temperature/free_chlorine/conductivity/
    # battery_voltage have no such artifact and are not in this set.
    _ZERO_IS_NOT_A_REAL_READING = frozenset({"salinity", "orp", "ph"})

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api: FluidraPoolAPI,
        pool_id: str,
        device_id: str,
        sensor_type: str,
        component_id: int,
    ) -> None:
        """Initialize the chlorinator sensor."""
        super().__init__(coordinator, pool_id, device_id)
        self._api = api
        self._sensor_type = sensor_type
        self._component_id = component_id

        # Last confirmed-good reading and when it was confirmed, updated for
        # every sensor_type on each trustworthy poll (see
        # _update_last_known_value). In-memory only — reset to None on every
        # integration reload/HA restart until the next trustworthy reading.
        # last_known_at is exposed in extra_state_attributes for all eight
        # sensor_type values; last_known_value only for the two in
        # _STALENESS_GUARDED_TYPES, which is also the only place
        # last_known_value itself is read (native_value's fallback).
        self._last_known_value: float | None = None
        self._last_known_at: datetime | None = None

        # Baseline for corroborating an online=False report against the
        # resolved component's raw `ts` field (see _poll_is_trustworthy).
        # None until a poll has actually been recorded, so the very first
        # sighting of an offline-flagged device has nothing to compare
        # against yet and cannot be corroborated as fresh.
        self._last_seen_component_ts: Any = None
        # Cached once per poll, in _update_last_known_value: whether an
        # online=False poll was corroborated as fresh by an advancing `ts`.
        # _poll_is_trustworthy reads this cached value instead of
        # recomputing the ts comparison on every call, so every property
        # read within the same poll cycle agrees -- recomputing live would
        # make the very read that just confirmed freshness immediately
        # disagree with itself, since by then _last_seen_component_ts has
        # already advanced to match the current ts.
        self._offline_poll_confirmed_fresh: bool = False

        # Sensor configuration based on type
        self._sensor_config: dict[str, dict[str, Any]] = {
            "ph": {
                "translation_key": "chlorinator_ph",
                "unit": None,
                "device_class": None,
                "state_class": SensorStateClass.MEASUREMENT,
                "icon": "mdi:ph",
                "divisor": 100,  # Component value is pH * 100 (720 = 7.20)
            },
            "orp": {
                "translation_key": "chlorinator_orp",
                "unit": UnitOfElectricPotential.MILLIVOLT,
                "device_class": SensorDeviceClass.VOLTAGE,
                "state_class": SensorStateClass.MEASUREMENT,
                "icon": "mdi:lightning-bolt",
                "divisor": 1,
            },
            "free_chlorine": {
                "translation_key": "chlorinator_free_chlorine",
                "unit": "mg/L",
                "device_class": None,
                "state_class": SensorStateClass.MEASUREMENT,
                "icon": "mdi:test-tube",
                "divisor": 100,  # Component value is mg/L * 100
            },
            "temperature": {
                "translation_key": "chlorinator_water_temperature",
                "unit": UnitOfTemperature.CELSIUS,
                "device_class": SensorDeviceClass.TEMPERATURE,
                "state_class": SensorStateClass.MEASUREMENT,
                "icon": "mdi:thermometer",
                "divisor": 10,  # Component value is °C * 10
            },
            "salinity": {
                "translation_key": "chlorinator_salinity",
                "unit": "g/L",
                "device_class": None,
                "state_class": SensorStateClass.MEASUREMENT,
                "icon": "mdi:water-opacity",
                "divisor": 100,  # Component value is g/L * 100
            },
            "chlorination_actual": {
                "translation_key": "chlorinator_chlorination_actual",
                "unit": PERCENTAGE,
                "device_class": None,
                "state_class": SensorStateClass.MEASUREMENT,
                "icon": "mdi:percent",
                "divisor": 1,  # Already a percentage.
            },
            "conductivity": {
                "translation_key": "chlorinator_conductivity",
                "unit": UnitOfConductivity.MICROSIEMENS_PER_CM,
                "device_class": SensorDeviceClass.CONDUCTIVITY,
                "state_class": SensorStateClass.MEASUREMENT,
                "icon": "mdi:sine-wave",
                "divisor": 1,  # Direct µS/cm (Issue #186: 1362 = 1362 µS/cm).
            },
            "battery_voltage": {
                "translation_key": "chlorinator_battery_voltage",
                "unit": UnitOfElectricPotential.MILLIVOLT,
                "device_class": SensorDeviceClass.VOLTAGE,
                "state_class": SensorStateClass.MEASUREMENT,
                "icon": "mdi:battery",
                "divisor": 1,  # Direct mV (Issue #138: 4116 = 4.116 V).
                "entity_category": EntityCategory.DIAGNOSTIC,
            },
        }

        config = self._sensor_config.get(sensor_type, {})
        self._attr_translation_key = config.get("translation_key", f"chlorinator_{sensor_type}")
        self._attr_unique_id = f"fluidra_{self._device_id}_{sensor_type}"
        self._attr_native_unit_of_measurement = config.get("unit")
        self._attr_device_class = config.get("device_class")
        self._attr_state_class = config.get("state_class")
        self._attr_icon = config.get("icon")
        self._attr_entity_category = config.get("entity_category")
        self._divisor = config.get("divisor", 1)
        # Override divisor from device registry if available
        custom_divisors = DeviceIdentifier.get_feature(self.device_data, "sensor_divisors", {})
        if sensor_type in custom_divisors:
            self._divisor = custom_divisors[sensor_type]

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

        Bridged chlorinator children (`*.nn_*`) often report ``online=False``
        through their connectivity flag even when polling them succeeds, so
        gating on ``online`` makes the sensors permanently unavailable. Use
        the presence of fresh component data as the availability signal
        instead (Issue #63). Staleness while offline is instead surfaced
        through ``native_value``/``extra_state_attributes`` for the sensor
        types in ``_STALENESS_GUARDED_TYPES`` -- see ``_poll_is_trustworthy``.
        """
        return self.coordinator.last_update_success and bool(self.device_data.get("components"))

    def _current_component_ts(self) -> Any:
        """Return the resolved component's raw ``ts`` field, or None if absent.

        Not parsed as a datetime -- its exact format (epoch, ISO, ...) isn't
        documented anywhere else in this integration, and comparing it for
        equality/inequality is all ``_poll_is_trustworthy`` needs to detect
        whether the value has moved since the last poll.
        """
        components = self.device_data.get("components", {})
        component_data = components.get(str(self._resolved_component_id), {})
        return component_data.get("ts")

    def _poll_is_trustworthy(self) -> bool:
        """Return True when this poll's component data can be trusted.

        False whenever the coordinator update itself failed. When the
        device reports ``online=False``, Fluidra's cloud can keep serving a
        cached component snapshot for a disconnected device (confirmed
        2026-08-10: chlorination_actual and salinity both stayed at their
        last real values -- a direct, cache-bypassing live API call
        returned the exact same component timestamp as the coordinator's
        cached copy, 12+ hours after the chlorinator was physically powered
        off) -- but some bridged ``.nn_`` children report ``online=False``
        on *every* poll while still serving genuinely live data (Issue #63;
        reproduced against this guard by @foXaCe in PR #194's review,
        2026-08-13: a bridged child with fresh incoming data was locked
        into ``unknown`` forever, since ``online`` alone never gave it a
        chance to be trusted).

        ``online`` can't tell the two cases apart, so an offline report is
        corroborated against the resolved component's raw ``ts``: a value
        that has advanced since the last poll means the cloud handed over a
        fresh reading this cycle, not a frozen cached one, and is trusted
        over the unreliable connectivity flag. A ``ts`` that is missing, or
        unchanged from the last poll, cannot be corroborated as fresh, so
        the device is treated as genuinely offline -- including the very
        first poll ever seen for a device flagged offline, which has
        nothing yet to compare its ``ts`` against.

        The comparison itself runs once per poll, in
        ``_update_last_known_value``, and is cached in
        ``_offline_poll_confirmed_fresh`` -- read here rather than
        recomputed, so repeated reads within the same poll cycle (
        ``native_value``, ``extra_state_attributes``) always agree.
        """
        if not self.coordinator.last_update_success:
            return False
        if self.device_data.get("online") is not False:
            return True
        return self._offline_poll_confirmed_fresh

    @property
    def _resolved_component_id(self) -> int:
        """Resolve the measurement component from the CURRENT profile.

        The component is captured at creation, but a tecnoLC2 chlorinator with an
        unknown serial is first identified as the generic domoticS2 catch-all
        (pH on c172) and only re-routed to the tecnoLC2 signature profile (pH on
        c165) one poll later, once c8/c172 have been scanned — *after* its
        entities were built, and they are never rebuilt. Re-resolving here lets
        the already-created pH sensor follow the corrected mapping instead of
        reading the water temperature as pH forever (Issue #156). This mirrors
        the number setpoints, which already resolve their component per read.
        A stable profile resolves to the same component, so dedicated-profile
        devices are unaffected; the creation-time id stays as the fallback.
        """
        sensors = DeviceIdentifier.get_feature(self.device_data, "sensors", {})
        mapped = sensors.get(self._sensor_type)
        return mapped if isinstance(mapped, int) else self._component_id

    def _parsed_value(self) -> float | None:
        """Return the current raw component value divided by ``_divisor``, or None."""
        components = self.device_data.get("components", {})
        component_data = components.get(str(self._resolved_component_id), {})
        raw_value = component_data.get("reportedValue")

        if raw_value is None:
            return None

        try:
            value: float = float(raw_value) / self._divisor
            return value
        except (ValueError, TypeError):
            _LOGGER.debug("Failed to parse sensor value %s for component %s", raw_value, self._component_id)
            return None

    def _update_last_known_value(self) -> None:
        """Snapshot the last confirmed-good reading, once per trustworthy poll.

        Kept separate from ``_handle_coordinator_update`` so it can be
        exercised in tests without a real ``hass``/entity_id. Runs for
        every sensor_type -- ``last_known_at`` is exposed on all eight so a
        dashboard can show staleness uniformly (2026-08-12), even though
        only the types in ``_STALENESS_GUARDED_TYPES`` actually fall back
        to the stored value in ``native_value``; for the rest, the value
        would be the same either way (see that constant's docstring), so
        tracking just the timestamp is enough. No-op for an untrustworthy
        poll (offline, or the coordinator update itself failed) -- an
        untrustworthy poll must never refresh ``last_known_at``, or a
        frozen cached value would appear to be confirmed fresh on every
        subsequent poll while the device stays disconnected.

        Also where the ``online=False`` staleness corroboration is decided
        for this poll (see ``_poll_is_trustworthy``): captures whether the
        resolved component's ``ts`` has moved since the last poll, caches
        the verdict in ``_offline_poll_confirmed_fresh``, then advances
        ``_last_seen_component_ts`` to the ``ts`` just observed -- always,
        trustworthy or not, so a device that starts advancing its ``ts``
        again after a genuine freeze is picked up on its very next poll
        rather than staying compared against a stale baseline forever.

        A reading of exactly 0 is excluded for the types in
        ``_ZERO_IS_NOT_A_REAL_READING`` (it means "no real measurement", not
        a genuine 0 -- see ``native_value``); every other sensor_type has no
        such exclusion, since 0 is a real reading for all of them.
        """
        current_ts = self._current_component_ts()
        if (
            self.device_data.get("online") is False
            and current_ts is not None
            and self._last_seen_component_ts is not None
        ):
            self._offline_poll_confirmed_fresh = current_ts != self._last_seen_component_ts
        else:
            self._offline_poll_confirmed_fresh = False
        self._last_seen_component_ts = current_ts

        if not self._poll_is_trustworthy():
            return
        value = self._parsed_value()
        if value is None:
            return
        if value == 0 and self._sensor_type in self._ZERO_IS_NOT_A_REAL_READING:
            return
        self._last_known_value = value
        self._last_known_at = dt_util.utcnow()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator.

        Runs once per coordinator refresh (unlike ``native_value``/
        ``extra_state_attributes``, which HA may read multiple times per
        update), so this is the right place for the last-known-good
        bookkeeping rather than a side effect inside those properties.
        """
        self._update_last_known_value()
        super()._handle_coordinator_update()

    @property
    def native_value(self) -> float | None:
        """Return the sensor value."""
        if self._sensor_type in self._STALENESS_GUARDED_TYPES and not self._poll_is_trustworthy():
            # The device is offline (or the last coordinator update failed
            # outright): device_data["components"] can still hold a cached
            # reportedValue from before the disconnect, so read that as an
            # untrustworthy echo rather than a live measurement.
            return self._last_known_value

        value = self._parsed_value()
        if value is None:
            return None

        # A pH / ORP / salinity reading of exactly 0 is physically impossible for
        # a probe in a running salt pool, so it means "no live reading" rather
        # than a real value: a bare ORP register on a probe-less unit (Zodiac
        # eXO iQ LS optional Dual Link probe, Issue #111), a pH register that
        # only echoes the setpoint and clears to 0 when the dosing pump is idle,
        # or a salinity slot that reads 0 whenever chlorination production drops
        # below the ~40% threshold Fluidra documents for the conductivity probe
        # (Issue #129). ORP/pH report "unknown" outright — a real value surfaces
        # automatically if it appears. Salinity instead falls back to the last
        # real reading (see _update_last_known_value), since a dashboard
        # gauge tracking a continuously-produced measurement is more useful
        # showing a slightly stale number than going blank every time
        # production dips, and "unknown" is still correct if no real reading
        # has ever been seen (e.g. right after an HA restart).
        if value == 0 and self._sensor_type == "salinity":
            # ...but not while the device reports no flow: then the cell is not
            # full of water at all, so the held reading describes water that is
            # no longer passing the probe. Report unknown, as before #187
            # (Issue #193, @FoxP).
            if self._no_flow_reported():
                return None
            return self._last_known_value
        if value == 0 and self._sensor_type in ("orp", "ph"):
            return None

        return value

    def _no_flow_reported(self) -> bool:
        """Return True while the device reports an active no-flow alarm.

        A low-production zero and a no-flow zero look identical in the register
        but mean different things. Production dipping below the ~40% threshold
        leaves the probe sitting in the same water, so holding the last reading
        is a reasonable way to keep a dashboard gauge useful. No flow means the
        cell is not full of water — the last reading then describes water that
        is no longer there, and showing it invites the reader to trust a number
        the device is not measuring.
        """
        for alarm in self.device_data.get("alarms") or []:
            if not isinstance(alarm, dict) or not alarm.get("value"):
                continue
            if str(alarm.get("errorCode") or "").upper() == "FLOW":
                return True
        return False

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        component_id = self._resolved_component_id
        components = self.device_data.get("components", {})
        component_data = components.get(str(component_id), {})

        attributes: dict[str, Any] = {
            "component_id": component_id,
            "sensor_type": self._sensor_type,
            "raw_value": component_data.get("reportedValue"),
            "divisor": self._divisor,
            "device_id": self._device_id,
            # Not the raw `online` flag -- see _poll_is_trustworthy: a
            # bridged `.nn_` child can report online=False while still
            # serving corroborated-fresh data, and must not show as offline
            # here either (Issue #63, PR #194 review).
            "device_offline": not self._poll_is_trustworthy(),
            "last_known_at": self._last_known_at.isoformat() if self._last_known_at else None,
        }

        if self._sensor_type in self._STALENESS_GUARDED_TYPES:
            attributes["last_known_value"] = self._last_known_value

        if self._sensor_type == "salinity":
            attributes["low_production"] = self._parsed_value() == 0
            attributes["no_flow"] = self._no_flow_reported()

        return attributes
