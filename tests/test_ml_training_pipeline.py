"""Synthetic software tests only; these fixtures are never research evidence."""
import hashlib
import json

import pytest

from app.ml.artifact_guard import ModelActivationDenied
from app.ml.json_backend import JsonLinearBackend
from app.ml.runtime import create_predictor
from research.evaluation_v1 import ResearchDataError
from research.training import TrainingConfig, train_target


def _record(index: int, *, fatigue_label: int | None = None):
    label = index % 2
    observed = {
        "status": "observed", "raw_value": float(label + 1), "class_label": label,
        "classification_rule_id": "synthetic-software-test-only", "label_definition_id": "synthetic-only",
        "unit": "synthetic class", "source_reference": "synthetic fixture",
        "assessor_role": "test fixture", "measured_at_utc": "2026-01-01T00:00:00Z",
    }
    sleepiness = {**observed, "instrument": "KSS", "qualified": True, "qualification_reference": "synthetic-only"}
    fatigue = {**observed, "class_label": label if fatigue_label is None else fatigue_label,
               "instrument": "SyntheticFatigueTest", "qualified": True,
               "qualification_reference": "synthetic-only"}
    vigilance = {**observed, "instrument": "PVT", "metric": "lapse_count", "protocol_id": "synthetic-only"}
    feature_value = float(label * 2 + index / 100)
    return {
        "schema_version": "1.1", "fixture_marker": "unit_test_synthetic",
        "sample_id": f"sample-{index}", "participant_id": f"participant-{index}",
        "session_id": f"session-{index}",
        "identity_linkage": {"pseudonymized": True, "linkage_key_stored_separately": True,
                              "linkage_governance_id": "synthetic-test"},
        "provenance": {"study_id": "synthetic-test", "dataset_id": "synthetic-test",
                       "dataset_version": "1", "source_reference": "test fixture",
                       "acquisition_protocol_id": "synthetic-test", "curator_role": "test"},
        "consent": {"status": "granted", "permissions": {"research_evaluation": True,
                    "model_training": True, "cross_site_transfer": True}},
        "context": {"recorded_at_utc": "2026-01-01T00:00:00Z", "site_id": "test",
                    "device_id": "test", "industry": "unknown", "occupation": "unknown"},
        "modality_availability": {m: "available" for m in ("video", "audio", "image", "reaction_task")},
        "features": {m: {"values": ({"signal": feature_value} if m == "video" else {}),
                         "provenance": {"extractor": "synthetic-test"}}
                     for m in ("video", "audio", "image", "reaction_task")},
        "labels": {"reference_sleepiness": sleepiness, "subjective_fatigue": fatigue,
                   "vigilance_performance": vigilance},
    }


def _rights():
    return {"dataset_id": "synthetic-test", "dataset_version": "1", "license_id": "synthetic-test-only",
            "approval_reference": "synthetic-test-only", "protocol_sha256": "a" * 64,
            "model_training_authorized": True, "consent_confirmed": True}


def _train(target="reference_sleepiness", records=None):
    return train_target(records or [_record(i) for i in range(40)], _rights(),
                        TrainingConfig(target=target, feature_names=("video.signal",), seed=17),
                        allow_unit_test_fixture=True)


def test_training_is_reproducible_and_emits_research_candidate_only():
    artifact1, output1 = _train()
    artifact2, output2 = _train()
    assert artifact1 == artifact2
    assert output1 == output2
    assert output1["manifest"]["artifact_class"] == "research_candidate"
    assert output1["manifest"]["research_only"] is True
    assert output1["manifest"]["validation_status"] == "not_validated"
    assert output1["report"]["scientific_validation"] == "not_assessed_by_training_software"


def test_target_labels_are_trained_independently():
    records = [_record(i, fatigue_label=(i + 1) % 2) for i in range(40)]
    sleepiness, _ = _train("reference_sleepiness", records)
    fatigue, _ = _train("subjective_fatigue", records)
    assert sleepiness["target"] == "reference_sleepiness"
    assert fatigue["target"] == "subjective_fatigue"
    assert sleepiness["target"] != fatigue["target"]


def test_real_fatigue_training_requires_authoritatively_approved_mapping():
    records = [_record(i) for i in range(40)]
    for row in records:
        row.pop("fixture_marker")
        row["labels"]["subjective_fatigue"].update({
            "approval_status": "approved", "approval_reference": "string-is-not-an-approval",
            "instrument_id": "unregistered-scale", "subscale": "total", "scoring_version": "1",
        })
    with pytest.raises(ResearchDataError, match="not scientifically approved"):
        train_target(records, _rights(), TrainingConfig("subjective_fatigue", ("video.signal",), seed=17))


def test_real_classification_requires_approved_threshold_rule():
    records = [_record(i) for i in range(40)]
    for row in records:
        row.pop("fixture_marker")
    with pytest.raises(ResearchDataError, match="approved target-specific threshold"):
        train_target(records, _rights(), TrainingConfig("reference_sleepiness", ("video.signal",), seed=17))


def test_unconsented_or_unlicensed_records_are_rejected():
    records = [_record(i) for i in range(40)]
    records[0]["consent"]["permissions"]["model_training"] = False
    with pytest.raises(ResearchDataError, match="allow model training"):
        _train(records=records)
    rights = _rights()
    rights["model_training_authorized"] = False
    with pytest.raises(ResearchDataError, match="rights and consent"):
        train_target([_record(i) for i in range(40)], rights,
                     TrainingConfig("reference_sleepiness", ("video.signal",)),
                     allow_unit_test_fixture=True)


def test_feature_missingness_gate_fails_closed():
    records = [_record(i) for i in range(40)]
    for row in records:
        row["features"]["video"]["values"]["signal"] = None
    with pytest.raises(ResearchDataError, match="entirely missing"):
        _train(records=records)


def test_json_backend_runs_bounded_artifact_and_runtime_guard_rejects_candidate(tmp_path):
    artifact, output = _train()
    path = tmp_path / "candidate.json"
    encoded = json.dumps(artifact, sort_keys=True, separators=(",", ":")).encode()
    path.write_bytes(encoded)
    backend = JsonLinearBackend.from_file(path)
    prediction = backend.predict("reference_sleepiness", {"video": {"signal": 0.1}})
    assert prediction["status"] in {"available", "abstained"}
    assert "tenant_id" not in prediction and "subject_id" not in prediction

    with pytest.raises(ModelActivationDenied):
        create_predictor(enabled=True, manifest=output["manifest"], artifact_path=path,
                         backend_loader=lambda p, m: JsonLinearBackend.from_file(p, m))


def test_json_backend_rejects_malformed_duplicate_oversize_and_unknown_versions(tmp_path):
    artifact, _ = _train()
    path = tmp_path / "artifact.json"
    path.write_text('{"format":"x","format":"y"}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        JsonLinearBackend.from_file(path)
    path.write_bytes(b" " * (1024 * 1024 + 1))
    with pytest.raises(ValueError, match="size"):
        JsonLinearBackend.from_file(path)
    artifact["format"] = "unknown/99"
    path.write_text(json.dumps(artifact), encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported"):
        JsonLinearBackend.from_file(path)

