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


def test_pulse_only_readings_do_not_qualify():
    collector = ReadingCollector(measurement_id=5)
    for _ in range(6):
        collector.add(None, 70.0, None)  # pulse only
    assert collector.finalize() is None
