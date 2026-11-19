"""Switch-Entities: Mute je Input- und Output-Kanal sowie Matrix-Kreuzpunkte.

Robustheit (v2): optimistisches Schalten ohne erzwungenen Sofort-Refresh und
tolerante Verfügbarkeit (ein verpasster Poll macht den Schalter nicht offline).
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import IntegraCoordinator
from .integra_bin import IntegraDspError


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: IntegraCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SwitchEntity] = []
    for channel in range(coordinator.outputs):
        entities.append(IntegraMute(coordinator, entry, "output", channel))
    for channel in range(coordinator.inputs):
        entities.append(IntegraMute(coordinator, entry, "input", channel))
    # Routing-Matrix (DSP-Protokoll): ein Schalter je Kreuzpunkt Eingang -> Ausgang.
    for inp in range(1, coordinator.inputs + 1):
        for out in range(1, coordinator.outputs + 1):
            entities.append(IntegraMatrixPoint(coordinator, entry, inp, out))
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
    def available(self) -> bool:
        data = self.coordinator.data.get(f"{self._kind}_mute", {}) if self.coordinator.data else {}
        return self._channel in data

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data.get(f"{self._kind}_mute", {}) if self.coordinator.data else {}
        return data.get(self._channel)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set_mute(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set_mute(False)

    async def _set_mute(self, mute: bool) -> None:
        # Optimistisch sofort anzeigen, dann senden (kein Sofort-Refresh).
        if self.coordinator.data is not None:
            self.coordinator.data.setdefault(f"{self._kind}_mute", {})[self._channel] = mute
            self.async_write_ha_state()
        if self._kind == "input":
            await self.coordinator.client.set_input_mute(self._channel, mute)
        else:
            await self.coordinator.client.set_output_mute(self._channel, mute)


class IntegraMatrixPoint(RestoreEntity, SwitchEntity):
    """Kreuzpunkt der Routing-Matrix (an = Eingang liegt auf dem Ausgang).

    Die Integra meldet den Matrix-Zustand nicht von sich aus. Angezeigt wird der
    zuletzt bekannte Zustand: über Home Assistant geschaltet, per Live-Meldung
    aus der Work-Software übernommen oder nach einem Neustart wiederhergestellt.
    Die Entitäten sind standardmäßig ausgeblendet (für die Matrix-Karte gedacht).
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:matrix"
    _attr_assumed_state = True
    _attr_entity_registry_visible_default = False

    def __init__(
        self, coordinator: IntegraCoordinator, entry: ConfigEntry, inp: int, out: int
    ) -> None:
        self._coordinator = coordinator
        self._inp = inp
        self._out = out
        in_name = coordinator.channel_name("input", inp - 1, entry.options)
        out_name = coordinator.channel_name("output", out - 1, entry.options)
        self._attr_unique_id = f"{entry.entry_id}_matrix_{inp}_{out}"
        self._attr_name = f"Matrix {in_name} → {out_name}"
        self._attr_is_on = None
        self._attr_extra_state_attributes = {
            "matrix_input": inp,
            "matrix_output": out,
            "input_name": in_name,
            "output_name": out_name,
        }
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Work Pro",
            model="Integra",
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in ("on", "off"):
            self._attr_is_on = last.state == "on"
        dsp = self._coordinator.dsp
        if dsp is not None:
            self.async_on_remove(dsp.add_push_listener(self._on_push))
            self.async_on_remove(dsp.add_availability_listener(self._on_availability))

    @callback
    def _on_push(self, change: dict) -> None:
        if (
            change.get("kind") == "matrix"
            and change.get("input") == self._inp
            and change.get("output") == self._out
        ):
            self._attr_is_on = bool(change.get("value"))
            self.async_write_ha_state()

    @callback
    def _on_availability(self, _available: bool) -> None:
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        dsp = self._coordinator.dsp
        return dsp is not None and dsp.available

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)

    async def _set(self, on: bool) -> None:
        dsp = self._coordinator.dsp
        if dsp is None:
            raise HomeAssistantError("DSP-Protokoll ist nicht aktiv.")
        try:
            dsp.set_matrix(self._inp, self._out, on)
        except IntegraDspError as err:
            raise HomeAssistantError(str(err)) from err
        self._attr_is_on = on
        self.async_write_ha_state()
