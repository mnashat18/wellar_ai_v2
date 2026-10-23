"""Software-only tests for the non-predictive behavioral evidence contract."""
from __future__ import annotations

import json

from app.behavioral.engine import CONTRACT_VERSION, build_behavioral_evidence
from app.measurements.dual_output import dual_measurement_output
from app.media.extractors import temporal_eye_features
from app.scans.schemas import ReactionTask
from app.workers.worker import Worker
from tests.media_fixtures import synthetic_face_image_bytes, video_file, wav_bytes


def _envelope(modality, extractor, version, features, *, status="available", quality=None):
    return {
        "schema_version": "2.0", "modality": modality, "presence": "present",
        "extraction_status": status,
        "quality": {"schema_version": "1.0", "modality": modality,
                    "status": status, "coverage": 1.0, "missingness": 0.0,
                    "provenance": extractor, **(quality or {})},
        "features": features,
        "provenance": {"extractor": extractor, "version": version},
    }


def _image(*, status="available", quality=None, **updates):
    features = {"face_visible": True, "face_count": 1, "eye_aspect_ratio": 0.24,
                "mouth_aspect_ratio": 0.31, "mean_luminance": 110.0,
                "mean_blur_variance": 85.0}
    features.update(updates)
    return _envelope("image", "opencv_image_quality_mediapipe_face_mesh_visibility",
                     "mediapipe-0.10.21-opencv-4.10.0", features, status=status,
                     quality=quality or {"low_light": False, "blurred": False})


def _video(**updates):
    features = {
        "observation_count": 12, "valid_face_count": 12, "actual_sample_hz": 4.0,
        "video_duration_seconds": 3.0, "face_coverage": 1.0, "overall_coverage": 1.0,
        "face_track_continuity_status": "spatial_continuity_established",
        "short_eye_feature_status": "available", "short_clip_eye_closure_fraction": 0.12,
        "median_eye_aspect_ratio": 0.25, "median_mouth_aspect_ratio": 0.28,
        "blink_event_count": 2, "closure_event_count": 2,
        "blink_rate_per_minute": 40.0, "closure_duration_mean_seconds": 0.25,
        "closure_duration_max_seconds": 0.3, "mouth_open_event_count": 1,
        "mouth_open_event_duration_mean_seconds": 0.6, "sampled_mouth_open_fraction": 0.1,
        "yaw_proxy": 0.05, "pitch_proxy": -0.08,
    }
    features.update(updates)
    return _envelope("video", "opencv_decode_mediapipe_face_mesh_eye_mouth_geometry",
                     "mediapipe-0.10.21-opencv-4.10.0", features,
                     quality={"face_track_continuity_established": True,
                              "low_light": False, "blurred": False, "multiple_faces": False})


def _reaction_envelope(valid_count=3):
    return _envelope("reaction_task", "wellar-reaction-task-integrity-check", "1.1",
                     {"valid_reaction_times_ms": [280, 310, 400][:valid_count],
                      "valid_trial_count": valid_count, "submitted_trial_count": valid_count,
                      "valid_trial_fraction": 1.0, "server_verified": False,
                      "clinical_pvt_equivalence": "not_established"})


def _task_and_measurement():
    task = ReactionTask(task_type="simple_reaction", device_clock="monotonic",
                        timestamp_resolution_ms=4, client_reported_completed=True,
                        planned_trial_count=3, trials=[
        {"trial_index": i, "stimulus_timestamp_ms": 10000 + i * 10000,
         "response_timestamp_ms": 10000 + i * 10000 + delta}
        for i, delta in enumerate((280, 310, 400))
    ]).model_dump(mode="json")
    measurements = dual_measurement_output(task)
    return task, measurements


def test_versioned_outputs_project_real_envelope_features_without_scores():
    _, measurements = _task_and_measurement()
    result = build_behavioral_evidence({"image": _image(), "video": _video(),
                                        "reaction_task": _reaction_envelope()}, measurements)
    assert result["schema_version"] == CONTRACT_VERSION
    visible = result["visible_fatigue_indicators"]
    assert visible["status"] == "partial_evidence"  # temporal events withheld at 4 Hz
    assert visible["observations"]["eye_aspect_ratio"]["value"] == 0.24
    assert visible["observations"]["short_clip_eye_closure_fraction"]["value"] == 0.12
    assert "blink_event_count" not in visible["observations"]
    assert "mouth_open_event_count" not in visible["observations"]
    assert "sampled_mouth_open_fraction" not in visible["observations"]
    assert "sampling_rate_insufficient_for_blink_resolution" in visible["abstention_reasons"]
    vigilance = result["vigilance_indicators"]
    assert vigilance["status"] == "observed"
    assert vigilance["observations"]["valid_reaction_times_ms"]["value"] == [280, 310, 400]
    assert vigilance["observations"]["median_valid_rt_ms"]["value"] == 310
    assert vigilance["observations"]["device_timing"]["server_verified"] is False
    assert result["model_prediction"]["status"] == "disabled"
    assert "fatigue_score" not in result["visible_fatigue_indicators"]["observations"]
    assert result["model_prediction"]["validated_occupational_fatigue_prediction"] is False
    json.dumps(result, allow_nan=False)


def test_tracking_failure_and_short_observation_abstain_temporal_events():
    _, measurements = _task_and_measurement()
    video = _video(face_track_continuity_status="unestablished",
                   short_eye_feature_status="face_track_continuity_unestablished")
    video["quality"]["face_track_continuity_established"] = False
    result = build_behavioral_evidence({"video": video}, measurements)
    visible = result["visible_fatigue_indicators"]
    assert visible["status"] == "partial_evidence"
    assert not any(key in visible["observations"] for key in (
        "blink_event_count", "closure_duration_mean_seconds", "mouth_open_event_count",
        "short_clip_eye_closure_fraction", "yaw_proxy", "pitch_proxy"))
    assert "face_track_continuity_unestablished" in visible["abstention_reasons"]


def test_unsupported_extractor_version_fails_closed_but_other_output_remains_available():
    _, measurements = _task_and_measurement()
    image = _image()
    image["provenance"]["version"] = "future/99"
    result = build_behavioral_evidence({"image": image, "reaction_task": _reaction_envelope()}, measurements)
    assert result["visible_fatigue_indicators"]["status"] == "insufficient_evidence"
    assert result["visible_fatigue_indicators"]["observations"] == {}
    assert "unsupported_extractor_version" in result["visible_fatigue_indicators"]["abstention_reasons"]
    assert result["vigilance_indicators"]["status"] == "observed"


def test_invalid_ranges_and_nan_features_are_never_projected():
    image = _image(eye_aspect_ratio=float("nan"), face_count=-1, mouth_aspect_ratio=9.0)
    result = build_behavioral_evidence({"image": image}, {})
    observations = result["visible_fatigue_indicators"]["observations"]
    assert "eye_aspect_ratio" not in observations
    assert "face_count" not in observations
    assert "mouth_aspect_ratio" not in observations
    json.dumps(result, allow_nan=False)


def test_low_quality_image_is_reported_but_landmark_measurements_are_not_projected():
    image = _image(status="insufficient_quality", quality={"reason": "low_light"})
    result = build_behavioral_evidence({"image": image}, {})["visible_fatigue_indicators"]
    assert result["status"] == "partial_evidence"
    assert "eye_aspect_ratio" not in result["observations"]
    assert result["observations"]["low_light"]["value"] is True
    assert result["quality"]["camera_occlusion"]["status"] == "not_assessed"
    assert "image_low_light" in result["abstention_reasons"]


def test_stage1_temporal_extractor_emits_bounded_eye_and_mouth_geometry_summaries():
    samples = [
        {"timestamp_seconds": 0.0, "face_valid": True, "closed": False, "mouth_open": False,
         "eye_aspect_ratio": 0.24, "mouth_aspect_ratio": 0.3, "yaw_proxy": 0.1, "pitch_proxy": 0.2},
        {"timestamp_seconds": 0.25, "face_valid": True, "closed": True, "mouth_open": True,
         "eye_aspect_ratio": 0.16, "mouth_aspect_ratio": 0.6, "yaw_proxy": 0.2, "pitch_proxy": 0.3},
        {"timestamp_seconds": 0.5, "face_valid": True, "closed": False, "mouth_open": False,
         "eye_aspect_ratio": 0.26, "mouth_aspect_ratio": 0.32, "yaw_proxy": 0.1, "pitch_proxy": 0.2},
    ]
    features = temporal_eye_features(samples, 0.75, 4.0)
    assert features["median_eye_aspect_ratio"] == 0.24
    assert features["median_mouth_aspect_ratio"] == 0.32
    video_features = dict(features)
    video_features.update(actual_sample_hz=4.0, video_duration_seconds=0.75,
                          face_track_continuity_status="spatial_continuity_established")
    envelope = _video(**video_features)
    projected = build_behavioral_evidence({"video": envelope}, {})["visible_fatigue_indicators"]["observations"]
    assert projected["median_eye_aspect_ratio"]["value"] == 0.24
    assert projected["median_mouth_aspect_ratio"]["value"] == 0.32
    assert "blink_event_count" not in projected  # 4 Hz is too sparse for this event


def test_audio_is_quality_only_and_missing_task_is_not_normal_vigilance():
    audio = _envelope("audio", "python_wave_pcm_acoustic_summary_spectral_vad_heuristic",
                      "wellar-audio-contract-1.2",
                      {"voice_activity_status": "speech_like_activity_heuristic",
                       "voice_activity_window_fraction": 0.8})
    result = build_behavioral_evidence({"audio": audio}, {})
    assert result["visible_fatigue_indicators"]["status"] == "not_provided"
    assert result["visible_fatigue_indicators"]["observations"] == {}
    assert result["vigilance_indicators"]["status"] == "not_provided"
    assert "reaction_task_measurement_missing" in result["vigilance_indicators"]["abstention_reasons"]


def test_reaction_metrics_keep_independent_status_and_unverified_client_clock():
    task, measurements = _task_and_measurement()
    envelopes = {"reaction_task": _reaction_envelope(), "image": _image()}
    result = build_behavioral_evidence(envelopes, measurements)
    vigilance = result["vigilance_indicators"]
    assert vigilance["status"] == "observed"
    assert vigilance["observations"]["valid_trial_count"]["value"] == 3
    assert vigilance["observations"]["submitted_trial_count"]["value"] == 3
    assert vigilance["observations"]["device_timing"]["client_clock_claim"] == "monotonic"
    assert vigilance["observations"]["device_timing"]["server_verified"] is False
    assert vigilance["quality"]["clinical_pvt_equivalence"] == "not_established"
    assert result["visible_fatigue_indicators"]["status"] == "observed"
    assert "overall_concentration" not in vigilance["observations"]


def test_reaction_false_starts_omissions_late_responses_and_cross_trial_anomalies_are_separate():
    task = {"task_type": "simple_reaction", "trials": [
        {"trial_index": 0, "stimulus_timestamp_ms": 10000, "response_timestamp_ms": 10080},
        {"trial_index": 1, "stimulus_timestamp_ms": 10200, "response_timestamp_ms": 20200},
        {"trial_index": 2, "stimulus_timestamp_ms": 20000, "response_timestamp_ms": None},
        {"trial_index": 3, "stimulus_timestamp_ms": 30000, "response_timestamp_ms": 32501},
    ]}
    measurements = dual_measurement_output(task)
    envelope = _reaction_envelope(valid_count=0)
    value = measurements["vigilance"]["value"]
    envelope["features"] = {key: value[key] for key in (
        "valid_reaction_times_ms", "valid_trial_count", "submitted_trial_count", "valid_trial_fraction",
        "server_verified", "clinical_pvt_equivalence")}
    result = build_behavioral_evidence({"reaction_task": envelope}, measurements)["vigilance_indicators"]
    assert result["status"] == "insufficient_evidence"
    assert result["observations"]["false_start_count"]["value"] == 1
    assert result["observations"]["ambiguous_timestamp_count"]["value"] == 1
    assert result["observations"]["missed_response_count"]["value"] == 1
    assert result["observations"]["late_response_count"]["value"] == 1
    assert result["observations"]["valid_trial_count"]["value"] == 0


def test_reaction_feature_and_measurement_mismatch_fails_closed():
    _, measurements = _task_and_measurement()
    envelope = _reaction_envelope()
    envelope["features"]["median_valid_rt_ms"] = 999
    result = build_behavioral_evidence({"reaction_task": envelope}, measurements)["vigilance_indicators"]
    assert result["status"] == "insufficient_evidence"
    assert "reaction_task_envelope_measurement_mismatch" in result["abstention_reasons"]


def test_missing_modalities_are_independent_and_optional_self_report_is_not_injected():
    no_measurements = dual_measurement_output(None)
    result = build_behavioral_evidence({}, no_measurements)
    assert result["visible_fatigue_indicators"]["status"] == "not_provided"
    assert result["vigilance_indicators"]["status"] == "not_provided"
    self_report = dual_measurement_output(None, legacy_reference={
        "score": 8, "collection_consent": True, "question_version": "wellar-fatigue-self-report/1.0"
    })
    result = build_behavioral_evidence({"image": _image()}, self_report)
    assert result["visible_fatigue_indicators"]["status"] == "observed"
    assert "self_report" not in json.dumps(result["visible_fatigue_indicators"]).lower()
    assert self_report["fatigue"]["value"] == 8


def test_real_scan_api_adds_contract_and_preserves_legacy_result_fields(client, auth, store):
    accepted = client.post("/api/v1/scans", headers={**auth, "Idempotency-Key": "behavioral-contract"},
                           json={"schema_version": "1.0", "media_refs": [], "reaction_task": None})
    assert accepted.status_code == 202
    scan_id = accepted.json()["scan_id"]
    assert Worker(store, "behavioral-api-test").run_once()
    response = client.get(f"/api/v1/scans/{scan_id}/result", headers=auth)
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "insufficient_evidence"
    assert "targets" in result and "measurements" in result and "features" in result
    assert result["behavioral_evidence"]["schema_version"] == CONTRACT_VERSION
    assert result["behavioral_evidence"]["visible_fatigue_indicators"]["status"] == "not_provided"
    assert result["behavioral_evidence"]["vigilance_indicators"]["status"] == "not_provided"


def test_synthetic_image_runs_through_real_scan_extractor_into_behavioral_api(client, auth, store):
    upload = client.post("/api/v1/media/image", headers={**auth, "Content-Type": "image/png"},
                         content=synthetic_face_image_bytes())
    assert upload.status_code == 201
    body = {"schema_version": "1.0", "media_refs": [upload.json()["media_ref"]],
            "reaction_task": None,
            "consent": {"media_processing": True, "consent_version": "operational-media-v1",
                        "research_use": False}}
    accepted = client.post("/api/v1/scans", headers={**auth, "Idempotency-Key": "behavioral-synthetic-image"}, json=body)
    assert accepted.status_code == 202
    assert Worker(store, "behavioral-image-test", client.app.state.worker_processor).run_once()
    result = client.get(f"/api/v1/scans/{accepted.json()['scan_id']}/result", headers=auth).json()
    assert result["features"]["image"]["provenance"]["extractor"] == "opencv_image_quality_mediapipe_face_mesh_visibility"
    expected = build_behavioral_evidence(result["features"], result["measurements"])
    assert result["behavioral_evidence"] == expected
    assert result["behavioral_evidence"]["vigilance_indicators"]["status"] == "not_provided"


def test_complete_synthetic_multimodal_scan_returns_independent_observed_outputs(client, auth, store, tmp_path):
    uploads = [
        client.post("/api/v1/media/image", headers={**auth, "Content-Type": "image/png"},
                    content=synthetic_face_image_bytes()),
        client.post("/api/v1/media/video", headers={**auth, "Content-Type": "video/x-msvideo"},
                    content=video_file(tmp_path / "behavioral.avi")),
        client.post("/api/v1/media/audio", headers={**auth, "Content-Type": "audio/wav"},
                    content=wav_bytes()),
    ]
    assert all(response.status_code == 201 for response in uploads)
    reaction = {"task_type": "simple_reaction", "device_clock": "monotonic",
                "timestamp_resolution_ms": 4, "planned_trial_count": 3,
                "client_reported_completed": True,
                "trials": [{"trial_index": i, "stimulus_timestamp_ms": 10000 + i * 10000,
                            "response_timestamp_ms": 10000 + i * 10000 + delta}
                           for i, delta in enumerate((280, 310, 400))]}
    body = {"schema_version": "1.0", "media_refs": [response.json()["media_ref"] for response in uploads],
            "reaction_task": reaction,
            "consent": {"media_processing": True, "consent_version": "operational-media-v1",
                        "research_use": False}}
    accepted = client.post("/api/v1/scans", headers={**auth, "Idempotency-Key": "behavioral-all-modalities"}, json=body)
    assert accepted.status_code == 202
    assert Worker(store, "behavioral-all-modalities", client.app.state.worker_processor).run_once()
    result = client.get(f"/api/v1/scans/{accepted.json()['scan_id']}/result", headers=auth).json()
    assert all(result["features"][name]["presence"] == "present" for name in ("image", "video", "audio", "reaction_task"))
    assert result["features"]["image"]["extraction_status"] == "available"
    assert result["features"]["video"]["extraction_status"] == "insufficient_quality"
    evidence = result["behavioral_evidence"]
    assert evidence["visible_fatigue_indicators"]["status"] == "partial_evidence"
    assert "eye_aspect_ratio" in evidence["visible_fatigue_indicators"]["observations"]
    assert evidence["vigilance_indicators"]["status"] == "observed"
    assert evidence["vigilance_indicators"]["observations"]["valid_reaction_times_ms"]["value"] == [280, 310, 400]
    assert evidence["vigilance_indicators"]["observations"]["device_timing"]["server_verified"] is False
    assert evidence["vigilance_indicators"]["quality"]["audio_context"]["inference_scope"] == "quality_only"
    assert evidence["model_prediction"]["status"] == "disabled"
    assert result["prediction_extension"]["inference_status"] == "disabled"
    assert result["measurements"]["fatigue"]["status"] == "unavailable"
    assert result["measurements"]["vigilance"]["status"] == "observed_unverified"
    expected_output_fields = {
        "status", "measurement_type", "observations", "units", "source_modalities",
        "quality", "coverage", "evidence", "limitations", "method_version",
        "inference_kind", "validation_status", "abstention_reasons",
    }
    assert expected_output_fields <= evidence["visible_fatigue_indicators"].keys()
    assert expected_output_fields <= evidence["vigilance_indicators"].keys()
    assert result["schema_version"] == "2.0"
    assert "behavioral_evidence" in result
    serialized = json.dumps(result, sort_keys=True)
    assert all(upload.json()["media_ref"] not in serialized for upload in uploads)
    assert "media_bytes" not in serialized and "authorization" not in serialized
    def has_key(value, key):
        if isinstance(value, dict):
            return key in value or any(has_key(child, key) for child in value.values())
        if isinstance(value, list):
            return any(has_key(child, key) for child in value)
        return False
    assert not has_key(result, "fatigue_score")
    assert not has_key(evidence["model_prediction"], "confidence")
    assert all(target["value"] is None for target in result["prediction_extension"]["targets"].values())
    assert all(target.get("confidence") is None for target in result["prediction_extension"]["targets"].values())
