"""Asynchroner Client für das Work Pro Integra ASCII-Steuerprotokoll (UDP).

Eigenständig nutzbar (auch ohne Home Assistant). Implementiert die in
Appendix C des Integra-Series-Manuals dokumentierten ASCII-Befehle.

Protokoll-Kurzform:
    set:<modul>#<item>#<startKanal>-<endKanal>#<wert>
    get:<modul>#<item>#<startKanal>-<endKanal>
Antworten kommen nur, wenn am Gerät "central control reply" aktiv ist.
"""

from __future__ import annotations

import asyncio
import logging

_LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 50000
_REPLY_TIMEOUT = 2.0  # Sekunden auf eine Antwort warten


class IntegraError(Exception):
    """Allgemeiner Fehler bei der Kommunikation mit dem Gerät."""


class _IntegraProtocol(asyncio.DatagramProtocol):
    """Interner asyncio-Datagramm-Handler. Leitet eingehende Pakete an den
    Client weiter, der sie der jeweils offenen Anfrage zuordnet."""

    def __init__(self, on_datagram) -> None:
        self._on_datagram = on_datagram

    def datagram_received(self, data: bytes, addr) -> None:
        self._on_datagram(data.decode("utf-8", errors="replace").strip())

    def error_received(self, exc: Exception) -> None:  # pragma: no cover
        _LOGGER.debug("UDP error_received: %s", exc)


class IntegraClient:
    """Minimaler, robuster Request/Response-Client über UDP.

    Befehle werden serialisiert (ein offener Request zur Zeit), was für die
    Steuerung aus Home Assistant völlig ausreicht und zuverlässig ist.
    """

    def __init__(self, host: str, port: int = DEFAULT_PORT) -> None:
        self.host = host
        self.port = port
        self._transport: asyncio.DatagramTransport | None = None
        self._lock = asyncio.Lock()
        self._pending: asyncio.Future[str] | None = None

    async def connect(self) -> None:
        """Öffnet den UDP-Endpunkt zum Gerät."""
        loop = asyncio.get_running_loop()
        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: _IntegraProtocol(self._handle_datagram),
            remote_addr=(self.host, self.port),
        )

    def close(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None

    def _handle_datagram(self, message: str) -> None:
        if self._pending is not None and not self._pending.done():
            self._pending.set_result(message)

    async def _command(self, command: str, expect_reply: bool = True) -> str | None:
        """Sendet einen rohen ASCII-Befehl. Gibt die Antwort zurück bzw. None
        bei Timeout oder wenn keine Antwort erwartet wird."""
        if self._transport is None:
            raise IntegraError("Nicht verbunden — connect() zuerst aufrufen.")

        async with self._lock:
            loop = asyncio.get_running_loop()
            self._pending = loop.create_future() if expect_reply else None
            self._transport.sendto(command.encode("utf-8"))

            if not expect_reply:
                return None
            try:
                return await asyncio.wait_for(self._pending, timeout=_REPLY_TIMEOUT)
            except asyncio.TimeoutError:
                _LOGGER.debug("Timeout auf Befehl: %s", command)
                return None
            finally:
                self._pending = None

    # --- Hilfsparser ----------------------------------------------------

    @staticmethod
    def _parse_values(reply: str) -> tuple[int, int, list[str]]:
        """Zerlegt eine get-Antwort wie
        'get:output#gain#0-15#-6.0#6.0#...' in (start, end, [werte...])."""
        parts = reply.split("#")
        if len(parts) < 4:
            raise IntegraError(f"Unerwartete Antwort: {reply!r}")
        channel_spec = parts[2]
        if "-" in channel_spec:
            start_s, end_s = channel_spec.split("-", 1)
            start, end = int(start_s), int(end_s)
        else:
            start = end = int(channel_spec)
        return start, end, parts[3:]

    # --- Lautstärke (Output-Gain) --------------------------------------

    async def set_output_gain(self, channel: int, db: float) -> None:
        await self._command(f"set:output#gain#{channel}#{db}")

    async def get_output_gains(self, start: int, end: int) -> dict[int, float]:
        reply = await self._command(f"get:output#gain#{start}-{end}")
        if reply is None:
            raise IntegraError("Keine Antwort auf get:output#gain")
        s, _e, values = self._parse_values(reply)
        return {s + i: float(v) for i, v in enumerate(values)}

    # --- Mute (Output) --------------------------------------------------

    async def set_output_mute(self, channel: int, mute: bool) -> None:
        await self._command(f"set:output#mute#{channel}#{1 if mute else 0}")

    async def get_output_mutes(self, start: int, end: int) -> dict[int, bool]:
        reply = await self._command(f"get:output#mute#{start}-{end}")
        if reply is None:
            raise IntegraError("Keine Antwort auf get:output#mute")
        s, _e, values = self._parse_values(reply)
        return {s + i: (v == "1") for i, v in enumerate(values)}

    # --- Lautstärke (Input-Gain) ---------------------------------------

    async def set_input_gain(self, channel: int, db: float) -> None:
        await self._command(f"set:input#gain#{channel}#{db}")

    async def get_input_gains(self, start: int, end: int) -> dict[int, float]:
        reply = await self._command(f"get:input#gain#{start}-{end}")
        if reply is None:
            raise IntegraError("Keine Antwort auf get:input#gain")
        s, _e, values = self._parse_values(reply)
        return {s + i: float(v) for i, v in enumerate(values)}

    # --- Mute (Input) ---------------------------------------------------

    async def set_input_mute(self, channel: int, mute: bool) -> None:
        await self._command(f"set:input#mute#{channel}#{1 if mute else 0}")

    async def get_input_mutes(self, start: int, end: int) -> dict[int, bool]:
        reply = await self._command(f"get:input#mute#{start}-{end}")
        if reply is None:
            raise IntegraError("Keine Antwort auf get:input#mute")
        s, _e, values = self._parse_values(reply)
        return {s + i: (v == "1") for i, v in enumerate(values)}

    # --- Pegel / Metering ----------------------------------------------

    async def get_output_levels(self, start: int, end: int) -> dict[int, float]:
        reply = await self._command(f"get:output#level#{start}-{end}")
        if reply is None:
            raise IntegraError("Keine Antwort auf get:output#level")
        s, _e, values = self._parse_values(reply)
        return {s + i: float(v) for i, v in enumerate(values)}

    async def get_input_levels(self, start: int, end: int) -> dict[int, float]:
        reply = await self._command(f"get:input#level#{start}-{end}")
        if reply is None:
            raise IntegraError("Keine Antwort auf get:input#level")
        s, _e, values = self._parse_values(reply)
        return {s + i: float(v) for i, v in enumerate(values)}

    # --- Kanalnamen -----------------------------------------------------

    async def get_input_names(self, start: int, end: int) -> dict[int, str]:
        reply = await self._command(f"get:input#name#{start}-{end}")
        if reply is None:
            raise IntegraError("Keine Antwort auf get:input#name")
        s, _e, values = self._parse_values(reply)
        return {s + i: v for i, v in enumerate(values)}

    async def get_output_names(self, start: int, end: int) -> dict[int, str]:
        reply = await self._command(f"get:output#name#{start}-{end}")
        if reply is None:
            raise IntegraError("Keine Antwort auf get:output#name")
        s, _e, values = self._parse_values(reply)
        return {s + i: v for i, v in enumerate(values)}

    # --- Szenen / System ------------------------------------------------

    async def recall_scene(self, scene_no: int) -> None:
        # Szenen-Aufruf nutzt eigene Syntax mit Leerzeichen.
        await self._command(f"toggle #{scene_no}", expect_reply=False)

    async def set_system_mute(self, mute: bool) -> None:
        await self._command(f"set:sysctl#mute#{1 if mute else 0}")
