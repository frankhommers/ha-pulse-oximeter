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
