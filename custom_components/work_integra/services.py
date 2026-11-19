"""Dienste für das binäre DSP-Protokoll (EQ, Kompressor, Filter, Delay, Matrix, Presets).

Alle Kanal-, Band- und Presetnummern sind 1-basiert – so wie sie in der
Herstellersoftware angezeigt werden.
"""

from __future__ import annotations

import asyncio
import logging

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, PRESET_COUNT
from .coordinator import IntegraCoordinator
from .integra_bin import IntegraDspClient, IntegraDspError

_LOGGER = logging.getLogger(__name__)

ATTR_DEVICE_ID = "device_id"
ATTR_SIDE = "side"
ATTR_CHANNEL = "channel"
ATTR_BAND = "band"
ATTR_FREQUENCY = "frequency"
ATTR_GAIN = "gain"
ATTR_Q = "q"
ATTR_BYPASS = "bypass"
ATTR_THRESHOLD = "threshold"
ATTR_RATIO = "ratio"
ATTR_ATTACK = "attack"
ATTR_RELEASE = "release"
ATTR_FILTER = "filter"
ATTR_SLOPE = "slope"
ATTR_DELAY = "delay"
ATTR_INPUT = "input"
ATTR_OUTPUT = "output"
ATTR_ENABLED = "enabled"
ATTR_PRESET = "preset"

SERVICE_SET_PEQ = "set_peq"
SERVICE_SET_COMPRESSOR = "set_compressor"
SERVICE_SET_FILTER = "set_filter"
SERVICE_SET_DELAY = "set_delay"
SERVICE_SET_MATRIX = "set_matrix"
SERVICE_RECALL_PRESET = "recall_preset"

_CHANNEL = vol.All(vol.Coerce(int), vol.Range(min=1, max=16))
_DEVICE = vol.Optional(ATTR_DEVICE_ID)

SCHEMA_SET_PEQ = vol.Schema(
    {
        _DEVICE: cv.string,
        vol.Required(ATTR_SIDE): vol.In(["input", "output"]),
        vol.Required(ATTR_CHANNEL): _CHANNEL,
        vol.Optional(ATTR_BAND, default=1): vol.All(vol.Coerce(int), vol.Range(min=1, max=31)),
        vol.Optional(ATTR_FREQUENCY): vol.All(vol.Coerce(float), vol.Range(min=20, max=20000)),
        vol.Optional(ATTR_GAIN): vol.All(vol.Coerce(float), vol.Range(min=-24, max=24)),
        vol.Optional(ATTR_Q): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=100)),
        vol.Optional(ATTR_BYPASS): cv.boolean,
    }
)

SCHEMA_SET_COMPRESSOR = vol.Schema(
    {
        _DEVICE: cv.string,
        vol.Required(ATTR_CHANNEL): _CHANNEL,
        vol.Optional(ATTR_THRESHOLD): vol.All(vol.Coerce(float), vol.Range(min=-90, max=20)),
        vol.Optional(ATTR_RATIO): vol.All(vol.Coerce(float), vol.Range(min=1, max=100)),
        vol.Optional(ATTR_ATTACK): vol.All(vol.Coerce(float), vol.Range(min=1, max=30000)),
        vol.Optional(ATTR_RELEASE): vol.All(vol.Coerce(float), vol.Range(min=1, max=30000)),
    }
)

SCHEMA_SET_FILTER = vol.Schema(
    {
        _DEVICE: cv.string,
        vol.Required(ATTR_CHANNEL): _CHANNEL,
        vol.Required(ATTR_FILTER): vol.In(["hpf", "lpf"]),
        vol.Optional(ATTR_FREQUENCY): vol.All(vol.Coerce(float), vol.Range(min=20, max=20000)),
        vol.Optional(ATTR_SLOPE): vol.All(vol.Coerce(int), vol.In([6, 12, 18, 24, 36, 48])),
    }
)

SCHEMA_SET_DELAY = vol.Schema(
    {
        _DEVICE: cv.string,
        vol.Required(ATTR_CHANNEL): _CHANNEL,
        vol.Required(ATTR_DELAY): vol.All(vol.Coerce(float), vol.Range(min=1, max=30000)),
    }
)

SCHEMA_SET_MATRIX = vol.Schema(
    {
        _DEVICE: cv.string,
        vol.Required(ATTR_INPUT): _CHANNEL,
        vol.Required(ATTR_OUTPUT): _CHANNEL,
        vol.Required(ATTR_ENABLED): cv.boolean,
    }
)

SCHEMA_RECALL_PRESET = vol.Schema(
    {
        _DEVICE: cv.string,
        vol.Required(ATTR_PRESET): vol.All(vol.Coerce(int), vol.Range(min=1, max=PRESET_COUNT)),
    }
)


def _targets(hass: HomeAssistant, call: ServiceCall) -> list[IntegraCoordinator]:
    """Ermittelt die Ziel-Integra(s) aus device_id oder – bei nur einem Gerät – automatisch."""
    loaded: dict[str, IntegraCoordinator] = hass.data.get(DOMAIN, {})
    if not loaded:
        raise ServiceValidationError("Keine Work Pro Integra eingerichtet.")

    device_id = call.data.get(ATTR_DEVICE_ID)
    if device_id is None:
        if len(loaded) > 1:
            raise ServiceValidationError(
                "Mehrere Integra eingerichtet – bitte device_id angeben."
            )
        return list(loaded.values())

    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        raise ServiceValidationError(f"Gerät {device_id} nicht gefunden.")
    targets = [loaded[e] for e in device.config_entries if e in loaded]
    if not targets:
        raise ServiceValidationError(f"Gerät {device_id} ist keine geladene Integra.")
    return targets


def _dsp(coordinator: IntegraCoordinator) -> IntegraDspClient:
    if coordinator.dsp is None:
        raise HomeAssistantError("DSP-Protokoll ist für dieses Gerät nicht aktiv.")
    return coordinator.dsp


def async_setup_services(hass: HomeAssistant) -> None:
    """Registriert alle DSP-Dienste."""

    async def set_peq(call: ServiceCall) -> None:
        d = call.data
        side, ch, band = d[ATTR_SIDE], d[ATTR_CHANNEL], d[ATTR_BAND]
        for coord in _targets(hass, call):
            dsp = _dsp(coord)
            try:
                if ATTR_FREQUENCY in d:
                    dsp.set_peq_frequency(side, ch, band, d[ATTR_FREQUENCY])
                if ATTR_GAIN in d:
                    dsp.set_peq_gain(side, ch, band, d[ATTR_GAIN])
                if ATTR_Q in d:
                    dsp.set_peq_q(side, ch, band, d[ATTR_Q])
                if ATTR_BYPASS in d:
                    dsp.set_peq_bypass(side, ch, d[ATTR_BYPASS])
            except IntegraDspError as err:
                raise HomeAssistantError(str(err)) from err

    async def set_compressor(call: ServiceCall) -> None:
        d = call.data
        ch = d[ATTR_CHANNEL]
        for coord in _targets(hass, call):
            dsp = _dsp(coord)
            try:
                if ATTR_THRESHOLD in d:
                    dsp.set_comp_threshold(ch, d[ATTR_THRESHOLD])
                if ATTR_RATIO in d:
                    dsp.set_comp_ratio(ch, d[ATTR_RATIO])
                if ATTR_ATTACK in d:
                    dsp.set_comp_attack(ch, d[ATTR_ATTACK])
                if ATTR_RELEASE in d:
                    dsp.set_comp_release(ch, d[ATTR_RELEASE])
            except IntegraDspError as err:
                raise HomeAssistantError(str(err)) from err

    async def set_filter(call: ServiceCall) -> None:
        d = call.data
        for coord in _targets(hass, call):
            dsp = _dsp(coord)
            setter = dsp.set_hpf if d[ATTR_FILTER] == "hpf" else dsp.set_lpf
            try:
                setter(d[ATTR_CHANNEL], hz=d.get(ATTR_FREQUENCY), slope=d.get(ATTR_SLOPE))
            except IntegraDspError as err:
                raise HomeAssistantError(str(err)) from err

    async def set_delay(call: ServiceCall) -> None:
        for coord in _targets(hass, call):
            try:
                _dsp(coord).set_delay(call.data[ATTR_CHANNEL], call.data[ATTR_DELAY])
            except IntegraDspError as err:
                raise HomeAssistantError(str(err)) from err

    async def set_matrix(call: ServiceCall) -> None:
        d = call.data
        for coord in _targets(hass, call):
            try:
                _dsp(coord).set_matrix(d[ATTR_INPUT], d[ATTR_OUTPUT], d[ATTR_ENABLED])
            except IntegraDspError as err:
                raise HomeAssistantError(str(err)) from err

    async def recall_preset(call: ServiceCall) -> None:
        for coord in _targets(hass, call):
            await async_recall_preset(coord, call.data[ATTR_PRESET])

    hass.services.async_register(DOMAIN, SERVICE_SET_PEQ, set_peq, SCHEMA_SET_PEQ)
    hass.services.async_register(DOMAIN, SERVICE_SET_COMPRESSOR, set_compressor, SCHEMA_SET_COMPRESSOR)
    hass.services.async_register(DOMAIN, SERVICE_SET_FILTER, set_filter, SCHEMA_SET_FILTER)
    hass.services.async_register(DOMAIN, SERVICE_SET_DELAY, set_delay, SCHEMA_SET_DELAY)
    hass.services.async_register(DOMAIN, SERVICE_SET_MATRIX, set_matrix, SCHEMA_SET_MATRIX)
    hass.services.async_register(DOMAIN, SERVICE_RECALL_PRESET, recall_preset, SCHEMA_RECALL_PRESET)


async def async_recall_preset(coordinator: IntegraCoordinator, preset: int) -> None:
    """Ruft ein Preset ab und liest danach Gain/Mute neu ein."""
    try:
        await _dsp(coordinator).recall_preset(preset)
    except IntegraDspError as err:
        raise HomeAssistantError(f"Preset {preset} konnte nicht abgerufen werden: {err}") from err
    # Die Integra lädt das Preset; danach Fader/Mutes im HA-Zustand nachziehen.
    await asyncio.sleep(1.0)
    await coordinator.async_request_refresh()
