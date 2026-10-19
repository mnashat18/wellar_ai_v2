"""Synthetic-only contract tests; values below do not describe real participants."""
import pytest

from research.evaluation_v1 import ResearchDataError
from research.manifest_validation import validate_manifest_record, validate_manifest_splits


T0 = "2026-01-02T08:00:00Z"
T1 = "2026-01-02T08:00:05Z"
T2 = "2026-01-02T08:01:00Z"
T3 = "2026-01-02T08:02:00Z"
T4 = "2026-01-02T08:03:00Z"
DIGEST = "a" * 64


def synthetic_record(sample="sample-001", participant="participant-001", session="session-001"):
    """Reproducible fabricated metadata; no real media or consent record exists."""
    return {
        "schema_version": "wellar-pilot-manifest/1.0",
        "fixture_marker": "unit_test_synthetic",
        "study_id": "synthetic-study",
        "protocol_version": "synthetic-v1",
        "protocol_sha256": DIGEST,
        "sample_id": sample,
        "participant_id": participant,
        "session_id": session,
        "session_sequence": 1,
        "context": {
            "pre_or_post_shift": "pre_shift", "site_code": "synthetic-site",
            "device_code": "synthetic-device", "os_code": "synthetic-os",
            "industry_code": "synthetic-industry", "occupation_code": "synthetic-occupation",
            "collection_time_utc": T0, "clock_sync_offset_ms": 0,
            "clock_sync_uncertainty_ms": 1.0,
        },
        "consent": {
            "consent_record_ref": "synthetic-consent-ref", "consent_version": "synthetic-v1",
            "status": "granted", "withdrawn_at_utc": None,
            "permissions": {
                "video_capture_and_analysis": False, "image_capture_and_analysis": True,
                "audio_capture_and_analysis": False, "raw_media_storage": False,
                "research_evaluation": True, "model_training": False,
                "cross_site_transfer": False, "commercial_use": False,
            },
        },
        "v2_provenance": {
            "api_contract_version": "synthetic-api-v1", "client_build_hash": DIGEST,
            "extractor_version": {"video": "synthetic-video-v1", "image": "synthetic-image-v1",
                                  "audio": "synthetic-audio-v1"},
            "feature_mapping_version": "synthetic-map-v1", "worker_build_hash": DIGEST,
            "configuration_digest": DIGEST, "processed_at_utc": T2,
        },
        "modalities": {
            "video": {"availability": "not_collected", "capture_start_utc": None,
                      "capture_end_utc": None, "quality_status": "not_provided",
                      "missing_reason": "synthetic_not_collected", "feature_values": {}, "raw_media_ref": None},
            "image": {"availability": "available", "capture_start_utc": T0,
                      "capture_end_utc": T1, "quality_status": "available", "missing_reason": None,
                      "feature_values": {"brightness": 120.0, "face_ratio": None}, "raw_media_ref": None,
                      "duration_ms": 5, "format": "synthetic-jpeg", "dimensions_px": [320, 240]},
            "audio": {"availability": "not_collected", "capture_start_utc": None,
                      "capture_end_utc": None, "quality_status": "not_provided",
                      "missing_reason": "synthetic_not_collected", "feature_values": {}, "raw_media_ref": None},
        },
        "references": {
            "kss": {"instrument": "KSS", "version": "synthetic-kss-v1", "value": 4,
                    "measured_at_utc": T2, "status": "available", "missing_reason": None},
            "occupational_fatigue": {"instrument": "A-OFER-15", "version": "candidate-only",
                                     "instrument_id": "A-OFER-15", "subscale": "IR",
                                     "scoring_version": "candidate-only", "units_or_range": "candidate-only",
                                     "value": None, "measured_at_utc": None, "status": "not_collected",
                                     "missing_reason": "synthetic_unapproved_target"},
            "pvt": {"instrument": "PVT", "version": None, "protocol_id": None,
                    "started_at_utc": None, "ended_at_utc": None, "metrics": {},
                    "value": None, "measured_at_utc": None, "status": "not_collected",
                    "missing_reason": "synthetic_not_collected"},
        },
        "protocol_deviations": [],
        "data_quality_flags": [],
    }


def _validate(record):
    validate_manifest_record(record, allow_synthetic_fixture=True)


def test_complete_synthetic_record_passes_but_fixtures_are_rejected_by_default():
    record = synthetic_record()
    _validate(record)
    with pytest.raises(ResearchDataError, match="synthetic fixture"):
        validate_manifest_record(record)


@pytest.mark.parametrize("field", ["protocol_sha256", "sample_id", "session_id", "session_sequence",
                                    "context", "consent", "v2_provenance", "modalities", "references"])
def test_required_top_level_fields_are_enforced(field):
    record = synthetic_record()
    del record[field]
    with pytest.raises(ResearchDataError):
        _validate(record)


def test_unknown_fields_are_rejected():
    record = synthetic_record()
    record["unexpected"] = "value"
    with pytest.raises(ResearchDataError, match="unsupported fields"):
        _validate(record)


@pytest.mark.parametrize("permissions", [
    {"research_evaluation": True},
    {"video_capture_and_analysis": 1},
])
def test_consent_scopes_must_be_complete_explicit_booleans(permissions):
    record = synthetic_record()
    record["consent"]["permissions"] = permissions
    with pytest.raises(ResearchDataError):
        _validate(record)


def test_available_modality_requires_matching_consent_and_quality():
    record = synthetic_record()
    record["consent"]["permissions"]["image_capture_and_analysis"] = False
    with pytest.raises(ResearchDataError, match="image processing consent"):
        _validate(record)
    record = synthetic_record()
    record["modalities"]["image"]["quality_status"] = "insufficient_quality"
    with pytest.raises(ResearchDataError, match="availability and quality_status disagree"):
        _validate(record)


def test_absent_modality_requires_reason_and_null_timestamps():
    record = synthetic_record()
    record["modalities"]["audio"]["missing_reason"] = None
    with pytest.raises(ResearchDataError, match="requires a reason"):
        _validate(record)
    record = synthetic_record()
    record["modalities"]["audio"]["capture_start_utc"] = T1
    with pytest.raises(ResearchDataError, match="timestamps disagree"):
        _validate(record)


def test_inconsistent_or_non_utc_timestamps_are_rejected():
    record = synthetic_record()
    record["modalities"]["image"]["capture_end_utc"] = "2026-01-02T07:59:59Z"
    with pytest.raises(ResearchDataError, match="timestamps are out of order"):
        _validate(record)
    record = synthetic_record()
    record["references"]["kss"]["measured_at_utc"] = "2026-01-02T10:00:00+02:00"
    with pytest.raises(ResearchDataError, match="UTC offset"):
        _validate(record)  # Offset timestamps are not canonical UTC values.
    record = synthetic_record()
    record["references"]["kss"]["measured_at_utc"] = "2026-01-02T08:00:00"
    with pytest.raises(ResearchDataError, match="UTC offset"):
        _validate(record)


def test_missing_reference_requires_null_value_and_reason():
    record = synthetic_record()
    record["references"]["kss"].update(status="not_provided", value=None, measured_at_utc=None,
                                         missing_reason="synthetic_missing")
    _validate(record)
    record["references"]["kss"]["missing_reason"] = None
    with pytest.raises(ResearchDataError, match="explicit missing_reason"):
        _validate(record)


@pytest.mark.parametrize("value", [0, 10, 4.5, True])
def test_kss_requires_integer_ordinal_value_from_one_to_nine(value):
    record = synthetic_record()
    record["references"]["kss"]["value"] = value
    with pytest.raises(ResearchDataError, match="integer from 1 through 9"):
        _validate(record)


def test_unapproved_fatigue_mapping_is_rejected_without_guessing():
    record = synthetic_record()
    record["references"]["occupational_fatigue"].update(
        status="available", value=12.0, measured_at_utc=T2, missing_reason=None,
    )
    with pytest.raises(ResearchDataError, match="not scientifically approved"):
        _validate(record)


def test_pvt_requires_independent_protocol_and_ordered_reference_interval():
    record = synthetic_record()
    record["references"]["pvt"].update(
        version="synthetic-pvt-v1", protocol_id="synthetic-pvt-protocol", started_at_utc=T3,
        ended_at_utc=T4, metrics={"median_reaction_time_ms": 300.0}, measured_at_utc=T4,
        status="available", missing_reason=None,
    )
    _validate(record)
    record["references"]["pvt"]["ended_at_utc"] = T1
    with pytest.raises(ResearchDataError, match="timestamps are inconsistent"):
        _validate(record)


@pytest.mark.parametrize("key,value", [
    ("employee_number", "synthetic-123"), ("raw_media", "not-real-bytes"),
    ("image_payload", "data:image/jpeg;base64,AAAA"),
])
def test_direct_identifiers_and_embedded_media_are_rejected(key, value):
    record = synthetic_record()
    record["test_extension"] = {key: value}
    with pytest.raises(ResearchDataError):
        _validate(record)


def test_email_address_in_any_value_is_rejected():
    record = synthetic_record()
    record["study_id"] = "fabricated@example.invalid"
    with pytest.raises(ResearchDataError, match="direct contact information"):
        _validate(record)


def test_raw_media_reference_requires_explicit_storage_permission():
    record = synthetic_record()
    record["modalities"]["image"]["raw_media_ref"] = "opaque-object-001"
    with pytest.raises(ResearchDataError, match="storage consent"):
        _validate(record)


def test_non_finite_feature_values_are_rejected():
    record = synthetic_record()
    record["modalities"]["image"]["feature_values"]["brightness"] = float("nan")
    with pytest.raises(ResearchDataError, match="finite numbers"):
        _validate(record)


def test_participant_disjoint_split_validation_accepts_repeated_same_split():
    records = [synthetic_record("sample-001", "participant-001", "session-001"),
               synthetic_record("sample-002", "participant-001", "session-002"),
               synthetic_record("sample-003", "participant-002", "session-003")]
    validate_manifest_splits(records, {"sample-001": "train", "sample-002": "train", "sample-003": "test"})


def test_participant_split_leakage_and_missing_assignments_are_rejected():
    records = [synthetic_record("sample-001", "participant-001", "session-001"),
               synthetic_record("sample-002", "participant-001", "session-002")]
    with pytest.raises(ResearchDataError, match="participant_id leakage"):
        validate_manifest_splits(records, {"sample-001": "train", "sample-002": "test"})
    with pytest.raises(ResearchDataError, match="exactly one split"):
        validate_manifest_splits(records, {"sample-001": "train"})
    with pytest.raises(ResearchDataError, match="known split"):
        validate_manifest_splits(records, {"sample-001": "train", "sample-002": "holdout"})


def test_repeated_sessions_for_one_participant_cannot_cross_splits():
    records = [synthetic_record("sample-001", "participant-001", "session-001"),
               synthetic_record("sample-002", "participant-001", "session-001")]
    with pytest.raises(ResearchDataError, match="participant_id leakage"):
        validate_manifest_splits(records, {"sample-001": "train", "sample-002": "validation"})


def test_duplicate_sample_ids_are_rejected_for_split_checks():
    records = [synthetic_record("sample-001"), synthetic_record("sample-001", "participant-002")]
    with pytest.raises(ResearchDataError, match="sample_id values must be unique"):
        validate_manifest_splits(records, {"sample-001": "train"})
