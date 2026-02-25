"""Constants for the Fluidra Pool integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, TypeAlias

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .coordinator import FluidraDataUpdateCoordinator

DOMAIN: Final = "fluidra_pool"

# 🏆 Type alias for ConfigEntry (Platinum) - Compatible Python 3.11+
FluidraPoolConfigEntry: TypeAlias = "ConfigEntry[FluidraPoolRuntimeData]"  # noqa: UP040


@dataclass(frozen=True, slots=True)
class FluidraPoolRuntimeData:
    """Runtime data for Fluidra Pool integration (Platinum - frozen, slots)."""

    coordinator: FluidraDataUpdateCoordinator


# Configuration
CONF_EMAIL = "email"
CONF_PASSWORD = "password"

# Device types
DEVICE_TYPE_PUMP = "pump"
DEVICE_TYPE_HEAT_PUMP = "heat_pump"
DEVICE_TYPE_HEATER = "heater"
DEVICE_TYPE_LIGHT = "light"
DEVICE_TYPE_SENSOR = "sensor"

# Attributes
ATTR_DEVICE_ID = "device_id"
ATTR_POOL_ID = "pool_id"
ATTR_SPEED = "speed"
ATTR_TARGET_TEMPERATURE = "target_temperature"
ATTR_CURRENT_TEMPERATURE = "current_temperature"
ATTR_BRIGHTNESS = "brightness"
ATTR_COLOR = "color"

# Services
SERVICE_SET_PUMP_SPEED = "set_pump_speed"
SERVICE_SET_HEATER_TEMPERATURE = "set_heater_temperature"
SERVICE_SET_LIGHT_COLOR = "set_light_color"
SERVICE_SET_LIGHT_BRIGHTNESS = "set_light_brightness"

# Default values
DEFAULT_SCAN_INTERVAL = 30  # seconds
DEFAULT_TIMEOUT = 10  # seconds

# Timing constants for optimistic state management
OPTIMISTIC_STATE_CLEAR_DELAY = 5  # seconds - delay before clearing optimistic state
COMMAND_CONFIRMATION_DELAY = 3  # seconds - delay after command before refresh
SWITCH_CONFIRMATION_DELAY = 2  # seconds - delay after switch toggle before refresh
UI_UPDATE_DELAY = 0.1  # seconds - small delay for UI responsiveness
PUMP_START_DELAY = 1  # seconds - delay after pump start before setting speed

# Z550iQ+ Heat Pump Constants
Z550_MIN_TEMP = 15.0
Z550_MAX_TEMP = 40.0
Z550_TEMP_STEP = 1.0

# Z550iQ+ HVAC modes (component 16 values)
Z550_MODE_HEATING = 0
Z550_MODE_COOLING = 1
Z550_MODE_AUTO = 2

# Z550iQ+ state values (component 61)
Z550_STATE_IDLE = 0
Z550_STATE_HEATING = 2
Z550_STATE_COOLING = 3
Z550_STATE_NO_FLOW = 11

# Z550iQ+ preset modes (component 17 values)
Z550_PRESET_SILENCE = "silence"
Z550_PRESET_SMART = "smart"
Z550_PRESET_BOOST = "boost"

Z550_PRESET_MODES = [Z550_PRESET_SILENCE, Z550_PRESET_SMART, Z550_PRESET_BOOST]

Z550_PRESET_TO_VALUE = {
    Z550_PRESET_SILENCE: 0,
    Z550_PRESET_SMART: 1,
    Z550_PRESET_BOOST: 2,
}

Z550_VALUE_TO_PRESET = {v: k for k, v in Z550_PRESET_TO_VALUE.items()}

# LG Heat Pump Constants (component 14 values)
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

# LumiPlus Connect component IDs
LUMIPLUS_COMPONENT_POWER: Final = 11
LUMIPLUS_COMPONENT_BRIGHTNESS: Final = 17
LUMIPLUS_COMPONENT_COLOR: Final = 45
