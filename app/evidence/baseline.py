"""Descriptive rolling baselines; these values are not diagnostic predictions."""
from __future__ import annotations

import math
import statistics
from collections import deque
from typing import Iterable


class RollingBaseline:
    def __init__(self, *, window: int = 100, minimum_scans: int = 5):
        if window < 1 or minimum_scans < 2 or minimum_scans > window:
            raise ValueError("baseline window must hold at least minimum_scans >= 2")
        self._window = window
        self._minimum_scans = minimum_scans
        self._values: dict[str, deque[float]] = {}

    def add(self, measurements: dict[str, float | int | None]) -> None:
        for name, value in measurements.items():
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                continue
            self._values.setdefault(name, deque(maxlen=self._window)).append(float(value))

    def deviations(self, measurements: dict[str, float | int | None]) -> dict:
        result = {}
        for name, value in measurements.items():
            history = self._values.get(name, ())
            if len(history) < self._minimum_scans:
                result[name] = {"status": "insufficient_evidence", "reason": "baseline_not_ready", "z_score": None}
                continue
            if value is None or isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                result[name] = {"status": "insufficient_evidence", "reason": "measurement_missing_or_invalid", "z_score": None}
                continue
            center = statistics.median(history)
            mad = statistics.median(abs(sample - center) for sample in history)
            if mad == 0:
                result[name] = {"status": "insufficient_evidence", "reason": "baseline_variation_zero", "z_score": None}
                continue
            result[name] = {
                "status": "available", "z_score": (float(value) - center) / (1.4826 * mad),
                "label": "descriptive_deviation_from_own_baseline_non_diagnostic",
                "baseline_median": center, "baseline_mad": mad, "baseline_scans": len(history),
            }
        return {"schema_version": "1.0", "interpretation": "descriptive_non_diagnostic", "measurements": result}

    def load_history(self, values: dict[str, Iterable[float]]) -> None:
        """Hydrate validated feature history from an owner-controlled persistence layer."""
        for name, series in values.items():
            for value in series:
                self.add({name: value})
