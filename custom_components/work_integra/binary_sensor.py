"""Binary-Sensor: Verbindungsstatus des binären DSP-Protokolls."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import IntegraCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: IntegraCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([IntegraDspConnected(coordinator, entry)])


class IntegraDspConnected(BinarySensorEntity):
    """An, solange die Integra auf das DSP-Keepalive (UDP 6001) antwortet."""

    _attr_has_entity_name = True
    _attr_name = "DSP-Verbindung"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False

    def __init__(self, coordinator: IntegraCoordinator, entry: ConfigEntry) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_dsp_connected"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Work Pro",
            model="Integra",
        )

    async def async_added_to_hass(self) -> None:
        dsp = self._coordinator.dsp
        if dsp is not None:
            self.async_on_remove(dsp.add_availability_listener(self._on_change))

    @callback
    def _on_change(self, _available: bool) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        dsp = self._coordinator.dsp
        return dsp is not None and dsp.available
