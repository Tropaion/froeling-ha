"""Diagnostics support for the Fröling Heater integration.

The diagnostics endpoint is used by the HA "Download diagnostics" button in the
device/integration UI.  It returns a JSON-serialisable dict with the full
snapshot of integration state – useful for bug reports without needing a log
file or shell access.

HA diagnostics docs:
  https://developers.home-assistant.io/docs/integration_diagnostics
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .coordinator import FroelingCoordinator


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Return a diagnostics snapshot for the given config entry.

    The returned dict is serialised to JSON by HA and offered as a download.
    All fields should be human-readable and JSON-safe (no datetime objects,
    no enum values, etc.).

    Structure
    ---------
    connection:
        Host, port, and current TCP connection state of the protocol client.

    status:
        Decoded heater status fields: operating state code and text, mode code
        and text, whether the controller is in an error state, firmware version,
        and the controller's real-time clock value as an ISO 8601 string.

    errors:
        Complete error log as a list of dicts.  Each entry carries the error
        number, German description text, lifecycle state name (ARRIVED /
        ACKNOWLEDGED / GONE), ISO 8601 timestamp, and the raw info code.

    sensor_specs:
        Metadata for every sensor spec discovered at startup.  Includes the
        register address (as "0xHHHH" hex string for readability), title,
        unit, scale factor, and the raw MenuStructType code.

    sensor_count:
        Convenience field: how many sensor values were successfully read during
        the most recent poll cycle.

    Parameters
    ----------
    hass:
        Home Assistant instance (unused directly; required by the interface).
    entry:
        The config entry whose diagnostics are requested.

    Returns
    -------
    dict[str, Any]
        JSON-serialisable diagnostics snapshot.
    """
    # Retrieve the coordinator that was stored at setup time.
    coordinator: FroelingCoordinator = entry.runtime_data

    # Guard against calling diagnostics before the first successful data fetch.
    data = coordinator.data

    # --- Connection info ---
    connection_info: dict[str, Any] = {
        # Host and port are stored in the config entry data dict.
        "host": entry.data.get("host"),
        "port": entry.data.get("port"),
        # Whether the TCP socket is currently open.
        "is_connected": coordinator.client.is_connected,
    }

    if data is None:
        # No data yet – return minimal diagnostics so the download still works.
        return {
            "connection": connection_info,
            "status": None,
            "errors": [],
            "sensor_specs": [],
            "sensor_count": 0,
        }

    # --- Heater status ---
    status = data.status
    status_info: dict[str, Any] = {
        # Raw numeric state code (useful for mapping back to STATE_TABLE).
        "state": status.state,
        # German state name from STATE_TABLE.
        "state_text": status.state_text,
        # Raw numeric mode code.
        "mode": status.mode,
        # Human-readable mode name.
        "mode_text": status.mode_text,
        # True if the heater is in a fault/error condition.
        "is_error": status.is_error,
        # Firmware version string from GET_VERSION response.
        "version": status.version,
        # Controller RTC value as ISO 8601 string (JSON-safe).
        "datetime": status.datetime.isoformat(),
    }

    # --- Error log ---
    errors_info: list[dict[str, Any]] = [
        {
            # Sequential error number within the controller's ring buffer.
            "number": err.number,
            # German error description text.
            "text": err.text,
            # Lifecycle state name (ARRIVED, ACKNOWLEDGED, or GONE).
            "state": err.state.name,
            # Timestamp when the error was recorded by the controller.
            "timestamp": err.timestamp.isoformat(),
            # Raw info code (meaning is error-type specific).
            "info": err.info,
        }
        for err in data.errors
    ]

    # --- Sensor specifications ---
    specs_info: list[dict[str, Any]] = [
        {
            # Register address as a human-readable hex string.
            "address": f"0x{spec.address:04X}",
            # German sensor title from the controller menu.
            "title": spec.title,
            # Physical unit (e.g. "°C", "%", "kW").
            "unit": spec.unit,
            # Scale factor used to convert raw integers to physical values.
            "factor": spec.factor,
            # Raw MenuStructType code (0x03=MESSWERT, 0x11=DIG_OUT, …).
            "menu_type": spec.menu_type,
        }
        for spec in data.specs
    ]

    return {
        "connection": connection_info,
        "status": status_info,
        "errors": errors_info,
        "sensor_specs": specs_info,
        # Number of sensor addresses that were successfully read in the last poll.
        "sensor_count": len(data.values),
    }
