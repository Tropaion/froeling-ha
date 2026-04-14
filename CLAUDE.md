# Fröling HA Integration

## Project Overview
Home Assistant custom integration for Fröling pellet heaters (P1, PE1, and other models using Lambdatronic P/S 3200 controllers). Communicates via the proprietary binary protocol on COM1 through a TCP-to-serial converter (e.g., Elfin EE10).

## Architecture
- `custom_components/froeling/pyfroeling/` -- Pure Python async protocol library
- `custom_components/froeling/` -- HA integration using DataUpdateCoordinator
- Protocol library is bundled inside the custom component (not a separate PyPI package)

## Key Technical Details
- Protocol: proprietary binary on COM1 at 57600 baud, 8N1
- Frame format: [0x02FD sync][size BE][cmd][payload][CRC]
- 5 bytes require escaping after the sync ID (0x02, 0x2B, 0xFE, 0x11, 0x13)
- CRC: XOR-based -- `crc = crc ^ (byte ^ (byte * 2 & 0xFF))`
- All multi-byte values are big-endian
- Protocol is strictly synchronous (one request/response at a time)
- Reference implementation: linux-p4d project (C, by Jörg Wendel)

## Development
```bash
# Run tests
python -m pytest tests/ -v

# Type checking
mypy custom_components/froeling/
```

## Conventions
- All code must be thoroughly commented with docstrings and inline explanations
- German sensor names from the heater are preserved as-is
- Sensor entities are created dynamically from discovered ValueSpecs
- Read-only in v0.1 -- no parameter writing or output control
