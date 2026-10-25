"""Synthetic vector tests for engine wiring only; not model/health evidence."""
from copy import deepcopy

import pytest

from app.rule_based_indicators import ENGINE_VERSION, evaluate, load_config


def vector(**overrides):
    value = {
        "video_status": "available", "face_detection_ratio": 1.0,
        "mean_luminance": 80.0, "mean_blur_variance": 40.0,
        "video_duration_seconds": 5.0,
        "frame_count": 32, "temporal_coverage": 0.95,
        "perclos_p80": 0.02, "blink_rate_per_minute": 12.0,
        "microsleep_count": 0, "prolonged_eye_closure_count": 0,
        "yawn_count": 0, "head_nod_count": 0,
        "gaze_variability": 0.01, "head_pose_variability": 0.01,
    }
    value.update(overrides)
    return value


def history(perclos=0.02, blink=12.0, n=3):
    return [{"engine_type": "rule_based_indicators", "engine_version": ENGINE_VERSION,
             "status": "prediction_available", "measurements": vector(
                 perclos_p80=perclos, blink_rate_per_minute=blink)} for _ in range(n)]


@pytest.mark.parametrize(("metrics", "expected"), [
    (vector(), "stable"),
    (vector(perclos_p80=0.11), "low_focus"),
    (vector(perclos_p80=0.15), "elevated_fatigue"),
    (vector(microsleep_count=1), "high_risk"),
])
def test_synthetic_vectors_emit_each_configured_class(metrics, expected):
    result = evaluate(metrics)
    assert result["prediction"] == expected
    assert result["analysis_status"] == "prediction_available"
    assert result["engine_type"] == "rule_based_indicators"
    assert result["model_version"] == ENGINE_VERSION
    assert result["risk_level"] == expected
    assert result["confidence_level"] in {"high", "medium", "low"}
    assert result["status"] == "available"


def test_class_priority_and_frozen_boundary_values():
    cfg = load_config()
    assert cfg["priority"] == ["high_risk", "elevated_fatigue", "low_focus", "stable"]
    assert evaluate(vector(perclos_p80=0.40, yawn_count=2))["prediction"] == "high_risk"
    assert evaluate(vector(perclos_p80=0.15))["prediction"] == "elevated_fatigue"
    assert evaluate(vector(perclos_p80=0.109999))["prediction"] == "stable"
    custom = deepcopy(cfg)
    custom["class_rules"]["low_focus"]["perclos_p80_at_least"] = 0.12
    assert evaluate(vector(perclos_p80=0.11), config=custom)["prediction"] == "stable"


def test_missing_corrupt_timed_out_and_low_quality_video_never_become_stable():
    for metrics, reason in [
        (vector(video_status="not_provided"), "video_not_provided"),
        (vector(video_status="invalid_media", video_reason="truncated_video"), "truncated_video"),
        (vector(video_status="timeout", video_reason="decode_timeout"), "decode_timeout"),
        (vector(face_detection_ratio=0.0), "no_usable_face_data"),
        (vector(video_duration_seconds=1.999), "too_short"),
        (vector(video_duration_seconds=None), "video_duration_unavailable"),
        (vector(mean_luminance=2.0), "low_light"),
    ]:
        result = evaluate(metrics)
        assert result["analysis_status"] == "retake_required"
        assert result["prediction"] is None
        assert result["risk_level"] is None
        assert result["insufficient_evidence_reason"] == reason


def test_first_three_valid_scans_build_baseline_then_deviation_is_used():
    for count in range(3):
        result = evaluate(vector(), history=history(n=count))
        assert result["baseline"]["status"] == "building"
        assert result["baseline"]["used"] is False
        assert result["confidence_level"] == "low"
    result = evaluate(vector(perclos_p80=0.121), history=history())
    assert result["baseline"]["used"] is True
    assert result["prediction"] == "elevated_fatigue"
    assert "perclos_p80_deviation_from_personal_baseline" in result["contributing_indicators"]


def test_baseline_ignores_other_engines_and_versions():
    previous = history(n=2) + [{"engine_type": "other", "engine_version": ENGINE_VERSION,
        "status": "prediction_available", "measurements": vector()}]
    assert evaluate(vector(), history=previous)["baseline"]["used"] is False
