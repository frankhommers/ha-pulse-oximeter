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


def test_same_target_is_noop_without_events(manager, events):
    manager.new_measurement(make_summary(1))
    assert manager.assign(FRANK)
    events_before = len(events)
    assert manager.assign(FRANK)  # same target again
    assert len(events) == events_before  # no duplicate events


def test_faulty_then_reclaim(manager):
    manager.new_measurement(make_summary(1))
    manager.assign(FAULTY)
    assert manager.assign(FRANK)
    assert manager.assigned_to == FRANK
    assert manager.person_values[FRANK].spo2 == 97.0


def test_source_defaults_to_service(manager, events):
    manager.new_measurement(make_summary(1))
    manager.assign(FRANK)
    assert events[-1][1]["source"] == "service"
