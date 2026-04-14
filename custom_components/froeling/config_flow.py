"""Config flow for the Fröling Heater integration.

Multi-step setup:
  1. User chooses connection type (Network TCP or USB Serial)
  2. User enters connection details + device name
  3. Integration connects, discovers sensors, reads current values
  4. User selects which sensors to enable
  5. Config entry is created

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
    CONF_SELECTED_SENSORS,
    CONF_SERIAL_DEVICE,
    CONN_TYPE_NETWORK,
    CONN_TYPE_SERIAL,
    DEFAULT_DEVICE_NAME,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .pyfroeling import FroelingClient, FroelingConnectionError, ValueSpec

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sensor discovery helper
# ---------------------------------------------------------------------------

@dataclass
class DiscoveredSensor:
    """A sensor discovered from the heater with its current value."""
    spec: ValueSpec
    value: float | None
    readable: bool


async def _discover_sensors(client: FroelingClient) -> list[DiscoveredSensor]:
    """Discover all sensors and read their current values.

    Returns a list of DiscoveredSensor with current readings.
    Sensors that fail to read are marked as not readable.
    """
    specs = await client.discover_sensors()
    discovered: list[DiscoveredSensor] = []
    for spec in specs:
        try:
            sv = await client.get_value(spec.address, spec)
            discovered.append(DiscoveredSensor(spec=spec, value=sv.value, readable=True))
        except Exception:
            discovered.append(DiscoveredSensor(spec=spec, value=None, readable=False))
    return discovered


def _is_likely_absent(sensor: DiscoveredSensor) -> bool:
    """Check if a sensor is likely not physically present.

    Temperature sensors reading exactly 0.0°C almost always indicate
    no physical sensor is connected to that address.
    """
    if not sensor.readable:
        return True
    if sensor.value is not None and sensor.value == 0.0 and sensor.spec.unit == "°C":
        return True
    return False


def _sensors_to_select_options(
    sensors: list[DiscoveredSensor], include_absent: bool = False
) -> list[SelectOptionDict]:
    """Convert discovered sensors to HA select options with live values.

    Filters absent sensors. Appends address suffix for duplicate titles.
    """
    present = [s for s in sensors if not _is_likely_absent(s) or include_absent]
    title_counts = Counter(s.spec.title for s in present)

    options: list[SelectOptionDict] = []
    for sensor in present:
        title = sensor.spec.title
        if sensor.readable and sensor.value is not None:
            label = f"{title} = {sensor.value:.1f} {sensor.spec.unit}".rstrip()
        else:
            label = f"{title} (nicht verfügbar)"

        if title_counts[title] > 1:
            label = f"{label}  [0x{sensor.spec.address:04X}]"

        options.append(
            SelectOptionDict(value=f"0x{sensor.spec.address:04X}", label=label)
        )
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
    """Multi-step config flow: connection type -> details -> sensor selection."""

    VERSION = 1

    def __init__(self) -> None:
        self._device_name: str = DEFAULT_DEVICE_NAME
        self._conn_type: str = CONN_TYPE_NETWORK
        self._host: str = ""
        self._port: int = DEFAULT_PORT
        self._serial_device: str = ""
        self._discovered: list[DiscoveredSensor] = []

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
        """Collect host, port, and device name for a TCP connection."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._conn_type = CONN_TYPE_NETWORK
            self._device_name = user_input[CONF_DEVICE_NAME]
            self._host = user_input[CONF_HOST]
            self._port = user_input[CONF_PORT]

            client = FroelingClient(host=self._host, port=self._port)
            try:
                await client.connect()
                self._discovered = await _discover_sensors(client)
                await client.disconnect()
            except FroelingConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during network setup")
                errors["base"] = "unknown"
            else:
                if not self._discovered:
                    errors["base"] = "no_sensors"
                else:
                    return await self.async_step_sensors()

        schema = vol.Schema({
            vol.Required(CONF_DEVICE_NAME, default=DEFAULT_DEVICE_NAME): str,
            vol.Required(CONF_HOST): str,
            vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
        })

        return self.async_show_form(
            step_id="network", data_schema=schema, errors=errors,
        )

    # ------------------------------------------------------------------
    # Step 1b: USB Serial connection details
    # ------------------------------------------------------------------

    async def async_step_serial(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect serial device path and device name for a USB connection."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._conn_type = CONN_TYPE_SERIAL
            self._device_name = user_input[CONF_DEVICE_NAME]
            self._serial_device = user_input[CONF_SERIAL_DEVICE]

            client = FroelingClient(serial_device=self._serial_device)
            try:
                await client.connect()
                self._discovered = await _discover_sensors(client)
                await client.disconnect()
            except FroelingConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during serial setup")
                errors["base"] = "unknown"
            else:
                if not self._discovered:
                    errors["base"] = "no_sensors"
                else:
                    return await self.async_step_sensors()

        schema = vol.Schema({
            vol.Required(CONF_DEVICE_NAME, default=DEFAULT_DEVICE_NAME): str,
            vol.Required(CONF_SERIAL_DEVICE): str,
        })

        return self.async_show_form(
            step_id="serial", data_schema=schema, errors=errors,
        )

    # ------------------------------------------------------------------
    # Step 2: Sensor selection (shared by both connection types)
    # ------------------------------------------------------------------

    async def async_step_sensors(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user select which sensors to enable."""
        if user_input is not None:
            selected = user_input.get(CONF_SELECTED_SENSORS, [])

            # Build unique_id from connection info
            if self._conn_type == CONN_TYPE_SERIAL:
                unique_id = f"serial:{self._serial_device}"
            else:
                unique_id = f"{self._host}:{self._port}"
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()

            # Build config entry data
            data: dict[str, Any] = {
                CONF_DEVICE_NAME: self._device_name,
                CONF_CONNECTION_TYPE: self._conn_type,
                CONF_SELECTED_SENSORS: selected,
            }
            if self._conn_type == CONN_TYPE_SERIAL:
                data[CONF_SERIAL_DEVICE] = self._serial_device
            else:
                data[CONF_HOST] = self._host
                data[CONF_PORT] = self._port

            return self.async_create_entry(title=self._device_name, data=data)

        options = _sensors_to_select_options(self._discovered)
        all_values = [opt["value"] for opt in options]

        schema = vol.Schema({
            vol.Required(CONF_SELECTED_SENSORS, default=all_values): SelectSelector(
                SelectSelectorConfig(
                    options=options, multiple=True, mode=SelectSelectorMode.LIST,
                )
            ),
        })

        return self.async_show_form(step_id="sensors", data_schema=schema)

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
            # Test new connection
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

        # Show form matching current connection type
        if conn_type == CONN_TYPE_SERIAL:
            schema = vol.Schema({
                vol.Required(CONF_SERIAL_DEVICE,
                             default=entry.data.get(CONF_SERIAL_DEVICE, "")): str,
            })
        else:
            schema = vol.Schema({
                vol.Required(CONF_HOST, default=entry.data.get(CONF_HOST, "")): str,
                vol.Required(CONF_PORT, default=entry.data.get(CONF_PORT, DEFAULT_PORT)): int,
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
        """Show options: polling interval and sensor re-selection."""
        if user_input is not None:
            new_options = {
                CONF_SCAN_INTERVAL: user_input.get(
                    CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                ),
            }
            if CONF_SELECTED_SENSORS in user_input:
                new_data = dict(self._config_entry.data)
                new_data[CONF_SELECTED_SENSORS] = user_input[CONF_SELECTED_SENSORS]
                self.hass.config_entries.async_update_entry(
                    self._config_entry, data=new_data
                )
            return self.async_create_entry(title="", data=new_options)

        current_interval = self._config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )
        current_selected = self._config_entry.data.get(CONF_SELECTED_SENSORS, [])

        # Try to discover sensors for re-selection
        sensor_options: list[SelectOptionDict] = []
        try:
            client = _create_client(self._config_entry.data)
            await client.connect()
            discovered = await _discover_sensors(client)
            await client.disconnect()
            sensor_options = _sensors_to_select_options(discovered)
        except Exception:
            _LOGGER.warning("Options flow: could not discover sensors")

        schema_dict: dict[vol.Marker, Any] = {
            vol.Required(CONF_SCAN_INTERVAL, default=current_interval): vol.All(
                vol.Coerce(int),
                vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
            ),
        }

        if sensor_options:
            default_selected = (
                current_selected if current_selected
                else [opt["value"] for opt in sensor_options]
            )
            schema_dict[vol.Required(
                CONF_SELECTED_SENSORS, default=default_selected,
            )] = SelectSelector(
                SelectSelectorConfig(
                    options=sensor_options, multiple=True, mode=SelectSelectorMode.LIST,
                )
            )

        return self.async_show_form(
            step_id="init", data_schema=vol.Schema(schema_dict),
        )
