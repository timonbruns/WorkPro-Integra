"""Config Flow + Options Flow für die Work Pro Integra."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback

from .const import (
    CONF_DSP_PORT,
    CONF_HOST,
    CONF_INPUTS,
    CONF_OUTPUTS,
    CONF_PORT,
    DEFAULT_DSP_PORT,
    DEFAULT_INPUTS,
    DEFAULT_OUTPUTS,
    DEFAULT_PORT,
    DOMAIN,
)
from .integra import IntegraClient, IntegraError


class IntegraConfigFlow(ConfigFlow, domain=DOMAIN):
    """Geführte Einrichtung der Integra."""

    VERSION = 1

    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            client = IntegraClient(user_input[CONF_HOST], user_input[CONF_PORT])
            try:
                await client.connect()
                await client.get_output_gains(0, 0)  # Verbindungstest
            except (IntegraError, OSError):
                errors["base"] = "cannot_connect"
            finally:
                client.close()

            if not errors:
                await self.async_set_unique_id(user_input[CONF_HOST])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Integra ({user_input[CONF_HOST]})",
                    data=user_input,
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
                vol.Required(CONF_OUTPUTS, default=DEFAULT_OUTPUTS): int,
                vol.Required(CONF_INPUTS, default=DEFAULT_INPUTS): int,
                vol.Optional(CONF_DSP_PORT, default=DEFAULT_DSP_PORT): int,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reconfigure(self, user_input=None) -> ConfigFlowResult:
        """IP-Adresse/Ports nachträglich ändern – Entitäten, Namen und Dashboards bleiben."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            client = IntegraClient(host, user_input[CONF_PORT])
            try:
                await client.connect()
                await client.get_output_gains(0, 0)  # Verbindungstest
            except (IntegraError, OSError):
                errors["base"] = "cannot_connect"
            finally:
                client.close()

            if not errors:
                for other in self._async_current_entries(include_ignore=False):
                    if other.entry_id != entry.entry_id and other.unique_id == host:
                        return self.async_abort(reason="already_configured")
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=host,
                    title=f"Integra ({host})",
                    data_updates={**user_input, CONF_HOST: host},
                )

        data = {**entry.data, **(user_input or {})}
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=data[CONF_HOST]): str,
                vol.Required(CONF_PORT, default=data.get(CONF_PORT, DEFAULT_PORT)): int,
                vol.Optional(
                    CONF_DSP_PORT, default=data.get(CONF_DSP_PORT, DEFAULT_DSP_PORT)
                ): int,
            }
        )
        return self.async_show_form(step_id="reconfigure", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return IntegraOptionsFlow()


class IntegraOptionsFlow(OptionsFlow):
    """Erlaubt das Umbenennen jedes Kanals (vorausgefüllt mit Gerätenamen)."""

    def __init__(self) -> None:
        self._names: dict[str, str] = {}

    def _build_schema(self, kind: str, count: int) -> vol.Schema:
        coordinator = self.hass.data[DOMAIN][self.config_entry.entry_id]
        options = self.config_entry.options
        fields = {}
        for channel in range(count):
            key = f"{kind}_name_{channel}"
            default = coordinator.channel_name(kind, channel, options)
            fields[vol.Optional(key, default=default)] = str
        return vol.Schema(fields)

    async def async_step_init(self, user_input=None):
        return await self.async_step_inputs()

    async def async_step_inputs(self, user_input=None):
        if user_input is not None:
            self._names.update(user_input)
            return await self.async_step_outputs()
        count = self.config_entry.data.get(CONF_INPUTS, DEFAULT_INPUTS)
        return self.async_show_form(
            step_id="inputs", data_schema=self._build_schema("input", count)
        )

    async def async_step_outputs(self, user_input=None):
        if user_input is not None:
            self._names.update(user_input)
            return self.async_create_entry(title="", data=self._names)
        count = self.config_entry.data.get(CONF_OUTPUTS, DEFAULT_OUTPUTS)
        return self.async_show_form(
            step_id="outputs", data_schema=self._build_schema("output", count)
        )
