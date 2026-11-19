"""HA-Tests für die DSP-Funktionen gegen ein simuliertes Gerät (UDP auf localhost).

Benötigt: pip install pytest-homeassistant-custom-component
"""
import asyncio
import pathlib
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events  # noqa: E402

from custom_components.work_integra import integra_bin as ib  # noqa: E402
from custom_components.work_integra.const import DOMAIN, EVENT_DSP_CHANGED  # noqa: E402

from .test_integra_bin import _start_fake  # noqa: E402


@pytest.fixture
async def fake_device():
    t, fake, port = await _start_fake()
    yield fake, port
    t.close()


def _ascii_mocks():
    """ASCII-Client (Gain/Mute) wird gemockt – getestet wird hier nur der DSP-Teil."""
    vals = {i: 0.0 for i in range(2)}
    mutes = {i: False for i in range(2)}
    return {
        "connect": AsyncMock(),
        "get_output_gains": AsyncMock(return_value=vals),
        "get_input_gains": AsyncMock(return_value=vals),
        "get_output_mutes": AsyncMock(return_value=mutes),
        "get_input_mutes": AsyncMock(return_value=mutes),
        "get_input_names": AsyncMock(return_value={0: "Funk Hand", 1: "Funk Headset"}),
        "get_output_names": AsyncMock(return_value={0: "LS Vorne", 1: "LS Mitte"}),
    }


async def _setup(hass, port):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Integra (127.0.0.1)",
        data={"host": "127.0.0.1", "port": 50000, "dsp_port": port, "outputs": 2, "inputs": 2},
    )
    entry.add_to_hass(hass)
    mocks = _ascii_mocks()
    with patch.multiple("custom_components.work_integra.integra.IntegraClient", **mocks):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await asyncio.sleep(0.6)  # Anmeldung + erstes Keepalive
        await hass.async_block_till_done()
    return entry, mocks


def _sets(fake):
    return [f.payload for f in fake.received if f.cmd == ib.CMD_SET]


async def test_setup_and_connectivity(hass, fake_device):
    fake, port = fake_device
    entry, _ = await _setup(hass, port)
    assert hass.states.get("binary_sensor.integra_127_0_0_1_dsp_verbindung").state == "on"
    sel = hass.states.get("select.integra_127_0_0_1_preset")
    assert sel is not None and sel.attributes["options"][0] == "Preset 1"
    # bestehende ASCII-Entities unverändert vorhanden
    assert hass.states.get("number.integra_127_0_0_1_ls_vorne_lautstarke") is not None
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_services_send_expected_frames(hass, fake_device):
    fake, port = fake_device
    entry, _ = await _setup(hass, port)

    await hass.services.async_call(DOMAIN, "set_peq", {
        "side": "output", "channel": 2, "band": 1, "gain": 3, "frequency": 100, "q": 0.7}, blocking=True)
    await hass.services.async_call(DOMAIN, "set_compressor", {
        "channel": 1, "threshold": -20, "ratio": 2}, blocking=True)
    await hass.services.async_call(DOMAIN, "set_filter", {
        "channel": 5, "filter": "lpf", "frequency": 120}, blocking=True)
    await hass.services.async_call(DOMAIN, "set_filter", {
        "channel": 1, "filter": "hpf", "frequency": 80, "slope": "18"}, blocking=True)
    await hass.services.async_call(DOMAIN, "set_delay", {"channel": 1, "delay": 12}, blocking=True)
    await hass.services.async_call(DOMAIN, "set_matrix", {"input": 1, "output": 2, "enabled": True}, blocking=True)
    await asyncio.sleep(0.1)

    H = bytes.fromhex
    assert _sets(fake) == [
        H("c8000300000064 00".replace(" ", "")),   # OUT2 Band1 100 Hz
        H("c80004000000 2c01".replace(" ", "")),   # +3 dB
        H("c80005000000 4600".replace(" ", "")),   # Q 0.7
        H("21000200 30f8 0000".replace(" ", "")),  # IN1 Threshold -20
        H("21000300 c800 0000".replace(" ", "")),  # Ratio 2
        H("ab001400 7800 0000".replace(" ", "")),  # OUT5 LPF 120 Hz
        H("a7000400 5000 0000".replace(" ", "")),  # OUT1 HPF 80 Hz
        H("a7000300 1200 0000".replace(" ", "")),  # Flanke 18
        H("e7000200 0c00 0000".replace(" ", "")),  # Delay 12 ms
        H("a6000100 0001 0100".replace(" ", "")),  # IN1 -> OUT2 an
    ]
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_preset_select_recalls_and_refreshes(hass, fake_device):
    fake, port = fake_device
    entry, mocks = await _setup(hass, port)
    calls_before = mocks["get_output_gains"].await_count
    with patch.multiple("custom_components.work_integra.integra.IntegraClient", **mocks), \
         patch("custom_components.work_integra.services.asyncio.sleep", AsyncMock()):
        await hass.services.async_call("select", "select_option", {
            "entity_id": "select.integra_127_0_0_1_preset", "option": "Preset 3"}, blocking=True)
        await hass.async_block_till_done()
    recalls = [f for f in fake.received if f.cmd == ib.CMD_RECALL]
    assert recalls and recalls[-1].payload == bytes.fromhex("02000000")
    assert hass.states.get("select.integra_127_0_0_1_preset").state == "Preset 3"
    assert mocks["get_output_gains"].await_count > calls_before
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_push_from_other_client_fires_event(hass, fake_device):
    fake, port = fake_device
    entry, _ = await _setup(hass, port)
    events = async_capture_events(hass, EVENT_DSP_CHANGED)

    other = ib.IntegraDspClient("127.0.0.1", port)   # z. B. WorkPro DSP 1.5
    await other.connect(keepalive=False)
    other.set_peq_gain("input", 1, 2, -4.5)
    await asyncio.sleep(0.2)
    await hass.async_block_till_done()
    other.close()

    assert len(events) == 1
    ev = events[0].data
    assert (ev["kind"], ev["side"], ev["channel"], ev["band"], ev["param"], ev["value"]) == (
        "peq", "input", 1, 2, "gain", -4.5)
    assert ev["config_entry_id"] == entry.entry_id
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_connectivity_goes_off_when_device_silent(hass, fake_device):
    fake, port = fake_device
    entry, _ = await _setup(hass, port)
    coord = hass.data[DOMAIN][entry.entry_id]
    coord.dsp._last_session -= 60       # Funkstille simulieren
    fake.drop_next = 10**6              # Gerät antwortet nicht mehr
    await asyncio.sleep(2.2)            # ein Keepalive-Zyklus
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.integra_127_0_0_1_dsp_verbindung").state == "off"
    assert hass.states.get("select.integra_127_0_0_1_preset").state == "unavailable"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_eq_card_is_served_and_registered(hass):
    """Die EQ-Karte wird als statische Datei bereitgestellt und im Frontend geladen."""
    from unittest.mock import MagicMock

    import custom_components.work_integra as wi

    assert wi.EQ_CARD_FILE.is_file()
    http = MagicMock()
    http.async_register_static_paths = AsyncMock()
    hass.http = http
    hass.config.components.add("frontend")
    with patch("homeassistant.components.frontend.add_extra_js_url") as add_js:
        await wi._async_register_frontend(hass)
    cfgs = [c.args[0][0] for c in http.async_register_static_paths.await_args_list]
    assert {c.url_path for c in cfgs} == {
        "/work_integra/work-integra-eq-card.js", "/work_integra/work-integra-comp-card.js",
        "/work_integra/work-integra-matrix-card.js"}
    assert all(pathlib.Path(c.path).is_file() for c in cfgs)

    urls = [c.args[1] for c in add_js.call_args_list]
    assert any(u.startswith("/work_integra/work-integra-eq-card.js?v=") for u in urls)
    assert any(u.startswith("/work_integra/work-integra-matrix-card.js?v=") for u in urls)


async def test_eq_card_skipped_without_frontend(hass):
    import custom_components.work_integra as wi

    if "frontend" in hass.config.components:
        hass.config.components.remove("frontend")
    with patch("homeassistant.components.frontend.add_extra_js_url") as add_js:
        await wi._async_register_frontend(hass)
    add_js.assert_not_called()


async def test_matrix_switches(hass, fake_device):
    fake, port = fake_device
    entry, _ = await _setup(hass, port)
    eid = "switch.integra_127_0_0_1_matrix_funk_hand_ls_mitte"
    st = hass.states.get(eid)
    assert st is not None and st.attributes["matrix_input"] == 1 and st.attributes["matrix_output"] == 2
    assert st.state == "off"  # Grundbelegung: nur Diagonale an
    assert hass.states.get("switch.integra_127_0_0_1_matrix_funk_hand_ls_vorne").state == "on"

    await hass.services.async_call("switch", "turn_on", {"entity_id": eid}, blocking=True)
    await asyncio.sleep(0.1)
    assert _sets(fake)[-1] == bytes.fromhex("a600010000010100")
    assert hass.states.get(eid).state == "on"

    # Dienst set_matrix aktualisiert den Schalter ebenfalls
    await hass.services.async_call(DOMAIN, "set_matrix", {"input": 1, "output": 2, "enabled": False}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(eid).state == "off"

    # Änderung aus der Work-Software (anderer Client) -> Schalter folgt
    other = ib.IntegraDspClient("127.0.0.1", port)
    await other.connect(keepalive=False)
    other.send_set(ib.MOD_MATRIX, ib.MATRIX_POINT, 0x0100, 1)
    await asyncio.sleep(0.2)
    await hass.async_block_till_done()
    other.close()
    assert hass.states.get(eid).state == "on"

    matrix = [s for s in hass.states.async_all("switch") if "matrix_input" in s.attributes]
    assert len(matrix) == 4  # 2 Eingänge x 2 Ausgänge im Test
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_read_dsp_reports_eq_from_device(hass, fake_device):
    from .test_integra_bin import make_image

    fake, port = fake_device
    fake.image = make_image({(1, 2): (642, 13.0, 6.89), (1, 0): 0})
    entry, _ = await _setup(hass, port)
    events = async_capture_events(hass, EVENT_DSP_CHANGED)
    await hass.services.async_call(DOMAIN, "read_dsp", {}, blocking=True)
    await hass.async_block_till_done()
    got = {(e.data["channel"], e.data.get("band", 0), e.data["param"]): e.data["value"]
           for e in events if e.data.get("source") == "image"}
    assert got[(1, 2, "frequency")] == 642 and got[(1, 2, "gain")] == 13.0 and got[(1, 2, "q")] == 6.89
    assert got[(1, 0, "bypass")] is False
    assert len(got) == 2 * 25          # 2 Eingänge im Test
    assert await hass.config_entries.async_unload(entry.entry_id)
