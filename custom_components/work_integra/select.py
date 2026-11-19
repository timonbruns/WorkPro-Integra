"""Select-Entity: Preset-Abruf über das binäre DSP-Protokoll."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, PRESET_COUNT
from .coordinator import IntegraCoordinator
from .services import async_recall_preset

PRESET_OPTIONS = [f"Preset {n}" for n in range(1, PRESET_COUNT + 1)]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: IntegraCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([IntegraPresetSelect(coordinator, entry)])


class IntegraPresetSelect(RestoreEntity, SelectEntity):
    """Zuletzt abgerufenes Preset.

    Die Integra meldet das aktive Preset nicht von sich aus; angezeigt wird
    daher das zuletzt über Home Assistant abgerufene Preset (über Neustarts
    hinweg wiederhergestellt).
    """

    _attr_has_entity_name = True
    _attr_name = "Preset"
    _attr_icon = "mdi:playlist-music"
    _attr_options = PRESET_OPTIONS

    def __init__(self, coordinator: IntegraCoordinator, entry: ConfigEntry) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_preset"
        self._attr_current_option = None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Work Pro",
            model="Integra",
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in PRESET_OPTIONS:
            self._attr_current_option = last.state
        dsp = self._coordinator.dsp
        if dsp is not None:
            self.async_on_remove(dsp.add_availability_listener(self._on_availability))

    @callback
    def _on_availability(self, _available: bool) -> None:
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        dsp = self._coordinator.dsp
        return dsp is not None and dsp.available

    async def async_select_option(self, option: str) -> None:
        preset = PRESET_OPTIONS.index(option) + 1
        await async_recall_preset(self._coordinator, preset)
        self._attr_current_option = option
        self.async_write_ha_state()
