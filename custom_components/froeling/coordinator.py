"""DataUpdateCoordinator for the Fröling Heater integration.

The coordinator is the single point of truth for all heater data within a
config entry.  It owns the FroelingClient, drives the polling loop, and
exposes a typed FroelingData snapshot to all entity subclasses.

HA coordinator docs:
  https://developers.home-assistant.io/docs/integration_fetching_data
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_SCAN_INTERVAL, CONF_SELECTED_SENSORS, DEFAULT_SCAN_INTERVAL, DOMAIN
from .pyfroeling import (
    ErrorEntry,
    FroelingClient,
    FroelingConnectionError,
    FroelingError,
    HeaterStatus,
    SensorValue,
    ValueSpec,
)

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Typed snapshot of all heater data
# ---------------------------------------------------------------------------

@dataclass
class FroelingData:
    """Immutable snapshot of all heater data fetched during one poll cycle.

    All entity ``native_value`` / ``is_on`` implementations read from this
    dataclass rather than querying the heater directly.  This ensures that all
    entities within a single refresh cycle see a consistent view of the data.

    Fields
    ------
    status:
        Combined heater state + firmware version, as returned by
        :meth:`FroelingClient.get_status`.
    values:
        Dict mapping 16-bit sensor address → :class:`~pyfroeling.SensorValue`.
        Includes measured values, digital outputs, analogue outputs and digital
        inputs, depending on what was discovered during ``_async_setup``.
    errors:
        List of error log entries from the controller's ring buffer, ordered
        from newest (index 0) to oldest.
    specs:
        The full list of :class:`~pyfroeling.ValueSpec` objects discovered at
        startup.  Entities use this to know which sensors exist.
    """

    status: HeaterStatus
    values: dict[int, SensorValue] = field(default_factory=dict)
    errors: list[ErrorEntry] = field(default_factory=list)
    specs: list[ValueSpec] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------

class FroelingCoordinator(DataUpdateCoordinator[FroelingData]):
    """Manages periodic polling of the Fröling heater and distributes data.

    Lifecycle
    ---------
    1. ``_async_setup()`` is called once at entry load time to discover all
       sensor specs from the controller.  The specs are cached in ``_specs``
       and re-used on every subsequent poll so the slow sensor-discovery phase
       is not repeated each minute.

    2. ``_async_update_data()`` is called by the HA scheduler every
       SCAN_INTERVAL seconds.  It fetches status, all sensor values, and the
       error log, bundling them into a :class:`FroelingData` snapshot.

    On reconnect: if the TCP connection has dropped between polls, we call
    ``client.connect()`` before attempting to read data.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        client: FroelingClient,
    ) -> None:
        """Initialise the coordinator.

        Parameters
        ----------
        hass:
            The Home Assistant instance.
        config_entry:
            The config entry this coordinator belongs to (used for logging and
            to give the coordinator a meaningful name).
        client:
            An already-instantiated (but possibly not yet connected)
            :class:`FroelingClient`.  The coordinator takes ownership of the
            connection lifecycle after this point.
        """
        # Read the polling interval from options (user-configurable), falling
        # back to the default if not set.
        scan_interval = config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
            always_update=False,
        )

        # The underlying protocol client – shared with __init__.py for
        # teardown (disconnect on entry unload).
        self.client = client

        # Sensor specs discovered during _async_setup().  Stored as an
        # instance attribute so _async_update_data can use them without
        # re-querying the controller.
        self._specs: list[ValueSpec] = []

        # Keep a reference to the config entry for unique_id / logging.
        self.config_entry = config_entry

    # ------------------------------------------------------------------
    # Setup (called once at integration load time)
    # ------------------------------------------------------------------

    async def _async_setup(self) -> None:
        """Discover available sensors from the controller.

        Called automatically by HA before the first
        :meth:`_async_update_data` invocation (via
        :meth:`async_config_entry_first_refresh`).

        The sensor-discovery exchange (GET_VALUE_LIST_FIRST / _NEXT) can
        take a few seconds on controllers with many sensors, so running it
        once at startup avoids a slow-down on every 60-second poll cycle.

        Raises
        ------
        UpdateFailed
            If the discovery query fails for any reason.
        """
        _LOGGER.debug("FroelingCoordinator: discovering sensors...")
        try:
            self._specs = await self.client.discover_sensors()
            _LOGGER.debug(
                "FroelingCoordinator: discovered %d sensor specs", len(self._specs)
            )
        except FroelingError as exc:
            # Wrap library errors in UpdateFailed so HA marks the entry as
            # "unavailable" rather than crashing the event loop.
            raise UpdateFailed(
                f"Failed to discover sensors from Fröling heater: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Polling (called every SCAN_INTERVAL seconds)
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> FroelingData:
        """Fetch a fresh data snapshot from the heater.

        Steps
        -----
        1. Reconnect if the TCP connection has been lost since the last poll.
        2. Send GET_STATE + GET_VERSION to build a :class:`~pyfroeling.HeaterStatus`.
        3. Send GET_VALUE / GET_DIG_OUT / etc. for every discovered sensor spec.
        4. Read the error log (GET_ERROR_FIRST / _NEXT).
        5. Bundle everything into a :class:`FroelingData` and return it.

        Raises
        ------
        UpdateFailed
            Wraps any :class:`~pyfroeling.FroelingError` so HA can mark
            entities as unavailable instead of propagating the exception.
        """
        try:
            # --- Reconnect if needed ---
            # The TCP connection might have been dropped by the serial bridge
            # (many converters have a configurable idle timeout).
            if not self.client.is_connected:
                _LOGGER.info(
                    "FroelingCoordinator: TCP connection lost, reconnecting…"
                )
                await self.client.connect()

            # --- Fetch heater status (state + firmware version) ---
            status = await self.client.get_status()
            _LOGGER.debug(
                "FroelingCoordinator: state=%s, mode=%s, is_error=%s",
                status.state_text, status.mode_text, status.is_error,
            )

            # --- Fetch only SELECTED sensor values ---
            # The user chose which sensors to enable during setup.
            # Only poll those addresses to minimize serial traffic.
            active_specs = self._get_selected_specs()
            _LOGGER.debug(
                "FroelingCoordinator: polling %d of %d sensors",
                len(active_specs), len(self._specs),
            )

            values = await self.client.get_all_values(active_specs)
            _LOGGER.debug(
                "FroelingCoordinator: fetched %d sensor values", len(values)
            )

            # --- Fetch the error log ---
            errors = await self.client.get_errors()
            _LOGGER.debug(
                "FroelingCoordinator: fetched %d error entries", len(errors)
            )

        except FroelingConnectionError as exc:
            raise UpdateFailed(
                f"Connection to Fröling heater lost: {exc}"
            ) from exc
        except FroelingError as exc:
            raise UpdateFailed(
                f"Error reading from Fröling heater: {exc}"
            ) from exc

        return FroelingData(
            status=status,
            values=values,
            errors=errors,
            specs=self._specs,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_selected_specs(self) -> list[ValueSpec]:
        """Return only the specs that the user selected during setup.

        The selected sensor addresses are stored as a list of hex strings
        (e.g. ["0x0000", "0x0001", ...]) in config_entry.data.

        If no selection is stored (e.g. migrating from an older version),
        all discovered specs are returned as a fallback.
        """
        selected = self.config_entry.data.get(CONF_SELECTED_SENSORS)

        if not selected:
            # No selection stored -- poll everything (backwards compat)
            return self._specs

        # Convert hex strings to int addresses for fast lookup
        selected_addrs = set()
        for addr_str in selected:
            try:
                selected_addrs.add(int(addr_str, 16))
            except ValueError:
                pass

        return [s for s in self._specs if s.address in selected_addrs]
