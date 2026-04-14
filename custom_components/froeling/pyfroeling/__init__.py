"""pyfroeling -- Async Python library for Fröling heater communication.

Implements the proprietary binary protocol used on the COM1 service interface
of Fröling Lambdatronic P/S 3200 controllers. Designed for use with
TCP-to-serial converters (e.g., Elfin EE10).

Protocol reverse-engineered by the linux-p4d project (Jörg Wendel).

Public API
----------
High-level client (main entry point for applications):
    FroelingClient          -- connects, reads state/sensors/errors/parameters

Exception classes:
    FroelingError           -- base exception for all library errors
    FroelingConnectionError -- TCP connection / timeout problems
    FroelingProtocolError   -- unexpected or malformed controller responses

Data model classes (returned by FroelingClient methods):
    HeaterStatus            -- combined state + version snapshot
    SensorValue             -- single scaled sensor reading
    ValueSpec               -- sensor metadata (address, factor, unit, title)
    IoValue                 -- digital/analogue I/O channel state
    ErrorEntry              -- single entry from the error log
    ErrorState              -- bitmask enum for error lifecycle flags
    ConfigParameter         -- configurable EEPROM parameter with limits
"""

# ---------------------------------------------------------------------------
# Re-export the high-level client and its exceptions
# ---------------------------------------------------------------------------
from .client import FroelingClient, FroelingConnectionError, FroelingError, FroelingProtocolError

# ---------------------------------------------------------------------------
# Re-export data model classes so callers can do:
#   from pyfroeling import HeaterStatus, SensorValue, ...
# ---------------------------------------------------------------------------
from .models import (
    ConfigParameter,
    ErrorEntry,
    ErrorState,
    HeaterStatus,
    IoValue,
    SensorValue,
    ValueSpec,
)

# Define __all__ so that ``from pyfroeling import *`` is predictable.
__all__ = [
    # Client
    "FroelingClient",
    # Exceptions
    "FroelingError",
    "FroelingConnectionError",
    "FroelingProtocolError",
    # Data models
    "ConfigParameter",
    "ErrorEntry",
    "ErrorState",
    "HeaterStatus",
    "IoValue",
    "SensorValue",
    "ValueSpec",
]
