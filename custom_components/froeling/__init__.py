"""Fröling Heater integration for Home Assistant.

Reads sensor data, heater state, and error logs from Fröling pellet heaters
via the proprietary binary protocol on COM1, connected through a TCP-to-serial
bridge.

Entry point called by HA when the integration is loaded or unloaded:

  async_setup_entry  – creates the FroelingClient, connects, starts the
                       DataUpdateCoordinator, and forwards setup to platforms.
  async_unload_entry – unloads platforms and disconnects the TCP client.

NOTE: All ``homeassistant.*`` imports are performed INSIDE the async functions
rather than at module level.  This is intentional: the pyfroeling unit tests
import ``custom_components.froeling.pyfroeling.*``, which causes Python to
execute this package __init__.py.  If HA imports were at the top level those
tests would fail with ``ModuleNotFoundError: No module named 'homeassistant'``
in environments without HA installed (e.g., bare pytest CI).

HA integration setup docs:
  https://developers.home-assistant.io/docs/config_entries_index
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Type-only imports (never executed at runtime, only used by type checkers)
# ---------------------------------------------------------------------------
if TYPE_CHECKING:
    # These are only needed for type annotations; importing them here means
    # mypy/pyright see them but they are never executed at runtime when the
    # module is loaded by the pyfroeling test suite.
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .coordinator import FroelingCoordinator


# ---------------------------------------------------------------------------
# Platform list (plain strings – no HA import needed at module level)
# ---------------------------------------------------------------------------

# Platform module names that HA will load.  Adding a new platform here is
# sufficient – HA will automatically call async_setup_entry in the
# corresponding sub-module.
_PLATFORMS_STR: list[str] = ["sensor", "binary_sensor"]


# ---------------------------------------------------------------------------
# Integration setup
# ---------------------------------------------------------------------------

async def async_setup_entry(hass: "HomeAssistant", entry: "ConfigEntry") -> bool:
    """Set up the Fröling integration from a config entry.

    Called by HA when the user has configured the integration (or on HA restart
    when a saved config entry is being restored).

    Steps
    -----
    1. Create a :class:`~.pyfroeling.FroelingClient` from the saved host/port.
    2. Open the TCP connection.
    3. Create a :class:`~.coordinator.FroelingCoordinator` and perform the
       first refresh (sensor discovery + initial data fetch).
    4. Store the coordinator on ``entry.runtime_data`` for platform access.
    5. Forward platform setup to the sensor and binary_sensor modules.

    Raises
    ------
    ConfigEntryNotReady
        If the heater is unreachable at startup so HA retries later.

    Returns
    -------
    bool
        True on success; False would cause HA to log an error and stop.
    """
    # HA imports are deferred to here to avoid breaking pyfroeling unit tests
    # that run without a full HA environment installed.
    from homeassistant.const import Platform
    from homeassistant.exceptions import ConfigEntryNotReady

    from .const import (
        CONF_CONNECTION_TYPE, CONF_HOST, CONF_PORT,
        CONF_SERIAL_DEVICE, CONN_TYPE_SERIAL,
    )
    from .coordinator import FroelingCoordinator
    from .pyfroeling import FroelingClient, FroelingConnectionError

    conn_type = entry.data.get(CONF_CONNECTION_TYPE, "network")
    if conn_type == CONN_TYPE_SERIAL:
        device = entry.data.get(CONF_SERIAL_DEVICE, "")
        _LOGGER.debug("Setting up Fröling integration via serial: %s", device)
        client = FroelingClient(serial_device=device)
    else:
        host = entry.data.get(CONF_HOST, "")
        port = entry.data.get(CONF_PORT, 0)
        _LOGGER.debug("Setting up Fröling integration via TCP: %s:%d", host, port)
        client = FroelingClient(host=host, port=port)

    try:
        await client.connect()
    except FroelingConnectionError as exc:
        raise ConfigEntryNotReady(
            f"Cannot connect to Fröling heater: {exc}"
        ) from exc

    # Create the coordinator that owns the polling loop.
    coordinator = FroelingCoordinator(hass, entry, client)

    # Perform the initial data fetch synchronously (blocks setup until done).
    # This also calls coordinator._async_setup() which discovers sensor specs.
    # If this fails, HA automatically raises ConfigEntryNotReady.
    await coordinator.async_config_entry_first_refresh()

    # Attach the coordinator to the entry so platform modules can reach it via
    # entry.runtime_data without going through hass.data.
    entry.runtime_data = coordinator

    # Resolve platform enum values and delegate entity creation to them.
    platforms = [Platform.SENSOR, Platform.BINARY_SENSOR]
    await hass.config_entries.async_forward_entry_setups(entry, platforms)

    # Listen for options changes (e.g. polling interval) so they take
    # effect without a full restart.
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    _LOGGER.info(
        "Fröling integration set up – firmware %s",
        coordinator.data.status.version,
    )
    return True


async def _async_options_updated(
    hass: "HomeAssistant", entry: "ConfigEntry"
) -> None:
    """Handle options update (e.g. polling interval changed).

    Reloads the integration so the new interval takes effect immediately.
    """
    _LOGGER.info("Fröling options changed, reloading integration")
    await hass.config_entries.async_reload(entry.entry_id)


# ---------------------------------------------------------------------------
# Integration teardown
# ---------------------------------------------------------------------------

async def async_unload_entry(hass: "HomeAssistant", entry: "ConfigEntry") -> bool:
    """Unload the Fröling integration for the given config entry.

    Called by HA when the user removes the integration or when HA is shutting
    down.  We must unload all platforms and close the TCP connection cleanly to
    avoid resource leaks.

    Returns
    -------
    bool
        True if all platforms unloaded successfully; False otherwise.
    """
    from homeassistant.const import Platform

    _LOGGER.debug("Unloading Fröling integration entry %s", entry.entry_id)

    platforms = [Platform.SENSOR, Platform.BINARY_SENSOR]

    # Tell HA to remove all entities registered by our platforms.
    unload_ok = await hass.config_entries.async_unload_platforms(entry, platforms)

    if unload_ok:
        # Disconnect the TCP client so the serial bridge can accept new
        # connections (many TCP-to-serial converters allow only one TCP client).
        coordinator: FroelingCoordinator = entry.runtime_data
        await coordinator.client.disconnect()
        _LOGGER.debug("Fröling TCP client disconnected")

    return unload_ok
