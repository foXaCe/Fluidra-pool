"""Constants for the Fluidra Pool integration."""

DOMAIN = "fluidra_pool"

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