"""Tests für den DSP-Binärclient – ohne Home Assistant lauffähig.

Die erwarteten Byte-Folgen stammen 1:1 aus Mitschnitten WorkPro DSP 1.5 <-> Integra 8S.
"""
import asyncio
import importlib.util
import pathlib
import struct
import sys

import pytest

_PATH = pathlib.Path(__file__).parents[1] / "custom_components/work_integra/integra_bin.py"
_spec = importlib.util.spec_from_file_location("integra_bin", _PATH)
ib = importlib.util.module_from_spec(_spec)
sys.modules["integra_bin"] = ib
_spec.loader.exec_module(ib)

H = bytes.fromhex


def _run(coro):
    """Eigene Event-Loop ohne asyncio.run(): verändert die globale Loop-Policy
    nicht und verträgt sich so mit dem HA-Testpaket."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# --- Rahmen exakt wie von DSP 1.5 gesendet --------------------------------
CAPTURED = {
    "keepalive": (lambda: ib.build_frame(ib.CMD_KEEPALIVE, bytes(16)),
                  "a55a1000000000005565" + "00" * 22),
    "info": (lambda: ib.build_frame(ib.CMD_INFO, bytes(16), flags=2),
             "a55a1000000000000113000200000000" + "00" * 16),
    "read_page0": (lambda: ib.build_frame(ib.CMD_READ_PAGE, bytes(16)),
                   "a55a1000000000001121" + "00" * 22),
    "read_page1": (lambda: ib.build_frame(ib.CMD_READ_PAGE, bytes(16), pages=15, page=1),
                   "a55a10000f0001001131" + "00" * 22),
    "recall_p1": (lambda: ib.build_frame(ib.CMD_RECALL, struct.pack("<I", 0)),
                  "a55a0400000000001317000000000000" + "00000000"),
    "recall_p2": (lambda: ib.build_frame(ib.CMD_RECALL, struct.pack("<I", 1)),
                  "a55a0400000000001318000000000000" + "01000000"),
    "in2_peq_b1_gain_3": (lambda: ib.build_set(0x62, 4, 0, 300),
                          "a55a080000000000 21bc000000000000 620004000000 2c01"),
    "in1_comp_thr_-30": (lambda: ib.build_set(0x21, 2, -3000),
                         "a55a080000000000 2188000000000000 21000200 48f40000"),
}


@pytest.mark.parametrize("name", CAPTURED)
def test_frames_match_capture(name):
    build, expected = CAPTURED[name]
    assert build().hex() == expected.replace(" ", "")


def _client_frames(fn):
    """Führt eine Client-Methode aus und gibt die gesendeten Rahmen zurück."""
    c = ib.IntegraDspClient("127.0.0.1")
    sent = []

    class T:
        def sendto(self, data):
            sent.append(data)

        def close(self):
            pass

    c._transport = T()
    fn(c)
    return [s[16:] for s in sent]


@pytest.mark.parametrize(
    "fn,payload",
    [
        (lambda c: c.set_peq_gain("input", 1, 1, 6), "6100 0400 0000 5802"),
        (lambda c: c.set_peq_gain("input", 1, 2, 3), "6100 0400 0100 2c01"),
        (lambda c: c.set_peq_frequency("input", 1, 1, 2000), "6100 0300 0000 d007"),
        (lambda c: c.set_peq_q("input", 1, 1, 4.0), "6100 0500 0000 9001"),
        (lambda c: c.set_peq_bypass("input", 1, True), "6100 0100 0100 0000"),
        (lambda c: c.set_peq_gain("output", 1, 1, -3), "c700 0400 0000 d4fe"),
        (lambda c: c.set_peq_gain("output", 2, 1, 3), "c800 0400 0000 2c01"),
        (lambda c: c.set_comp_threshold(1, -20), "2100 0200 30f8 0000"),
        (lambda c: c.set_comp_ratio(1, 1.5), "2100 0300 9600 0000"),
        (lambda c: c.set_comp_attack(1, 15), "2100 0400 0f00 0000"),
        (lambda c: c.set_comp_release(1, 1500), "2100 0500 dc05 0000"),
        (lambda c: c.set_hpf(1, hz=80), "a700 0400 5000 0000"),
        (lambda c: c.set_hpf(1, slope=18), "a700 0300 1200 0000"),
        (lambda c: c.set_lpf(1, hz=300), "a700 1400 2c01 0000"),
        (lambda c: c.set_delay(1, 20), "e700 0200 1400 0000"),
        (lambda c: c.set_delay(1, 0), "e700 0200 0100 0000"),
        (lambda c: c.set_matrix(1, 2, True), "a600 0100 0001 0100"),
    ],
)
def test_set_payloads_match_capture(fn, payload):
    assert _client_frames(fn) == [H(payload.replace(" ", ""))]


def test_clamping():
    assert _client_frames(lambda c: c.set_peq_gain("input", 1, 1, 99)) == [H("610004000000 6009")]
    with pytest.raises(ValueError):
        _client_frames(lambda c: c.set_peq_gain("input", 0, 1, 0))


def test_decode_set():
    d = ib.decode_set(H("620004000000 2c01".replace(" ", "")))
    assert (d["kind"], d["side"], d["channel"], d["band"], d["param"], d["value"]) == (
        "peq", "input", 2, 1, "gain", 3.0)
    d = ib.decode_set(H("c8000400 0000d4fe".replace(" ", "")))
    assert (d["side"], d["channel"], d["value"]) == ("output", 2, -3.0)
    d = ib.decode_set(H("21000200 48f40000".replace(" ", "")))
    assert (d["kind"], d["param"], d["value"]) == ("compressor", "threshold", -30.0)
    d = ib.decode_set(H("a6000100 00010100".replace(" ", "")))
    assert (d["kind"], d["input"], d["output"], d["value"]) == ("matrix", 1, 2, True)
    d = ib.decode_set(H("e7000200 14000000".replace(" ", "")))
    assert (d["kind"], d["channel"], d["value"]) == ("delay", 1, 20)


def test_parse_checksum_flag():
    good = ib.build_set(0x61, 4, 0, 0)
    bad = bytearray(good)
    bad[9] ^= 1
    assert ib.parse_frame(good).checksum_ok
    assert not ib.parse_frame(bytes(bad)).checksum_ok
    assert ib.parse_frame(b"\x00" * 20) is None


def test_real_replies_echo_request_checksum():
    """Aus dem Mitschnitt: Antwort auf Preset-Abruf 2 (Anfrage-chk 0x18)."""
    reply = ib.parse_frame(H("a55a0400000000001418000000000000" "01000000"))
    assert reply.cmd == 0x14 and reply.chk == 0x18
    req = ib.build_frame(ib.CMD_RECALL, struct.pack("<I", 1))
    assert req[9] == reply.chk


# --- Simuliertes Gerät -------------------------------------------------------
class FakeIntegra(asyncio.DatagramProtocol):
    """Antwortet wie die echte Integra (aus den Mitschnitten)."""

    def __init__(self):
        self.received = []
        self.clients = set()      # alle, die etwas gesendet haben
        self.sessions = set()     # angemeldet (komplettes Abbild gelesen)
        self.drop_next = 0

    def connection_made(self, transport):
        self.t = transport

    def datagram_received(self, data, addr):
        f = ib.parse_frame(data)
        assert f is not None, "Client hat ungültigen Rahmen gesendet"
        self.received.append(f)
        self.clients.add(addr)
        if self.drop_next:
            self.drop_next -= 1
            return
        if f.cmd == ib.CMD_SET:
            for c in self.sessions - {addr}:  # nur an angemeldete Clients weiterleiten
                self.t.sendto(data, c)
        elif f.cmd == ib.CMD_KEEPALIVE:
            if addr in self.sessions:          # echte Integra: nur mit Sitzung
                self.reply(ib.build_frame(ib.CMD_KEEPALIVE), f, addr)
        elif f.cmd == ib.CMD_METER and f.payload[:1] == b"\x00":
            self.sessions.discard(addr)        # "Meter aus" beendet die Sitzung
        elif f.cmd == ib.CMD_STATUS:
            if addr in self.sessions:
                self.reply(ib.build_frame(ib.CMD_STATUS + 1, bytes(152)), f, addr)
        elif f.cmd == ib.CMD_RECALL:
            self.reply(ib.build_frame(ib.CMD_RECALL + 1, f.payload), f, addr)
        elif f.cmd == ib.CMD_INFO:
            p = bytearray(360)
            p[0:4] = H("c3a50800")
            p[4:14] = b"INTEGRA 8S"
            p[20:24] = bytes([192, 168, 178, 249])
            p[24:28] = bytes([255, 255, 255, 0])
            p[32:38] = H("020007 47d27e")
            p[40:48] = b"30.2.3.3"
            self.reply(ib.build_frame(ib.CMD_INFO + 1, bytes(p), flags=2), f, addr)
        elif f.cmd == ib.CMD_READ_PAGE:
            page = f.page
            body = bytes([page]) * (1008 if page < 14 else 842)
            if page == 14:                     # Abschluss wie am Gerät: erst 0xA3, dann Seite
                self.reply(ib.build_frame(0xA3, bytes(16), pages=15, page=14), f, addr)
                self.sessions.add(addr)
            self.reply(ib.build_frame(ib.CMD_READ_PAGE + 1, body, pages=15, page=page), f, addr)

    def reply(self, data, req, addr):
        """Wie die echte Integra: Antwort spiegelt das chk der Anfrage."""
        data = bytearray(data)
        data[9] = req.chk
        self.t.sendto(bytes(data), addr)


async def _start_fake():
    loop = asyncio.get_running_loop()
    t, fake = await loop.create_datagram_endpoint(FakeIntegra, local_addr=("127.0.0.1", 0))
    return t, fake, t.get_extra_info("sockname")[1]


def test_fake_device_roundtrip():
    async def run():
        t, fake, port = await _start_fake()
        ha = ib.IntegraDspClient("127.0.0.1", port)
        dsp15 = ib.IntegraDspClient("127.0.0.1", port)
        pushes = []
        avail = []
        ha.add_push_listener(pushes.append)
        ha.add_availability_listener(avail.append)
        await ha.connect()
        await dsp15.connect(keepalive=False)
        await asyncio.sleep(0.5)                      # Anmeldung + erstes Keepalive
        assert ha.available and avail == [True]
        assert ha.info["name"] == "INTEGRA 8S" and len(ha.image) == 14954

        info = await ha.get_info()
        assert info == {"name": "INTEGRA 8S", "ip": "192.168.178.249", "netmask": "255.255.255.0",
                        "mac": "02:00:07:47:d2:7e", "firmware": "30.2.3.3"}

        fake.drop_next = 1            # erste Anfrage geht verloren -> Wiederholung
        await ha.recall_preset(2)
        assert fake.received[-1].payload == H("01000000")

        img = await ha.read_image()
        assert len(img) == 14 * 1008 + 842 == 14954

        dsp15.set_peq_gain("input", 1, 1, 4)   # anderer Client ändert etwas
        await asyncio.sleep(0.2)
        assert pushes and pushes[-1]["param"] == "gain" and pushes[-1]["value"] == 4.0

        ha.close()
        dsp15.close()
        t.close()
        assert ha.available is False

    _run(run())


def test_no_session_without_full_image_read():
    """Wie am Gerät verifiziert: Info allein öffnet keine Sitzung."""
    async def run():
        t, fake, port = await _start_fake()
        c = ib.IntegraDspClient("127.0.0.1", port)
        await c.connect(keepalive=False)
        await c.get_info()
        c._send(ib.build_frame(ib.CMD_KEEPALIVE))
        await asyncio.sleep(0.2)
        assert not c.available
        await c.login()
        c._send(ib.build_frame(ib.CMD_KEEPALIVE))
        await asyncio.sleep(0.2)
        assert c.available
        c.close()
        t.close()

    _run(run())


def test_relogin_after_session_loss():
    async def run():
        t, fake, port = await _start_fake()
        c = ib.IntegraDspClient("127.0.0.1", port)
        await c.connect()
        await asyncio.sleep(0.5)
        assert c.available
        fake.sessions.clear()                 # Gerät verliert die Sitzung (z. B. Neustart)
        c._last_session -= 60
        c._last_login_try -= 60
        await asyncio.sleep(2.5)              # ein Keepalive-Zyklus -> Neuanmeldung
        assert fake.sessions and c.available
        c.close()
        t.close()

    _run(run())
