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
