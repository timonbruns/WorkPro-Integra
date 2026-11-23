"""Asynchroner Client für das Work Pro Integra ASCII-Steuerprotokoll (UDP).

Eigenständig nutzbar (auch ohne Home Assistant). Implementiert die in
Appendix C des Integra-Series-Manuals dokumentierten ASCII-Befehle.

Protokoll-Kurzform:
    set:<modul>#<item>#<startKanal>-<endKanal>#<wert>
    get:<modul>#<item>#<startKanal>-<endKanal>
Antworten kommen nur, wenn am Gerät "central control reply" aktiv ist.

Robustheit (v2): UDP darf Pakete verlieren. Deshalb werden get-Abfragen bei
Timeout automatisch wiederholt und nur Antworten akzeptiert, die zum gerade
gestellten Befehl passen (verspätete/fremde Antworten werden verworfen).
"""

from __future__ import annotations

import asyncio
import logging

_LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 50000
_REPLY_TIMEOUT = 1.0   # Sekunden auf eine Antwort warten (pro Versuch)
_RETRIES = 3           # Anzahl Sendeversuche pro get-Abfrage


class IntegraError(Exception):
    """Allgemeiner Fehler bei der Kommunikation mit dem Gerät."""


class _IntegraProtocol(asyncio.DatagramProtocol):
    """Interner asyncio-Datagramm-Handler."""

    def __init__(self, on_datagram) -> None:
        self._on_datagram = on_datagram

    def datagram_received(self, data: bytes, addr) -> None:
        self._on_datagram(data.decode("utf-8", errors="replace").strip())

    def error_received(self, exc: Exception) -> None:  # pragma: no cover
        _LOGGER.debug("UDP error_received: %s", exc)


class IntegraClient:
    """Request/Response-Client über UDP mit Verlust-Toleranz.

    Befehle werden serialisiert (ein offener Request zur Zeit). get-Abfragen
    werden bei Paketverlust mehrfach gesendet; nur passende Antworten zählen.
    """

    def __init__(self, host: str, port: int = DEFAULT_PORT) -> None:
        self.host = host
        self.port = port
        self._transport: asyncio.DatagramTransport | None = None
        self._lock = asyncio.Lock()
        self._pending: asyncio.Future[str] | None = None
        self._expect_token: str | None = None

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
        fut = self._pending
        if fut is None or fut.done():
            return
        # Nur Antworten annehmen, die zum aktuellen Befehl passen. So kann eine
        # verspätete Mute-Antwort nicht als Gain-Antwort fehlinterpretiert werden.
        if self._expect_token is None or self._expect_token in message:
            fut.set_result(message)

    async def _command(
        self,
        command: str,
        expect_reply: bool = True,
        expect_token: str | None = None,
        retries: int = _RETRIES,
    ) -> str | None:
        """Sendet einen ASCII-Befehl. Bei erwarteter Antwort wird bis zu
        `retries`-mal gesendet, falls Pakete verloren gehen."""
        if self._transport is None:
            raise IntegraError("Nicht verbunden — connect() zuerst aufrufen.")

        if not expect_reply:
            # Set-Befehle sofort senden – nicht hinter einer laufenden Abfrage
            # warten (Echtzeit beim Ziehen eines Faders). Ein UDP-Datagramm ist
            # atomar; eine offene get-Abfrage bleibt davon unberührt.
            self._transport.sendto(command.encode("utf-8"))
            return None

        async with self._lock:
            loop = asyncio.get_running_loop()

            for attempt in range(1, retries + 1):
                self._pending = loop.create_future()
                self._expect_token = expect_token
                self._transport.sendto(command.encode("utf-8"))
                try:
                    return await asyncio.wait_for(self._pending, timeout=_REPLY_TIMEOUT)
                except asyncio.TimeoutError:
                    _LOGGER.debug(
                        "Timeout (Versuch %d/%d) auf: %s", attempt, retries, command
                    )
                finally:
                    self._pending = None
                    self._expect_token = None
            return None

    # --- Hilfsparser ----------------------------------------------------

    @staticmethod
    def _parse_values(reply: str) -> tuple[int, int, list[str]]:
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

    @staticmethod
    def _floats(start: int, values: list[str]) -> dict[int, float]:
        """Wandelt Werte um; leere/ungültige Einträge (z. B. abschließendes '#',
        an der Integra 8S beobachtet) werden übersprungen statt den Poll abzubrechen."""
        result: dict[int, float] = {}
        for i, v in enumerate(values):
            try:
                result[start + i] = float(v)
            except ValueError:
                if v.strip():
                    _LOGGER.debug("Unerwarteter Wert %r für Kanal %d ignoriert", v, start + i)
        if not result:
            raise IntegraError(f"Antwort ohne gültige Werte: {values!r}")
        return result

    # --- Lautstärke (Output-Gain) --------------------------------------

    async def set_output_gain(self, channel: int, db: float) -> None:
        await self._command(f"set:output#gain#{channel}#{db}", expect_reply=False)

    async def get_output_gains(self, start: int, end: int) -> dict[int, float]:
        reply = await self._command(
            f"get:output#gain#{start}-{end}", expect_token=f"output#gain#{start}-{end}"
        )
        if reply is None:
            raise IntegraError("Keine Antwort auf get:output#gain")
        s, _e, values = self._parse_values(reply)
        return self._floats(s, values)

    # --- Mute (Output) --------------------------------------------------

    async def set_output_mute(self, channel: int, mute: bool) -> None:
        await self._command(
            f"set:output#mute#{channel}#{1 if mute else 0}", expect_reply=False
        )

    async def get_output_mutes(self, start: int, end: int) -> dict[int, bool]:
        reply = await self._command(
            f"get:output#mute#{start}-{end}", expect_token=f"output#mute#{start}-{end}"
        )
        if reply is None:
            raise IntegraError("Keine Antwort auf get:output#mute")
        s, _e, values = self._parse_values(reply)
        return {s + i: (v == "1") for i, v in enumerate(values)}

    # --- Lautstärke (Input-Gain) ---------------------------------------

    async def set_input_gain(self, channel: int, db: float) -> None:
        await self._command(f"set:input#gain#{channel}#{db}", expect_reply=False)

    async def get_input_gains(self, start: int, end: int) -> dict[int, float]:
        reply = await self._command(
            f"get:input#gain#{start}-{end}", expect_token=f"input#gain#{start}-{end}"
        )
        if reply is None:
            raise IntegraError("Keine Antwort auf get:input#gain")
        s, _e, values = self._parse_values(reply)
        return self._floats(s, values)

    # --- Mute (Input) ---------------------------------------------------

    async def set_input_mute(self, channel: int, mute: bool) -> None:
        await self._command(
            f"set:input#mute#{channel}#{1 if mute else 0}", expect_reply=False
        )

    async def get_input_mutes(self, start: int, end: int) -> dict[int, bool]:
        reply = await self._command(
            f"get:input#mute#{start}-{end}", expect_token=f"input#mute#{start}-{end}"
        )
        if reply is None:
            raise IntegraError("Keine Antwort auf get:input#mute")
        s, _e, values = self._parse_values(reply)
        return {s + i: (v == "1") for i, v in enumerate(values)}

    # --- Pegel / Metering ----------------------------------------------

    async def get_output_levels(self, start: int, end: int) -> dict[int, float]:
        reply = await self._command(
            f"get:output#level#{start}-{end}", expect_token=f"output#level#{start}-{end}"
        )
        if reply is None:
            raise IntegraError("Keine Antwort auf get:output#level")
        s, _e, values = self._parse_values(reply)
        return self._floats(s, values)

    async def get_input_levels(self, start: int, end: int) -> dict[int, float]:
        reply = await self._command(
            f"get:input#level#{start}-{end}", expect_token=f"input#level#{start}-{end}"
        )
        if reply is None:
            raise IntegraError("Keine Antwort auf get:input#level")
        s, _e, values = self._parse_values(reply)
        return self._floats(s, values)

    # --- Kanalnamen -----------------------------------------------------

    async def get_input_names(self, start: int, end: int) -> dict[int, str]:
        reply = await self._command(
            f"get:input#name#{start}-{end}", expect_token=f"input#name#{start}-{end}"
        )
        if reply is None:
            raise IntegraError("Keine Antwort auf get:input#name")
        s, _e, values = self._parse_values(reply)
        return {s + i: v for i, v in enumerate(values)}

    async def get_output_names(self, start: int, end: int) -> dict[int, str]:
        reply = await self._command(
            f"get:output#name#{start}-{end}", expect_token=f"output#name#{start}-{end}"
        )
        if reply is None:
            raise IntegraError("Keine Antwort auf get:output#name")
        s, _e, values = self._parse_values(reply)
        return {s + i: v for i, v in enumerate(values)}

    # --- Matrix (am Gerät verifiziert, Integra 8S) -----------------------
    # Antwort: "get:mixer#switch#<Eingänge>#<Ausgang>#<Wert je Eingang>…"

    async def get_matrix_output(self, output: int, inputs: int) -> dict[int, bool]:
        """Liest, welche Eingänge (0-basiert) auf einen Ausgang (0-basiert) geroutet sind."""
        spec = f"0-{inputs - 1}"
        reply = await self._command(
            f"get:mixer#switch#{spec}#{output}", expect_token=f"mixer#switch#{spec}#{output}#"
        )
        if reply is None:
            raise IntegraError("Keine Antwort auf get:mixer#switch")
        parts = reply.split("#")
        try:
            i = parts.index("switch")
        except ValueError as err:
            raise IntegraError(f"Unerwartete Matrix-Antwort: {reply!r}") from err
        start = int(parts[i + 1].split("-")[0])
        values = parts[i + 3:]
        result = {start + k: v.strip() == "1" for k, v in enumerate(values) if v.strip() in ("0", "1")}
        if not result:
            raise IntegraError(f"Matrix-Antwort ohne Werte: {reply!r}")
        return result

    async def get_scene_names(self, count: int = 16) -> list[str]:
        """Namen der Preset-Speicherplätze (wie in der Work-Software benannt)."""
        reply = await self._command(
            f"get:scene#name#0-{count - 1}", expect_token=f"scene#name#0-{count - 1}"
        )
        if reply is None:
            raise IntegraError("Keine Antwort auf get:scene#name")
        _s, _e, values = self._parse_values(reply)
        return [v.strip() for v in values][:count]

    # --- Szenen / System ------------------------------------------------

    async def recall_scene(self, scene_no: int) -> None:
        await self._command(f"toggle #{scene_no}", expect_reply=False)

    async def set_system_mute(self, mute: bool) -> None:
        await self._command(
            f"set:sysctl#mute#{1 if mute else 0}", expect_reply=False
        )
