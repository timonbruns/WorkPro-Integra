"""Work Pro Integra — Integration-Setup."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import (
    CONF_HOST,
    CONF_INPUTS,
    CONF_OUTPUTS,
    CONF_PORT,
    DEFAULT_INPUTS,
    DEFAULT_OUTPUTS,
    DOMAIN,
)
from .coordinator import IntegraCoordinator
from .integra import IntegraClient, IntegraError

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.NUMBER, Platform.SWITCH]


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
    return unloaded
