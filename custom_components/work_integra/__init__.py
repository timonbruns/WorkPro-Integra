"""Work Pro Integra — Integration-Setup."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_DSP_PORT,
    CONF_HOST,
    CONF_INPUTS,
    CONF_OUTPUTS,
    CONF_PORT,
    DEFAULT_DSP_PORT,
    DEFAULT_INPUTS,
    DEFAULT_OUTPUTS,
    DOMAIN,
    EVENT_DSP_CHANGED,
)
from .coordinator import IntegraCoordinator
from .integra import IntegraClient, IntegraError
from .integra_bin import IntegraDspClient
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SWITCH,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Registriert die Dienste einmalig für alle Integra-Geräte."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Richtet eine konfigurierte Integra ein."""
    client = IntegraClient(entry.data[CONF_HOST], entry.data[CONF_PORT])
    await client.connect()

    outputs = entry.data.get(CONF_OUTPUTS, DEFAULT_OUTPUTS)
    inputs = entry.data.get(CONF_INPUTS, DEFAULT_INPUTS)

    # Kanalnamen einmalig vom Gerät lesen (als Default-Bezeichnung der Entities).
    device_names: dict[str, dict[int, str]] = {"input": {}, "output": {}}
    try:
        if inputs:
            device_names["input"] = await client.get_input_names(0, inputs - 1)
        if outputs:
            device_names["output"] = await client.get_output_names(0, outputs - 1)
    except IntegraError as err:
        _LOGGER.debug("Kanalnamen konnten nicht gelesen werden: %s", err)

    coordinator = IntegraCoordinator(hass, client, outputs, inputs, device_names)
    await coordinator.async_config_entry_first_refresh()

    # Binäres DSP-Protokoll (EQ, Kompressor, Filter, Delay, Matrix, Presets).
    # Läuft parallel zum ASCII-Protokoll; ein Ausfall stört Gain/Mute nicht.
    dsp = IntegraDspClient(
        entry.data[CONF_HOST], entry.data.get(CONF_DSP_PORT, DEFAULT_DSP_PORT)
    )
    try:
        await dsp.connect()
    except OSError as err:
        _LOGGER.warning("DSP-Protokoll nicht verfügbar: %s", err)
    coordinator.dsp = dsp

    @callback
    def _on_dsp_push(change: dict) -> None:
        hass.bus.async_fire(
            EVENT_DSP_CHANGED, {"config_entry_id": entry.entry_id, **change}
        )

    entry.async_on_unload(dsp.add_push_listener(_on_dsp_push))

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Bei Änderung der Optionen (Kanalnamen) Eintrag neu laden.
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Entlädt die Integration sauber."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator: IntegraCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        coordinator.client.close()
        if coordinator.dsp is not None:
            coordinator.dsp.close()
    return unloaded
