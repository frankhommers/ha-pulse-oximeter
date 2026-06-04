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
