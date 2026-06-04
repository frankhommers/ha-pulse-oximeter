# Person Assignment Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Link finished pulse oximeter measurements to Home Assistant persons via explicit claiming (actionable notifications + dashboard select), per the approved design in `docs/plans/2026-06-04-person-assignment-design.md`.

**Architecture:** The coordinator detects measurement sessions (finger-on → finger-off) and produces a summary. A pure-Python `AssignmentManager` holds the latest measurement and per-person claimed values (state machine: unassigned → person | faulty). A `MeasurementNotifier` sends personalized actionable notifications and listens for `mobile_app_notification_action` events. Per-person sensors and a select entity render the state. No automations or blueprints required.

**Tech Stack:** Home Assistant custom integration (Python 3.12+), bleak, pytest + pytest-homeassistant-custom-component.

**Design doc:** `docs/plans/2026-06-04-person-assignment-design.md` — read it first.

---

## Task 1: Test infrastructure

**Files:**
- Create: `requirements_test.txt`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Modify: `.gitignore`

**Step 1: Create `requirements_test.txt`**

```
pytest-homeassistant-custom-component
bleak>=0.21.0
bleak-retry-connector>=3.5.0
```

**Step 2: Create `tests/__init__.py`** (empty file)

**Step 3: Create `tests/conftest.py`**

```python
"""Shared fixtures."""

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading custom integrations in all tests."""
    yield
```

**Step 4: Add to `.gitignore`** (if not already present): `.venv/`

**Step 5: Create venv and install**

Run:
```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements_test.txt
```

**Step 6: Sanity check**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: `no tests ran` (exit code 5 is fine — infrastructure works, no import errors)

**Step 7: Commit**

```bash
git add requirements_test.txt tests/ .gitignore
git commit -m "Add pytest test infrastructure"
```

---

## Task 2: Constants

**Files:**
- Modify: `custom_components/pulse_oximeter/const.py`

**Step 1: Append to `const.py`** (and change `PLATFORMS`):

```python
PLATFORMS = ["sensor", "select"]

# Measurement session detection
SESSION_INACTIVITY_TIMEOUT = 30.0  # seconds without valid readings = session end
SESSION_MIN_READINGS = 5  # fewer valid readings = discard session

# Config entry options
CONF_PARTICIPANTS = "participants"
CONF_NOTIFY_TARGETS = "notify_targets"
CONF_NOTIFICATIONS_ENABLED = "notifications_enabled"

# Events (escape hatch for custom automations)
EVENT_MEASUREMENT_FINISHED = "pulse_oximeter_measurement_finished"
EVENT_MEASUREMENT_ASSIGNED = "pulse_oximeter_measurement_assigned"

# Service
SERVICE_ASSIGN_MEASUREMENT = "assign_measurement"

# Notifications
NOTIFY_ACTION_PREFIX = "PULSEOX"
MOBILE_APP_ACTION_EVENT = "mobile_app_notification_action"

# Dispatcher signal (format with entry_id)
SIGNAL_ASSIGNMENT_UPDATE = "pulse_oximeter_assignment_update_{entry_id}"

# User-facing texts, keyed by hass.config.language
TEXTS = {
    "en": {
        "notification_title": "Pulse Oximeter",
        "notification_message": "Measurement {time} — SpO2 {spo2}%, pulse {pulse} bpm. Yours?",
        "mine": "Mine",
        "not_mine": "Not mine",
        "faulty": "Faulty measurement",
        "unassigned": "Not assigned",
        "select_name": "Last measurement",
    },
    "nl": {
        "notification_title": "Saturatiemeter",
        "notification_message": "Meting {time} — SpO2 {spo2}%, pols {pulse} bpm. Voor jou?",
        "mine": "Voor mij",
        "not_mine": "Niet voor mij",
        "faulty": "Foutieve meting",
        "unassigned": "Niet toegewezen",
        "select_name": "Laatste meting",
    },
}


def get_texts(language: str) -> dict[str, str]:
    """Return UI texts for a language, falling back to English."""
    return TEXTS.get(language, TEXTS["en"])
```

**Step 2: Verify import**

Run: `.venv/bin/python -c "from custom_components.pulse_oximeter.const import get_texts; print(get_texts('nl')['mine'])"`
Expected: `Voor mij`

**Step 3: Commit**

```bash
git add custom_components/pulse_oximeter/const.py
git commit -m "Add constants for person assignment feature"
```

---

## Task 3: Measurement collector (pure logic, TDD)

**Files:**
- Create: `custom_components/pulse_oximeter/measurement.py`
- Test: `tests/test_measurement.py`

**Step 1: Write the failing tests**

```python
"""Tests for measurement session collection."""

from custom_components.pulse_oximeter.measurement import ReadingCollector


def test_too_few_readings_returns_none():
    collector = ReadingCollector(measurement_id=1)
    for _ in range(4):
        collector.add(97.0, 72.0, 5.0)
    assert collector.finalize() is None


def test_summary_uses_median_and_minmax():
    collector = ReadingCollector(measurement_id=2)
    spo2_values = [91.0, 95.0, 96.0, 97.0, 99.0]
    pulse_values = [60.0, 70.0, 72.0, 74.0, 90.0]
    for spo2, pulse in zip(spo2_values, pulse_values):
        collector.add(spo2, pulse, 4.0)
    summary = collector.finalize()
    assert summary is not None
    assert summary.measurement_id == 2
    assert summary.spo2 == 96.0  # median
    assert summary.pulse_rate == 72.0  # median
    assert summary.spo2_min == 91.0
    assert summary.spo2_max == 99.0
    assert summary.pulse_min == 60.0
    assert summary.pulse_max == 90.0
    assert summary.perfusion_index == 4.0  # mean
    assert summary.readings == 5
    assert summary.finished_at >= summary.started_at


def test_invalid_readings_are_skipped():
    collector = ReadingCollector(measurement_id=3)
    collector.add(0.0, 0.0, None)  # zeros: finger not placed
    collector.add(None, None, None)
    for _ in range(5):
        collector.add(98.0, 65.0, None)
    summary = collector.finalize()
    assert summary is not None
    assert summary.spo2 == 98.0
    assert summary.perfusion_index is None  # no valid PI readings
    assert summary.readings == 5


def test_event_data_shape():
    collector = ReadingCollector(measurement_id=4)
    for _ in range(5):
        collector.add(97.0, 70.0, 3.0)
    data = collector.finalize().as_event_data()
    for key in (
        "measurement_id", "spo2", "pulse_rate", "perfusion_index",
        "spo2_min", "spo2_max", "pulse_min", "pulse_max",
        "duration", "measured_at", "readings",
    ):
        assert key in data
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_measurement.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'custom_components.pulse_oximeter.measurement'`

**Step 3: Create `measurement.py`**

```python
"""Measurement session collection and summarizing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import median

from .const import SESSION_MIN_READINGS


@dataclass
class MeasurementSummary:
    """Summary of one finished measurement (finger-on to finger-off)."""

    measurement_id: int
    spo2: float
    pulse_rate: float
    perfusion_index: float | None
    spo2_min: float
    spo2_max: float
    pulse_min: float
    pulse_max: float
    started_at: datetime
    finished_at: datetime
    readings: int

    def as_event_data(self) -> dict:
        """Return a JSON-safe dict for events and attributes."""
        return {
            "measurement_id": self.measurement_id,
            "spo2": self.spo2,
            "pulse_rate": self.pulse_rate,
            "perfusion_index": self.perfusion_index,
            "spo2_min": self.spo2_min,
            "spo2_max": self.spo2_max,
            "pulse_min": self.pulse_min,
            "pulse_max": self.pulse_max,
            "duration": round(
                (self.finished_at - self.started_at).total_seconds(), 1
            ),
            "measured_at": self.finished_at.isoformat(),
            "readings": self.readings,
        }


class ReadingCollector:
    """Collects readings of one measurement in progress."""

    def __init__(self, measurement_id: int) -> None:
        self.measurement_id = measurement_id
        self.started_at = datetime.now(timezone.utc)
        self._spo2: list[float] = []
        self._pulse: list[float] = []
        self._pi: list[float] = []
        self._count = 0

    def add(
        self,
        spo2: float | None,
        pulse_rate: float | None,
        perfusion_index: float | None,
    ) -> None:
        """Add one notification's values; zeros and Nones are ignored."""
        valid = False
        if spo2 is not None and spo2 > 0:
            self._spo2.append(spo2)
            valid = True
        if pulse_rate is not None and pulse_rate > 0:
            self._pulse.append(pulse_rate)
            valid = True
        if perfusion_index is not None and perfusion_index > 0:
            self._pi.append(perfusion_index)
        if valid:
            self._count += 1

    def finalize(self) -> MeasurementSummary | None:
        """Build the summary; None if too few valid readings."""
        if (
            len(self._spo2) < SESSION_MIN_READINGS
            or len(self._pulse) < SESSION_MIN_READINGS
        ):
            return None
        return MeasurementSummary(
            measurement_id=self.measurement_id,
            spo2=round(median(self._spo2), 1),
            pulse_rate=round(median(self._pulse), 1),
            perfusion_index=(
                round(sum(self._pi) / len(self._pi), 2) if self._pi else None
            ),
            spo2_min=min(self._spo2),
            spo2_max=max(self._spo2),
            pulse_min=min(self._pulse),
            pulse_max=max(self._pulse),
            started_at=self.started_at,
            finished_at=datetime.now(timezone.utc),
            readings=self._count,
        )
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_measurement.py -v`
Expected: 4 passed

**Step 5: Commit**

```bash
git add custom_components/pulse_oximeter/measurement.py tests/test_measurement.py
git commit -m "Add measurement session collector with median summary"
```

---

## Task 4: Assignment state machine (pure logic, TDD)

**Files:**
- Create: `custom_components/pulse_oximeter/assignment.py`
- Test: `tests/test_assignment.py`

**Step 1: Write the failing tests**

```python
"""Tests for the assignment state machine."""

from datetime import datetime, timezone

import pytest

from custom_components.pulse_oximeter.assignment import FAULTY, AssignmentManager
from custom_components.pulse_oximeter.const import (
    EVENT_MEASUREMENT_ASSIGNED,
    EVENT_MEASUREMENT_FINISHED,
)
from custom_components.pulse_oximeter.measurement import MeasurementSummary

FRANK = "person.frank"
ANNEKE = "person.anneke"


def make_summary(measurement_id: int, spo2: float = 97.0) -> MeasurementSummary:
    now = datetime.now(timezone.utc)
    return MeasurementSummary(
        measurement_id=measurement_id,
        spo2=spo2,
        pulse_rate=72.0,
        perfusion_index=4.0,
        spo2_min=spo2 - 1,
        spo2_max=spo2 + 1,
        pulse_min=70.0,
        pulse_max=75.0,
        started_at=now,
        finished_at=now,
        readings=10,
    )


@pytest.fixture
def events():
    return []


@pytest.fixture
def manager(events):
    return AssignmentManager(
        participants=[FRANK, ANNEKE],
        fire_event=lambda name, data: events.append((name, data)),
    )


def test_new_measurement_is_unassigned(manager, events):
    manager.new_measurement(make_summary(1))
    assert manager.assigned_to is None
    assert manager.person_values == {}
    assert events[-1][0] == EVENT_MEASUREMENT_FINISHED
    assert events[-1][1]["measurement_id"] == 1


def test_claim_sets_person_values(manager, events):
    manager.new_measurement(make_summary(1))
    assert manager.assign(FRANK, measurement_id=1, source="notification")
    assert manager.assigned_to == FRANK
    assert manager.person_values[FRANK].spo2 == 97.0
    name, data = events[-1]
    assert name == EVENT_MEASUREMENT_ASSIGNED
    assert data["assigned_to"] == FRANK
    assert data["faulty"] is False
    assert data["source"] == "notification"


def test_reassign_reverts_previous_owner(manager):
    manager.new_measurement(make_summary(1))
    manager.assign(FRANK)
    manager.assign(ANNEKE)
    assert manager.assigned_to == ANNEKE
    assert FRANK not in manager.person_values  # had no earlier values
    assert manager.person_values[ANNEKE].spo2 == 97.0


def test_reassign_restores_older_values(manager):
    manager.new_measurement(make_summary(1, spo2=95.0))
    manager.assign(FRANK)
    manager.new_measurement(make_summary(2, spo2=99.0))
    manager.assign(FRANK)
    assert manager.person_values[FRANK].spo2 == 99.0
    manager.assign(ANNEKE)  # take measurement 2 away from Frank
    assert manager.person_values[FRANK].spo2 == 95.0  # back to measurement 1


def test_unassign(manager):
    manager.new_measurement(make_summary(1))
    manager.assign(FRANK)
    assert manager.assign(None)
    assert manager.assigned_to is None
    assert FRANK not in manager.person_values


def test_faulty_discards(manager, events):
    manager.new_measurement(make_summary(1))
    manager.assign(FRANK)
    assert manager.assign(FAULTY)
    assert manager.assigned_to == FAULTY
    assert FRANK not in manager.person_values
    assert events[-1][1]["faulty"] is True
    assert events[-1][1]["assigned_to"] is None


def test_stale_measurement_id_ignored(manager):
    manager.new_measurement(make_summary(1))
    manager.new_measurement(make_summary(2))
    assert not manager.assign(FRANK, measurement_id=1)
    assert manager.assigned_to is None


def test_unknown_person_rejected(manager):
    manager.new_measurement(make_summary(1))
    assert not manager.assign("person.stranger")


def test_assign_without_measurement_rejected(manager):
    assert not manager.assign(FRANK)


def test_listener_called_on_changes(manager):
    calls = []
    manager.add_listener(lambda: calls.append(1))
    manager.new_measurement(make_summary(1))
    manager.assign(FRANK)
    assert len(calls) == 2
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_assignment.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'custom_components.pulse_oximeter.assignment'`

**Step 3: Create `assignment.py`**

```python
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
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_assignment.py -v`
Expected: 10 passed

**Step 5: Commit**

```bash
git add custom_components/pulse_oximeter/assignment.py tests/test_assignment.py
git commit -m "Add assignment state machine for measurements"
```

---

## Task 5: Session detection in the coordinator

**Files:**
- Modify: `custom_components/pulse_oximeter/coordinator.py`

The coordinator already filters readings in `_update_from_measurement` (coordinator.py:342-361). Wire the collector into it; finalize on disconnect or 30 s inactivity.

**Step 1: Add imports and constructor state**

At the top of `coordinator.py` add to the imports:

```python
from collections.abc import Callable
from itertools import count

from homeassistant.helpers.event import async_call_later

from .const import SESSION_INACTIVITY_TIMEOUT
from .measurement import MeasurementSummary, ReadingCollector
```

In `OxiCoordinator.__init__` (after `self._expected_disconnect = False`):

```python
        self._collector: ReadingCollector | None = None
        self._measurement_ids = count(1)
        self._inactivity_unsub: Callable[[], None] | None = None
        self.session_callback: Callable[[MeasurementSummary], None] | None = None
```

**Step 2: Feed the collector from `_update_from_measurement`**

At the end of `_update_from_measurement` (before `self.async_set_updated_data(self.data)`):

```python
        has_valid = (spo2 is not None and spo2 > 0) or (pr is not None and pr > 0)
        if has_valid:
            if self._collector is None:
                self._collector = ReadingCollector(next(self._measurement_ids))
                _LOGGER.debug(
                    "Measurement %d started", self._collector.measurement_id
                )
            self._collector.add(spo2, pr, pi)
            self._reset_inactivity_timer()
```

**Step 3: Add the session methods** (new methods on `OxiCoordinator`):

```python
    def _reset_inactivity_timer(self) -> None:
        """(Re)start the timer that ends a session after silence."""
        if self._inactivity_unsub:
            self._inactivity_unsub()
        self._inactivity_unsub = async_call_later(
            self.hass, SESSION_INACTIVITY_TIMEOUT, self._on_inactivity
        )

    @callback
    def _on_inactivity(self, _now) -> None:
        self._inactivity_unsub = None
        self._finalize_session()

    @callback
    def _finalize_session(self) -> None:
        """End the current measurement session, if any."""
        if self._inactivity_unsub:
            self._inactivity_unsub()
            self._inactivity_unsub = None
        if self._collector is None:
            return
        summary = self._collector.finalize()
        self._collector = None
        if summary is None:
            _LOGGER.debug("Measurement discarded (too few valid readings)")
            return
        _LOGGER.info(
            "Measurement %d finished: SpO2 %.1f%%, pulse %.1f bpm (%d readings)",
            summary.measurement_id,
            summary.spo2,
            summary.pulse_rate,
            summary.readings,
        )
        if self.session_callback:
            self.session_callback(summary)
```

**Step 4: Finalize on disconnect and stop**

In `_on_disconnect`, after `self._client = None` add:

```python
        self._finalize_session()
```

In `async_stop`, before `await self._async_disconnect()` add:

```python
        self._finalize_session()
```

**Step 5: Verify all tests still pass and the module imports**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: all pass

Run: `.venv/bin/python -c "import ast; ast.parse(open('custom_components/pulse_oximeter/coordinator.py').read())"`
Expected: no output (syntax OK)

**Step 6: Commit**

```bash
git add custom_components/pulse_oximeter/coordinator.py
git commit -m "Detect measurement sessions in coordinator"
```

---

## Task 6: Notifier (TDD)

**Files:**
- Create: `custom_components/pulse_oximeter/notifier.py`
- Test: `tests/test_notifier.py`

**Step 1: Write the failing tests**

```python
"""Tests for actionable notifications."""

from datetime import datetime, timezone

import pytest
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.pulse_oximeter.assignment import FAULTY, AssignmentManager
from custom_components.pulse_oximeter.const import (
    MOBILE_APP_ACTION_EVENT,
    get_texts,
)
from custom_components.pulse_oximeter.measurement import MeasurementSummary
from custom_components.pulse_oximeter.notifier import (
    MeasurementNotifier,
    build_action_id,
    build_notification,
    parse_action_id,
)

FRANK = "person.frank"
ANNEKE = "person.anneke"


def make_summary(measurement_id: int = 1) -> MeasurementSummary:
    now = datetime.now(timezone.utc)
    return MeasurementSummary(
        measurement_id=measurement_id,
        spo2=97.0,
        pulse_rate=72.0,
        perfusion_index=4.0,
        spo2_min=96.0,
        spo2_max=98.0,
        pulse_min=70.0,
        pulse_max=75.0,
        started_at=now,
        finished_at=now,
        readings=10,
    )


def test_action_id_roundtrip():
    action = build_action_id("MINE", 42, FRANK)
    assert parse_action_id(action) == ("MINE", 42, FRANK)


def test_parse_rejects_garbage():
    assert parse_action_id("") is None
    assert parse_action_id("URLO|MINE|1|person.x") is None
    assert parse_action_id("PULSEOX|MINE|abc|person.x") is None
    assert parse_action_id("PULSEOX|MINE|1") is None


def test_build_notification_payload():
    payload = build_notification(
        make_summary(7), FRANK, "pulseox_entry1", get_texts("nl")
    )
    assert payload["title"] == "Saturatiemeter"
    assert "97.0" in payload["message"]
    assert "72" in payload["message"]
    assert payload["data"]["tag"] == "pulseox_entry1"
    actions = payload["data"]["actions"]
    assert len(actions) == 3  # exactly the Android maximum
    assert actions[0]["action"] == build_action_id("MINE", 7, FRANK)
    assert actions[0]["title"] == "Voor mij"
    assert actions[1]["title"] == "Niet voor mij"
    assert actions[2]["title"] == "Foutieve meting"


@pytest.fixture
def manager():
    return AssignmentManager(
        participants=[FRANK, ANNEKE], fire_event=lambda name, data: None
    )


async def test_send_and_claim_flow(hass, manager):
    frank_calls = async_mock_service(hass, "notify", "mobile_app_frank")
    anneke_calls = async_mock_service(hass, "notify", "mobile_app_anneke")
    notifier = MeasurementNotifier(
        hass,
        "entry1",
        manager,
        {FRANK: "mobile_app_frank", ANNEKE: "mobile_app_anneke"},
        enabled=True,
    )
    notifier.async_setup()

    summary = make_summary(1)
    manager.new_measurement(summary)
    await notifier.async_send(summary)
    assert len(frank_calls) == 1
    assert len(anneke_calls) == 1
    assert frank_calls[0].data["data"]["actions"][0]["action"] == build_action_id(
        "MINE", 1, FRANK
    )

    # Frank taps "Voor mij" -> assigned + cleared everywhere
    hass.bus.async_fire(
        MOBILE_APP_ACTION_EVENT, {"action": build_action_id("MINE", 1, FRANK)}
    )
    await hass.async_block_till_done()
    assert manager.assigned_to == FRANK
    assert frank_calls[-1].data["message"] == "clear_notification"
    assert anneke_calls[-1].data["message"] == "clear_notification"


async def test_not_mine_clears_only_own_phone(hass, manager):
    frank_calls = async_mock_service(hass, "notify", "mobile_app_frank")
    anneke_calls = async_mock_service(hass, "notify", "mobile_app_anneke")
    notifier = MeasurementNotifier(
        hass,
        "entry1",
        manager,
        {FRANK: "mobile_app_frank", ANNEKE: "mobile_app_anneke"},
        enabled=True,
    )
    notifier.async_setup()
    manager.new_measurement(make_summary(1))

    hass.bus.async_fire(
        MOBILE_APP_ACTION_EVENT, {"action": build_action_id("NOTMINE", 1, ANNEKE)}
    )
    await hass.async_block_till_done()
    assert manager.assigned_to is None  # nothing was assigned to Anneke
    assert len(anneke_calls) == 1  # her clear
    assert anneke_calls[-1].data["message"] == "clear_notification"
    assert len(frank_calls) == 0  # Frank keeps his notification


async def test_faulty_clears_everywhere(hass, manager):
    frank_calls = async_mock_service(hass, "notify", "mobile_app_frank")
    notifier = MeasurementNotifier(
        hass, "entry1", manager, {FRANK: "mobile_app_frank"}, enabled=True
    )
    notifier.async_setup()
    manager.new_measurement(make_summary(1))

    hass.bus.async_fire(
        MOBILE_APP_ACTION_EVENT, {"action": build_action_id("FAULTY", 1, FRANK)}
    )
    await hass.async_block_till_done()
    assert manager.assigned_to == FAULTY
    assert frank_calls[-1].data["message"] == "clear_notification"


async def test_stale_answer_ignored(hass, manager):
    async_mock_service(hass, "notify", "mobile_app_frank")
    notifier = MeasurementNotifier(
        hass, "entry1", manager, {FRANK: "mobile_app_frank"}, enabled=True
    )
    notifier.async_setup()
    manager.new_measurement(make_summary(1))
    manager.new_measurement(make_summary(2))

    hass.bus.async_fire(
        MOBILE_APP_ACTION_EVENT, {"action": build_action_id("MINE", 1, FRANK)}
    )
    await hass.async_block_till_done()
    assert manager.assigned_to is None


async def test_disabled_sends_nothing(hass, manager):
    frank_calls = async_mock_service(hass, "notify", "mobile_app_frank")
    notifier = MeasurementNotifier(
        hass, "entry1", manager, {FRANK: "mobile_app_frank"}, enabled=False
    )
    notifier.async_setup()
    summary = make_summary(1)
    manager.new_measurement(summary)
    await notifier.async_send(summary)
    assert len(frank_calls) == 0
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_notifier.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'custom_components.pulse_oximeter.notifier'`

**Step 3: Create `notifier.py`**

```python
"""Actionable notifications for measurement assignment."""

from __future__ import annotations

import logging

from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .assignment import FAULTY, AssignmentManager
from .const import MOBILE_APP_ACTION_EVENT, NOTIFY_ACTION_PREFIX, get_texts
from .measurement import MeasurementSummary

_LOGGER = logging.getLogger(__name__)


def build_action_id(kind: str, measurement_id: int, person: str) -> str:
    """Build a notification action ID encoding measurement and person."""
    return f"{NOTIFY_ACTION_PREFIX}|{kind}|{measurement_id}|{person}"


def parse_action_id(action: str) -> tuple[str, int, str] | None:
    """Parse an action ID; None if it is not ours."""
    parts = action.split("|")
    if len(parts) != 4 or parts[0] != NOTIFY_ACTION_PREFIX:
        return None
    try:
        return parts[1], int(parts[2]), parts[3]
    except ValueError:
        return None


def build_notification(
    summary: MeasurementSummary,
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
                    "action": build_action_id("MINE", mid, person),
                    "title": texts["mine"],
                },
                {
                    "action": build_action_id("NOTMINE", mid, person),
                    "title": texts["not_mine"],
                },
                {
                    "action": build_action_id("FAULTY", mid, person),
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
            payload = build_notification(summary, person, self.tag, texts)
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
        parsed = parse_action_id(event.data.get("action", ""))
        if parsed is None:
            return
        kind, measurement_id, person = parsed
        if person not in self.notify_targets:
            return

        if kind == "MINE":
            if self.manager.assign(person, measurement_id, source="notification"):
                await self.async_clear()
        elif kind == "FAULTY":
            if self.manager.assign(FAULTY, measurement_id, source="notification"):
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
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_notifier.py -v`
Expected: 8 passed

**Step 5: Commit**

```bash
git add custom_components/pulse_oximeter/notifier.py tests/test_notifier.py
git commit -m "Add actionable notification flow for assignment"
```

---

## Task 7: Runtime data container

**Files:**
- Create: `custom_components/pulse_oximeter/data.py`

**Step 1: Create `data.py`**

```python
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
```

**Step 2: Verify syntax**

Run: `.venv/bin/python -c "import ast; ast.parse(open('custom_components/pulse_oximeter/data.py').read())"`
Expected: no output

**Step 3: Commit**

```bash
git add custom_components/pulse_oximeter/data.py
git commit -m "Add runtime data container"
```

---

## Task 8: Options flow

**Files:**
- Modify: `custom_components/pulse_oximeter/config_flow.py`
- Test: `tests/test_options_flow.py`

**Step 1: Write the failing test**

```python
"""Tests for the options flow."""

from unittest.mock import patch

from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.pulse_oximeter.const import (
    CONF_NOTIFICATIONS_ENABLED,
    CONF_NOTIFY_TARGETS,
    CONF_PARTICIPANTS,
    DOMAIN,
)

FRANK = "person.frank"


async def test_options_flow_full(hass):
    async_mock_service(hass, "notify", "mobile_app_frank_phone")
    hass.states.async_set(
        FRANK, "home", {"device_trackers": ["device_tracker.frank_phone"]}
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="AA:BB:CC:DD:EE:FF",
        data={"address": "AA:BB:CC:DD:EE:FF", "name": "Test Oxi"},
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.pulse_oximeter.async_setup_entry", return_value=True
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "init"

        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                CONF_PARTICIPANTS: [FRANK],
                CONF_NOTIFICATIONS_ENABLED: True,
            },
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "targets"

        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={FRANK: "mobile_app_frank_phone"},
        )
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"] == {
            CONF_PARTICIPANTS: [FRANK],
            CONF_NOTIFY_TARGETS: {FRANK: "mobile_app_frank_phone"},
            CONF_NOTIFICATIONS_ENABLED: True,
        }


async def test_options_flow_no_participants_skips_targets(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="AA:BB:CC:DD:EE:FF",
        data={"address": "AA:BB:CC:DD:EE:FF", "name": "Test Oxi"},
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.pulse_oximeter.async_setup_entry", return_value=True
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={CONF_PARTICIPANTS: [], CONF_NOTIFICATIONS_ENABLED: True},
        )
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_NOTIFY_TARGETS] == {}
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_options_flow.py -v`
Expected: FAIL (no options flow handler registered)

**Step 3: Extend `config_flow.py`**

Add imports at the top:

```python
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector

from .const import (
    CONF_NOTIFICATIONS_ENABLED,
    CONF_NOTIFY_TARGETS,
    CONF_PARTICIPANTS,
    DOMAIN,
)
```

Add a module-level helper:

```python
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
```

Add to `OxiConfigFlow`:

```python
    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> "OxiOptionsFlow":
        """Create the options flow."""
        return OxiOptionsFlow()
```

Add the options flow class:

```python
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
            default = (
                existing.get(person)
                or suggest_notify_target(self.hass, person)
                or "none"
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
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_options_flow.py -v`
Expected: 2 passed

**Step 5: Commit**

```bash
git add custom_components/pulse_oximeter/config_flow.py tests/test_options_flow.py
git commit -m "Add options flow for participants and notify targets"
```

---

## Task 9: Per-person sensors

**Files:**
- Modify: `custom_components/pulse_oximeter/sensor.py`
- Test: `tests/test_person_sensor.py`

**Step 1: Write the failing test** (logic-level, no full HA setup)

```python
"""Tests for per-person sensors."""

from datetime import datetime, timezone
from types import SimpleNamespace

from custom_components.pulse_oximeter.assignment import AssignmentManager
from custom_components.pulse_oximeter.measurement import MeasurementSummary
from custom_components.pulse_oximeter.sensor import (
    PERSON_SENSOR_DESCRIPTIONS,
    PersonOxiSensor,
)

FRANK = "person.frank"


def make_summary() -> MeasurementSummary:
    now = datetime.now(timezone.utc)
    return MeasurementSummary(
        measurement_id=1,
        spo2=97.0,
        pulse_rate=72.0,
        perfusion_index=4.0,
        spo2_min=96.0,
        spo2_max=98.0,
        pulse_min=70.0,
        pulse_max=75.0,
        started_at=now,
        finished_at=now,
        readings=10,
    )


def make_sensor(manager, key: str) -> PersonOxiSensor:
    description = next(d for d in PERSON_SENSOR_DESCRIPTIONS if d.key == key)
    entry = SimpleNamespace(unique_id="AA:BB", entry_id="entry1")
    return PersonOxiSensor(
        manager=manager,
        entry=entry,
        person_entity_id=FRANK,
        person_name="Frank",
        device_name="Pulse Oximeter",
        coordinator_address="AA:BB",
        description=description,
    )


def test_person_sensor_descriptions_exclude_battery():
    keys = {d.key for d in PERSON_SENSOR_DESCRIPTIONS}
    assert keys == {"spo2", "pulse_rate", "perfusion_index"}


def test_unclaimed_sensor_has_no_value():
    manager = AssignmentManager([FRANK], lambda n, d: None)
    sensor = make_sensor(manager, "spo2")
    assert sensor.native_value is None
    assert sensor.available is False


def test_claimed_values_shown():
    manager = AssignmentManager([FRANK], lambda n, d: None)
    manager.new_measurement(make_summary())
    manager.assign(FRANK)
    spo2 = make_sensor(manager, "spo2")
    pulse = make_sensor(manager, "pulse_rate")
    assert spo2.native_value == 97.0
    assert pulse.native_value == 72.0
    assert spo2.extra_state_attributes["readings"] == 10
    assert spo2.unique_id == "AA:BB_frank_spo2"


def test_falls_back_to_restored_value():
    manager = AssignmentManager([FRANK], lambda n, d: None)
    sensor = make_sensor(manager, "spo2")
    sensor._restored_value = 95.0
    sensor._restored_attrs = {"readings": 3}
    assert sensor.native_value == 95.0
    assert sensor.extra_state_attributes == {"readings": 3}
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_person_sensor.py -v`
Expected: FAIL — `ImportError` (PersonOxiSensor does not exist)

**Step 3: Extend `sensor.py`**

Add imports:

```python
from homeassistant.components.sensor import RestoreSensor
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.util import slugify

from .assignment import AssignmentManager
from .const import CONF_PARTICIPANTS, DOMAIN, SIGNAL_ASSIGNMENT_UPDATE
from .data import RuntimeData, person_display_name
```

Add below `SENSOR_DESCRIPTIONS`:

```python
PERSON_SENSOR_DESCRIPTIONS = [
    description
    for description in SENSOR_DESCRIPTIONS
    if description.key in ("spo2", "pulse_rate", "perfusion_index")
]

PERSON_ATTR_KEYS = (
    "measured_at",
    "duration",
    "spo2_min",
    "spo2_max",
    "pulse_min",
    "pulse_max",
    "readings",
)
```

Replace the body of `async_setup_entry` with:

```python
    data: RuntimeData = hass.data[DOMAIN][entry.entry_id]
    coordinator = data.coordinator

    entities: list[SensorEntity] = [
        OxiSensor(coordinator, entry, description)
        for description in SENSOR_DESCRIPTIONS
    ]

    for person_entity_id in entry.options.get(CONF_PARTICIPANTS, []):
        person_name = person_display_name(hass, person_entity_id)
        entities.extend(
            PersonOxiSensor(
                manager=data.manager,
                entry=entry,
                person_entity_id=person_entity_id,
                person_name=person_name,
                device_name=coordinator.device_name,
                coordinator_address=coordinator.address,
                description=description,
            )
            for description in PERSON_SENSOR_DESCRIPTIONS
        )

    async_add_entities(entities)
```

Add the new class at the bottom:

```python
class PersonOxiSensor(RestoreSensor):
    """Last claimed measurement value for one person."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        manager: AssignmentManager,
        entry: ConfigEntry,
        person_entity_id: str,
        person_name: str,
        device_name: str,
        coordinator_address: str,
        description: SensorEntityDescription,
    ) -> None:
        """Initialize the person sensor."""
        self.entity_description = description
        self._manager = manager
        self._entry = entry
        self._person_entity_id = person_entity_id
        self._person_name = person_name
        self._device_name = device_name
        self._coordinator_address = coordinator_address
        person_slug = slugify(person_entity_id.split(".", 1)[1])
        self._attr_unique_id = f"{entry.unique_id}_{person_slug}_{description.key}"
        self._person_slug = person_slug
        self._restored_value: float | None = None
        self._restored_attrs: dict = {}

    @property
    def device_info(self) -> DeviceInfo:
        """One virtual device per person, linked to the oximeter."""
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.entry_id}_{self._person_slug}")},
            name=f"{self._device_name} {self._person_name}",
            via_device=(DOMAIN, self._coordinator_address),
        )

    async def async_added_to_hass(self) -> None:
        """Restore last values and subscribe to assignment updates."""
        await super().async_added_to_hass()
        if (data := await self.async_get_last_sensor_data()) is not None:
            self._restored_value = data.native_value
        if (state := await self.async_get_last_state()) is not None:
            self._restored_attrs = {
                key: value
                for key, value in state.attributes.items()
                if key in PERSON_ATTR_KEYS
            }
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
    def native_value(self) -> float | None:
        """Claimed value, falling back to the restored value."""
        values = self._manager.person_values.get(self._person_entity_id)
        if values is not None:
            return getattr(values, self.entity_description.key)
        return self._restored_value

    @property
    def available(self) -> bool:
        """Available once the person ever claimed a measurement."""
        return self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict | None:
        """Measurement details of the last claimed measurement."""
        values = self._manager.person_values.get(self._person_entity_id)
        if values is not None:
            return values.attributes
        return self._restored_attrs or None
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_person_sensor.py tests/ -v`
Expected: all pass

**Step 5: Commit**

```bash
git add custom_components/pulse_oximeter/sensor.py tests/test_person_sensor.py
git commit -m "Add per-person sensors for claimed measurements"
```

---

## Task 10: Select entity

**Files:**
- Create: `custom_components/pulse_oximeter/select.py`
- Test: `tests/test_select.py`

**Step 1: Write the failing test**

```python
"""Tests for the assignment select entity."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from custom_components.pulse_oximeter.assignment import FAULTY, AssignmentManager
from custom_components.pulse_oximeter.const import get_texts
from custom_components.pulse_oximeter.measurement import MeasurementSummary
from custom_components.pulse_oximeter.select import OxiAssignSelect

FRANK = "person.frank"
ANNEKE = "person.anneke"


def make_summary() -> MeasurementSummary:
    now = datetime.now(timezone.utc)
    return MeasurementSummary(
        measurement_id=1,
        spo2=97.0,
        pulse_rate=72.0,
        perfusion_index=None,
        spo2_min=96.0,
        spo2_max=98.0,
        pulse_min=70.0,
        pulse_max=75.0,
        started_at=now,
        finished_at=now,
        readings=10,
    )


@pytest.fixture
def manager():
    return AssignmentManager([FRANK, ANNEKE], lambda n, d: None)


@pytest.fixture
def select(manager):
    return OxiAssignSelect(
        manager=manager,
        entry=SimpleNamespace(unique_id="AA:BB", entry_id="entry1"),
        coordinator_address="AA:BB",
        device_name="Pulse Oximeter",
        person_names={FRANK: "Frank", ANNEKE: "Anneke"},
        texts=get_texts("nl"),
    )


def test_options(select):
    assert select.options == [
        "Niet toegewezen",
        "Frank",
        "Anneke",
        "Foutieve meting",
    ]


def test_current_option_follows_manager(select, manager):
    assert select.current_option == "Niet toegewezen"
    manager.new_measurement(make_summary())
    manager.assign(FRANK)
    assert select.current_option == "Frank"
    manager.assign(FAULTY)
    assert select.current_option == "Foutieve meting"


async def test_select_option_assigns(select, manager):
    manager.new_measurement(make_summary())
    await select.async_select_option("Anneke")
    assert manager.assigned_to == ANNEKE
    await select.async_select_option("Foutieve meting")
    assert manager.assigned_to == FAULTY
    await select.async_select_option("Niet toegewezen")
    assert manager.assigned_to is None
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_select.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'custom_components.pulse_oximeter.select'`

**Step 3: Create `select.py`**

```python
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
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_select.py -v`
Expected: 3 passed

**Step 5: Commit**

```bash
git add custom_components/pulse_oximeter/select.py tests/test_select.py
git commit -m "Add select entity for measurement assignment"
```

---

## Task 11: Wire everything in `__init__.py` + service

**Files:**
- Modify: `custom_components/pulse_oximeter/__init__.py`
- Create: `custom_components/pulse_oximeter/services.yaml`

**Step 1: Replace `__init__.py`**

```python
"""The Pulse Oximeter (BLE) integration."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, callback
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
        hass.async_create_task(notifier.async_send(summary))

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
        for data in hass.data.get(DOMAIN, {}).values():
            data.manager.assign(target, source="service")

    hass.services.async_register(
        DOMAIN, SERVICE_ASSIGN_MEASUREMENT, _handle_assign, schema=ASSIGN_SCHEMA
    )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    data: RuntimeData = hass.data[DOMAIN][entry.entry_id]
    data.notifier.async_unload()
    await data.coordinator.async_stop()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
```

**Step 2: Create `services.yaml`**

```yaml
assign_measurement:
  name: Assign measurement
  description: >-
    Assign the latest measurement to a person, unassign it, or mark it as
    faulty.
  fields:
    person:
      name: Person
      description: Person to assign the latest measurement to. Leave empty to unassign.
      required: false
      selector:
        entity:
          domain: person
    faulty:
      name: Faulty measurement
      description: Discard the latest measurement as faulty.
      required: false
      default: false
      selector:
        boolean:
```

**Step 3: Run the full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: all pass

**Step 4: Commit**

```bash
git add custom_components/pulse_oximeter/__init__.py custom_components/pulse_oximeter/services.yaml
git commit -m "Wire assignment manager, notifier and service into setup"
```

---

## Task 12: Translations and manifest

**Files:**
- Modify: `custom_components/pulse_oximeter/strings.json`
- Modify: `custom_components/pulse_oximeter/translations/en.json`
- Create: `custom_components/pulse_oximeter/translations/nl.json`
- Modify: `custom_components/pulse_oximeter/manifest.json`

**Step 1: Add options + services sections to `strings.json`**

Merge into the existing JSON (keep the current `config` block):

```json
{
  "title": "Pulse Oximeter (BLE)",
  "config": {
    "step": {
      "bluetooth_confirm": {
        "description": "Set up {name} as a pulse oximeter?"
      }
    },
    "abort": {
      "already_configured": "Device is already configured",
      "bluetooth_only": "This integration can only be set up via Bluetooth discovery. Make sure your pulse oximeter is on and within range."
    }
  },
  "options": {
    "step": {
      "init": {
        "title": "Measurement assignment",
        "data": {
          "participants": "Participants (persons who use this device)",
          "notifications_enabled": "Send assignment notifications"
        }
      },
      "targets": {
        "title": "Notification targets",
        "description": "Pick the phone (mobile_app notify service) for each participant."
      }
    }
  },
  "services": {
    "assign_measurement": {
      "name": "Assign measurement",
      "description": "Assign the latest measurement to a person, unassign it, or mark it as faulty.",
      "fields": {
        "person": {
          "name": "Person",
          "description": "Person to assign the latest measurement to. Leave empty to unassign."
        },
        "faulty": {
          "name": "Faulty measurement",
          "description": "Discard the latest measurement as faulty."
        }
      }
    }
  }
}
```

**Step 2: Mirror the same structure in `translations/en.json`** (same content as `strings.json`).

**Step 3: Create `translations/nl.json`**

```json
{
  "title": "Saturatiemeter (BLE)",
  "config": {
    "step": {
      "bluetooth_confirm": {
        "description": "{name} instellen als saturatiemeter?"
      }
    },
    "abort": {
      "already_configured": "Apparaat is al geconfigureerd",
      "bluetooth_only": "Deze integratie kan alleen via Bluetooth-detectie worden ingesteld. Zorg dat de saturatiemeter aan staat en binnen bereik is."
    }
  },
  "options": {
    "step": {
      "init": {
        "title": "Metingen toewijzen",
        "data": {
          "participants": "Deelnemers (personen die dit apparaat gebruiken)",
          "notifications_enabled": "Toewijzingsnotificaties versturen"
        }
      },
      "targets": {
        "title": "Notificatiedoelen",
        "description": "Kies per deelnemer de telefoon (mobile_app notify-service)."
      }
    }
  },
  "services": {
    "assign_measurement": {
      "name": "Meting toewijzen",
      "description": "Wijs de laatste meting toe aan een persoon, koppel hem los of markeer hem als foutief.",
      "fields": {
        "person": {
          "name": "Persoon",
          "description": "Persoon om de laatste meting aan toe te wijzen. Leeg laten om los te koppelen."
        },
        "faulty": {
          "name": "Foutieve meting",
          "description": "Gooi de laatste meting weg als foutief."
        }
      }
    }
  }
}
```

**Step 4: Update `manifest.json`**

- Bump `"version"` to `"1.1.0"`
- Add `"person"` to `"dependencies"` (becomes `["bluetooth_adapters", "person"]`)

**Step 5: Validate JSON files**

Run:
```bash
for f in custom_components/pulse_oximeter/strings.json custom_components/pulse_oximeter/translations/en.json custom_components/pulse_oximeter/translations/nl.json custom_components/pulse_oximeter/manifest.json; do .venv/bin/python -m json.tool "$f" > /dev/null && echo "OK $f"; done
```
Expected: four `OK` lines

**Step 6: Run the full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: all pass

**Step 7: Commit**

```bash
git add custom_components/pulse_oximeter/strings.json custom_components/pulse_oximeter/translations/ custom_components/pulse_oximeter/manifest.json
git commit -m "Add translations and bump version to 1.1.0"
```

---

## Task 13: README + manual verification

**Files:**
- Modify: `README.md`

**Step 1: Add a "Assigning measurements to persons" section to README.md** (after "How it works"):

```markdown
## Assigning measurements to persons

The oximeter itself does not know who is wearing it. This integration links
measurements to Home Assistant persons via explicit claiming:

1. Open the integration's **Options** and select the participating persons,
   plus the phone (companion app) of each participant.
2. After each measurement (finger on → finger off) every configured phone
   receives an actionable notification: *"Measurement 14:32 — SpO2 97%,
   pulse 72 bpm. Yours?"* with three buttons: **Mine**, **Not mine**, and
   **Faulty measurement**.
3. Tapping **Mine** assigns the measurement to you: your personal sensors
   (e.g. `sensor.pulse_oximeter_frank_spo2`) update and build per-person
   history. The notification disappears on all phones.
4. **Not mine** dismisses it on your phone only; **Faulty measurement**
   discards it for everyone.
5. Unclaimed measurements enter no one's history. The
   **Last measurement** select entity on the device page lets you (re)assign
   the latest measurement from the dashboard at any time.

For custom automations: the integration fires
`pulse_oximeter_measurement_finished` and
`pulse_oximeter_measurement_assigned` events and provides the
`pulse_oximeter.assign_measurement` service. Built-in notifications can be
disabled in the options.
```

**Step 2: Commit**

```bash
git add README.md
git commit -m "Document person assignment in README"
```

**Step 3: Manual verification on real Home Assistant** (requires the Medisana device):

1. Copy `custom_components/pulse_oximeter` to a HA instance (or restart if symlinked)
2. Open the integration → **Configure**: select yourself as participant, verify your phone is pre-filled, save
3. Verify new entities exist: per-person device with 3 sensors + "Laatste meting" select on the oximeter device
4. Take a measurement (finger on ~30 s, finger off)
5. Verify notification arrives with 3 buttons within ~5 s of the device disconnecting
6. Tap **Voor mij** → person sensors update, notification disappears
7. Take another measurement, tap **Foutieve meting** → no sensors change, select shows "Foutieve meting"
8. Take another measurement, ignore the notification → select shows "Niet toegewezen"; assign via the select → sensors update
9. Restart HA → person sensors keep their values
10. Check the log for warnings/errors

---

## Out of scope (YAGNI)

- Historical measurement log/storage beyond sensor history
- Default person / auto-assignment (explicitly rejected in design)
- Per-user notification text customization
- Multiple oximeter support for the service call (assigns on all entries; fine for v1)
