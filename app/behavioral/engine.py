"""Build bounded, descriptive behavioral evidence from Stage 1 envelopes.

This module does not classify fatigue, sleepiness, attention, or fitness for work.
It validates and projects measured Stage 1 observations into a stable API object.
"""
from __future__ import annotations

import math
import statistics
from collections.abc import Mapping
from typing import Any


CONTRACT_VERSION = "wellar.behavioral-evidence/1.0"
METHOD_VERSION = "wellar.behavioral-engine/1.0"
ENVELOPE_VERSION = "2.0"
_EXTRACTORS = {
    "image": ("opencv_image_quality_mediapipe_face_mesh_visibility", "mediapipe-0.10.21-opencv-4.10.0"),
    "video": ("opencv_decode_mediapipe_face_mesh_eye_mouth_geometry", "mediapipe-0.10.21-opencv-4.10.0"),
    "audio": ("python_wave_pcm_acoustic_summary_spectral_vad_heuristic", "wellar-audio-contract-1.2"),
    "reaction_task": ("wellar-reaction-task-integrity-check", "1.1"),
}
_MIN_BLINK_SAMPLE_HZ = 10.0
_MIN_MOUTH_EVENT_SAMPLE_HZ = 5.0
_MIN_VALID_REACTION_TRIALS = 3


def _finite_number(value: Any, *, minimum: float | None = None, maximum: float | None = None) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return False
    return (minimum is None or value >= minimum) and (maximum is None or value <= maximum)


def _count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _envelope(envelopes: Mapping[str, Any], modality: str) -> tuple[Mapping[str, Any] | None, str | None]:
    item = envelopes.get(modality)
    if not isinstance(item, Mapping):
        return None, "modality_not_provided"
    if item.get("schema_version") != ENVELOPE_VERSION or item.get("modality") != modality:
        return None, "unsupported_feature_schema"
    if item.get("presence") != "present":
        return None, "modality_not_provided"
    if item.get("extraction_status") not in {"available", "insufficient_quality"}:
        return None, "modality_extraction_unavailable"
    provenance = item.get("provenance")
    expected = _EXTRACTORS[modality]
    if (not isinstance(provenance, Mapping) or provenance.get("extractor") != expected[0]
            or provenance.get("version") != expected[1]):
        return None, "unsupported_extractor_version"
    features = item.get("features")
    quality = item.get("quality")
    if not isinstance(features, Mapping) or not isinstance(quality, Mapping):
        return None, "invalid_feature_envelope"
    return item, None


def _quality_for(envelopes: Mapping[str, Any], modalities: tuple[str, ...]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for modality in modalities:
        item, reason = _envelope(envelopes, modality)
        if item is None:
            original = envelopes.get(modality)
            status = original.get("extraction_status") if isinstance(original, Mapping) else "not_provided"
            quality = original.get("quality") if isinstance(original, Mapping) else None
            result[modality] = {
                "status": status if isinstance(status, str) else "not_provided",
                "reason": reason,
                "coverage": quality.get("coverage") if isinstance(quality, Mapping) and _finite_number(quality.get("coverage"), minimum=0, maximum=1) else None,
                "quality_reason": quality.get("reason") if isinstance(quality, Mapping) and isinstance(quality.get("reason"), str) else None,
            }
            continue
        quality = item["quality"]
        result[modality] = {
            "status": quality.get("status", item.get("extraction_status")),
            "reason": quality.get("reason") if isinstance(quality.get("reason"), str) else None,
            "coverage": quality.get("coverage") if _finite_number(quality.get("coverage"), minimum=0, maximum=1) else None,
        }
    return result


def _observation(value: Any, units: str, *, source: str, description_code: str) -> dict[str, Any]:
    return {"value": value, "units": units, "source": source, "explanation_code": description_code}


def _audio_quality_context(envelopes: Mapping[str, Any]) -> dict[str, Any]:
    audio, error = _envelope(envelopes, "audio")
    if audio is None:
        return {"status": "not_provided" if error == "modality_not_provided" else "unavailable",
                "reason": error, "inference_scope": "quality_only"}
    f = audio["features"]
    activity_status = f.get("voice_activity_status")
    allowed = {"silence", "uncertain_noisy_or_non_speech", "uncertain_clipped_signal", "speech_like_activity_heuristic"}
    return {
        "status": audio["extraction_status"],
        "reason": audio["quality"].get("reason") if isinstance(audio["quality"].get("reason"), str) else None,
        "signal_activity_status": activity_status if activity_status in allowed else None,
        "signal_activity_semantics": "heuristic_quality_and_participation_context_only_not_validated_speech_detection",
        "inference_scope": "quality_only",
    }


def _visible(envelopes: Mapping[str, Any]) -> dict[str, Any]:
    observations: dict[str, Any] = {}
    evidence: list[dict[str, Any]] = []
    reasons: set[str] = set()
    seen_media = False
    image, image_error = _envelope(envelopes, "image")
    video, video_error = _envelope(envelopes, "video")
    for raw in (envelopes.get("image"), envelopes.get("video")):
        if isinstance(raw, Mapping) and raw.get("presence") == "present":
            seen_media = True
    for error in (image_error, video_error):
        if error and error != "modality_not_provided":
            reasons.add(error)

    if image is not None:
        f = image["features"]
        quality = image["quality"]
        for field, units, code, min_v, max_v in (
            ("face_count", "count", "face_count_observed", 0, 100),
        ):
            value = f.get(field)
            valid = _count(value) if units == "count" else _finite_number(value, minimum=min_v, maximum=max_v)
            if valid:
                observations[field] = _observation(value, units, source="image", description_code=code)
        if image["extraction_status"] == "available":
            for field, units, code, minimum, maximum in (
                ("eye_aspect_ratio", "ratio", "eye_geometry_observed", 0, 1),
                ("mouth_aspect_ratio", "ratio", "mouth_geometry_observed", 0, 2),
            ):
                value = f.get(field)
                if _finite_number(value, minimum=minimum, maximum=maximum):
                    observations[field] = _observation(value, units, source="image", description_code=code)
        if f.get("face_visible") is True:
            observations["face_presence"] = _observation(True, "boolean", source="image", description_code="single_face_detected")
        elif f.get("face_visible") is False:
            observations["face_presence"] = _observation(False, "boolean", source="image", description_code="face_not_detected_or_ambiguous")
        for key, field, units, code, low_code in (
            ("low_light", "mean_luminance", "grayscale_level", "illumination_observed", "low_illumination"),
            ("blurred", "mean_blur_variance", "variance", "sharpness_observed", "blur_observed"),
        ):
            flag = quality.get(key)
            value = f.get(field)
            if isinstance(flag, bool):
                observations[key] = _observation(flag, "boolean", source="image_quality", description_code=low_code if flag else code)
            if _finite_number(value, minimum=0):
                observations[field] = _observation(value, units, source="image_quality", description_code=code)
        image_reason = quality.get("reason")
        if image_reason in {"low_light", "blurred_image", "multiple_faces_detected", "face_not_detected", "insufficient_face_landmarks"}:
            reasons.add(f"image_{image_reason}")
        if image_reason == "low_light":
            observations["low_light"] = _observation(True, "boolean", source="image_quality", description_code="low_illumination")
        if image_reason == "blurred_image":
            observations["blurred"] = _observation(True, "boolean", source="image_quality", description_code="blur_observed")
        evidence.append({"modality": "image", "extractor": image["provenance"]["extractor"], "extractor_version": image["provenance"]["version"]})
        if image["extraction_status"] != "available":
            reasons.add("image_quality_insufficient")
    elif image_error != "modality_not_provided":
        reasons.add(image_error or "image_unavailable")

    if video is not None:
        f = video["features"]
        quality = video["quality"]
        for field, units, code, low, high in (
            ("valid_face_count", "count", "valid_face_observations", 0, 100000),
            ("observation_count", "count", "video_samples_observed", 0, 100000),
            ("actual_sample_hz", "Hz", "video_sampling_rate_observed", 0.01, 240),
            ("video_duration_seconds", "seconds", "video_observation_window", 0, 300),
            ("face_coverage", "fraction", "face_coverage_observed", 0, 1),
            ("overall_coverage", "fraction", "video_coverage_observed", 0, 1),
        ):
            value = f.get(field)
            valid = _count(value) if units == "count" else _finite_number(value, minimum=low, maximum=high)
            if valid:
                observations[field] = _observation(value, units, source="video", description_code=code)
        track_continuity = f.get("face_track_continuity_status") == "spatial_continuity_established"
        continuity = track_continuity and video["extraction_status"] == "available"
        if not continuity:
            if not track_continuity:
                reasons.add("face_track_continuity_unestablished")
            elif video["extraction_status"] != "available":
                reasons.add("video_quality_insufficient")
        else:
            ear = f.get("median_eye_aspect_ratio")
            mar = f.get("median_mouth_aspect_ratio")
            if _finite_number(ear, minimum=0, maximum=1):
                observations["median_eye_aspect_ratio"] = _observation(ear, "ratio", source="video", description_code="eye_geometry_observed")
            if _finite_number(mar, minimum=0, maximum=2):
                observations["median_mouth_aspect_ratio"] = _observation(mar, "ratio", source="video", description_code="mouth_geometry_observed")

            if f.get("short_eye_feature_status") == "available" and _finite_number(f.get("short_clip_eye_closure_fraction"), minimum=0, maximum=1):
                observations["short_clip_eye_closure_fraction"] = _observation(
                    f["short_clip_eye_closure_fraction"], "fraction", source="video",
                    description_code="short_clip_observed_eye_closure_not_perclos",
                )
            else:
                reasons.add("short_clip_eye_closure_unavailable_or_insufficient")

            hz = f.get("actual_sample_hz")
            enough_blink_sampling = _finite_number(hz, minimum=_MIN_BLINK_SAMPLE_HZ)
            if enough_blink_sampling:
                for field, units, code in (
                    ("blink_event_count", "count", "sampled_blink_events"),
                    ("closure_event_count", "count", "sampled_eye_closure_events"),
                ):
                    if _count(f.get(field)):
                        observations[field] = _observation(f[field], units, source="video", description_code=code)
                for field, code in (("closure_duration_mean_seconds", "sampled_eye_closure_duration"), ("closure_duration_max_seconds", "sampled_eye_closure_duration")):
                    if _finite_number(f.get(field), minimum=0):
                        observations[field] = _observation(f[field], "seconds", source="video", description_code=code)
                if _finite_number(f.get("blink_rate_per_minute"), minimum=0):
                    observations["blink_rate_per_minute"] = _observation(f["blink_rate_per_minute"], "events_per_minute", source="video", description_code="sampled_blink_rate")
            else:
                reasons.add("sampling_rate_insufficient_for_blink_resolution")

            enough_mouth_sampling = _finite_number(hz, minimum=_MIN_MOUTH_EVENT_SAMPLE_HZ)
            if enough_mouth_sampling and _count(f.get("mouth_open_event_count")):
                observations["mouth_open_event_count"] = _observation(
                    f["mouth_open_event_count"], "count", source="video",
                    description_code="mouth_opening_events_not_yawn_classification",
                )
                if _finite_number(f.get("mouth_open_event_duration_mean_seconds"), minimum=0):
                    observations["mouth_open_event_duration_mean_seconds"] = _observation(
                        f["mouth_open_event_duration_mean_seconds"], "seconds", source="video",
                        description_code="sampled_mouth_opening_duration_not_yawn",
                    )
                if _finite_number(f.get("sampled_mouth_open_fraction"), minimum=0, maximum=1):
                    observations["sampled_mouth_open_fraction"] = _observation(
                        f["sampled_mouth_open_fraction"], "fraction", source="video",
                        description_code="sampled_mouth_opening_not_yawn",
                    )
            else:
                reasons.add("sampling_rate_insufficient_for_mouth_event_resolution")
            for field in ("yaw_proxy", "pitch_proxy"):
                if _finite_number(f.get(field), minimum=-2, maximum=2):
                    observations[field] = _observation(f[field], "normalized_proxy_ratio", source="video", description_code="aggregate_head_orientation_proxy_not_motion")

        for key, feature_key, reason_code in (("low_light", "light_quality", "low_illumination"), ("blurred", "blur_quality", "blur_observed"), ("multiple_faces", "multiple_faces_detected", "multiple_faces_detected")):
            feature_value = f.get(feature_key)
            observed_flag = feature_value is True if isinstance(feature_value, bool) else feature_value in {"low_light", "blurred"} if isinstance(feature_value, str) else None
            if observed_flag is not None:
                observations[f"video_{key}"] = _observation(observed_flag, "boolean", source="video_quality", description_code=reason_code)
        if quality.get("reason") in {"low_light", "blurred_image", "multiple_faces_detected", "face_track_continuity_unestablished", "insufficient_temporal_samples", "insufficient_temporal_coverage"}:
            reasons.add(f"video_{quality['reason']}")
        if not continuity:
            for name in ("blink_event_count", "closure_event_count", "closure_duration_mean_seconds", "closure_duration_max_seconds", "blink_rate_per_minute", "mouth_open_event_count", "mouth_open_event_duration_mean_seconds", "sampled_mouth_open_fraction", "short_clip_eye_closure_fraction", "yaw_proxy", "pitch_proxy"):
                observations.pop(name, None)
        evidence.append({"modality": "video", "extractor": video["provenance"]["extractor"], "extractor_version": video["provenance"]["version"], "sampling_hz": f.get("actual_sample_hz"), "tracking": f.get("face_track_continuity_status")})
        if video["extraction_status"] != "available":
            reasons.add("video_quality_insufficient")
    elif video_error != "modality_not_provided":
        reasons.add(video_error or "video_unavailable")

    if not observations:
        status = "not_provided" if not seen_media and not reasons else "insufficient_evidence"
    elif image is not None and video is not None and image["extraction_status"] == "available" and video["extraction_status"] == "available":
        status = "observed" if not reasons else "partial_evidence"
    else:
        status = "observed" if not reasons and not any(r in reasons for r in ("sampling_rate_insufficient_for_blink_resolution", "sampling_rate_insufficient_for_mouth_event_resolution")) else "partial_evidence"
    # Sampling limits are metric-specific. Keep measured geometry available but
    # mark the overall result partial whenever a requested temporal metric abstains.
    if reasons and observations and status == "observed":
        status = "partial_evidence"
    if not observations and seen_media:
        status = "insufficient_evidence"
    modality_quality = _quality_for(envelopes, ("image", "video", "audio"))
    audio_context = _audio_quality_context(envelopes)
    modality_quality["audio_context"] = audio_context
    modality_quality["camera_occlusion"] = {"status": "not_assessed", "reason_code": "camera_occlusion_not_independently_measured"}
    modality_quality["tracking_continuity"] = {
        "status": "not_provided" if video is None else "established" if video["features"].get("face_track_continuity_status") == "spatial_continuity_established" else "unestablished",
        "method": "bounded_spatial_face_box_continuity_not_identity_tracking",
    }
    modality_quality["temporal_resolution"] = {
        "sample_rate_hz": video["features"].get("actual_sample_hz") if video is not None and _finite_number(video["features"].get("actual_sample_hz"), minimum=0.01, maximum=240) else None,
        "observation_duration_seconds": video["features"].get("video_duration_seconds") if video is not None and _finite_number(video["features"].get("video_duration_seconds"), minimum=0, maximum=300) else None,
        "blink_and_closure_event_minimum_sample_hz": _MIN_BLINK_SAMPLE_HZ,
        "mouth_event_minimum_sample_hz": _MIN_MOUTH_EVENT_SAMPLE_HZ,
    }
    if modality_quality["audio"]["status"] not in {"available", "not_provided"}:
        reasons.add("audio_quality_insufficient_no_behavior_inference")
    return {
        "status": status,
        "measurement_type": "visible_fatigue_behavioral_indicators",
        "observations": observations,
        "units": "per_observation",
        "source_modalities": [name for name in ("image", "video") if modality_quality[name]["status"] != "not_provided"] + (["audio_quality_only"] if modality_quality["audio"]["status"] != "not_provided" else []),
        "quality": modality_quality,
        "coverage": {
            "image": modality_quality["image"]["coverage"] if modality_quality["image"]["coverage"] is not None else (
                image["features"].get("face_detection_ratio") if image is not None and _finite_number(image["features"].get("face_detection_ratio"), minimum=0, maximum=1) else None),
            "video": modality_quality["video"]["coverage"],
        },
        "evidence": evidence,
        "limitations": [
            "Observed behaviors can sometimes accompany tiredness but do not establish a person's fatigue level.",
            "Short-clip eye closure is not clinical PERCLOS; no normal/abnormal population threshold is applied.",
            "Audio contributes modality quality only; its activity heuristic is not validated speech detection or fatigue evidence.",
            "Camera occlusion is not independently measured; missing observations are not interpreted as normal.",
            "Head pose summaries are orientation proxies, not detected nodding or pose change.",
        ],
        "method_version": METHOD_VERSION,
        "inference_kind": "descriptive_observation_no_prediction",
        "validation_status": "software_contract_tested_not_scientifically_validated",
        "abstention_reasons": sorted(reasons),
        "explanation_codes": ["visible_behavior_observations_only", "not_a_fatigue_score"],
    }


def _vigilance(envelopes: Mapping[str, Any], measurements: Mapping[str, Any], visible: Mapping[str, Any]) -> dict[str, Any]:
    reasons: set[str] = set()
    task = measurements.get("vigilance") if isinstance(measurements, Mapping) else None
    reaction_envelope, envelope_error = _envelope(envelopes, "reaction_task")
    modality_quality = _quality_for(envelopes, ("reaction_task", "video", "audio"))
    audio_context = _audio_quality_context(envelopes)
    visual_context = {
        key: visible.get("observations", {}).get(key)
        for key in ("short_clip_eye_closure_fraction", "median_eye_aspect_ratio", "blink_event_count", "face_coverage")
        if key in visible.get("observations", {})
    }
    if not isinstance(task, Mapping) or task.get("status") == "not_provided":
        reasons.add("reaction_task_measurement_missing")
        return {
            "status": "not_provided" if envelope_error == "modality_not_provided" else "insufficient_evidence",
            "measurement_type": "brief_reaction_task_and_separate_visual_observations",
            "observations": {}, "units": {"reaction_times": "ms", "event_counts": "count", "valid_trial_fraction": "fraction"},
            "source_modalities": (["video_visual_context"] if visual_context else []) + (["audio_quality_only"] if audio_context["status"] != "not_provided" else []),
            "quality": {**modality_quality, "audio_context": audio_context, "timing_integrity": "not_assessed"},
            "coverage": {"reaction_task": None, "visual_context": visible.get("coverage", {}).get("video")},
            "evidence": ([{"visual_context_separate_from_task_performance": visual_context}] if visual_context else []) + ([{"audio_quality_only": audio_context}] if audio_context["status"] != "not_provided" else []),
            "limitations": ["No reaction task was provided; missing task data is not treated as normal vigilance."],
            "method_version": METHOD_VERSION, "inference_kind": "direct_task_observation_no_prediction",
            "validation_status": "not_scientifically_validated", "abstention_reasons": sorted(reasons),
            "explanation_codes": ["reaction_task_not_provided"],
        }

    if task.get("measurement_type") != "brief_reaction_task_observation":
        reasons.add("unsupported_reaction_task_measurement_type")
        value: Mapping[str, Any] = {}
    else:
        value = task.get("value") if isinstance(task.get("value"), Mapping) else {}
        if not value:
            reasons.add("reaction_task_value_missing_or_invalid")
        if task.get("protocol_version") != "wellar.brief-simple-reaction-observation/1.0":
            reasons.add("unsupported_reaction_task_protocol_version")
    if reaction_envelope is None and envelope_error != "modality_not_provided":
        reasons.add(envelope_error or "reaction_task_envelope_invalid")
    if reaction_envelope is not None and value:
        reaction_features = reaction_envelope["features"]
        for field in ("valid_reaction_times_ms", "median_valid_rt_ms", "p90_valid_rt_ms",
                      "valid_trial_count", "submitted_trial_count", "valid_trial_fraction",
                      "false_start_count", "late_response_count", "missed_response_count",
                      "ambiguous_timestamp_count"):
            if field in reaction_features and reaction_features[field] != value.get(field):
                reasons.add("reaction_task_envelope_measurement_mismatch")

    observations: dict[str, Any] = {}
    valid_rts = value.get("valid_reaction_times_ms")
    if isinstance(valid_rts, list) and len(valid_rts) <= 100 and all(_finite_number(rt, minimum=100, maximum=2000) for rt in valid_rts):
        observations["valid_reaction_times_ms"] = _observation(valid_rts, "ms", source="client_timestamps", description_code="unverified_client_reported_reaction_times")
    else:
        reasons.add("invalid_or_missing_reaction_time_samples")
    for field in ("median_valid_rt_ms", "p90_valid_rt_ms"):
        if _finite_number(value.get(field), minimum=100, maximum=2000):
            observations[field] = _observation(value[field], "ms", source="client_timestamps", description_code="unverified_client_reported_reaction_time_summary")
        elif field == "median_valid_rt_ms" or valid_rts:
            reasons.add("reaction_time_summary_unavailable_or_invalid")
    for field in ("valid_trial_count", "submitted_trial_count", "false_start_count", "late_response_count", "missed_response_count", "ambiguous_timestamp_count"):
        if _count(value.get(field)) and value[field] <= 100:
            observations[field] = _observation(value[field], "count", source="client_task", description_code=field)
        else:
            reasons.add("reaction_task_count_missing_or_invalid")
    if _finite_number(value.get("valid_trial_fraction"), minimum=0, maximum=1):
        observations["valid_trial_fraction"] = _observation(value["valid_trial_fraction"], "fraction", source="client_task", description_code="valid_trial_fraction")
    else:
        reasons.add("valid_trial_fraction_missing_or_invalid")
    completion = value.get("task_completion_status")
    if isinstance(completion, str) and len(completion) <= 80:
        observations["task_completion_status"] = _observation(completion, "status_code", source="client_task", description_code="client_reported_task_completion")
    timing = value.get("device_timestamp_reliability")
    if isinstance(timing, Mapping):
        clock_claim = timing.get("client_clock_claim", "unknown")
        if clock_claim not in {"unknown", "monotonic", "performance", "wall_clock"}:
            clock_claim = "unknown"
            reasons.add("invalid_client_clock_claim")
        observations["device_timing"] = {
            "status": "unverified_client_clock",
            "client_clock_claim": clock_claim,
            "client_reported_resolution_ms": timing.get("client_reported_resolution_ms") if _finite_number(timing.get("client_reported_resolution_ms"), minimum=0, maximum=1000) else None,
            "server_verified": False,
        }
    else:
        reasons.add("client_timing_provenance_missing")
    if value.get("server_verified") is not False or value.get("clinical_pvt_equivalence") != "not_established":
        reasons.add("reaction_task_contract_incompatible")
    valid_count = value.get("valid_trial_count")
    submitted_count = value.get("submitted_trial_count")
    valid_count_ok = _count(valid_count) and _count(submitted_count) and valid_count <= submitted_count
    if valid_count_ok and isinstance(valid_rts, list):
        if len(valid_rts) != valid_count:
            reasons.add("reaction_time_vector_count_mismatch")
            valid_count_ok = False
        elif valid_rts:
            median_value = value.get("median_valid_rt_ms")
            if not _finite_number(median_value, minimum=100, maximum=2000) or float(median_value) != float(statistics.median(valid_rts)):
                reasons.add("reaction_time_median_inconsistent_with_samples")
                valid_count_ok = False
            p90_value = value.get("p90_valid_rt_ms")
            expected_p90 = sorted(valid_rts)[math.ceil(0.9 * len(valid_rts)) - 1]
            if not _finite_number(p90_value, minimum=100, maximum=2000) or float(p90_value) != float(expected_p90):
                reasons.add("reaction_time_p90_inconsistent_with_samples")
                valid_count_ok = False
        if submitted_count and _finite_number(value.get("valid_trial_fraction"), minimum=0, maximum=1):
            if abs(float(value["valid_trial_fraction"]) - valid_count / submitted_count) > 1e-9:
                reasons.add("valid_trial_fraction_inconsistent_with_counts")
                valid_count_ok = False
        categories = ("false_start_count", "late_response_count", "missed_response_count", "ambiguous_timestamp_count")
        if all(_count(value.get(field)) for field in categories):
            if valid_count + sum(value[field] for field in categories) != submitted_count:
                reasons.add("reaction_trial_categories_inconsistent_with_submitted_count")
                valid_count_ok = False
    if not valid_count_ok:
        reasons.add("reaction_task_trial_counts_inconsistent")
    if valid_count_ok and valid_count == 0:
        status = "insufficient_evidence"
        reasons.add("no_valid_reaction_trials")
    elif valid_count_ok and valid_count < _MIN_VALID_REACTION_TRIALS:
        status = "partial_evidence"
        reasons.add("too_few_valid_trials_for_task_summary")
    elif valid_count_ok:
        status = "observed"
    else:
        status = "insufficient_evidence"
    if reaction_envelope is None and envelope_error == "modality_not_provided":
        status = "insufficient_evidence" if observations else "not_provided"
        reasons.add("reaction_task_feature_envelope_not_provided")
    if value.get("ambiguous_timestamp_count", 0) or value.get("timing_quality") == "insufficient_valid_trials":
        if status == "observed":
            status = "partial_evidence"
        reasons.add("client_timing_or_trial_quality_limitation")
    if reasons and status == "observed":
        status = "partial_evidence"
    evidence = [{"measurement_type": task.get("measurement_type"), "protocol_version": task.get("protocol_version"), "provenance": task.get("source")}]
    if visual_context:
        evidence.append({"visual_context_separate_from_task_performance": visual_context})
    if audio_context["status"] != "not_provided":
        evidence.append({"audio_quality_only": audio_context})
    if envelope_error and envelope_error not in {"modality_not_provided"}:
        status = "insufficient_evidence"
    if "reaction_task_envelope_measurement_mismatch" in reasons:
        status = "insufficient_evidence"
    if "unsupported_reaction_task_protocol_version" in reasons:
        status = "insufficient_evidence"
    return {
        "status": status,
        "measurement_type": "brief_reaction_task_and_separate_visual_observations",
        "observations": observations,
        "units": {"reaction_times": "ms", "event_counts": "count", "valid_trial_fraction": "fraction"},
        "source_modalities": (["reaction_task"] if reaction_envelope is not None else []) + (["video_visual_context"] if visual_context else []) + (["audio_quality_only"] if audio_context["status"] != "not_provided" else []),
        "quality": {**modality_quality, "audio_context": audio_context, "timing_integrity": "unverified_client_clock", "clinical_pvt_equivalence": "not_established"},
        "coverage": {"reaction_task": value.get("valid_trial_fraction") if _finite_number(value.get("valid_trial_fraction"), minimum=0, maximum=1) else None, "visual_context": visible.get("coverage", {}).get("video")},
        "evidence": evidence,
        "limitations": [
            "Client timestamps are not independently verified by the server.",
            "This brief task is not a clinically validated PVT and does not establish overall concentration or fitness for work.",
            "Reaction-time performance and visual observations are reported separately; no vigilance score or population threshold is inferred.",
            "Missing task data is not interpreted as normal performance.",
        ],
        "method_version": METHOD_VERSION,
        "inference_kind": "direct_task_observation_no_prediction",
        "validation_status": "software_contract_tested_not_scientifically_validated",
        "abstention_reasons": sorted(reasons),
        "explanation_codes": ["short_reaction_task_observation", "client_timing_unverified", "not_a_clinical_pvt"],
    }


def build_behavioral_evidence(
    feature_envelopes: Mapping[str, Any], measurements: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return versioned, bounded observations without deriving a score."""
    if not isinstance(feature_envelopes, Mapping):
        feature_envelopes = {}
    if not isinstance(measurements, Mapping):
        measurements = {}
    visible = _visible(feature_envelopes)
    return {
        "schema_version": CONTRACT_VERSION,
        "visible_fatigue_indicators": visible,
        "vigilance_indicators": _vigilance(feature_envelopes, measurements, visible),
        "model_prediction": {"status": "disabled", "inference_status": "not_performed", "validated_occupational_fatigue_prediction": False},
    }
