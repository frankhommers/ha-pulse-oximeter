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
