"""Runtime data shared between platforms."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.core import HomeAssistant

from .assignment import AssignmentManager
from .coordinator import OxiCoordinator
from .notifier import MeasurementNotifier


@dataclass
class RuntimeData:
    """Objects stored in hass.data[DOMAIN][entry_id]."""

    coordinator: OxiCoordinator
    manager: AssignmentManager
    notifier: MeasurementNotifier


def person_display_name(hass: HomeAssistant, entity_id: str) -> str:
    """Best-effort display name for a person entity."""
    state = hass.states.get(entity_id)
    return state.name if state else entity_id
