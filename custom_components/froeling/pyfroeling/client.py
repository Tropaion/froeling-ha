"""High-level async client for the Fröling Lambdatronic P/S 3200 controller.

This module provides :class:`FroelingClient`, which is the main entry point for
application code.  It sits on top of:

  - :mod:`pyfroeling.connection` – raw TCP byte stream (``FroelingConnection``)
  - :mod:`pyfroeling.protocol`   – frame building / CRC (``build_frame``)
  - :mod:`pyfroeling.commands`   – request builders and response parsers

Typical usage::

    from pyfroeling.client import FroelingClient

    client = FroelingClient("192.168.1.100", 8899, timeout=5.0)
    await client.connect()

    status = await client.get_status()
    print(status.state_text)   # e.g. "Heizen"

    sensors = await client.discover_sensors()
    values  = await client.get_all_values(sensors)

    await client.disconnect()

All public methods acquire the connection lock internally so that concurrent
callers from the same event-loop do not interleave frames.
"""

from __future__ import annotations

import logging
from typing import Optional

from .commands import (
    build_check_request,
    build_get_anl_out_request,
    build_get_dig_in_request,
    build_get_dig_out_request,
    build_get_error_request,
    build_get_parameter_request,
    build_get_state_request,
    build_get_value_list_request,
    build_get_value_request,
    build_get_version_request,
    parse_error_response,
    parse_io_response,
    parse_parameter_response,
    parse_state_response,
    parse_value_response,
    parse_value_spec_response,
    parse_version_response,
)
from .connection import FroelingConnection
from .connection import ConnectionError as _ConnErr
from .connection import TimeoutError as _TimeoutErr
from .const import ERROR_STATE_CODES, Command, MenuStructType
from .models import (
    ConfigParameter,
    ErrorEntry,
    HeaterStatus,
    IoValue,
    SensorValue,
    ValueSpec,
)
from .protocol import build_frame

_log = logging.getLogger(__name__)

# Maximum number of pages to read from paginated commands.
# Prevents an infinite loop if the controller misbehaves.
_MAX_PAGES: int = 500


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------

class FroelingError(Exception):
    """Base exception for all Fröling client errors.

    Derive from this class when you want to catch any error originating from
    this library without distinguishing between connection and protocol issues.
    """


class FroelingConnectionError(FroelingError):
    """Raised when the TCP connection to the heater bridge cannot be
    established or is unexpectedly lost.

    Wraps :exc:`~pyfroeling.connection.ConnectionError` and
    :exc:`~pyfroeling.connection.TimeoutError` from the lower layer.
    """


class FroelingProtocolError(FroelingError):
    """Raised when the controller returns a response that cannot be parsed
    or that does not match the expected command.

    This typically indicates a firmware version mismatch or a corrupted frame.
    """


# ---------------------------------------------------------------------------
# FroelingClient
# ---------------------------------------------------------------------------

class FroelingClient:
    """High-level async API for reading data from a Fröling heater.

    All I/O is performed over a single persistent TCP connection to a
    TCP-to-RS232 bridge (e.g. Elfin EE10) connected to the heater's COM1 port.

    Parameters
    ----------
    host:
        Hostname or IP address of the TCP-to-serial bridge.
    port:
        TCP port the bridge listens on (commonly 8899).
    timeout:
        Per-operation timeout in seconds (default: 5.0).
    """

    def __init__(self, host: str, port: int, timeout: float = 5.0) -> None:
        # Store connection parameters so we can reconnect if needed.
        self._host = host
        self._port = port
        self._timeout = timeout

        # The low-level TCP connection object.
        self._conn: FroelingConnection = FroelingConnection(timeout=timeout)

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        """Delegate to the underlying connection's ``is_connected`` property."""
        return self._conn.is_connected

    async def connect(self) -> None:
        """Open the TCP connection to the heater bridge.

        Raises
        ------
        FroelingConnectionError
            If the TCP handshake fails.
        """
        try:
            await self._conn.connect(self._host, self._port)
        except (_ConnErr, _TimeoutErr) as exc:
            raise FroelingConnectionError(str(exc)) from exc

    async def disconnect(self) -> None:
        """Close the TCP connection gracefully.

        Safe to call even when already disconnected.
        """
        await self._conn.disconnect()

    # ------------------------------------------------------------------
    # Public API – read methods
    # ------------------------------------------------------------------

    async def check_connection(self) -> bool:
        """Send a CHECK (0x22) ping and verify the controller echoes it back.

        The CHECK command sends the fixed string "Tescht ;-)" and the controller
        is expected to echo the same bytes.

        Returns
        -------
        bool
            True if the controller responded correctly, False on any error.
        """
        try:
            cmd, payload = build_check_request()
            response = await self._send_and_receive(cmd, payload)
            # The controller should echo the payload verbatim.
            return response == b"Tescht ;-)"
        except (FroelingConnectionError, FroelingProtocolError):
            return False

    async def get_status(self) -> HeaterStatus:
        """Fetch the overall heater status by combining GET_STATE + GET_VERSION.

        Sends GET_STATE to read the current operating mode and state, then
        GET_VERSION to read the firmware version string and the controller's
        real-time clock.  The two responses are merged into a single
        :class:`~pyfroeling.models.HeaterStatus` object.

        Returns
        -------
        HeaterStatus
            Combined state + version information.

        Raises
        ------
        FroelingConnectionError
            On TCP failure.
        FroelingProtocolError
            If either response cannot be parsed.
        """
        # --- GET_STATE ---
        state_cmd, state_payload = build_get_state_request()
        raw_state = await self._send_and_receive(state_cmd, state_payload)
        state_data = parse_state_response(raw_state)

        # --- GET_VERSION ---
        ver_cmd, ver_payload = build_get_version_request()
        raw_ver = await self._send_and_receive(ver_cmd, ver_payload)
        ver_data = parse_version_response(raw_ver)

        state_code: int = state_data["state"]

        return HeaterStatus(
            state      = state_code,
            mode       = state_data["mode"],
            state_text = state_data["state_text"],
            mode_text  = state_data["mode_text"],
            version    = ver_data["version"],
            datetime   = ver_data["datetime"],
            # An error state is any code listed in ERROR_STATE_CODES.
            is_error   = state_code in ERROR_STATE_CODES,
        )

    async def get_value(self, address: int, spec: ValueSpec) -> SensorValue:
        """Read a single sensor value by address.

        The raw signed integer returned by the controller is divided by
        ``spec.factor`` to produce the physical measurement.

        Parameters
        ----------
        address:
            16-bit register address to read.
        spec:
            :class:`~pyfroeling.models.ValueSpec` that provides metadata
            (factor, unit, title, sensor_type) for this address.

        Returns
        -------
        SensorValue
            Scaled physical reading with metadata.

        Raises
        ------
        FroelingConnectionError / FroelingProtocolError
            On communication failure.
        """
        cmd, payload = build_get_value_request(address)
        raw = await self._send_and_receive(cmd, payload)
        raw_value = parse_value_response(raw)

        # Apply the scale factor from the spec to get the physical value.
        factor = spec.factor if spec.factor else 1
        value  = raw_value / factor

        return SensorValue(
            address     = address,
            value       = value,
            raw_value   = raw_value,
            factor      = factor,
            unit        = spec.unit,
            title       = spec.title,
            sensor_type = MenuStructType(spec.menu_type).name
                          if spec.menu_type in [m.value for m in MenuStructType]
                          else str(spec.menu_type),
        )

    async def get_all_values(
        self, specs: list[ValueSpec]
    ) -> dict[int, SensorValue]:
        """Read sensor values for all specs in the list.

        Dispatches each spec to the appropriate GET_* command based on its
        ``menu_type`` field:

        ============  ===========  ======================================
        menu_type     Command      Mapping
        ============  ===========  ======================================
        MESSWERT      GET_VALUE    Standard sensor measurement
        MESSWERT1     GET_VALUE    Extended sensor measurement
        DIG_OUT       GET_DIG_OUT  Digital output channel state
        ANL_OUT       GET_ANL_OUT  Analogue output channel state
        DIG_IN        GET_DIG_IN   Digital input channel state
        ============  ===========  ======================================

        Errors for individual sensors are logged at WARNING level and the
        sensor is omitted from the result dict rather than aborting the entire
        batch.

        Parameters
        ----------
        specs:
            List of :class:`~pyfroeling.models.ValueSpec` objects as returned
            by :meth:`discover_sensors`.

        Returns
        -------
        dict[int, SensorValue]
            Mapping from register address to :class:`~pyfroeling.models.SensorValue`.
            Addresses that could not be read are absent from the dict.
        """
        results: dict[int, SensorValue] = {}

        for spec in specs:
            try:
                menu_type = spec.menu_type

                if menu_type in (MenuStructType.MESSWERT, MenuStructType.MESSWERT1):
                    # Standard measured sensor value.
                    sv = await self.get_value(spec.address, spec)
                    results[spec.address] = sv

                elif menu_type == MenuStructType.DIG_OUT:
                    # Digital output: fetch via GET_DIG_OUT.
                    io = await self._get_io(spec.address, Command.GET_DIG_OUT)
                    results[spec.address] = SensorValue(
                        address     = spec.address,
                        value       = float(io.state),
                        raw_value   = io.state,
                        factor      = 1,
                        unit        = spec.unit,
                        title       = spec.title,
                        sensor_type = "DIG_OUT",
                    )

                elif menu_type == MenuStructType.ANL_OUT:
                    # Analogue output: fetch via GET_ANL_OUT.
                    io = await self._get_io(spec.address, Command.GET_ANL_OUT)
                    results[spec.address] = SensorValue(
                        address     = spec.address,
                        value       = float(io.state),
                        raw_value   = io.state,
                        factor      = 1,
                        unit        = spec.unit,
                        title       = spec.title,
                        sensor_type = "ANL_OUT",
                    )

                elif menu_type == MenuStructType.DIG_IN:
                    # Digital input: fetch via GET_DIG_IN.
                    io = await self._get_io(spec.address, Command.GET_DIG_IN)
                    results[spec.address] = SensorValue(
                        address     = spec.address,
                        value       = float(io.state),
                        raw_value   = io.state,
                        factor      = 1,
                        unit        = spec.unit,
                        title       = spec.title,
                        sensor_type = "DIG_IN",
                    )

                else:
                    # Unknown / unsupported menu type – skip silently.
                    _log.debug(
                        "Skipping address 0x%04X: unsupported menu_type 0x%02X",
                        spec.address, menu_type,
                    )

            except (FroelingConnectionError, FroelingProtocolError) as exc:
                # Log and continue so one bad sensor doesn't abort the batch.
                _log.warning(
                    "Failed to read sensor at address 0x%04X (%s): %s",
                    spec.address, spec.title, exc,
                )

        return results

    async def discover_sensors(self) -> list[ValueSpec]:
        """Read the full sensor/value specification list from the controller.

        Uses the paginated GET_VALUE_LIST_FIRST / GET_VALUE_LIST_NEXT protocol
        to enumerate all entries in the controller's menu-structure table.
        Each entry becomes one :class:`~pyfroeling.models.ValueSpec`.

        The loop stops when the controller sets ``more=0`` or after
        ``_MAX_PAGES`` pages (safety guard against misbehaving firmware).

        Returns
        -------
        list[ValueSpec]
            All sensor specifications found on the controller.

        Raises
        ------
        FroelingConnectionError / FroelingProtocolError
            On communication failure during any page request.
        """
        specs: list[ValueSpec] = []
        first = True

        for page_num in range(_MAX_PAGES):
            cmd, payload = build_get_value_list_request(first=first)
            raw = await self._send_and_receive(cmd, payload)
            data = parse_value_spec_response(raw)

            if not data.get("more", False):
                # End-of-list sentinel received.
                _log.debug(
                    "discover_sensors: received end-of-list after %d specs", len(specs)
                )
                break

            specs.append(ValueSpec(
                address   = data["address"],
                factor    = data["factor"],
                unit      = data["unit"],
                title     = data["title"],
                menu_type = data["menu_type"],
            ))
            first = False  # All pages after the first use the NEXT command.

            _log.debug(
                "discover_sensors page %d: 0x%04X '%s'",
                page_num, data["address"], data["title"],
            )
        else:
            _log.warning(
                "discover_sensors hit _MAX_PAGES limit (%d); list may be incomplete",
                _MAX_PAGES,
            )

        return specs

    async def get_parameter(self, address: int) -> ConfigParameter:
        """Read a single configurable parameter by address.

        Parameters
        ----------
        address:
            16-bit parameter address in the controller's EEPROM.

        Returns
        -------
        ConfigParameter
            Current value, unit, limits, and scale factor.

        Raises
        ------
        FroelingConnectionError / FroelingProtocolError
        """
        cmd, payload = build_get_parameter_request(address)
        raw = await self._send_and_receive(cmd, payload)
        data = parse_parameter_response(raw)

        return ConfigParameter(
            address       = data["address"],
            value         = data["value"],
            unit          = data["unit"],
            digits        = data["digits"],
            factor        = data["factor"],
            min_value     = data["min_value"],
            max_value     = data["max_value"],
            default_value = data["default_value"],
            # The parameter title is not in the GET_PARAMETER response; it must
            # come from the menu-structure list.  Leave blank here.
            title         = "",
        )

    async def get_errors(self) -> list[ErrorEntry]:
        """Read the controller's error log.

        Iterates over paginated GET_ERROR_FIRST / GET_ERROR_NEXT responses
        until the end-of-list sentinel is received or ``_MAX_PAGES`` is hit.

        Returns
        -------
        list[ErrorEntry]
            All error log entries currently stored in the controller.

        Raises
        ------
        FroelingConnectionError / FroelingProtocolError
            On communication failure.
        """
        entries: list[ErrorEntry] = []
        first = True

        for _ in range(_MAX_PAGES):
            cmd, payload = build_get_error_request(first=first)
            raw = await self._send_and_receive(cmd, payload)
            data = parse_error_response(raw)

            if not data.get("more", False):
                # End-of-list received.
                break

            entries.append(ErrorEntry(
                number    = data["number"],
                text      = data["text"],
                state     = data["state"],
                timestamp = data["timestamp"],
                info      = data["info"],
            ))
            first = False

        return entries

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_io(self, address: int, command: Command) -> IoValue:
        """Fetch the mode and state of a single I/O channel.

        This is a helper used by :meth:`get_all_values` to dispatch the correct
        GET_DIG_OUT / GET_ANL_OUT / GET_DIG_IN command.

        Parameters
        ----------
        address:
            16-bit channel address.
        command:
            The specific GET_* Command to use for this channel type.

        Returns
        -------
        IoValue
            Channel mode and current physical state.
        """
        # Build the request payload (2-byte address).
        if command == Command.GET_DIG_OUT:
            _, req_payload = build_get_dig_out_request(address)
        elif command == Command.GET_ANL_OUT:
            _, req_payload = build_get_anl_out_request(address)
        else:  # GET_DIG_IN
            _, req_payload = build_get_dig_in_request(address)

        raw = await self._send_and_receive(command, req_payload)
        data = parse_io_response(raw)

        return IoValue(
            address = address,
            mode    = data["mode"],
            state   = data["state"],
        )

    async def _send_and_receive(self, command: Command, payload: bytes) -> bytes:
        """Acquire the lock, send a frame, receive the response, strip CRC.

        This is the single choke-point for all request/response exchanges.  It:
          1. Acquires the connection lock to prevent interleaving.
          2. Builds the escaped wire frame via :func:`~pyfroeling.protocol.build_frame`.
          3. Writes the frame to the stream.
          4. Reads and un-escapes the response frame.
          5. Strips the single trailing CRC byte.
          6. Returns the raw payload bytes for the caller to parse.

        Parameters
        ----------
        command:
            The :class:`~pyfroeling.const.Command` opcode to send.
        payload:
            Request payload bytes (may be empty).

        Returns
        -------
        bytes
            Payload bytes from the response frame, CRC byte removed.

        Raises
        ------
        FroelingConnectionError
            On TCP connection / timeout errors.
        FroelingProtocolError
            If the response command byte does not match the request, or if
            the response payload is unexpectedly short (no CRC byte).
        """
        async with self._conn.lock:
            # --- Build and transmit the request ---
            frame = build_frame(command, payload)
            try:
                await self._conn.write_frame(frame)
            except (_ConnErr, _TimeoutErr) as exc:
                raise FroelingConnectionError(
                    f"Failed to send command {command.name}: {exc}"
                ) from exc

            # --- Read the response ---
            try:
                resp_cmd, _size, payload_with_crc = await self._conn.read_response()
            except (_ConnErr, _TimeoutErr) as exc:
                raise FroelingConnectionError(
                    f"Failed to receive response for {command.name}: {exc}"
                ) from exc
            except ValueError as exc:
                raise FroelingProtocolError(
                    f"Malformed response for {command.name}: {exc}"
                ) from exc

            # Verify the controller echoed back the same command code.
            if resp_cmd != command.value:
                raise FroelingProtocolError(
                    f"Command mismatch: sent 0x{command.value:02X} "
                    f"({command.name}), received 0x{resp_cmd:02X}."
                )

            # Validate that there is at least one byte (the CRC).
            if len(payload_with_crc) == 0:
                raise FroelingProtocolError(
                    f"Empty response for command {command.name} – missing CRC byte."
                )

            # Strip the trailing CRC byte; the caller only needs the payload.
            payload_only = payload_with_crc[:-1]
            return payload_only
