"""Pegelanzeigen (Meter) als Live-Stream ohne Entitäten.

Solange mindestens eine Karte abonniert hat, fragt die Integration die Pegel
aller Ein- und Ausgänge zyklisch per ASCII ab (get:input#level / get:output#level)
und schickt sie per Websocket an die Karten. Keine Entitäten, kein Recorder.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .integra import IntegraClient, IntegraError

_LOGGER = logging.getLogger(__name__)

DEFAULT_INTERVAL = 0.1   # s zwischen zwei Abfragen (≈ 10 Updates/s)


class MeterStreamer:
    """Fragt Pegel nur ab, solange jemand zuschaut."""

    def __init__(self, hass: HomeAssistant, client: IntegraClient, inputs: int, outputs: int) -> None:
        self.hass = hass
        self.client = client
        self.inputs = inputs
        self.outputs = outputs
        self._subs: dict[int, tuple[Callable[[dict], None], float]] = {}
        self._next_id = 0
        self._task: asyncio.Task | None = None

    @property
    def interval(self) -> float:
        return min((iv for _cb, iv in self._subs.values()), default=DEFAULT_INTERVAL)

    @callback
    def subscribe(self, cb: Callable[[dict], None], interval: float = DEFAULT_INTERVAL) -> Callable[[], None]:
        sid = self._next_id
        self._next_id += 1
        self._subs[sid] = (cb, interval)
        if self._task is None or self._task.done():
            self._task = self.hass.async_create_background_task(self._loop(), f"{DOMAIN}_meters")

        @callback
        def unsubscribe() -> None:
            self._subs.pop(sid, None)
            if not self._subs and self._task is not None:
                self._task.cancel()
                self._task = None

        return unsubscribe

    def stop(self) -> None:
        self._subs.clear()
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _read(self, getter, count: int) -> list[float | None]:
        if not count:
            return []
        try:
            values = await getter(0, count - 1, fast=True)
        except IntegraError as err:
            _LOGGER.debug("Pegelabfrage fehlgeschlagen: %s", err)
            return [None] * count
        return [values.get(i) for i in range(count)]

    async def _loop(self) -> None:
        while self._subs:
            started = time.monotonic()
            data = {
                "in": await self._read(self.client.get_input_levels, self.inputs),
                "out": await self._read(self.client.get_output_levels, self.outputs),
                "ts": time.time(),
            }
            for cb, _iv in list(self._subs.values()):
                try:
                    cb(data)
                except Exception:  # pragma: no cover - Abonnent darf den Stream nicht stoppen
                    _LOGGER.exception("Fehler beim Senden der Pegel")
            await asyncio.sleep(max(0.0, self.interval - (time.monotonic() - started)))


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/subscribe_meters",
        vol.Optional("entry_id"): str,
        vol.Optional("interval", default=DEFAULT_INTERVAL): vol.All(
            vol.Coerce(float), vol.Range(min=0.05, max=2.0)
        ),
    }
)
@websocket_api.async_response
async def ws_subscribe_meters(hass: HomeAssistant, connection, msg: dict) -> None:
    """Abonniert die Pegel einer Integra: {"in": [dB…], "out": [dB…], "names": {...}}."""
    loaded = hass.data.get(DOMAIN, {})
    entry_id = msg.get("entry_id") or (next(iter(loaded)) if len(loaded) == 1 else None)
    coordinator = loaded.get(entry_id) if entry_id else None
    if coordinator is None:
        connection.send_error(msg["id"], "not_found", "Integra nicht gefunden – bitte entry_id angeben")
        return

    names = {
        "in": [coordinator.channel_name("input", i, coordinator.options) for i in range(coordinator.inputs)],
        "out": [coordinator.channel_name("output", i, coordinator.options) for i in range(coordinator.outputs)],
    }

    first = True

    @callback
    def forward(data: dict) -> None:
        nonlocal first
        payload = {**data, "names": names} if first else data   # Namen nur mit der ersten Meldung
        first = False
        connection.send_message(websocket_api.event_message(msg["id"], payload))

    # Erst bestätigen, dann abonnieren: HA startet Tasks sofort, die erste
    # Pegelmeldung darf nicht vor der Bestätigung beim Frontend ankommen.
    connection.send_result(msg["id"], {"names": names})
    connection.subscriptions[msg["id"]] = coordinator.meters.subscribe(forward, msg["interval"])


@callback
def async_register_websocket(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_subscribe_meters)
