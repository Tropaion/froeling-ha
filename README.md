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
  Reads sensor data, operating state, and error logs via the proprietary COM1 protocol
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

Connect to the heater's **COM1** port via either method:

| Method | Hardware | Examples |
|--------|----------|----------|
| **Network** | TCP-to-serial converter | Elfin EE10, Waveshare RS232-to-Ethernet |
| **USB Serial** | USB-to-RS232 adapter | FTDI USB-RS232, Prolific PL2303 |

</td>
  </tr>
</table>

**Option A: Network (TCP-to-serial converter)**
```
 ┌──────────────┐    RS232     ┌────────────────────┐    TCP/IP    ┌─────────────────┐
 │  Fröling     │◄────────────►│  TCP-to-serial     │◄────────────►│  Home Assistant  │
 │  COM1 (DB9)  │  null-modem  │  converter         │   network    │  Integration     │
 └──────────────┘              └────────────────────┘              └─────────────────┘
```

**Option B: USB Serial (direct connection)**
```
 ┌──────────────┐    RS232     ┌────────────────────┐     USB      ┌─────────────────┐
 │  Fröling     │◄────────────►│  USB-to-RS232      │◄────────────►│  Home Assistant  │
 │  COM1 (DB9)  │  null-modem  │  adapter           │              │  Integration     │
 └──────────────┘              └────────────────────┘              └─────────────────┘
```

> **Note:** This integration uses the **COM1 service interface** with the proprietary binary protocol, **not** COM2/Modbus. The adapter must be connected to the **COM1 DB9 port** on the Lambdatronic board.

---

## Features

### Sensor Discovery

The integration **automatically discovers all available sensors** from your heater during setup. You choose which sensors to monitor -- only selected sensors are polled, minimizing serial traffic.

<table>
  <tr>
    <td width="50%">

#### Sensors

| Type | Examples |
|------|----------|
| Temperatures | Boiler, exhaust gas, buffer, flow/return |
| Percentages | Pump speed, valve position, fan speed |
| Operating state | "Heizen", "Brenner aus", "Störung" |
| Operating mode | "Automatik", "Übergangsbetrieb" |
| Counters | Operating hours, pellet consumption |
| Errors | Active count, last error text |

</td>
<td width="50%">

#### Binary Sensors

| Type | Device Class |
|------|--------------|
| Heater fault state | `problem` |

#### Error Monitoring

| Entity | Description |
|--------|-------------|
| Active Errors | Count of unacknowledged errors |
| Last Error | Error text + state (aktiv/quittiert/gegangen) |
| Heater Error | ON during fault conditions |

</td>
  </tr>
</table>

#### Diagnostics

Access detailed diagnostic data via **Settings > Integrations > Fröling Heater > Diagnostics**:

> Firmware version, heater date/time, complete error log with timestamps, all discovered sensor specifications

---

## Installation

### HACS (Recommended)

1. Open **HACS** in Home Assistant
2. Go to **Integrations** > **three-dot menu** > **Custom repositories**
3. Enter: `https://github.com/Tropaion/froeling-ha` | Category: **Integration**
4. Find **"Fröling Heater"** and click **Install**
5. **Restart Home Assistant**

### Manual

1. Download the [latest release](https://github.com/Tropaion/froeling-ha/releases)
2. Copy `custom_components/froeling/` to your HA `config/custom_components/`
3. Restart Home Assistant

---

## Configuration

### Setup Flow

The integration guides you through a **3-step setup**:

| Step | Description |
|------|-------------|
| **1. Connection Type** | Choose **Network** (TCP) or **USB Serial** |
| **2. Connection Details** | Network: host + port. Serial: device path (e.g., `/dev/ttyUSB0`). Both: custom device name. |
| **3. Sensor Selection** | Browse discovered sensors with live values, select which to monitor |

### Options (after setup)

**Settings > Integrations > Fröling Heater > Configure**

| Option | Default | Range |
|--------|---------|-------|
| Polling interval | 60 seconds | 10 - 600 seconds |
| Sensor selection | Changeable | Re-discover and re-select |

---

## Hardware Setup

### Serial Settings (both connection types)

The heater's COM1 port uses these fixed settings:

| Setting | Value |
|---------|-------|
| Baud Rate | **57600** |
| Data Bits | 8 |
| Parity | None |
| Stop Bits | 1 |
| Flow Control | None |

### Network: TCP-to-Serial Converter

Configure the converter to the settings above, set it to **TCP Server** mode, and note the IP address and port.

### USB Serial: USB-to-RS232 Adapter

Plug the adapter into your HA host. The device will appear as `/dev/ttyUSB0` (Linux) or `COM3` (Windows). No driver configuration needed for FTDI-based adapters.

> **Tip:** On HA OS, USB devices are available under `/dev/ttyUSB0` or `/dev/ttyACM0`. Check **Settings > System > Hardware** to find your device path.

### Wiring (both connection types)

Connect your adapter to **COM1** on the Lambdatronic mainboard using a **null-modem (crossed) RS232 cable**:

| Adapter (DB9) | Heater COM1 (DB9) |
|---------------|-------------------|
| Pin 2 (RX) | Pin 3 (TX) |
| Pin 3 (TX) | Pin 2 (RX) |
| Pin 5 (GND) | Pin 5 (GND) |

---

## How It Works

This integration reimplements the proprietary binary protocol used by Fröling Lambdatronic controllers on their COM1 service interface. The protocol was reverse-engineered by the [linux-p4d](https://github.com/horchi/linux-p4d) project.

| Aspect | Detail |
|--------|--------|
| Protocol | Binary frames with CRC verification and byte escaping |
| Polling | Configurable interval (default 60s), only enabled sensors |
| Discovery | Automatic -- heater reports all available sensor addresses |
| Scope | **Read-only** in current version |

---

## Troubleshooting

<details>
<summary><b>"Failed to connect to the heater"</b></summary>

- Verify the converter is powered and reachable (`ping <converter-ip>`)
- Check that the converter is in **TCP Server** mode on the correct port
- Ensure the RS232 cable is connected to **COM1** (not COM2)
- Verify baud rate is set to **57600**
- Only one TCP client can connect at a time -- close other connections (e.g., socat)

</details>

<details>
<summary><b>No sensors discovered</b></summary>

- The heater must be fully powered on (not just the controller display)
- Check HA logs for "Discovered X sensors" messages
- Try removing and re-adding the integration

</details>

<details>
<summary><b>Sensor values seem wrong</b></summary>

- Check the diagnostics panel for raw sensor specs (factor, unit)
- Some sensors report 0 when the heater is in standby
- Temperature sensors reading 0.0°C during setup are filtered (no physical sensor connected)

</details>

---

## Attribution & Acknowledgements

This integration would not be possible without the **[linux-p4d](https://github.com/horchi/linux-p4d)** project by **[Jörg Wendel (@horchi)](https://github.com/horchi)**, which reverse-engineered the proprietary binary protocol used by Fröling Lambdatronic controllers on the COM1 service interface.

The `pyfroeling` protocol library bundled in this integration is a **clean-room Python reimplementation** of the protocol as documented in the linux-p4d source code (`p4io.c`, `service.h`, `service.c`, `lib/common.c`). No code was copied -- only the protocol specification (frame format, byte escaping rules, CRC algorithm, command codes, and response structures) was referenced.

linux-p4d is licensed under the [GNU General Public License v2.0](https://github.com/horchi/linux-p4d/blob/master/LICENSE).

### Credits

- Protocol: [linux-p4d](https://github.com/horchi/linux-p4d) by [@horchi](https://github.com/horchi)
- Built with [Claude Code](https://claude.ai/claude-code)
- Inspired by [ha_froeling_lambdatronic_modbus](https://github.com/GyroGearl00se/ha_froeling_lambdatronic_modbus) and [pe1-modbus](https://github.com/smokyflex/pe1-modbus)

---

<p align="center">
  <sub>Licensed under the <a href="https://www.gnu.org/licenses/gpl-3.0">GNU General Public License v3.0</a></sub>
</p>
