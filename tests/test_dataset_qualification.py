"""Metadata-only tests: identifiers and labels are invented test values."""
import pytest

from research.dataset_qualification import qualify_metadata, validate_subject_splits
from research.evaluation_v1 import ResearchDataError


def _metadata(dataset="DROZY"):
    if dataset == "DROZY":
        label_sources = {"reference_sleepiness": "KSS.txt.score_1_9"}
        feature_mappings = {"video.sampled_closed_fraction": "video_nir_mp4"}
    else:
        label_sources = {"reference_sleepiness": "self_reported_drowsiness_category_0_5_10"}
        feature_mappings = {"video.sampled_closed_fraction": "video_rgb_mp4"}
    return {
        "dataset_id": dataset,
        "participant_id_field": "participant_code",
        "sample_id_field": "sample_code",
        "session_id_field": "session_code",
        "requested_targets": ["reference_sleepiness"],
        "label_sources": label_sources,
        "classification_rule_ids": {},
        "feature_mappings": feature_mappings,
        "rights": {
            "research_evaluation": True,
            "model_training": True,
            "commercial_model_training": True,
            "cross_site_transfer": False,
            "written_permission_reference": "synthetic-ref-only",
            "consent_scope_reference": "synthetic-ref-only",
            "ethics_or_governance_approval_reference": "synthetic-ref-only",
        },
    }


def test_uta_category_labels_are_not_accepted_as_kss_or_vigilance():
    metadata = _metadata("UTA-RLDD")
    result = qualify_metadata(metadata)
    assert result["compatible_label_targets"] == []
    assert any("no proxy-label conversion" in reason for reason in result["blockers"])
    assert result["training_authorized"] is False
    metadata["requested_targets"] = ["vigilance_impairment"]
    metadata["label_sources"] = {"vigilance_impairment": "self_reported_drowsiness_category_0_5_10"}
    result = qualify_metadata(metadata)
    assert result["compatible_label_targets"] == []
    assert any("no proxy-label conversion" in reason for reason in result["blockers"])


def test_drozy_raw_kss_source_matches_target_but_license_still_blocks_training():
    result = qualify_metadata(_metadata("DROZY"))
    assert result["compatible_label_targets"] == ["reference_sleepiness"]
    assert result["training_ready"] is False
    assert any("license excludes commercial development" in reason for reason in result["blockers"])


def test_drozy_pvt_requires_rule_and_cannot_be_its_own_vigilance_feature():
    metadata = _metadata("DROZY")
    metadata["requested_targets"] = ["vigilance_impairment"]
    metadata["label_sources"] = {"vigilance_impairment": "pvt-rt.reaction_times"}
    metadata["feature_mappings"] = {"reaction_task.median_rt_ms": "pvt_reaction_times"}
    result = qualify_metadata(metadata)
    assert result["compatible_label_targets"] == []
    assert any("classification rule" in reason for reason in result["blockers"])
    assert any("cannot be both vigilance labels and model inputs" in reason for reason in result["blockers"])


def test_rights_and_consent_denial_remains_blocked():
    metadata = _metadata()
    metadata["rights"]["commercial_model_training"] = False
    metadata["rights"]["research_evaluation"] = False
    result = qualify_metadata(metadata)
    assert result["training_authorized"] is False
    assert any("commercial model-training permission" in reason for reason in result["blockers"])
    assert any("consent/rights" in reason for reason in result["blockers"])


def test_missing_pseudonymous_participant_field_is_blocked():
    metadata = _metadata()
    metadata["participant_id_field"] = ""
    result = qualify_metadata(metadata)
    assert any("stable pseudonymous participant ID" in reason for reason in result["blockers"])


def test_participant_split_validator_rejects_missing_ids_and_cross_split_reuse():
    with pytest.raises(ResearchDataError, match="participant and split"):
        validate_subject_splits({"s1": "p1"}, {})
    with pytest.raises(ResearchDataError, match="participant leakage"):
        validate_subject_splits({"s1": "p1", "s2": "p1"}, {"s1": "train", "s2": "test"})
    validate_subject_splits({"s1": "p1", "s2": "p1", "s3": "p2"},
                            {"s1": "train", "s2": "train", "s3": "test"})


def test_unsupported_source_modality_mapping_is_rejected():
    metadata = _metadata("UTA-RLDD")
    metadata["feature_mappings"] = {"audio.ac_rms_dbfs": "video_rgb_mp4"}
    result = qualify_metadata(metadata)
    assert any("cannot be mapped to Wellar audio" in reason for reason in result["blockers"])
