"""Software contract tests; adapter readiness is not evidence of model validity."""
import pytest

from app.ml.dual_feature_adapter import build_dual_predictor_input


def test_stage1_audio_video_features_are_versioned_and_missingness_is_explicit():
    envelopes = {
        "video": {"extraction_status": "available", "features": {"sampled_closed_fraction": 0.1}},
        "audio": {"extraction_status": "insufficient_quality", "features": {"voice_activity_window_fraction": 0.5}},
    }
    result = build_dual_predictor_input("subjective_fatigue", envelopes,
        ["video.sampled_closed_fraction", "audio.voice_activity_window_fraction"])
    assert result["schema_version"] == "wellar.dual-predictor-input/1.0"
    assert result["feature_mapping_version"] == "1.2"
    assert result["features"] == {"video": {"sampled_closed_fraction": 0.1}}
    assert result["modality_status"] == {"video": "available", "audio": "insufficient_quality"}
    assert result["feature_missingness"] == 0.5
    assert result["reference_data_excluded"] is True
    assert result["prediction_status"] == "not_invoked"
    assert result["ai_validation_status"] == "not_validated"


def test_direct_reaction_task_feature_is_rejected_to_prevent_target_leakage():
    with pytest.raises(ValueError, match="not predictor inputs"):
        build_dual_predictor_input("vigilance_impairment", {}, ["reaction_task.median_valid_rt_ms"])


def test_audio_activity_features_remain_descriptive_numeric_candidates():
    result = build_dual_predictor_input("vigilance_impairment", {
        "audio": {"extraction_status": "available", "features": {
            "voice_activity_window_fraction": 0.7,
            "speech_like_window_fraction": 0.62,
            "voice_activity_status": "speech_like_activity_heuristic",
        }},
    }, ["audio.voice_activity_window_fraction", "audio.speech_like_window_fraction"])
    assert result["features"] == {"audio": {
        "voice_activity_window_fraction": 0.7,
        "speech_like_window_fraction": 0.62,
    }}
    assert result["ai_validation_status"] == "not_validated"


def test_unsupported_target_does_not_fall_back_to_combined_score():
    with pytest.raises(ValueError, match="unsupported dual-output"):
        build_dual_predictor_input("fatigue_and_vigilance", {}, ["video.sampled_closed_fraction"])
