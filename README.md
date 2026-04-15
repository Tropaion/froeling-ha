<p align="center">
  <img src="https://raw.githubusercontent.com/Tropaion/froeling-ha/main/custom_components/froeling/brand/icon.png" alt="Fröling Heater" width="120">
</p>

<h1 align="center">Fröling Heater Integration for Home Assistant</h1>

<p align="center">
  <a href="https://github.com/hacs/integration"><img src="https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge" alt="HACS Custom"></a>
  <a href="https://www.gnu.org/licenses/gpl-3.0"><img src="https://img.shields.io/badge/License-GPLv3-blue.svg?style=for-the-badge" alt="License: GPL v3"></a>
  <a href="https://github.com/Tropaion/froeling-ha/releases"><img src="https://img.shields.io/github/v/release/Tropaion/froeling-ha?style=for-the-badge&color=green" alt="Latest Release"></a>
</p>

<p align="center">
  A Home Assistant custom integration for <b>Fröling pellet heaters</b><br>
  Reads sensor data, operating state, error logs, and controls heater parameters<br>
  via the proprietary COM1 protocol
</p>

---

## Supported Hardware

<table>
  <tr>
    <td width="50%">

### Heaters

Any Fröling heater with a **Lambdatronic P 3200** or **S 3200** controller:

| Model | Power Range |
|-------|-------------|
| Fröling **P1** Pellet | 7 - 20 kW |
| Fröling **PE1** Pellet | 15 - 35 kW |
| Fröling **P4** Pellet | 15 - 105 kW |
| Other Lambdatronic 3200 models | varies |

</td>
<td width="50%">

### Connection (two options)

| Method | Hardware |
|--------|----------|
| **Network** | TCP-to-serial converter (Elfin EE10, Waveshare, etc.) |
| **USB Serial** | USB-to-RS232 adapter (FTDI, Prolific PL2303, etc.) |

</td>
  </tr>
</table>

```
 Option A: Network
 ┌──────────────┐    RS232     ┌────────────────────┐    TCP/IP    ┌─────────────────┐
 │  Fröling     │◄────────────►│  TCP-to-serial     │◄────────────►│  Home Assistant  │
 │  COM1 (DB9)  │  null-modem  │  converter         │   network    │                 │
 └──────────────┘              └────────────────────┘              └─────────────────┘

 Option B: USB Serial
 ┌──────────────┐    RS232     ┌────────────────────┐     USB      ┌─────────────────┐
 │  Fröling     │◄────────────►│  USB-to-RS232      │◄────────────►│  Home Assistant  │
 │  COM1 (DB9)  │  null-modem  │  adapter           │              │                 │
 └──────────────┘              └────────────────────┘              └─────────────────┘
```

> **Note:** This integration uses the **COM1 service interface** with the proprietary binary protocol, **not** COM2/Modbus.

---

## Features

### Automatic Sensor Discovery

The integration connects to your heater and **automatically discovers all available sensors** with their current values. You choose which sensors to monitor -- only selected sensors are polled, minimizing serial traffic.

<table>
  <tr>
    <td width="50%">

#### Sensors

| Type | Examples |
|------|----------|
| Temperatures | Boiler, exhaust gas, buffer, flow/return, outside |
| Percentages | Pump speed, valve position, fan speed |
| Operating state | "Heizen", "Brenner aus", "Störung" |
| Operating mode | "Automatik", "Übergangsbetrieb" |
| Counters | Operating hours, pellet consumption, ignitions |
| Errors | Active count, last error text with state |

</td>
<td width="50%">

#### Write Support (optional)

| Entity Type | Examples |
|-------------|----------|
| **Select** | Operating mode (Sommer/Übergang/Winter) |
| **Number** | Boiler target temp, DHW temp, flow temps |
| **Select** | Legionella heating (Ein/Aus), sliding mode |

Write mode is **opt-in** with safety warnings.
Parameters categorized as **Basic** and **Expert**.

</td>
  </tr>
</table>

#### Error Monitoring

| Entity | Description |
|--------|-------------|
| Active Errors | Count of unacknowledged errors |
| Last Error | Error text + state (aktiv / quittiert / gegangen) |
| Heater Error | Binary sensor: ON during fault conditions |

#### Diagnostics

Access detailed data via **Settings > Integrations > Fröling Heater > Diagnostics**: firmware version, heater date/time, complete error log, all sensor specs.

---

## Installation

### HACS (Recommended)

1. Open **HACS** > **Integrations** > three-dot menu > **Custom repositories**
2. Enter: `https://github.com/Tropaion/froeling-ha` | Category: **Integration**
3. Find **"Fröling Heater"** and click **Install**
4. **Restart Home Assistant**

### Manual

1. Download the [latest release](https://github.com/Tropaion/froeling-ha/releases)
2. Copy `custom_components/froeling/` to your HA `config/custom_components/`
3. Restart Home Assistant

---

## Setup Flow

The integration guides you through setup with progress indicators:

| Step | Description |
|------|-------------|
| **1. Connection type** | Choose **Network** (TCP) or **USB Serial** |
| **2. Connection details** | Device name + host/port or serial device path |
| **3. Sensor scanning** | Progress spinner while discovering sensors (~60s) |
| **4. Sensor selection** | Browse sensors with live values, select which to monitor |
| **5. Access mode** | Read-only (default) or Read/Write with safety warning |
| **6. Parameter scanning** | _(write mode only)_ Discovers writable parameters (~30s) |
| **7. Basic parameters** | _(write mode only)_ Select from safe, commonly-used parameters |
| **8. Expert parameters** | _(write mode only, optional)_ Explicit button to access internal settings |

### After Setup

| Setting | Where | Description |
|---------|-------|-------------|
| Polling interval | Configure button | 10 - 600 seconds (default: 60) |
| Sensor re-selection | Configure button | Re-discover and change selection |
| Connection settings | Reconfigure | Change host/port without removing |
| Debug logging | Enable debug logging | Downloads detailed protocol log |

---

## Hardware Setup

### Serial Settings

The heater's COM1 port uses these fixed settings (configure your adapter to match):

| Setting | Value |
|---------|-------|
| Baud Rate | **57600** |
| Data Bits | 8 |
| Parity | None |
| Stop Bits | 1 |
| Flow Control | None |
| Mode | **TCP Server** (network only) |

### USB Serial

Plug the adapter into your HA host. The device appears as `/dev/ttyUSB0` (Linux) or `COM3` (Windows).

> **Tip:** On HA OS, check **Settings > System > Hardware** to find your device path.

### Wiring

Connect your adapter to **COM1** on the Lambdatronic mainboard using a **null-modem (crossed) RS232 cable**:

| Adapter (DB9) | Heater COM1 (DB9) |
|---------------|-------------------|
| Pin 2 (RX) | Pin 3 (TX) |
| Pin 3 (TX) | Pin 2 (RX) |
| Pin 5 (GND) | Pin 5 (GND) |

---

## How It Works

This integration reimplements the proprietary binary protocol used by Fröling Lambdatronic controllers on their COM1 service interface.

| Aspect | Detail |
|--------|--------|
| Protocol | Binary frames with CRC verification and byte escaping |
| Connection | Single persistent connection (TCP or USB serial) |
| Polling | Configurable interval, only selected sensors polled |
| Discovery | Automatic -- heater reports available sensors and parameters |
| Read | All sensor types (temperatures, I/O, status, errors) |
| Write | Parameters via cmdSetParameter with value validation |
| Startup | Fast (~5s) -- sensor specs cached from setup, no re-discovery |
| Recovery | Automatic reconnect if connection drops |
| Languages | German + English UI, sensor names from heater (German) |

---

## Troubleshooting

<details>
<summary><b>"Failed to connect to the heater"</b></summary>

- Verify the adapter is powered and reachable (`ping <ip>`)
- Check TCP Server mode and correct port
- Ensure RS232 cable is on **COM1** (not COM2)
- Verify baud rate **57600**
- Only one TCP client at a time -- close other connections (socat, etc.)

</details>

<details>
<summary><b>No sensors discovered</b></summary>

- Heater must be fully powered on (not just the display)
- Check HA logs for "Discovered X sensors"
- Try removing and re-adding the integration

</details>

<details>
<summary><b>Sensor values seem wrong</b></summary>

- Check diagnostics panel for raw sensor specs (factor, unit)
- Some sensors report 0 when heater is in standby
- Temperature sensors reading 0.0°C during setup = no physical sensor connected

</details>

<details>
<summary><b>Write mode: parameter not found</b></summary>

- The parameter may be in the Expert section (click "Configure expert parameters")
- Some parameters only exist on certain heater models
- Check the debug log for "Menu tree types found" to see all available types

</details>

---

## Attribution & Acknowledgements

This integration would not be possible without the **[linux-p4d](https://github.com/horchi/linux-p4d)** project by **[Jörg Wendel (@horchi)](https://github.com/horchi)**, which reverse-engineered the proprietary binary protocol used by Fröling Lambdatronic controllers on the COM1 service interface.

The `pyfroeling` protocol library bundled in this integration is a **clean-room Python reimplementation** of the protocol as documented in the linux-p4d source code (`p4io.c`, `service.h`, `service.c`, `lib/common.c`). No code was copied -- only the protocol specification was referenced.

linux-p4d is licensed under the [GNU General Public License v2.0](https://github.com/horchi/linux-p4d/blob/master/LICENSE).

### Credits

- Protocol: [linux-p4d](https://github.com/horchi/linux-p4d) by [@horchi](https://github.com/horchi)
- Built with [Claude Code](https://claude.ai/claude-code)
- Inspired by [ha_froeling_lambdatronic_modbus](https://github.com/GyroGearl00se/ha_froeling_lambdatronic_modbus) and [pe1-modbus](https://github.com/smokyflex/pe1-modbus)

---

<p align="center">
  <sub>Licensed under the <a href="https://www.gnu.org/licenses/gpl-3.0">GNU General Public License v3.0</a></sub>
</p>
