"""Async TCP connection layer for the Fröling Lambdatronic P/S 3200 protocol.

This module handles the raw TCP byte stream to/from the serial bridge
that is connected to the controller's COM1 service port.

Responsibilities:
  - Open / close an asyncio TCP stream to the bridge.
  - Write complete escaped frames to the stream.
  - Read a response frame back, byte-by-byte un-escaping on the fly.
  - Serialise all communication through an asyncio.Lock so callers can
    safely share a single connection from multiple coroutines.

Frame structure (unescaped) reminder:
    [sync_hi][sync_lo] [size_hi][size_lo] [cmd] [payload...] [crc]

The sync word (0x02FD) is sent RAW – it is never escaped.  Everything from
the size field onward is subject to the escape rules in const.ESCAPE_TABLE.
When reading a response we therefore:
  1. Read the 2 raw sync bytes directly.
  2. Read (and un-escape) the next 3 bytes to recover size + command.
  3. Read (and un-escape) ``size`` more bytes to recover payload + CRC.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from .const import COMM_ID

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class ConnectionError(Exception):
    """Raised when a TCP connection to the heater bridge cannot be established
    or when the connection drops unexpectedly mid-operation."""


class TimeoutError(Exception):
    """Raised when a read operation does not complete within the configured
    timeout period.  This usually indicates the bridge is unresponsive."""


# ---------------------------------------------------------------------------
# FroelingConnection
# ---------------------------------------------------------------------------

class FroelingConnection:
    """Manages a single async TCP connection to the Fröling serial bridge.

    The bridge is a TCP-to-serial adapter that forwards
    bytes between a TCP socket and the controller's COM1 service port.

    Usage pattern::

        conn = FroelingConnection(timeout=5.0)
        await conn.connect("192.168.1.100", 8899)
        async with conn.lock:
            await conn.write_frame(frame_bytes)
            command, size, payload_crc = await conn.read_response()
        await conn.disconnect()

    Thread safety:
        All send/receive operations must be protected by ``conn.lock``.
        The lock is deliberately *not* acquired inside write_frame /
        read_response so that callers can atomically pair a write with its
        corresponding read without the lock being released in between.

    Parameters
    ----------
    timeout:
        Seconds to wait for each individual asyncio read before raising
        :exc:`TimeoutError`.  Defaults to 5.0 s.
    """

    def __init__(self, timeout: float = 5.0) -> None:
        # Configurable per-read timeout in seconds.
        self._timeout: float = timeout

        # asyncio StreamReader / StreamWriter set by connect().
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None

        # Mutex so the high-level client can lock a request/response pair.
        self._lock: asyncio.Lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        """Return True when the underlying TCP stream appears to be open.

        Checks that a writer exists and that it has not been closed
        (asyncio.StreamWriter.is_closing() returns True once close() has been
        called).
        """
        return self._writer is not None and not self._writer.is_closing()

    @property
    def lock(self) -> asyncio.Lock:
        """Return the asyncio.Lock used to serialise request/response pairs."""
        return self._lock

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self, host: str, port: int) -> None:
        """Open a TCP connection to the serial bridge.

        Parameters
        ----------
        host:
            Hostname or IP address of the TCP-to-serial bridge.
        port:
            TCP port the bridge listens on (commonly 8899).

        Raises
        ------
        ConnectionError
            If the TCP handshake fails (OSError / ConnectionRefusedError from
            asyncio, re-raised as our own ConnectionError for clean API).
        """
        _log.debug("Connecting to %s:%d (timeout=%.1f s)", host, port, self._timeout)
        try:
            self._reader, self._writer = await asyncio.open_connection(host, port)
        except OSError as exc:
            raise ConnectionError(
                f"Cannot connect to Fröling bridge at {host}:{port}: {exc}"
            ) from exc
        _log.info("Connected to Fröling bridge at %s:%d", host, port)

    async def connect_serial(self, device: str, baudrate: int = 57600) -> None:
        """Open a direct USB serial connection to the heater's COM1 port.

        Uses pyserial-asyncio to open the serial device. The returned
        reader/writer streams are API-compatible with asyncio TCP streams,
        so the rest of the protocol code works unchanged.

        Parameters
        ----------
        device:
            Serial device path (e.g., /dev/ttyUSB0, COM3).
        baudrate:
            Baud rate -- always 57600 for the Lambdatronic protocol.

        Raises
        ------
        ConnectionError
            If the serial port cannot be opened.
        """
        _log.debug("Opening serial port %s at %d baud", device, baudrate)
        try:
            import serial_asyncio_fast as serial_asyncio
        except ImportError:
            try:
                import serial_asyncio
            except ImportError:
                raise ConnectionError(
                    "pyserial-asyncio is required for USB serial connections. "
                    "Install it with: pip install pyserial-asyncio"
                )
        try:
            self._reader, self._writer = await serial_asyncio.open_serial_connection(
                url=device,
                baudrate=baudrate,
                bytesize=8,       # 8 data bits
                parity="N",       # No parity
                stopbits=1,       # 1 stop bit
                xonxoff=False,    # No software flow control
                rtscts=False,     # No hardware flow control
            )
        except Exception as exc:
            raise ConnectionError(
                f"Cannot open serial port {device}: {exc}"
            ) from exc
        _log.info("Connected to Fröling heater via serial port %s", device)

    async def disconnect(self) -> None:
        """Close the TCP connection gracefully.

        Calls writer.close() and awaits writer.wait_closed() so that the OS
        socket is fully released before this coroutine returns.  Safe to call
        even when already disconnected.
        """
        if self._writer is None:
            return
        _log.debug("Disconnecting from Fröling bridge")
        try:
            self._writer.close()
            await self._writer.wait_closed()
        except OSError:
            # Ignore errors during teardown – the connection may already be gone.
            pass
        finally:
            # Always clear references so is_connected returns False.
            self._writer = None
            self._reader = None
        _log.info("Disconnected from Fröling bridge")

    # ------------------------------------------------------------------
    # Frame I/O
    # ------------------------------------------------------------------

    async def write_frame(self, frame: bytes) -> None:
        """Write a complete escaped frame to the TCP stream.

        Parameters
        ----------
        frame:
            A fully-built, escaped wire frame as returned by
            :func:`~pyfroeling.protocol.build_frame`.

        Raises
        ------
        ConnectionError
            If the writer is not available (not connected).
        """
        if self._writer is None:
            raise ConnectionError("Not connected – call connect() first.")
        _log.debug("TX %d bytes: %s", len(frame), frame.hex())
        self._writer.write(frame)
        # drain() flushes the write buffer and yields to the event loop,
        # ensuring the bytes are handed off to the OS network stack.
        await self._writer.drain()

    async def read_response(self) -> tuple[int, int, bytes]:
        """Read one complete response frame from the TCP stream.

        The Fröling protocol frames start with a fixed 2-byte sync word
        (0x02, 0xFD) that is transmitted WITHOUT escaping.  Everything after
        the sync word is escaped on the wire and must be un-escaped as we read.

        Reading strategy:
          1. Read the 2 raw sync bytes.  Verify they equal COMM_ID.
          2. Un-escape-read 3 bytes → size_hi, size_lo, command_byte.
          3. Un-escape-read ``size`` bytes → payload bytes + 1 CRC byte.

        Returns
        -------
        tuple[int, int, bytes]
            ``(command, size, payload_with_crc)`` where:
            - ``command`` is the raw command byte value (int).
            - ``size`` is the value from the size field (= len(payload)+1).
            - ``payload_with_crc`` is the un-escaped payload bytes followed by
              the single CRC byte at the end.

        Raises
        ------
        ConnectionError
            If the reader is not available.
        TimeoutError
            If any individual read exceeds the configured timeout.
        ValueError
            If the sync word does not match COMM_ID.
        """
        if self._reader is None:
            raise ConnectionError("Not connected – call connect() first.")

        # --- Step 1: read the 2-byte sync word (raw, no un-escaping) ---
        sync_raw: bytes = await self._read_exact_raw(2)
        sync_val: int = int.from_bytes(sync_raw, "big")
        if sync_val != COMM_ID:
            raise ValueError(
                f"Bad sync word: expected 0x{COMM_ID:04X}, got 0x{sync_val:04X}"
            )

        # --- Step 2: un-escape-read 3 bytes: size(2) + command(1) ---
        header_body: bytearray = await self._read_and_unescape(3)
        size: int = int.from_bytes(header_body[0:2], "big")
        command: int = header_body[2]

        # --- Step 3: un-escape-read ``size`` bytes: payload + CRC ---
        payload_with_crc: bytearray = await self._read_and_unescape(size)

        _log.debug(
            "RX cmd=0x%02X size=%d payload+crc=%s",
            command, size, bytes(payload_with_crc).hex(),
        )
        return command, size, bytes(payload_with_crc)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _read_exact_raw(self, count: int) -> bytes:
        """Read exactly ``count`` bytes from the stream without un-escaping.

        Used only for reading the 2-byte raw sync word.

        Parameters
        ----------
        count:
            Number of raw bytes to read.

        Raises
        ------
        TimeoutError
            If the read does not complete within the configured timeout.
        ConnectionError
            If the stream closes before the requested bytes arrive.
        """
        try:
            data = await asyncio.wait_for(
                self._reader.readexactly(count),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError as exc:
            raise TimeoutError(
                f"Timeout waiting for {count} raw bytes from Fröling bridge."
            ) from exc
        except asyncio.IncompleteReadError as exc:
            raise ConnectionError(
                f"Connection closed while reading {count} raw bytes."
            ) from exc
        return data

    async def _read_and_unescape(self, count: int) -> bytearray:
        """Read exactly ``count`` *unescaped* bytes from the TCP stream.

        Reads wire bytes one-at-a-time, expanding two-byte escape sequences
        into the original single byte.  Continues until ``count`` unescaped
        bytes have been accumulated.

        Escape rules (mirrors ESCAPE_TABLE in const.py):
          - 0x02 followed by 0x00  → 0x02
          - 0x2B followed by 0x00  → 0x2B
          - 0xFE followed by 0x00  → 0xFE
          - 0xFE followed by 0x12  → 0x11  (XON)
          - 0xFE followed by 0x14  → 0x13  (XOFF)

        Any other byte passes through unchanged (one wire byte = one output
        byte).

        Parameters
        ----------
        count:
            Number of *unescaped* bytes to return.

        Returns
        -------
        bytearray
            Exactly ``count`` unescaped bytes.

        Raises
        ------
        TimeoutError
            If any individual byte read exceeds the timeout.
        ConnectionError
            If the stream closes prematurely.
        ValueError
            If 0xFE is followed by an unrecognised second byte.
        """
        # Bytes that indicate the start of a two-byte escape sequence.
        # 0x02 and 0x2B each escape themselves (followed by 0x00).
        # 0xFE is the escape prefix for XON, XOFF, and itself.
        SELF_ESCAPE_FIRST: frozenset[int] = frozenset({0x02, 0x2B})

        result = bytearray()
        while len(result) < count:
            # Read one wire byte.
            b: int = await self._read_one_byte()

            if b in SELF_ESCAPE_FIRST:
                # These bytes escape themselves: the next byte must be 0x00.
                # The decoded value is the first byte itself.
                second: int = await self._read_one_byte()
                if second != 0x00:
                    # Not a valid escape – treat the first byte literally and
                    # "push back" the second byte by appending both.
                    # In practice the protocol should never do this, but being
                    # lenient here avoids losing bytes on unexpected traffic.
                    result.append(b)
                    if len(result) < count:
                        result.append(second)
                else:
                    # Valid self-escape: decoded value = first byte.
                    result.append(b)

            elif b == 0xFE:
                # 0xFE is the generic escape prefix; the second byte determines
                # which original byte is being represented.
                second = await self._read_one_byte()
                if second == 0x00:
                    result.append(0xFE)       # 0xFE 0x00 → 0xFE
                elif second == 0x12:
                    result.append(0x11)       # 0xFE 0x12 → 0x11 (XON)
                elif second == 0x14:
                    result.append(0x13)       # 0xFE 0x14 → 0x13 (XOFF)
                else:
                    raise ValueError(
                        f"Unknown escape sequence: 0xFE 0x{second:02X}"
                    )

            else:
                # Regular byte: one wire byte → one output byte.
                result.append(b)

        return result

    async def _read_one_byte(self) -> int:
        """Read a single raw byte from the stream.

        Wraps asyncio.StreamReader.readexactly(1) with timeout handling.

        Returns
        -------
        int
            The byte value in range [0, 255].

        Raises
        ------
        TimeoutError
            If the byte does not arrive within the configured timeout.
        ConnectionError
            If the stream closes before delivering the byte.
        """
        try:
            data = await asyncio.wait_for(
                self._reader.readexactly(1),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError as exc:
            raise TimeoutError(
                "Timeout waiting for byte from Fröling bridge."
            ) from exc
        except asyncio.IncompleteReadError as exc:
            raise ConnectionError(
                "Connection closed while waiting for byte."
            ) from exc
        return data[0]
