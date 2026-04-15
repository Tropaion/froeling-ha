"""Config flow for the Fröling Heater integration.

Setup flow:
  1. Connection type menu (Network / USB Serial)
  2. Connection details form (host+port or serial device + device name)
  3. Progress: scanning for sensors...
  4. Sensor selection form
  5. Access mode menu (Read-only / Read-Write with warning)
  6. If Read-Write: progress scanning for parameters...
  7. Parameter selection form
  8. Config entry created
"""

from __future__ import annotations

import asyncio
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
# Discovery helpers
# ---------------------------------------------------------------------------

@dataclass
class DiscoveredSensor:
    """A sensor discovered from the heater with its current value."""
    spec: ValueSpec
    value: float | None
    readable: bool


async def _validate_and_discover(client: FroelingClient) -> list[DiscoveredSensor]:
    """Discover sensors and read values on a single connection.

    Client must already be connected. Uses the proven v0.5.1 approach:
    check -> discover -> read values, all on the same connection.
    """
    ok = await client.check_connection()
    if not ok:
        raise FroelingConnectionError("Heater did not respond to CHECK command")

    specs = await client.discover_sensors()
    _LOGGER.debug("Discovered %d sensor specs, reading values...", len(specs))

    discovered: list[DiscoveredSensor] = []
    failure_count = 0

    for spec in specs:
        try:
            sv = await client.get_value(spec.address, spec)
            discovered.append(DiscoveredSensor(spec=spec, value=sv.value, readable=True))
            failure_count = 0
        except Exception as exc:
            failure_count += 1
            _LOGGER.debug("Failed to read 0x%04X '%s': %s", spec.address, spec.title, exc)
            discovered.append(DiscoveredSensor(spec=spec, value=None, readable=False))
            if failure_count >= 5:
                raise FroelingConnectionError(
                    f"Connection lost after reading {len(discovered)} of "
                    f"{len(specs)} sensors. Error: {exc}"
                )

    return discovered


def _sensors_to_select_options(
    sensors: list[DiscoveredSensor],
) -> tuple[list[SelectOptionDict], list[str]]:
    """Convert sensors to select options. Returns (options, preselected)."""
    title_counts = Counter(s.spec.title for s in sensors)
    options: list[SelectOptionDict] = []
    preselected: list[str] = []

    for sensor in sensors:
        title = sensor.spec.title
        addr_hex = f"0x{sensor.spec.address:04X}"
        unit = sensor.spec.unit.strip() if sensor.spec.unit else ""

        if sensor.readable and sensor.value is not None:
            if sensor.value == int(sensor.value):
                val_str = f"{int(sensor.value)}"
            else:
                val_str = f"{sensor.value:.1f}"
            label = f"{title} = {val_str} {unit}".rstrip() if unit else f"{title} = {val_str}"
        elif unit:
            label = f"{title} ({unit})"
        else:
            label = title

        if title_counts[title] > 1:
            label = f"{label}  [{addr_hex}]"

        options.append(SelectOptionDict(value=addr_hex, label=label))
        if sensor.readable and sensor.value is not None and sensor.value != 0.0:
            preselected.append(addr_hex)

    return options, preselected


def _params_to_select_options(params: list[WritableParameter]) -> list[SelectOptionDict]:
    """Convert writable parameters to select options."""
    options: list[SelectOptionDict] = []
    for param in params:
        addr_hex = f"0x{param.address:04X}"
        val_str = str(int(param.value)) if param.value == int(param.value) else f"{param.value:.1f}"
        min_str = str(int(param.min_value)) if param.min_value == int(param.min_value) else f"{param.min_value:.1f}"
        max_str = str(int(param.max_value)) if param.max_value == int(param.max_value) else f"{param.max_value:.1f}"
        unit = param.unit.strip() if param.unit else ""
        label = f"{param.title} = {val_str}"
        if unit:
            label += f" {unit}"
        label += f"  (min: {min_str}, max: {max_str})"
        options.append(SelectOptionDict(value=addr_hex, label=label))
    return options


def _create_client(data: dict[str, Any]) -> FroelingClient:
    """Create a FroelingClient from config data."""
    conn_type = data.get(CONF_CONNECTION_TYPE, CONN_TYPE_NETWORK)
    if conn_type == CONN_TYPE_SERIAL:
        return FroelingClient(serial_device=data[CONF_SERIAL_DEVICE])
    return FroelingClient(host=data[CONF_HOST], port=data[CONF_PORT])


# ---------------------------------------------------------------------------
# Config flow
# ---------------------------------------------------------------------------

class FroelingConfigFlow(ConfigFlow, domain=DOMAIN):
    """Multi-step config flow with progress indicators for long operations."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize flow state."""
        self._device_name: str = DEFAULT_DEVICE_NAME
        self._conn_type: str = CONN_TYPE_NETWORK
        self._host: str = ""
        self._port: int = 0
        self._serial_device: str = ""
        self._discovered: list[DiscoveredSensor] = []
        self._write_enabled: bool = False
        self._writable_params: list[WritableParameter] = []
        self._selected_sensors: list[str] = []
        # Background task references for progress steps
        self._sensor_discover_task: asyncio.Task | None = None
        self._param_discover_task: asyncio.Task | None = None
        self._discover_error: str = ""

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> FroelingOptionsFlow:
        return FroelingOptionsFlow(config_entry)

    def _make_client(self) -> FroelingClient:
        """Create a client from current flow state."""
        if self._conn_type == CONN_TYPE_SERIAL:
            return FroelingClient(serial_device=self._serial_device)
        return FroelingClient(host=self._host, port=self._port)

    # ------------------------------------------------------------------
    # Step 1: Connection type
    # ------------------------------------------------------------------

    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="user",
            menu_options=["network", "serial"],
        )

    # ------------------------------------------------------------------
    # Step 2a: Network connection details
    # ------------------------------------------------------------------

    async def async_step_network(self, user_input=None) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            self._conn_type = CONN_TYPE_NETWORK
            self._device_name = user_input[CONF_DEVICE_NAME]
            self._host = user_input[CONF_HOST]
            self._port = user_input[CONF_PORT]
            # Go to sensor scanning progress step
            return await self.async_step_discover_sensors()

        schema = vol.Schema({
            vol.Required(CONF_DEVICE_NAME, default=DEFAULT_DEVICE_NAME): str,
            vol.Required(CONF_HOST): str,
            vol.Required(CONF_PORT): int,
        })
        return self.async_show_form(step_id="network", data_schema=schema, errors=errors)

    # ------------------------------------------------------------------
    # Step 2b: USB Serial connection details
    # ------------------------------------------------------------------

    async def async_step_serial(self, user_input=None) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            self._conn_type = CONN_TYPE_SERIAL
            self._device_name = user_input[CONF_DEVICE_NAME]
            self._serial_device = user_input[CONF_SERIAL_DEVICE]
            return await self.async_step_discover_sensors()

        schema = vol.Schema({
            vol.Required(CONF_DEVICE_NAME, default=DEFAULT_DEVICE_NAME): str,
            vol.Required(CONF_SERIAL_DEVICE): str,
        })
        return self.async_show_form(step_id="serial", data_schema=schema, errors=errors)

    # ------------------------------------------------------------------
    # Step 3: Progress - scanning for sensors
    # ------------------------------------------------------------------

    async def _async_do_sensor_discovery(self) -> None:
        """Background task: connect, discover sensors, read values."""
        client = self._make_client()
        try:
            await client.connect()
            self._discovered = await _validate_and_discover(client)
        finally:
            await client.disconnect()

    async def async_step_discover_sensors(self, user_input=None) -> ConfigFlowResult:
        """Show progress spinner while scanning for sensors."""
        if not self._sensor_discover_task:
            self._sensor_discover_task = self.hass.async_create_task(
                self._async_do_sensor_discovery()
            )

        if not self._sensor_discover_task.done():
            return self.async_show_progress(
                step_id="discover_sensors",
                progress_action="discovering_sensors",
                progress_task=self._sensor_discover_task,
                description_placeholders={
                    "info": "Connecting to the heater and reading all available sensors. This may take up to 60 seconds..."
                },
            )

        # Task completed - check result
        try:
            await self._sensor_discover_task
        except FroelingConnectionError as exc:
            self._discover_error = str(exc)
            self._sensor_discover_task = None
            return self.async_show_progress_done(next_step_id="discover_failed")
        except Exception as exc:
            self._discover_error = str(exc)
            self._sensor_discover_task = None
            return self.async_show_progress_done(next_step_id="discover_failed")

        self._sensor_discover_task = None

        if not self._discovered:
            self._discover_error = "No sensors found on the heater."
            return self.async_show_progress_done(next_step_id="discover_failed")

        return self.async_show_progress_done(next_step_id="sensors")

    async def async_step_discover_failed(self, user_input=None) -> ConfigFlowResult:
        """Show error after failed discovery and let user retry."""
        return self.async_abort(reason="discover_failed")

    # ------------------------------------------------------------------
    # Step 4: Sensor selection
    # ------------------------------------------------------------------

    async def async_step_sensors(self, user_input=None) -> ConfigFlowResult:
        if user_input is not None:
            self._selected_sensors = user_input.get(CONF_SELECTED_SENSORS, [])
            # Go to access mode selection
            return await self.async_step_access_mode()

        options, preselected = _sensors_to_select_options(self._discovered)
        schema = vol.Schema({
            vol.Required(CONF_SELECTED_SENSORS, default=preselected): SelectSelector(
                SelectSelectorConfig(options=options, multiple=True, mode=SelectSelectorMode.LIST)
            ),
        })
        return self.async_show_form(step_id="sensors", data_schema=schema)

    # ------------------------------------------------------------------
    # Step 5: Access mode (read-only or read/write)
    # ------------------------------------------------------------------

    async def async_step_access_mode(self, user_input=None) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="access_mode",
            menu_options=["read_only", "read_write"],
        )

    async def async_step_read_only(self, user_input=None) -> ConfigFlowResult:
        """Read-only selected: create entry without parameters."""
        self._write_enabled = False
        return await self._create_config_entry()

    async def async_step_read_write(self, user_input=None) -> ConfigFlowResult:
        """Read/write selected: show warning, then scan for parameters.

        NOTE: async_show_menu requires that step_id matches an
        async_step_<step_id> method. We reuse 'read_write' as the step_id
        so this method handles both the initial call and the menu display.
        The menu_options map to async_step_confirm_write / async_step_back_to_read_only.
        """
        return self.async_show_menu(
            step_id="read_write",
            menu_options=["confirm_write", "back_to_read_only"],
        )

    async def async_step_back_to_read_only(self, user_input=None) -> ConfigFlowResult:
        self._write_enabled = False
        return await self._create_config_entry()

    async def async_step_confirm_write(self, user_input=None) -> ConfigFlowResult:
        """User confirmed write mode: start parameter discovery."""
        self._write_enabled = True
        return await self.async_step_discover_parameters()

    # ------------------------------------------------------------------
    # Step 6: Progress - scanning for writable parameters
    # ------------------------------------------------------------------

    async def _async_do_parameter_discovery(self) -> None:
        """Background task: connect, discover menu tree, read writable params."""
        # Brief delay to let the serial bridge recover from the sensor
        # discovery connection (EE10 and similar converters need time
        # to accept a new TCP connection after the previous one closed)
        await asyncio.sleep(1.0)

        client = self._make_client()
        try:
            await client.connect()
            # Sync the protocol
            await client.check_connection()
            # Discover the full menu tree
            menu_items = await client.discover_menu()
            # Read current values for writable parameters
            self._writable_params = await client.get_writable_parameters(menu_items)
        finally:
            await client.disconnect()

    async def async_step_discover_parameters(self, user_input=None) -> ConfigFlowResult:
        """Show progress spinner while scanning for parameters."""
        if not self._param_discover_task:
            self._param_discover_task = self.hass.async_create_task(
                self._async_do_parameter_discovery()
            )

        if not self._param_discover_task.done():
            return self.async_show_progress(
                step_id="discover_parameters",
                progress_action="discovering_parameters",
                progress_task=self._param_discover_task,
                description_placeholders={
                    "info": "Reading the heater's menu tree to find writable parameters..."
                },
            )

        try:
            await self._param_discover_task
        except Exception as exc:
            _LOGGER.error("Parameter discovery failed: %s", exc)
            self._discover_error = str(exc)
            self._param_discover_task = None
            return self.async_show_progress_done(next_step_id="discover_params_failed")

        self._param_discover_task = None

        if not self._writable_params:
            # No writable parameters found -- create entry without them
            _LOGGER.info("No writable parameters found, creating read-only entry")
            self._write_enabled = False
            return self.async_show_progress_done(next_step_id="create_entry")

        return self.async_show_progress_done(next_step_id="parameters")

    async def async_step_discover_params_failed(self, user_input=None) -> ConfigFlowResult:
        """Parameter discovery failed: fall back to read-only."""
        _LOGGER.warning("Parameter discovery failed, falling back to read-only")
        self._write_enabled = False
        return await self._create_config_entry()

    async def async_step_create_entry(self, user_input=None) -> ConfigFlowResult:
        """Helper step called from progress_done to create the entry."""
        return await self._create_config_entry()

    # ------------------------------------------------------------------
    # Step 7: Parameter selection
    # ------------------------------------------------------------------

    async def async_step_parameters(self, user_input=None) -> ConfigFlowResult:
        if user_input is not None:
            selected = user_input.get(CONF_SELECTED_PARAMETERS, [])
            return await self._create_config_entry(selected_parameters=selected)

        options = _params_to_select_options(self._writable_params)
        schema = vol.Schema({
            vol.Required(CONF_SELECTED_PARAMETERS, default=[]): SelectSelector(
                SelectSelectorConfig(options=options, multiple=True, mode=SelectSelectorMode.LIST)
            ),
        })
        return self.async_show_form(step_id="parameters", data_schema=schema)

    # ------------------------------------------------------------------
    # Entry creation
    # ------------------------------------------------------------------

    async def _create_config_entry(self, selected_parameters: list[str] | None = None) -> ConfigFlowResult:
        """Build and create the config entry from accumulated flow state."""
        if self._conn_type == CONN_TYPE_SERIAL:
            unique_id = f"serial:{self._serial_device}"
        else:
            unique_id = f"{self._host}:{self._port}"
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()

        data: dict[str, Any] = {
            CONF_DEVICE_NAME: self._device_name,
            CONF_CONNECTION_TYPE: self._conn_type,
            CONF_SELECTED_SENSORS: self._selected_sensors,
            CONF_WRITE_ENABLED: self._write_enabled,
            CONF_SELECTED_PARAMETERS: selected_parameters or [],
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

    async def async_step_reconfigure(self, user_input=None) -> ConfigFlowResult:
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
                vol.Required(CONF_SERIAL_DEVICE, default=entry.data.get(CONF_SERIAL_DEVICE, "")): str,
            })
        else:
            schema = vol.Schema({
                vol.Required(CONF_HOST, default=entry.data.get(CONF_HOST, "")): str,
                vol.Required(CONF_PORT, default=entry.data.get(CONF_PORT, 0)): int,
            })
        return self.async_show_form(step_id="reconfigure", data_schema=schema, errors=errors)


# ---------------------------------------------------------------------------
# Options flow
# ---------------------------------------------------------------------------

class FroelingOptionsFlow(OptionsFlow):
    """Options for polling interval and sensor re-selection."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None) -> ConfigFlowResult:
        if user_input is not None:
            new_options = {
                CONF_SCAN_INTERVAL: user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            }
            if CONF_SELECTED_SENSORS in user_input or CONF_SELECTED_PARAMETERS in user_input:
                new_data = dict(self._config_entry.data)
                if CONF_SELECTED_SENSORS in user_input:
                    new_data[CONF_SELECTED_SENSORS] = user_input[CONF_SELECTED_SENSORS]
                if CONF_SELECTED_PARAMETERS in user_input:
                    new_data[CONF_SELECTED_PARAMETERS] = user_input[CONF_SELECTED_PARAMETERS]
                self.hass.config_entries.async_update_entry(self._config_entry, data=new_data)
            return self.async_create_entry(title="", data=new_options)

        current_interval = self._config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        current_selected_sensors = self._config_entry.data.get(CONF_SELECTED_SENSORS, [])
        current_selected_params = self._config_entry.data.get(CONF_SELECTED_PARAMETERS, [])

        # Try to discover for re-selection
        sensor_options: list[SelectOptionDict] = []
        param_options: list[SelectOptionDict] = []
        try:
            client = _create_client(self._config_entry.data)
            try:
                await client.connect()
                # Sensor discovery
                discovered = await _validate_and_discover(client)
                sensor_options, _ = _sensors_to_select_options(discovered)
                # Parameter discovery (if write mode enabled)
                if self._config_entry.data.get(CONF_WRITE_ENABLED, False):
                    menu_items = await client.discover_menu()
                    writable = await client.get_writable_parameters(menu_items)
                    param_options = _params_to_select_options(writable)
            finally:
                await client.disconnect()
        except Exception:
            _LOGGER.warning("Options flow: could not discover sensors/parameters")

        schema_dict: dict[vol.Marker, Any] = {
            vol.Required(CONF_SCAN_INTERVAL, default=current_interval): vol.All(
                vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
            ),
        }
        if sensor_options:
            schema_dict[vol.Required(CONF_SELECTED_SENSORS, default=current_selected_sensors)] = SelectSelector(
                SelectSelectorConfig(options=sensor_options, multiple=True, mode=SelectSelectorMode.LIST)
            )
        if param_options:
            schema_dict[vol.Required(CONF_SELECTED_PARAMETERS, default=current_selected_params)] = SelectSelector(
                SelectSelectorConfig(options=param_options, multiple=True, mode=SelectSelectorMode.LIST)
            )
        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema_dict))
