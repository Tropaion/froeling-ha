"""Low-level protocol encoder/decoder for the Fröling binary serial protocol.

This module provides the fundamental building blocks for constructing and
dissecting wire frames exchanged with a Fröling Lambdatronic P/S 3200 controller.

Wire frame layout (unescaped)
------------------------------
    ┌──────────────┬───────────────────┬───────────┬─────────────┬─────┐
    │  Sync ID     │   Size (BE u16)   │  Command  │   Payload   │ CRC │
    │  2 bytes     │   2 bytes         │  1 byte   │  0-254 B    │ 1 B │
    └──────────────┴───────────────────┴───────────┴─────────────┴─────┘

    - Sync ID   : 0x02FD (COMM_ID), never escaped.
    - Size      : big-endian uint16 = len(payload) + SIZE_CRC (i.e. +1 for the
                  trailing CRC byte).  The size field itself is also escaped.
    - Command   : one byte from the Command enum.
    - Payload   : command-specific bytes (0 to MAX_PAYLOAD_SIZE bytes).
    - CRC       : single byte XOR checksum over command+payload (see calculate_crc).

Escaping
--------
All bytes after the 2-byte sync word are escaped before transmission using the
rules in ESCAPE_TABLE.  The sync word is transmitted raw so the receiver can
reliably detect frame boundaries.

CRC algorithm
--------------
From linux-p4d lib/common.c lines 1804-1813:

    crc = 0
    for each byte b:
        dummy = (b * 2) & 0xFF
        crc   = crc ^ (b ^ dummy)
    return crc & 0xFF

This is equivalent to XOR-folding: for each byte, XOR crc with (b XOR (b<<1)&0xFF).
"""

from __future__ import annotations

from .const import (
    COMM_ID,
    ESCAPE_BYTES,
    ESCAPE_TABLE,
    HEADER_SIZE,
    MAX_PAYLOAD_SIZE,
    SIZE_CRC,
    UNESCAPE_FIRST_BYTES,
    UNESCAPE_TABLE,
    Command,
)


# ---------------------------------------------------------------------------
# CRC
# ---------------------------------------------------------------------------

def calculate_crc(data: bytes) -> int:
    """Calculate the single-byte CRC over a sequence of bytes.

    Algorithm (from linux-p4d/lib/common.c lines 1804-1813)::

        crc = 0
        for b in data:
            dummy = (b * 2) & 0xFF   # left-shift by 1, keep lower 8 bits
            crc   = crc ^ (b ^ dummy)  # XOR current crc with b XOR (b<<1)
        return crc & 0xFF

    The CRC is computed over the *unescaped* frame bytes starting from the
    command byte through to (but not including) the CRC byte itself.

    Parameters
    ----------
    data:
        The raw (unescaped) bytes to checksum.

    Returns
    -------
    int
        Single-byte CRC value in the range [0, 255].
    """
    crc: int = 0
    for b in data:
        # Multiply by 2 and mask to 8 bits (same as left-shift by 1 mod 256).
        dummy: int = (b * 2) & 0xFF
        # XOR the running CRC with (byte XOR double-byte).
        crc = crc ^ (b ^ dummy)
    return crc & 0xFF


# ---------------------------------------------------------------------------
# Byte escaping / unescaping
# ---------------------------------------------------------------------------

def escape_bytes(data: bytes) -> bytes:
    """Apply protocol byte-escaping to a sequence of bytes.

    Special bytes that could be mistaken for framing markers or RS-232
    flow-control characters are replaced with two-byte escape sequences as
    defined in :data:`~pyfroeling.const.ESCAPE_TABLE`.

    This function must be called on everything that follows the 2-byte sync
    word (i.e. the size field, command byte, payload, and CRC byte).

    Parameters
    ----------
    data:
        Raw (unescaped) bytes to process.

    Returns
    -------
    bytes
        Escaped byte sequence ready for transmission.
    """
    result = bytearray()
    for b in data:
        if b in ESCAPE_BYTES:
            # Replace this byte with its two-byte escape sequence.
            result.extend(ESCAPE_TABLE[b])
        else:
            # Regular byte: transmit as-is.
            result.append(b)
    return bytes(result)


def unescape_bytes(data: bytes) -> bytes:
    """Reverse protocol byte-escaping on a received byte sequence.

    Scans ``data`` for two-byte escape sequences defined in
    :data:`~pyfroeling.const.UNESCAPE_TABLE` and replaces each pair with the
    original single byte.

    This function is applied to received data AFTER the 2-byte sync word has
    been stripped (the sync word is never escaped).

    Parameters
    ----------
    data:
        Escaped bytes as received from the wire (sync word already removed).

    Returns
    -------
    bytes
        Unescaped byte sequence.

    Raises
    ------
    ValueError
        If a recognised escape-prefix byte is found but the following byte does
        not form a valid two-byte escape sequence.
    """
    result = bytearray()
    i = 0
    while i < len(data):
        b = data[i]
        if b in UNESCAPE_FIRST_BYTES:
            # This byte is the first byte of a potential escape sequence.
            # We need to look at the next byte to confirm.
            if i + 1 >= len(data):
                raise ValueError(
                    f"Truncated escape sequence: byte 0x{b:02X} at index {i} "
                    "has no following byte."
                )
            pair = bytes([b, data[i + 1]])
            if pair in UNESCAPE_TABLE:
                # Valid escape sequence: replace with the original byte.
                result.append(UNESCAPE_TABLE[pair])
                i += 2  # consume both bytes
                continue
            # The first byte is in UNESCAPE_FIRST_BYTES but the pair is not
            # recognised.  Treat the byte literally (not all occurrences of
            # e.g. 0x02 after the sync are necessarily escapes).
        result.append(b)
        i += 1
    return bytes(result)


# ---------------------------------------------------------------------------
# Frame building
# ---------------------------------------------------------------------------

def build_frame(command: Command, payload: bytes = b"") -> bytes:
    """Build a complete escaped wire frame ready for transmission.

    Constructs the raw (unescaped) frame, computes the CRC, then escapes
    everything after the 2-byte sync word before returning.

    Frame structure (unescaped)::

        [COMM_ID hi][COMM_ID lo] [size hi][size lo] [cmd] [payload...] [crc]

    where ``size`` = len(payload) + 1 (accounting for the CRC byte).

    Parameters
    ----------
    command:
        The :class:`~pyfroeling.const.Command` opcode for this frame.
    payload:
        Optional command-specific payload bytes (default: empty).

    Returns
    -------
    bytes
        Complete escaped frame including sync word, ready to write to the socket.

    Raises
    ------
    ValueError
        If ``payload`` exceeds :data:`~pyfroeling.const.MAX_PAYLOAD_SIZE` bytes.
    """
    if len(payload) > MAX_PAYLOAD_SIZE:
        raise ValueError(
            f"Payload too large: {len(payload)} bytes > MAX_PAYLOAD_SIZE "
            f"({MAX_PAYLOAD_SIZE})."
        )

    # The size field encodes the number of bytes that follow it in the frame
    # (command byte + payload + CRC byte).
    size: int = len(payload) + SIZE_CRC  # +1 for the CRC byte

    # Build the unescaped body: size (2 bytes, BE) + command (1 byte) + payload.
    # The CRC is computed over command + payload only.
    crc_input: bytes = bytes([command]) + payload
    crc: int = calculate_crc(crc_input)

    # Assemble the full unescaped frame body (everything after the sync word).
    body: bytes = (
        size.to_bytes(2, byteorder="big")  # 16-bit big-endian size field
        + bytes([command])                  # command byte
        + payload                           # variable-length payload
        + bytes([crc])                      # trailing CRC byte
    )

    # The sync word is transmitted raw (never escaped).
    sync: bytes = COMM_ID.to_bytes(2, byteorder="big")  # 0x02, 0xFD

    # Escape all bytes that follow the sync word.
    escaped_body: bytes = escape_bytes(body)

    return sync + escaped_body


# ---------------------------------------------------------------------------
# Frame parsing
# ---------------------------------------------------------------------------

def parse_frame_header(data: bytes) -> tuple[Command, int]:
    """Parse the 5-byte unescaped frame header.

    Validates the sync word and extracts the command opcode and the total
    payload size (including the trailing CRC byte).

    This function expects *unescaped* bytes — the caller must unescape the
    received data (excluding the first 2 sync bytes) before calling here.

    Parameters
    ----------
    data:
        Exactly :data:`~pyfroeling.const.HEADER_SIZE` (5) unescaped bytes
        representing the frame header::

            [sync hi][sync lo][size hi][size lo][command]

    Returns
    -------
    tuple[Command, int]
        A tuple of ``(command, payload_size_including_crc)`` where
        ``payload_size_including_crc`` is the value from the size field and
        equals ``len(actual_payload) + 1``.

    Raises
    ------
    ValueError
        If ``data`` is not exactly HEADER_SIZE bytes, if the sync word does
        not match COMM_ID, or if the command byte is not a known Command value.
    """
    if len(data) != HEADER_SIZE:
        raise ValueError(
            f"Header must be exactly {HEADER_SIZE} bytes, got {len(data)}."
        )

    # --- Validate sync word (bytes 0-1) ---
    sync_received: int = int.from_bytes(data[0:2], byteorder="big")
    if sync_received != COMM_ID:
        raise ValueError(
            f"Invalid sync word: expected 0x{COMM_ID:04X}, "
            f"got 0x{sync_received:04X}."
        )

    # --- Extract size field (bytes 2-3, big-endian) ---
    # This is len(payload) + 1 (the +1 accounts for the CRC byte).
    payload_size_including_crc: int = int.from_bytes(data[2:4], byteorder="big")

    # --- Extract and validate command byte (byte 4) ---
    command_byte: int = data[4]
    try:
        command = Command(command_byte)
    except ValueError as exc:
        raise ValueError(
            f"Unknown command byte: 0x{command_byte:02X}."
        ) from exc

    return command, payload_size_including_crc
