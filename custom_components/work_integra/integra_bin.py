"""Asynchroner Client für das binäre DSP-Protokoll der Work Pro Integra (UDP 6001).

Dieses Protokoll nutzt die Herstellersoftware (WorkPro DSP 1.5) für alles, was
das ASCII-Protokoll nicht abdeckt: parametrischer EQ, Kompressor, Hoch-/Tiefpass,
Delay, Routing-Matrix und Presets. Es ist nicht offiziell dokumentiert; die
Kodierung wurde aus Netzwerkmitschnitten an einer Integra 8S (Firmware 30.2.3.3)
rekonstruiert und am Gerät gegengeprüft.

Rahmen (alle Mehrbyte-Werte Little Endian)::

    A5 5A | len u16 | pages u16 | page u16 | cmd u8 | chk u8 | 00 flags 00 00 00 00 | payload

* ``len``  – Länge des Payloads (Bytes nach dem 16-Byte-Header)
* ``chk``  – bei Anfragen: (Summe aller übrigen Bytes + 1) & 0xFF.
  Antworten des Geräts tragen KEINE eigene Prüfsumme, sondern spiegeln das
  ``chk`` der Anfrage – darüber lassen sich Antworten eindeutig zuordnen.
* Antworten tragen ``cmd + 1``; SET (0x21) wird NICHT quittiert.
* Sitzung: Die Integra beantwortet Keepalives (0x55) und leitet Änderungen
  erst an einen Client weiter, nachdem er das komplette Speicherabbild gelesen
  hat (alle Seiten via 0x11; nach der letzten Seite sendet das Gerät zusätzlich
  0xA3). „Meter aus“ (0xAA 00) beendet die Sitzung. Am Gerät verifiziert.
* Ein SET, das ein Client sendet, leitet die Integra 1:1 an die anderen
  angemeldeten Clients weiter. Darüber bekommt Home Assistant Änderungen aus
  der Herstellersoftware live mit.

SET-Payload: ``module u16 | param u16 | w1 i16 | w2 i16``

Eigenständig nutzbar (auch ohne Home Assistant).
"""

from __future__ import annotations

import asyncio
import logging
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass

_LOGGER = logging.getLogger(__name__)

DEFAULT_DSP_PORT = 6001

MAGIC = b"\xA5\x5A"
HEADER_LEN = 16

# Befehle
CMD_INFO = 0x01          # Geräteinfo -> 0x02
CMD_STATUS = 0x05        # -> 0x06
CMD_READ_PAGE = 0x11     # Speicherabbild seitenweise lesen -> 0x12
CMD_RECALL = 0x13        # Preset abrufen (u32, 0-basiert) -> 0x14
CMD_STORE = 0x15         # Preset speichern (u32, 0-basiert) -> 0x16
CMD_SET = 0x21           # Parameter setzen, keine Antwort
CMD_KEEPALIVE = 0x55     # Antwort trägt ebenfalls 0x55
CMD_METER = 0xAA         # Meter-Stream an/aus

# Module (SET)
MOD_MATRIX = 0xA6


def mod_input_comp(channel: int) -> int:
    """Kompressor eines Eingangs (Kanal 1-basiert)."""
    return 0x20 + channel


def mod_input_peq(channel: int) -> int:
    """PEQ eines Eingangs (Kanal 1-basiert)."""
    return 0x60 + channel


def mod_output_xover(channel: int) -> int:
    """Hoch-/Tiefpass eines Ausgangs (Kanal 1-basiert)."""
    return 0xA6 + channel


def mod_output_peq(channel: int) -> int:
    """PEQ eines Ausgangs (Kanal 1-basiert)."""
    return 0xC6 + channel


def mod_output_delay(channel: int) -> int:
    """Delay eines Ausgangs (Kanal 1-basiert)."""
    return 0xE6 + channel


# Parameter
PEQ_BYPASS = 1  # 1 = EQ gebrückt (in DSP 1.5 ausgegraut), 0 = EQ aktiv – am Gerät verifiziert
PEQ_TYPE = 2
PEQ_FREQ = 3
PEQ_GAIN = 4
PEQ_Q = 5

COMP_THRESHOLD = 2
COMP_RATIO = 3
COMP_ATTACK = 4
COMP_RELEASE = 5

XOVER_HPF_SLOPE = 3
XOVER_HPF_FREQ = 4
XOVER_LPF_SLOPE = 0x13  # aus der Systematik abgeleitet, noch nicht mitgeschnitten
XOVER_LPF_FREQ = 0x14

DELAY_MS = 2

MATRIX_POINT = 1

# Nach dieser Zeit ohne gültiges Paket gilt die DSP-Verbindung als getrennt.
_KEEPALIVE_INTERVAL = 2.0
_OFFLINE_AFTER = 7.0      # ohne Keepalive-Antwort gilt die Sitzung als beendet
_LOGIN_RETRY = 10.0       # Mindestabstand zwischen zwei Anmeldeversuchen
_REPLY_TIMEOUT = 1.0
_RETRIES = 3


class IntegraDspError(Exception):
    """Fehler bei der Kommunikation über das DSP-Protokoll."""


def checksum(data: bytes | bytearray) -> int:
    """Prüfsumme über alle Bytes außer dem Prüfsummenbyte selbst."""
    return (sum(data[:9]) + sum(data[10:]) + 1) & 0xFF


def build_frame(
    cmd: int, payload: bytes = b"", pages: int = 0, page: int = 0, flags: int = 0
) -> bytes:
    """Baut einen vollständigen Rahmen inklusive Prüfsumme."""
    header = bytearray(
        struct.pack("<2sHHHBBxBxxxx", MAGIC, len(payload), pages, page, cmd, 0, flags)
    )
    data = header + payload
    data[9] = checksum(data)
    return bytes(data)


def build_set(module: int, param: int, w1: int, w2: int = 0) -> bytes:
    """Baut einen SET-Rahmen."""
    return build_frame(CMD_SET, struct.pack("<HHhh", module, param, w1, w2))


@dataclass(frozen=True)
class Frame:
    """Ein empfangener, geprüfter Rahmen."""

    cmd: int
    chk: int
    pages: int
    page: int
    payload: bytes
    checksum_ok: bool


def parse_frame(data: bytes) -> Frame | None:
    """Zerlegt einen Rahmen. Falsche Kennung/Länge ergibt None.

    ``checksum_ok`` ist nur für Rahmen aussagekräftig, die ein Client erzeugt
    hat (Anfragen, weitergeleitete SETs); Antworten spiegeln das chk der Anfrage.
    """
    if len(data) < HEADER_LEN or data[:2] != MAGIC:
        return None
    length, pages, page = struct.unpack_from("<HHH", data, 2)
    if len(data) < HEADER_LEN + length:
        return None
    payload = data[HEADER_LEN : HEADER_LEN + length]
    return Frame(cmd=data[8], chk=data[9], pages=pages, page=page, payload=payload,
                 checksum_ok=checksum(data) == data[9])


def decode_set(payload: bytes) -> dict | None:
    """Übersetzt einen SET-Payload in eine lesbare Beschreibung.

    Liefert u. a. ``kind`` (peq/compressor/hpf/lpf/delay/matrix/unknown),
    ``side`` (input/output), ``channel`` (1-basiert), ``param`` und ``value``.
    """
    if len(payload) < 8:
        return None
    module, param, w1, w2 = struct.unpack_from("<HHhh", payload)
    raw = {"module": module, "param_id": param, "w1": w1, "w2": w2}

    if 0x21 <= module <= 0x30:
        names = {COMP_THRESHOLD: "threshold", COMP_RATIO: "ratio",
                 COMP_ATTACK: "attack", COMP_RELEASE: "release"}
        scale = {COMP_THRESHOLD: 100, COMP_RATIO: 100}.get(param, 1)
        return {"kind": "compressor", "side": "input", "channel": module - 0x20,
                "param": names.get(param, f"p{param}"), "value": w1 / scale, **raw}

    if 0x61 <= module <= 0x70 or 0xC7 <= module <= 0xD6:
        side = "input" if module <= 0x70 else "output"
        channel = module - (0x60 if side == "input" else 0xC6)
        if param == PEQ_BYPASS:
            return {"kind": "peq", "side": side, "channel": channel,
                    "param": "bypass", "value": bool(w1), **raw}
        names = {PEQ_TYPE: "type", PEQ_FREQ: "frequency", PEQ_GAIN: "gain", PEQ_Q: "q"}
        scale = {PEQ_GAIN: 100, PEQ_Q: 100}.get(param, 1)
        return {"kind": "peq", "side": side, "channel": channel, "band": w1 + 1,
                "param": names.get(param, f"p{param}"), "value": w2 / scale, **raw}

    if 0xA7 <= module <= 0xB6:
        channel = module - 0xA6
        names = {XOVER_HPF_SLOPE: ("hpf", "slope"), XOVER_HPF_FREQ: ("hpf", "frequency"),
                 XOVER_LPF_SLOPE: ("lpf", "slope"), XOVER_LPF_FREQ: ("lpf", "frequency")}
        kind, pname = names.get(param, ("xover", f"p{param}"))
        return {"kind": kind, "side": "output", "channel": channel,
                "param": pname, "value": w1, **raw}

    if 0xE7 <= module <= 0xF6:
        return {"kind": "delay", "side": "output", "channel": module - 0xE6,
                "param": "delay" if param == DELAY_MS else f"p{param}", "value": w1, **raw}

    if module == MOD_MATRIX and param == MATRIX_POINT:
        return {"kind": "matrix", "input": (w1 & 0xFF) + 1, "output": ((w1 >> 8) & 0xFF) + 1,
                "param": "on", "value": bool(w2), **raw}

    return {"kind": "unknown", **raw}


# --- Speicherabbild: Eingangs-EQ ---------------------------------------------
# Am Gerät verifiziert (Integra 8S, FW 30.2.3.3): Der Eingangs-PEQ steht im
# Abbild als Live-Zustand. Je Eingang ein Block von 88 Byte:
#   +0  u16  Bypass (1 = gebrückt)
#   +2  u16  (unbekannt)
#   +4  8 × Band à 10 Byte: u16 ?, u16 Typ, u16 Frequenz Hz, i16 Gain ×0,01 dB, u16 Q ×0,01
# Der Kompressor steht NICHT live im Abbild (nur der Stand des zuletzt geladenen
# Presets) und wird deshalb nicht ausgelesen.
IMG_IN_EQ_BASE = 4882
IMG_IN_EQ_STRIDE = 88
IMG_EQ_BAND_STRIDE = 10
IMG_EQ_BANDS = 8


def parse_input_eq(image: bytes, inputs: int) -> dict[tuple, float | bool]:
    """Liest Bypass und Bänder aller Eingangs-EQs aus dem Speicherabbild.

    Schlüssel: (Kanal, Band, Parameter) – Band 0 für den Bypass des Kanals.
    Unplausible Werte werden ausgelassen.
    """
    result: dict[tuple, float | bool] = {}
    for ch in range(1, inputs + 1):
        base = IMG_IN_EQ_BASE + IMG_IN_EQ_STRIDE * (ch - 1)
        end = base + 4 + IMG_EQ_BAND_STRIDE * IMG_EQ_BANDS
        if len(image) < end:
            break
        bypass = struct.unpack_from("<H", image, base)[0]
        if bypass in (0, 1):
            result[(ch, 0, "bypass")] = bool(bypass)
        for band in range(1, IMG_EQ_BANDS + 1):
            _a, _typ, freq, gain, q = struct.unpack_from(
                "<HHHhH", image, base + 4 + IMG_EQ_BAND_STRIDE * (band - 1))
            if 20 <= freq <= 20000:
                result[(ch, band, "frequency")] = freq
            if -3000 <= gain <= 3000:
                result[(ch, band, "gain")] = gain / 100
            if 1 <= q <= 10000:
                result[(ch, band, "q")] = q / 100
    return result


# Kompressor je Eingang: Block à 88 Byte ab 0x0812 (am Gerät verifiziert):
#   +0 u16 ?, +2 u16 ?, +4 u16 ?, +6 i16 Threshold ×0,01 dB, +8 u16 Ratio ×0,01,
#   +10 u16 Attack ms, +12 u16 Release ms
# Gibt den Stand des zuletzt GELADENEN Presets wieder (nicht live) -> nur
# direkt nach einem Preset-Abruf auswerten.
IMG_IN_COMP_BASE = 0x0812
IMG_IN_COMP_STRIDE = 88


def parse_input_comp(image: bytes, inputs: int) -> dict[tuple, float]:
    """Kompressorwerte aller Eingänge aus dem Abbild (Schlüssel: (Kanal, Parameter))."""
    result: dict[tuple, float] = {}
    for ch in range(1, inputs + 1):
        base = IMG_IN_COMP_BASE + IMG_IN_COMP_STRIDE * (ch - 1)
        if len(image) < base + 14:
            break
        thr, ratio, attack, release = struct.unpack_from("<hHHH", image, base + 6)
        if -9000 <= thr <= 2000:
            result[(ch, "threshold")] = thr / 100
        if 100 <= ratio <= 10000:
            result[(ch, "ratio")] = ratio / 100
        if 1 <= attack <= 30000:
            result[(ch, "attack")] = attack
        if 1 <= release <= 30000:
            result[(ch, "release")] = release
    return result


def eq_change_event(key: tuple, value) -> dict:
    """Baut aus einem Abbild-Wert eine Meldung im Format von decode_set."""
    ch, band, param = key
    event = {"kind": "peq", "side": "input", "channel": ch, "param": param,
             "value": value, "source": "image"}
    if band:
        event["band"] = band
    return event


class _DspProtocol(asyncio.DatagramProtocol):
    def __init__(self, client: IntegraDspClient) -> None:
        self._client = client

    def datagram_received(self, data: bytes, addr) -> None:
        self._client._handle_datagram(data)

    def error_received(self, exc: Exception) -> None:  # pragma: no cover
        _LOGGER.debug("DSP UDP error_received: %s", exc)


class IntegraDspClient:
    """Client für das DSP-Protokoll.

    * SET-Befehle werden sofort gesendet (fire-and-forget, wie DSP 1.5).
    * Beim Start (und automatisch nach Sitzungsverlust) meldet sich der Client
      an, indem er Geräteinfo und das komplette Speicherabbild liest.
    * Ein Keepalive alle 2 s hält die Sitzung offen; darüber meldet die Integra
      Änderungen anderer Clients (Push) an diesen Client.
    * Anfragen mit Antwort (Info, Preset, Speicherabbild) werden serialisiert
      und bei Paketverlust wiederholt.
    """

    def __init__(self, host: str, port: int = DEFAULT_DSP_PORT) -> None:
        self.host = host
        self.port = port
        self._transport: asyncio.DatagramTransport | None = None
        self._lock = asyncio.Lock()
        self._pending: tuple[int, int, asyncio.Future[Frame]] | None = None
        self._keepalive_task: asyncio.Task | None = None
        self._last_seen = 0.0          # letztes gültiges Paket überhaupt
        self._last_session = 0.0       # letzte Keepalive-Antwort / letzter Push / Anmeldung
        self._last_login_try = 0.0
        self._login_lock = asyncio.Lock()
        self.info: dict | None = None
        self.image: bytes | None = None
        self.input_count = 16           # wird von der Integration gesetzt
        self.eq_state: dict[tuple, float | bool] = {}
        self._push_listeners: list[Callable[[dict], None]] = []
        self._availability_listeners: list[Callable[[bool], None]] = []
        self._available = False

    # --- Verbindung ---------------------------------------------------

    async def connect(self, keepalive: bool = True) -> None:
        """Öffnet den UDP-Endpunkt und startet optional das Keepalive."""
        loop = asyncio.get_running_loop()
        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: _DspProtocol(self), remote_addr=(self.host, self.port)
        )
        if keepalive:
            self._keepalive_task = loop.create_task(self._keepalive_loop())

    def close(self) -> None:
        if self._keepalive_task is not None:
            self._keepalive_task.cancel()
            self._keepalive_task = None
        if self._transport is not None:
            self._transport.close()
            self._transport = None
        self._set_available(False)

    @property
    def available(self) -> bool:
        return self._available

    def add_push_listener(self, cb: Callable[[dict], None]) -> Callable[[], None]:
        """Registriert einen Callback für weitergeleitete SETs. Gibt Abmelder zurück."""
        self._push_listeners.append(cb)
        return lambda: self._push_listeners.remove(cb)

    def add_availability_listener(self, cb: Callable[[bool], None]) -> Callable[[], None]:
        self._availability_listeners.append(cb)
        return lambda: self._availability_listeners.remove(cb)

    def _set_available(self, value: bool) -> None:
        if value == self._available:
            return
        self._available = value
        for cb in list(self._availability_listeners):
            try:
                cb(value)
            except Exception:  # pragma: no cover - Listener-Fehler isolieren
                _LOGGER.exception("Fehler im Verfügbarkeits-Listener")

    async def login(self) -> None:
        """Öffnet eine Sitzung: Geräteinfo + komplettes Speicherabbild + Status."""
        async with self._login_lock:
            self._last_login_try = time.monotonic()
            self.info = await self.get_info()
            self.image = await self.read_image()
            try:
                await self._request(build_frame(CMD_STATUS, bytes(16)), CMD_STATUS + 1, retries=1)
            except IntegraDspError:
                _LOGGER.debug("Keine Statusantwort nach Anmeldung (unkritisch)")
            self._last_session = time.monotonic()
            _LOGGER.debug("DSP-Sitzung mit %s geöffnet (%s, FW %s)", self.host,
                          self.info.get("name"), self.info.get("firmware"))
            self._publish_image_state()

    async def refresh_state(self, include_compressor: bool = False) -> None:
        """Liest das Abbild neu und meldet Änderungen.

        include_compressor: nur direkt nach einem Preset-Abruf sinnvoll – dann
        entspricht der Kompressorbereich des Abbilds dem geladenen Preset.
        """
        self.image = await self.read_image()
        self._publish_image_state()
        if include_compressor:
            for (ch, param), value in parse_input_comp(self.image, self.input_count).items():
                self._notify_push({"kind": "compressor", "side": "input", "channel": ch,
                                   "param": param, "value": value, "source": "preset"})

    def _publish_image_state(self) -> None:
        """Meldet EQ-Werte aus dem Abbild, die sich gegenüber dem letzten Stand geändert haben."""
        if not self.image:
            return
        new = parse_input_eq(self.image, self.input_count)
        for key, value in new.items():
            if self.eq_state.get(key) != value:
                self._notify_push(eq_change_event(key, value))
        self.eq_state = new

    def _session_alive(self) -> bool:
        return time.monotonic() - self._last_session <= _OFFLINE_AFTER

    async def _keepalive_loop(self) -> None:
        keepalive = build_frame(CMD_KEEPALIVE, bytes(16))
        while True:
            if not self._session_alive():
                self._set_available(False)
                if time.monotonic() - self._last_login_try >= _LOGIN_RETRY:
                    try:
                        await self.login()
                    except (IntegraDspError, OSError) as err:
                        _LOGGER.debug("DSP-Anmeldung an %s fehlgeschlagen: %s", self.host, err)
            try:
                self._send(keepalive)
            except IntegraDspError:
                pass
            await asyncio.sleep(_KEEPALIVE_INTERVAL)

    # --- Empfang ------------------------------------------------------

    def _handle_datagram(self, data: bytes) -> None:
        frame = parse_frame(data)
        if frame is None:
            _LOGGER.debug("Ungültiges DSP-Paket verworfen: %s", data[:32].hex(" "))
            return
        self._last_seen = time.monotonic()

        if frame.cmd == CMD_KEEPALIVE:
            self._last_session = self._last_seen
            self._set_available(True)
            return

        if frame.cmd == CMD_SET:
            if not frame.checksum_ok:
                _LOGGER.debug("Weitergeleitetes SET mit falscher Prüfsumme verworfen")
                return
            decoded = decode_set(frame.payload)
            self._last_session = self._last_seen
            self._set_available(True)
            if decoded is not None:
                self._track_eq(decoded)
                self._notify_push(decoded)
            return

        pending = self._pending
        if pending is not None:
            want, chk, fut = pending
            if frame.cmd == want and frame.chk == chk and not fut.done():
                fut.set_result(frame)

    # --- Senden -------------------------------------------------------

    def _send(self, data: bytes) -> None:
        if self._transport is None:
            raise IntegraDspError("DSP-Verbindung nicht geöffnet")
        self._transport.sendto(data)

    async def _request(self, data: bytes, want: int, retries: int = _RETRIES) -> Frame:
        if self._transport is None:
            raise IntegraDspError("DSP-Verbindung nicht geöffnet")
        async with self._lock:
            loop = asyncio.get_running_loop()
            for attempt in range(1, retries + 1):
                fut: asyncio.Future[Frame] = loop.create_future()
                self._pending = (want, data[9], fut)
                self._send(data)
                try:
                    return await asyncio.wait_for(fut, timeout=_REPLY_TIMEOUT)
                except asyncio.TimeoutError:
                    _LOGGER.debug("DSP-Timeout (Versuch %d/%d), erwartet 0x%02x",
                                  attempt, retries, want)
                finally:
                    self._pending = None
        raise IntegraDspError(f"Keine Antwort 0x{want:02x} von {self.host}")

    def send_set(self, module: int, param: int, w1: int, w2: int = 0) -> None:
        """Sendet einen rohen SET-Befehl."""
        self._send(build_set(module, param, int(w1), int(w2)))
        decoded = decode_set(struct.pack("<HHhh", module, param, int(w1), int(w2)))
        if decoded is not None:
            self._track_eq(decoded)

    # --- Abfragen -----------------------------------------------------

    async def get_info(self) -> dict:
        """Gerätename, IP, MAC und Firmware."""
        frame = await self._request(build_frame(CMD_INFO, bytes(16), flags=0x02), CMD_INFO + 1)
        p = frame.payload
        if len(p) < 56:
            raise IntegraDspError("Geräteinfo zu kurz")
        return {
            "name": p[4:20].split(b"\x00")[0].decode(errors="replace"),
            "ip": ".".join(str(b) for b in p[20:24]),
            "netmask": ".".join(str(b) for b in p[24:28]),
            "mac": ":".join(f"{b:02x}" for b in p[32:38]),
            "firmware": p[40:56].split(b"\x00")[0].decode(errors="replace"),
        }

    async def read_image(self) -> bytes:
        """Liest das komplette Parameter-Speicherabbild (ca. 15 kB)."""
        first = await self._request(build_frame(CMD_READ_PAGE, bytes(16)), CMD_READ_PAGE + 1)
        pages = first.pages or 1
        image = bytearray(first.payload)
        for page in range(1, pages):
            frame = await self._request(
                build_frame(CMD_READ_PAGE, bytes(16), pages=pages, page=page),
                CMD_READ_PAGE + 1,
            )
            image += frame.payload
        return bytes(image)

    async def recall_preset(self, preset: int) -> None:
        """Ruft ein Preset ab (1-basiert, wie in der Software angezeigt)."""
        if preset < 1:
            raise ValueError("Preset beginnt bei 1")
        await self._request(build_frame(CMD_RECALL, struct.pack("<I", preset - 1)), CMD_RECALL + 1)
        await asyncio.sleep(0.5)  # Gerät lädt das Preset
        try:
            await self.refresh_state(include_compressor=True)
        except IntegraDspError as err:
            _LOGGER.debug("Abbild nach Preset-Abruf nicht lesbar: %s", err)

    # --- PEQ ----------------------------------------------------------

    def _peq_module(self, side: str, channel: int) -> int:
        _check_channel(channel)
        if side == "input":
            return mod_input_peq(channel)
        if side == "output":
            return mod_output_peq(channel)
        raise ValueError("side muss 'input' oder 'output' sein")

    def set_peq_bypass(self, side: str, channel: int, bypass: bool) -> None:
        """Bypass an (1) = EQ gebrückt, aus (0) = EQ aktiv."""
        self.send_set(self._peq_module(side, channel), PEQ_BYPASS, 1 if bypass else 0)

    def set_peq_frequency(self, side: str, channel: int, band: int, hz: float) -> None:
        _check_band(band)
        self.send_set(self._peq_module(side, channel), PEQ_FREQ, band - 1,
                      _clamp(round(hz), 20, 20000))

    def set_peq_gain(self, side: str, channel: int, band: int, db: float) -> None:
        _check_band(band)
        self.send_set(self._peq_module(side, channel), PEQ_GAIN, band - 1,
                      round(_clamp(db, -24.0, 24.0) * 100))

    def set_peq_q(self, side: str, channel: int, band: int, q: float) -> None:
        _check_band(band)
        self.send_set(self._peq_module(side, channel), PEQ_Q, band - 1,
                      round(_clamp(q, 0.1, 100.0) * 100))

    # --- Kompressor (Eingänge) ------------------------------------------

    def set_comp_threshold(self, channel: int, db: float) -> None:
        _check_channel(channel)
        self.send_set(mod_input_comp(channel), COMP_THRESHOLD, round(_clamp(db, -90.0, 20.0) * 100))

    def set_comp_ratio(self, channel: int, ratio: float) -> None:
        _check_channel(channel)
        self.send_set(mod_input_comp(channel), COMP_RATIO, round(_clamp(ratio, 1.0, 100.0) * 100))

    def set_comp_attack(self, channel: int, ms: float) -> None:
        _check_channel(channel)
        self.send_set(mod_input_comp(channel), COMP_ATTACK, _clamp(round(ms), 1, 30000))

    def set_comp_release(self, channel: int, ms: float) -> None:
        _check_channel(channel)
        self.send_set(mod_input_comp(channel), COMP_RELEASE, _clamp(round(ms), 1, 30000))

    # --- Ausgänge: Filter, Delay ----------------------------------------

    def set_hpf(self, channel: int, hz: float | None = None, slope: int | None = None) -> None:
        _check_channel(channel)
        if hz is not None:
            self.send_set(mod_output_xover(channel), XOVER_HPF_FREQ, _clamp(round(hz), 20, 20000))
        if slope is not None:
            self.send_set(mod_output_xover(channel), XOVER_HPF_SLOPE, int(slope))

    def set_lpf(self, channel: int, hz: float | None = None, slope: int | None = None) -> None:
        _check_channel(channel)
        if hz is not None:
            self.send_set(mod_output_xover(channel), XOVER_LPF_FREQ, _clamp(round(hz), 20, 20000))
        if slope is not None:
            self.send_set(mod_output_xover(channel), XOVER_LPF_SLOPE, int(slope))

    def set_delay(self, channel: int, ms: float) -> None:
        """Ausgangs-Delay in ms (Minimum 1, wie in DSP 1.5)."""
        _check_channel(channel)
        self.send_set(mod_output_delay(channel), DELAY_MS, _clamp(round(ms), 1, 30000))

    # --- Matrix -------------------------------------------------------

    def set_matrix(self, input_ch: int, output_ch: int, on: bool) -> None:
        _check_channel(input_ch)
        _check_channel(output_ch)
        point = ((output_ch - 1) << 8) | (input_ch - 1)
        self.send_set(MOD_MATRIX, MATRIX_POINT, point, 1 if on else 0)
        # Das Gerät schickt eigene Änderungen nicht zurück. Damit z. B. Matrix-
        # Schalter auch bei Aufruf über den Dienst stimmen, lokal melden.
        decoded = decode_set(struct.pack("<HHhh", MOD_MATRIX, MATRIX_POINT, point, 1 if on else 0))
        if decoded is not None:
            self._notify_push({**decoded, "local": True})

    def _track_eq(self, d: dict) -> None:
        if (d.get("kind") == "peq" and d.get("side") == "input"
                and d["param"] in ("frequency", "gain", "q", "bypass")):
            self.eq_state[(d["channel"], d.get("band", 0), d["param"])] = d["value"]

    def _notify_push(self, decoded: dict) -> None:
        for cb in list(self._push_listeners):
            try:
                cb(decoded)
            except Exception:  # pragma: no cover
                _LOGGER.exception("Fehler im Push-Listener")


def _clamp(value, low, high):
    return max(low, min(high, value))


def _check_channel(channel: int) -> None:
    if not 1 <= channel <= 16:
        raise ValueError(f"Kanal {channel} außerhalb 1..16")


def _check_band(band: int) -> None:
    if not 1 <= band <= 31:
        raise ValueError(f"Band {band} außerhalb 1..31")
