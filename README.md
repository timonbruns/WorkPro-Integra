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
- **DSP control (new in 1.2.0):** parametric EQ, input compressor, output
  high-/low-pass, output delay, routing matrix and preset recall via services.
- **Preset select** entity and a **live change event** when someone edits the
  DSP in the Work software.
- Fully local communication over UDP — no cloud, no account.

Gain/mute tested with the **Work Pro Integra 16+**; DSP control tested with the
**Integra 8S** (firmware 30.2.3.3).

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

Additionally (one per device):

| Platform | Entity | Notes |
| --- | --- | --- |
| `select` | Preset | Recalls preset 1–16. Shows the preset last recalled from HA (the device does not report its active preset). |
| `binary_sensor` | DSP-Verbindung | Diagnostic: on while the device answers the DSP keepalive on UDP 6001. |

## DSP control (EQ, compressor, filters, delay, matrix, presets)

These functions use the binary protocol of the Work software (UDP **6001**),
which runs in parallel to the ASCII protocol. Nothing has to be enabled on the
device. On start-up the integration logs in like the Work software (device info
+ full parameter image) and keeps the session alive; if the session drops (device
restart, network outage) it logs in again automatically. All channel, band and preset numbers are **1-based**, exactly as shown in
the Work software. Only the values you pass are changed.

| Service | Fields |
| --- | --- |
| `work_integra.set_peq` | `side` (input/output), `channel`, `band`, `frequency` (Hz), `gain` (dB), `q`, `bypass` |
| `work_integra.set_compressor` | `channel` (input), `threshold` (dB), `ratio`, `attack` (ms), `release` (ms) |
| `work_integra.set_filter` | `channel` (output), `filter` (hpf/lpf), `frequency` (Hz), `slope` (dB/oct) |
| `work_integra.set_delay` | `channel` (output), `delay` (ms, min. 1) |
| `work_integra.set_matrix` | `input`, `output`, `enabled` |
| `work_integra.recall_preset` | `preset` |

`device_id` is only needed when more than one Integra is set up.

```yaml
# Example: "Bass" for output 1 via PEQ band 1 (set up as a low shelf in the Work software)
action: work_integra.set_peq
data:
  side: output
  channel: 1
  band: 1
  gain: 3
```

### Equalizer card

The integration ships a Lovelace card with a frequency-response graph and
draggable band points. It is loaded automatically – no resource entry needed.

```yaml
type: custom:work-integra-eq-card
title: Kanal 1 – Equalizer
prefix: in1        # input_number.in1_eq_band_<1..8>_{gain,frequenz,q} + input_boolean.in1_eq_bypass
db_range: 15       # optional, ±dB of the graph
height: 300        # optional, px
```

- Drag a band point: left/right = frequency, up/down = gain. Double-click/tap = 0 dB.
- Band chips 1–8 select a band; the sliders below set frequency, gain and Q.
- Sum curve plus each band's own curve in its colour; BYPASS button toggles the EQ.
- Instead of `prefix`, bands can be listed explicitly:
  `bands: [{gain: input_number.a, frequency: input_number.b, q: input_number.c}, …]`
  and `bypass: input_boolean.x`.

The card writes to helper entities; an automation forwards helper changes to
`work_integra.set_peq` (see the example under *Live updates*). The curve is
calculated as peaking filters (RBJ cookbook, 48 kHz) – shelf/pass filter types
are not modelled yet.

### Routing matrix

For every input × output crosspoint the integration creates a `switch`
(hidden by default, attributes `matrix_input`, `matrix_output`, `input_name`,
`output_name`). The device does not report the matrix on its own, so a
crosspoint starts as *unknown* and then follows what was switched in HA, live
changes from the Work software, or the state restored after a restart.

```yaml
type: custom:work-integra-matrix-card
title: Matrix
entity_prefix: switch.integra   # optional: only this device's crosspoints
```

The card shows inputs as rows and outputs as columns; tap a crosspoint to switch
it. Hatched points are still unknown.

### Live updates

The Integra forwards every DSP change made by another client (e.g. WorkPro DSP
1.5 on a laptop) to Home Assistant. Each change fires the event
`work_integra_dsp_changed`, e.g.:

```yaml
event_type: work_integra_dsp_changed
data:
  config_entry_id: 01J...
  kind: peq          # peq | compressor | hpf | lpf | delay | matrix | unknown
  side: input
  channel: 1
  band: 2
  param: gain
  value: -4.5
```

Use it to keep `input_number` helpers in sync with the device:

```yaml
triggers:
  - trigger: event
    event_type: work_integra_dsp_changed
    event_data: {kind: peq, side: output, channel: 1, band: 1, param: gain}
actions:
  - action: input_number.set_value
    target: {entity_id: input_number.ls_vorne_bass}
    data: {value: "{{ trigger.event.data.value }}"}
```

### Limitations (DSP part)

- The protocol is undocumented and was reverse-engineered from network captures
  of WorkPro DSP 1.5 ↔ Integra 8S. Verified: input PEQ/compressor, output
  PEQ/HPF/LPF/delay, matrix crosspoint, preset recall. Not yet verified:
  LPF slope and the PEQ filter-type parameter.
- DSP values are write-only for now: HA does not read the current EQ/compressor
  values from the device yet (live changes arrive via the event above).
- Integra 16+ with WorkPro DSP 3.0 uses the same transport but its module
  numbering has not been checked yet.

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

EQ, compressor, filters, delay, matrix and presets use the binary protocol on
UDP 6001 (`custom_components/work_integra/integra_bin.py`, also usable
standalone). Frame: `A5 5A | len u16 | pages u16 | page u16 | cmd | chk | 6×00 |
payload`, checksum = (sum of all other bytes + 1) & 0xFF; replies echo the
request's checksum byte. A SET is `module u16 | param u16 | w1 i16 | w2 i16`.

## Tests

```bash
pip install pytest-homeassistant-custom-component
pytest
```

The client tests compare every generated frame byte-for-byte with frames
captured from the Work software; the HA tests run against a simulated Integra
on localhost.

## Roadmap

- Read current DSP values from the device's parameter image (state for EQ entities)
- Optional EQ/compressor `number` entities
- Level meters via the binary meter stream
- System master mute
- Phantom power (48 V) per input
- Verify module numbering on the Integra 16+ / DSP 3.0

## License

[MIT](LICENSE)
