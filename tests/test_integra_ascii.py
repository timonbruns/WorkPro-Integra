"""ASCII-Parser: Integra 8S liefert teils leere Werte (z. B. abschließendes '#')."""
import importlib.util
import pathlib
import sys

import pytest

_PATH = pathlib.Path(__file__).parents[1] / "custom_components/work_integra/integra.py"
_spec = importlib.util.spec_from_file_location("integra_ascii", _PATH)
ia = importlib.util.module_from_spec(_spec)
sys.modules["integra_ascii"] = ia
_spec.loader.exec_module(ia)


def test_empty_values_are_skipped():
    s, _e, values = ia.IntegraClient._parse_values("output#gain#0-7#0.0#-1.5#0#0#0#0#0#0#")
    result = ia.IntegraClient._floats(s, values)
    assert result[1] == -1.5 and len(result) == 8


def test_reply_without_values_raises():
    with pytest.raises(ia.IntegraError):
        ia.IntegraClient._floats(0, ["", ""])


def test_set_is_sent_while_get_is_pending():
    """Set-Befehle warten nicht hinter einer laufenden Abfrage (Echtzeit-Fader)."""
    import asyncio

    async def run():
        sent = []
        c = ia.IntegraClient("127.0.0.1")

        class T:
            def sendto(self, data):
                sent.append(data.decode())

        c._transport = T()
        get_task = asyncio.ensure_future(c._command("get:output#gain#0-7", expect_token="output#gain#0-7", retries=1))
        await asyncio.sleep(0.05)               # get wartet jetzt auf Antwort
        await asyncio.wait_for(c.set_output_gain(0, -6.0), timeout=0.2)
        assert sent == ["get:output#gain#0-7", "set:output#gain#0#-6.0"]
        assert c._pending is not None and not c._pending.done()   # get nicht abgeräumt
        c._handle_datagram("output#gain#0-7#0#0#0#0#0#0#0#0")
        assert (await get_task).startswith("output#gain")

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(run())
    finally:
        loop.close()



def test_matrix_reply_parsing_from_device():
    """Antworten 1:1 von der Integra 8S (23.11.2026)."""
    import asyncio

    async def run(reply, out):
        c = ia.IntegraClient("127.0.0.1")

        class T:
            def sendto(self, data):
                asyncio.get_running_loop().call_soon(c._handle_datagram, reply)

        c._transport = T()
        return await c.get_matrix_output(out, 8)

    loop = asyncio.new_event_loop()
    try:
        r = loop.run_until_complete(run("get:mixer#switch#0-7#0#1#1#1#1#1#1#1#1", 0))
        assert r == {i: True for i in range(8)}
        r = loop.run_until_complete(run("get:mixer#switch#0-7#4#0#0#0#0#1#0#0#0", 4))
        assert r == {i: i == 4 for i in range(8)}
    finally:
        loop.close()


def test_level_reply_parsing_from_device():
    """Antworten 1:1 von der Integra 8S (23.11.2026, Internetradio auf IN8)."""
    s, _e, values = ia.IntegraClient._parse_values("get:input#level#0-7#-104.8#-105.0#-104.3#-104.7#-104.7#-104.0#-105.8#-35.2")
    lv = ia.IntegraClient._floats(s, values)
    assert lv[7] == -35.2 and lv[0] == -104.8 and len(lv) == 8
    s, _e, values = ia.IntegraClient._parse_values("get:input#level#7#-36.6")
    assert ia.IntegraClient._floats(s, values) == {7: -36.6}
