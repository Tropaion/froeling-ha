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

_LOGGER = logging.getLogger(__name__)

from .commands import (
    _normalize_unit,
    build_check_request,
    build_get_anl_out_request,
    build_get_dig_in_request,
    build_get_dig_out_request,
    build_get_error_request,
    build_get_menu_list_request,
    build_get_parameter_request,
    build_get_state_request,
    build_get_value_list_request,
    build_get_value_request,
    build_get_version_request,
    build_set_parameter_request,
    parse_error_response,
    parse_io_response,
    parse_menu_entry_response,
    parse_parameter_response,
    parse_set_parameter_response,
    parse_state_response,
    parse_value_response,
    parse_value_spec_response,
    parse_version_response,
)
from .connection import FroelingConnection
from .connection import ConnectionError as _ConnErr
from .connection import TimeoutError as _TimeoutErr
from .const import COMM_ID, ERROR_STATE_CODES, Command, MenuStructType
from .models import (
    ConfigParameter,
    ErrorEntry,
    HeaterStatus,
    IoValue,
    MenuItem,
    SensorValue,
    ValueSpec,
    WritableParameter,
)
from .protocol import build_frame, calculate_crc

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
    TCP-to-serial converter connected to the heater's COM1 port.

    Parameters
    ----------
    host:
        Hostname or IP address of the TCP-to-serial bridge.
    port:
        TCP port the bridge listens on (commonly 8899).
    timeout:
        Per-operation timeout in seconds (default: 5.0).
    """

    def __init__(
        self,
        host: str = "",
        port: int = 0,
        serial_device: str = "",
        timeout: float = 5.0,
    ) -> None:
        # Store connection parameters so we can reconnect if needed.
        self._host = host
        self._port = port
        self._serial_device = serial_device
        self._timeout = timeout

        # The low-level connection object (works for both TCP and serial).
        self._conn: FroelingConnection = FroelingConnection(timeout=timeout)

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        """Delegate to the underlying connection's ``is_connected`` property."""
        return self._conn.is_connected

    async def connect(self) -> None:
        """Open the connection to the heater (TCP or serial).

        Automatically chooses TCP or serial based on which parameters
        were provided at construction time. If serial_device is set,
        opens a direct USB serial connection. Otherwise uses TCP.

        Raises
        ------
        FroelingConnectionError
            If the connection cannot be established.
        """
        try:
            if self._serial_device:
                # Direct USB serial connection to COM1
                await self._conn.connect_serial(self._serial_device)
            else:
                # Network connection via TCP-to-serial converter
                await self._conn.connect(self._host, self._port)
        except (_ConnErr, _TimeoutErr) as exc:
            raise FroelingConnectionError(str(exc)) from exc

    async def disconnect(self) -> None:
        """Close the connection gracefully. Safe to call when already disconnected."""
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
                # ALL sensors discovered via cmdGetValueListFirst/Next are
                # readable with cmdGetValue (0x30), regardless of their
                # menu_type.  This matches linux-p4d's behaviour in
                # specific.c:initValueFacts() where every ValueSpec entry
                # is stored as type "VA" and read with request->getValue().
                #
                # The menu_type field is metadata about the sensor's role
                # in the heater's menu structure, NOT the read method.
                sv = await self.get_value(spec.address, spec)
                results[spec.address] = sv

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
        seen_addresses: set[int] = set()  # Track addresses to skip duplicates
        first = True

        for page_num in range(_MAX_PAGES):
            cmd, payload = build_get_value_list_request(first=first)
            raw = await self._send_and_receive(cmd, payload)
            data = parse_value_spec_response(raw)

            if not data.get("more", False):
                _log.debug(
                    "discover_sensors: received end-of-list after %d specs", len(specs)
                )
                break

            # The heater may send a short/empty entry with more=True.
            # Skip it and continue to the next page (p4io.c:1089 wrnEmpty).
            if data.get("empty", False):
                _log.debug("discover_sensors page %d: skipping short/empty entry", page_num)
                first = False
                continue

            address = data["address"]

            # The heater often reports the same address multiple times in the
            # value list (e.g. "Außentemperatur" at 0x0004 appears 5+ times).
            # Reading the same address twice gives the same value, so skip dupes.
            if address in seen_addresses:
                _log.debug(
                    "discover_sensors page %d: skipping duplicate address 0x%04X '%s'",
                    page_num, address, data["title"],
                )
                first = False
                continue
            seen_addresses.add(address)

            specs.append(ValueSpec(
                address   = address,
                factor    = data["factor"],
                unit      = data["unit"],
                title     = data["title"],
                menu_type = data["menu_type"],
            ))
            first = False

            _log.debug(
                "discover_sensors page %d: 0x%04X '%s'",
                page_num, address, data["title"],
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

    async def discover_menu(self) -> list[MenuItem]:
        """Read the full menu-tree structure from the controller.

        Uses the paginated GET_MENU_LIST_FIRST (0x37) / GET_MENU_LIST_NEXT (0x38)
        protocol to enumerate every node in the controller's internal menu
        hierarchy.  Nodes include both read-only sensors and writable parameters.

        The enumeration pattern mirrors :meth:`discover_sensors`:
          - Send GET_MENU_LIST_FIRST on the first iteration.
          - Send GET_MENU_LIST_NEXT on all subsequent iterations.
          - Stop when the controller returns ``more=0`` (end-of-list) or after
            ``_MAX_PAGES`` pages (safety guard against misbehaving firmware).
          - Skip "empty" entries (more=1 but payload too short – linux-p4d wrnEmpty).
          - Deduplicate by address so each register appears only once.

        Returns
        -------
        list[MenuItem]
            All menu entries found on the controller (sensors and parameters).

        Raises
        ------
        FroelingConnectionError / FroelingProtocolError
            On communication failure during any page request.
        """
        items: list[MenuItem] = []
        # Track seen addresses to avoid duplicate entries (same address can
        # appear more than once in the menu tree under different parent nodes).
        seen_addresses: set[int] = set()
        first = True

        for page_num in range(_MAX_PAGES):
            # Use FIRST command on the opening request, NEXT on all subsequent.
            cmd, payload = build_get_menu_list_request(first=first)
            raw = await self._send_and_receive(cmd, payload)
            data = parse_menu_entry_response(raw)

            if not data.get("more", False):
                # End-of-list sentinel: controller has no more entries.
                _log.debug(
                    "discover_menu: received end-of-list after %d items", len(items)
                )
                break

            # Controller sent a short/empty page (more=True but entry incomplete).
            # linux-p4d p4io.c handles this as "wrnEmpty" – skip and continue.
            if data.get("empty", False):
                _log.debug(
                    "discover_menu page %d: skipping short/empty entry", page_num
                )
                first = False
                continue

            address = data["address"]

            # The menu tree frequently lists the same address under multiple
            # parent nodes.  Deduplicate by address so callers see each register
            # only once.
            if address in seen_addresses:
                _log.debug(
                    "discover_menu page %d: skipping duplicate address 0x%04X '%s'",
                    page_num, address, data["title"],
                )
                first = False
                continue
            seen_addresses.add(address)

            items.append(MenuItem(
                menu_type = data["menu_type"],
                parent    = data["parent"],
                child     = data["child"],
                address   = address,
                title     = data["title"],
            ))
            first = False

            _log.debug(
                "discover_menu page %d: 0x%04X type=0x%02X '%s'",
                page_num, address, data["menu_type"], data["title"],
            )
        else:
            # Loop exhausted _MAX_PAGES without hitting end-of-list.
            _log.warning(
                "discover_menu hit _MAX_PAGES limit (%d); list may be incomplete",
                _MAX_PAGES,
            )

        return items

    async def set_parameter(self, address: int, value: float, factor: int) -> float:
        """Write a parameter value to the controller and confirm the new value.

        The Lambdatronic protocol requires a specific multi-step write sequence:
          1. Send SET_PARAMETER (0x39) with the address and raw integer value.
          2. Read the FIRST echo frame – the controller echoes address + new value.
          3. Read the SECOND echo frame – an additional confirmation frame that
             the controller always sends after a write (discarded, no useful data).
          4. Re-read the parameter with GET_PARAMETER (0x55) to confirm the value
             was stored correctly in EEPROM.

        All steps are executed inside the connection lock (held by
        :meth:`_send_and_receive` for step 1; held manually for steps 2-3 via the
        same lock context used in step 1 to prevent interleaving).

        IMPORTANT: The connection is NEVER disconnected or reconnected during or
        between steps.  All communication happens on the single persistent TCP or
        serial connection.

        Parameters
        ----------
        address:
            16-bit parameter register address to write.
        value:
            Physical float value to set (e.g. 75.0 for 75 °C).
        factor:
            Scale factor for this parameter.  The raw integer sent on the wire
            is ``int(value * factor)``.  Must be >= 1.

        Returns
        -------
        float
            The confirmed physical value read back from the controller after the
            write.  This may differ slightly from ``value`` due to the controller's
            internal rounding or range clamping.

        Raises
        ------
        FroelingConnectionError
            On TCP / serial communication failure.
        FroelingProtocolError
            If the first echo frame cannot be parsed.
        """
        # Convert the physical float to the raw integer value for the wire.
        # For example: value=75.5, factor=10 → raw_value=755
        raw_value: int = int(value * factor)

        # --- Step 1: Send SET_PARAMETER and receive the first echo frame ---
        # _send_and_receive handles the lock, frame building, and CRC verification.
        cmd, req_payload = build_set_parameter_request(address, raw_value)
        first_echo_raw = await self._send_and_receive(cmd, req_payload)

        # Parse the first echo to confirm the controller acknowledged the write.
        parse_set_parameter_response(first_echo_raw)

        # --- Steps 2-3: Read the second echo frame (inside the connection lock) ---
        # The Lambdatronic always sends a second confirmation frame after a write.
        # We must consume it to keep the protocol synchronised, but its content is
        # not useful and we intentionally ignore parse errors on it.
        async with self._conn.lock:
            try:
                _resp_cmd, _size, payload_with_crc = await self._conn.read_response()
                # Strip the CRC byte (last byte) from the second echo; ignore content.
                _second_echo = payload_with_crc[:-1] if payload_with_crc else b""
                _log.debug(
                    "set_parameter: consumed second echo for address 0x%04X", address
                )
            except Exception as exc:
                # Log but do NOT raise – the write already succeeded.
                # A missing or malformed second echo is non-fatal.
                _log.debug(
                    "set_parameter: could not read second echo for 0x%04X: %s",
                    address, exc,
                )

        # --- Step 4: Re-read to confirm the value was stored in EEPROM ---
        confirmed_param = await self.get_parameter(address)
        confirmed_value = confirmed_param.value

        _log.debug(
            "set_parameter: address=0x%04X requested=%s confirmed=%s",
            address, value, confirmed_value,
        )

        return confirmed_value

    async def get_writable_parameters(
        self, menu_items: list[MenuItem]
    ) -> list[WritableParameter]:
        """Build writable parameter objects from a filtered menu-tree list.

        Filters ``menu_items`` to only the writable parameter types:
          - 0x07 (PAR)      – numeric configuration parameters
          - 0x08 (PAR_DIG)  – digital / boolean parameters
          - 0x0A (PAR_ZEIT) – time-programme parameters

        For each writable menu item, calls :meth:`get_parameter` to fetch the
        current value, limits, unit, scale factor, and display digits.  The two
        results are merged into a :class:`~pyfroeling.models.WritableParameter`.

        Failures for individual parameters are logged at DEBUG level and the item
        is silently skipped so that one inaccessible parameter does not abort the
        entire enumeration.

        Parameters
        ----------
        menu_items:
            List of :class:`~pyfroeling.models.MenuItem` objects as returned by
            :meth:`discover_menu`.  Only those with writable menu_types are used.

        Returns
        -------
        list[WritableParameter]
            All successfully read writable parameters.
        """
        # Set of MenuStructType codes that correspond to writable parameters.
        # From const.py / service.h:
        #   PAR      = 0x07  Numeric parameter (temperature, %, etc.)
        #   PAR_DIG  = 0x08  Boolean/choice parameter (ja/nein, 0/1)
        #   PAR_ZEIT = 0x0A  Time parameter (HH:MM)
        #   PAR_SET  = 0x32  Parameter set
        #   PAR_SET1 = 0x39  Parameter set variant 1
        #   PAR_SET2 = 0x40  Parameter set variant 2
        #   WORKMODE = 0x2F  Operating mode (Sommer/Übergang/Winter)
        WRITABLE_TYPES: frozenset[int] = frozenset({
            0x07, 0x08, 0x0A, 0x0B,  # PAR, PAR_DIG, PAR_ZEIT, PAR_WEEKDAY
            0x2F,                      # WORKMODE (operating mode)
            0x32, 0x39, 0x40,          # PAR_SET, PAR_SET1, PAR_SET2
        })

        # Log all unique menu types found for debugging
        type_counts: dict[int, int] = {}
        for item in menu_items:
            type_counts[item.menu_type] = type_counts.get(item.menu_type, 0) + 1
        _log.info(
            "Menu tree types found: %s",
            {f"0x{t:02X}": c for t, c in sorted(type_counts.items())},
        )

        results: list[WritableParameter] = []

        for item in menu_items:
            # Skip non-writable menu types (sensors, I/O channels, etc.).
            if item.menu_type not in WRITABLE_TYPES:
                continue

            try:
                # Fetch the current value and metadata via GET_PARAMETER (0x55).
                param: ConfigParameter = await self.get_parameter(item.address)
            except (FroelingConnectionError, FroelingProtocolError) as exc:
                # Skip this parameter; it may be inaccessible on this model.
                _log.debug(
                    "get_writable_parameters: skipping address 0x%04X ('%s'): %s",
                    item.address, item.title, exc,
                )
                continue

            # Apply unit normalisation using the menu item title for context
            # (e.g. "m" → "min" for runtime parameters).
            unit: str = _normalize_unit(param.unit, item.title)

            results.append(WritableParameter(
                address       = item.address,
                title         = item.title,           # From menu tree (has the name)
                menu_type     = item.menu_type,
                value         = param.value,
                unit          = unit,
                digits        = param.digits,
                factor        = param.factor,
                min_value     = param.min_value,
                max_value     = param.max_value,
                default_value = param.default_value,
            ))

            _log.debug(
                "get_writable_parameters: 0x%04X '%s' = %s %s (min=%s max=%s)",
                item.address, item.title, param.value, unit,
                param.min_value, param.max_value,
            )

        return results

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

            # Log the response command for debugging but do NOT enforce a
            # strict match.  The Lambdatronic controller does not always echo
            # the same command code -- e.g. it responds to GET_VALUE_LIST_NEXT
            # (0x32) with 0x31 (GET_VALUE_LIST_FIRST) in the response header.
            # linux-p4d (the reference implementation) never checks this field.
            if resp_cmd != command.value:
                _LOGGER.debug(
                    "Response command 0x%02X differs from request 0x%02X (%s) "
                    "– this is normal for some Lambdatronic commands",
                    resp_cmd, command.value, command.name,
                )

            # Validate that there is at least one byte (the CRC).
            if len(payload_with_crc) == 0:
                raise FroelingProtocolError(
                    f"Empty response for command {command.name} – missing CRC byte."
                )

            # --- Verify the response CRC ---
            # The CRC is the last byte of payload_with_crc.
            received_crc: int = payload_with_crc[-1]
            payload_only: bytes = payload_with_crc[:-1]

            # Reconstruct the full unescaped response frame (excluding CRC) so we
            # can recompute the expected CRC.  The frame header is:
            #   sync_id (2 bytes, 0x02FD) + size (2 bytes, BE) + command (1 byte)
            # followed by the payload bytes (without CRC).
            # Note: read_response() returns (command_byte, size, payload_with_crc)
            # where size == len(payload_with_crc) (includes the CRC byte itself).
            sync_bytes: bytes = COMM_ID.to_bytes(2, byteorder="big")
            size_bytes: bytes = _size.to_bytes(2, byteorder="big")
            crc_input: bytes = (
                sync_bytes
                + size_bytes
                + bytes([resp_cmd])
                + payload_only
            )
            expected_crc: int = calculate_crc(crc_input)

            if received_crc != expected_crc:
                raise FroelingProtocolError(
                    f"CRC mismatch for command {command.name}: "
                    f"expected 0x{expected_crc:02X}, got 0x{received_crc:02X}."
                )

            return payload_only
