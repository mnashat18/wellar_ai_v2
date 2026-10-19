import copy
import json
from pathlib import Path

import pytest

from app.ml.artifact_guard import ModelActivationDenied, require_operational_artifact
from research.evaluation_v1 import (
    ResearchDataError,
    check_split_leakage,
    classification_metrics,
    eligible_subgroup_metrics,
    external_domain_holdout,
    experiment_record,
    participant_bootstrap_ci,
    participant_disjoint_split,
    validate_preregistration,
    validate_record,
)


def _fixture(index, *, participant=None, site=None, device=None):
    """Synthetic software-test-only record; never a scientific observation."""
    return {
        "schema_version": "1.0",
        "fixture_marker": "unit_test_synthetic",
        "sample_id": f"sample-{index}",
        "participant_id": participant or f"participant-{index}",
        "session_id": f"session-{index}",
        "identity_linkage": {"pseudonymized": True, "linkage_key_stored_separately": True,
                             "linkage_governance_id": "test-governance-only"},
        "provenance": {"study_id": "software-test-only", "dataset_id": "unit-fixture",
                       "dataset_version": "none", "source_reference": "synthetic-test-code",
                       "acquisition_protocol_id": "not-a-study", "curator_role": "test-only"},
        "consent": {"status": "granted", "permissions": {"research_evaluation": True,
                    "model_training": True, "cross_site_transfer": False}},
        "context": {"recorded_at_utc": "unknown", "site_id": site or "site-a",
                    "device_id": device or "device-a", "industry": "transport",
                    "occupation": "unknown"},
        "modality_availability": {"video": "available", "audio": "available",
                                  "image": "missing", "reaction_task": "available"},
        "features": {name: {"values": {"summary_value": float(index)},
                             "provenance": {"extractor": "test-fixture", "version": "0"}}
                     for name in ("video", "audio", "image", "reaction_task")},
        "labels": {
            "reference_sleepiness": {"status": "observed", "raw_value": 5,
                "class_label": "moderate", "classification_rule_id": "test-only-rule",
                "label_definition_id": "test-kss", "instrument": "KSS", "qualified": True,
                "qualification_reference": "test-only", "unit": "KSS points",
                "source_reference": "test fixture", "assessor_role": "test-only",
                "measured_at_utc": "2025-01-01T00:00:00Z"},
            "vigilance_performance": {"status": "observed", "raw_value": 2,
                "class_label": "test-class", "classification_rule_id": "test-only-rule",
                "label_definition_id": "test-pvt", "instrument": "PVT",
                "metric": "lapse_count", "protocol_id": "test-pvt-protocol",
                "unit": "lapses", "source_reference": "test fixture",
                "assessor_role": "test-only", "measured_at_utc": "2025-01-01T00:00:00Z"},
        },
    }


def test_record_schema_accepts_only_explicit_unit_test_fixture():
    record = _fixture(1)
    with pytest.raises(ResearchDataError, match="not eligible"):
        validate_record(record)
    validate_record(record, allow_unit_test_fixture=True)
    identified = _fixture(1)
    identified["employee_name"] = "must-not-be-stored"
    with pytest.raises(ResearchDataError, match="unsupported or potentially identifying"):
        validate_record(identified, allow_unit_test_fixture=True)


def test_sleepiness_requires_qualified_kss_and_vigilance_requires_pvt():
    record = _fixture(1)
    record["labels"]["reference_sleepiness"]["qualified"] = False
    with pytest.raises(ResearchDataError, match="qualified KSS"):
        validate_record(record, allow_unit_test_fixture=True)
    record = _fixture(1)
    record["labels"]["vigilance_performance"]["instrument"] = "KSS"
    with pytest.raises(ResearchDataError, match="PVT"):
        validate_record(record, allow_unit_test_fixture=True)


def test_v11_represents_fatigue_separately_and_preserves_explicit_absence():
    with pytest.raises(ResearchDataError, match="not present in this record schema version"):
        from research.evaluation_v1 import evaluate_target
        evaluate_target([_fixture(0)], {"sample-0": {"prediction": "unused"}},
                        target="subjective_fatigue", allow_unit_test_fixture=True)
    record = _fixture(1)
    record["schema_version"] = "1.1"
    record["labels"]["subjective_fatigue"] = {
        "status": "unknown", "raw_value": None, "class_label": None,
        "label_definition_id": "fatigue-instrument-not-selected",
        "missing_reason": "no approved independent fatigue instrument",
    }
    validate_record(record, allow_unit_test_fixture=True)
    assert "subjective_fatigue" not in _fixture(2)["labels"]
    schema = json.loads((Path(__file__).resolve().parents[1] / "research" / "schema_v1_1.json").read_text(encoding="utf-8"))
    assert schema["properties"]["schema_version"]["const"] == "1.1"
    assert "subjective_fatigue" in schema["properties"]["labels"]["required"]
    with pytest.raises(ResearchDataError, match="observed, preregistered class labels"):
        evaluate_target([record], {record["sample_id"]: {"prediction": "unused"}},
                        target="subjective_fatigue", allow_unit_test_fixture=True)

    # A KSS sleepiness measure cannot be relabeled as a fatigue observation.
    record["labels"]["subjective_fatigue"] = {
        "status": "observed", "raw_value": 5, "class_label": None,
        "label_definition_id": "test-only-invalid-reuse", "instrument": "KSS",
        "qualified": True, "qualification_reference": "test-only",
        "unit": "points", "source_reference": "test fixture",
        "assessor_role": "test-only", "measured_at_utc": "2025-01-01T00:00:00Z",
    }
    with pytest.raises(ResearchDataError, match="non-KSS instrument"):
        validate_record(record, allow_unit_test_fixture=True)


def test_missing_label_and_modality_require_explicit_unknowns():
    record = _fixture(1)
    label = record["labels"]["vigilance_performance"]
    label.update(status="unknown", raw_value=None, class_label=None, missing_reason="not collected")
    del record["modality_availability"]["audio"]
    with pytest.raises(ResearchDataError, match="availability"):
        validate_record(record, allow_unit_test_fixture=True)
    record["modality_availability"]["audio"] = "unknown"
    validate_record(record, allow_unit_test_fixture=True)


def test_participant_split_is_deterministic_and_disjoint():
    rows = [_fixture(i, participant=f"p-{i // 2}") for i in range(30)]
    first = participant_disjoint_split(rows, seed=10)
    second = participant_disjoint_split(rows, seed=10)
    assert first == second
    check_split_leakage(rows, first)
    assert set(first.values()) == {"train", "validation", "test"}


def test_leakage_check_rejects_participant_and_media_reuse():
    rows = [_fixture(1, participant="shared"), _fixture(2, participant="shared")]
    with pytest.raises(ResearchDataError, match="participant_id leakage"):
        check_split_leakage(rows, {"sample-1": "train", "sample-2": "test"})
    rows[0]["participant_id"] = "p1"
    rows[1]["participant_id"] = "p2"
    rows[0]["media_fingerprints"] = ["opaque-hash"]
    rows[1]["media_fingerprints"] = ["opaque-hash"]
    with pytest.raises(ResearchDataError, match="media fingerprint"):
        check_split_leakage(rows, {"sample-1": "train", "sample-2": "test"})


def test_clustered_confidence_interval_is_reproducible():
    truth = [0, 1, 0, 1, 0, 1]
    pred = [0, 1, 1, 1, 0, 0]
    participants = ["a", "a", "b", "b", "c", "c"]
    one = participant_bootstrap_ci(truth, pred, participants, replicates=200, seed=7)
    assert one == participant_bootstrap_ci(truth, pred, participants, replicates=200, seed=7)
    assert one["participant_clusters"] == 3
    assert one["lower"] <= one["upper"]


def test_metrics_report_balance_calibration_and_abstention():
    result = classification_metrics(
        [0, 1, 0, 1], [0, None, 1, 1], abstain_value=None,
        probabilities=[{0: .9, 1: .1}, {0: .2, 1: .8}, {0: .4, 1: .6}, {0: .1, 1: .9}],
    )
    assert result["class_balance"] == {"0": 2, "1": 2}
    assert result["coverage"] == .75
    assert result["abstention_count"] == 1
    assert "brier_score" in result["calibration"]
    assert "expected_calibration_error" in result["calibration"]


def test_small_subgroups_are_suppressed_and_large_groups_are_aggregated():
    rows = [_fixture(i, site="site-x") for i in range(24)]
    truth = [i % 2 for i in range(24)]
    pred = truth[:]
    suppressed = eligible_subgroup_metrics(rows[:10], truth[:10], pred[:10], min_participants=20)
    assert suppressed["site_id"]["site-x"]["suppressed"] is True
    report = eligible_subgroup_metrics(rows, truth, pred, min_participants=20, min_class_count=5)
    assert report["site_id"]["site-x"]["suppressed"] is False


def test_site_and_device_holdouts_are_distinct_and_participant_disjoint():
    rows = []
    for i in range(8):
        rows.append(_fixture(i, participant=f"site-a-{i}", site="site-a", device="device-a"))
        rows.append(_fixture(i + 8, participant=f"site-b-{i}", site="site-b", device="device-b"))
    site = external_domain_holdout(rows, domain_field="site_id", held_out_value="site-b")
    device = external_domain_holdout(rows, domain_field="device_id", held_out_value="device-b")
    assert site["assessment"] == "site_disjoint"
    assert device["assessment"] == "device_disjoint"
    assert not ({r["participant_id"] for r in site["train"]} & {r["participant_id"] for r in site["test"]})


def test_experiment_record_contains_hashes_and_aggregate_results_only():
    result = experiment_record(protocol={"protocol_id": "test"}, results={"sample_count": 0},
                              split_by_sample={"s1": "train"})
    assert result["artifact_class"] == "research_only"
    assert len(result["protocol_sha256"]) == 64
    assert "sample_id" not in result
    with pytest.raises(ResearchDataError, match="aggregate metrics only"):
        experiment_record(protocol={}, results={"participant_id": "private"}, split_by_sample={})


def test_preregistration_rejects_reused_locked_test_and_requires_three_separate_targets():
    protocol = {"status": "preregistered", "protocol_id": "study-05", "version": "1.1", "targets": ["reference_sleepiness", "subjective_fatigue", "vigilance_performance"],
        "target_definitions": {"reference_sleepiness": {"instrument": "KSS", "rule": "kss-class-v1"},
                               "subjective_fatigue": {"instrument": "independent-scale-v1", "rule": "fatigue-class-v1"},
                               "vigilance_performance": {"instrument": "PVT", "rule": "pvt-class-v1"}},
        "sample_size_and_power_analysis": "approved-study-analysis-v1",
        "primary_metrics": ["balanced_accuracy"], "split_policy": "participant_disjoint",
        "inclusion_criteria": ["consent"], "exclusion_criteria": ["invalid_labels"],
        "missing_data_policy": "report", "abstention_policy": "predefined",
        "subgroup_policy": "minimum-support-suppression", "calibration_policy": "report",
        "held_out_test_policy": {"one_time_evaluation": True, "previously_inspected_data_reused_as_locked_test": False},
        "analysis_code_version": "evaluation_v1"}
    validate_preregistration(protocol)
    legacy = copy.deepcopy(protocol)
    legacy["version"] = "1.0"
    legacy["targets"] = ["reference_sleepiness", "vigilance_performance"]
    del legacy["target_definitions"]["subjective_fatigue"]
    validate_preregistration(legacy)
    bad = copy.deepcopy(protocol)
    bad["held_out_test_policy"]["previously_inspected_data_reused_as_locked_test"] = True
    with pytest.raises(ResearchDataError, match="previously inspected"):
        validate_preregistration(bad)
    bad = copy.deepcopy(protocol)
    bad["sample_size_and_power_analysis"] = "REPLACE_BEFORE_STUDY"
    with pytest.raises(ResearchDataError, match="unresolved placeholders"):
        validate_preregistration(bad)


def test_research_artifact_is_denied_to_operational_loader_gate():
    with pytest.raises(ModelActivationDenied, match="research-only"):
        require_operational_artifact({"artifact_class": "research_only", "research_only": True})
    with pytest.raises(ModelActivationDenied):
        require_operational_artifact({})


def test_evaluate_target_keeps_sleepiness_separate_and_tracks_calibration():
    from research.evaluation_v1 import evaluate_target
    rows = []
    predictions = {}
    for index in range(8):
        row = _fixture(index, participant=f"p-{index}")
        row["labels"]["reference_sleepiness"]["class_label"] = index % 2
        row["labels"]["reference_sleepiness"]["classification_rule_id"] = "preregistered-kss-rule"
        rows.append(row)
        predictions[row["sample_id"]] = {"prediction": index % 2,
            "probabilities": {0: .8, 1: .2} if index % 2 == 0 else {0: .2, 1: .8}}
    report = evaluate_target(rows, predictions, target="reference_sleepiness", bootstrap_replicates=100,
                             allow_unit_test_fixture=True)
    assert report["target"] == "reference_sleepiness"
    assert report["research_only"] is True
    assert report["metrics"]["accuracy_non_abstained"] == 1.0
    with pytest.raises(ResearchDataError, match="target must be"):
        evaluate_target(rows, predictions, target="fatigue")


def test_continuous_regression_metrics_cluster_ci_and_abstention():
    from research.evaluation_v1 import participant_cluster_regression_ci, regression_metrics
    truth = [1.0, 2.0, 3.0, 4.0]
    pred = [1.5, None, 2.0, 5.0]
    metrics = regression_metrics(truth, pred)
    assert metrics["sample_count"] == 4
    assert metrics["evaluated_count"] == 3
    assert metrics["abstention_count"] == 1
    assert metrics["coverage"] == .75
    assert metrics["mae"] == pytest.approx(5 / 6)
    ci = participant_cluster_regression_ci(truth, pred, ["p1", "p1", "p2", "p2"],
                                           metric="rmse", replicates=100, seed=11)
    assert ci["participant_clusters"] == 2
    assert ci["replicates"] == 100
    assert ci == participant_cluster_regression_ci(truth, pred, ["p1", "p1", "p2", "p2"],
                                                    metric="rmse", replicates=100, seed=11)
