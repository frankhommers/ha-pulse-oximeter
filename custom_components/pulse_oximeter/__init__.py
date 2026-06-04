"""The Pulse Oximeter (BLE) integration."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .assignment import FAULTY, AssignmentManager
from .const import (
    CONF_NOTIFICATIONS_ENABLED,
    CONF_NOTIFY_TARGETS,
    CONF_PARTICIPANTS,
    DOMAIN,
    PLATFORMS,
    SERVICE_ASSIGN_MEASUREMENT,
    SIGNAL_ASSIGNMENT_UPDATE,
)
from .coordinator import OxiCoordinator
from .data import RuntimeData
from .measurement import MeasurementSummary
from .notifier import MeasurementNotifier

_LOGGER = logging.getLogger(__name__)

ASSIGN_SCHEMA = vol.Schema(
    {
        vol.Optional("person"): cv.entity_id,
        vol.Optional("faulty", default=False): cv.boolean,
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Pulse Oximeter from a config entry."""
    address: str = entry.data["address"]
    name: str = entry.data.get("name", "Pulse Oximeter")

    coordinator = OxiCoordinator(hass, address, name)

    participants: list[str] = entry.options.get(CONF_PARTICIPANTS, [])
    missing = [p for p in participants if hass.states.get(p) is None]
    if missing:
        _LOGGER.warning("Configured participants not found: %s", missing)

    manager = AssignmentManager(participants, hass.bus.async_fire)

    notify_targets = {
        person: target
        for person, target in entry.options.get(CONF_NOTIFY_TARGETS, {}).items()
        if person in participants and target
    }
    notifier = MeasurementNotifier(
        hass,
        entry.entry_id,
        manager,
        notify_targets,
        entry.options.get(CONF_NOTIFICATIONS_ENABLED, True),
    )
    notifier.async_setup()

    signal = SIGNAL_ASSIGNMENT_UPDATE.format(entry_id=entry.entry_id)

    @callback
    def _on_assignment_update() -> None:
        async_dispatcher_send(hass, signal)

    manager.add_listener(_on_assignment_update)

    @callback
    def _on_session_finished(summary: MeasurementSummary) -> None:
        manager.new_measurement(summary)
        entry.async_create_background_task(
            hass, notifier.async_send(summary), name="pulse_oximeter_notify"
        )

    coordinator.session_callback = _on_session_finished

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = RuntimeData(coordinator, manager, notifier)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Start connecting after platforms are set up so entities receive updates
    await coordinator.async_start()

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    _async_register_services(hass)

    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


@callback
def _async_register_services(hass: HomeAssistant) -> None:
    """Register integration services (idempotent)."""
    if hass.services.has_service(DOMAIN, SERVICE_ASSIGN_MEASUREMENT):
        return

    async def _handle_assign(call: ServiceCall) -> None:
        person = call.data.get("person")
        faulty = call.data["faulty"]
        target = FAULTY if faulty else person
        entries = list(hass.data.get(DOMAIN, {}).values())
        if person and not faulty and not any(
            person in data.manager.participants for data in entries
        ):
            raise ServiceValidationError(
                f"{person} is not a configured participant"
            )
        for data in entries:
            data.manager.assign(target, source="service")

    hass.services.async_register(
        DOMAIN, SERVICE_ASSIGN_MEASUREMENT, _handle_assign, schema=ASSIGN_SCHEMA
    )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    data: RuntimeData = hass.data[DOMAIN][entry.entry_id]
    data.notifier.async_unload()
    data.coordinator.session_callback = None
    await data.coordinator.async_stop()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
