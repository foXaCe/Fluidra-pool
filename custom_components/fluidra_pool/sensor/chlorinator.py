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

        # Last real (non-zero) salinity reading, held while the probe can't
        # measure due to low production (see native_value). In-memory only —
        # reset to None on every integration reload/HA restart until the next
        # real reading, same lifetime as the alarm binary sensor's
        # last-known-* pair. Unused by every other sensor_type.
        self._last_known_value: float | None = None
        self._last_known_at: datetime | None = None

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
        instead (Issue #63).
        """
        return self.coordinator.last_update_success and bool(self.device_data.get("components"))

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

    def _update_last_known_salinity(self) -> None:
        """Snapshot the last real (non-zero) salinity reading, once per poll.

        Kept separate from ``_handle_coordinator_update`` so it can be
        exercised in tests without a real ``hass``/entity_id. No-op for
        every sensor_type other than salinity.
        """
        if self._sensor_type != "salinity":
            return
        value = self._parsed_value()
        if value is not None and value != 0:
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
        self._update_last_known_salinity()
        super()._handle_coordinator_update()

    @property
    def native_value(self) -> float | None:
        """Return the sensor value."""
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
        # real reading (see _update_last_known_salinity), since a dashboard
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
        }

        if self._sensor_type == "salinity":
            attributes["last_known_value"] = self._last_known_value
            attributes["last_known_at"] = self._last_known_at.isoformat() if self._last_known_at else None
            attributes["low_production"] = self._parsed_value() == 0
            attributes["no_flow"] = self._no_flow_reported()

        return attributes
