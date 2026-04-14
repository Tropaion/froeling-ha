"""Config flow for the Fröling Heater integration.

Multi-step setup:
  1. User enters host + port of the TCP-to-serial converter
  2. Integration connects, discovers all available sensors
  3. User selects which sensors to enable
  4. Config entry is created with selected sensors

Also provides:
  - Reconfigure flow to change host/port
  - Options flow to adjust polling interval and sensor selection

HA config-flow docs:
  https://developers.home-assistant.io/docs/config_entries_config_flow_handler
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_HOST,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_SELECTED_SENSORS,
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .pyfroeling import FroelingClient, FroelingConnectionError, ValueSpec

_LOGGER = logging.getLogger(__name__)

# Schema for the connection step
_STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST, default=DEFAULT_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
    }
)


@dataclass
class DiscoveredSensor:
    """A sensor discovered from the heater with its current value."""
    spec: ValueSpec
    value: float | None  # Current reading, or None if read failed
    readable: bool       # True if the sensor responded with a valid value


async def _validate_and_discover(
    host: str, port: int
) -> list[DiscoveredSensor]:
    """Connect to the heater, discover sensors, and read their current values.

    Returns a list of DiscoveredSensor objects with current readings.
    Sensors that fail to read are marked as not readable.

    Raises FroelingConnectionError if the initial connection fails.
    """
    client = FroelingClient(host, port)
    try:
        await client.connect()
        ok = await client.check_connection()
        if not ok:
            raise FroelingConnectionError(
                f"Heater at {host}:{port} did not respond to CHECK command"
            )

        # Step 1: Discover all available sensor addresses
        specs = await client.discover_sensors()

        # Step 2: Read current values for each sensor to check which are real
        discovered: list[DiscoveredSensor] = []
        for spec in specs:
            try:
                sv = await client.get_value(spec.address, spec)
                discovered.append(DiscoveredSensor(
                    spec=spec, value=sv.value, readable=True
                ))
            except Exception:
                # Sensor address exists in the list but couldn't be read --
                # likely not physically connected on this heater model
                discovered.append(DiscoveredSensor(
                    spec=spec, value=None, readable=False
                ))

        return discovered
    finally:
        await client.disconnect()


def _sensors_to_select_options(
    sensors: list[DiscoveredSensor], include_unreadable: bool = False
) -> list[SelectOptionDict]:
    """Convert discovered sensors to HA SelectOptionDict for multi-select UI.

    Shows the sensor title, unit, and current value.
    Example: "Kesseltemperatur = 65.3 °C" with value "0x0000"

    Sensors that couldn't be read are excluded by default (likely not
    physically present on this heater model).
    """
    options: list[SelectOptionDict] = []
    for sensor in sensors:
        if not sensor.readable and not include_unreadable:
            continue

        # Build a descriptive label showing the current value
        if sensor.readable and sensor.value is not None:
            if sensor.spec.unit:
                label = f"{sensor.spec.title} = {sensor.value:.1f} {sensor.spec.unit}"
            else:
                label = f"{sensor.spec.title} = {sensor.value:.1f}"
        else:
            label = f"{sensor.spec.title} (nicht verfügbar)"

        options.append(
            SelectOptionDict(
                value=f"0x{sensor.spec.address:04X}",
                label=label,
            )
        )
    return options


class FroelingConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the multi-step config flow for adding a Fröling heater.

    Flow steps:
      user     -> enter host/port, validate connection, discover sensors
      sensors  -> select which sensors to enable
      create   -> create config entry with selected sensors
    """

    VERSION = 1

    def __init__(self) -> None:
        """Initialize flow state for multi-step data passing."""
        self._host: str = ""
        self._port: int = 0
        self._discovered: list[DiscoveredSensor] = []

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> FroelingOptionsFlow:
        """Return the options flow handler (Configure button)."""
        return FroelingOptionsFlow(config_entry)

    # ------------------------------------------------------------------
    # Step 1: Connection settings
    # ------------------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: Collect host and port, validate connection, discover sensors.

        On success, transitions to step 2 (sensor selection).
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            self._host = user_input[CONF_HOST]
            self._port = user_input[CONF_PORT]

            try:
                self._discovered = await _validate_and_discover(
                    self._host, self._port
                )
                readable = sum(1 for s in self._discovered if s.readable)
                _LOGGER.info(
                    "Config flow: discovered %d sensors (%d readable) from %s:%d",
                    len(self._discovered), readable, self._host, self._port,
                )
            except FroelingConnectionError:
                _LOGGER.debug(
                    "Config flow: cannot connect to %s:%d", self._host, self._port
                )
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception(
                    "Config flow: unexpected error with %s:%d", self._host, self._port
                )
                errors["base"] = "unknown"
            else:
                if not self._discovered:
                    errors["base"] = "no_sensors"
                else:
                    # Connection OK, sensors found -> go to sensor selection
                    return await self.async_step_sensors()

        return self.async_show_form(
            step_id="user",
            data_schema=_STEP_USER_SCHEMA,
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Step 2: Sensor selection
    # ------------------------------------------------------------------

    async def async_step_sensors(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2: Let the user select which sensors to enable.

        Shows a multi-select list of all discovered sensors. All sensors
        are pre-selected by default. The user can deselect sensors they
        don't need to reduce serial traffic.
        """
        if user_input is not None:
            selected = user_input.get(CONF_SELECTED_SENSORS, [])

            # Set unique_id and check for duplicates
            unique_id = f"{self._host}:{self._port}"
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()

            # Create the config entry with connection + selected sensors
            return self.async_create_entry(
                title=f"Fröling ({self._host})",
                data={
                    CONF_HOST: self._host,
                    CONF_PORT: self._port,
                    CONF_SELECTED_SENSORS: selected,
                },
            )

        # Build the multi-select options from discovered sensors.
        # Only show readable sensors (those that responded with a value).
        # Unreadable sensors are likely not physically present on this model.
        options = _sensors_to_select_options(self._discovered)

        # Pre-select all readable sensors by default
        all_values = [opt["value"] for opt in options]

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SELECTED_SENSORS,
                    default=all_values,
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=options,
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="sensors",
            data_schema=schema,
        )

    # ------------------------------------------------------------------
    # Reconfigure (change host/port)
    # ------------------------------------------------------------------

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Reconfigure host/port from the Settings page."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()

        if user_input is not None:
            host: str = user_input[CONF_HOST]
            port: int = user_input[CONF_PORT]

            try:
                await _validate_and_discover(host, port)
            except FroelingConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                errors["base"] = "unknown"
            else:
                # Keep existing selected sensors, update connection
                new_data = dict(entry.data)
                new_data[CONF_HOST] = host
                new_data[CONF_PORT] = port
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=f"{host}:{port}",
                    title=f"Fröling ({host})",
                    data=new_data,
                )

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
    """Options flow for polling interval and sensor selection.

    Accessible via Settings > Integrations > Fröling Heater > Configure.
    """

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show options: polling interval and sensor re-selection.

        Sensor re-selection requires reconnecting to the heater to
        discover the current sensor list.
        """
        if user_input is not None:
            # If sensor selection was included, update config data too
            new_options = {
                CONF_SCAN_INTERVAL: user_input.get(
                    CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                ),
            }

            # Update selected sensors in config entry data if provided
            if CONF_SELECTED_SENSORS in user_input:
                new_data = dict(self._config_entry.data)
                new_data[CONF_SELECTED_SENSORS] = user_input[CONF_SELECTED_SENSORS]
                self.hass.config_entries.async_update_entry(
                    self._config_entry, data=new_data
                )

            return self.async_create_entry(title="", data=new_options)

        # Current values
        current_interval = self._config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )
        current_selected = self._config_entry.data.get(CONF_SELECTED_SENSORS, [])

        # Try to discover sensors for the selection list
        host = self._config_entry.data.get(CONF_HOST, "")
        port = self._config_entry.data.get(CONF_PORT, 0)
        sensor_options: list[SelectOptionDict] = []

        try:
            discovered = await _validate_and_discover(host, port)
            sensor_options = _sensors_to_select_options(discovered)
        except Exception:
            _LOGGER.warning(
                "Options flow: could not discover sensors from %s:%d, "
                "showing only polling interval",
                host, port,
            )

        # Build schema
        schema_dict: dict[vol.Marker, Any] = {
            vol.Required(
                CONF_SCAN_INTERVAL,
                default=current_interval,
            ): vol.All(
                vol.Coerce(int),
                vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
            ),
        }

        # Only show sensor selection if discovery succeeded
        if sensor_options:
            # Default to current selection, or all if none set
            default_selected = (
                current_selected
                if current_selected
                else [opt["value"] for opt in sensor_options]
            )
            schema_dict[vol.Required(
                CONF_SELECTED_SENSORS,
                default=default_selected,
            )] = SelectSelector(
                SelectSelectorConfig(
                    options=sensor_options,
                    multiple=True,
                    mode=SelectSelectorMode.LIST,
                )
            )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema_dict),
        )
