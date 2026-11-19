"""Konstanten für die Work Pro Integra Integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "work_integra"

CONF_HOST = "host"
CONF_PORT = "port"
CONF_OUTPUTS = "outputs"
CONF_INPUTS = "inputs"

DEFAULT_PORT = 50000

# Binäres DSP-Protokoll (EQ, Kompressor, Filter, Delay, Matrix, Presets).
CONF_DSP_PORT = "dsp_port"
DEFAULT_DSP_PORT = 6001
PRESET_COUNT = 16  # DSP-Controller: 16 Preset-Speicherplätze

# Event bei jeder über das DSP-Protokoll gemeldeten Parameteränderung
# (z. B. wenn jemand in WorkPro DSP 1.5 einen EQ verstellt).
EVENT_DSP_CHANGED = "work_integra_dsp_changed"
DEFAULT_OUTPUTS = 16  # Integra 16+ hat 16 Output-Kanäle
DEFAULT_INPUTS = 16   # ... und 16 Input-Kanäle

# Gültiger dB-Bereich der Output-Fader.
# Am Gerät (Integra 16+) verifiziert: -72 dB bis +12 dB.
OUTPUT_GAIN_MIN_DB = -72.0
OUTPUT_GAIN_MAX_DB = 12.0

# Gültiger dB-Bereich der Input-Fader.
# Startwert identisch zu Output; bei Bedarf am Gerät verifizieren und anpassen.
INPUT_GAIN_MIN_DB = -72.0
INPUT_GAIN_MAX_DB = 12.0

GAIN_STEP_DB = 0.5

# Poll-Intervall: Wie oft HA den Ist-Zustand vom Gerät abfragt
# (fängt auch externe Änderungen über Wandregler/Software auf).
SCAN_INTERVAL = timedelta(seconds=10)
