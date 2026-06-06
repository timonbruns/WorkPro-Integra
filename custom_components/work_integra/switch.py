"""Switch-Entities: Mute je Input- und Output-Kanal."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import IntegraCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: IntegraCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[IntegraMute] = []
    for channel in range(coordinator.outputs):
        entities.append(IntegraMute(coordinator, entry, "output", channel))
    for channel in range(coordinator.inputs):
        entities.append(IntegraMute(coordinator, entry, "input", channel))
    async_add_entities(entities)


class IntegraMute(CoordinatorEntity[IntegraCoordinator], SwitchEntity):
    """Mute-Schalter eines In- oder Output-Kanals."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:volume-mute"

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
        self._attr_unique_id = f"{entry.entry_id}_{kind}{channel}_mute"
        self._attr_name = f"{base} Mute"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Work Pro",
            model="Integra",
        )

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data.get(f"{self._kind}_mute", {})
        return data.get(self._channel)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set_mute(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set_mute(False)

    async def _set_mute(self, mute: bool) -> None:
        if self._kind == "input":
            await self.coordinator.client.set_input_mute(self._channel, mute)
        else:
            await self.coordinator.client.set_output_mute(self._channel, mute)
        await self.coordinator.async_request_refresh()
