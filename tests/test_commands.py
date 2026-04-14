"""Tests for command builders and response parsers (Task 6).

Test classes:
  - TestRequestBuilders       : all build_* functions
  - TestParseStateResponse    : parse_state_response
  - TestParseVersionResponse  : parse_version_response
  - TestParseValueResponse    : parse_value_response (signed int)
  - TestParseValueSpecResponse: parse_value_spec_response (pagination)
  - TestParseErrorResponse    : parse_error_response (pagination)
  - TestParseIoResponse       : parse_io_response
  - TestParseParameterResponse: parse_parameter_response
"""

from __future__ import annotations

import struct
from datetime import datetime

import pytest

from custom_components.froeling.pyfroeling.commands import (
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
from custom_components.froeling.pyfroeling.const import Command
from custom_components.froeling.pyfroeling.models import ErrorState


# ===========================================================================
# TestRequestBuilders
# ===========================================================================

class TestRequestBuilders:
    """Verify that all build_* functions return the correct (command, payload)."""

    def test_check_request_command(self) -> None:
        """build_check_request must return Command.CHECK."""
        cmd, _ = build_check_request()
        assert cmd == Command.CHECK

    def test_check_request_payload_text(self) -> None:
        """build_check_request payload must be b'Tescht ;-)'."""
        _, payload = build_check_request()
        assert payload == b"Tescht ;-)"

    def test_get_state_command(self) -> None:
        """build_get_state_request must return Command.GET_STATE."""
        cmd, _ = build_get_state_request()
        assert cmd == Command.GET_STATE

    def test_get_state_empty_payload(self) -> None:
        """build_get_state_request must have an empty payload."""
        _, payload = build_get_state_request()
        assert payload == b""

    def test_get_version_command(self) -> None:
        """build_get_version_request must return Command.GET_VERSION."""
        cmd, _ = build_get_version_request()
        assert cmd == Command.GET_VERSION

    def test_get_version_empty_payload(self) -> None:
        """build_get_version_request must have an empty payload."""
        _, payload = build_get_version_request()
        assert payload == b""

    def test_get_value_command(self) -> None:
        """build_get_value_request must return Command.GET_VALUE."""
        cmd, _ = build_get_value_request(0x0010)
        assert cmd == Command.GET_VALUE

    def test_get_value_encodes_address_big_endian(self) -> None:
        """build_get_value_request encodes the address as big-endian uint16."""
        _, payload = build_get_value_request(0x00A4)
        assert payload == bytes([0x00, 0xA4])

    def test_get_value_address_0x0000(self) -> None:
        """Address 0x0000 encodes to two zero bytes."""
        _, payload = build_get_value_request(0x0000)
        assert payload == b"\x00\x00"

    def test_get_value_address_0xFFFF(self) -> None:
        """Address 0xFFFF encodes to two 0xFF bytes."""
        _, payload = build_get_value_request(0xFFFF)
        assert payload == b"\xFF\xFF"

    def test_get_value_list_first_uses_first_command(self) -> None:
        """build_get_value_list_request(first=True) must use GET_VALUE_LIST_FIRST."""
        cmd, _ = build_get_value_list_request(first=True)
        assert cmd == Command.GET_VALUE_LIST_FIRST

    def test_get_value_list_next_uses_next_command(self) -> None:
        """build_get_value_list_request(first=False) must use GET_VALUE_LIST_NEXT."""
        cmd, _ = build_get_value_list_request(first=False)
        assert cmd == Command.GET_VALUE_LIST_NEXT

    def test_get_value_list_empty_payload(self) -> None:
        """Both GET_VALUE_LIST variants have empty payloads."""
        _, p1 = build_get_value_list_request(first=True)
        _, p2 = build_get_value_list_request(first=False)
        assert p1 == b""
        assert p2 == b""

    def test_get_parameter_command(self) -> None:
        cmd, _ = build_get_parameter_request(0x0100)
        assert cmd == Command.GET_PARAMETER

    def test_get_parameter_encodes_address(self) -> None:
        _, payload = build_get_parameter_request(0x0100)
        assert payload == b"\x01\x00"

    def test_get_dig_out_command(self) -> None:
        cmd, _ = build_get_dig_out_request(0x0005)
        assert cmd == Command.GET_DIG_OUT

    def test_get_dig_out_address_encoding(self) -> None:
        _, payload = build_get_dig_out_request(0x0005)
        assert payload == b"\x00\x05"

    def test_get_anl_out_command(self) -> None:
        cmd, _ = build_get_anl_out_request(0x000A)
        assert cmd == Command.GET_ANL_OUT

    def test_get_dig_in_command(self) -> None:
        cmd, _ = build_get_dig_in_request(0x000B)
        assert cmd == Command.GET_DIG_IN

    def test_get_error_first_command(self) -> None:
        cmd, _ = build_get_error_request(first=True)
        assert cmd == Command.GET_ERROR_FIRST

    def test_get_error_next_command(self) -> None:
        cmd, _ = build_get_error_request(first=False)
        assert cmd == Command.GET_ERROR_NEXT

    def test_get_error_empty_payload(self) -> None:
        _, p1 = build_get_error_request(first=True)
        _, p2 = build_get_error_request(first=False)
        assert p1 == b""
        assert p2 == b""


# ===========================================================================
# TestParseStateResponse
# ===========================================================================

class TestParseStateResponse:
    """Verify parse_state_response correctly splits mode/state/text."""

    def _make_payload(self, mode: int, state: int, text: str) -> bytes:
        """Construct a GET_STATE payload from components."""
        return bytes([mode, state]) + text.encode("latin-1")

    def test_mode_and_state_extracted(self) -> None:
        """First byte → mode, second byte → state."""
        payload = self._make_payload(1, 3, "Automatik;Heizen")
        result = parse_state_response(payload)
        assert result["mode"] == 1
        assert result["state"] == 3

    def test_text_split_on_semicolon(self) -> None:
        """Text field is split on ';' into mode_text and state_text."""
        payload = self._make_payload(1, 3, "Automatik;Heizen")
        result = parse_state_response(payload)
        assert result["mode_text"] == "Automatik"
        assert result["state_text"] == "Heizen"

    def test_state_0_stoerung(self) -> None:
        """State 0 is the general fault state."""
        payload = self._make_payload(0, 0, "Manuell;Störung")
        result = parse_state_response(payload)
        assert result["state"] == 0
        assert result["state_text"] == "Störung"

    def test_mode_text_stripped(self) -> None:
        """Leading/trailing whitespace in mode_text is stripped."""
        payload = self._make_payload(2, 19, " Auto ; Betriebsbereit ")
        result = parse_state_response(payload)
        assert result["mode_text"] == "Auto"
        assert result["state_text"] == "Betriebsbereit"

    def test_missing_semicolon_fallback(self) -> None:
        """If no ';' in text, mode_text gets the whole string and state_text is empty."""
        payload = self._make_payload(0, 0, "NoSemicolon")
        result = parse_state_response(payload)
        assert result["mode_text"] == "NoSemicolon"
        assert result["state_text"] == ""


# ===========================================================================
# TestParseVersionResponse
# ===========================================================================

class TestParseVersionResponse:
    """Verify parse_version_response produces a correct version string and datetime."""

    def _make_payload(
        self,
        v1: int, v2: int, v3: int, v4: int,
        ss: int, mm: int, hh: int,
        dd: int, mo: int, dow: int, yy: int,
    ) -> bytes:
        return bytes([v1, v2, v3, v4, ss, mm, hh, dd, mo, dow, yy])

    def test_version_string_format(self) -> None:
        """Version is formatted as 'xx.xx.xx.xx' hex pairs."""
        payload = self._make_payload(3, 12, 4, 1, 0, 0, 0, 1, 1, 1, 24)
        result = parse_version_response(payload)
        assert result["version"] == "03.0c.04.01"

    def test_year_is_2000_plus_yy(self) -> None:
        """Year in the response is YY (2-digit); full year = 2000 + YY."""
        payload = self._make_payload(1, 0, 0, 0, 30, 15, 10, 14, 4, 1, 25)
        result = parse_version_response(payload)
        assert result["datetime"].year == 2025

    def test_datetime_components(self) -> None:
        """All datetime components are parsed correctly from the payload."""
        # SS=30, MM=45, HH=8, DD=14, MO=4, DOW=1(Mon), YY=24 → 2024
        payload = self._make_payload(1, 0, 0, 0, 30, 45, 8, 14, 4, 1, 24)
        result = parse_version_response(payload)
        dt: datetime = result["datetime"]
        assert dt.year == 2024
        assert dt.month == 4
        assert dt.day == 14
        assert dt.hour == 8
        assert dt.minute == 45
        assert dt.second == 30

    def test_version_string_zero_bytes(self) -> None:
        """Version bytes all 0 → '00.00.00.00'."""
        payload = self._make_payload(0, 0, 0, 0, 0, 0, 0, 1, 1, 0, 0)
        result = parse_version_response(payload)
        assert result["version"] == "00.00.00.00"


# ===========================================================================
# TestParseValueResponse
# ===========================================================================

class TestParseValueResponse:
    """Verify parse_value_response handles positive and negative values."""

    def test_positive_value(self) -> None:
        """A positive raw value is decoded correctly."""
        # 0x00A4 = 164 decimal
        payload = struct.pack(">h", 164)
        assert parse_value_response(payload) == 164

    def test_negative_value(self) -> None:
        """A negative raw value (signed 16-bit) is decoded correctly."""
        # -5 in signed big-endian 16-bit = 0xFFFB
        payload = struct.pack(">h", -5)
        assert parse_value_response(payload) == -5

    def test_zero_value(self) -> None:
        """Raw value 0 decodes to 0."""
        payload = struct.pack(">h", 0)
        assert parse_value_response(payload) == 0

    def test_max_positive(self) -> None:
        """Max signed 16-bit positive value = 32767."""
        payload = struct.pack(">h", 32767)
        assert parse_value_response(payload) == 32767

    def test_max_negative(self) -> None:
        """Min signed 16-bit value = -32768."""
        payload = struct.pack(">h", -32768)
        assert parse_value_response(payload) == -32768

    def test_temperature_example(self) -> None:
        """Typical temperature: raw 230 → value 23.0 when factor=10 (applied by caller)."""
        payload = struct.pack(">h", 230)
        raw = parse_value_response(payload)
        assert raw == 230
        # Caller divides by factor: 230/10 = 23.0
        assert raw / 10 == pytest.approx(23.0)


# ===========================================================================
# TestParseValueSpecResponse
# ===========================================================================

class TestParseValueSpecResponse:
    """Verify parse_value_spec_response handles both entry and end-of-list."""

    def _make_spec_payload(
        self,
        more: int,
        factor: int,
        menu_type: int,
        unit: str,
        address: int,
        title: str,
    ) -> bytes:
        """Construct a value-spec payload byte-by-byte."""
        unit_bytes = unit.encode("latin-1")[:2].ljust(2, b"\x00")
        title_bytes = title.encode("latin-1") + b"\x00"
        return (
            bytes([more])
            + struct.pack(">H", factor)
            + struct.pack(">H", menu_type)
            + unit_bytes
            + struct.pack(">H", address)
            + title_bytes
        )

    def test_end_of_list_returns_more_false(self) -> None:
        """A payload starting with 0x00 (more=0) returns {'more': False}."""
        result = parse_value_spec_response(b"\x00")
        assert result == {"more": False}

    def test_more_true_when_data_present(self) -> None:
        """more=1 means there is a valid entry in this response."""
        payload = self._make_spec_payload(1, 10, 0x03, "°C", 0x0001, "Kessel Ist")
        result = parse_value_spec_response(payload)
        assert result["more"] is True

    def test_factor_extracted(self) -> None:
        """Factor is correctly extracted from the 2-byte big-endian field."""
        payload = self._make_spec_payload(1, 10, 0x03, "C ", 0x0001, "Test")
        result = parse_value_spec_response(payload)
        assert result["factor"] == 10

    def test_factor_zero_normalised_to_one(self) -> None:
        """A raw factor of 0 is normalised to 1 (no scaling)."""
        payload = self._make_spec_payload(1, 0, 0x03, "%  ", 0x0001, "Level")
        result = parse_value_spec_response(payload)
        assert result["factor"] == 1

    def test_menu_type_extracted(self) -> None:
        """menu_type is correctly extracted."""
        payload = self._make_spec_payload(1, 1, 0x11, "  ", 0x0005, "Pumpe")
        result = parse_value_spec_response(payload)
        assert result["menu_type"] == 0x11  # DIG_OUT

    def test_address_extracted(self) -> None:
        """Address is correctly extracted from the 2-byte big-endian field."""
        payload = self._make_spec_payload(1, 10, 0x03, "C ", 0xABCD, "Sensor")
        result = parse_value_spec_response(payload)
        assert result["address"] == 0xABCD

    def test_title_extracted(self) -> None:
        """Title string is correctly decoded."""
        payload = self._make_spec_payload(1, 10, 0x03, "C ", 0x0001, "Kessel Ist")
        result = parse_value_spec_response(payload)
        assert result["title"] == "Kessel Ist"

    def test_unit_degree_normalised_to_degree_c(self) -> None:
        """A unit of '°' (lone degree sign) is expanded to '°C'."""
        # Build payload with unit b'\xB0 ' (latin-1 degree sign + space).
        # _normalize_unit strips whitespace and converts '°' → '°C'.
        payload = self._make_spec_payload(1, 10, 0x03, "°\x00", 0x0001, "Temp")
        result = parse_value_spec_response(payload)
        assert result["unit"] == "°C"


# ===========================================================================
# TestParseErrorResponse
# ===========================================================================

class TestParseErrorResponse:
    """Verify parse_error_response handles both entry and end-of-list."""

    def _make_error_payload(
        self,
        more: int,
        number: int,
        info: int,
        state: int,
        ss: int, mm: int, hh: int,
        dd: int, mo: int, yy: int,
        text: str,
    ) -> bytes:
        """Construct an error log response payload."""
        text_bytes = text.encode("latin-1") + b"\x00"
        return (
            bytes([more])
            + struct.pack(">H", number)
            + bytes([info, state])
            + bytes([ss, mm, hh, dd, mo, yy])
            + text_bytes
        )

    def test_end_of_list_returns_more_false(self) -> None:
        """A payload starting with 0x00 returns {'more': False}."""
        result = parse_error_response(b"\x00")
        assert result == {"more": False}

    def test_more_true_when_entry_present(self) -> None:
        """more=1 means there is an error entry in the response."""
        payload = self._make_error_payload(
            1, 5, 0, ErrorState.ARRIVED, 0, 30, 10, 14, 4, 24, "Störung STB"
        )
        result = parse_error_response(payload)
        assert result["more"] is True

    def test_error_number_extracted(self) -> None:
        """Error sequence number is correctly extracted."""
        payload = self._make_error_payload(
            1, 42, 0, ErrorState.ARRIVED, 0, 0, 0, 1, 1, 24, "Test"
        )
        result = parse_error_response(payload)
        assert result["number"] == 42

    def test_info_byte_extracted(self) -> None:
        """Info byte is correctly extracted."""
        payload = self._make_error_payload(
            1, 1, 7, ErrorState.ARRIVED, 0, 0, 0, 1, 1, 24, "Test"
        )
        result = parse_error_response(payload)
        assert result["info"] == 7

    def test_error_state_arrived(self) -> None:
        """ErrorState.ARRIVED is correctly decoded."""
        payload = self._make_error_payload(
            1, 1, 0, ErrorState.ARRIVED, 0, 0, 12, 1, 6, 24, "Fehler"
        )
        result = parse_error_response(payload)
        assert result["state"] == ErrorState.ARRIVED

    def test_error_state_gone(self) -> None:
        """ErrorState.GONE (4) is correctly decoded."""
        payload = self._make_error_payload(
            1, 1, 0, ErrorState.GONE, 0, 0, 0, 1, 1, 24, "Fehler"
        )
        result = parse_error_response(payload)
        assert result["state"] == ErrorState.GONE

    def test_timestamp_year_2000_plus_yy(self) -> None:
        """Year in timestamp is 2000 + YY."""
        payload = self._make_error_payload(
            1, 1, 0, ErrorState.ARRIVED, 30, 15, 10, 14, 4, 25, "Test"
        )
        result = parse_error_response(payload)
        assert result["timestamp"].year == 2025

    def test_timestamp_full_datetime(self) -> None:
        """Full timestamp (date + time) is reconstructed correctly."""
        # SS=30, MM=45, HH=8, DD=14, MO=4, YY=24 → 2024-04-14 08:45:30
        payload = self._make_error_payload(
            1, 1, 0, ErrorState.ARRIVED, 30, 45, 8, 14, 4, 24, "Error"
        )
        result = parse_error_response(payload)
        dt: datetime = result["timestamp"]
        assert dt == datetime(2024, 4, 14, 8, 45, 30)

    def test_error_text_extracted(self) -> None:
        """Error description text is correctly decoded."""
        payload = self._make_error_payload(
            1, 1, 0, ErrorState.ARRIVED, 0, 0, 0, 1, 1, 24, "Störung Hydraulik"
        )
        result = parse_error_response(payload)
        assert result["text"] == "Störung Hydraulik"


# ===========================================================================
# TestParseIoResponse
# ===========================================================================

class TestParseIoResponse:
    """Verify parse_io_response extracts mode and state."""

    def test_mode_extracted(self) -> None:
        """First byte is mode."""
        result = parse_io_response(bytes([2, 1]))
        assert result["mode"] == 2

    def test_state_extracted(self) -> None:
        """Second byte is state."""
        result = parse_io_response(bytes([0, 1]))
        assert result["state"] == 1

    def test_automatic_mode_off_state(self) -> None:
        """mode=0 (automatic), state=0 (off)."""
        result = parse_io_response(bytes([0, 0]))
        assert result["mode"] == 0
        assert result["state"] == 0

    def test_manual_on_mode(self) -> None:
        """mode=2 (manual-on), state=1 (on)."""
        result = parse_io_response(bytes([2, 1]))
        assert result["mode"] == 2
        assert result["state"] == 1

    def test_analogue_output_raw_value(self) -> None:
        """Analogue output: state carries a raw ADC/DAC value."""
        result = parse_io_response(bytes([0, 128]))
        assert result["state"] == 128


# ===========================================================================
# TestParseParameterResponse
# ===========================================================================

class TestParseParameterResponse:
    """Verify parse_parameter_response correctly unpacks the 16-byte structure."""

    def _make_param_payload(
        self,
        addr: int,
        unit_byte: bytes,
        digits: int,
        factor: int,
        value: int,
        min_v: int,
        max_v: int,
        default_v: int,
    ) -> bytes:
        """Build a minimal GET_PARAMETER response payload.

        Layout: [ub1][addr:2][unit:1][digits:1][ub2:1][factor:1][value:2s]
                [min:2s][max:2s][default:2s][uw1:2][ub3:1]
        """
        return (
            b"\x00"                         # ub1 (unknown, ignored)
            + struct.pack(">H", addr)        # address (2 bytes)
            + unit_byte                      # unit (1 byte)
            + bytes([digits])               # digits
            + b"\x00"                        # ub2 (unknown, ignored)
            + bytes([factor])               # factor (1 byte)
            + struct.pack(">h", value)       # current value (signed 16-bit)
            + struct.pack(">h", min_v)       # min value
            + struct.pack(">h", max_v)       # max value
            + struct.pack(">h", default_v)   # default value
            + b"\x00\x00"                   # uw1 (unknown word, ignored)
            + b"\x00"                        # ub3 (unknown, ignored)
        )

    def test_address_extracted(self) -> None:
        """Parameter address is correctly extracted."""
        payload = self._make_param_payload(0x0100, b"C", 1, 10, 750, 400, 900, 700)
        result = parse_parameter_response(payload)
        assert result["address"] == 0x0100

    def test_value_divided_by_factor(self) -> None:
        """Raw value is divided by factor to yield the physical float."""
        # raw_value=750, factor=10 → value=75.0
        payload = self._make_param_payload(0x0100, b"C", 1, 10, 750, 400, 900, 700)
        result = parse_parameter_response(payload)
        assert result["value"] == pytest.approx(75.0)

    def test_min_max_default_divided_by_factor(self) -> None:
        """min/max/default values are also divided by factor."""
        payload = self._make_param_payload(0x0100, b"C", 1, 10, 750, 400, 900, 700)
        result = parse_parameter_response(payload)
        assert result["min_value"]     == pytest.approx(40.0)
        assert result["max_value"]     == pytest.approx(90.0)
        assert result["default_value"] == pytest.approx(70.0)

    def test_digits_extracted(self) -> None:
        """digits field is extracted correctly."""
        payload = self._make_param_payload(0x0100, b"C", 2, 10, 750, 400, 900, 700)
        result = parse_parameter_response(payload)
        assert result["digits"] == 2

    def test_factor_extracted(self) -> None:
        """factor field is extracted correctly."""
        payload = self._make_param_payload(0x0100, b"C", 1, 100, 750, 400, 900, 700)
        result = parse_parameter_response(payload)
        assert result["factor"] == 100

    def test_factor_zero_normalised_to_one(self) -> None:
        """A factor of 0 in the payload is treated as 1."""
        payload = self._make_param_payload(0x0100, b"C", 0, 0, 75, 40, 90, 70)
        result = parse_parameter_response(payload)
        assert result["factor"] == 1
        assert result["value"] == pytest.approx(75.0)

    def test_unit_degree_normalised(self) -> None:
        """The single degree byte (0xB0 in latin-1) is normalised to '°C'."""
        # 0xB0 is "°" in latin-1.
        payload = self._make_param_payload(0x0100, b"\xB0", 1, 10, 750, 400, 900, 700)
        result = parse_parameter_response(payload)
        assert result["unit"] == "°C"

    def test_unit_percent(self) -> None:
        """A percent unit '%' passes through unchanged."""
        payload = self._make_param_payload(0x0100, b"%", 0, 1, 50, 0, 100, 50)
        result = parse_parameter_response(payload)
        assert result["unit"] == "%"

    def test_negative_value(self) -> None:
        """Negative raw values are handled correctly."""
        payload = self._make_param_payload(0x0200, b"C", 1, 10, -50, -200, 200, 0)
        result = parse_parameter_response(payload)
        assert result["value"] == pytest.approx(-5.0)
        assert result["min_value"] == pytest.approx(-20.0)
