"""Synthetic software-only end-to-end checks for continuous regression."""
import copy
import json

import pytest

from app.ml.artifact_guard import ModelActivationDenied, require_operational_artifact
from research.evaluation_v1 import ResearchDataError
from research.media_feature_adapter import build_feature_record
from research.model_input_v1 import build_model_input_records
from research.training import RegressionConfig, train_regression, train_regression_files


def _sample(index: int, participant_index: int, split: str, *, rejected: bool = False):
    luminance = float(20 + participant_index * 7 + (index % 2) * 3)
    image = {
        "status": "insufficient_quality" if rejected else "available",
        "quality": {"reason": "low_light"} if rejected else {"reason": None, "face_visible": True},
        "features": {"face_detection_ratio": 1.0, "mean_luminance": luminance,
                     "mean_blur_variance": 12.0, "eye_aspect_ratio": 0.2},
        "provenance": {"extractor": "synthetic_image_test", "version": "1.0",
                       "eye_aspect_ratio": "normalized_eye_geometry"},
    }
    feature = build_feature_record(
        participant_id=f"p_{participant_index:016x}", session_id=f"s_{index:016x}",
        sample_id=f"sample_{index:016x}", extractor_outputs={"image": image},
    )
    label = {"status": "observed", "value": luminance * 0.4 + (participant_index % 3),
             "definition_id": "synthetic_continuous_reference_v1", "source_reference": "synthetic_fixture",
             "measured_at_utc": "2026-01-01T00:00:00Z", "approval_status": "synthetic_test_only",
             "continuous_target_approved": True}
    return feature, label, split


def _dataset():
    features, labels, splits = [], {}, {}
    index = 0
    for participant in range(18):
        split = "train" if participant < 10 else "validation" if participant < 14 else "test"
        for session in range(2):
            feature, label, _ = _sample(index, participant, split,
                                        rejected=(participant == 14 and session == 0))
            features.append(feature)
            labels[feature["sample_id"]] = label
            splits[feature["sample_id"]] = split
            index += 1
    return build_model_input_records(features, labels, splits, target="reference_sleepiness",
                                     allow_synthetic_fixture=True)


def _config():
    return RegressionConfig(target="reference_sleepiness", feature_names=("image.mean_luminance",),
                            seed=37, ridge_alphas=(0.01, 0.1, 1.0), bootstrap_replicates=200)


def test_synthetic_adapter_to_regression_train_validate_locked_test_and_persist(tmp_path):
    records = _dataset()
    assert len({row["participant_id"] for row in records if row["split"] == "train"}) == 10
    assert len({row["session_id"] for row in records if row["split"] == "train"}) == 20
    assert records[0]["modality_quality"]["audio"]["status"] == "not_provided"
    assert any(row["modality_quality"]["image"]["status"] == "insufficient_quality" for row in records)
    artifact, report = train_regression(records, _config(), allow_synthetic_fixture=True)
    assert artifact["artifact_class"] == "research_candidate"
    assert artifact["research_only"] is True
    assert artifact["preprocessor"]["fit_split"] == "train"
    assert {model["name"] for model in artifact["candidate_models"]} == {"constant_median", "ridge_linear"}
    assert artifact["metadata"]["media_feature_schema_version"] == "wellar-media-features/1.1"
    assert artifact["metadata"]["code_sha256"]["regression_training.py"]
    assert artifact["metadata"]["test_access"] == "after_validation_selection_only"
    assert report["test_split_consumed_for_fit_or_selection"] is False
    metrics = report["test_metrics_selected_candidate_once"]
    baseline_metrics = report["test_metrics_constant_median_baseline_once"]
    assert metrics["estimand"]["primary"] == "equal_weight_mean_of_participant_level_MAE"
    assert metrics["estimand"]["secondary"] == "equal_weight_mean_of_participant_level_RMSE"
    assert metrics["coverage"] < 1.0
    assert metrics["abstention_count"] >= 1
    assert metrics["participant_level_mae_ci95"]["method"] == "participant_cluster_percentile_bootstrap"
    assert baseline_metrics["estimand"] == metrics["estimand"]
    assert baseline_metrics["coverage"] == metrics["coverage"]

    output = tmp_path / "research_artifacts"
    from research.regression_training import persist_research_artifact
    paths = persist_research_artifact(output, artifact, report)
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    assert manifest["research_only"] is True
    with pytest.raises(ModelActivationDenied, match="research-only"):
        require_operational_artifact(manifest)
    with pytest.raises(ResearchDataError, match="research-only directory"):
        persist_research_artifact(tmp_path / "production", artifact, report)


def test_determinism_and_test_contamination_cannot_change_fitted_model():
    records = _dataset()
    first, report1 = train_regression(records, _config(), allow_synthetic_fixture=True)
    second, report2 = train_regression(records, _config(), allow_synthetic_fixture=True)
    assert first == second
    assert report1 == report2

    contaminated = copy.deepcopy(records)
    for row in contaminated:
        if row["split"] == "test":
            row["label"]["value"] += 100_000
            if row["feature_available_mask"]["image.mean_luminance"]:
                row["feature_values"]["image.mean_luminance"] = 1_000_000.0
    altered, _ = train_regression(contaminated, _config(), allow_synthetic_fixture=True)
    for key in ("selected_candidate", "selected_ridge_alpha", "preprocessor", "baseline_median",
                "selected_intercept", "selected_coefficients"):
        assert altered[key] == first[key]


def test_unapproved_continuous_target_and_split_leakage_are_rejected():
    records = _dataset()
    no_approval = copy.deepcopy(records)
    no_approval[0]["label"]["continuous_target_approved"] = False
    with pytest.raises(ResearchDataError, match="explicitly designated"):
        train_regression(no_approval, _config(), allow_synthetic_fixture=True)

    leaked = copy.deepcopy(records)
    participant = leaked[0]["participant_id"]
    leaked[0]["split"] = "test" if leaked[0]["split"] != "test" else "train"
    assert any(row["participant_id"] == participant and row["split"] != leaked[0]["split"] for row in leaked[1:])
    with pytest.raises(ResearchDataError, match="participant_id leakage"):
        train_regression(leaked, _config(), allow_synthetic_fixture=True)


def test_unapproved_fatigue_target_mapping_is_blocked():
    features, labels, splits = [], {}, {}
    feature, label, _ = _sample(900, 30, "train")
    label.update({"approval_status": "approved", "approval_reference": "unverified",
                  "instrument_id": "scale-x", "subscale": "total", "scoring_version": "1",
                  "continuous_target_approved": True})
    features.append(feature)
    labels[feature["sample_id"]] = label
    splits[feature["sample_id"]] = "train"
    with pytest.raises(ResearchDataError, match="not scientifically approved"):
        build_model_input_records(features, labels, splits, target="subjective_fatigue")


def test_jsonl_config_runner_writes_new_research_artifacts_only(tmp_path):
    records = _dataset()
    data = tmp_path / "synthetic_inputs.jsonl"
    data.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in records), encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"target": "reference_sleepiness", "feature_names": ["image.mean_luminance"],
                                 "seed": 37, "ridge_alphas": [0.01, 0.1], "bootstrap_replicates": 100}),
                      encoding="utf-8")
    report = train_regression_files(data, config, tmp_path / "research_artifacts",
                                    allow_synthetic_fixture=True)
    assert report["status"] == "synthetic_software_pipeline_only"
