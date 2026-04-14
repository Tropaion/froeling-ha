"""Async tests for the FroelingConnection TCP layer (Task 5).

These tests use unittest.mock to mock asyncio.open_connection so that no
real TCP connections are made.  Each test constructs a fake reader/writer
pair to exercise the connection lifecycle and byte-reading logic.

Test classes:
  - TestConnectionLifecycle  : connect / disconnect / is_connected
  - TestWriteFrame           : write_frame forwards bytes to the writer
  - TestReadAndUnescape      : _read_and_unescape correctly un-escapes bytes
  - TestReadResponse         : read_response orchestrates sync + header + body
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from custom_components.froeling.pyfroeling.connection import (
    ConnectionError,
    FroelingConnection,
    TimeoutError,
)
from custom_components.froeling.pyfroeling.const import COMM_ID


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_writer() -> MagicMock:
    """Create a mock asyncio.StreamWriter with the minimum interface."""
    writer = MagicMock()
    # is_closing() must return False by default (connection open).
    writer.is_closing.return_value = False
    # write() and drain() are the I/O methods.
    writer.write = MagicMock()
    writer.drain = AsyncMock()
    # close() and wait_closed() for teardown.
    writer.close = MagicMock()
    writer.wait_closed = AsyncMock()
    return writer


def _make_mock_reader(data_sequence: list[bytes]) -> MagicMock:
    """Create a mock asyncio.StreamReader whose readexactly() returns bytes
    from ``data_sequence`` in order.

    Each call to readexactly(n) pops the front of ``data_sequence`` and
    returns it.  The test must ensure the sequence matches the sizes requested.
    """
    reader = MagicMock()
    call_index = {"i": 0}

    async def fake_readexactly(n: int) -> bytes:
        if call_index["i"] >= len(data_sequence):
            raise asyncio.IncompleteReadError(b"", n)
        chunk = data_sequence[call_index["i"]]
        call_index["i"] += 1
        return chunk

    reader.readexactly = fake_readexactly
    return reader


# ===========================================================================
# TestConnectionLifecycle
# ===========================================================================

class TestConnectionLifecycle:
    """Verify connect / disconnect / is_connected behaviour."""

    @pytest.mark.asyncio
    async def test_is_connected_false_before_connect(self) -> None:
        """is_connected must be False when no connect() call has been made."""
        conn = FroelingConnection()
        assert conn.is_connected is False

    @pytest.mark.asyncio
    async def test_connect_stores_host_port(self) -> None:
        """After connect(), the writer is stored and is_connected returns True."""
        conn = FroelingConnection()
        mock_reader = MagicMock()
        mock_writer = _make_mock_writer()

        with patch(
            "custom_components.froeling.pyfroeling.connection.asyncio.open_connection",
            new=AsyncMock(return_value=(mock_reader, mock_writer)),
        ) as mock_open:
            await conn.connect("192.168.1.10", 8899)

            # asyncio.open_connection must have been called with host and port.
            mock_open.assert_called_once_with("192.168.1.10", 8899)

        assert conn.is_connected is True

    @pytest.mark.asyncio
    async def test_connect_raises_connection_error_on_oserror(self) -> None:
        """connect() must raise ConnectionError when asyncio.open_connection fails."""
        conn = FroelingConnection()

        with patch(
            "custom_components.froeling.pyfroeling.connection.asyncio.open_connection",
            new=AsyncMock(side_effect=OSError("refused")),
        ):
            with pytest.raises(ConnectionError, match="Cannot connect"):
                await conn.connect("192.168.1.10", 8899)

        assert conn.is_connected is False

    @pytest.mark.asyncio
    async def test_disconnect_closes_writer(self) -> None:
        """disconnect() calls writer.close() and wait_closed()."""
        conn = FroelingConnection()
        mock_reader = MagicMock()
        mock_writer = _make_mock_writer()

        with patch(
            "custom_components.froeling.pyfroeling.connection.asyncio.open_connection",
            new=AsyncMock(return_value=(mock_reader, mock_writer)),
        ):
            await conn.connect("localhost", 9000)

        assert conn.is_connected is True
        await conn.disconnect()

        # After disconnect, is_connected must return False.
        assert conn.is_connected is False
        mock_writer.close.assert_called_once()
        mock_writer.wait_closed.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_disconnect_safe_when_not_connected(self) -> None:
        """disconnect() must not raise when called without a prior connect()."""
        conn = FroelingConnection()
        # Should complete without exception.
        await conn.disconnect()

    @pytest.mark.asyncio
    async def test_is_connected_false_after_disconnect(self) -> None:
        """is_connected must be False after disconnect()."""
        conn = FroelingConnection()
        mock_writer = _make_mock_writer()
        mock_reader = MagicMock()

        with patch(
            "custom_components.froeling.pyfroeling.connection.asyncio.open_connection",
            new=AsyncMock(return_value=(mock_reader, mock_writer)),
        ):
            await conn.connect("localhost", 9000)

        await conn.disconnect()
        assert conn.is_connected is False

    @pytest.mark.asyncio
    async def test_is_connected_false_when_writer_is_closing(self) -> None:
        """is_connected returns False when the writer reports it is closing."""
        conn = FroelingConnection()
        mock_writer = _make_mock_writer()
        mock_writer.is_closing.return_value = True  # simulate closing state
        mock_reader = MagicMock()

        with patch(
            "custom_components.froeling.pyfroeling.connection.asyncio.open_connection",
            new=AsyncMock(return_value=(mock_reader, mock_writer)),
        ):
            await conn.connect("localhost", 9000)

        # Even though we "connected", is_closing=True means is_connected=False.
        assert conn.is_connected is False


# ===========================================================================
# TestWriteFrame
# ===========================================================================

class TestWriteFrame:
    """Verify that write_frame forwards bytes to the underlying writer."""

    @pytest.mark.asyncio
    async def test_write_frame_calls_writer_write(self) -> None:
        """write_frame must call writer.write with the exact frame bytes."""
        conn = FroelingConnection()
        mock_writer = _make_mock_writer()
        mock_reader = MagicMock()

        with patch(
            "custom_components.froeling.pyfroeling.connection.asyncio.open_connection",
            new=AsyncMock(return_value=(mock_reader, mock_writer)),
        ):
            await conn.connect("localhost", 9000)

        test_frame = b"\x02\xFD\x00\x01\x22\x66"
        await conn.write_frame(test_frame)

        mock_writer.write.assert_called_once_with(test_frame)
        mock_writer.drain.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_write_frame_raises_when_not_connected(self) -> None:
        """write_frame must raise ConnectionError when not connected."""
        conn = FroelingConnection()
        with pytest.raises(ConnectionError, match="Not connected"):
            await conn.write_frame(b"\x02\xFD")

    @pytest.mark.asyncio
    async def test_lock_property_returns_asyncio_lock(self) -> None:
        """The lock property must return an asyncio.Lock instance."""
        conn = FroelingConnection()
        assert isinstance(conn.lock, asyncio.Lock)


# ===========================================================================
# TestReadAndUnescape
# ===========================================================================

class TestReadAndUnescape:
    """Verify the _read_and_unescape helper handles all escape sequences."""

    async def _connected_conn(
        self, data_sequence: list[bytes]
    ) -> FroelingConnection:
        """Return a connected FroelingConnection backed by a mock reader."""
        conn = FroelingConnection(timeout=1.0)
        mock_reader = _make_mock_reader(data_sequence)
        mock_writer = _make_mock_writer()

        with patch(
            "custom_components.froeling.pyfroeling.connection.asyncio.open_connection",
            new=AsyncMock(return_value=(mock_reader, mock_writer)),
        ):
            await conn.connect("localhost", 9000)

        return conn

    @pytest.mark.asyncio
    async def test_passthrough_bytes_unchanged(self) -> None:
        """Regular (non-escape) bytes pass through un-modified."""
        # Feed three plain bytes; expect exactly three unescaped bytes back.
        conn = await self._connected_conn([b"\x30", b"\x41", b"\x51"])
        result = await conn._read_and_unescape(3)
        assert result == bytearray([0x30, 0x41, 0x51])

    @pytest.mark.asyncio
    async def test_unescape_0x02_0x00(self) -> None:
        """0x02 0x00 on the wire must decode to a single 0x02 byte."""
        conn = await self._connected_conn([b"\x02", b"\x00"])
        result = await conn._read_and_unescape(1)
        assert result == bytearray([0x02])

    @pytest.mark.asyncio
    async def test_unescape_0x2b_0x00(self) -> None:
        """0x2B 0x00 on the wire must decode to a single 0x2B byte."""
        conn = await self._connected_conn([b"\x2B", b"\x00"])
        result = await conn._read_and_unescape(1)
        assert result == bytearray([0x2B])

    @pytest.mark.asyncio
    async def test_unescape_0xfe_0x00(self) -> None:
        """0xFE 0x00 on the wire must decode to a single 0xFE byte."""
        conn = await self._connected_conn([b"\xFE", b"\x00"])
        result = await conn._read_and_unescape(1)
        assert result == bytearray([0xFE])

    @pytest.mark.asyncio
    async def test_unescape_0xfe_0x12_gives_xon(self) -> None:
        """0xFE 0x12 must decode to 0x11 (XON)."""
        conn = await self._connected_conn([b"\xFE", b"\x12"])
        result = await conn._read_and_unescape(1)
        assert result == bytearray([0x11])

    @pytest.mark.asyncio
    async def test_unescape_0xfe_0x14_gives_xoff(self) -> None:
        """0xFE 0x14 must decode to 0x13 (XOFF)."""
        conn = await self._connected_conn([b"\xFE", b"\x14"])
        result = await conn._read_and_unescape(1)
        assert result == bytearray([0x13])

    @pytest.mark.asyncio
    async def test_unescape_mixed_sequence(self) -> None:
        """Mixed escaped and plain bytes are all handled in one call."""
        # Wire: 0x30 (plain) | 0x02 0x00 (→ 0x02) | 0xFE 0x12 (→ 0x11)
        # Expected output: [0x30, 0x02, 0x11]
        conn = await self._connected_conn([
            b"\x30", b"\x02", b"\x00", b"\xFE", b"\x12"
        ])
        result = await conn._read_and_unescape(3)
        assert result == bytearray([0x30, 0x02, 0x11])

    @pytest.mark.asyncio
    async def test_unknown_fe_escape_raises_value_error(self) -> None:
        """0xFE followed by an unrecognised byte must raise ValueError."""
        conn = await self._connected_conn([b"\xFE", b"\x99"])
        with pytest.raises(ValueError, match="Unknown escape sequence"):
            await conn._read_and_unescape(1)


# ===========================================================================
# TestReadResponse
# ===========================================================================

class TestReadResponse:
    """Verify that read_response correctly parses a full response frame."""

    async def _conn_for_frame(self, frame_wire_bytes: list[bytes]) -> FroelingConnection:
        """Return a connected FroelingConnection that will yield the given bytes."""
        conn = FroelingConnection(timeout=1.0)
        mock_reader = _make_mock_reader(frame_wire_bytes)
        mock_writer = _make_mock_writer()

        with patch(
            "custom_components.froeling.pyfroeling.connection.asyncio.open_connection",
            new=AsyncMock(return_value=(mock_reader, mock_writer)),
        ):
            await conn.connect("localhost", 9000)

        return conn

    @pytest.mark.asyncio
    async def test_read_response_raises_when_not_connected(self) -> None:
        """read_response must raise ConnectionError when not connected."""
        conn = FroelingConnection()
        with pytest.raises(ConnectionError, match="Not connected"):
            await conn.read_response()

    @pytest.mark.asyncio
    async def test_read_response_bad_sync_raises_value_error(self) -> None:
        """read_response must raise ValueError when the sync word is wrong."""
        # Feed a bad sync word: 0x02FE instead of 0x02FD.
        # We need subsequent bytes for the reader not to stall, but the
        # ValueError should fire as soon as the sync is validated.
        conn = await self._conn_for_frame([
            b"\x02\xFE",        # BAD sync word
            b"\x00", b"\x01", b"\x22",  # size(2) + command(1) – won't be reached
        ])
        with pytest.raises(ValueError, match="Bad sync word"):
            await conn.read_response()

    @pytest.mark.asyncio
    async def test_read_response_simple_frame(self) -> None:
        """read_response correctly parses a simple un-escaped CHECK response.

        We simulate a CHECK response with payload b"Tescht ;-)" + CRC 0x00
        (CRC value doesn't matter here – connection layer doesn't validate it).

        Wire layout (unescaped values, no special bytes so no escaping needed):
          Sync:     0x02 0xFD                       (2 bytes raw)
          Size:     0x00 0x0B  (= 10 payload + 1 CRC = 11)
          Command:  0x22       (CHECK)
          Payload:  b"Tescht ;-)" (10 bytes)
          CRC:      0xAB       (arbitrary – not validated by connection layer)
        """
        check_payload = b"Tescht ;-)"
        crc_byte = b"\xAB"
        size = len(check_payload) + 1  # +1 for CRC

        # Feed byte-by-byte (readexactly is called one byte at a time inside
        # _read_and_unescape).  We provide multi-byte chunks for the raw reads.
        raw_bytes: list[bytes] = []
        # Sync (2 bytes, raw)
        raw_bytes.append(b"\x02\xFD")
        # Remaining header: size (2 bytes) + command (1 byte) – unescaped/plain
        for b in [size >> 8, size & 0xFF, 0x22]:
            raw_bytes.append(bytes([b]))
        # Payload + CRC: each as individual byte (no escape sequences here)
        for b in check_payload + crc_byte:
            raw_bytes.append(bytes([b]))

        conn = await self._conn_for_frame(raw_bytes)
        command, s, payload_with_crc = await conn.read_response()

        assert command == 0x22  # CHECK
        assert s == size
        assert payload_with_crc == check_payload + crc_byte

    @pytest.mark.asyncio
    async def test_read_response_with_escape_in_payload(self) -> None:
        """read_response correctly un-escapes 0x02 0x00 in the payload.

        We build a minimal frame where the payload is the single byte 0x02,
        which on the wire is encoded as the two bytes 0x02 0x00.

        Unescaped frame:
          Sync:     0x02 0xFD
          Size:     0x00 0x02  (1 payload byte + 1 CRC byte)
          Command:  0x30       (GET_VALUE)
          Payload:  0x02       (raw; wire form: 0x02 0x00)
          CRC:      0x00

        Wire bytes fed to reader:
          [0x02 0xFD]  raw sync
          [0x00]       size byte 0 (unescaped)
          [0x02 0x00]  size byte 1 – but wait, 0x02 is an escape prefix!
          ...
        """
        # Let's use a simpler payload that avoids escaping in the header.
        # Size = 2 (1 payload + 1 CRC) → bytes 0x00 0x02 – 0x02 is escaped!
        # Easier: make size = 0x00 0x05 so header bytes are plain.
        # Payload = [0x30, 0x02 (escaped→0x02 0x00), 0x41] + CRC = 4 plain + CRC
        # size = 4 (3 payload bytes + 1 CRC)
        # Unescaped: cmd=0x30, payload=[0x30,0x02,0x41], crc=0xAA

        raw_bytes: list[bytes] = []
        raw_bytes.append(b"\x02\xFD")          # sync

        # Header (size=4, cmd=0x30) – individual bytes for _read_and_unescape
        for b in [0x00, 0x04, 0x30]:
            raw_bytes.append(bytes([b]))

        # Payload: 0x30 (plain), 0x02 0x00 (escaped 0x02), 0x41 (plain)
        for b in [0x30, 0x02, 0x00, 0x41]:
            raw_bytes.append(bytes([b]))

        # CRC byte (plain)
        raw_bytes.append(b"\xAA")

        conn = await self._conn_for_frame(raw_bytes)
        command, size, payload_with_crc = await conn.read_response()

        assert command == 0x30  # GET_VALUE
        assert size == 4
        # Payload (3 bytes) + CRC (1 byte): 0x30, 0x02 (unescaped), 0x41, 0xAA
        assert payload_with_crc == bytes([0x30, 0x02, 0x41, 0xAA])
