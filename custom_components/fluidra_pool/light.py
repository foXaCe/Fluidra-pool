"""Light platform for Fluidra Pool integration (LumiPlus Connect)."""

import logging
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_RGBW_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import FluidraDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

# LumiPlus Connect component IDs (discovered via mitmproxy)
COMPONENT_POWER = 11  # ON/OFF: "1" = ON, "0" = OFF
COMPONENT_BRIGHTNESS = 17  # Brightness: 0-100
COMPONENT_COLOR = 45  # RGBW color: {r, g, b, k, extra: {w}}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Fluidra Pool light entities."""
    coordinator: FluidraDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = []

    # Wait for first refresh if data not available
    if not coordinator.data:
        await coordinator.async_config_entry_first_refresh()

    if coordinator.data:
        for _pool_id, pool in coordinator.data.items():
            for device in pool.get("devices", []):
                device_type = device.get("type", "")
                family = device.get("family", "").lower()

                # Detect LumiPlus Connect and other light controllers
                if device_type == "light" or "light" in family:
                    entities.append(
                        FluidraLight(
                            coordinator,
                            pool.get("id"),
                            device.get("device_id"),
                            device.get("name", "Pool Light"),
                            device,
                        )
                    )

    async_add_entities(entities)


class FluidraLight(CoordinatorEntity, LightEntity):
    """Representation of a Fluidra LumiPlus Connect light."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        pool_id: str,
        device_id: str,
        name: str,
        device_data: dict,
    ) -> None:
        """Initialize the light."""
        super().__init__(coordinator)
        self._pool_id = pool_id
        self._device_id = device_id
        self._attr_name = name
        self._attr_unique_id = f"{device_id}_light"
        self._device_data = device_data

        # RGBW support
        self._attr_color_mode = ColorMode.RGBW
        self._attr_supported_color_modes = {ColorMode.RGBW}

        # State cache
        self._is_on = False
        self._brightness = 255
        self._rgbw_color = (255, 255, 255, 255)  # Default white

        # Optimistic state to prevent coordinator from overwriting during command
        self._optimistic_state = None  # None, True, or False

    @property
    def device_info(self) -> dict[str, Any]:
        """Return device info."""
        return {
            "identifiers": {(DOMAIN, self._device_id)},
            "name": self._attr_name,
            "manufacturer": "Fluidra",
            "model": self._device_data.get("model", "LumiPlus Connect"),
        }

    @property
    def is_on(self) -> bool:
        """Return true if light is on."""
        # Use optimistic state if set
        if self._optimistic_state is not None:
            return self._optimistic_state
        return self._is_on

    @property
    def brightness(self) -> int | None:
        """Return the brightness of the light (0-255)."""
        return self._brightness

    @property
    def rgbw_color(self) -> tuple[int, int, int, int] | None:
        """Return the RGBW color value."""
        return self._rgbw_color

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if not self.coordinator.data:
            return

        for pool_id, pool in self.coordinator.data.items():
            if pool_id != self._pool_id:
                continue

            for device in pool.get("devices", []):
                if device.get("device_id") != self._device_id:
                    continue

                components = device.get("components", {})

                # Power state (component 11)
                power_comp = components.get(str(COMPONENT_POWER), {})
                reported_power = power_comp.get("reportedValue")
                if reported_power is not None:
                    self._is_on = bool(int(reported_power))

                # Brightness (component 17) - 0-100, convert to 0-255
                brightness_comp = components.get(str(COMPONENT_BRIGHTNESS), {})
                reported_brightness = brightness_comp.get("reportedValue")
                if reported_brightness is not None:
                    self._brightness = int(reported_brightness * 255 / 100)

                # Color (component 45)
                color_comp = components.get(str(COMPONENT_COLOR), {})
                reported_color = color_comp.get("reportedValue")
                if reported_color and isinstance(reported_color, dict):
                    r = reported_color.get("r", 0)
                    g = reported_color.get("g", 0)
                    b = reported_color.get("b", 0)
                    w = reported_color.get("extra", {}).get("w", 0)
                    self._rgbw_color = (r, g, b, w)

                break

        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the light."""
        import asyncio

        # Set optimistic state immediately
        self._optimistic_state = True
        self.async_write_ha_state()

        # Handle brightness
        if ATTR_BRIGHTNESS in kwargs:
            brightness_255 = kwargs[ATTR_BRIGHTNESS]
            brightness_100 = int(brightness_255 * 100 / 255)
            await self.coordinator.api.set_component_value(self._device_id, COMPONENT_BRIGHTNESS, brightness_100)
            self._brightness = brightness_255

        # Handle RGBW color
        if ATTR_RGBW_COLOR in kwargs:
            r, g, b, w = kwargs[ATTR_RGBW_COLOR]
            color_value = {
                "r": r,
                "g": g,
                "b": b,
                "k": 5000,  # Default color temperature
                "extra": {"w": w},
            }
            await self.coordinator.api.set_component_json_value(self._device_id, COMPONENT_COLOR, color_value)
            self._rgbw_color = (r, g, b, w)

        # Turn on power
        await self.coordinator.api.set_component_string_value(self._device_id, COMPONENT_POWER, "1")
        self._is_on = True

        # Wait for device to process, then clear optimistic state
        await asyncio.sleep(5)
        self._optimistic_state = None
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the light."""
        import asyncio

        # Set optimistic state immediately
        self._optimistic_state = False
        self.async_write_ha_state()

        await self.coordinator.api.set_component_string_value(self._device_id, COMPONENT_POWER, "0")
        self._is_on = False

        # Wait for device to process, then clear optimistic state
        await asyncio.sleep(5)
        self._optimistic_state = None
        self.async_write_ha_state()
