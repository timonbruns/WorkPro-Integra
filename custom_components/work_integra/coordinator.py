"""Coordinator: fragt den Ist-Zustand des Geräts periodisch ab."""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, SCAN_INTERVAL
from .integra import IntegraClient, IntegraError

_LOGGER = logging.getLogger(__name__)


class IntegraCoordinator(DataUpdateCoordinator):
    """Hält den aktuellen Zustand (Gain + Mute je In-/Output-Kanal)."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: IntegraClient,
        outputs: int,
        inputs: int,
        device_names: dict[str, dict[int, str]] | None = None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self.client = client
        self.outputs = outputs
        self.inputs = inputs
        # Vom Gerät gelesene Kanalnamen, dienen als Default-Bezeichnung.
        self.device_names = device_names or {"input": {}, "output": {}}

    def channel_name(self, kind: str, channel: int, options: dict) -> str:
        """Ermittelt den anzuzeigenden Kanalnamen.

        Reihenfolge: 1) Override aus den Integrations-Optionen,
        2) vom Gerät gelesener Name, 3) generischer Fallback.
        """
        override = options.get(f"{kind}_name_{channel}")
        if override:
            return override
        device = self.device_names.get(kind, {}).get(channel)
        if device:
            return device
        label = "Input" if kind == "input" else "Output"
        return f"{label} {channel + 1}"

    async def _async_update_data(self) -> dict:
        """Wird vom Coordinator periodisch aufgerufen."""
        try:
            data: dict = {}
            if self.outputs:
                last = self.outputs - 1
                data["output_gain"] = await self.client.get_output_gains(0, last)
                data["output_mute"] = await self.client.get_output_mutes(0, last)
            if self.inputs:
                last = self.inputs - 1
                data["input_gain"] = await self.client.get_input_gains(0, last)
                data["input_mute"] = await self.client.get_input_mutes(0, last)
        except IntegraError as err:
            raise UpdateFailed(f"Fehler beim Abfragen der Integra: {err}") from err
        return data
