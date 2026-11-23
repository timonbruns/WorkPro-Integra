"""Number-Entities: Lautstärke (Gain) je Input- und Output-Kanal.

Robustheit (v2): Beim Schieben wird der Wert sofort optimistisch angezeigt und
KEIN erzwungener Sofort-Refresh mehr ausgelöst (der periodische Poll gleicht ab).
Die Entität bleibt verfügbar, solange irgendwann ein Wert gelesen wurde – ein
kurzer Poll-Aussetzer lässt den Fader damit nicht mehr auf Null springen.
"""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    GAIN_STEP_DB,
    INPUT_GAIN_MAX_DB,
    INPUT_GAIN_MIN_DB,
    OUTPUT_GAIN_MAX_DB,
    OUTPUT_GAIN_MIN_DB,
)
from .coordinator import IntegraCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: IntegraCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[IntegraGain] = []
    for channel in range(coordinator.outputs):
        entities.append(IntegraGain(coordinator, entry, "output", channel))
    for channel in range(coordinator.inputs):
        entities.append(IntegraGain(coordinator, entry, "input", channel))
    async_add_entities(entities)


class IntegraGain(CoordinatorEntity[IntegraCoordinator], NumberEntity):
    """Lautstärke-Fader eines In- oder Output-Kanals (in dB)."""

    _attr_has_entity_name = True
    _attr_native_step = GAIN_STEP_DB
    _attr_native_unit_of_measurement = "dB"
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:volume-high"

    def __init__(
        self,
        coordinator: IntegraCoordinator,
        entry: ConfigEntry,
        kind: str,  # "input" oder "output"
        channel: int,
    ) -> None:
        super().__init__(coordinator)
        self._kind = kind
        self._channel = channel
        base = coordinator.channel_name(kind, channel, entry.options)
        self._attr_unique_id = f"{entry.entry_id}_{kind}{channel}_gain"
        self._attr_name = f"{base} Lautstärke"
        if kind == "input":
            self._attr_native_min_value = INPUT_GAIN_MIN_DB
            self._attr_native_max_value = INPUT_GAIN_MAX_DB
        else:
            self._attr_native_min_value = OUTPUT_GAIN_MIN_DB
            self._attr_native_max_value = OUTPUT_GAIN_MAX_DB
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Work Pro",
            model="Integra",
        )

    @property
    def available(self) -> bool:
        # Verfügbar, solange wir je einen Wert für diesen Kanal hatten.
        # Ein einzelner verpasster Poll macht die Entität damit nicht offline.
        gains = self.coordinator.data.get(f"{self._kind}_gain", {}) if self.coordinator.data else {}
        return self._channel in gains

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data.get(f"{self._kind}_gain", {}) if self.coordinator.data else {}
        return data.get(self._channel)

    async def async_set_native_value(self, value: float) -> None:
        # 1) Optimistisch sofort anzeigen und für kurze Zeit gegen ältere
        #    Poll-Werte schützen -> Fader springt beim Ziehen nicht zurück.
        self.coordinator.set_optimistic(f"{self._kind}_gain", self._channel, value)
        self.async_write_ha_state()
        # 2) An das Gerät senden (fire-and-forget, kein Sofort-Refresh).
        if self._kind == "input":
            await self.coordinator.client.set_input_gain(self._channel, value)
        else:
            await self.coordinator.client.set_output_gain(self._channel, value)
