"""Constants for the Fröling Heater integration.

This module centralises all integration-level constants so they can be imported
by any other module without risk of circular imports.  Protocol-level constants
(command codes, state tables, etc.) live in pyfroeling/const.py; only the HA
integration layer constants belong here.
"""

from homeassistant.components.sensor import SensorDeviceClass


# ---------------------------------------------------------------------------
# Integration identity
# ---------------------------------------------------------------------------

# The domain string must match the folder name under custom_components/ and
# every reference to this integration inside HA (config entries, services, …).
DOMAIN = "froeling"

# ---------------------------------------------------------------------------
# Connection defaults
# ---------------------------------------------------------------------------

# No hardcoded defaults -- each user's setup is different.
DEFAULT_HOST = ""
DEFAULT_PORT = 0

# Default polling interval in seconds.  The heater's COM1 serial link is
# shared with the LCD display, so we don't poll too aggressively.
DEFAULT_SCAN_INTERVAL = 60

# Allowed range for the user-configurable polling interval.
MIN_SCAN_INTERVAL = 10    # Faster than 10s risks overloading the serial link
MAX_SCAN_INTERVAL = 600   # 10 minutes is the longest that makes sense

# ---------------------------------------------------------------------------
# Config entry keys
# ---------------------------------------------------------------------------

# Connection types
CONN_TYPE_NETWORK = "network"
CONN_TYPE_SERIAL = "serial"

# Keys stored in config_entry.data (connection settings).
CONF_CONNECTION_TYPE = "connection_type"
CONF_HOST = "host"
CONF_PORT = "port"
CONF_SERIAL_DEVICE = "serial_device"
CONF_DEVICE_NAME = "device_name"

# Default device name shown in HA device registry
DEFAULT_DEVICE_NAME = "Fröling Heater"

# Keys stored in config_entry.data (selected sensors from setup flow).
CONF_SELECTED_SENSORS = "selected_sensors"

# Whether write mode was enabled during the config flow.
# If True, the coordinator will also poll writable parameters and the
# number/select platforms will create control entities.
CONF_WRITE_ENABLED = "write_enabled"

# List of writable parameter addresses (as hex strings, e.g. "0x00A3")
# that the user selected for control during the config flow.
CONF_SELECTED_PARAMETERS = "selected_parameters"

# Keys stored in config_entry.options (user-adjustable settings).
CONF_SCAN_INTERVAL = "scan_interval"

# ---------------------------------------------------------------------------
# Sensor unit → HA device-class mapping
# ---------------------------------------------------------------------------

# Maps the raw unit strings returned by the controller to the corresponding
# Home Assistant SensorDeviceClass.  Sensors whose unit is not in this map
# will have device_class=None, which is perfectly fine for dimensionless or
# custom units (e.g. "kW", "l/h").
UNIT_DEVICE_CLASS_MAP: dict[str, SensorDeviceClass] = {
    "°C": SensorDeviceClass.TEMPERATURE,  # Temperature sensors (Boiler, buffer, etc.)
    "bar": SensorDeviceClass.PRESSURE,    # Pressure sensors (system pressure)
    # NOTE: "%" is intentionally not mapped to POWER_FACTOR.  POWER_FACTOR is an
    # electrical cos(phi) device class and is inappropriate for heater percentages
    # such as fan speed or pump modulation.  Percentage sensors will have
    # device_class=None and display correctly with a plain "%" unit.
}
