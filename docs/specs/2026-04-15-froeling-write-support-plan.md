# Write Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add menu tree discovery and write support (select/number entities for writable parameters) to the Fröling HA integration.

**Architecture:** Extend the existing pyfroeling library with menu tree parsing and parameter writing commands. Add new HA entity platforms (select, number). Config flow gets an access mode step and parameter selection step.

**Tech Stack:** Python 3.12+, asyncio, Home Assistant Core

**Spec:** `docs/specs/2026-04-15-froeling-write-support-design.md`

**IMPORTANT:** Single connection constraint -- never disconnect/reconnect mid-session.

---

## File Map

### Protocol Library (modify existing + new)

| File | Change |
|------|--------|
| `pyfroeling/models.py` | Add `MenuItem` and `WritableParameter` dataclasses |
| `pyfroeling/commands.py` | Add menu list and set parameter request builders + response parsers |
| `pyfroeling/client.py` | Add `discover_menu()` and `set_parameter()` methods |

### HA Integration (modify existing + new)

| File | Change |
|------|--------|
| `const.py` | Add `CONF_WRITE_ENABLED`, `CONF_SELECTED_PARAMETERS` |
| `config_flow.py` | Add access mode step + parameter selection step |
| `coordinator.py` | Add parameter reading to polling cycle, add `async_write_parameter()` |
| `__init__.py` | Register `number` and `select` platforms |
| `number.py` | **NEW**: Numeric parameter entities |
| `select.py` | **NEW**: Choice/mode parameter entities |
| `strings.json` + translations | Add strings for new steps |
| `manifest.json` | Bump version |

### Tests

| File | Change |
|------|--------|
| `tests/test_commands.py` | Add tests for menu list and set parameter parsing |

---

## Task 1: Data Models

**Files:**
- Modify: `custom_components/froeling/pyfroeling/models.py`

- [ ] **Step 1: Add MenuItem and WritableParameter dataclasses**

Append to `models.py`:

```python
@dataclass
class MenuItem:
    """An entry from the heater's menu tree.

    Retrieved via cmdGetMenuListFirst/Next (0x37/0x38). The menu tree
    contains all parameters, sensors, and settings organized in a
    hierarchy. Each entry has a type that determines how to interact
    with it (read-only sensor, writable parameter, etc.).

    Format from p4io.c:1157 (getMenuItem):
        [more:1] [type:1] [unknown1:1] [parent:2 BE] [child:2 BE]
        [18 spare bytes] [address:2 BE] [unknown2:2 BE]
        [description:N bytes] [terminator:1] [crc:1]
    """

    menu_type: int      # MenuStructType code (1 byte, e.g., 0x07=mstPar)
    parent: int         # Parent menu node ID
    child: int          # This entry's node ID
    address: int        # Parameter address for read/write
    title: str          # Human-readable name


@dataclass
class WritableParameter:
    """A writable parameter discovered from the menu tree with its current state.

    Combines data from the menu tree (MenuItem) and cmdGetParameter (0x55)
    to provide a complete picture of a writable setting.
    """

    address: int            # 16-bit parameter address
    title: str              # Human-readable name from menu tree
    menu_type: int          # mstPar (0x07) or mstParDig (0x08)
    value: float            # Current value (raw / factor)
    unit: str               # Unit string ("°C", etc.)
    digits: int             # Number of decimal places
    factor: int             # Division factor
    min_value: float        # Minimum allowed value
    max_value: float        # Maximum allowed value
    default_value: float    # Factory default value
```

- [ ] **Step 2: Update pyfroeling __init__.py exports**

Add `MenuItem` and `WritableParameter` to the `__all__` list and imports in `pyfroeling/__init__.py`.

- [ ] **Step 3: Run tests, commit**

```bash
python -m pytest tests/ -v
git add -A && git commit -m "feat(pyfroeling): add MenuItem and WritableParameter data models"
```

---

## Task 2: Command Builders & Parsers

**Files:**
- Modify: `custom_components/froeling/pyfroeling/commands.py`
- Modify: `tests/test_commands.py`

- [ ] **Step 1: Add menu list request builders**

Add to `commands.py`:

```python
def build_get_menu_list_request(first: bool = True) -> tuple[Command, bytes]:
    """Build a menu tree enumeration request.

    Args:
        first: True for cmdGetMenuListFirst (0x37),
               False for cmdGetMenuListNext (0x38).
    """
    cmd = Command.GET_MENU_LIST_FIRST if first else Command.GET_MENU_LIST_NEXT
    return cmd, b""
```

- [ ] **Step 2: Add menu entry response parser**

Add to `commands.py`:

```python
def parse_menu_entry_response(payload: bytes) -> dict:
    """Parse cmdGetMenuListFirst/Next (0x37/0x38) response.

    Format per p4io.c:1157-1300 (getMenuItem):
        [more: 1 byte]     -- 0 = end of list
        [type: 1 byte]     -- MenuStructType code
        [unknown1: 1 byte] -- skip
        [parent: 2 BE]     -- parent menu node ID
        [child: 2 BE]      -- this entry's node ID
        [18 spare bytes]   -- unknown, skip
        [address: 2 BE]    -- parameter address
        [unknown2: 2 BE]   -- skip
        [title: N bytes]   -- description text, null-terminated
        [terminator: 1 byte]

    Returns dict with: more, menu_type, parent, child, address, title.
    If more=False, only 'more' key is present.
    """
    more = payload[0]
    if more == 0:
        return {"more": False}

    # Minimum size check (p4io.c:1189: size < 30)
    if len(payload) < 30:
        return {"more": True, "empty": True}

    offset = 1
    menu_type = payload[offset]; offset += 1      # type: 1 byte
    offset += 1                                    # unknown1: skip
    parent = struct.unpack(">H", payload[offset:offset+2])[0]; offset += 2
    child = struct.unpack(">H", payload[offset:offset+2])[0]; offset += 2
    offset += 18                                   # 18 spare bytes: skip
    address = struct.unpack(">H", payload[offset:offset+2])[0]; offset += 2
    offset += 2                                    # unknown2: skip

    # Title: remaining bytes minus terminator
    title_raw = payload[offset:]
    null_pos = title_raw.find(b"\x00")
    if null_pos != -1:
        title_raw = title_raw[:null_pos]
    title = title_raw.decode("latin-1", errors="replace").strip()

    return {
        "more": True,
        "menu_type": menu_type,
        "parent": parent,
        "child": child,
        "address": address,
        "title": title,
    }
```

- [ ] **Step 3: Add set parameter request builder**

Add to `commands.py`:

```python
def build_set_parameter_request(address: int, value: int) -> tuple[Command, bytes]:
    """Build a parameter write request (cmdSetParameter, 0x39).

    Args:
        address: 16-bit parameter address.
        value: Raw integer value (already multiplied by factor).
    """
    return Command.SET_PARAMETER, struct.pack(">HH", address, value)
```

- [ ] **Step 4: Add set parameter response parser**

Add to `commands.py`:

```python
def parse_set_parameter_response(payload: bytes) -> dict:
    """Parse cmdSetParameter (0x39) response.

    The heater echoes back [address: 2 BE][value: 2 BE].

    Returns dict with: address, value.
    """
    address = struct.unpack(">H", payload[0:2])[0]
    value = struct.unpack(">h", payload[2:4])[0]
    return {"address": address, "value": value}
```

- [ ] **Step 5: Add tests for new parsers**

Add to `tests/test_commands.py`:

```python
class TestMenuListParsing:

    def test_menu_entry_end_of_list(self):
        payload = bytes([0x00])
        result = parse_menu_entry_response(payload)
        assert result["more"] is False

    def test_menu_entry_short_payload(self):
        payload = bytes([0x01]) + bytes(10)  # more=1 but too short
        result = parse_menu_entry_response(payload)
        assert result["more"] is True
        assert result.get("empty") is True

    def test_menu_entry_valid(self):
        # more=1, type=0x07 (mstPar), unknown1=0, parent=0x0001, child=0x0002
        # 18 spare bytes, address=0x01E0, unknown2=0x0000, title="Betriebsart"
        payload = bytearray()
        payload.append(0x01)                          # more
        payload.append(0x07)                          # type = mstPar
        payload.append(0x00)                          # unknown1
        payload.extend(struct.pack(">H", 0x0001))     # parent
        payload.extend(struct.pack(">H", 0x0002))     # child
        payload.extend(bytes(18))                      # 18 spare bytes
        payload.extend(struct.pack(">H", 0x01E0))     # address
        payload.extend(struct.pack(">H", 0x0000))     # unknown2
        payload.extend(b"Betriebsart\x00")            # title + null
        result = parse_menu_entry_response(bytes(payload))
        assert result["more"] is True
        assert result["menu_type"] == 0x07
        assert result["parent"] == 0x0001
        assert result["child"] == 0x0002
        assert result["address"] == 0x01E0
        assert result["title"] == "Betriebsart"

    def test_set_parameter_request(self):
        cmd, payload = build_set_parameter_request(0x01E0, 2)
        assert cmd == Command.SET_PARAMETER
        assert payload == struct.pack(">HH", 0x01E0, 2)

    def test_set_parameter_response(self):
        payload = struct.pack(">Hh", 0x01E0, 2)
        result = parse_set_parameter_response(payload)
        assert result["address"] == 0x01E0
        assert result["value"] == 2
```

- [ ] **Step 6: Run tests, commit**

```bash
python -m pytest tests/ -v
git add -A && git commit -m "feat(pyfroeling): add menu tree and set parameter command parsers"
```

---

## Task 3: Client Methods

**Files:**
- Modify: `custom_components/froeling/pyfroeling/client.py`

- [ ] **Step 1: Add discover_menu() method**

Add to `FroelingClient`:

```python
async def discover_menu(self) -> list[MenuItem]:
    """Read the full menu tree from the heater.

    Enumerates all entries via cmdGetMenuListFirst/Next (0x37/0x38).
    Returns MenuItem objects for each entry. Used to find writable
    parameters and their addresses.
    """
    from .commands import build_get_menu_list_request, parse_menu_entry_response
    from .models import MenuItem

    items: list[MenuItem] = []
    seen_addresses: set[int] = set()
    first = True

    for _ in range(_MAX_PAGES):
        cmd, payload = build_get_menu_list_request(first=first)
        raw = await self._send_and_receive(cmd, payload)
        data = parse_menu_entry_response(raw)

        if not data.get("more", False):
            break

        if data.get("empty", False):
            first = False
            continue

        address = data["address"]
        # Skip duplicate addresses (same as sensor discovery)
        if address in seen_addresses:
            first = False
            continue
        seen_addresses.add(address)

        items.append(MenuItem(
            menu_type=data["menu_type"],
            parent=data["parent"],
            child=data["child"],
            address=address,
            title=data["title"],
        ))
        first = False

        _log.debug(
            "discover_menu: type=0x%02X addr=0x%04X '%s'",
            data["menu_type"], address, data["title"],
        )

    _log.info("Discovered %d menu items from heater", len(items))
    return items
```

- [ ] **Step 2: Add set_parameter() method**

Add to `FroelingClient`:

```python
async def set_parameter(self, address: int, value: float, factor: int) -> float:
    """Write a parameter value to the heater.

    Sends cmdSetParameter (0x39) with the raw value (value * factor).
    The heater responds with two echo frames. After writing, re-reads
    the parameter with cmdGetParameter (0x55) to confirm the new value.

    Args:
        address: 16-bit parameter address.
        value: The desired real value (will be multiplied by factor).
        factor: Division factor from the parameter spec.

    Returns:
        The confirmed value read back from the heater after writing.

    Raises:
        FroelingProtocolError: If the heater rejects the write.
    """
    from .commands import (
        build_set_parameter_request,
        parse_set_parameter_response,
    )

    raw_value = int(value * factor)
    cmd, payload = build_set_parameter_request(address, raw_value)

    # Send the write command
    response1 = await self._send_and_receive(cmd, payload)
    result1 = parse_set_parameter_response(response1)

    # The heater sends a SECOND response frame as confirmation
    try:
        _, _, resp2_raw = await self._conn.read_response()
        # Strip CRC byte
        if len(resp2_raw) > 0:
            resp2_raw = resp2_raw[:-1]
    except Exception as exc:
        _log.warning("Did not receive second confirmation frame: %s", exc)

    # Re-read the parameter to confirm the new value
    param = await self.get_parameter(address)
    _log.info(
        "Set parameter 0x%04X: requested=%.1f, confirmed=%.1f",
        address, value, param.value,
    )

    return param.value
```

- [ ] **Step 3: Add get_writable_parameters() convenience method**

Add to `FroelingClient`:

```python
async def get_writable_parameters(
    self, menu_items: list[MenuItem]
) -> list[WritableParameter]:
    """Read current values for all writable menu items.

    Filters menu_items to only writable types (mstPar, mstParDig),
    then reads each with cmdGetParameter (0x55).

    Args:
        menu_items: Menu tree from discover_menu().

    Returns:
        List of WritableParameter with current values and ranges.
    """
    from .models import WritableParameter

    # Writable types: mstPar (numeric), mstParDig (boolean/choice)
    writable_types = {0x07, 0x08, 0x0A}  # mstPar, mstParDig, mstParZeit

    writable: list[WritableParameter] = []
    for item in menu_items:
        if item.menu_type not in writable_types:
            continue
        try:
            param = await self.get_parameter(item.address)
            writable.append(WritableParameter(
                address=item.address,
                title=item.title,
                menu_type=item.menu_type,
                value=param.value,
                unit=param.unit,
                digits=param.digits,
                factor=param.factor,
                min_value=param.min_value,
                max_value=param.max_value,
                default_value=param.default_value,
            ))
        except Exception as exc:
            _log.debug(
                "Skipping non-readable parameter 0x%04X '%s': %s",
                item.address, item.title, exc,
            )

    _log.info("Found %d writable parameters", len(writable))
    return writable
```

- [ ] **Step 4: Run tests, commit**

```bash
python -m pytest tests/ -v
git add -A && git commit -m "feat(pyfroeling): add menu discovery, parameter write, and writable parameter reading"
```

---

## Task 4: HA Constants & Strings

**Files:**
- Modify: `custom_components/froeling/const.py`
- Modify: `custom_components/froeling/strings.json`
- Modify: `custom_components/froeling/translations/en.json`
- Modify: `custom_components/froeling/translations/de.json`

- [ ] **Step 1: Add new constants**

Add to `const.py`:

```python
# Write mode
CONF_WRITE_ENABLED = "write_enabled"
CONF_SELECTED_PARAMETERS = "selected_parameters"
```

- [ ] **Step 2: Update strings.json**

Add the new steps after the `sensors` step:

```json
"access_mode": {
    "title": "Access Mode",
    "description": "Choose how the integration interacts with your heater.",
    "menu_options": {
        "read_only": "Read-only (recommended)",
        "read_write": "Read/Write — allows changing heater parameters"
    }
},
"read_write_warning": {
    "title": "⚠ Write Mode Warning",
    "description": "Write mode allows changing heater parameters from Home Assistant. Incorrect values could affect heater operation. Only enable if you understand the risks. Continue?",
    "menu_options": {
        "confirm_write": "Yes, enable write mode",
        "back_to_read_only": "No, use read-only mode"
    }
},
"parameters": {
    "title": "Select Parameters",
    "description": "The following writable parameters were found on your heater. Select which ones you want to control from Home Assistant. For safety, none are selected by default.",
    "data": {
        "selected_parameters": "Writable Parameters"
    }
}
```

Also add to `options.step.init.data`:
```json
"selected_parameters": "Writable Parameters"
```

- [ ] **Step 3: Update de.json with German translations**

Add matching German strings for access_mode, read_write_warning, and parameters steps.

- [ ] **Step 4: Copy strings.json to en.json, commit**

```bash
cp strings.json translations/en.json
git add -A && git commit -m "feat(ha): add write mode constants and UI strings"
```

---

## Task 5: Config Flow Changes

**Files:**
- Modify: `custom_components/froeling/config_flow.py`

- [ ] **Step 1: Add access mode step**

After the network/serial step succeeds and sensors are discovered, add a new branching step:

```python
async def async_step_access_mode(self, user_input=None):
    """Ask user: read-only or read/write mode."""
    return self.async_show_menu(
        step_id="access_mode",
        menu_options=["read_only", "read_write"],
    )

async def async_step_read_only(self, user_input=None):
    """Read-only selected: go straight to sensor selection."""
    self._write_enabled = False
    return await self.async_step_sensors()

async def async_step_read_write(self, user_input=None):
    """Read/write selected: show warning first."""
    return self.async_show_menu(
        step_id="read_write_warning",
        menu_options=["confirm_write", "back_to_read_only"],
    )

async def async_step_confirm_write(self, user_input=None):
    """User confirmed write mode: discover menu tree, then sensors."""
    self._write_enabled = True
    # Discover menu tree and writable parameters on the SAME connection
    # (connection is still open from the network/serial step)
    client = FroelingClient(...)  # recreate or keep reference
    try:
        await client.connect()
        # Discover menu tree
        menu_items = await client.discover_menu()
        self._writable_params = await client.get_writable_parameters(menu_items)
        # Also discover sensors (reuse existing logic)
        self._discovered = await _validate_and_discover(client)
    finally:
        await client.disconnect()
    return await self.async_step_sensors()

async def async_step_back_to_read_only(self, user_input=None):
    """User declined write mode: fall back to read-only."""
    self._write_enabled = False
    return await self.async_step_sensors()
```

- [ ] **Step 2: Add parameter selection step**

After sensor selection, if write mode enabled:

```python
async def async_step_parameters(self, user_input=None):
    """Let user select which writable parameters to enable."""
    if user_input is not None:
        selected = user_input.get(CONF_SELECTED_PARAMETERS, [])
        # Include in config entry data
        # ... create entry with both selected sensors and parameters
        return self.async_create_entry(...)

    # Build options from discovered writable parameters
    options = []
    for param in self._writable_params:
        # Format: "Betriebsart = 1 (min: 0, max: 3)"
        label = f"{param.title} = {param.value} {param.unit}".rstrip()
        label += f"  (min: {param.min_value}, max: {param.max_value})"
        options.append(SelectOptionDict(
            value=f"0x{param.address:04X}", label=label
        ))

    schema = vol.Schema({
        vol.Required(CONF_SELECTED_PARAMETERS, default=[]): SelectSelector(
            SelectSelectorConfig(options=options, multiple=True, mode=SelectSelectorMode.LIST)
        ),
    })
    return self.async_show_form(step_id="parameters", data_schema=schema)
```

- [ ] **Step 3: Modify sensor step to chain to parameters**

Change `async_step_sensors` so that after sensor selection, if write mode is enabled, it proceeds to `async_step_parameters` instead of creating the entry directly.

- [ ] **Step 4: Store write_enabled and selected_parameters in config entry data**

Update the entry creation to include:
```python
data[CONF_WRITE_ENABLED] = self._write_enabled
data[CONF_SELECTED_PARAMETERS] = selected_parameters
```

- [ ] **Step 5: Run tests, commit**

```bash
python -m pytest tests/ -v
git add -A && git commit -m "feat(ha): add access mode and parameter selection to config flow"
```

---

## Task 6: Coordinator Changes

**Files:**
- Modify: `custom_components/froeling/coordinator.py`

- [ ] **Step 1: Extend FroelingData with parameters**

```python
@dataclass
class FroelingData:
    status: HeaterStatus
    values: dict[int, SensorValue]
    errors: list[ErrorEntry]
    specs: list[ValueSpec]
    parameters: dict[int, WritableParameter] = field(default_factory=dict)
```

- [ ] **Step 2: Add parameter reading to _async_update_data**

If write mode is enabled, read selected parameter values each polling cycle:

```python
# After reading sensor values and errors:
if self._parameter_addresses:
    parameters = {}
    for addr in self._parameter_addresses:
        try:
            param = await self.client.get_parameter(addr)
            # Look up title from stored menu data
            parameters[addr] = WritableParameter(
                address=addr, title=..., value=param.value, ...
            )
        except Exception:
            pass
    # Store in FroelingData
```

- [ ] **Step 3: Add async_write_parameter method**

```python
async def async_write_parameter(self, address: int, value: float, factor: int) -> float:
    """Write a parameter value and trigger a data refresh."""
    confirmed = await self.client.set_parameter(address, value, factor)
    await self.async_request_refresh()
    return confirmed
```

- [ ] **Step 4: Initialize parameter addresses from config entry**

In `__init__`, read `CONF_SELECTED_PARAMETERS` and `CONF_WRITE_ENABLED` from the config entry to determine which parameters to poll.

- [ ] **Step 5: Run tests, commit**

```bash
python -m pytest tests/ -v
git add -A && git commit -m "feat(ha): extend coordinator with parameter reading and writing"
```

---

## Task 7: Number Entity Platform

**Files:**
- Create: `custom_components/froeling/number.py`

- [ ] **Step 1: Implement number platform**

```python
"""Number platform for writable numeric parameters (mstPar, 0x07).

Creates NumberEntity instances for parameters like temperature setpoints.
Values are bounded by min/max from the heater's parameter definition.
"""

from homeassistant.components.number import NumberEntity, NumberMode
from .entity import FroelingEntity
from .coordinator import FroelingCoordinator

class FroelingNumberEntity(FroelingEntity, NumberEntity):
    """Numeric parameter that can be read and written."""

    _attr_mode = NumberMode.BOX  # Input field, not slider (safer)

    def __init__(self, coordinator, address, title, unit, digits, factor, min_val, max_val):
        super().__init__(coordinator, "PAR", address, title)
        self._attr_native_unit_of_measurement = unit
        self._attr_native_min_value = min_val
        self._attr_native_max_value = max_val
        self._attr_native_step = 1.0 / factor if factor > 1 else 1.0
        self._factor = factor

    @property
    def native_value(self):
        if self.coordinator.data is None:
            return None
        param = self.coordinator.data.parameters.get(self._address)
        return param.value if param else None

    async def async_set_native_value(self, value: float) -> None:
        """Write the new value to the heater."""
        await self.coordinator.async_write_parameter(
            self._address, value, self._factor
        )
```

- [ ] **Step 2: Add async_setup_entry**

```python
async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    if not entry.data.get(CONF_WRITE_ENABLED, False):
        return  # Write mode not enabled

    selected = entry.data.get(CONF_SELECTED_PARAMETERS, [])
    # ... create entities for selected numeric parameters
```

- [ ] **Step 3: Commit**

```bash
git add -A && git commit -m "feat(ha): add number entity platform for writable numeric parameters"
```

---

## Task 8: Select Entity Platform

**Files:**
- Create: `custom_components/froeling/select.py`

- [ ] **Step 1: Implement select platform**

```python
"""Select platform for writable choice parameters (mstParDig, 0x08).

Creates SelectEntity instances for parameters like operating mode.
Options are derived from the parameter's min/max range.
"""

from homeassistant.components.select import SelectEntity
from .entity import FroelingEntity
from .coordinator import FroelingCoordinator

class FroelingSelectEntity(FroelingEntity, SelectEntity):
    """Choice parameter that can be read and written."""

    def __init__(self, coordinator, address, title, options_map, factor):
        super().__init__(coordinator, "PAR", address, title)
        self._options_map = options_map  # {display_name: raw_value}
        self._reverse_map = {v: k for k, v in options_map.items()}
        self._attr_options = list(options_map.keys())
        self._factor = factor

    @property
    def current_option(self):
        if self.coordinator.data is None:
            return None
        param = self.coordinator.data.parameters.get(self._address)
        if param is None:
            return None
        raw = int(param.value * self._factor)
        return self._reverse_map.get(raw)

    async def async_select_option(self, option: str) -> None:
        """Write the selected option to the heater."""
        raw_value = self._options_map[option]
        value = raw_value / self._factor
        await self.coordinator.async_write_parameter(
            self._address, value, self._factor
        )
```

For mstParDig parameters, the options are generated from min_value to max_value as integer steps. The display names are the raw integer values (since we don't know the heater's label for each value without reading the menu display text).

- [ ] **Step 2: Add async_setup_entry**

```python
async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    if not entry.data.get(CONF_WRITE_ENABLED, False):
        return

    selected = entry.data.get(CONF_SELECTED_PARAMETERS, [])
    # ... create entities for selected choice parameters
```

- [ ] **Step 3: Commit**

```bash
git add -A && git commit -m "feat(ha): add select entity platform for writable choice parameters"
```

---

## Task 9: Integration Setup & Version Bump

**Files:**
- Modify: `custom_components/froeling/__init__.py`
- Modify: `custom_components/froeling/manifest.json`

- [ ] **Step 1: Register new platforms**

Add `Platform.NUMBER` and `Platform.SELECT` to the platforms list in `__init__.py`:

```python
platforms = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.NUMBER, Platform.SELECT]
```

- [ ] **Step 2: Bump version**

Update `manifest.json` to `"version": "0.7.0"`.

- [ ] **Step 3: Run all tests, commit**

```bash
python -m pytest tests/ -v
git add -A && git commit -m "feat: register number and select platforms, bump to v0.7.0"
```

---

## Task 10: Release

- [ ] **Step 1: Tag and push**

```bash
git tag v0.7.0 && git push && git push --tags
```

- [ ] **Step 2: Create GitHub release**

```bash
gh release create v0.7.0 --title "v0.7.0 - Write Support" --notes "..."
```
