"""Versioned, fail-closed fatigue and vigilance measurement outputs."""
from __future__ import annotations

import math
import statistics
from collections.abc import Mapping
from typing import Any


DUAL_MEASUREMENT_SCHEMA = "wellar.dual-measurements/1.0"
REACTION_PROTOCOL = "wellar.brief-simple-reaction-observation/1.0"
MIN_VALID_RT_MS = 100
MAX_VALID_RT_MS = 2000
MIN_VALID_TRIALS = 3

FATIGUE_LIMITATIONS = [
    "This original single item and 0-10 scale are not a validated occupational fatigue instrument.",
    "It is a participant self-report, not a medical assessment or fitness-for-duty result.",
    "No score is inferred from image, video, audio, KSS, or reaction-task data.",
]
FATIGUE_QUESTION_VERSION = "wellar-fatigue-self-report/1.0"
FATIGUE_QUESTION = "Right now, how physically or mentally fatigued do you feel before starting work?"
FATIGUE_TIMEFRAME = "Right now, before starting work."
FATIGUE_ANCHORS = {"0": "Not fatigued at all", "10": "Extremely fatigued"}
VIGILANCE_LIMITATIONS = [
    "Client-reported event timestamps are not independently verified by the server.",
    "This brief reaction task is not an equivalently validated clinical PVT.",
    "Reaction-time observations do not establish workplace safety or fitness for duty.",
    "No fatigue or AI prediction is derived from these task measurements.",
]


def fatigue_measurement(
    *,
    legacy_reference: Mapping[str, Any] | None = None,
    instrument_candidate: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Expose an explicitly observed self-report without implying validation."""
    candidate_id = instrument_candidate.get("instrument_id") if instrument_candidate else None
    candidate_id = candidate_id if isinstance(candidate_id, str) and len(candidate_id) <= 128 else None
    score = legacy_reference.get("score") if isinstance(legacy_reference, Mapping) else None
    consent = legacy_reference.get("collection_consent") is True if isinstance(legacy_reference, Mapping) else False
    version = legacy_reference.get("question_version") if isinstance(legacy_reference, Mapping) else None
    valid_score = isinstance(score, int) and not isinstance(score, bool) and 0 <= score <= 10
    observed = consent and valid_score
    exact_item = version == FATIGUE_QUESTION_VERSION
    reason = ("explicit_response_and_collection_consent_required" if not observed
              else "legacy_prompt_timeframe_and_anchors_unverified" if not exact_item else None)
    quality = {"status": "participant_reported" if observed else "not_available", "reason": reason,
               "collection_consent": consent}
    if observed and exact_item:
        quality["prompt_protocol"] = "backend_defined_version"
    return {
        "measurement_type": "self_reported_fatigue",
        "value": score if observed else None,
        "units": "custom_0_to_10_scale" if observed else None,
        "status": ("observed_self_report" if exact_item else "observed_legacy_self_report_protocol_unverified") if observed else "unavailable",
        "method": "single_item_participant_self_report",
        "source": "participant_entered" if observed else None,
        "instrument_id": "wellar_original_unvalidated_single_item" if exact_item and observed else ("wellar_custom_fatigue_rating_0_10" if observed else candidate_id),
        "instrument_version": version if observed else None,
        "question": FATIGUE_QUESTION if exact_item and observed else None,
        "timeframe": FATIGUE_TIMEFRAME if exact_item and observed else None,
        "scale_anchors": FATIGUE_ANCHORS if exact_item and observed else None,
        "subscale": None,
        "scoring_version": None,
        "quality": quality,
        "limitations": list(FATIGUE_LIMITATIONS),
        "ai_validation_status": "not_validated",
        "release_status": "direct_observed_measurement" if observed else "not_available",
    }


def score_approved_fatigue_response(
    *,
    instrument_id: str,
    instrument_version: str,
    subscale: str,
    response: Any,
    approved_registry: Mapping[tuple[str, str, str], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Apply a future approved scorer only when rights and intended-use gates exist.

    The runtime registry is empty in this release. No questionnaire items or
    scoring rules are embedded in this module.
    """
    key = (instrument_id, instrument_version, subscale)
    spec = (approved_registry or {}).get(key)
    gates = ("rights_verified", "language_approved", "intended_use_approved")
    if (not isinstance(spec, Mapping) or not all(spec.get(gate) is True for gate in gates)
            or not isinstance(spec.get("approval_reference"), str) or not spec["approval_reference"].strip()):
        return fatigue_measurement(instrument_candidate={"instrument_id": instrument_id})
    scorer = spec.get("scorer")
    scoring_version = spec.get("scoring_version")
    units = spec.get("units")
    minimum, maximum = spec.get("minimum_score"), spec.get("maximum_score")
    if (not callable(scorer) or not isinstance(scoring_version, str) or not scoring_version
            or not isinstance(units, str) or not units
            or isinstance(minimum, bool) or not isinstance(minimum, (int, float)) or not math.isfinite(minimum)
            or isinstance(maximum, bool) or not isinstance(maximum, (int, float)) or not math.isfinite(maximum)
            or minimum >= maximum):
        return fatigue_measurement(instrument_candidate={"instrument_id": instrument_id})
    try:
        value = scorer(response)
    except Exception:
        value = None
    if (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
            or not minimum <= value <= maximum):
        return {
            **fatigue_measurement(instrument_candidate={"instrument_id": instrument_id}),
            "status": "scoring_failed",
            "quality": {"status": "failed", "reason": "approved_scorer_returned_invalid_value"},
        }
    return {
        "measurement_type": "pre_work_fatigue_self_report",
        "value": float(value),
        "units": units,
        "status": "observed_self_report",
        "method": "explicitly_approved_instrument_scorer",
        "source": "participant_self_report",
        "instrument_id": instrument_id,
        "instrument_version": instrument_version,
        "subscale": subscale,
        "scoring_version": scoring_version,
        "quality": {"status": "scored_under_approved_registry", "approval_reference": spec.get("approval_reference")},
        "limitations": [
            "This is a directly scored self-report, not an AI estimate.",
            "The instrument's approved construct and time window define interpretation.",
            "It is not a diagnosis or fitness-for-duty determination.",
        ],
        "ai_validation_status": "not_applicable_not_ai",
        "release_status": "direct_observed_measurement",
    }


def _task_completion(task: Mapping[str, Any], trial_count: int) -> str:
    reported = task.get("client_reported_completed")
    planned = task.get("planned_trial_count")
    if reported is None:
        return "not_reported"
    if reported is False:
        return "client_reported_incomplete"
    if planned is None:
        return "client_reported_complete_unverifiable_plan"
    return "client_reported_complete" if planned == trial_count else "client_reported_inconsistent_plan"


def vigilance_measurement(task: Mapping[str, Any] | None) -> dict[str, Any]:
    """Summarize paired client event timestamps using an explicit brief-task rule.

    RTs below 100 ms are anticipatory/false starts; RTs from 100 through 2000 ms
    are retained as valid observations; greater RTs are late. A response at or
    after the next stimulus is treated as an ambiguous timer/protocol anomaly.
    These engineering cutoffs are not validated PVT scoring rules.
    """
    if task is None:
        return {
            "measurement_type": "brief_reaction_task_observation",
            "value": None,
            "units": {"reaction_times": "ms", "event_counts": "count"},
            "status": "not_provided",
            "method": "client_event_timestamp_difference",
            "source": None,
            "protocol_version": REACTION_PROTOCOL,
            "quality": {"status": "not_assessed", "reason": "reaction_task_not_provided"},
            "limitations": list(VIGILANCE_LIMITATIONS),
            "ai_validation_status": "not_validated",
            "release_status": "not_available",
        }

    trials = task.get("trials", [])
    stimuli = [trial["stimulus_timestamp_ms"] for trial in trials]
    valid: list[int] = []
    false_starts = 0
    late = 0
    missed = 0
    ambiguous = 0
    per_trial: list[dict[str, Any]] = []
    for index, trial in enumerate(trials):
        stimulus = trial["stimulus_timestamp_ms"]
        response = trial.get("response_timestamp_ms")
        if response is None:
            missed += 1
            per_trial.append({"trial_index": trial["trial_index"], "status": "missed_response", "reaction_time_ms": None})
            continue
        delta = response - stimulus
        next_stimulus = stimuli[index + 1] if index + 1 < len(stimuli) else None
        if next_stimulus is not None and response >= next_stimulus:
            ambiguous += 1
            per_trial.append({"trial_index": trial["trial_index"], "status": "timer_or_trial_attribution_anomaly", "reaction_time_ms": None})
        elif delta < MIN_VALID_RT_MS:
            false_starts += 1
            per_trial.append({"trial_index": trial["trial_index"], "status": "false_start_or_anticipatory", "reaction_time_ms": delta})
        elif delta > MAX_VALID_RT_MS:
            late += 1
            per_trial.append({"trial_index": trial["trial_index"], "status": "late_response", "reaction_time_ms": delta})
        else:
            valid.append(delta)
            per_trial.append({"trial_index": trial["trial_index"], "status": "valid_response", "reaction_time_ms": delta})

    ordered = sorted(valid)
    p90 = ordered[math.ceil(0.9 * len(ordered)) - 1] if ordered else None
    trial_count = len(trials)
    valid_fraction = len(valid) / trial_count if trial_count else 0.0
    completion = _task_completion(task, trial_count)
    measurement_status = "observed_unverified" if valid else "insufficient_quality"
    quality_status = "unverified_client_timing" if len(valid) >= MIN_VALID_TRIALS else "insufficient_valid_trials"
    resolution = task.get("timestamp_resolution_ms")
    if resolution is None:
        resolution = task.get("timing_metadata", {}).get("device_clock_resolution_ms")
    if isinstance(resolution, bool) or not isinstance(resolution, (int, float)) or not math.isfinite(resolution):
        resolution = None
    clock_claim = task.get("device_clock", "unknown")
    if clock_claim == "unknown" and task.get("timing_metadata", {}).get("monotonic_clock") is True:
        clock_claim = "monotonic_claim_in_legacy_metadata"
    value = {
        "valid_reaction_times_ms": valid,
        "median_valid_rt_ms": None if not valid else float(statistics.median(valid)),
        "p90_valid_rt_ms": p90,
        "late_response_count": late,
        "false_start_count": false_starts,
        "missed_response_count": missed,
        "ambiguous_timestamp_count": ambiguous,
        "valid_trial_count": len(valid),
        "submitted_trial_count": trial_count,
        "valid_trial_fraction": valid_fraction,
        "trial_observations": per_trial,
        "task_completion_status": completion,
        "device_timestamp_reliability": {
            "status": "unverified_client_clock",
            "client_clock_claim": clock_claim,
            "client_reported_resolution_ms": resolution,
            "server_verified": False,
        },
        "timing_quality": quality_status,
        "protocol_cutoffs_ms": {"minimum_valid_rt_ms": MIN_VALID_RT_MS, "maximum_valid_rt_ms": MAX_VALID_RT_MS},
        "task_type_declared_by_client": task.get("task_type"),
        "timing_metadata": task.get("timing_metadata", {}),
        "integrity_status": "unverified_client_report",
        "server_verified": False,
        "clinical_pvt_equivalence": "not_established",
    }
    return {
        "measurement_type": "brief_reaction_task_observation",
        "value": value,
        "units": {"reaction_times": "ms", "event_counts": "count", "valid_trial_fraction": "fraction"},
        "status": measurement_status,
        "method": "client_event_timestamp_difference",
        "source": "client_reported_stimulus_and_response_timestamps",
        "protocol_version": REACTION_PROTOCOL,
        "quality": {
            "status": quality_status,
            "valid_trial_fraction": valid_fraction,
            "minimum_valid_trials_for_adequate_coverage": MIN_VALID_TRIALS,
            "client_clock_verification": "not_available",
        },
        "limitations": list(VIGILANCE_LIMITATIONS),
        "ai_validation_status": "not_validated",
        "release_status": "direct_observed_measurement",
    }


def dual_measurement_output(
    task: Mapping[str, Any] | None,
    *,
    legacy_reference: Mapping[str, Any] | None = None,
    instrument_candidate: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": DUAL_MEASUREMENT_SCHEMA,
        "fatigue": fatigue_measurement(legacy_reference=legacy_reference, instrument_candidate=instrument_candidate),
        "vigilance": vigilance_measurement(task),
        "ai_release_policy": {
            "direct_observed_measurement": "Measured or explicitly client-reported source values only; provenance and timing quality remain visible.",
            "research_only_ai_estimate": "Isolated research evaluation only; never an operational employee result.",
            "validated_ai_estimate": "Requires an approved target, independent participant-disjoint evaluation, calibrated/abstaining artifact, and release-manifest acceptance.",
        },
    }
