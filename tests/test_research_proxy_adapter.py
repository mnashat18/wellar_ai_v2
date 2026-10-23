import json
import shutil
from pathlib import Path

import pytest

from app.ml.research_proxy_adapter import predict_research_context, stage1_model_abstentions


ARTIFACT_ROOT = Path(__file__).resolve().parents[1] / "research" / "public_data" / "brightertime_28911977" / "models_real_proxy_v1"


@pytest.mark.parametrize("target", ["modified_kss_sleepiness", "brightertime_pvt_median_hit_rt"])
def test_pinned_real_research_artifact_loads_and_returns_nonoperational_candidate(target):
    result = predict_research_context(target, {"timeawake_hours": 8.0, "dayhour": 10.5}, artifact_root=ARTIFACT_ROOT, research_mode=True)
    assert result["status"] in {"research_only_candidate", "research_only_not_promoted"}
    assert isinstance(result["value"], float)
    assert result["confidence"] is None
    assert result["operational_release"] is False
    assert len(result["artifact_sha256"]) == 64
    assert "not occupational fatigue" in result["target_semantics"] if target == "modified_kss_sleepiness" else "not a Wellar score" in result["target_semantics"]


def test_research_mode_must_be_explicit_and_missing_or_out_of_range_features_abstain():
    default = predict_research_context("modified_kss_sleepiness", {"timeawake_hours": 8, "dayhour": 10}, artifact_root=ARTIFACT_ROOT)
    assert default["status"] == "abstained" and default["value"] is None
    for features, reason in [
        ({"timeawake_hours": 8}, "required_research_context_feature_missing_or_nonfinite"),
        ({"timeawake_hours": 40, "dayhour": 10}, "research_context_out_of_supported_range"),
        ({"timeawake_hours": 8, "dayhour": 24}, "research_context_out_of_supported_range"),
    ]:
        result = predict_research_context("modified_kss_sleepiness", features, artifact_root=ARTIFACT_ROOT, research_mode=True)
        assert result["status"] == "abstained" and result["abstention_reason"] == reason


def test_stage1_envelopes_abstain_independently_and_do_not_map_reaction_times_as_predictions():
    stage1 = {
        "image": {"features": {"face_count": 1}},
        "video": {"features": {"short_eye_closure_fraction": 0.2}},
        "audio": {"features": {"voice_activity_window_fraction": 0.3}},
        "reaction_task": {"features": {"valid_reaction_times_ms": [250, 270]}},
    }
    results = stage1_model_abstentions(stage1)
    assert results["fatigue"]["status"] == "abstained"
    assert results["vigilance"]["status"] == "abstained"
    assert results["fatigue"]["value"] is None and results["vigilance"]["value"] is None
    assert results["fatigue"]["stage1_compatible"] is False
    assert results["vigilance"]["stage1_compatible"] is False


def test_corrupted_artifact_hash_fails_closed(tmp_path):
    for source in ARTIFACT_ROOT.iterdir():
        shutil.copyfile(source, tmp_path / source.name)
    target_file = tmp_path / "modified_kss_sleepiness_ridge_v1.json"
    target_file.write_text(target_file.read_text(encoding="utf-8") + " ", encoding="utf-8")
    result = predict_research_context("modified_kss_sleepiness", {"timeawake_hours": 8, "dayhour": 10}, artifact_root=tmp_path, research_mode=True)
    assert result["status"] == "abstained"
    assert result["abstention_reason"] == "research_artifact_unavailable_or_invalid"


def test_manifest_remains_explicitly_research_only():
    manifest = json.loads((ARTIFACT_ROOT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["artifact_class"] == "research_only_not_operational"
    assert all(item["evaluation_gate"] in {"passed_research_candidate_criterion", "not_promoted_gate_not_met", "exploratory_only_historical_test_overlap"} for item in manifest["artifacts"])
