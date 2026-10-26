"""Synthetic engineering tests for video extraction plus rule indicators.

These generated frames and deterministic landmark providers are pipeline
fixtures only. They are not model-quality or accuracy evidence.
"""
from __future__ import annotations

from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from app.media.extractors import extract_video
from app.rule_based_indicators import evaluate
from tests.media_fixtures import synthetic_face_image_bytes


def _landmarks(closed: bool):
    points = [SimpleNamespace(x=0.5, y=0.5) for _ in range(478)]
    # Stable, single-face geometry with enough extent for the extractor's
    # spatial continuity check. These coordinates are synthetic by design.
    points[10] = SimpleNamespace(x=0.5, y=0.2)
    points[152] = SimpleNamespace(x=0.5, y=0.8)
    points[1] = SimpleNamespace(x=0.5, y=0.5)
    eyes = (
        (33, 160, 158, 133, 153, 144, 159, 145, 470, 472, 468, 33, 133),
        (362, 385, 387, 263, 373, 380, 386, 374, 475, 477, 473, 362, 263),
    )
    for outer, upper_a, upper_b, inner, lower_b, lower_a, upper, lower, iris_top, iris_bottom, iris_center, _, _ in eyes:
        points[outer] = SimpleNamespace(x=0.25 if outer == 33 else 0.75, y=0.4)
        points[inner] = SimpleNamespace(x=0.45 if outer == 33 else 0.55, y=0.4)
        opening = 0.004 if closed else 0.03
        points[upper_a] = SimpleNamespace(x=0.3, y=0.4 - opening / 2)
        points[upper_b] = SimpleNamespace(x=0.38, y=0.4 - opening / 2)
        points[lower_b] = SimpleNamespace(x=0.38, y=0.4 + opening / 2)
        points[lower_a] = SimpleNamespace(x=0.3, y=0.4 + opening / 2)
        points[upper] = SimpleNamespace(x=0.4, y=0.4 - opening / 2)
        points[lower] = SimpleNamespace(x=0.4, y=0.4 + opening / 2)
        points[iris_top] = SimpleNamespace(x=0.4, y=0.36)
        points[iris_bottom] = SimpleNamespace(x=0.4, y=0.44)
        points[iris_center] = SimpleNamespace(x=0.4, y=0.4)
    points[13] = SimpleNamespace(x=0.4, y=0.62)
    points[14] = SimpleNamespace(x=0.4, y=0.65)
    points[78] = SimpleNamespace(x=0.3, y=0.63)
    points[308] = SimpleNamespace(x=0.5, y=0.63)
    return points


class _SyntheticMesh:
    def __init__(self, mode: str):
        self.mode = mode
        self.landmarks = _landmarks(mode == "closed")

    def process(self, _frame):
        faces = [] if self.mode == "no_face" else [SimpleNamespace(landmark=self.landmarks)]
        return SimpleNamespace(multi_face_landmarks=faces)


def _write_video(path, *, mode: str, dark: bool = False):
    pytest.importorskip("PIL.Image")
    from PIL import Image
    import io

    frame = cv2.cvtColor(
        np.asarray(Image.open(io.BytesIO(synthetic_face_image_bytes())).convert("RGB")),
        cv2.COLOR_RGB2BGR,
    )
    if dark:
        frame = (frame.astype(np.float32) * 0.08).astype(np.uint8)
    height, width = frame.shape[:2]
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 4, (width, height))
    if not writer.isOpened():
        pytest.skip("OpenCV MJPG writer is unavailable in this environment")
    for _ in range(16):
        writer.write(frame)
    writer.release()
    return path


def _extract_and_evaluate(tmp_path, name: str, *, mode: str, dark: bool = False):
    path = _write_video(tmp_path / f"{name}.avi", mode=mode, dark=dark)
    extracted = extract_video(
        path,
        max_seconds=10,
        max_frames=100,
        sample_hz=4,
        max_pixels=4_000_000,
        face_mesh_provider=lambda: _SyntheticMesh(mode),
    )
    features = extracted["features"]
    metrics = {
        **features,
        "video_status": "available" if extracted["status"] == "available" else "insufficient_quality",
        "video_reason": extracted["quality"].get("reason"),
    }
    return extracted, evaluate(metrics)


@pytest.mark.parametrize(
    ("name", "mode", "dark", "prediction", "perclos_min", "microsleeps_min", "reason"),
    [
        ("open", "open", False, "stable", 0.0, 0, None),
        ("closed", "closed", False, "high_risk", 0.9, 1, None),
        ("no-face", "no_face", False, None, None, 0, "no_usable_face_data"),
        ("dark", "open", True, None, None, 0, "low_light"),
    ],
)
def test_synthetic_video_extractor_to_rule_engine(
    tmp_path, name, mode, dark, prediction, perclos_min, microsleeps_min, reason
):
    extracted, result = _extract_and_evaluate(tmp_path, name, mode=mode, dark=dark)
    features = extracted["features"]
    if prediction is None:
        assert result["analysis_status"] == "retake_required"
        assert result["prediction"] is None
        assert result["insufficient_evidence_reason"] == reason
    else:
        assert result["prediction"] == prediction
        assert features["perclos_p80"] >= perclos_min
        assert features["microsleep_count"] >= microsleeps_min
    print(
        f"synthetic_engineering_case={name} status={result['analysis_status']} "
        f"prediction={result['prediction']} perclos_p80={features.get('perclos_p80')} "
        f"blink_rate_per_minute={features.get('blink_rate_per_minute')} "
        f"microsleep_count={features.get('microsleep_count')} "
        f"insufficient_evidence_reason={result['insufficient_evidence_reason']}"
    )
