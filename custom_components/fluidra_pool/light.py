"""Light platform for Fluidra Pool integration (LumiPlus Connect)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import aiohttp
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_RGBW_COLOR,
    LightEntity,
)
from homeassistant.components.light.const import ColorMode
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api_resilience import FluidraError
from .const import (
    DEVICE_TYPE_LIGHT,
    DOMAIN,
    LUMIPLUS_COMPONENT_BRIGHTNESS,
    LUMIPLUS_COMPONENT_COLOR,
    LUMIPLUS_COMPONENT_POWER,
    FluidraPoolConfigEntry,
)
from .entity import FluidraPoolControlEntity

if TYPE_CHECKING:
    from .coordinator import FluidraDataUpdateCoordinator
    from .fluidra_api import FluidraPoolAPI

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FluidraPoolConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Fluidra Pool light entities, including devices added later."""
    coordinator = entry.runtime_data.coordinator

    if not coordinator.data:
        await coordinator.async_config_entry_first_refresh()

    known_devices: set[str] = set()

    @callback
    def _add_entities(pools: list[dict[str, Any]]) -> None:
        """Create light entities for any device not seen yet (dynamic-devices)."""
        entities: list[FluidraLight] = []

        for pool in pools:
            pool_id = pool["id"]

            for device in pool.get("devices", []):
                device_id = device.get("device_id")
                if not device_id:
                    continue

                key = f"{pool_id}_{device_id}"
                if key in known_devices:
                    continue
                known_devices.add(key)

                device_type = device.get("type", "")
                family = device.get("family", "").lower()

                if device_type == DEVICE_TYPE_LIGHT or DEVICE_TYPE_LIGHT in family:
                    entities.append(FluidraLight(coordinator, coordinator.api, pool_id, device_id))

        if entities:
            async_add_entities(entities)

    # Initial setup from the cached discovery (consistent with the other platforms).
    pools = coordinator.api.cached_pools or await coordinator.api.get_pools()
    _add_entities(pools)

    # Add entities for devices that appear on later polls, without a reload.
    @callback
    def _on_coordinator_update() -> None:
        _add_entities(coordinator.get_pools_from_data())

    entry.async_on_unload(coordinator.async_add_listener(_on_coordinator_update))


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

    def __init__(
        self,
        coordinator: FluidraDataUpdateCoordinator,
        api: FluidraPoolAPI,
        pool_id: str,
        device_id: str,
    ) -> None:
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
        # Aggregate every sub-command's result: a False from brightness or colour
        # must fail (and roll back) just like a failed power command, otherwise a
        # silent partial failure leaves the optimistic UI permanently out of sync.
        success = True

        try:
            if ATTR_BRIGHTNESS in kwargs:
                brightness_255 = int(kwargs[ATTR_BRIGHTNESS])
                brightness_100 = round(brightness_255 * 100 / 255)
                self._optimistic_brightness = brightness_255
                success = (
                    await self._api.set_component_value(self._device_id, LUMIPLUS_COMPONENT_BRIGHTNESS, brightness_100)
                    and success
                )

            if ATTR_RGBW_COLOR in kwargs:
                r, g, b, w = kwargs[ATTR_RGBW_COLOR]
                color_value = {"r": int(r), "g": int(g), "b": int(b), "k": 5000, "extra": {"w": int(w)}}
                self._optimistic_rgbw = (int(r), int(g), int(b), int(w))
                success = (
                    await self._api.set_component_json_value(self._device_id, LUMIPLUS_COMPONENT_COLOR, color_value)
                    and success
                )

            success = (
                await self._api.set_component_string_value(self._device_id, LUMIPLUS_COMPONENT_POWER, "1") and success
            )
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
