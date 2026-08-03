"""Constants for the Fluidra Pool integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final, TypeAlias

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .coordinator import FluidraDataUpdateCoordinator

DOMAIN: Final = "fluidra_pool"

FluidraPoolConfigEntry: TypeAlias = "ConfigEntry[FluidraPoolRuntimeData]"  # noqa: UP040


@dataclass(frozen=True, slots=True)
class FluidraPoolRuntimeData:
    """Runtime data for the Fluidra Pool integration."""

    coordinator: FluidraDataUpdateCoordinator
    # Options captured at setup time. The update listener reloads only when the
    # *options* actually change, so token-only data writes don't self-reload.
    options_snapshot: dict[str, Any] = field(default_factory=dict)


# Configuration keys (CONF_EMAIL / CONF_PASSWORD come from homeassistant.const)
CONF_REFRESH_TOKEN: Final = "refresh_token"

# Device types
DEVICE_TYPE_PUMP: Final = "pump"
DEVICE_TYPE_HEAT_PUMP: Final = "heat_pump"
DEVICE_TYPE_HEATER: Final = "heater"
DEVICE_TYPE_LIGHT: Final = "light"
DEVICE_TYPE_CHLORINATOR: Final = "chlorinator"

# Device-registry model labels shown on the HA device page.
DEVICE_MODEL_MAP: Final[dict[str, str]] = {
    DEVICE_TYPE_CHLORINATOR: "Chlorinator",
    DEVICE_TYPE_PUMP: "Pump",
    DEVICE_TYPE_HEAT_PUMP: "Heat Pump",
    DEVICE_TYPE_LIGHT: "Light",
    DEVICE_TYPE_HEATER: "Heater",
}
DEVICE_MODEL_FALLBACK: Final = "Pool Equipment"

# Stale-device reconciliation: a device must be absent from this many consecutive
# successful polls before it is purged from the registry, so a transient partial
# cloud response cannot wipe devices, entities and their history on one hiccup.
STALE_DEVICE_THRESHOLD: Final = 3

# Connection repair issue: raised after this many consecutive failed poll cycles
# so a single cloud hiccup does not spam the repairs dashboard.
CONNECTION_ISSUE_THRESHOLD: Final = 3

# Smart Heat+Cool (c14=2) exposes no direction register, so hvac_action is
# inferred from the water-vs-setpoint delta; inside this deadband (°C) the
# setpoint is satisfied and the unit reports IDLE (Issue #139). Set to 2.0 °C
# after a Z250iQ power-meter capture (@Kal42): with a 28 °C setpoint the
# compressor stayed off until the water reached 30 °C, i.e. it only engages at
# ±2 °C, so a ±1 °C window over-reported heating/cooling in the 1-2 °C band.
HEAT_COOL_ACTION_DEADBAND: Final = 2.0

# Consecutive "connectivity.connected == false" reports required before a device
# is marked offline. The Fluidra cloud heartbeat routinely misreports a healthy
# WiFi device (e.g. Z550iQ+) as disconnected for a single poll — the same
# unreliable flag worked around for chlorinators in Issue #63 — which flipped
# every entity of the device unavailable for one scan interval (Issue #140).
# Recovery is immediate on the first online report.
OFFLINE_GRACE_POLLS: Final = 2

# A device's component fetch (_fetch_components) can come back completely
# empty every poll — e.g. every per-component request failing individually —
# without ever raising: _fetch_components_parallel catches each task's
# exception via asyncio.gather(return_exceptions=True) and only debug-logs it
# (a single bad component must not sink the whole poll). That's correct for an
# occasional partial miss, but if it happens for every requested component on
# every poll, the device's sensors silently freeze on stale data forever: no
# exception ever reaches _async_update_data, so _note_update_failure() is never
# called, no connection_error repair issue is raised, and the entities keep
# reporting "successful" stale state indefinitely. This threshold bounds how
# many consecutive fully-empty polls a device gets before that silence is
# turned into a real failure that surfaces to the user (see coordinator.py
# _refresh_pool). Observed in the wild: a transient DNS failure tripped the
# circuit breaker, and a device sat frozen for 6+ hours with zero visible
# error until a manual HA restart.
EMPTY_COMPONENT_FETCH_THRESHOLD: Final = 3

# Attributes
ATTR_BRIGHTNESS: Final = "brightness"

# Default values
DEFAULT_SCAN_INTERVAL: Final = 30  # seconds
DEFAULT_TIMEOUT: Final = 30  # seconds — aligned with HTTP request timeout

# Timing constants for optimistic state management
COMMAND_CONFIRMATION_DELAY: Final = 3  # seconds - delay after command before refresh
SWITCH_CONFIRMATION_DELAY: Final = 2  # seconds - delay after switch toggle before refresh
UI_UPDATE_DELAY: Final = 0.1  # seconds - small delay for UI responsiveness
PUMP_START_DELAY: Final = 1  # seconds - delay after pump start before setting speed
OPTIMISTIC_ACTION_TIMEOUT: Final = 10  # seconds - timeout for optimistic local state
# Consecutive bulk-component-fetch failures before falling back to per-component
# polling for the rest of the session (Issue #144). A few retries absorb transient
# network errors; a backend that genuinely lacks the endpoint stops costing an
# extra request per device per poll.
BULK_FETCH_FAILURE_LIMIT: Final = 3
# Victoria VS pumps need much longer: the cloud takes 15-30 s to reflect a command,
# and arming the schedule kicks off a PRIMING → CALIBRATION sequence lasting ~1 min
# before the pump settles. With the default 10 s the toggle dropped its optimistic
# state and flapped through intermediate values (Issue #144, @renaatski).
VICTORIA_OPTIMISTIC_TIMEOUT: Final = 120  # seconds
# Heat pumps report back quickly; a short optimistic window avoids masking real state.
CLIMATE_OPTIMISTIC_TIMEOUT: Final = 5  # seconds
# tecnoLC2 chlorinator cells can take up to ~2 min to report a mode change back.
CHLORINATOR_MODE_OPTIMISTIC_TIMEOUT: Final = 120  # seconds

# Fluidra component IDs (discovered via reverse engineering). Only IDs with a
# single stable meaning get a name — most component IDs mean different things
# per device family, so the coordinator keys off commented raw integers instead.
COMPONENT_PUMP_ONOFF: Final = 9
COMPONENT_AUTO_MODE: Final = 10
COMPONENT_PUMP_SPEED: Final = 11
COMPONENT_HEAT_PUMP_ONOFF: Final = 13
COMPONENT_HEAT_PUMP_SETPOINT: Final = 15
COMPONENT_LIGHT_BRIGHTNESS: Final = 17

# Victoria Smart Connect VS write path (Issue #144 — verified traffic capture by
# @renaatski). This family ignores the E30iQ c9/c10 writes; control goes through:
#   c13 (bool)  — enable/disable the auto schedule
#   c15 (bool)  — stop trigger: with c13=0 it halts the motor (STOP), with c13=1
#                 it only ends the current override and returns to AUTO. It cannot
#                 command a *run* state — starting is done via c13 or a c20 preset.
#   c20 (int)   — trigger a quick-function / preset index (write-side of the read reg)
COMPONENT_VICTORIA_AUTO_SCHEDULE: Final = 13
COMPONENT_VICTORIA_STOP: Final = 15
COMPONENT_VICTORIA_QUICK_FUNCTION: Final = 20
# Quick-function preset slots as consolidated JSON objects (Issue #144). The pump
# also exposes them as scalar quadruplets on c89-c124, but @renaatski found the
# field offsets there inconsistent (c92 and c126 disagree on Clean's duration),
# so the objects are used instead. Empty slots report null. The index written to
# c20 is the slot's offset within this range.
VICTORIA_PRESET_FIRST: Final = 126
VICTORIA_PRESET_LAST: Final = 134
COMPONENT_SCHEDULE: Final = 20
COMPONENT_LIGHT_COLOR: Final = 45
COMPONENT_DM24049704_SCHEDULE: Final = 258

# Pump speed mapping: API level → displayed percentage
PUMP_SPEED_PERCENTAGES: Final[dict[int, int]] = {
    0: 45,  # Low
    1: 65,  # Medium
    2: 100,  # High
}

# Z550iQ+ Heat Pump constants (no preset constants: component 17 is read-only,
# the Z550 climate entity intentionally exposes no controllable preset).
Z550_MIN_TEMP: Final = 15.0
Z550_MAX_TEMP: Final = 40.0
Z550_TEMP_STEP: Final = 1.0

Z550_MODE_HEATING: Final = 0
Z550_MODE_COOLING: Final = 1
Z550_MODE_AUTO: Final = 2

Z550_STATE_IDLE: Final = 0
Z550_STATE_HEATING: Final = 2
Z550_STATE_COOLING: Final = 3
Z550_STATE_NO_FLOW: Final = 11

# LG Heat Pump constants (component 14 values)
LG_PRESET_SMART_HEATING: Final = "smart_heating"
LG_PRESET_SMART_COOLING: Final = "smart_cooling"
LG_PRESET_SMART_HEAT_COOL: Final = "smart_heat_cool"
LG_PRESET_BOOST_HEATING: Final = "boost_heating"
LG_PRESET_SILENCE_HEATING: Final = "silence_heating"
LG_PRESET_BOOST_COOLING: Final = "boost_cooling"
LG_PRESET_SILENCE_COOLING: Final = "silence_cooling"

LG_PRESET_MODES: Final = [
    LG_PRESET_SMART_HEATING,
    LG_PRESET_SMART_COOLING,
    LG_PRESET_SMART_HEAT_COOL,
    LG_PRESET_BOOST_HEATING,
    LG_PRESET_SILENCE_HEATING,
    LG_PRESET_BOOST_COOLING,
    LG_PRESET_SILENCE_COOLING,
]

LG_MODE_TO_VALUE: Final = {
    LG_PRESET_SMART_HEATING: 0,
    LG_PRESET_SMART_COOLING: 1,
    LG_PRESET_SMART_HEAT_COOL: 2,
    LG_PRESET_BOOST_HEATING: 3,
    LG_PRESET_SILENCE_HEATING: 4,
    LG_PRESET_BOOST_COOLING: 5,
    LG_PRESET_SILENCE_COOLING: 6,
}

LG_VALUE_TO_MODE: Final = {v: k for k, v in LG_MODE_TO_VALUE.items()}

# Z650iQ Heat Pump constants — the Fluidra App uses different names for the
# same component-14 register values. Only 4 of the 7 LG presets are available.
# Mapping: App "Smart+" -> c14=0, "Boost" -> c14=1, "Smart" -> c14=2, "Ecosilence" -> c14=3
Z650_PRESET_SMART_PLUS: Final = "smart_plus"  # Fluidra App: "Smart+" (Heat)
Z650_PRESET_SMART: Final = "smart"  # Fluidra App: "Smart" (Heat/Cool)
Z650_PRESET_ECOSILENCE: Final = "ecosilence"  # Fluidra App: "Ecosilence" (Heat)
Z650_PRESET_BOOST: Final = "boost"  # Fluidra App: "Boost" (Cool)

Z650_PRESET_MODES: Final = [
    Z650_PRESET_SMART_PLUS,
    Z650_PRESET_SMART,
    Z650_PRESET_ECOSILENCE,
    Z650_PRESET_BOOST,
]

Z650_MODE_TO_VALUE: Final = {
    Z650_PRESET_SMART_PLUS: 0,  # c14=0 -> Heat
    Z650_PRESET_BOOST: 1,  # c14=1 -> Cool
    Z650_PRESET_SMART: 2,  # c14=2 -> Heat/Cool
    Z650_PRESET_ECOSILENCE: 3,  # c14=3 -> Heat
}

Z650_VALUE_TO_MODE: Final = {v: k for k, v in Z650_MODE_TO_VALUE.items()}

# Z260iQ Heat Pump constants
Z260_MIN_TEMP: Final = 7.0
Z260_MAX_TEMP: Final = 40.0
Z260_TEMP_STEP: Final = 1.0

# LumiPlus Connect component IDs (re-export of generic constants)
LUMIPLUS_COMPONENT_POWER: Final = 11
LUMIPLUS_COMPONENT_BRIGHTNESS: Final = COMPONENT_LIGHT_BRIGHTNESS
LUMIPLUS_COMPONENT_COLOR: Final = COMPONENT_LIGHT_COLOR
