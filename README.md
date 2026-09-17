# Canton Smart — Unfolded Circle Integration

Integration driver for [Unfolded Circle Remote Two/3](https://www.unfoldedcircle.com/) that controls
Canton Smart Sound devices locally — no cloud required.

Port of the Home Assistant integration [ha-canton](https://github.com/thecodingdad/ha-canton), built
on [ucapi-framework](https://github.com/JackJPowell/ucapi-framework).

## Features

- **Local control** over the reverse-engineered LUCI and Tunnel protocols
- **Auto-discovery** of Canton devices via LSSDP, manual setup as fallback
- **Device-named inputs**: the source list shows the names configured on the device under
  *System Setup → Input Setup → Input Name*; inputs left unnamed are hidden
- **Media player entity**: power, volume, mute, input or preset selection, sound modes,
  play/pause/stop/next/previous/seek/shuffle/repeat and media metadata (title, artist, album,
  cover art) for NET and BT sources
- **Remote entity**: physical button mapping plus UI pages for sources, sound modes, presets,
  EQ and settings — ready to drop into activities
- **Simple commands** for everything else: inputs, sound modes, presets 1-10, EQ steps, subwoofer
  level, lip sync, max volume, sleep timer, standby mode, CEC, DRC, voice clarity, display options,
  RF power/channel and Bluetooth pairing
- **Optional settings entities** (switches, selects, sensor) for direct state feedback
- **Push updates** from the device and automatic reconnect

## Supported devices

The OSD menu settings are model specific; unsupported commands are hidden automatically.

- Canton Smart Soundbar 10
- Canton Smart Soundbar 9
- Canton Smart Sounddeck 100
- Canton Smart Soundbox 3
- Canton Smart Connect 5.1
- Canton Smart Amp 5.1

## Installation

### Integration Manager (recommended)

Install and update through the [Integration Manager](https://github.com/JackJPowell/uc-intg-manager).
It also backs up the integration configuration before an update and restores it afterwards.

### Manual

1. Download the latest `uc-intg-canton_smart-<version>-aarch64.tar.gz` from the
   [releases](https://github.com/thecodingdad/uc-intg-canton-smart/releases) page
2. Upload it in the Remote's web configurator under *Integrations → Install custom integration*
3. Run the setup wizard: pick the discovered device or enter its IP address

### Docker

```yaml
services:
  uc-intg-canton-smart:
    image: ghcr.io/thecodingdad/uc-intg-cantonsmart:latest
    container_name: uc-intg-canton-smart
    network_mode: host
    volumes:
      - ./config:/config
    restart: unless-stopped
```

Host networking is required — LSSDP discovery uses UDP multicast on port 1800.

## Setup options

After the device is selected, the setup wizard asks for:

| Option | Description |
|---|---|
| **Device model** | Decides which OSD menu settings exist. Pre-filled from discovery. |
| **Source list** | Whether the media player source list shows the **inputs** (as named on the device) or the configured **presets**. Inputs and presets stay available as simple commands either way. |
| **Additional settings entities** | Creates switch, select and sensor entities for the device settings. Off by default — the media player and remote entity already expose every function. |

All three can be changed later: start the setup again and choose
*Update information for selected device*.

## Sharing a device with other controllers

Canton devices accept only a **single** connection on the tunnel port (50006) — a second client
(Canton app, Home Assistant, another Remote) takes the slot over and the device closes the previous
connection. Two controllers holding the tunnel permanently would disconnect each other in a loop.

The integration handles this automatically:

- **Exclusive mode** (default): the tunnel is held permanently, state changes arrive as push
  messages — no delay.
- **Shared mode**: as soon as another controller takes the tunnel, the integration stops holding it
  and switches to short sessions (~200 ms) per command plus a state poll every 10 seconds. Commands
  keep working, device-side changes show up within the poll interval.
- Every few minutes the integration checks whether the tunnel is free again and returns to exclusive
  mode. The retry interval grows from 5 up to 30 minutes while the tunnel stays occupied.

Connecting never sends `MID_TUNNELING_START` unless the plain connect fails — the device keeps the
port open, and the start command would kick whoever is connected.

The Home Assistant integration [ha-canton](https://github.com/thecodingdad/ha-canton) uses the same
mechanism, so a Remote and Home Assistant can control the same device.

## Development

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
UC_CONFIG_HOME=./config python intg-cantonsmart/driver.py
```

Then add the driver in the Remote's web configurator as an external integration
(`ws://<host>:9090`).

| Variable | Description | Default |
|----------|-------------|---------|
| `UC_LOG_LEVEL` | Logging level | `DEBUG` |
| `UC_CONFIG_HOME` | Configuration directory | `/config` |
| `UC_INTEGRATION_INTERFACE` | Network interface to bind | `0.0.0.0` |
| `UC_INTEGRATION_HTTP_PORT` | HTTP port | `9090` |
| `UC_DISABLE_MDNS_PUBLISH` | Disable mDNS advertisement | `false` |

## Protocols

- **LSSDP** (UDP 1800) — device discovery via multicast
- **LUCI** (TCP 7777) — registration, streaming volume, playback control and metadata for NET/BT
- **Tunnel** (TCP 50006) — power, input, sound mode, EQ, presets and OSD menu settings

## License

Mozilla Public License 2.0 — see [LICENSE](LICENSE).
