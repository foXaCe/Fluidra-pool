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

# Fluidra component IDs (discovered via reverse engineering). Only IDs with a
# single stable meaning get a name — most component IDs mean different things
# per device family, so the coordinator keys off commented raw integers instead.
COMPONENT_PUMP_ONOFF: Final = 9
COMPONENT_AUTO_MODE: Final = 10
COMPONENT_PUMP_SPEED: Final = 11
COMPONENT_HEAT_PUMP_ONOFF: Final = 13
COMPONENT_HEAT_PUMP_SETPOINT: Final = 15
COMPONENT_LIGHT_BRIGHTNESS: Final = 17
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

# Z260iQ Heat Pump constants
Z260_MIN_TEMP: Final = 7.0
Z260_MAX_TEMP: Final = 40.0
Z260_TEMP_STEP: Final = 1.0

# LumiPlus Connect component IDs (re-export of generic constants)
LUMIPLUS_COMPONENT_POWER: Final = 11
LUMIPLUS_COMPONENT_BRIGHTNESS: Final = COMPONENT_LIGHT_BRIGHTNESS
LUMIPLUS_COMPONENT_COLOR: Final = COMPONENT_LIGHT_COLOR
