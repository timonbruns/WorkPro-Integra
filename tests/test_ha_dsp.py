"""HA-Tests für die DSP-Funktionen gegen ein simuliertes Gerät (UDP auf localhost).

Benötigt: pip install pytest-homeassistant-custom-component
"""
import asyncio
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
