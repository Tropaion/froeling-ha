"""Config flow for the Fröling Heater integration.

Setup flow:
  1. async_step_user          -> menu: network / serial
  2. async_step_network       -> form: host, port, name
  3. async_step_discover_sensors -> progress spinner + background task
  4. async_step_sensors       -> form: select sensors
  5. async_step_access_mode   -> form: write mode checkbox
  6. async_step_discover_params -> progress spinner + background task (if write)
  7. async_step_parameters    -> form: select parameters
  8. entry created

IMPORTANT: Every async_show_progress(step_id=X) requires a method
async_step_X. Every async_show_progress_done(next_step_id=Y) requires
a method async_step_Y.
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
    CONF_PARAMETER_TITLES,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_SELECTED_PARAMETERS,
    CONF_SELECTED_SENSORS,
    CONF_SENSOR_SPECS,
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
    spec: ValueSpec
    value: float | None
    readable: bool


async def _validate_and_discover(client: FroelingClient) -> list[DiscoveredSensor]:
    """Single-connection sensor discovery (proven v0.5.1 approach)."""
    ok = await client.check_connection()
    if not ok:
        raise FroelingConnectionError("Heater did not respond to CHECK command")

    specs = await client.discover_sensors()
    discovered: list[DiscoveredSensor] = []
    failure_count = 0

    for spec in specs:
        try:
            sv = await client.get_value(spec.address, spec)
            discovered.append(DiscoveredSensor(spec=spec, value=sv.value, readable=True))
            failure_count = 0
        except Exception as exc:
            failure_count += 1
            discovered.append(DiscoveredSensor(spec=spec, value=None, readable=False))
            if failure_count >= 5:
                raise FroelingConnectionError(
                    f"Connection lost after reading {len(discovered)} of {len(specs)} sensors: {exc}"
                )
    return discovered


def _sensors_to_select_options(sensors: list[DiscoveredSensor]) -> tuple[list[SelectOptionDict], list[str]]:
    title_counts = Counter(s.spec.title for s in sensors)
    options: list[SelectOptionDict] = []
    preselected: list[str] = []

    for sensor in sensors:
        title = sensor.spec.title
        addr_hex = f"0x{sensor.spec.address:04X}"
        unit = sensor.spec.unit.strip() if sensor.spec.unit else ""

        if sensor.readable and sensor.value is not None:
            val_str = str(int(sensor.value)) if sensor.value == int(sensor.value) else f"{sensor.value:.1f}"
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


def _format_param_value(param: WritableParameter) -> str:
    """Format a parameter's current value with known labels where possible.

    Uses the same KNOWN_OPTION_LABELS from select.py to show readable values
    in the config flow selection list (e.g., "Betriebsart = Übergangsbetrieb"
    instead of "Betriebsart = 1").
    """
    # Import the label lookup from select.py
    from .select import _get_option_labels

    int_val = int(param.value) if param.value == int(param.value) else None
    labels = _get_option_labels(param)

    if labels and int_val is not None and int_val in labels:
        return labels[int_val]

    # No known label -- show numeric value
    if param.value == int(param.value):
        return str(int(param.value))
    return f"{param.value:.1f}"


def _params_to_select_options(params: list[WritableParameter]) -> list[SelectOptionDict]:
    """Convert writable parameters to select options with readable labels."""
    options: list[SelectOptionDict] = []
    for param in params:
        addr_hex = f"0x{param.address:04X}"
        val_str = _format_param_value(param)
        min_str = str(int(param.min_value)) if param.min_value == int(param.min_value) else f"{param.min_value:.1f}"
        max_str = str(int(param.max_value)) if param.max_value == int(param.max_value) else f"{param.max_value:.1f}"
        unit = param.unit.strip() if param.unit else ""
        label = f"{param.title} = {val_str}"
        if unit:
            label += f" {unit}"
        label += f"  (min: {min_str}, max: {max_str})"
        options.append(SelectOptionDict(value=addr_hex, label=label))
    return options


# ---------------------------------------------------------------------------
# Parameter categorization (Basic vs Expert)
# ---------------------------------------------------------------------------

# Keywords in parameter titles that indicate expert/internal settings.
# Parameters matching ANY of these are hidden by default.
_EXPERT_KEYWORDS: list[str] = [
    # Calibration / controller tuning
    "Regler", "Kp", "Tn", "Td", "Abtastrate", "Filterkonstante",
    "Proportionalfaktor", "Nachstellzeit", "Reglerverstärkung",
    # Internal sensor/output assignments
    "Welcher Fühler", "Welche Pumpe", "Welche Ausgang",
    # System internals
    "Vorgabewerte übernehmen", "Standardwerte übernehmen",
    "Modem vorhanden", "Speicherzyklus des Datenloggers",
    "Display mit Adresse", "Funktion des Bediengerätes",
    # Combustion internals
    "O2-Regler", "O2 Regler", "O2 Soll", "O2 Überwachung",
    "Lambdasonden", "Saugzug Min", "Saugzug Max",
    "Saugzug beim", "Einschubperiode", "Einschubzeit",
    "Maximaler Einschub", "Minimaler Einschub",
    "Einschub beim", "Sicherheitszeit", "WOS Laufzeit",
    "Überwachungs Fenster", "Luftmenge welche",
    "Maximale Abweichung", "Startverzögerung für O2",
    # Ash / cleaning internals
    "Zyklus der Ascheaustragung", "Laufzeit der Ascheschnecke",
    "Absperrschieber", "Mindestfahrweg",
    # Fuel internals
    "Kein Einschub wenn", "Restsauerstoffgehalt, über dem",
    "Einflussfaktor für O2",
    # Pellets internals
    "Nachfüllen des Zyklons",
]


def _is_expert_param(param: WritableParameter) -> bool:
    """Check if a parameter is an expert/internal setting.

    Returns True if the parameter title contains any expert keyword.
    These parameters are hidden by default in the selection list.
    """
    title = param.title
    return any(kw in title for kw in _EXPERT_KEYWORDS)


def _create_client_from_data(data: dict[str, Any]) -> FroelingClient:
    conn_type = data.get(CONF_CONNECTION_TYPE, CONN_TYPE_NETWORK)
    if conn_type == CONN_TYPE_SERIAL:
        return FroelingClient(serial_device=data[CONF_SERIAL_DEVICE])
    return FroelingClient(host=data[CONF_HOST], port=data[CONF_PORT])


# ---------------------------------------------------------------------------
# Config flow
# ---------------------------------------------------------------------------

class FroelingConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._device_name: str = DEFAULT_DEVICE_NAME
        self._conn_type: str = CONN_TYPE_NETWORK
        self._host: str = ""
        self._port: int = 0
        self._serial_device: str = ""
        self._discovered: list[DiscoveredSensor] = []
        self._write_enabled: bool = False
        self._writable_params: list[WritableParameter] = []
        self._selected_sensors: list[str] = []
        # Background tasks for progress steps
        self._task: asyncio.Task | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> FroelingOptionsFlow:
        return FroelingOptionsFlow(config_entry)

    def _make_client(self) -> FroelingClient:
        if self._conn_type == CONN_TYPE_SERIAL:
            return FroelingClient(serial_device=self._serial_device)
        return FroelingClient(host=self._host, port=self._port)

    # ------------------------------------------------------------------
    # Step 1: Connection type menu
    # ------------------------------------------------------------------

    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="user", menu_options=["network", "serial"])

    # ------------------------------------------------------------------
    # Step 2a: Network form
    # ------------------------------------------------------------------

    async def async_step_network(self, user_input=None) -> ConfigFlowResult:
        if user_input is not None:
            self._conn_type = CONN_TYPE_NETWORK
            self._device_name = user_input[CONF_DEVICE_NAME]
            self._host = user_input[CONF_HOST]
            self._port = user_input[CONF_PORT]
            # Go to sensor discovery progress step
            return await self.async_step_discover_sensors()

        schema = vol.Schema({
            vol.Required(CONF_DEVICE_NAME, default=DEFAULT_DEVICE_NAME): str,
            vol.Required(CONF_HOST): str,
            vol.Required(CONF_PORT): int,
        })
        return self.async_show_form(step_id="network", data_schema=schema)

    # ------------------------------------------------------------------
    # Step 2b: Serial form
    # ------------------------------------------------------------------

    async def async_step_serial(self, user_input=None) -> ConfigFlowResult:
        if user_input is not None:
            self._conn_type = CONN_TYPE_SERIAL
            self._device_name = user_input[CONF_DEVICE_NAME]
            self._serial_device = user_input[CONF_SERIAL_DEVICE]
            return await self.async_step_discover_sensors()

        schema = vol.Schema({
            vol.Required(CONF_DEVICE_NAME, default=DEFAULT_DEVICE_NAME): str,
            vol.Required(CONF_SERIAL_DEVICE): str,
        })
        return self.async_show_form(step_id="serial", data_schema=schema)

    # ------------------------------------------------------------------
    # Step 3: Sensor discovery with progress spinner
    # Method name = async_step_discover_sensors -> step_id = "discover_sensors"
    # ------------------------------------------------------------------

    async def _do_sensor_discovery(self) -> None:
        """Background task for sensor discovery."""
        client = self._make_client()
        try:
            await client.connect()
            self._discovered = await _validate_and_discover(client)
        finally:
            await client.disconnect()

    async def async_step_discover_sensors(self, user_input=None) -> ConfigFlowResult:
        """Show progress spinner while discovering sensors."""
        # Start background task on first call
        if self._task is None:
            self._task = self.hass.async_create_task(self._do_sensor_discovery())

        # If task is still running, show the progress spinner
        if not self._task.done():
            return self.async_show_progress(
                step_id="discover_sensors",
                progress_action="discover_sensors",
                progress_task=self._task,
            )

        # Task finished -- check result
        try:
            await self._task
        except Exception as exc:
            _LOGGER.error("Sensor discovery failed: %s", exc)
            self._task = None
            return self.async_show_progress_done(next_step_id="discover_sensors_failed")

        self._task = None

        if not self._discovered:
            return self.async_show_progress_done(next_step_id="discover_sensors_failed")

        return self.async_show_progress_done(next_step_id="sensors")

    async def async_step_discover_sensors_failed(self, user_input=None) -> ConfigFlowResult:
        """Discovery failed -- abort with message."""
        return self.async_abort(reason="discover_failed")

    # ------------------------------------------------------------------
    # Step 4: Sensor selection
    # ------------------------------------------------------------------

    async def async_step_sensors(self, user_input=None) -> ConfigFlowResult:
        if user_input is not None:
            self._selected_sensors = user_input.get(CONF_SELECTED_SENSORS, [])
            return await self.async_step_access_mode()

        readable = sum(1 for s in self._discovered if s.readable)
        options, preselected = _sensors_to_select_options(self._discovered)
        schema = vol.Schema({
            vol.Required(CONF_SELECTED_SENSORS, default=preselected): SelectSelector(
                SelectSelectorConfig(options=options, multiple=True, mode=SelectSelectorMode.LIST)
            ),
        })
        return self.async_show_form(
            step_id="sensors",
            data_schema=schema,
            description_placeholders={"count": str(readable)},
        )

    # ------------------------------------------------------------------
    # Step 5: Access mode (checkbox form)
    # ------------------------------------------------------------------

    async def async_step_access_mode(self, user_input=None) -> ConfigFlowResult:
        if user_input is not None:
            self._write_enabled = user_input.get(CONF_WRITE_ENABLED, False)
            if self._write_enabled:
                return await self.async_step_discover_params()
            return await self._create_config_entry()

        schema = vol.Schema({
            vol.Required(CONF_WRITE_ENABLED, default=False): bool,
        })
        return self.async_show_form(step_id="access_mode", data_schema=schema)

    # ------------------------------------------------------------------
    # Step 6: Parameter discovery with progress spinner
    # Method name = async_step_discover_params -> step_id = "discover_params"
    # ------------------------------------------------------------------

    async def _do_param_discovery(self) -> None:
        """Background task for parameter discovery."""
        await asyncio.sleep(1.0)  # Let EE10 recover from sensor discovery
        client = self._make_client()
        try:
            await client.connect()
            await client.check_connection()
            menu_items = await client.discover_menu()
            self._writable_params = await client.get_writable_parameters(menu_items)
        finally:
            await client.disconnect()

    async def async_step_discover_params(self, user_input=None) -> ConfigFlowResult:
        """Show progress spinner while discovering parameters."""
        if self._task is None:
            self._task = self.hass.async_create_task(self._do_param_discovery())

        if not self._task.done():
            return self.async_show_progress(
                step_id="discover_params",
                progress_action="discover_params",
                progress_task=self._task,
            )

        try:
            await self._task
        except Exception as exc:
            _LOGGER.error("Parameter discovery failed: %s", exc)
            self._task = None
            self._write_enabled = False
            return self.async_show_progress_done(next_step_id="discover_params_done")

        self._task = None

        if not self._writable_params:
            self._write_enabled = False

        return self.async_show_progress_done(next_step_id="discover_params_done")

    async def async_step_discover_params_done(self, user_input=None) -> ConfigFlowResult:
        """Route after parameter discovery completes."""
        if self._writable_params:
            return await self.async_step_parameters()
        return await self._create_config_entry()

    # ------------------------------------------------------------------
    # Step 7: Parameter selection
    # ------------------------------------------------------------------

    async def async_step_parameters(self, user_input=None) -> ConfigFlowResult:
        if user_input is not None:
            # Check if "show expert" was toggled -- if so, re-show with all params
            show_expert = user_input.get("show_expert", False)
            if show_expert and not getattr(self, '_showing_expert', False):
                self._showing_expert = True
                return await self.async_step_parameters()

            selected = user_input.get(CONF_SELECTED_PARAMETERS, [])
            return await self._create_config_entry(selected_parameters=selected)

        # Filter parameters: show only basic by default, all if expert toggled
        showing_expert = getattr(self, '_showing_expert', False)
        if showing_expert:
            visible_params = self._writable_params
        else:
            visible_params = [p for p in self._writable_params if not _is_expert_param(p)]

        basic_count = len([p for p in self._writable_params if not _is_expert_param(p)])
        expert_count = len(self._writable_params) - basic_count

        options = _params_to_select_options(visible_params)
        schema_dict: dict = {
            vol.Required(CONF_SELECTED_PARAMETERS, default=[]): SelectSelector(
                SelectSelectorConfig(options=options, multiple=True, mode=SelectSelectorMode.LIST)
            ),
        }
        # Only show the "show expert" toggle when expert params are hidden
        if not showing_expert and expert_count > 0:
            schema_dict[vol.Optional("show_expert", default=False)] = bool

        return self.async_show_form(
            step_id="parameters",
            data_schema=vol.Schema(schema_dict),
            description_placeholders={
                "count": str(len(visible_params)),
                "hidden": str(expert_count) if not showing_expert else "0",
            },
        )

    # ------------------------------------------------------------------
    # Entry creation
    # ------------------------------------------------------------------

    async def _create_config_entry(self, selected_parameters: list[str] | None = None) -> ConfigFlowResult:
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

        # -----------------------------------------------------------------------
        # Bug 1 fix: store writable parameter titles so the coordinator can label
        # parameters by their real names (e.g. "Betriebsart") instead of showing
        # the generic "Parameter 0x02F5" fallback.  The title mapping survives
        # across HA restarts because it is embedded in the config entry data.
        # -----------------------------------------------------------------------
        param_titles: dict[str, str] = {}
        for param in self._writable_params:
            addr_hex = f"0x{param.address:04X}"
            param_titles[addr_hex] = param.title
        data[CONF_PARAMETER_TITLES] = param_titles

        # -----------------------------------------------------------------------
        # Bug 4 / Bug 5 fix: cache the discovered sensor specs in the config entry
        # so the coordinator can skip the slow GET_VALUE_LIST_FIRST/NEXT exchange
        # on every startup.  If the coordinator finds cached specs it uses them
        # directly; otherwise it falls back to live discovery (old entries without
        # this field will continue to work after a one-time upgrade).
        # -----------------------------------------------------------------------
        sensor_specs_data: list[dict] = []
        for sensor in self._discovered:
            # Only store readable (successfully polled) specs; unreadable sensors
            # should not be offered as entities because they produce no data.
            if sensor.readable:
                sensor_specs_data.append({
                    "address":   sensor.spec.address,
                    "factor":    sensor.spec.factor,
                    "unit":      sensor.spec.unit,
                    "title":     sensor.spec.title,
                    "menu_type": sensor.spec.menu_type,
                })
        data[CONF_SENSOR_SPECS] = sensor_specs_data

        return self.async_create_entry(title=self._device_name, data=data)

    # ------------------------------------------------------------------
    # Reconfigure
    # ------------------------------------------------------------------

    async def async_step_reconfigure(self, user_input=None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()
        conn_type = entry.data.get(CONF_CONNECTION_TYPE, CONN_TYPE_NETWORK)

        if user_input is not None:
            client = _create_client_from_data(user_input | {CONF_CONNECTION_TYPE: conn_type})
            try:
                await client.connect()
                await client.check_connection()
                await client.disconnect()
            except Exception:
                errors["base"] = "cannot_connect"
            else:
                new_data = dict(entry.data)
                new_data.update(user_input)
                uid = (f"serial:{user_input.get(CONF_SERIAL_DEVICE, '')}" if conn_type == CONN_TYPE_SERIAL
                       else f"{user_input.get(CONF_HOST, '')}:{user_input.get(CONF_PORT, '')}")
                return self.async_update_reload_and_abort(
                    entry, unique_id=uid, title=new_data.get(CONF_DEVICE_NAME, entry.title), data=new_data)

        if conn_type == CONN_TYPE_SERIAL:
            schema = vol.Schema({vol.Required(CONF_SERIAL_DEVICE, default=entry.data.get(CONF_SERIAL_DEVICE, "")): str})
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
    def __init__(self, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None) -> ConfigFlowResult:
        if user_input is not None:
            new_options = {CONF_SCAN_INTERVAL: user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)}
            if CONF_SELECTED_SENSORS in user_input or CONF_SELECTED_PARAMETERS in user_input:
                new_data = dict(self._config_entry.data)
                if CONF_SELECTED_SENSORS in user_input:
                    new_data[CONF_SELECTED_SENSORS] = user_input[CONF_SELECTED_SENSORS]
                if CONF_SELECTED_PARAMETERS in user_input:
                    new_data[CONF_SELECTED_PARAMETERS] = user_input[CONF_SELECTED_PARAMETERS]
                self.hass.config_entries.async_update_entry(self._config_entry, data=new_data)
            return self.async_create_entry(title="", data=new_options)

        current_interval = self._config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        current_sensors = self._config_entry.data.get(CONF_SELECTED_SENSORS, [])
        current_params = self._config_entry.data.get(CONF_SELECTED_PARAMETERS, [])

        sensor_options: list[SelectOptionDict] = []
        param_options: list[SelectOptionDict] = []
        try:
            client = _create_client_from_data(self._config_entry.data)
            try:
                await client.connect()
                discovered = await _validate_and_discover(client)
                sensor_options, _ = _sensors_to_select_options(discovered)
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
                vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL)),
        }
        if sensor_options:
            schema_dict[vol.Required(CONF_SELECTED_SENSORS, default=current_sensors)] = SelectSelector(
                SelectSelectorConfig(options=sensor_options, multiple=True, mode=SelectSelectorMode.LIST))
        if param_options:
            schema_dict[vol.Required(CONF_SELECTED_PARAMETERS, default=current_params)] = SelectSelector(
                SelectSelectorConfig(options=param_options, multiple=True, mode=SelectSelectorMode.LIST))
        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema_dict))
