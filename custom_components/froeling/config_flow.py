"""Config flow for the Fröling Heater integration.

Multi-step setup:
  1. User chooses connection type (Network TCP or USB Serial)
  2. User enters connection details + device name
     → Integration connects, discovers sensors, reads current values
  3. User chooses access mode (read-only or read/write)
  4. If read/write: warning screen, then parameter discovery
  5. User selects which sensors to enable
  6. If write mode: user selects which writable parameters to control
  7. Config entry is created

Also provides:
  - Reconfigure flow to change connection settings
  - Options flow to adjust polling interval and sensor selection
"""

from __future__ import annotations

import logging
from collections import Counter
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
    CONF_CONNECTION_TYPE,
    CONF_DEVICE_NAME,
    CONF_HOST,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_SELECTED_PARAMETERS,
    CONF_SELECTED_SENSORS,
    CONF_SERIAL_DEVICE,
    CONF_WRITE_ENABLED,
    CONN_TYPE_NETWORK,
    CONN_TYPE_SERIAL,
    DEFAULT_DEVICE_NAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .pyfroeling import FroelingClient, FroelingConnectionError, ValueSpec, WritableParameter

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sensor discovery helper
# ---------------------------------------------------------------------------

@dataclass
class DiscoveredSensor:
    """A sensor discovered from the heater with its current value."""
    spec: ValueSpec
    value: float | None  # Current reading, or None if read failed
    readable: bool       # True if get_value succeeded


async def _validate_and_discover(
    client: FroelingClient,
) -> list[DiscoveredSensor]:
    """Connect, verify, discover sensors, and read their current values.

    Uses a single continuous connection for the entire flow (connect ->
    check -> discover -> read values). This matches v0.5.1's approach
    which is proven to work reliably with the EE10 and other converters.

    The client must already be connected before calling this function.

    Raises FroelingConnectionError if the connection or protocol fails.
    """
    # Verify the connection with a CHECK command (protocol sync)
    ok = await client.check_connection()
    if not ok:
        raise FroelingConnectionError("Heater did not respond to CHECK command")

    # Discover all available sensor specs
    specs = await client.discover_sensors()
    _LOGGER.debug("Discovered %d sensor specs, reading values...", len(specs))

    # Read current values on the SAME connection (no disconnect/reconnect)
    discovered: list[DiscoveredSensor] = []
    failure_count = 0

    for spec in specs:
        try:
            sv = await client.get_value(spec.address, spec)
            discovered.append(DiscoveredSensor(
                spec=spec, value=sv.value, readable=True
            ))
            failure_count = 0

        except Exception as exc:
            failure_count += 1
            _LOGGER.debug(
                "Failed to read 0x%04X '%s': %s", spec.address, spec.title, exc
            )
            discovered.append(DiscoveredSensor(
                spec=spec, value=None, readable=False
            ))

            # After 5 consecutive failures, the connection is dead
            if failure_count >= 5:
                raise FroelingConnectionError(
                    f"Connection lost after reading {len(discovered)} of "
                    f"{len(specs)} sensors. Last successful: "
                    f"'{discovered[-1].spec.title if discovered else 'none'}'. "
                    f"Error: {exc}"
                )

    return discovered


def _sensors_to_select_options(
    sensors: list[DiscoveredSensor],
) -> tuple[list[SelectOptionDict], list[str]]:
    """Convert discovered sensors to HA select options with live values.

    Returns a tuple of (all_options, preselected_values).

    Filtering:
    - Temperature sensors reading 0.0°C are included but NOT preselected
      (likely no physical sensor, but user can still opt in)
    - Unreadable sensors are included but NOT preselected

    Preselection:
    - Only sensors with a non-zero readable value are preselected

    Formatting:
    - Integer values shown without decimals (e.g., "16497" not "16497.0")
    - Float values shown with 1 decimal (e.g., "65.3")
    - Duplicate titles get an address suffix [0x0004]
    """
    title_counts = Counter(s.spec.title for s in sensors)

    options: list[SelectOptionDict] = []
    preselected: list[str] = []

    for sensor in sensors:
        title = sensor.spec.title
        addr_hex = f"0x{sensor.spec.address:04X}"

        # Format the label: show value if available, otherwise just name + unit
        unit = sensor.spec.unit.strip() if sensor.spec.unit else ""

        if sensor.readable and sensor.value is not None:
            # Use integer display when the value has no fractional part
            if sensor.value == int(sensor.value):
                val_str = f"{int(sensor.value)}"
            else:
                val_str = f"{sensor.value:.1f}"

            label = f"{title} = {val_str} {unit}".rstrip() if unit else f"{title} = {val_str}"
        elif unit:
            # No value but we know the unit -- show name + unit
            label = f"{title} ({unit})"
        else:
            label = title

        # Append address for duplicate titles
        if title_counts[title] > 1:
            label = f"{label}  [{addr_hex}]"

        options.append(SelectOptionDict(value=addr_hex, label=label))

        # Preselect only sensors with non-zero readable values
        if sensor.readable and sensor.value is not None and sensor.value != 0.0:
            preselected.append(addr_hex)

    return options, preselected


def _writable_params_to_select_options(
    params: list[WritableParameter],
) -> list[SelectOptionDict]:
    """Convert writable parameters to HA select options for the parameter step.

    Each option label shows the parameter's current value and limits so the user
    can make an informed decision about which parameters to expose as controls.

    Formatting rules:
    - Numeric parameters (digits > 0 or min != max): "Title = <val> unit (min: X, max: Y)"
    - Choice parameters (small integer range, e.g. 0-3): "Title = <val> (min: X, max: Y)"
    - Values are shown as integers when they have no fractional part.
    """
    options: list[SelectOptionDict] = []

    for param in params:
        addr_hex = f"0x{param.address:04X}"

        # Format the current value smartly: integer for whole numbers
        if param.value == int(param.value):
            val_str = str(int(param.value))
        else:
            val_str = f"{param.value:.1f}"

        # Format min/max the same way
        if param.min_value == int(param.min_value):
            min_str = str(int(param.min_value))
        else:
            min_str = f"{param.min_value:.1f}"

        if param.max_value == int(param.max_value):
            max_str = str(int(param.max_value))
        else:
            max_str = f"{param.max_value:.1f}"

        # Build label: include unit if present
        unit = param.unit.strip() if param.unit else ""
        if unit:
            label = f"{param.title} = {val_str} {unit} (min: {min_str}, max: {max_str})"
        else:
            label = f"{param.title} = {val_str} (min: {min_str}, max: {max_str})"

        options.append(SelectOptionDict(value=addr_hex, label=label))

    return options


def _create_client(data: dict[str, Any]) -> FroelingClient:
    """Create a FroelingClient from config entry data."""
    conn_type = data.get(CONF_CONNECTION_TYPE, CONN_TYPE_NETWORK)
    if conn_type == CONN_TYPE_SERIAL:
        return FroelingClient(serial_device=data[CONF_SERIAL_DEVICE])
    else:
        return FroelingClient(host=data[CONF_HOST], port=data[CONF_PORT])


# ---------------------------------------------------------------------------
# Config flow
# ---------------------------------------------------------------------------

class FroelingConfigFlow(ConfigFlow, domain=DOMAIN):
    """Multi-step config flow: connection type -> details -> access mode -> sensor/parameter selection."""

    VERSION = 1

    def __init__(self) -> None:
        # Connection settings collected in the network/serial steps
        self._device_name: str = DEFAULT_DEVICE_NAME
        self._conn_type: str = CONN_TYPE_NETWORK
        self._host: str = ""
        self._port: int = 0
        self._serial_device: str = ""

        # Sensors discovered during network/serial step
        self._discovered: list[DiscoveredSensor] = []

        # Write mode flag: set True in async_step_read_write / async_step_confirm_write
        self._write_enabled: bool = False

        # Writable parameters discovered in async_step_confirm_write
        self._writable_params: list[WritableParameter] = []

        # Error detail for description_placeholders in error forms
        self._error_detail: str = ""

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> FroelingOptionsFlow:
        return FroelingOptionsFlow(config_entry)

    # ------------------------------------------------------------------
    # Step 1: Connection type menu
    # ------------------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show menu to choose between network and USB serial connection."""
        return self.async_show_menu(
            step_id="user",
            menu_options=["network", "serial"],
        )

    # ------------------------------------------------------------------
    # Step 1a: Network (TCP) connection details
    # ------------------------------------------------------------------

    async def async_step_network(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect host, port, and device name for a TCP connection.

        After successful sensor discovery, routes to the access_mode step
        rather than directly to sensors (new in v0.7.0).
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            self._conn_type = CONN_TYPE_NETWORK
            self._device_name = user_input[CONF_DEVICE_NAME]
            self._host = user_input[CONF_HOST]
            self._port = user_input[CONF_PORT]

            client = FroelingClient(host=self._host, port=self._port)
            try:
                await client.connect()
                self._discovered = await _validate_and_discover(client)
            except FroelingConnectionError as exc:
                _LOGGER.error("Connection error: %s", exc)
                errors["base"] = "cannot_connect"
                self._error_detail = str(exc)
            except Exception as exc:
                _LOGGER.exception("Unexpected error during network setup")
                errors["base"] = "unknown"
                self._error_detail = str(exc)
            else:
                if not self._discovered:
                    errors["base"] = "no_sensors"
                else:
                    # Route to access mode selection (new step)
                    return await self.async_step_access_mode()
            finally:
                await client.disconnect()

        schema = vol.Schema({
            vol.Required(CONF_DEVICE_NAME, default=self._device_name or DEFAULT_DEVICE_NAME): str,
            vol.Required(CONF_HOST, default=self._host or vol.UNDEFINED): str,
            vol.Required(CONF_PORT, default=self._port or vol.UNDEFINED): int,
        })

        return self.async_show_form(
            step_id="network", data_schema=schema, errors=errors,
            description_placeholders={"error_detail": self._error_detail},
        )

    # ------------------------------------------------------------------
    # Step 1b: USB Serial connection details
    # ------------------------------------------------------------------

    async def async_step_serial(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect serial device path and device name for a USB connection.

        After successful sensor discovery, routes to the access_mode step
        rather than directly to sensors (new in v0.7.0).
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            self._conn_type = CONN_TYPE_SERIAL
            self._device_name = user_input[CONF_DEVICE_NAME]
            self._serial_device = user_input[CONF_SERIAL_DEVICE]

            client = FroelingClient(serial_device=self._serial_device)
            try:
                await client.connect()
                self._discovered = await _validate_and_discover(client)
            except FroelingConnectionError as exc:
                _LOGGER.error("Connection error: %s", exc)
                errors["base"] = "cannot_connect"
                self._error_detail = str(exc)
            except Exception as exc:
                _LOGGER.exception("Unexpected error during serial setup")
                errors["base"] = "unknown"
                self._error_detail = str(exc)
            else:
                if not self._discovered:
                    errors["base"] = "no_sensors"
                else:
                    # Route to access mode selection (new step)
                    return await self.async_step_access_mode()
            finally:
                await client.disconnect()

        schema = vol.Schema({
            vol.Required(CONF_DEVICE_NAME, default=self._device_name or DEFAULT_DEVICE_NAME): str,
            vol.Required(CONF_SERIAL_DEVICE, default=self._serial_device or vol.UNDEFINED): str,
        })

        return self.async_show_form(
            step_id="serial", data_schema=schema, errors=errors,
        )

    # ------------------------------------------------------------------
    # Step 2 (NEW): Access mode menu
    # ------------------------------------------------------------------

    async def async_step_access_mode(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show menu to choose between read-only and read/write access mode.

        Read-only is the recommended and safe default.  Read/write mode allows
        HA automations and scripts to change heater parameters, but requires
        an additional warning step to confirm the user understands the risks.
        """
        return self.async_show_menu(
            step_id="access_mode",
            menu_options=["read_only", "read_write"],
        )

    # ------------------------------------------------------------------
    # Step 2a (NEW): Read-only path
    # ------------------------------------------------------------------

    async def async_step_read_only(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """User chose read-only mode.  Clear write flag and proceed to sensors."""
        # Explicitly disable write mode
        self._write_enabled = False
        # Skip directly to sensor selection
        return await self.async_step_sensors()

    # ------------------------------------------------------------------
    # Step 2b (NEW): Read/write path – warning menu
    # ------------------------------------------------------------------

    async def async_step_read_write(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """User chose read/write mode.  Show a safety warning menu before proceeding.

        The warning presents two choices:
          - confirm_write  → proceed to parameter discovery and enable write mode
          - back_to_read_only → cancel write mode and fall back to read-only
        """
        return self.async_show_menu(
            step_id="read_write_warning",
            menu_options=["confirm_write", "back_to_read_only"],
        )

    # ------------------------------------------------------------------
    # Step 2b-i (NEW): Confirmed write mode – discover writable parameters
    # ------------------------------------------------------------------

    async def async_step_confirm_write(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """User confirmed write mode.  Connect, discover menu tree and writable parameters.

        This is a SEPARATE short-lived connection from the sensor discovery that
        happened in async_step_network / async_step_serial.  That connection was
        already disconnected before we reach here.  We open a new connection,
        walk the full menu tree, read the limits / current values for every
        writable parameter, then disconnect before continuing to sensor selection.

        The 'single connection' constraint applies within each phase: we never
        disconnect and reconnect while a sequence of commands is in progress.
        """
        # Build a fresh client using the previously stored connection details
        if self._conn_type == CONN_TYPE_SERIAL:
            client = FroelingClient(serial_device=self._serial_device)
        else:
            client = FroelingClient(host=self._host, port=self._port)

        try:
            await client.connect()

            # Walk the full menu tree (GET_MENU_LIST_FIRST / NEXT)
            _LOGGER.debug("config_flow: discovering menu tree for writable parameters…")
            menu_items = await client.discover_menu()
            _LOGGER.debug("config_flow: %d menu items discovered", len(menu_items))

            # Read GET_PARAMETER for each writable item to get value + limits
            self._writable_params = await client.get_writable_parameters(menu_items)
            _LOGGER.debug(
                "config_flow: %d writable parameters discovered",
                len(self._writable_params),
            )

        except Exception as exc:
            # If parameter discovery fails we do NOT abort – fall back to
            # read-only mode so the user is not blocked from completing setup.
            _LOGGER.warning(
                "config_flow: writable parameter discovery failed (%s); "
                "falling back to read-only mode", exc
            )
            self._write_enabled = False
            self._writable_params = []
            return await self.async_step_sensors()

        finally:
            # Always disconnect; the coordinator will open its own connection
            # when the entry is loaded.
            await client.disconnect()

        # Enable write mode now that we have successfully discovered parameters
        self._write_enabled = True

        return await self.async_step_sensors()

    # ------------------------------------------------------------------
    # Step 2b-ii (NEW): Back to read-only from warning screen
    # ------------------------------------------------------------------

    async def async_step_back_to_read_only(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """User changed their mind on the warning screen.  Use read-only mode."""
        self._write_enabled = False
        return await self.async_step_sensors()

    # ------------------------------------------------------------------
    # Step 3: Sensor selection (shared by both connection types)
    # ------------------------------------------------------------------

    async def async_step_sensors(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user select which sensors to enable.

        After the user submits the sensor selection:
        - If write mode is enabled AND writable parameters were discovered,
          route to the parameter selection step.
        - Otherwise, create the config entry directly.
        """
        if user_input is not None:
            selected = user_input.get(CONF_SELECTED_SENSORS, [])

            # Build unique_id from connection info
            if self._conn_type == CONN_TYPE_SERIAL:
                unique_id = f"serial:{self._serial_device}"
            else:
                unique_id = f"{self._host}:{self._port}"
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()

            # Store the sensor selection in flow state for when we create the entry
            self._selected_sensors = selected

            # If write mode is active and we have writable params, go to parameters
            if self._write_enabled and self._writable_params:
                return await self.async_step_parameters()

            # Otherwise create the entry now (read-only or no writable params found)
            return self._create_config_entry(selected_sensors=selected)

        # Build options and determine preselection
        options, preselected = _sensors_to_select_options(self._discovered)

        schema = vol.Schema({
            vol.Required(CONF_SELECTED_SENSORS, default=preselected): SelectSelector(
                SelectSelectorConfig(
                    options=options, multiple=True, mode=SelectSelectorMode.LIST,
                )
            ),
        })

        return self.async_show_form(step_id="sensors", data_schema=schema)

    # ------------------------------------------------------------------
    # Step 4 (NEW): Parameter selection (only in write mode)
    # ------------------------------------------------------------------

    async def async_step_parameters(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user choose which writable parameters to expose as controls.

        Shows a multi-select list of all writable parameters discovered from
        the heater's menu tree.  For safety, NONE are selected by default –
        the user must explicitly opt in to each parameter they want to control.

        Each option label shows: "Title = current_value unit (min: X, max: Y)"
        so the user can understand what they are enabling.
        """
        if user_input is not None:
            selected_params = user_input.get(CONF_SELECTED_PARAMETERS, [])
            # Create the entry with both sensor and parameter selections
            return self._create_config_entry(
                selected_sensors=getattr(self, "_selected_sensors", []),
                selected_params=selected_params,
            )

        # Build parameter select options from discovered writable parameters
        param_options = _writable_params_to_select_options(self._writable_params)

        schema = vol.Schema({
            # Default is an empty list – no parameters selected (safety-first)
            vol.Required(CONF_SELECTED_PARAMETERS, default=[]): SelectSelector(
                SelectSelectorConfig(
                    options=param_options, multiple=True, mode=SelectSelectorMode.LIST,
                )
            ),
        })

        return self.async_show_form(step_id="parameters", data_schema=schema)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _create_config_entry(
        self,
        selected_sensors: list[str],
        selected_params: list[str] | None = None,
    ) -> ConfigFlowResult:
        """Assemble the config entry data dict and create the entry.

        Parameters
        ----------
        selected_sensors:
            Hex-string addresses of sensors selected during the sensors step.
        selected_params:
            Hex-string addresses of writable parameters selected (may be None
            or empty when write mode is disabled).
        """
        data: dict[str, Any] = {
            CONF_DEVICE_NAME: self._device_name,
            CONF_CONNECTION_TYPE: self._conn_type,
            CONF_SELECTED_SENSORS: selected_sensors,
            CONF_WRITE_ENABLED: self._write_enabled,
            CONF_SELECTED_PARAMETERS: selected_params or [],
        }
        if self._conn_type == CONN_TYPE_SERIAL:
            data[CONF_SERIAL_DEVICE] = self._serial_device
        else:
            data[CONF_HOST] = self._host
            data[CONF_PORT] = self._port

        return self.async_create_entry(title=self._device_name, data=data)

    # ------------------------------------------------------------------
    # Reconfigure
    # ------------------------------------------------------------------

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Update connection settings from the Settings page."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()
        conn_type = entry.data.get(CONF_CONNECTION_TYPE, CONN_TYPE_NETWORK)

        if user_input is not None:
            client = _create_client(user_input | {CONF_CONNECTION_TYPE: conn_type})
            try:
                await client.connect()
                await client.check_connection()
                await client.disconnect()
            except Exception:
                errors["base"] = "cannot_connect"
            else:
                new_data = dict(entry.data)
                new_data.update(user_input)
                uid = (
                    f"serial:{user_input.get(CONF_SERIAL_DEVICE, '')}"
                    if conn_type == CONN_TYPE_SERIAL
                    else f"{user_input.get(CONF_HOST, '')}:{user_input.get(CONF_PORT, '')}"
                )
                return self.async_update_reload_and_abort(
                    entry, unique_id=uid,
                    title=new_data.get(CONF_DEVICE_NAME, entry.title),
                    data=new_data,
                )

        if conn_type == CONN_TYPE_SERIAL:
            schema = vol.Schema({
                vol.Required(CONF_SERIAL_DEVICE,
                             default=entry.data.get(CONF_SERIAL_DEVICE, "")): str,
            })
        else:
            schema = vol.Schema({
                vol.Required(CONF_HOST, default=entry.data.get(CONF_HOST, "")): str,
                vol.Required(CONF_PORT, default=entry.data.get(CONF_PORT, 0)): int,
            })

        return self.async_show_form(
            step_id="reconfigure", data_schema=schema, errors=errors,
        )


# ---------------------------------------------------------------------------
# Options flow
# ---------------------------------------------------------------------------

class FroelingOptionsFlow(OptionsFlow):
    """Options flow for polling interval and sensor re-selection."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show options: polling interval, sensor re-selection, and (if write mode) parameter re-selection."""
        if user_input is not None:
            new_options = {
                CONF_SCAN_INTERVAL: user_input.get(
                    CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                ),
            }
            new_data = dict(self._config_entry.data)
            # Update sensor selection if present in the form
            if CONF_SELECTED_SENSORS in user_input:
                new_data[CONF_SELECTED_SENSORS] = user_input[CONF_SELECTED_SENSORS]
            # Update parameter selection if present in the form (write mode)
            if CONF_SELECTED_PARAMETERS in user_input:
                new_data[CONF_SELECTED_PARAMETERS] = user_input[CONF_SELECTED_PARAMETERS]
            self.hass.config_entries.async_update_entry(
                self._config_entry, data=new_data
            )
            return self.async_create_entry(title="", data=new_options)

        current_interval = self._config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )
        current_selected = self._config_entry.data.get(CONF_SELECTED_SENSORS, [])
        current_params = self._config_entry.data.get(CONF_SELECTED_PARAMETERS, [])
        write_enabled = self._config_entry.data.get(CONF_WRITE_ENABLED, False)

        # Try to discover sensors for re-selection (same approach as v0.5.1)
        sensor_options: list[SelectOptionDict] = []
        preselected: list[str] = current_selected
        try:
            client = _create_client(self._config_entry.data)
            try:
                await client.connect()
                discovered = await _validate_and_discover(client)
                sensor_options, _ = _sensors_to_select_options(discovered)
            finally:
                await client.disconnect()
        except Exception:
            _LOGGER.warning("Options flow: could not discover sensors")

        schema_dict: dict[vol.Marker, Any] = {
            vol.Required(CONF_SCAN_INTERVAL, default=current_interval): vol.All(
                vol.Coerce(int),
                vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
            ),
        }

        if sensor_options:
            schema_dict[vol.Required(
                CONF_SELECTED_SENSORS, default=preselected,
            )] = SelectSelector(
                SelectSelectorConfig(
                    options=sensor_options, multiple=True, mode=SelectSelectorMode.LIST,
                )
            )

        # Only show the parameter selector if write mode is enabled in the entry
        if write_enabled:
            # Build parameter options from any previously-stored parameters.
            # We cannot re-discover here without another connection; use the
            # stored list to allow de-selecting parameters but not adding new ones.
            # Showing at least the currently-selected parameters is better than nothing.
            if current_params:
                # Generate minimal option labels from the stored hex addresses
                # (we don't have the full parameter metadata here without connecting)
                param_options = [
                    SelectOptionDict(value=addr, label=addr)
                    for addr in current_params
                ]
                schema_dict[vol.Required(
                    CONF_SELECTED_PARAMETERS, default=current_params,
                )] = SelectSelector(
                    SelectSelectorConfig(
                        options=param_options, multiple=True, mode=SelectSelectorMode.LIST,
                    )
                )

        return self.async_show_form(
            step_id="init", data_schema=vol.Schema(schema_dict),
        )
