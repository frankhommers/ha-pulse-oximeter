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
