"""Coordinator: fragt den Ist-Zustand des Geräts periodisch ab.

Robustheit (v2): Ein einzelner verpasster Poll darf die Oberfläche NICHT mehr
leeren. Jeder der vier Teilwerte wird einzeln abgefragt; misslingt einer, bleibt
der letzte gute Wert erhalten und die Entitäten bleiben verfügbar. Erst nach
längerer echter Funkstille wird auf "nicht verfügbar" umgeschaltet.
"""

from __future__ import annotations

import logging
import time

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, OPTIMISTIC_HOLD_SECONDS, SCAN_INTERVAL
from .integra import IntegraClient, IntegraError
from .integra_bin import IntegraDspClient

_LOGGER = logging.getLogger(__name__)

# Nach so vielen aufeinanderfolgenden komplett fehlgeschlagenen Polls gilt das
# Gerät als offline (bei 2 s Intervall ~1 Minute).
_MAX_CONSECUTIVE_FAILS = 30


class IntegraCoordinator(DataUpdateCoordinator):
    """Hält den aktuellen Zustand (Gain + Mute je In-/Output-Kanal)."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: IntegraClient,
        outputs: int,
        inputs: int,
        device_names: dict[str, dict[int, str]] | None = None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self.client = client
        self.outputs = outputs
        self.inputs = inputs
        self.device_names = device_names or {"input": {}, "output": {}}
        # Letzter erfolgreich gelesener Zustand (bleibt bei Aussetzern erhalten).
        self._last_good: dict = {}
        self._fail_count = 0
        # Zuletzt aus HA gesendete Werte: (key, channel) -> (gültig bis, Wert)
        self._hold: dict[tuple[str, int], tuple[float, object]] = {}
        # Client für das binäre DSP-Protokoll (wird in async_setup_entry gesetzt).
        self.dsp: IntegraDspClient | None = None
        # Preset-Namen vom Gerät (get:scene#name), in async_setup_entry gelesen
        self.scene_names: list[str] = []
        # Optionen des Config-Entries (Kanalnamen), in async_setup_entry gesetzt
        self.options: dict = {}
        # Pegel-Stream (meters.MeterStreamer), in async_setup_entry gesetzt
        self.meters = None

    def channel_name(self, kind: str, channel: int, options: dict) -> str:
        override = options.get(f"{kind}_name_{channel}")
        if override:
            return override
        device = self.device_names.get(kind, {}).get(channel)
        if device:
            return device
        label = "Input" if kind == "input" else "Output"
        return f"{label} {channel + 1}"

    def set_optimistic(self, key: str, channel: int, value) -> None:
        """Merkt einen aus HA gesendeten Wert und zeigt ihn sofort an."""
        self._hold[(key, channel)] = (time.monotonic() + OPTIMISTIC_HOLD_SECONDS, value)
        if self.data is not None:
            self.data.setdefault(key, {})[channel] = value
        self._last_good.setdefault(key, {})[channel] = value

    def _apply_hold(self, data: dict) -> None:
        now = time.monotonic()
        for (key, channel), (until, value) in list(self._hold.items()):
            if now > until:
                del self._hold[(key, channel)]
            else:
                data.setdefault(key, {})[channel] = value

    async def _async_update_data(self) -> dict:
        """Periodischer Abruf mit Verlust-Toleranz.

        Startet vom letzten guten Stand und überschreibt nur, was erfolgreich
        gelesen wurde. So bleiben Fader stehen, wenn ein Paket verloren geht.
        """
        data: dict = {key: dict(val) for key, val in self._last_good.items()}
        got_something = False

        async def fetch(key: str, coro) -> None:
            nonlocal got_something
            try:
                data[key] = await coro
                got_something = True
            except IntegraError as err:
                _LOGGER.debug("Teilabruf '%s' fehlgeschlagen: %s", key, err)

        if self.outputs:
            last = self.outputs - 1
            await fetch("output_gain", self.client.get_output_gains(0, last))
            await fetch("output_mute", self.client.get_output_mutes(0, last))
        if self.inputs:
            last = self.inputs - 1
            await fetch("input_gain", self.client.get_input_gains(0, last))
            await fetch("input_mute", self.client.get_input_mutes(0, last))

        # Routing-Matrix: je Ausgang eine Abfrage (Eingänge 0..n-1)
        if self.inputs and self.outputs:
            matrix = dict(data.get("matrix", {}))
            matrix_ok = False
            for out in range(self.outputs):
                try:
                    for inp, on in (await self.client.get_matrix_output(out, self.inputs)).items():
                        matrix[(inp, out)] = on
                    matrix_ok = True
                except IntegraError as err:
                    _LOGGER.debug("Matrix-Abfrage Ausgang %d fehlgeschlagen: %s", out + 1, err)
            if matrix_ok:
                data["matrix"] = matrix
                got_something = True

        if got_something:
            self._apply_hold(data)
            self._fail_count = 0
            self._last_good = data
            return data

        # Gar nichts empfangen -> zählen. Werte trotzdem halten, solange die
        # Funkstille kurz ist; erst danach Gerät als offline melden.
        self._fail_count += 1
        if self._fail_count >= _MAX_CONSECUTIVE_FAILS:
            raise UpdateFailed(
                f"Gerät reagiert seit {self._fail_count} Polls nicht."
            )
        return self._last_good
