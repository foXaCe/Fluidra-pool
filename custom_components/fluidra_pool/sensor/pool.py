"""Pool-level sensors (weather, status, location, water quality)."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import UnitOfTemperature

from .base import FluidraPoolSensorBase


class FluidraPoolWeatherSensor(FluidraPoolSensorBase):
    """Sensor for weather temperature at pool location."""

    def __init__(self, coordinator, api, pool_id: str):
        """Initialize the pool weather sensor."""
        super().__init__(coordinator, api, pool_id, "weather")
        self._attr_translation_key = "weather_temperature"

    @property
    def native_value(self) -> float | None:
        """Return the weather temperature."""
        pool_data = self.pool_data

        status_data = pool_data.get("status_data", {})
        weather = status_data.get("weather", {})
        if weather.get("status") == "ok":
            weather_value = weather.get("value")
            if weather_value is not None and isinstance(weather_value, dict):
                current = weather_value.get("current", {})
                if isinstance(current, dict) and "main" in current and "temp" in current["main"]:
                    # Convert Kelvin → Celsius (rounded to 1 decimal).
                    temp_kelvin = current["main"]["temp"]
                    return round(temp_kelvin - 273.15, 1)

        return None

    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit of measurement."""
        return UnitOfTemperature.CELSIUS

    @property
    def device_class(self) -> SensorDeviceClass:
        """Return the device class."""
        return SensorDeviceClass.TEMPERATURE

    @property
    def state_class(self) -> SensorStateClass:
        """Return the state class."""
        return SensorStateClass.MEASUREMENT

    @property
    def icon(self) -> str:
        """Return the icon of the sensor."""
        return "mdi:thermometer"


class FluidraPoolStatusSensor(FluidraPoolSensorBase):
    """Sensor for overall pool status."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["using", "maintenance", "offline", "winterized", "connected", "unknown_state"]

    def __init__(self, coordinator, api, pool_id: str):
        """Initialize the pool status sensor."""
        super().__init__(coordinator, api, pool_id, "status")
        self._attr_translation_key = "pool_status"

    @property
    def native_value(self) -> str:
        """Return the pool status."""
        pool_data = self.pool_data

        state = pool_data.get("state", "unknown")

        if state == "using":
            return "using"
        if state == "maintenance":
            return "maintenance"
        if state == "offline":
            return "offline"
        if state == "winterized":
            return "winterized"
        if pool_data.get("name"):
            return "connected"
        return "unknown_state"

    @property
    def icon(self) -> str:
        """Return the icon of the sensor."""
        pool_data = self.pool_data
        state = pool_data.get("state", "unknown")

        if state == "using":
            return "mdi:pool"
        if state == "maintenance":
            return "mdi:tools"
        if state == "offline":
            return "mdi:pool-off"
        if state == "winterized":
            return "mdi:snowflake"
        return "mdi:help-circle"

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional state attributes."""
        pool_data = self.pool_data
        attrs = {}

        attrs["pool_state"] = pool_data.get("state", "unknown")
        if "owner" in pool_data:
            attrs["owner_id"] = pool_data["owner"]

        characteristics = pool_data.get("characteristics", {})
        if characteristics:
            attrs["shape"] = characteristics.get("shape")
            attrs["construction_year"] = characteristics.get("constructionYear")
            attrs["waterproof"] = characteristics.get("waterproof")
            attrs["ground"] = characteristics.get("ground")
            attrs["place"] = characteristics.get("place")
            attrs["pool_type"] = characteristics.get("type")

            dimensions = characteristics.get("dimensions", {})
            if "volume" in dimensions:
                attrs["volume_m3"] = dimensions["volume"]

        disinfection = pool_data.get("disinfection", {})
        if disinfection:
            method = disinfection.get("method", {})
            attrs["disinfection_type"] = method.get("type")
            attrs["disinfection_method"] = method.get("name")
            attrs["automatic_disinfection"] = disinfection.get("automatic", False)

        devices = pool_data.get("devices", [])
        attrs["total_devices"] = len(devices)

        device_types: dict[str, int] = {}
        for device in devices:
            device_type = device.get("type", "unknown")
            device_types[device_type] = device_types.get(device_type, 0) + 1
        attrs["device_types"] = device_types

        status_data = pool_data.get("status_data", {})
        weather = status_data.get("weather", {})
        if weather.get("status") == "ok":
            weather_value = weather.get("value")
            if weather_value is not None:
                current = weather_value.get("current", {})
                if current:
                    attrs["weather_available"] = True
                    main = current.get("main")
                    if isinstance(main, dict):
                        temp_kelvin = main.get("temp")
                        if temp_kelvin is not None:
                            attrs["air_temperature"] = round(temp_kelvin - 273.15, 1)
                        attrs["humidity"] = main.get("humidity")
                        attrs["pressure"] = main.get("pressure")
                    wind = current.get("wind")
                    if isinstance(wind, dict):
                        attrs["wind_speed"] = wind.get("speed")

        return attrs


class FluidraPoolLocationSensor(FluidraPoolSensorBase):
    """Sensor for pool location and geographic information."""

    def __init__(self, coordinator, api, pool_id: str):
        """Initialize the pool location sensor."""
        super().__init__(coordinator, api, pool_id, "location")
        self._attr_translation_key = "pool_location"

    @property
    def native_value(self) -> str:
        """Return the pool location."""
        pool_data = self.pool_data

        geolocation = pool_data.get("geolocation", {})
        if geolocation:
            locality = geolocation.get("locality")
            country_code = geolocation.get("countryCode")

            if locality and country_code:
                return f"{locality}, {country_code}"
            if locality:
                return locality
            if country_code:
                return country_code

        return "Unknown"

    @property
    def icon(self) -> str:
        """Return the icon of the sensor."""
        return "mdi:map-marker"

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional state attributes."""
        pool_data = self.pool_data
        attrs = {}

        geolocation = pool_data.get("geolocation", {})
        if geolocation:
            attrs["latitude"] = geolocation.get("latitude")
            attrs["longitude"] = geolocation.get("longitude")
            attrs["locality"] = geolocation.get("locality")
            attrs["country_code"] = geolocation.get("countryCode")

        status_data = pool_data.get("status_data", {})
        weather = status_data.get("weather", {})
        if weather.get("status") == "ok":
            weather_value = weather.get("value")
            if weather_value is not None:
                current = weather_value.get("current", {})
                if current:
                    attrs["weather_country"] = current.get("sys", {}).get("country")
                    attrs["timezone"] = weather_value.get("current", {}).get("timezone")

        return attrs


class FluidraPoolWaterQualitySensor(FluidraPoolSensorBase):
    """Sensor for pool water quality information."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["auto", "manual", "not_configured"]

    def __init__(self, coordinator, api, pool_id: str):
        """Initialize the pool water quality sensor."""
        super().__init__(coordinator, api, pool_id, "water_quality")
        self._attr_translation_key = "water_quality"

    @property
    def native_value(self) -> str:
        """Return the water quality status."""
        pool_data = self.pool_data

        disinfection = pool_data.get("disinfection", {})
        if disinfection:
            automatic = disinfection.get("automatic", False)

            if automatic:
                return "auto"
            return "manual"

        return "not_configured"

    @property
    def icon(self) -> str:
        """Return the icon of the sensor."""
        return "mdi:water-check"

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional state attributes."""
        pool_data = self.pool_data
        attrs = {}

        disinfection = pool_data.get("disinfection", {})
        if disinfection:
            method = disinfection.get("method", {})
            attrs["disinfection_type"] = method.get("type")
            attrs["disinfection_method"] = method.get("name")
            attrs["automatic_disinfection"] = disinfection.get("automatic", False)

        water_quality_ranges = pool_data.get("waterQualitySensorRanges", {})
        if water_quality_ranges:
            ph_data = water_quality_ranges.get("ph", {})
            if ph_data:
                attrs["ph_min"] = ph_data.get("minValue")
                attrs["ph_max"] = ph_data.get("maxValue")
                attrs["ph_unit"] = ph_data.get("unit")

            chlorine_data = water_quality_ranges.get("chlorine", {})
            if chlorine_data:
                attrs["chlorine_min"] = chlorine_data.get("minValue")
                attrs["chlorine_max"] = chlorine_data.get("maxValue")
                attrs["chlorine_unit"] = chlorine_data.get("unit")

            salinity_data = water_quality_ranges.get("salinity", {})
            if salinity_data:
                attrs["salinity_min"] = salinity_data.get("minValue")
                attrs["salinity_max"] = salinity_data.get("maxValue")
                attrs["salinity_unit"] = salinity_data.get("unit")

            orp_data = water_quality_ranges.get("orp", {})
            if orp_data:
                attrs["orp_min"] = orp_data.get("minValue")
                attrs["orp_max"] = orp_data.get("maxValue")
                attrs["orp_unit"] = orp_data.get("unit")

        water_quality = pool_data.get("water_quality", {})
        if water_quality:
            attrs["current_water_quality"] = water_quality

        characteristics = pool_data.get("characteristics", {})
        if characteristics:
            dimensions = characteristics.get("dimensions", {})
            if "volume" in dimensions:
                attrs["pool_volume_m3"] = dimensions["volume"]

            attrs["pool_type"] = characteristics.get("type")
            attrs["waterproof"] = characteristics.get("waterproof")

        return attrs
