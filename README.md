# Work Pro Integra — Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![Validate](https://github.com/timonbruns/WorkPro-Integra/actions/workflows/validate.yml/badge.svg)](https://github.com/timonbruns/WorkPro-Integra/actions/workflows/validate.yml)

Control your **Work Pro Integra** audio matrix from Home Assistant over the local
network. Adjust volume and mute for all input and output channels, with channel
names read straight from the device and editable in the integration options.
Fast, local UDP control — no cloud required.

> Not affiliated with, endorsed by, or supported by Equipson S.A. / Work Pro.
> "Work Pro" and "Integra" are used only to describe the compatible hardware.

## Features

- Volume (gain, in dB) for every input and output channel as a slider (`number`).
- Mute for every input and output channel as a `switch`.
- Channel names are read from the device and used to label the entities.
- Rename any channel later via the integration's **Configure** dialog.
- Fully local communication over UDP — no cloud, no account.

Tested with the **Work Pro Integra 16+** (16 inputs, 16 outputs).

## Requirements

On the device (via the Integra / Integra Plus software, under **Device Setting**):

- Enable the **"central control reply"** switch — otherwise the device never
  answers status queries.
- Note the UDP control port (default **50000**) and the device IP address.

The device and Home Assistant must be on the same network.

## Installation

### Via HACS (custom repository)

1. In HACS, open the three-dot menu → **Custom repositories**.
2. Add `https://github.com/timonbruns/WorkPro-Integra` with category
   **Integration**.
3. Search for **Work Pro Integra** in HACS, download it, and restart
   Home Assistant.

### Manual

Copy `custom_components/work_integra` into your Home Assistant
`config/custom_components/` directory, then restart Home Assistant.

## Configuration

1. Go to **Settings → Devices & Services → Add Integration**.
2. Search for **Work Pro Integra**.
3. Enter the device IP, UDP port (default 50000), and the number of input and
   output channels (16 each for the 16+).

The setup performs a quick connection test, so a wrong IP/port or a disabled
"central control reply" switch is reported immediately.

### Renaming channels

Open **Settings → Devices & Services → Work Pro Integra → Configure**. You will
be guided through two steps (inputs, then outputs), each pre-filled with the
names read from the device. Leave a field unchanged to keep the device name.

## Entities

For each channel the integration creates:

| Platform | Per channel | Unit | Notes |
| --- | --- | --- | --- |
| `number` | Volume / gain | dB | Slider, range −72 … +12 dB (output, verified on 16+) |
| `switch` | Mute | — | On = muted |

With 16 inputs and 16 outputs that is 64 entities, all grouped under a single
device. Entity names follow the channel names (e.g. *Kitchen Volume*,
*Kitchen Mute*).

## Notes & tuning

- **Gain range:** output gain is −72 … +12 dB. Input gain uses the same range by
  default; adjust `INPUT_GAIN_MIN_DB` / `INPUT_GAIN_MAX_DB` in `const.py` if your
  unit differs.
- **Poll interval:** state is refreshed every 10 s (`SCAN_INTERVAL` in
  `const.py`), which also picks up changes made at a wall controller or in the
  Work software.
- **Brand images:** an icon and logo ship in `custom_components/work_integra/brand/`
  and are shown automatically on Home Assistant 2026.3 and newer.

## Protocol

The integration uses the documented ASCII control protocol over UDP
(e.g. `set:output#gain#0#-6.0`, `get:input#mute#0-15`). The client lives in
`custom_components/work_integra/integra.py` and can be used standalone.

The device's parametric EQ, compressor and routing matrix are only reachable via
the binary protocol and are not implemented yet. EQ presets can be switched
indirectly by recalling scenes.

## Roadmap

- Scene/preset recall as a `select` entity
- System master mute
- Phantom power (48 V) per input
- Optional full EQ control via the binary protocol

## License

[MIT](LICENSE)
