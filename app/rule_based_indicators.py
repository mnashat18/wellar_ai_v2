"""Explicitly heuristic, non-diagnostic scan indicator engine.

Literature supports PERCLOS P80 as the *definition* of the indicator (time
with eyelid closure covering >80% of the pupil); short-window thresholds vary
by setting and device. Published driver studies have reported PERCLOS values
around 0.11-0.15 as associated with fatigue, but do not validate these cutoffs
for Wellar users or establish four wellness classes. The fixed class rules in
the adjacent JSON are therefore engineering heuristics only, never medical,
occupational fitness, or calibrated probability claims.

References: Wierwille et al. (1994), NHTSA DOT HS 808 762; PERCLOS evidence
review, SLEEP Advances 4 (2023), zpad006, doi:10.1093/sleepadvances/zpad006;
Larue et al., Sensors 21 (2021), 7057, doi:10.3390/s21217057 (30 s windows,
closure and blink duration definitions); Alvaro et al., J Clin Sleep Med 12
(2016), 1099-1103, doi:10.5664/jcsm.6044 (prolonged closure observations in
a sleep-deprived professional-driver simulation).

Threshold provenance: P80, 0.11/0.15 PERCLOS values, and 0.1-0.5 s blink
durations are reported in the sources above, in driving/sleep-loss contexts;
they are not validated cutoffs for Wellar. The 0.5 s microsleep rule is also
an explicit product requirement. The high-tier PERCLOS, yawn, head-pose, and
confidence thresholds are conservative frozen engineering heuristics because
the literature does not establish these four Wellar classes. They require
prospective target-population evaluation before operational reliance.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from statistics import median
from typing import Any, Mapping, Sequence

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "rule_based_indicators_v1.json"
PREDICTIONS = {"stable", "low_focus", "elevated_fatigue", "high_risk"}
ENGINE_VERSION = "rule-based-indicators/1.0.1"


def load_config(path: str | Path = CONFIG_PATH) -> dict[str, Any]:
    raw = Path(path).read_bytes()
    config = json.loads(raw, parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)))
    if (not isinstance(config, dict) or config.get("schema_version") != "wellar.rule-based-indicators/1.0"
            or config.get("engine_type") != "rule_based_indicators"
            or config.get("priority") != ["high_risk", "elevated_fatigue", "low_focus", "stable"]):
        raise ValueError("unsupported rule-based indicator configuration")
    if config.get("baseline_valid_scans") != 3 or config.get("microsleep_min_seconds_exclusive") != 0.5:
        raise ValueError("rule-based indicator safety invariants changed")
    config["config_sha256"] = hashlib.sha256(raw).hexdigest()
    return config


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _number(metrics: Mapping[str, Any], key: str, default: float = 0.0) -> float:
    value = metrics.get(key)
    return float(value) if _finite(value) else default


def _usable(metrics: Mapping[str, Any], config: Mapping[str, Any]) -> tuple[bool, str | None]:
    if metrics.get("video_status") in {"not_provided", "invalid_media", "failed", "timeout"}:
        return False, str(metrics.get("video_reason") or "video_" + str(metrics.get("video_status")))
    if not _finite(metrics.get("face_detection_ratio")) or metrics["face_detection_ratio"] <= 0:
        return False, "no_usable_face_data"
    if _number(metrics, "face_detection_ratio") < config["minimum_face_detection_ratio"]:
        return False, "face_detection_quality_below_minimum"
    duration = metrics.get("video_duration_seconds")
    if not _finite(duration):
        return False, "video_duration_unavailable"
    if duration < config["minimum_video_duration_seconds"]:
        return False, "too_short"
    if int(_number(metrics, "frame_count")) < config["minimum_samples"]:
        return False, "too_few_video_frames"
    if _number(metrics, "temporal_coverage") < config["minimum_temporal_coverage"]:
        return False, "insufficient_temporal_coverage"
    if _number(metrics, "mean_luminance") < config["minimum_luminance"]:
        return False, "low_light"
    if _number(metrics, "mean_blur_variance") < config["minimum_blur_variance"]:
        return False, "blurred_video"
    required = ("perclos_p80", "blink_rate_per_minute", "microsleep_count", "yawn_count",
                "head_nod_count", "gaze_variability", "head_pose_variability")
    if any(not _finite(metrics.get(key)) for key in required):
        return False, "required_face_indicators_unavailable"
    return True, None


def _baseline(history: Sequence[Mapping[str, Any]], config: Mapping[str, Any]) -> dict[str, Any]:
    eligible = [item.get("measurements", {}) for item in history
                if item.get("engine_type") == "rule_based_indicators"
                and item.get("engine_version") == ENGINE_VERSION
                and item.get("status") == "prediction_available"
                and isinstance(item.get("measurements"), Mapping)]
    eligible = eligible[:config["baseline_valid_scans"]]
    if len(eligible) < config["baseline_valid_scans"]:
        return {"status": "building", "valid_prior_scans": len(eligible),
                "required_valid_scans": config["baseline_valid_scans"], "used": False}
    return {"status": "available", "valid_prior_scans": len(eligible),
            "required_valid_scans": config["baseline_valid_scans"], "used": True,
            "perclos_p80_median": median(x["perclos_p80"] for x in eligible),
            "blink_rate_per_minute_median": median(x["blink_rate_per_minute"] for x in eligible)}


def evaluate(metrics: Mapping[str, Any], *, history: Sequence[Mapping[str, Any]] = (),
             config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return one four-way heuristic indicator or a diagnostic retake state.

    Confidence is an ordinal signal-quality description, not probability.
    Null/incomplete face evidence always abstains and cannot become ``stable``.
    """
    cfg = dict(config or load_config())
    usable, reason = _usable(metrics, cfg)
    base = {"schema_version": "1.0", "engine_type": "rule_based_indicators",
            "model_version": ENGINE_VERSION, "config_version": cfg["version"],
            "config_sha256": cfg["config_sha256"], "risk_level": None,
            "confidence_level": "low", "prediction": None,
            "contributing_indicators": [], "insufficient_evidence_reason": None,
            "analysis_status": "retake_required", "baseline": _baseline(history, cfg),
            "quality_metrics": {key: metrics.get(key) for key in
                ("face_detection_ratio", "mean_luminance", "mean_blur_variance", "frame_count", "video_duration_seconds", "temporal_coverage")},
            "limitations": ["heuristic_not_validated", "not_medical_advice", "short_scan_perclos_not_equivalent_to_validated_long_window_measure"]}
    if not usable:
        base["insufficient_evidence_reason"] = reason or "no_usable_face_data"
        base["confidence_level"] = "low"
        base["status"] = "insufficient_quality"
        return base

    rules = cfg["class_rules"]
    perclos = float(metrics["perclos_p80"])
    microsleeps = int(metrics["microsleep_count"])
    prolonged = int(metrics.get("prolonged_eye_closure_count", microsleeps))
    yawns = int(metrics["yawn_count"])
    blinks = float(metrics["blink_rate_per_minute"])
    nods = int(metrics["head_nod_count"])
    contributors: list[str] = []
    baseline = base["baseline"]
    if baseline["used"]:
        perclos_delta = perclos - baseline["perclos_p80_median"]
        blink_delta = blinks - baseline["blink_rate_per_minute_median"]
        if perclos_delta >= cfg["baseline_perclos_deviation"]:
            contributors.append("perclos_p80_deviation_from_personal_baseline")
        if blink_delta >= cfg["baseline_blink_rate_deviation_per_minute"]:
            contributors.append("blink_rate_deviation_from_personal_baseline")
    else:
        perclos_delta = blink_delta = 0.0

    if microsleeps >= rules["high_risk"]["microsleep_count_at_least"]:
        prediction = "high_risk"; contributors.append("microsleep_over_500ms")
    elif perclos >= rules["high_risk"]["perclos_p80_at_least"]:
        prediction = "high_risk"; contributors.append("perclos_p80_high")
    elif (perclos >= rules["elevated_fatigue"]["perclos_p80_at_least"]
          or prolonged >= rules["elevated_fatigue"]["prolonged_eye_closure_count_at_least"]
          or yawns >= rules["elevated_fatigue"]["yawn_count_at_least"]
          or perclos_delta >= cfg["baseline_perclos_deviation"]
          or blink_delta >= cfg["baseline_blink_rate_deviation_per_minute"]):
        prediction = "elevated_fatigue"
        if perclos >= rules["elevated_fatigue"]["perclos_p80_at_least"]: contributors.append("perclos_p80_elevated")
        if prolonged >= rules["elevated_fatigue"]["prolonged_eye_closure_count_at_least"]: contributors.append("prolonged_eye_closures")
        if yawns >= rules["elevated_fatigue"]["yawn_count_at_least"]: contributors.append("yawn_like_events")
    elif (perclos >= rules["low_focus"]["perclos_p80_at_least"]
          or blinks >= rules["low_focus"]["blink_rate_per_minute_at_least"]
          or nods >= rules["low_focus"]["head_nod_count_at_least"]):
        prediction = "low_focus"
        if perclos >= rules["low_focus"]["perclos_p80_at_least"]: contributors.append("perclos_p80_mild")
        if blinks >= rules["low_focus"]["blink_rate_per_minute_at_least"]: contributors.append("blink_rate_high")
        if nods >= rules["low_focus"]["head_nod_count_at_least"]: contributors.append("head_nod_events")
    else:
        prediction = "stable"
        contributors.append("no_configured_indicator_threshold_crossed")

    ratio = _number(metrics, "face_detection_ratio")
    coverage = _number(metrics, "temporal_coverage")
    signal_count = len([x for x in contributors if x != "no_configured_indicator_threshold_crossed"])
    quality_score = min(ratio, coverage)
    confidence_cfg = cfg["confidence"]
    if (quality_score >= confidence_cfg["high_quality_minimum"]
            and signal_count >= confidence_cfg["high_indicator_count_minimum"] and baseline["used"]):
        confidence = "high"
    elif quality_score >= confidence_cfg["medium_quality_minimum"] and (
            signal_count >= confidence_cfg["medium_indicator_count_minimum"] or prediction == "stable"):
        confidence = "medium" if baseline["used"] else "low"
    else:
        confidence = "low"
    base.update({"prediction": prediction, "analysis_status": "prediction_available",
                 "status": "available",
                 "risk_level": prediction, "confidence_level": confidence,
                 "contributing_indicators": contributors,
                 "baseline_deviation": ({"perclos_p80": perclos_delta,
                                         "blink_rate_per_minute": blink_delta}
                                        if baseline["used"] else None),
                 "raw_measurements": dict(metrics)})
    return base
