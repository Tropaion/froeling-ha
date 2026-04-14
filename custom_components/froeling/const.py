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

# Default target for the TCP-to-serial converter connected to COM1.
DEFAULT_HOST = "192.168.88.180"

# Default port used by the Elfin EE10 and similar serial-over-TCP adapters.
DEFAULT_PORT = 8899

# Default polling interval in seconds.  The heater's COM1 serial link is
# shared with the LCD display, so we don't poll too aggressively.
DEFAULT_SCAN_INTERVAL = 60

# Allowed range for the user-configurable polling interval.
MIN_SCAN_INTERVAL = 10    # Faster than 10s risks overloading the serial link
MAX_SCAN_INTERVAL = 600   # 10 minutes is the longest that makes sense

# ---------------------------------------------------------------------------
# Config entry keys
# ---------------------------------------------------------------------------

# Keys stored in config_entry.data (connection settings).
CONF_HOST = "host"
CONF_PORT = "port"

# Keys stored in config_entry.data (selected sensors from setup flow).
CONF_SELECTED_SENSORS = "selected_sensors"

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
