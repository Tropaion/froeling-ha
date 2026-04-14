# Fröling Heater Integration for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)

A Home Assistant custom integration for **Fröling pellet heaters** that communicates via the proprietary binary protocol on COM1 (the service interface). Connects through a TCP-to-serial converter such as the Elfin EE10.

## Supported Hardware

### Heaters

Any Fröling heater with a **Lambdatronic P 3200** or **S 3200** controller, including:

- Fröling **P1** Pellet (7-20 kW)
- Fröling **PE1** Pellet (15-35 kW)
- Fröling **P4** Pellet
- Other models using the Lambdatronic 3200 controller family

### Connection

The integration connects to the heater's **COM1** (service interface) via a network-to-serial converter:

- **Elfin EE10** (tested)
- **Waveshare RS232 to Ethernet** (should work)
- Any TCP-to-RS232 bridge connected to COM1

```
Fröling Heater (COM1 RS232) ──── Elfin EE10 ──── TCP/IP ──── Home Assistant
```

> **Note:** This integration uses the COM1 service interface with the proprietary binary protocol, **not** COM2/Modbus. The converter must be physically connected to the COM1 DB9 port on the Lambdatronic board.

## Features

### Sensors

The integration automatically discovers all available sensors from your heater. Typical sensors include:

| Type | Examples |
|------|----------|
| Temperatures | Boiler temperature, exhaust gas, buffer top/bottom, flow/return |
| Analog outputs | Pump speed, valve position (%) |
| Operating state | "Heizen", "Brenner aus", "Anheizen", "Störung", ... |
| Operating mode | "Automatik", "Übergangsbetrieb", ... |
| Error tracking | Active error count, last error text |

### Binary Sensors

| Type | Examples |
|------|----------|
| Digital outputs | Pumps (on/off), valves (open/closed) |
| Digital inputs | Door contacts, safety switches |
| Heater error | ON when the heater reports a fault condition |

### Error Monitoring

- **Active error count** -- number of currently active errors
- **Last error** -- text description of the most recent error
- **Error binary sensor** -- ON when any fault state is detected (codes: Störung, Fehler)
- **Full error log** available in the HA diagnostics panel with timestamps and lifecycle states (arrived / acknowledged / gone)

### Diagnostics

Access detailed diagnostic data via **Settings > Integrations > Fröling Heater > Diagnostics**:

- Firmware version
- Heater date/time
- Complete error log
- All discovered sensor specifications

## Installation

### HACS (Recommended)

1. Open **HACS** in Home Assistant
2. Go to **Integrations**
3. Click the **three-dot menu** (top right) > **Custom repositories**
4. Enter: `https://github.com/Tropaion/froeling-ha`
5. Category: **Integration**
6. Click **Add**
7. Find **"Fröling Heater"** in the integration list and click **Install**
8. **Restart Home Assistant**

### Manual Installation

1. Download the latest release from [GitHub](https://github.com/Tropaion/froeling-ha)
2. Copy the `custom_components/froeling/` folder to your Home Assistant `config/custom_components/` directory
3. Restart Home Assistant

## Configuration

1. Go to **Settings** > **Integrations** > **Add Integration**
2. Search for **"Fröling"**
3. Enter the connection details:
   - **Host**: IP address of your TCP-to-serial converter (e.g., `192.168.88.180`)
   - **Port**: TCP port number (e.g., `8899`)
4. The integration will test the connection and discover all available sensors automatically

## Hardware Setup

### Elfin EE10 Configuration

Configure the Elfin EE10 to match the heater's COM1 serial settings:

| Setting | Value |
|---------|-------|
| Baud Rate | 57600 |
| Data Bits | 8 |
| Parity | None |
| Stop Bits | 1 |
| Flow Control | None |
| Mode | TCP Server |

### Wiring

Connect the EE10 to the **COM1** DB9 port on the Lambdatronic mainboard using a **null-modem (crossed) RS232 cable**:

| EE10 (DB9) | Heater COM1 (DB9) |
|------------|-------------------|
| Pin 2 (RX) | Pin 3 (TX) |
| Pin 3 (TX) | Pin 2 (RX) |
| Pin 5 (GND) | Pin 5 (GND) |

## How It Works

The integration implements the proprietary binary protocol used by Fröling Lambdatronic controllers on their COM1 service interface. This is the same protocol used by the [linux-p4d](https://github.com/horchi/linux-p4d) project, reimplemented in async Python.

- **Polling interval**: 60 seconds
- **Protocol**: Binary frames with CRC verification and byte escaping
- **Sensor discovery**: Automatic -- the heater reports all available sensor addresses on startup
- **Read-only**: v0.1 does not write parameters or control outputs

## Troubleshooting

### "Failed to connect to the heater"

- Verify the EE10 is powered and has network connectivity (`ping 192.168.88.180`)
- Check that the EE10 is in TCP Server mode on the correct port
- Ensure the RS232 cable is connected to **COM1** (not COM2)
- Check the EE10 baud rate is set to **57600**
- Only one TCP client can connect to the EE10 at a time -- close any other connections (e.g., socat)

### No sensors discovered

- The heater must be powered on (not just the controller, the heater itself)
- Check the HA logs for "Discovered X sensors" messages
- Try restarting the integration

### Sensor values seem wrong

- Check the HA diagnostics panel for the raw sensor specs (factor, unit)
- Some sensors may report 0 when the heater is in standby

## Credits

- Protocol reverse-engineered by [Jörg Wendel](https://github.com/horchi) in the [linux-p4d](https://github.com/horchi/linux-p4d) project
- Built with [Claude Code](https://claude.ai/claude-code)

## License

This project is licensed under the GNU General Public License v3.0 -- see the [LICENSE](LICENSE) file for details.
