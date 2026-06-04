"""Smoke tests for integration setup wiring."""

from contextlib import contextmanager
from unittest.mock import patch

import pytest
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.pulse_oximeter.const import (
    CONF_NOTIFICATIONS_ENABLED,
    CONF_NOTIFY_TARGETS,
    CONF_PARTICIPANTS,
    DOMAIN,
    SERVICE_ASSIGN_MEASUREMENT,
)
from custom_components.pulse_oximeter.data import RuntimeData

FRANK = "person.frank"


@contextmanager
def _patch_ble():
    """Patch the BLE connect/disconnect away for the whole test body."""
    with (
        patch(
            "custom_components.pulse_oximeter.coordinator.OxiCoordinator.async_start"
        ),
        patch(
            "custom_components.pulse_oximeter.coordinator.OxiCoordinator.async_stop"
        ),
    ):
        yield


async def _setup_entry(hass) -> MockConfigEntry:
    async_mock_service(hass, "notify", "mobile_app_frank")
    hass.states.async_set(FRANK, "home", {"friendly_name": "Frank"})
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="AA:BB:CC:DD:EE:FF",
        data={"address": "AA:BB:CC:DD:EE:FF", "name": "Test Oxi"},
        options={
            CONF_PARTICIPANTS: [FRANK],
            CONF_NOTIFY_TARGETS: {FRANK: "mobile_app_frank"},
            CONF_NOTIFICATIONS_ENABLED: True,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_setup_creates_runtime_data_and_entities(hass):
    with _patch_ble():
        entry = await _setup_entry(hass)
        data = hass.data[DOMAIN][entry.entry_id]
        assert isinstance(data, RuntimeData)
        assert hass.services.has_service(DOMAIN, SERVICE_ASSIGN_MEASUREMENT)
        # The select entity name comes from TEXTS["en"] -> "Last measurement".
        assert hass.states.get("select.test_oxi_last_measurement") is not None
        # Per-person sensors exist (spo2 / pulse_rate / perfusion_index).
        sensor_ids = hass.states.async_entity_ids("sensor")
        assert any("frank" in eid for eid in sensor_ids)


async def test_service_routes_to_manager(hass):
    with _patch_ble():
        entry = await _setup_entry(hass)
        data: RuntimeData = hass.data[DOMAIN][entry.entry_id]
        # No current measurement -> assign returns False silently, no exception.
        await hass.services.async_call(
            DOMAIN, SERVICE_ASSIGN_MEASUREMENT, {"person": FRANK}, blocking=True
        )
        assert data.manager.assigned_to is None  # nothing to assign yet


async def test_service_rejects_unknown_person(hass):
    with _patch_ble():
        await _setup_entry(hass)
        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_ASSIGN_MEASUREMENT,
                {"person": "person.stranger"},
                blocking=True,
            )


async def test_unload_cleans_up(hass):
    with _patch_ble():
        entry = await _setup_entry(hass)
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.entry_id not in hass.data.get(DOMAIN, {})
