"""Synthetic software-wiring tests; these fixtures are not scientific evidence."""
import hashlib
import json

import pytest

from app.core.adapters import create_analysis_processor
from app.core.config import Settings
from app.media.analysis import MultimodalFeatureProcessor
from app.ml.artifact_guard import ModelActivationDenied
from app.ml.feature_mapping import FEATURE_MAPPING_VERSION
from app.ml.registry import load_predictors
from app.scans.schemas import ScanResult


TARGET = "reference_sleepiness"


def _test_release(directory, *, feature_name="video.sampled_closed_fraction", research=False):
    artifact = {
        "format": "wellar-linear-softmax/1.0",
        "model_interface_version": "1.0",
        "target": TARGET,
        "model_version": "unit-test-only-fixture",
        "classes": [0, 1],
        "feature_names": [feature_name],
        "imputation_medians": [0.0],
        "means": [0.0],
        "scales": [1.0],
        "weights": [[0.0], [1.0]],
        "bias": [0.0, 2.0],
        "temperature": 1.0,
        "min_confidence": 0.5,
        "max_feature_missingness": 0.5,
        "calibrated": True,
        "calibration_method": "validation_temperature_scaling_grid_0.5_to_5.0",
        "required_modalities": [feature_name.split(".", 1)[0]],
        "metadata": {"fixture_marker": "test_only_not_validation"},
    }
    artifact_path = directory / f"{TARGET}.model.json"
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    manifest = {
        "artifact_class": "research_candidate" if research else "validated_operational",
        "research_only": research,
        "validation_status": "not_validated" if research else "validated",
        "artifact_sha256": hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
        "target": TARGET,
        "feature_mapping_version": FEATURE_MAPPING_VERSION,
        "protocol_sha256": "a" * 64,
        "held_out_evaluation_id": "unit-test-only-fixture",
        "approval_reference": "unit-test-only-fixture",
        "model_interface_version": "1.0",
        "model_version": "unit-test-only-fixture",
        "required_modalities": [feature_name.split(".", 1)[0]],
        "max_missingness": 0.5,
        "calibrated": True,
    }
    (directory / f"{TARGET}.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return artifact_path


class _MediaStore:
    def resolve(self, ref, tenant_id, subject_id, verify_integrity):
        return {"modality": "video", "path": "synthetic-test-only.mp4", "integrity_ok": True, "available": True}

    def release(self, asset):
        pass


def _processor(predictors, monkeypatch, *, extraction_status="available"):
    import app.media.analysis as analysis

    monkeypatch.setattr(analysis, "run_killable", lambda *args, **kwargs: {
        "status": extraction_status,
        "quality": {"overall_coverage": 1.0 if extraction_status == "available" else 0.0,
                    "missingness": 0.0 if extraction_status == "available" else 1.0,
                    "reason": None if extraction_status == "available" else "synthetic_quality_failure"},
        "features": {"sampled_closed_fraction": 0.25, "closure_event_count": 1},
        "timing_ms": {"total": 0.01},
        "provenance": {"extractor": "unit-test-fixture", "version": "test-only"},
    })
    return MultimodalFeatureProcessor(
        _MediaStore(), max_video_seconds=1, max_audio_seconds=1,
        max_image_pixels=100, max_video_frames=10, video_sample_hz=1,
        predictors=predictors,
    )


def _payload():
    return {
        "media_refs": ["media://fixture/0123456789abcdef"],
        "_media_manifest": [{"media_ref": "media://fixture/0123456789abcdef", "modality": "video"}],
        "_worker_context": {"scan_id": "scan-synthetic", "tenant_id": "tenant-secret-fixture", "subject_id": "subject-secret-fixture"},
    }


def test_analysis_processor_only_enables_guarded_configured_artifacts(tmp_path):
    disabled=create_analysis_processor(Settings(),_MediaStore())
    assert disabled.prediction_enabled is False
    assert all(predictor.__class__.__name__=="DisabledPredictor" for predictor in disabled.predictors.values())

    # This fixture exercises loader wiring only; it is not a real or approved model.
    _test_release(tmp_path)
    enabled=create_analysis_processor(Settings(model_inference_enabled=True,model_artifact_dir=str(tmp_path)),_MediaStore())
    assert enabled.prediction_enabled is True
    assert enabled.predictors[TARGET].__class__.__name__!="DisabledPredictor"

    with pytest.raises(ModelActivationDenied):
        create_analysis_processor(Settings(model_inference_enabled=True,model_artifact_dir=str(tmp_path/"missing")),_MediaStore())


def test_stage_one_does_not_invoke_even_an_injected_predictor(tmp_path, monkeypatch):
    # This hand-authored test artifact exists only to exercise code wiring; it is not a trained model.
    _test_release(tmp_path)
    predictors = load_predictors(enabled=True, artifact_dir=tmp_path)
    processor = _processor(predictors, monkeypatch)
    result = processor(_payload())

    extension = result["prediction_extension"]
    target = extension["targets"][TARGET]
    assert extension["schema_version"] == "1.0"
    assert extension["inference_status"] == "disabled"
    assert target["status"] == "unavailable"
    assert target["value"] is None
    assert target["confidence"] is None
    assert target["model_version"] is None
    assert target["quality_status"] == "not_evaluated"
    assert target["abstention_reason"] == "prediction_not_enabled"
    assert result["status"] == "insufficient_evidence"
    assert result["targets"]["reference_sleepiness"]["value"] is None
    assert result["targets"]["vigilance_impairment"]["value"] is None
    encoded = json.dumps(result)
    assert "tenant-secret-fixture" not in encoded and "subject-secret-fixture" not in encoded
    assert ScanResult.model_validate(result).prediction_extension is not None


def test_default_processor_has_three_disabled_null_targets_and_legacy_contract(tmp_path, monkeypatch):
    predictors = load_predictors()
    result = _processor(predictors, monkeypatch)(_payload())
    extension = result["prediction_extension"]
    assert extension["inference_status"] == "disabled"
    assert set(extension["targets"]) == {"reference_sleepiness", "subjective_fatigue", "vigilance_impairment"}
    assert all(value["status"] == "unavailable" and value["value"] is None for value in extension["targets"].values())
    assert result["schema_version"] == "2.0"
    assert result["status"] == "insufficient_evidence"
    assert result["model_version"] is None
    assert result["targets"] == {
        "reference_sleepiness": {"status": "unavailable", "value": None, "reason": "model_unavailable"},
        "vigilance_impairment": {"status": "unavailable", "value": None, "reason": "model_unavailable"},
    }
    ScanResult.model_validate(result)


def test_worker_evidence_versions_media_pipe_provenance_and_stage_timing(monkeypatch):
    processor = _processor({}, monkeypatch)
    result = processor(_payload())
    evidence = result["evidence"]
    assert evidence["schema_version"] == "1.2"
    assert evidence["modalities"]["video"]["timing_ms"] == {"total": 0.01}
    # The test extractor is explicitly a fixture, so production model provenance
    # must only be reported when the real MediaPipe extractor ran.
    assert not any(model["name"] == "MediaPipe Face Mesh" for model in evidence["models"])


def test_research_candidate_artifact_is_rejected_before_model_loading(tmp_path):
    _test_release(tmp_path, research=True)
    with pytest.raises(ModelActivationDenied):
        load_predictors(enabled=True, artifact_dir=tmp_path)


def test_malformed_release_manifest_is_rejected_without_details(tmp_path):
    _test_release(tmp_path)
    manifest = tmp_path / f"{TARGET}.manifest.json"
    manifest.write_text('{"target":"reference_sleepiness","target":"forged"}', encoding="utf-8")
    with pytest.raises(ModelActivationDenied, match="manifest is invalid") as error:
        load_predictors(enabled=True, artifact_dir=tmp_path)
    assert "forged" not in str(error.value)


def test_unsupported_feature_schema_is_rejected(tmp_path):
    _test_release(tmp_path, feature_name="video.short_clip_eye_closure_fraction")
    with pytest.raises(ModelActivationDenied, match="unsupported feature schema"):
        load_predictors(enabled=True, artifact_dir=tmp_path)


def test_quality_failure_keeps_stage_one_prediction_disabled(tmp_path, monkeypatch):
    _test_release(tmp_path)
    predictors = load_predictors(enabled=True, artifact_dir=tmp_path)
    result = _processor(predictors, monkeypatch, extraction_status="insufficient_quality")(_payload())
    target = result["prediction_extension"]["targets"][TARGET]
    assert target["status"] == "unavailable"
    assert target["value"] is None and target["confidence"] is None
    assert target["quality_status"] == "not_evaluated"
    assert target["abstention_reason"] == "prediction_not_enabled"


def test_injected_predictor_is_not_called_in_stage_one(monkeypatch):
    class FailingPredictor:
        feature_names = ("video.sampled_closed_fraction",)

        def predict(self, request):
            raise RuntimeError("private backend path and token=do-not-return")

    processor = _processor({TARGET: FailingPredictor()}, monkeypatch)
    result = processor(_payload())
    target = result["prediction_extension"]["targets"][TARGET]
    assert target["status"] == "unavailable"
    assert target["abstention_reason"] == "prediction_not_enabled"
    assert "do-not-return" not in json.dumps(result)
