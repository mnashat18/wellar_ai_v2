"""Non-operational adapter boundary for experimental facial drowsiness models.

This module intentionally does not load model files. A reviewed, trusted backend
must be injected by test-only application code, and both explicit opt-in flags
must be set. Outputs are uncalibrated model scores and are never translated into
sleepiness, fatigue, vigilance, fitness, or risk results.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import numpy as np

EXPERIMENTAL_ADAPTER_VERSION = "wellar-experimental-face-drowsiness/1.0"
MAX_FRAMES = 30
MAX_FRAME_PIXELS = 2_000_000
MAX_BATCH_BYTES = 16 * 1024 * 1024


class ExperimentalFrameBackend(Protocol):
    """Trusted in-process model wrapper; artifact deserialization is out of scope."""

    model_revision: str

    def predict_drowsy_score(self, rgb_frames: Sequence[np.ndarray]) -> Sequence[float]: ...


@dataclass(frozen=True)
class FrameSample:
    rgb: np.ndarray
    quality_status: Literal["passed", "failed"]
    face_visible: bool
    low_light: bool


@dataclass(frozen=True)
class ExperimentalResult:
    schema_version: str
    status: Literal["unavailable", "abstained", "experimental"]
    raw_drowsy_score: float | None
    calibrated: bool
    model_revision: str | None
    reason: str | None
    usable_frame_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "raw_drowsy_score": self.raw_drowsy_score,
            "calibrated": self.calibrated,
            "model_revision": self.model_revision,
            "reason": self.reason,
            "usable_frame_count": self.usable_frame_count,
            "claim_scope": "experimental_driver_drowsiness_only_not_validated_workplace_measurement",
        }


class ExperimentalDrowsinessAdapter:
    """Bounded test-only adapter; cannot activate V2 operational prediction."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        non_production: bool = False,
        backend: ExperimentalFrameBackend | None = None,
        max_temporal_mad: float = 0.15,
    ) -> None:
        if isinstance(max_temporal_mad, bool) or not isinstance(max_temporal_mad, (int, float)) or not math.isfinite(max_temporal_mad) or not 0 <= max_temporal_mad <= 0.5:
            raise ValueError("temporal disagreement bound is invalid")
        self.enabled = enabled is True
        self.non_production = non_production is True
        self.backend = backend
        self.max_temporal_mad = float(max_temporal_mad)

    def predict(
        self,
        samples: Sequence[FrameSample],
        *,
        input_kind: Literal["image", "video"],
    ) -> ExperimentalResult:
        if not self.enabled or not self.non_production or self.backend is None:
            return self._result("unavailable", "experimental_activation_disabled")
        if input_kind not in {"image", "video"} or not isinstance(samples, Sequence) or isinstance(samples, (str, bytes)):
            return self._result("abstained", "invalid_input")
        if not samples or len(samples) > MAX_FRAMES or (input_kind == "image" and len(samples) != 1):
            return self._result("abstained", "frame_count_out_of_bounds")

        accepted: list[np.ndarray] = []
        byte_count = 0
        for sample in samples:
            if not isinstance(sample, FrameSample):
                return self._result("abstained", "invalid_frame")
            if sample.quality_status != "passed" or sample.face_visible is not True or sample.low_light is not False:
                continue
            frame = sample.rgb
            if not isinstance(frame, np.ndarray) or frame.dtype != np.uint8 or frame.ndim != 3 or frame.shape[2] != 3:
                return self._result("abstained", "invalid_frame")
            height, width = frame.shape[:2]
            if height < 1 or width < 1 or height * width > MAX_FRAME_PIXELS:
                return self._result("abstained", "frame_resource_limit")
            byte_count += int(frame.nbytes)
            if byte_count > MAX_BATCH_BYTES:
                return self._result("abstained", "batch_resource_limit")
            accepted.append(frame)

        minimum = 1 if input_kind == "image" else 3
        if len(accepted) < minimum:
            return self._result("abstained", "insufficient_quality_frames")

        try:
            scores = self.backend.predict_drowsy_score(accepted)
            if not isinstance(scores, Sequence) or isinstance(scores, (str, bytes)) or len(scores) != len(accepted):
                return self._result("abstained", "invalid_model_output", len(accepted))
            values = [self._score(value) for value in scores]
        except Exception:
            # Do not expose backend exception text, paths, or model internals.
            return self._result("abstained", "inference_failed", len(accepted))

        median = float(np.median(np.asarray(values, dtype=np.float64)))
        mad = float(np.median(np.abs(np.asarray(values, dtype=np.float64) - median)))
        if input_kind == "video" and mad > self.max_temporal_mad:
            return self._result("abstained", "temporal_disagreement", len(accepted))
        return ExperimentalResult(
            EXPERIMENTAL_ADAPTER_VERSION,
            "experimental",
            median,
            False,
            self._safe_revision(),
            None,
            len(accepted),
        )

    @staticmethod
    def _score(value: Any) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError("invalid model score")
        return float(value)

    def _safe_revision(self) -> str | None:
        revision = getattr(self.backend, "model_revision", None)
        if isinstance(revision, str) and 1 <= len(revision) <= 80 and all(c.isalnum() or c in "._-" for c in revision):
            return revision
        return None

    @staticmethod
    def _result(status: Literal["unavailable", "abstained"], reason: str, usable: int = 0) -> ExperimentalResult:
        return ExperimentalResult(EXPERIMENTAL_ADAPTER_VERSION, status, None, False, None, reason, usable)
