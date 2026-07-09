"""Per-family behavior objects for FluidraHeatPumpClimate.

One heat-pump family = one command set = one behavior class. The entity keeps
the family-agnostic scaffolding (optimistic state, error handling, HA
plumbing) and delegates every family-specific decision here. Behaviors are
stateless singletons: they receive the entity's ``device_data`` (and, for
writes, the api/device ids) on every call — statelessness is what allows
re-resolving the family on each access when identification firms up after
the first poll.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from homeassistant.components.climate.const import HVACAction, HVACMode

from .const import (
    LG_MODE_TO_VALUE,
    LG_PRESET_SMART_COOLING,
    LG_PRESET_SMART_HEAT_COOL,
    LG_PRESET_SMART_HEATING,
    Z260_MAX_TEMP,
    Z260_MIN_TEMP,
    Z260_TEMP_STEP,
    Z550_MAX_TEMP,
    Z550_MIN_TEMP,
    Z550_MODE_AUTO,
    Z550_MODE_COOLING,
    Z550_MODE_HEATING,
    Z550_STATE_COOLING,
    Z550_STATE_HEATING,
    Z550_STATE_IDLE,
    Z550_STATE_NO_FLOW,
    Z550_TEMP_STEP,
)
from .device_registry import DeviceIdentifier

if TYPE_CHECKING:
    from .fluidra_api import FluidraPoolAPI

# Supplied by the entity: reads current_temperature/target_temperature
# (which include optimistic state), so the heuristic itself must stay on the
# entity — behaviors only get to call it.
InferHeatCoolAction = Callable[[], HVACAction]


class HeatPumpBehavior:
    """Command set for one heat-pump family.

    Also serves as the "standard" fallback implementation (heat_pump_reported
    -> is_heating -> is_running reads, start_pump/stop_pump writes): a family
    that behaves exactly like the fallback simply doesn't override anything.
    Plain base class (not ABC) — every method already has a concrete default
    body, so there is nothing to declare abstract (ruff B024).
    """

    hvac_modes: list[HVACMode] = [HVACMode.OFF, HVACMode.HEAT]
    min_temp: float = 10.0
    max_temp: float = 40.0
    temp_step: float = 1.0

    def hvac_mode(self, device_data: dict[str, Any]) -> HVACMode:
        """Return current hvac operation mode (standard heat pump logic).

        1. Priority: heat_pump_reported (specific heat pump state)
        2. Fallback: is_heating (compatibility)
        3. Fallback: is_running (état général)
        """
        heat_pump_reported = device_data.get("heat_pump_reported")
        if heat_pump_reported is not None:
            if heat_pump_reported:
                return HVACMode.HEAT
            return HVACMode.OFF

        if device_data.get("is_heating", False):
            return HVACMode.HEAT

        if device_data.get("is_running", False):
            return HVACMode.HEAT

        return HVACMode.OFF

    def hvac_action(self, device_data: dict[str, Any], infer_heat_cool: InferHeatCoolAction) -> HVACAction:
        """Return the current hvac action (standard heat pump logic)."""
        if device_data.get("is_heating", False):
            return HVACAction.HEATING
        return HVACAction.OFF

    async def async_set_hvac_mode(
        self,
        api: FluidraPoolAPI,
        pool_id: str,
        device_id: str,
        hvac_mode: HVACMode,
        current_preset: str | None,
    ) -> bool | None:
        """Write the new hvac mode (standard heat pump: start/stop pump).

        Returns ``None`` for a mode this family doesn't support: the caller
        clears the optimistic state silently, without raising or refreshing —
        mirroring the pre-refactor early ``return`` in that branch.
        """
        if hvac_mode == HVACMode.HEAT:
            return await api.start_pump(device_id)
        if hvac_mode == HVACMode.OFF:
            return await api.stop_pump(device_id)
        return None


class StandardBehavior(HeatPumpBehavior):
    """Standard heat pump (LG detected as a generic pump, unknown families).

    No overrides: this is exactly the fallback implementation carried by
    ``HeatPumpBehavior``. Kept as an explicit class (instead of using the
    base directly) so ``resolve_behavior`` and callers have a name for it.
    """


class Z550Behavior(HeatPumpBehavior):
    """Z550iQ+ command set: components 21 (on/off), 16 (mode), 61 (state)."""

    hvac_modes: list[HVACMode] = [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL, HVACMode.HEAT_COOL]
    min_temp: float = Z550_MIN_TEMP
    max_temp: float = Z550_MAX_TEMP
    temp_step: float = Z550_TEMP_STEP

    def hvac_mode(self, device_data: dict[str, Any]) -> HVACMode:
        """Z550iQ+ specific mode handling."""
        # Check if heat pump is ON (component 21)
        heat_pump_reported = device_data.get("heat_pump_reported")
        if not heat_pump_reported:
            return HVACMode.OFF

        # Get mode from component 16: 0=heating, 1=cooling, 2=auto
        z550_mode = device_data.get("z550_mode_reported")
        if z550_mode == Z550_MODE_HEATING:
            return HVACMode.HEAT
        if z550_mode == Z550_MODE_COOLING:
            return HVACMode.COOL
        if z550_mode == Z550_MODE_AUTO:
            return HVACMode.HEAT_COOL
        # Default to HEAT if mode unknown but pump is ON
        return HVACMode.HEAT

    def hvac_action(self, device_data: dict[str, Any], infer_heat_cool: InferHeatCoolAction) -> HVACAction:
        """Z550iQ+ uses component 61 for detailed state."""
        z550_state = device_data.get("z550_state_reported")
        if z550_state == Z550_STATE_HEATING:
            return HVACAction.HEATING
        if z550_state == Z550_STATE_COOLING:
            return HVACAction.COOLING
        # No flow: the unit is powered but circulation is blocked (typically an
        # external pump is off). Report IDLE — like the Z260 no-flow alarm — so
        # it doesn't read as switched off (Issue #88).
        if z550_state == Z550_STATE_NO_FLOW:
            return HVACAction.IDLE
        if z550_state == Z550_STATE_IDLE:
            return HVACAction.IDLE
        return HVACAction.OFF

    async def async_set_hvac_mode(
        self,
        api: FluidraPoolAPI,
        pool_id: str,
        device_id: str,
        hvac_mode: HVACMode,
        current_preset: str | None,
    ) -> bool | None:
        """Z550iQ+ specific mode handling."""
        if hvac_mode == HVACMode.OFF:
            # Turn OFF: component 21 = 0
            return await api.control_device_component(device_id, 21, 0)

        # Turn ON first: component 21 = 1
        success = await api.control_device_component(device_id, 21, 1)
        if success:
            # Set mode: component 16 (0=heating, 1=cooling, 2=auto)
            mode_value: int | None = None
            if hvac_mode == HVACMode.HEAT:
                mode_value = Z550_MODE_HEATING
            elif hvac_mode == HVACMode.COOL:
                mode_value = Z550_MODE_COOLING
            elif hvac_mode == HVACMode.HEAT_COOL:
                mode_value = Z550_MODE_AUTO
            if mode_value is not None:
                success = await api.control_device_component(device_id, 16, mode_value)
                if not success:
                    # Power was just turned on but the mode write failed:
                    # roll power back off so the physical device matches the
                    # reverted optimistic UI instead of running in a stale mode.
                    await api.control_device_component(device_id, 21, 0)
        return success


class Z260iqBehavior(HeatPumpBehavior):
    """Z260iQ/Z250iQ command set: components 13 (on/off), 14 (mode/preset), 28 (no-flow), 67 (air temp)."""

    hvac_modes: list[HVACMode] = [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL, HVACMode.HEAT_COOL]
    min_temp: float = Z260_MIN_TEMP
    max_temp: float = Z260_MAX_TEMP
    temp_step: float = Z260_TEMP_STEP

    def hvac_mode(self, device_data: dict[str, Any]) -> HVACMode:
        """Z260iQ: ON/OFF from component 13, mode from component 14."""
        heat_pump_reported = device_data.get("heat_pump_reported")
        if heat_pump_reported is not None and not bool(heat_pump_reported):
            return HVACMode.OFF
        mode_value = device_data.get("z260iq_mode_value")
        if mode_value is not None:
            # 0=Smart Heat, 3=Boost Heat, 4=Silence Heat → HEAT
            # 1=Smart Cool, 5=Boost Cool, 6=Silence Cool → COOL
            # 2=Smart H+C → HEAT_COOL
            if mode_value in (0, 3, 4):
                return HVACMode.HEAT
            if mode_value in (1, 5, 6):
                return HVACMode.COOL
            if mode_value == 2:
                return HVACMode.HEAT_COOL
        # Fallback: if ON, assume HEAT
        return HVACMode.HEAT if bool(heat_pump_reported) else HVACMode.OFF

    def hvac_action(self, device_data: dict[str, Any], infer_heat_cool: InferHeatCoolAction) -> HVACAction:
        """Derive the action from ON/OFF + mode direction (component 14).

        Not is_heating — otherwise an actively-cooling unit reports HEATING.
        """
        heat_pump_reported = device_data.get("heat_pump_reported")
        if heat_pump_reported is not None and not bool(heat_pump_reported):
            return HVACAction.OFF
        if device_data.get("no_flow_alarm"):
            return HVACAction.IDLE
        mode_value = device_data.get("z260iq_mode_value")
        if mode_value in (1, 5, 6):  # Smart/Boost/Silence Cool
            return HVACAction.COOLING
        if mode_value in (0, 3, 4):  # Smart/Boost/Silence Heat
            return HVACAction.HEATING
        if mode_value == 2:  # Smart Heat+Cool: infer direction from temps
            return infer_heat_cool()
        return HVACAction.HEATING if bool(heat_pump_reported) else HVACAction.OFF

    async def async_set_hvac_mode(
        self,
        api: FluidraPoolAPI,
        pool_id: str,
        device_id: str,
        hvac_mode: HVACMode,
        current_preset: str | None,
    ) -> bool | None:
        """Z260iQ: ON/OFF via component 13, mode via component 14."""
        if hvac_mode == HVACMode.OFF:
            return await api.control_device_component(device_id, 13, 0)

        # Determine component 14 value from target HVAC mode:
        # Use the current preset to preserve the specific mode, or default to Smart variant
        if hvac_mode == HVACMode.HEAT:
            # Keep current preset if it's already a HEAT preset, else default to Smart Heat
            mode_value = LG_MODE_TO_VALUE.get(
                current_preset or LG_PRESET_SMART_HEATING, LG_MODE_TO_VALUE[LG_PRESET_SMART_HEATING]
            )
            if mode_value not in (0, 3, 4):
                mode_value = LG_MODE_TO_VALUE[LG_PRESET_SMART_HEATING]
        elif hvac_mode == HVACMode.COOL:
            mode_value = LG_MODE_TO_VALUE.get(
                current_preset or LG_PRESET_SMART_COOLING, LG_MODE_TO_VALUE[LG_PRESET_SMART_COOLING]
            )
            if mode_value not in (1, 5, 6):
                mode_value = LG_MODE_TO_VALUE[LG_PRESET_SMART_COOLING]
        elif hvac_mode == HVACMode.HEAT_COOL:
            mode_value = LG_MODE_TO_VALUE[LG_PRESET_SMART_HEAT_COOL]
        else:
            return None

        success = await api.control_device_component(device_id, 14, mode_value)
        if success:
            success = await api.control_device_component(device_id, 13, 1)
        return success


class LgBehavior(HeatPumpBehavior):
    """LG-style heat pumps with a controllable preset (component 14).

    Bounds/hvac_modes/write path are identical to the standard fallback (the
    on/off write is the same start_pump/stop_pump as any other pump; preset
    writes go through the entity's separate async_set_preset_mode). Only the
    reads differ, since they can also derive state from pump_reported and the
    component-14 direction value.
    """

    def hvac_mode(self, device_data: dict[str, Any]) -> HVACMode:
        """Use multiple sources like the switch, for heat pumps with preset modes."""
        # 1. heat_pump_reported from real-time polling
        heat_pump_reported = device_data.get("heat_pump_reported")
        if heat_pump_reported is not None:
            return HVACMode.HEAT if bool(heat_pump_reported) else HVACMode.OFF

        # 2. pump_reported (fallback for LG detected as pump)
        pump_reported = device_data.get("pump_reported")
        if pump_reported is not None:
            return HVACMode.HEAT if bool(pump_reported) else HVACMode.OFF

        # 3. is_running (base state for pumps)
        if device_data.get("is_running", False):
            return HVACMode.HEAT

        # 4. Fallback on is_heating
        return HVACMode.HEAT if device_data.get("is_heating", False) else HVACMode.OFF

    def hvac_action(self, device_data: dict[str, Any], infer_heat_cool: InferHeatCoolAction) -> HVACAction:
        """Decode the same component-14 direction values as Z260iQ."""
        on = device_data.get("heat_pump_reported")
        if on is None:
            on = device_data.get("pump_reported")
        if on is None:
            on = device_data.get("is_running")
        if not on:
            return HVACAction.OFF
        components = device_data.get("components", {})
        reported = components.get("14", {}).get("reportedValue") if isinstance(components, dict) else None
        if reported in (1, 5, 6):  # cooling presets
            return HVACAction.COOLING
        if reported == 2:  # heat_cool: infer direction from temps
            return infer_heat_cool()
        return HVACAction.HEATING


# Behaviors are stateless: one singleton per family is enough.
Z550_BEHAVIOR = Z550Behavior()
Z260IQ_BEHAVIOR = Z260iqBehavior()
LG_BEHAVIOR = LgBehavior()
STANDARD_BEHAVIOR = StandardBehavior()


def resolve_behavior(device_data: dict[str, Any]) -> HeatPumpBehavior:
    """Resolve the behavior for a device, re-checked on every access.

    Identification can change between the first poll (empty components, no
    comp-7 signature yet) and later ones; DeviceIdentifier.identify_device is
    cached, so re-resolving on every property/action access is cheap and
    mirrors the pre-refactor per-property has_feature() checks. Precedence
    order matches the original dispatch: z550_mode, then z260iq_mode, then
    preset_modes, else standard.
    """
    if DeviceIdentifier.has_feature(device_data, "z550_mode"):
        return Z550_BEHAVIOR
    if DeviceIdentifier.has_feature(device_data, "z260iq_mode"):
        return Z260IQ_BEHAVIOR
    if DeviceIdentifier.has_feature(device_data, "preset_modes"):
        return LG_BEHAVIOR
    return STANDARD_BEHAVIOR
