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

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.core import HomeAssistant

from .const import CONF_HOST, CONF_PORT, DEFAULT_HOST, DEFAULT_PORT, DOMAIN
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
    user  –  Show form → validate connection → create entry (or show errors)

    A unique_id of ``"host:port"`` is used so that the same physical device
    cannot be added twice.  If the combination is already configured, the flow
    is aborted with the "already_configured" reason.
    """

    # HA uses this version number to decide whether a migration is needed when
    # the data schema changes in a future release.
    VERSION = 1

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
