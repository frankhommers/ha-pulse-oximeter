"""Tests for coordinator session detection."""

from unittest.mock import MagicMock

from custom_components.pulse_oximeter.coordinator import OxiCoordinator
from custom_components.pulse_oximeter.measurement import MeasurementSummary


def make_coordinator(hass):
    coordinator = OxiCoordinator(hass, "AA:BB:CC:DD:EE:FF", "Test Oxi")
    coordinator.session_callback = MagicMock()
    return coordinator


async def test_valid_readings_produce_summary_on_disconnect(hass):
    coordinator = make_coordinator(hass)
    for _ in range(6):
        coordinator._update_from_measurement(
            {"spo2": 97.0, "pulse_rate": 72.0, "pi": 4.0}
        )
    assert coordinator._collector is not None  # session started
    coordinator._finalize_session()
    coordinator.session_callback.assert_called_once()
    summary = coordinator.session_callback.call_args[0][0]
    assert isinstance(summary, MeasurementSummary)
    assert summary.spo2 == 97.0
    assert coordinator._collector is None  # session closed


async def test_too_few_readings_discarded(hass):
    coordinator = make_coordinator(hass)
    for _ in range(3):
        coordinator._update_from_measurement({"spo2": 96.0, "pulse_rate": 70.0})
    coordinator._finalize_session()
    coordinator.session_callback.assert_not_called()


async def test_zero_readings_do_not_start_session(hass):
    coordinator = make_coordinator(hass)
    coordinator._update_from_measurement({"spo2": 0.0, "pulse_rate": 0.0})
    assert coordinator._collector is None


async def test_disconnect_finalizes_session(hass):
    coordinator = make_coordinator(hass)
    for _ in range(6):
        coordinator._update_from_measurement({"spo2": 95.0, "pulse_rate": 65.0})
    coordinator._on_disconnect(MagicMock())
    coordinator.session_callback.assert_called_once()


async def test_double_finalize_is_noop(hass):
    coordinator = make_coordinator(hass)
    for _ in range(6):
        coordinator._update_from_measurement({"spo2": 95.0, "pulse_rate": 65.0})
    coordinator._finalize_session()
    coordinator._finalize_session()  # second call must be a no-op
    coordinator.session_callback.assert_called_once()
