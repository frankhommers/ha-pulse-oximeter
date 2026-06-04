"""Assignment state machine: who does the latest measurement belong to."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from .const import EVENT_MEASUREMENT_ASSIGNED, EVENT_MEASUREMENT_FINISHED
from .measurement import MeasurementSummary

_LOGGER = logging.getLogger(__name__)

FAULTY = "faulty"

_PERSON_ATTR_KEYS = (
    "measured_at",
    "duration",
    "spo2_min",
    "spo2_max",
    "pulse_min",
    "pulse_max",
    "readings",
)


@dataclass
class PersonValues:
    """Last claimed measurement values for one person."""

    spo2: float
    pulse_rate: float
    perfusion_index: float | None
    attributes: dict


class AssignmentManager:
    """Tracks the latest measurement and per-person claimed values.

    Only the latest measurement is mutable. Assignment targets:
    a person entity_id, FAULTY (discard), or None (unassign).
    """

    def __init__(
        self,
        participants: list[str],
        fire_event: Callable[[str, dict], None],
    ) -> None:
        self.participants = participants
        self._fire_event = fire_event
        self._listeners: list[Callable[[], None]] = []
        self.current: MeasurementSummary | None = None
        self.assigned_to: str | None = None  # person entity_id or FAULTY
        self.person_values: dict[str, PersonValues] = {}
        self._person_backup: dict[str, PersonValues | None] = {}

    def add_listener(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Register an update listener; returns an unsubscribe callable."""
        self._listeners.append(callback)
        return lambda: self._listeners.remove(callback)

    def _notify_listeners(self) -> None:
        for callback in self._listeners:
            callback()

    def new_measurement(self, summary: MeasurementSummary) -> None:
        """Register a freshly finished measurement (replaces an unclaimed one)."""
        self.current = summary
        self.assigned_to = None
        self._person_backup = {}
        self._fire_event(EVENT_MEASUREMENT_FINISHED, summary.as_event_data())
        self._notify_listeners()

    def assign(
        self,
        target: str | None,
        measurement_id: int | None = None,
        source: str = "service",
    ) -> bool:
        """(Re)assign the latest measurement. Returns False if rejected."""
        if self.current is None:
            return False
        if (
            measurement_id is not None
            and measurement_id != self.current.measurement_id
        ):
            _LOGGER.debug(
                "Ignoring stale answer for measurement %s (current is %s)",
                measurement_id,
                self.current.measurement_id,
            )
            return False
        if target not in (None, FAULTY) and target not in self.participants:
            _LOGGER.warning("Unknown participant: %s", target)
            return False
        if target == self.assigned_to:
            return True  # no-op

        # Revert the previous owner to their pre-claim values
        if self.assigned_to not in (None, FAULTY):
            backup = self._person_backup.get(self.assigned_to)
            if backup is None:
                self.person_values.pop(self.assigned_to, None)
            else:
                self.person_values[self.assigned_to] = backup

        # Apply the new target
        if target not in (None, FAULTY):
            self._person_backup[target] = self.person_values.get(target)
            data = self.current.as_event_data()
            self.person_values[target] = PersonValues(
                spo2=self.current.spo2,
                pulse_rate=self.current.pulse_rate,
                perfusion_index=self.current.perfusion_index,
                attributes={key: data[key] for key in _PERSON_ATTR_KEYS},
            )

        self.assigned_to = target
        event_data = self.current.as_event_data()
        event_data.update(
            {
                "assigned_to": None if target == FAULTY else target,
                "faulty": target == FAULTY,
                "source": source,
            }
        )
        self._fire_event(EVENT_MEASUREMENT_ASSIGNED, event_data)
        self._notify_listeners()
        return True
