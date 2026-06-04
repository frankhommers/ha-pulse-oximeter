"""Config flow for Pulse Oximeter (BLE) integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .const import (
    CONF_NOTIFICATIONS_ENABLED,
    CONF_NOTIFY_TARGETS,
    CONF_PARTICIPANTS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def suggest_notify_target(hass: HomeAssistant, person_entity_id: str) -> str | None:
    """Guess the mobile_app notify service belonging to a person."""
    state = hass.states.get(person_entity_id)
    if state is None:
        return None
    notify_services = hass.services.async_services().get("notify", {})
    for tracker in state.attributes.get("device_trackers", []):
        candidate = f"mobile_app_{tracker.split('.', 1)[1]}"
        if candidate in notify_services:
            return candidate
    return None


class OxiConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Pulse Oximeter."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize."""
        self._discovery_info: BluetoothServiceInfoBleak | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> "OxiOptionsFlow":
        """Create the options flow."""
        return OxiOptionsFlow()

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> FlowResult:
        """Handle the bluetooth discovery step."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()

        self._discovery_info = discovery_info
        name = discovery_info.name or discovery_info.address
        self.context["title_placeholders"] = {"name": name}

        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm discovery."""
        if user_input is not None:
            return self.async_create_entry(
                title=self._discovery_info.name or "Pulse Oximeter",
                data={
                    "address": self._discovery_info.address,
                    "name": self._discovery_info.name or "Pulse Oximeter",
                },
            )

        self._set_confirm_only()
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={
                "name": self._discovery_info.name or self._discovery_info.address,
            },
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the user step - manual setup not supported."""
        return self.async_abort(reason="bluetooth_only")


class OxiOptionsFlow(OptionsFlow):
    """Configure participants and notification targets."""

    def __init__(self) -> None:
        """Initialize."""
        self._participants: list[str] = []
        self._enabled: bool = True

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Choose participants and toggle notifications."""
        if user_input is not None:
            self._participants = user_input.get(CONF_PARTICIPANTS, [])
            self._enabled = user_input.get(CONF_NOTIFICATIONS_ENABLED, True)
            if not self._participants:
                return self._create_entry({})
            return await self.async_step_targets()

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_PARTICIPANTS,
                    default=options.get(CONF_PARTICIPANTS, []),
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="person", multiple=True)
                ),
                vol.Optional(
                    CONF_NOTIFICATIONS_ENABLED,
                    default=options.get(CONF_NOTIFICATIONS_ENABLED, True),
                ): selector.BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

    async def async_step_targets(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Pick a notify service per participant."""
        if user_input is not None:
            targets = {
                person: user_input[person]
                for person in self._participants
                if user_input.get(person) and user_input[person] != "none"
            }
            return self._create_entry(targets)

        notify_services = sorted(
            name
            for name in self.hass.services.async_services().get("notify", {})
            if name.startswith("mobile_app_")
        )
        select_options = ["none", *notify_services]
        existing = self.config_entry.options.get(CONF_NOTIFY_TARGETS, {})
        schema_dict = {}
        for person in self._participants:
            stored = existing.get(person)
            default = (
                stored
                if stored in select_options
                else suggest_notify_target(self.hass, person) or "none"
            )
            schema_dict[vol.Required(person, default=default)] = (
                selector.SelectSelector(
                    selector.SelectSelectorConfig(options=select_options)
                )
            )
        return self.async_show_form(
            step_id="targets", data_schema=vol.Schema(schema_dict)
        )

    def _create_entry(self, targets: dict[str, str]) -> FlowResult:
        return self.async_create_entry(
            data={
                CONF_PARTICIPANTS: self._participants,
                CONF_NOTIFY_TARGETS: targets,
                CONF_NOTIFICATIONS_ENABLED: self._enabled,
            }
        )
