from research.short_mobile_kss_v1 import paired_kss_rejection, short_feature_rejection


FEATURE_VALUES = {
    "short_eye_closure_fraction": 0.25,
    "video_duration_seconds": 5.0,
    "short_eye_closure_observed_seconds": 4.0,
    "short_eye_valid_observation_count": 17,
    "short_eye_time_coverage": 0.8,
    "actual_sample_hz": 3.75,
}
FEATURE_PROVENANCE = {"short_eye_feature_version": "wellar-short-mobile-eye/1.0", "status": "available"}


def test_short_feature_gate_accepts_only_quality_gated_mobile_window():
    assert short_feature_rejection(FEATURE_VALUES, FEATURE_PROVENANCE) is None
    assert short_feature_rejection({**FEATURE_VALUES, "short_eye_time_coverage": 0.3}, FEATURE_PROVENANCE) == "short_eye_quality_gate_failed"
    assert short_feature_rejection({**FEATURE_VALUES, "video_duration_seconds": 300.0}, FEATURE_PROVENANCE) == "capture_duration_outside_mobile_protocol"
    assert short_feature_rejection(FEATURE_VALUES, {}) == "feature_version_mismatch"
    assert short_feature_rejection(FEATURE_VALUES, {**FEATURE_PROVENANCE, "status": "insufficient_quality"}) == "video_extraction_not_available"


def test_kss_pairing_requires_verbatim_qualified_score_immediately_after_capture():
    record = {
        "context": {"recorded_at_utc": "2026-01-01T00:00:00Z"},
        "features": {"video": {"values": {"video_duration_seconds": 5.0}}},
        "labels": {"reference_sleepiness": {"status": "observed", "instrument": "KSS",
            "qualified": True, "raw_value": 7, "class_label": 7,
            "measured_at_utc": "2026-01-01T00:00:10Z"}},
    }
    assert paired_kss_rejection(record) is None
    record["labels"]["reference_sleepiness"]["class_label"] = 1
    assert paired_kss_rejection(record) == "kss_label_must_be_qualified_observed_1_to_9_without_remapping"
    record["labels"]["reference_sleepiness"]["class_label"] = 7
    record["labels"]["reference_sleepiness"]["measured_at_utc"] = "2026-01-01T00:00:30Z"
    assert paired_kss_rejection(record) == "kss_not_collected_within_15_seconds_after_clip"
