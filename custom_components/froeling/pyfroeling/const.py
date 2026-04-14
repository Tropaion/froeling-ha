"""Protocol constants for the Fröling Lambdatronic P/S 3200 binary protocol.

The Fröling heater communicates over a proprietary binary serial protocol on its
COM1 service port. This module contains all constants, enumerations, lookup
tables, and state mappings derived from reverse-engineering the linux-p4d project
(https://github.com/4projects/linux-p4d).

Primary reference files in linux-p4d:
  - lib/service.h  : Command codes and menu-structure type codes (lines 90-132)
  - lib/service.c  : State table / state name strings (lines 21-89)
  - lib/common.c   : CRC algorithm and byte-escaping details (lines 1804-1813)
"""

from __future__ import annotations

from enum import IntEnum


# ---------------------------------------------------------------------------
# Wire-level framing constants
# ---------------------------------------------------------------------------

# Sync word that marks the start of every frame (sent as two bytes: 0x02, 0xFD).
# This value is also known as the "communication ID" in the linux-p4d source.
COMM_ID: int = 0x02FD

# Number of bytes in the frame header (sync-ID 2 + size-field 2 + command 1).
HEADER_SIZE: int = 5

# Size in bytes of individual header sub-fields.
SIZE_CRC: int = 1       # CRC is a single byte appended at the end of the payload
SIZE_ID: int = 2        # Sync-word / communication ID
SIZE_SIZE: int = 2      # 16-bit big-endian payload-length field (includes CRC byte)
SIZE_COMMAND: int = 1   # Command byte immediately following the size field
SIZE_ADDRESS: int = 2   # 16-bit address used in many request payloads

# Upper bounds for payload and total frame size.
# A payload may carry at most 254 bytes (excluding the CRC byte that follows).
MAX_PAYLOAD_SIZE: int = 254
# Maximum total wire frame = COMM_ID(2) + size(2) + command(1) + payload(254) + CRC(1) + slack(2)
MAX_FRAME_SIZE: int = 262

# Sentinel address used when a register address is not known or not applicable.
ADDRESS_UNKNOWN: int = 0xFFFF


# ---------------------------------------------------------------------------
# Command codes  (service.h:90-132)
# ---------------------------------------------------------------------------

class Command(IntEnum):
    """Opcodes exchanged between host and Fröling controller.

    Each constant corresponds to the single command byte placed immediately
    after the 16-bit size field in an unescaped frame.  Both request frames
    (host → controller) and response frames (controller → host) carry the same
    command code, so the caller distinguishes direction by context.

    Source: linux-p4d/lib/service.h lines 90-132.
    """

    # Keepalive / synchronisation ping
    CHECK                = 0x22

    # Read a single sensor/measurement value by address
    GET_VALUE            = 0x30

    # Paginated sensor-value list: request first page
    GET_VALUE_LIST_FIRST = 0x31
    # Paginated sensor-value list: request subsequent pages
    GET_VALUE_LIST_NEXT  = 0x32

    # Paginated menu-structure list: request first page
    GET_MENU_LIST_FIRST  = 0x37
    # Paginated menu-structure list: request subsequent pages
    GET_MENU_LIST_NEXT   = 0x38

    # Write a parameter value by address
    SET_PARAMETER        = 0x39

    # Read a parameter value by address (returns limits/digits too)
    GET_PARAMETER        = 0x55

    # Read the controller's base configuration (boiler model, etc.)
    GET_BASE_SETUP       = 0x40

    # Read firmware version string
    GET_VERSION          = 0x41

    # Paginated timer-programme list: first page
    GET_TIMES_FIRST      = 0x42
    # Paginated timer-programme list: subsequent pages
    GET_TIMES_NEXT       = 0x43

    # Read digital output states
    GET_DIG_OUT          = 0x44
    # Read analogue output states
    GET_ANL_OUT          = 0x45
    # Read digital input states
    GET_DIG_IN           = 0x46

    # Paginated error-log list: first page
    GET_ERROR_FIRST      = 0x47
    # Paginated error-log list: subsequent pages
    GET_ERROR_NEXT       = 0x48

    # Read overall heater state (maps to STATE_TABLE below)
    GET_STATE            = 0x51

    # Write timer-programme entry
    SET_TIMES            = 0x50
    # Write the controller real-time clock
    SET_DATE_TIME        = 0x54

    # Write digital output state
    SET_DIG_OUT          = 0x58
    # Write analogue output state
    SET_ANL_OUT          = 0x59
    # Write digital input state
    SET_DIG_IN           = 0x5A

    # Read forced-operation mode flags
    GET_FORCE            = 0x5E
    # Write forced-operation mode flags
    SET_FORCE            = 0x7E


# ---------------------------------------------------------------------------
# Menu-structure type codes  (service.h, near the MenuStructType definition)
# ---------------------------------------------------------------------------

class MenuStructType(IntEnum):
    """Type codes used in menu-structure list responses (GET_MENU_LIST_*).

    These codes classify how each entry in the controller's menu tree should
    be interpreted (measured value, parameter, digital I/O, etc.).

    Source: linux-p4d/lib/service.h (MenuStructType enum).
    """

    # Standard measured value (e.g., temperature sensor reading)
    MESSWERT  = 0x03
    # Alternate measured-value type (used for certain extended sensors)
    MESSWERT1 = 0x46
    # Numeric configuration parameter
    PAR       = 0x07
    # Digital (boolean) configuration parameter
    PAR_DIG   = 0x08
    # Time-programme configuration parameter
    PAR_ZEIT  = 0x0A
    # Digital output channel
    DIG_OUT   = 0x11
    # Analogue output channel
    ANL_OUT   = 0x12
    # Digital input channel
    DIG_IN    = 0x13


# ---------------------------------------------------------------------------
# Byte-escaping tables  (lib/common.c, escape/unescape logic)
# ---------------------------------------------------------------------------
# The protocol uses a simple escape scheme so that special bytes inside the
# frame body cannot be confused with framing bytes.  The sync word starts with
# 0x02 and some flow-control bytes are also reserved, so they must be replaced
# with two-byte sequences before transmission.
#
# Escape rules (raw byte → escaped two-byte sequence):
#   0x02  →  0x02 0x00   (sync word first byte, escaped to itself + 0x00)
#   0x2B  →  0x2B 0x00   ('+' character, escaped to itself + 0x00)
#   0xFE  →  0xFE 0x00   (0xFE escaped to itself + 0x00)
#   0x11  →  0xFE 0x12   (XON / DC1 flow-control byte)
#   0x13  →  0xFE 0x14   (XOFF / DC3 flow-control byte)
#
# Note: escaping is applied ONLY to everything AFTER the 2-byte sync word.
#       The sync word itself is never escaped.

ESCAPE_TABLE: dict[int, bytes] = {
    0x02: b'\x02\x00',  # Sync-word byte: escape to avoid false frame starts
    0x2B: b'\x2B\x00',  # '+' character: protocol delimiter in some contexts
    0xFE: b'\xFE\x00',  # Escape prefix byte itself must be escaped
    0x11: b'\xFE\x12',  # XON (DC1) – RS-232 software flow control
    0x13: b'\xFE\x14',  # XOFF (DC3) – RS-232 software flow control
}

# Inverse mapping: escaped two-byte sequences → original single byte value.
# Built automatically from ESCAPE_TABLE so the two tables stay in sync.
UNESCAPE_TABLE: dict[bytes, int] = {v: k for k, v in ESCAPE_TABLE.items()}

# Frozenset of the raw byte values that MUST be escaped before transmission.
# Used for fast membership tests in the escaping loop.
ESCAPE_BYTES: frozenset[int] = frozenset(ESCAPE_TABLE.keys())

# Frozenset of the first bytes of any two-byte escaped sequence.
# Used during unescaping to detect that the next byte is part of an escape pair.
UNESCAPE_FIRST_BYTES: frozenset[int] = frozenset(seq[0] for seq in ESCAPE_TABLE.values())


# ---------------------------------------------------------------------------
# Heater state table  (service.c:21-89)
# ---------------------------------------------------------------------------
# The GET_STATE response contains a numeric state code.  This table maps those
# codes to their German display names exactly as shown on the controller LCD.
# Note: the code space is sparse (some values, e.g. 24-31, are not used).

STATE_TABLE: dict[int, str] = {
    0:  "Störung",               # General fault condition
    1:  "Brenner aus",           # Burner off
    2:  "Anheizen",              # Ignition / fire-up phase
    3:  "Heizen",                # Normal heating operation
    4:  "Feuerhaltung",          # Fire-hold (maintaining ember)
    5:  "Feuer Aus",             # Fire extinguished
    6:  "Tür offen",             # Door open
    7:  "Vorbereitung",          # Preparation phase
    8:  "Vorwärmphase",          # Pre-heat phase
    9:  "Zünden",                # Igniting
    10: "Abstellen Warten",      # Shutdown: waiting
    11: "Abstellen Warten 1",    # Shutdown: waiting (step 1)
    12: "Abstellen Einsch.",     # Shutdown: switch-off
    13: "Abstellen Warten 2",    # Shutdown: waiting (step 2)
    14: "Abstellen Einsch. 2",   # Shutdown: switch-off (step 2)
    15: "Abreinigen",            # Cleaning cycle
    16: "Std warten",            # Standard waiting
    17: "Saugheizen",            # Suction heating
    18: "Fehlzündung",           # Failed ignition
    19: "Betriebsbereit",        # Ready for operation
    20: "Rost schliessen",       # Closing grate
    21: "Stokerleeren",          # Emptying stoker
    22: "Vorheizen",             # Pre-heating
    23: "Saugen",                # Suction
    # Codes 24-31 are not defined in the reference source
    32: "Gebläsenachlauf 2",     # Fan run-on (phase 2)
    33: "Abgestellt",            # Switched off / standby
    34: "Nachzünden",            # Re-ignition
    35: "Zünden warten",         # Waiting to ignite
    36: "Fehlerbehebung",        # Troubleshooting
    37: "Fehlerbehebung 1",      # Troubleshooting step 1
    38: "Fehlerbehebung 2",      # Troubleshooting step 2
    39: "Fehlerbehebung 3",      # Troubleshooting step 3
    40: "Abstellen RSE",         # Shutdown via RSE interface
    41: "Störung STB",           # Fault: safety temperature limiter
    42: "Störung Kipprost",      # Fault: tilting grate
    43: "Störung RGTW",          # Fault: flue-gas temperature
    44: "Störung Tür",           # Fault: door
    45: "Störung Saugzug",       # Fault: induced draught
    46: "Störung HYDR",          # Fault: hydraulics
    47: "Fehler STB",            # Error: safety temperature limiter
    48: "Fehler Kipprost",       # Error: tilting grate
    49: "Fehler RGTW",           # Error: flue-gas temperature
    50: "Fehler Tür",            # Error: door
    51: "Fehler Saugzug",        # Error: induced draught
    52: "Fehler Hydraulik",      # Error: hydraulics
    53: "Fehler Stoker",         # Error: stoker
    54: "Störung Stoker",        # Fault: stoker
    55: "Fehlerbehebung 4",      # Troubleshooting step 4
    56: "Vorbelüften",           # Pre-ventilation
    57: "Störung Modul",         # Fault: module
    58: "Fehler Modul",          # Error: module
    59: "SHNB Tür offen",        # Safety heating-boiler: door open
    60: "SHNB ANHEIZEN",         # Safety heating-boiler: firing up
    61: "SHNB HEIZEN",           # Safety heating-boiler: heating
    62: "SHNB STB Notaus",       # Safety heating-boiler: emergency stop
    63: "SHNB Fehler Allgemein", # Safety heating-boiler: general error
    64: "SHNB Feuer Aus",        # Safety heating-boiler: fire extinguished
    65: "Selbsttest",            # Self-test
    66: "Fehlerb P2",            # Error recovery P2
    67: "Fehler Fallschluft",    # Error: drop-air valve
    68: "Störung Fallschluft",   # Fault: drop-air valve
    69: "Reinigen TMC",          # TMC cleaning
    70: "Onlinereinigen",        # Online cleaning
    71: "SH Anheizen",           # Solid-fuel heating: firing up
    72: "SH Heizen",             # Solid-fuel heating: heating
}

# ---------------------------------------------------------------------------
# Error / fault state codes
# ---------------------------------------------------------------------------
# A frozen set of state codes that represent an active fault or error condition.
# Any state whose display name begins with "Störung" (fault) or "Fehler" (error)
# is considered an error state.  Derived automatically so it stays in sync with
# STATE_TABLE updates.

ERROR_STATE_CODES: frozenset[int] = frozenset(
    code
    for code, name in STATE_TABLE.items()
    if name.startswith("Störung") or name.startswith("Fehler")
)
