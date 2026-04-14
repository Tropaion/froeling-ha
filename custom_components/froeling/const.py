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

# How often (in seconds) the DataUpdateCoordinator polls the heater.
# Fröling controllers are not real-time; 60 s gives fresh data without flooding
# the COM1 serial link (which is shared with the LCD display).
SCAN_INTERVAL = 60

# ---------------------------------------------------------------------------
# Config entry keys
# ---------------------------------------------------------------------------

# Keys stored in config_entry.data.  Using explicit constants instead of bare
# strings prevents typos from causing subtle bugs at runtime.
CONF_HOST = "host"
CONF_PORT = "port"

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
    "%": SensorDeviceClass.POWER_FACTOR,  # Percentage values (fan speed, modulation)
}
