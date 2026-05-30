"""Light platform for Fluidra Pool integration (LumiPlus Connect)."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_RGBW_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api_resilience import FluidraError
from .const import (
    DOMAIN,
    LUMIPLUS_COMPONENT_BRIGHTNESS,
    LUMIPLUS_COMPONENT_COLOR,
    LUMIPLUS_COMPONENT_POWER,
    FluidraPoolConfigEntry,
)
from .entity import FluidraPoolControlEntity

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FluidraPoolConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Fluidra Pool light entities."""
    coordinator = entry.runtime_data.coordinator

    if not coordinator.data:
        await coordinator.async_config_entry_first_refresh()

    entities: list[FluidraLight] = []
    if coordinator.data:
        for pool_id, pool in coordinator.data.items():
            for device in pool.get("devices", []):
                device_type = device.get("type", "")
                family = device.get("family", "").lower()

                if device_type == "light" or "light" in family:
                    device_id = device.get("device_id")
                    if not device_id:
                        continue
                    entities.append(FluidraLight(coordinator, coordinator.api, pool_id, device_id))

    async_add_entities(entities)


class FluidraLight(FluidraPoolControlEntity, LightEntity):
    """Representation of a Fluidra LumiPlus Connect light."""

    __slots__ = (
        "_optimistic_brightness",
        "_optimistic_is_on",
        "_optimistic_rgbw",
    )

    _attr_translation_key = "pool_light"
    _attr_color_mode = ColorMode.RGBW
    _attr_supported_color_modes = {ColorMode.RGBW}

    def __init__(self, coordinator, api, pool_id: str, device_id: str) -> None:
        """Initialize the light."""
        super().__init__(coordinator, api, pool_id, device_id)
        self._attr_unique_id = f"{DOMAIN}_{pool_id}_{device_id}_light"
        self._optimistic_is_on: bool | None = None
        self._optimistic_brightness: int | None = None
        self._optimistic_rgbw: tuple[int, int, int, int] | None = None

    def _get_component(self, component_id: int) -> dict[str, Any]:
        """Return raw component dict from coordinator data."""
        components = self.device_data.get("components", {})
        value = components.get(str(component_id))
        return value if isinstance(value, dict) else {}

    def _clear_optimistic(self) -> None:
        """Reset all optimistic overrides (used when a command fails)."""
        self._optimistic_is_on = None
        self._optimistic_brightness = None
        self._optimistic_rgbw = None

    @property
    def is_on(self) -> bool:
        """Return true if the light is currently on."""
        if self._optimistic_is_on is not None:
            return self._optimistic_is_on
        reported = self._get_component(LUMIPLUS_COMPONENT_POWER).get("reportedValue")
        if reported is None:
            return False
        try:
            return bool(int(reported))
        except (TypeError, ValueError):
            return False

    @property
    def brightness(self) -> int | None:
        """Return the brightness of the light on a 0-255 scale."""
        if self._optimistic_brightness is not None:
            return self._optimistic_brightness
        reported = self._get_component(LUMIPLUS_COMPONENT_BRIGHTNESS).get("reportedValue")
        if reported is None:
            return None
        try:
            return round(float(reported) * 255 / 100)
        except (TypeError, ValueError):
            return None

    @property
    def rgbw_color(self) -> tuple[int, int, int, int] | None:
        """Return the RGBW color as a tuple."""
        if self._optimistic_rgbw is not None:
            return self._optimistic_rgbw
        reported = self._get_component(LUMIPLUS_COMPONENT_COLOR).get("reportedValue")
        if not isinstance(reported, dict):
            return None
        try:
            r = int(reported.get("r", 0))
            g = int(reported.get("g", 0))
            b = int(reported.get("b", 0))
            w = int(reported.get("extra", {}).get("w", 0))
        except (TypeError, ValueError):
            return None
        return (r, g, b, w)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Drop optimistic overrides once the backend confirms the new state."""
        reported_power = self._get_component(LUMIPLUS_COMPONENT_POWER).get("reportedValue")
        if reported_power is not None and self._optimistic_is_on is not None:
            try:
                if bool(int(reported_power)) == self._optimistic_is_on:
                    self._optimistic_is_on = None
            except (TypeError, ValueError):
                pass

        # Brightness: compare on the 0-100 wire scale (the device reports 0-100)
        # to avoid the 255<->100 rounding mismatch, and only clear once confirmed.
        if self._optimistic_brightness is not None:
            reported_b = self._get_component(LUMIPLUS_COMPONENT_BRIGHTNESS).get("reportedValue")
            if reported_b is not None:
                try:
                    if round(float(reported_b)) == round(self._optimistic_brightness * 100 / 255):
                        self._optimistic_brightness = None
                except (TypeError, ValueError):
                    pass

        # RGBW: exact tuple match (no scaling involved).
        if self._optimistic_rgbw is not None:
            reported_c = self._get_component(LUMIPLUS_COMPONENT_COLOR).get("reportedValue")
            if isinstance(reported_c, dict):
                try:
                    r = int(reported_c.get("r", 0))
                    g = int(reported_c.get("g", 0))
                    b = int(reported_c.get("b", 0))
                    w = int(reported_c.get("extra", {}).get("w", 0))
                    if (r, g, b, w) == self._optimistic_rgbw:
                        self._optimistic_rgbw = None
                except (TypeError, ValueError):
                    pass

        super()._handle_coordinator_update()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on, optionally setting brightness/colour."""
        self._optimistic_is_on = True

        try:
            if ATTR_BRIGHTNESS in kwargs:
                brightness_255 = int(kwargs[ATTR_BRIGHTNESS])
                brightness_100 = round(brightness_255 * 100 / 255)
                self._optimistic_brightness = brightness_255
                await self._api.set_component_value(self._device_id, LUMIPLUS_COMPONENT_BRIGHTNESS, brightness_100)

            if ATTR_RGBW_COLOR in kwargs:
                r, g, b, w = kwargs[ATTR_RGBW_COLOR]
                color_value = {"r": int(r), "g": int(g), "b": int(b), "k": 5000, "extra": {"w": int(w)}}
                self._optimistic_rgbw = (int(r), int(g), int(b), int(w))
                await self._api.set_component_json_value(self._device_id, LUMIPLUS_COMPONENT_COLOR, color_value)

            success = await self._api.set_component_string_value(self._device_id, LUMIPLUS_COMPONENT_POWER, "1")
        except (aiohttp.ClientError, TimeoutError, FluidraError) as err:
            # Any failed sub-command (brightness/colour/power) must roll back the
            # optimistic overrides so the UI doesn't get stuck on an unconfirmed state.
            self._clear_optimistic()
            self.async_write_ha_state()
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="light_set_failed") from err

        if not success:
            self._clear_optimistic()
            self.async_write_ha_state()
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="light_set_failed")
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        self._optimistic_is_on = False
        try:
            success = await self._api.set_component_string_value(self._device_id, LUMIPLUS_COMPONENT_POWER, "0")
        except (aiohttp.ClientError, TimeoutError, FluidraError) as err:
            self._optimistic_is_on = None
            self.async_write_ha_state()
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="light_set_failed") from err
        if not success:
            self._optimistic_is_on = None
            self.async_write_ha_state()
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="light_set_failed")
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()
