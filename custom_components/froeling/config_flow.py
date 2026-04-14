"""Config flow for the Fröling Heater integration.

Guides the user through entering the host/port of the TCP-to-serial converter
(e.g. Elfin EE10) that bridges the heater's COM1 serial port to the network.
A live connection test is performed during setup to catch typos early.

HA config-flow docs:
  https://developers.home-assistant.io/docs/config_entries_config_flow_handler
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback

from .const import (
    CONF_HOST,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .pyfroeling import FroelingClient, FroelingConnectionError

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Voluptuous schema for the user form
# ---------------------------------------------------------------------------

# Defined at module level so it can be reused in error-retry flows without
# reconstructing the schema object each time.
_STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST, default=DEFAULT_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
    }
)


async def _validate_connection(hass: HomeAssistant, host: str, port: int) -> None:
    """Try to connect to the heater and verify it responds to a ping.

    Runs in the HA event loop.  Opens a transient FroelingClient, sends a
    CHECK command (0x22), and disconnects.  Any failure raises
    FroelingConnectionError so the caller can map it to the right error key.

    Parameters
    ----------
    hass:
        Home Assistant instance (unused currently, kept for future executor use).
    host:
        Hostname or IP of the TCP-to-serial bridge.
    port:
        TCP port the bridge listens on.

    Raises
    ------
    FroelingConnectionError
        If the TCP handshake fails or the heater does not respond to the ping.
    """
    client = FroelingClient(host, port)
    try:
        await client.connect()
        ok = await client.check_connection()
        if not ok:
            # The TCP connection succeeded but the heater gave no valid response.
            raise FroelingConnectionError(
                f"Heater at {host}:{port} did not respond to CHECK command"
            )
    finally:
        # Always disconnect to avoid leaving dangling TCP connections.
        await client.disconnect()


class FroelingConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the UI-driven config flow for adding a Fröling heater.

    Flow steps
    ----------
    user        –  Show form → validate connection → create entry
    reconfigure –  Update host/port from Settings page
    options     –  Configure polling interval via FroelingOptionsFlow

    A unique_id of ``"host:port"`` is used so that the same physical device
    cannot be added twice.  If the combination is already configured, the flow
    is aborted with the "already_configured" reason.
    """

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Return the options flow handler for this integration.

        This enables the "Configure" button on the integration page in
        Settings, allowing users to adjust the polling interval.
        """
        return FroelingOptionsFlow(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial 'user' step of the config flow.

        On first entry (user_input is None) the empty form is shown.
        When the user submits, we:
          1. Attempt a live connection to the heater.
          2. On success: set unique_id, abort if duplicate, create the entry.
          3. On FroelingConnectionError: re-show the form with an error banner.
          4. On any other exception: show the generic "unknown" error.

        Parameters
        ----------
        user_input:
            Dict with keys CONF_HOST / CONF_PORT when the form was submitted,
            or None on the first (empty) display.

        Returns
        -------
        ConfigFlowResult
            Either another step, an abort, or the finished entry creation.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            host: str = user_input[CONF_HOST]
            port: int = user_input[CONF_PORT]

            try:
                await _validate_connection(self.hass, host, port)

            except FroelingConnectionError:
                # The most common failure: wrong IP, port, or no cable.
                _LOGGER.debug(
                    "Config flow: cannot connect to Fröling at %s:%d", host, port
                )
                errors["base"] = "cannot_connect"

            except Exception:  # noqa: BLE001
                # Catch-all for unexpected errors (e.g., DNS resolution failure).
                _LOGGER.exception(
                    "Config flow: unexpected error connecting to %s:%d", host, port
                )
                errors["base"] = "unknown"

            else:
                # Connection validated successfully.
                # Build the unique_id from the network address so the same
                # device cannot be added twice.
                unique_id = f"{host}:{port}"
                await self.async_set_unique_id(unique_id)

                # Abort with a friendly message if already configured.
                self._abort_if_unique_id_configured()

                # Create the persistent config entry.
                return self.async_create_entry(
                    title=f"Fröling ({host})",
                    data={
                        CONF_HOST: host,
                        CONF_PORT: port,
                    },
                )

        # Show (or re-show with errors) the host/port input form.
        return self.async_show_form(
            step_id="user",
            data_schema=_STEP_USER_SCHEMA,
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguration of host/port from the Settings page.

        Allows the user to update the TCP-to-serial bridge address without
        removing and re-adding the integration.  Accessible via:
          Settings > Integrations > Fröling Heater > Reconfigure

        The same connection validation as the initial setup is performed.
        """
        errors: dict[str, str] = {}

        # Get the existing config entry being reconfigured
        entry = self._get_reconfigure_entry()

        if user_input is not None:
            host: str = user_input[CONF_HOST]
            port: int = user_input[CONF_PORT]

            try:
                await _validate_connection(self.hass, host, port)
            except FroelingConnectionError:
                _LOGGER.debug(
                    "Reconfigure: cannot connect to Fröling at %s:%d", host, port
                )
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception(
                    "Reconfigure: unexpected error connecting to %s:%d", host, port
                )
                errors["base"] = "unknown"
            else:
                # Update the unique_id and config entry data
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=f"{host}:{port}",
                    title=f"Fröling ({host})",
                    data={CONF_HOST: host, CONF_PORT: port},
                )

        # Pre-fill the form with the current values
        current_host = entry.data.get(CONF_HOST, DEFAULT_HOST)
        current_port = entry.data.get(CONF_PORT, DEFAULT_PORT)
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=current_host): str,
                vol.Required(CONF_PORT, default=current_port): int,
            }
        )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=schema,
            errors=errors,
        )


# ---------------------------------------------------------------------------
# Options flow (Settings > Integrations > Fröling > Configure)
# ---------------------------------------------------------------------------

class FroelingOptionsFlow(OptionsFlow):
    """Handle the options flow for adjusting the polling interval.

    Accessible via Settings > Integrations > Fröling Heater > Configure.
    Changes take effect after the next polling cycle (no restart needed).
    """

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the options form with the current polling interval.

        The polling interval determines how often the heater is queried.
        Lower values = fresher data but more serial traffic.
        Minimum: 10s, Maximum: 600s (10 min), Default: 60s.
        """
        if user_input is not None:
            # Save the new options and let HA reload the integration
            return self.async_create_entry(title="", data=user_input)

        # Pre-fill with current value
        current_interval = self._config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=current_interval,
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
                ),
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)
