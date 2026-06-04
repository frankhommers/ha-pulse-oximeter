"""Actionable notifications for measurement assignment."""

from __future__ import annotations

import logging

from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .assignment import FAULTY, AssignmentManager
from .const import MOBILE_APP_ACTION_EVENT, NOTIFY_ACTION_PREFIX, get_texts
from .measurement import MeasurementSummary

_LOGGER = logging.getLogger(__name__)


def build_action_id(
    entry_id: str, kind: str, measurement_id: int, person: str
) -> str:
    """Build a notification action ID encoding entry, measurement and person."""
    return f"{NOTIFY_ACTION_PREFIX}|{entry_id}|{kind}|{measurement_id}|{person}"


def parse_action_id(action: str) -> tuple[str, str, int, str] | None:
    """Parse an action ID into (entry_id, kind, mid, person); None if not ours."""
    parts = action.split("|")
    if len(parts) != 5 or parts[0] != NOTIFY_ACTION_PREFIX:
        return None
    try:
        return parts[1], parts[2], int(parts[3]), parts[4]
    except ValueError:
        return None


def build_notification(
    summary: MeasurementSummary,
    entry_id: str,
    person: str,
    tag: str,
    texts: dict[str, str],
) -> dict:
    """Build the personalized notify payload for one person's phone."""
    local_time = dt_util.as_local(summary.finished_at).strftime("%H:%M")
    mid = summary.measurement_id
    return {
        "title": texts["notification_title"],
        "message": texts["notification_message"].format(
            time=local_time,
            spo2=summary.spo2,
            pulse=round(summary.pulse_rate),
        ),
        "data": {
            "tag": tag,
            "actions": [
                {
                    "action": build_action_id(entry_id, "MINE", mid, person),
                    "title": texts["mine"],
                },
                {
                    "action": build_action_id(entry_id, "NOTMINE", mid, person),
                    "title": texts["not_mine"],
                },
                {
                    "action": build_action_id(entry_id, "FAULTY", mid, person),
                    "title": texts["faulty"],
                },
            ],
        },
    }


class MeasurementNotifier:
    """Sends assignment questions and processes the answers."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        manager: AssignmentManager,
        notify_targets: dict[str, str],
        enabled: bool,
    ) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.manager = manager
        self.notify_targets = notify_targets
        self.enabled = enabled
        self.tag = f"pulseox_{entry_id}"
        self._unsub = None

    @callback
    def async_setup(self) -> None:
        """Start listening for notification action events."""
        self._unsub = self.hass.bus.async_listen(
            MOBILE_APP_ACTION_EVENT, self._on_action
        )

    @callback
    def async_unload(self) -> None:
        """Stop listening."""
        if self._unsub:
            self._unsub()
            self._unsub = None

    async def async_send(self, summary: MeasurementSummary) -> None:
        """Send the assignment question to every configured phone."""
        if not self.enabled or not self.notify_targets:
            return
        texts = get_texts(self.hass.config.language)
        for person, target in self.notify_targets.items():
            payload = build_notification(
                summary, self.entry_id, person, self.tag, texts
            )
            try:
                await self.hass.services.async_call(
                    "notify", target, payload, blocking=True
                )
            except Exception as err:  # noqa: BLE001 - phone gone must not break others
                _LOGGER.warning(
                    "Failed to notify %s via notify.%s: %s", person, target, err
                )

    async def async_clear(self, only_person: str | None = None) -> None:
        """Dismiss the question on all phones, or on one person's phone."""
        if only_person is not None:
            targets = [self.notify_targets[only_person]]
        else:
            targets = list(self.notify_targets.values())
        for target in targets:
            try:
                await self.hass.services.async_call(
                    "notify",
                    target,
                    {"message": "clear_notification", "data": {"tag": self.tag}},
                    blocking=True,
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Failed to clear notification on %s: %s", target, err)

    async def _on_action(self, event: Event) -> None:
        """Handle a mobile_app_notification_action event."""
        action = event.data.get("action")
        if not isinstance(action, str):
            return
        parsed = parse_action_id(action)
        if parsed is None:
            return
        entry_id, kind, measurement_id, person = parsed
        if entry_id != self.entry_id:
            return
        if person not in self.notify_targets:
            return

        try:
            if kind == "MINE":
                if self.manager.assign(
                    person, measurement_id, source="notification"
                ):
                    await self.async_clear()
            elif kind == "FAULTY":
                if self.manager.assign(
                    FAULTY, measurement_id, source="notification"
                ):
                    await self.async_clear()
            elif kind == "NOTMINE":
                current = self.manager.current
                if (
                    self.manager.assigned_to == person
                    and current is not None
                    and current.measurement_id == measurement_id
                ):
                    self.manager.assign(None, measurement_id, source="notification")
                await self.async_clear(only_person=person)
        except Exception:  # noqa: BLE001 - must not escape the bus listener
            _LOGGER.exception("Error handling notification action")
