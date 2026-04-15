# Fröling HA Integration -- Write Support & Menu Tree Reader

**Date:** 2026-04-15
**Status:** Draft
**Author:** Fabian Plaimauer + Claude

---

## 1. Overview

### Goal

Add write support to the Fröling HA integration by reading the heater's menu tree to discover writable parameters, and exposing them as HA `select` and `number` entities. Users choose read-only or read/write mode during setup.

### Scope

- Read the heater's full menu tree via `cmdGetMenuListFirst/Next` (0x37/0x38)
- Discover writable parameters from the menu tree (type mstPar, mstParDig, etc.)
- Expose writable parameters as HA `select` or `number` entities
- Write parameters via `cmdSetParameter` (0x39) when the user changes a value in HA
- User selects which writable parameters to enable during setup (default: none preselected for safety)
- Setup flow asks read-only vs. read/write mode with a safety warning

### Out of Scope

- Controlling digital/analog outputs (`cmdSetDigOut`, `cmdSetAnlOut`)
- Time range editing (`cmdSetTimes`)
- Date/time sync (`cmdSetDateTime`)
- Force mode (`cmdSetForce`)

---

## 2. Protocol

### Menu Tree: cmdGetMenuListFirst (0x37) / cmdGetMenuListNext (0x38)

Request: no payload.

Response format per entry:

| Field | Size | Type | Description |
|-------|------|------|-------------|
| more | 1 byte | uint8 | 0 = end of list, else continue |
| parent | 2 bytes BE | uint16 | Parent menu node ID |
| child | 2 bytes BE | uint16 | This entry's node ID |
| type | 2 bytes BE | uint16 | MenuStructType code |
| address | 2 bytes BE | uint16 | Parameter address (for read/write) |
| title | N bytes | latin-1 | Human-readable name, null-terminated |

When `more == 0`, only the more byte + CRC are present (end of list).

Relevant MenuStructType codes for writable parameters:
- `mstPar` (0x07): Numeric parameter with min/max/step
- `mstParDig` (0x08): Boolean/choice parameter (ja/nein, or multi-option)
- `mstParZeit` (0x0A): Time-of-day parameter (HH:MM)

### Read Parameter: cmdGetParameter (0x55)

Already implemented. Returns current value, unit, digits, factor, min, max, default.

### Write Parameter: cmdSetParameter (0x39)

Request payload: `[address: 2 bytes BE][value: 2 bytes BE]`

Response: The heater echoes `[address][value]` twice (two separate response frames) to confirm the write. After the confirmation, the client re-reads the parameter with `cmdGetParameter` to verify the new value took effect.

Safety: Before writing, the client reads the current parameter to get min/max bounds and verifies the new value is within range.

---

## 3. Config Flow Changes

### Step 1: Connection Type (unchanged)

Menu: Network or USB Serial.

### Step 1a/1b: Connection Details (unchanged)

Host+port or serial device + device name.

### NEW Step: Access Mode

After successful connection test, before sensor discovery:

| Option | Description |
|--------|-------------|
| **Read-only** | Only sensor values are read. No parameters can be changed. (Default) |
| **Read/Write** | Sensor values AND writable parameters are available. Changing values sends commands to the heater. |

Warning text for read/write: "Write mode allows changing heater parameters from Home Assistant. Incorrect values could affect heater operation. Only enable if you understand the risks."

Stored in config entry as `CONF_WRITE_ENABLED: bool`.

### Step: Sensor Selection (unchanged)

Same as current.

### NEW Step: Parameter Selection (only if write mode)

After sensor selection, if write mode is enabled:
- Discover menu tree via `cmdGetMenuListFirst/Next`
- Read current values for discovered writable parameters via `cmdGetParameter`
- Show multi-select list with parameter name, current value, unit, and min/max range
- Default: **none preselected** (user must explicitly opt in to each writable parameter)

Stored in config entry as `CONF_SELECTED_PARAMETERS: list[str]` (hex addresses like sensors).

---

## 4. New Entity Platforms

### `number.py` -- Numeric Parameters (mstPar, 0x07)

For writable numeric parameters like temperature setpoints:

- Entity type: `NumberEntity`
- `native_min_value` / `native_max_value` from cmdGetParameter response
- `native_step` derived from factor (e.g., factor=10 -> step=0.1)
- `native_unit_of_measurement` from parameter unit
- `device_class` mapped from unit (°C -> temperature, etc.)
- `mode = NumberMode.BOX` (input field, not slider, for safety)

On value change:
1. Validate value is within min/max
2. Send `cmdSetParameter(address, value * factor)` 
3. Re-read with `cmdGetParameter` to confirm
4. Update entity state

### `select.py` -- Choice Parameters (mstParDig, 0x08)

For parameters with discrete options like operating mode:

- Entity type: `SelectEntity`
- Options discovered from the parameter's value range
- Current value read via `cmdGetParameter`

On option change:
1. Map selected option text to numeric value
2. Send `cmdSetParameter(address, value)`
3. Re-read to confirm
4. Update entity state

---

## 5. Data Model Changes

### New Model: MenuItem

```python
@dataclass
class MenuItem:
    """An entry from the heater's menu tree."""
    parent: int          # Parent menu node ID
    child: int           # This entry's node ID
    menu_type: int       # MenuStructType code
    address: int         # Parameter address
    title: str           # Human-readable name
```

### New Model: WritableParameter

```python
@dataclass
class WritableParameter:
    """A discovered writable parameter with its current state and bounds."""
    address: int
    title: str
    menu_type: int       # mstPar or mstParDig
    value: float         # Current value
    unit: str
    digits: int          # Decimal places
    factor: int          # Division factor
    min_value: float
    max_value: float
    default_value: float
```

---

## 6. Coordinator Changes

### FroelingData Extended

```python
@dataclass
class FroelingData:
    status: HeaterStatus
    values: dict[int, SensorValue]
    errors: list[ErrorEntry]
    specs: list[ValueSpec]
    parameters: dict[int, WritableParameter]  # NEW: address -> parameter
```

### Polling Cycle (extended)

If write mode enabled:
1. Read status (unchanged)
2. Read selected sensor values (unchanged)
3. Read errors (unchanged)
4. **NEW**: Read selected parameter values via `cmdGetParameter` for each enabled parameter

### Write Method

New method on the coordinator (or directly on client):

```python
async def write_parameter(self, address: int, value: float) -> None:
    """Write a parameter value to the heater."""
```

---

## 7. File Changes

| File | Change |
|------|--------|
| `pyfroeling/models.py` | Add `MenuItem`, `WritableParameter` dataclasses |
| `pyfroeling/commands.py` | Add `build_get_menu_list_request`, `parse_menu_entry_response`, `build_set_parameter_request`, `parse_set_parameter_response` |
| `pyfroeling/client.py` | Add `discover_menu()`, `set_parameter()` methods |
| `pyfroeling/const.py` | No changes needed (MenuStructType already defined) |
| `const.py` | Add `CONF_WRITE_ENABLED`, `CONF_SELECTED_PARAMETERS` |
| `config_flow.py` | Add access mode step + parameter selection step |
| `coordinator.py` | Add parameter reading to polling cycle |
| `number.py` | **NEW**: Numeric parameter entities |
| `select.py` | **NEW**: Choice parameter entities |
| `__init__.py` | Register number + select platforms |
| `strings.json` + translations | Add strings for new steps + entities |
| `manifest.json` | Bump version |

---

## 8. Safety

- Write mode is **opt-in** during setup (default: read-only)
- Warning text shown when enabling write mode
- Individual parameters must be explicitly selected (default: none)
- Value range validated against min/max from `cmdGetParameter` before writing
- After writing, the value is re-read to confirm it was accepted
- If the heater rejects the value, the entity reverts to the previous state and logs an error

---

## 9. Single Connection Constraint

All operations (sensor discovery + menu tree discovery + value reading + parameter reading) happen on a single continuous TCP connection per the lesson learned from v0.6.0-v0.6.5. No disconnect/reconnect mid-session.

Setup flow: `connect -> check -> discover sensors -> read values -> discover menu -> read parameters -> disconnect`

Polling: `connect (if needed) -> read status -> read values -> read parameters -> read errors`
