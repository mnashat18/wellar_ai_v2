from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.compat.legacy_projection import project_v1


MEASUREMENTS = {
    "short_clip_eye_closure_fraction": 0.03,
    "blink_rate_per_minute": 10,
    "mean_eye_closure_seconds": 0.1,
    "max_eye_closure_seconds": 0.2,
    "face_detection_ratio": 0.9,
    "mean_luminance": 80,
    "mean_blur_variance": 40,
    "temporal_coverage": 0.8,
    "yaw_proxy": 0.01,
    "pitch_proxy": 0.01,
}


@pytest.mark.parametrize("state", ["Stable", "Low Focus", "Elevated Fatigue", "High Risk"])
def test_legacy_projection_has_only_allowed_rule_labels_and_null_scores(state):
    output = project_v1({"drowsiness_indicator": {"status": "available", "state": state, "config_version": "1.0"}})
    assert output["risk_level"] == output["readiness_state"] == output["overall_state"] == state
    assert all(output[key] is None for key in ("readiness_score", "fatigue_score", "focus_score", "risk_score", "confidence_score"))
    assert output["analysis_mode"] == "rule_based_indicator"


def test_legacy_projection_maps_quality_abstention_to_retake_required():
    output = project_v1({"drowsiness_indicator": {"status": "insufficient_quality", "config_version": "1.0"}})
    assert output["status"] == "retake_required" and output["risk_level"] is None


def test_client_measurements_endpoint_validates_and_marks_unverified(client, auth):
    result = client.post("/api/v1/scans/measurements", headers=auth, json=MEASUREMENTS)
    assert result.status_code == 200
    assert result.json()["source"] == "client_measurements"
    assert result.json()["measurement_provenance"] == "client-supplied, unverified"
    assert result.json()["drowsiness_indicator"]["state"] == "Stable"
    assert "confidence" not in result.text and "accuracy" not in result.text


@pytest.mark.parametrize("changes", [
    {"short_clip_eye_closure_fraction": 8.0},
    {"blink_rate_per_minute": 10000},
    {"yaw_proxy": 99},
    {"temporal_coverage": -1},
    {"mean_blur_variance": 1_000_001},
])
def test_client_measurements_rejects_implausible_ranges(client, auth, changes):
    payload = {**MEASUREMENTS, **changes}
    assert client.post("/api/v1/scans/measurements", headers=auth, json=payload).status_code == 422


def test_client_measurements_rejects_missing_and_unknown_fields(client, auth):
    missing = {key: value for key, value in MEASUREMENTS.items() if key != "yaw_proxy"}
    unknown = {**MEASUREMENTS, "confidence": 0.99}
    assert client.post("/api/v1/scans/measurements", headers=auth, json=missing).status_code == 422
    assert client.post("/api/v1/scans/measurements", headers=auth, json=unknown).status_code == 422


def test_checked_openapi_matches_runtime():
    from app.main import app
    checked = json.loads((Path(__file__).resolve().parents[1] / "openapi.json").read_text(encoding="utf-8"))
    assert checked == app.openapi()
