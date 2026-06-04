"""Select entity to (re)assign the latest measurement."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .assignment import FAULTY, AssignmentManager
from .const import (
    CONF_PARTICIPANTS,
    DOMAIN,
    SIGNAL_ASSIGNMENT_UPDATE,
    get_texts,
)
from .data import RuntimeData, person_display_name


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the assignment select."""
    data: RuntimeData = hass.data[DOMAIN][entry.entry_id]
    participants = entry.options.get(CONF_PARTICIPANTS, [])
    if not participants:
        return
    person_names = {
        person: person_display_name(hass, person) for person in participants
    }
    async_add_entities(
        [
            OxiAssignSelect(
                manager=data.manager,
                entry=entry,
                coordinator_address=data.coordinator.address,
                device_name=data.coordinator.device_name,
                person_names=person_names,
                texts=get_texts(hass.config.language),
            )
        ]
    )


class OxiAssignSelect(SelectEntity):
    """Shows and changes who the latest measurement belongs to."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_icon = "mdi:account-question"

    def __init__(
        self,
        manager: AssignmentManager,
        entry: ConfigEntry,
        coordinator_address: str,
        device_name: str,
        person_names: dict[str, str],
        texts: dict[str, str],
    ) -> None:
        """Initialize the select."""
        self._manager = manager
        self._entry = entry
        self._coordinator_address = coordinator_address
        self._device_name = device_name
        self._person_names = person_names
        self._texts = texts
        self._attr_unique_id = f"{entry.unique_id}_last_measurement"
        self._attr_name = texts["select_name"]

    @property
    def device_info(self) -> DeviceInfo:
        """Attach to the oximeter device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._coordinator_address)},
            name=self._device_name,
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to assignment updates."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_ASSIGNMENT_UPDATE.format(entry_id=self._entry.entry_id),
                self._handle_update,
            )
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def options(self) -> list[str]:
        """Unassigned + persons + faulty."""
        return [
            self._texts["unassigned"],
            *self._person_names.values(),
            self._texts["faulty"],
        ]

    @property
    def current_option(self) -> str:
        """Mirror the assignment state."""
        if self._manager.assigned_to is None:
            return self._texts["unassigned"]
        if self._manager.assigned_to == FAULTY:
            return self._texts["faulty"]
        return self._person_names.get(
            self._manager.assigned_to, self._manager.assigned_to
        )

    async def async_select_option(self, option: str) -> None:
        """Map the chosen option onto an assignment."""
        if option == self._texts["unassigned"]:
            target: str | None = None
        elif option == self._texts["faulty"]:
            target = FAULTY
        else:
            target = next(
                (
                    person
                    for person, name in self._person_names.items()
                    if name == option
                ),
                None,
            )
            if target is None:
                return
        self._manager.assign(target, source="select")
