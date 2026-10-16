from __future__ import annotations

import hashlib
import json
import math
import statistics
from pathlib import Path

LABEL = "indicative, non-diagnostic, not a validated classifier"
STATES = ("Stable", "Low Focus", "Elevated Fatigue", "High Risk")
CONFIG_SCHEMA_VERSION = "1.0"


def load_thresholds(path: str | Path | None = None) -> dict:
    config = Path(path) if path else Path(__file__).resolve().parents[1] / "config" / "drowsiness_indicator.json"
    raw = config.read_bytes()
    value = json.loads(raw.decode("utf-8"), parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)))
    required_top = {"schema_version", "version", "label", "minimum_face_detection_ratio", "minimum_mean_luminance", "minimum_blur_variance", "minimum_temporal_coverage", "maximum_absolute_yaw_proxy", "maximum_absolute_pitch_proxy", "states", "baseline_minimum_prior_scans", "baseline_freshness_window_days"}
    required = {"high_risk", "elevated_fatigue", "low_focus"}
    if not isinstance(value, dict) or set(value) != required_top:
        raise ValueError("drowsiness indicator config has missing or unknown keys")
    if value.get("schema_version") != CONFIG_SCHEMA_VERSION or value.get("version") != "1.0":
        raise ValueError("unsupported drowsiness indicator config version")
    if value.get("label") != LABEL or not isinstance(value.get("states"), dict) or set(value["states"]) != required:
        raise ValueError("invalid drowsiness indicator threshold configuration")
    ranges = {"minimum_face_detection_ratio": (0, 1), "minimum_mean_luminance": (0, 255), "minimum_blur_variance": (0, 1_000_000_000), "minimum_temporal_coverage": (0, 1), "maximum_absolute_yaw_proxy": (0, math.pi), "maximum_absolute_pitch_proxy": (0, math.pi), "baseline_minimum_prior_scans": (2, 1000), "baseline_freshness_window_days": (1, 3650)}
    for name, (minimum, maximum) in ranges.items():
        item = value.get(name)
        if not isinstance(item, (int, float)) or isinstance(item, bool) or not math.isfinite(item) or item < minimum or item > maximum:
            raise ValueError("invalid drowsiness indicator numeric configuration")
    if not isinstance(value["baseline_minimum_prior_scans"], int) or not isinstance(value["baseline_freshness_window_days"], int):
        raise ValueError("baseline configuration values must be integers")
    state_keys = {"high_risk": {"short_clip_eye_closure_fraction_at_least", "max_eye_closure_seconds_at_least"}, "elevated_fatigue": {"short_clip_eye_closure_fraction_at_least", "blink_rate_per_minute_at_least", "mean_eye_closure_seconds_at_least"}, "low_focus": {"short_clip_eye_closure_fraction_at_least", "blink_rate_per_minute_at_least", "mean_eye_closure_seconds_at_least"}}
    for state, fields in value["states"].items():
        if not isinstance(fields, dict) or set(fields) != state_keys[state]:
            raise ValueError("invalid drowsiness indicator state configuration")
        for name, item in fields.items():
            maximum = 1 if "fraction" in name else (120 if "blink_rate" in name else 10)
            if not isinstance(item, (int, float)) or isinstance(item, bool) or not math.isfinite(item) or item < 0 or item > maximum:
                raise ValueError("invalid drowsiness indicator state threshold")
    low = value["states"]["low_focus"]["short_clip_eye_closure_fraction_at_least"]
    elevated = value["states"]["elevated_fatigue"]["short_clip_eye_closure_fraction_at_least"]
    high = value["states"]["high_risk"]["short_clip_eye_closure_fraction_at_least"]
    if not low < elevated < high or value["states"]["high_risk"]["max_eye_closure_seconds_at_least"] < value["states"]["elevated_fatigue"]["mean_eye_closure_seconds_at_least"] or value["states"]["elevated_fatigue"]["short_clip_eye_closure_fraction_at_least"] <= value["states"]["low_focus"]["short_clip_eye_closure_fraction_at_least"] or value["states"]["elevated_fatigue"]["blink_rate_per_minute_at_least"] < value["states"]["low_focus"]["blink_rate_per_minute_at_least"] or value["states"]["elevated_fatigue"]["mean_eye_closure_seconds_at_least"] < value["states"]["low_focus"]["mean_eye_closure_seconds_at_least"]:
        raise ValueError("drowsiness indicator severity thresholds are not monotonic")
    value["config_sha256"] = hashlib.sha256(raw).hexdigest()
    return value


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def personal_closure_deviation(current: float | None, prior_values: list[float], minimum_scans: int) -> tuple[float | None, int]:
    valid = [value for value in prior_values if _finite(value) and 0 <= value <= 1]
    if not _finite(current) or len(valid) < minimum_scans:
        return None, len(valid)
    median = statistics.median(valid)
    mad = statistics.median(abs(value - median) for value in valid)
    if mad == 0:
        return None, len(valid)
    return (current - median) / (1.4826 * mad), len(valid)


def fresh_baseline_values(history: list[dict], freshness_days: int, *, now: float | None = None) -> tuple[list[float], str | None]:
    """Keep only same-user history with a valid timestamp inside the freshness window."""
    import time
    cutoff = (time.time() if now is None else now) - freshness_days * 86400
    values = []
    found_stale = False
    for entry in history:
        if not isinstance(entry, dict):
            continue
        timestamp = entry.get("created_at")
        measurement = entry.get("measurements", {}).get("short_clip_eye_closure_fraction")
        if not _finite(timestamp) or timestamp > cutoff + freshness_days * 86400:
            continue
        if timestamp < cutoff:
            found_stale = True
            continue
        if _finite(measurement) and 0 <= measurement <= 1:
            values.append(measurement)
    return values, "baseline_stale_history" if not values and found_stale else ("baseline_not_ready" if not values else None)


def personal_perclos_deviation(current: float | None, prior_values: list[float], minimum_scans: int) -> tuple[float | None, int]:
    """Deprecated function name retained for internal source compatibility."""
    return personal_closure_deviation(current, prior_values, minimum_scans)


def classify(measurements: dict, thresholds: dict | None = None, *, audio_quality_status: str | None = None, baseline_z_score: float | None = None, minimum_prior_scans: int = 5, prior_scans: int = 0) -> dict:
    """Apply eye/face rules only; audio can only reject quality."""
    t = thresholds or load_thresholds()
    raw = {key: measurements.get(key) for key in (
        "short_clip_eye_closure_fraction", "blink_rate_per_minute", "mean_eye_closure_seconds",
        "max_eye_closure_seconds", "face_detection_ratio", "mean_luminance", "mean_blur_variance",
        "temporal_coverage", "yaw_proxy", "pitch_proxy",
    )}
    quality_checks = {
        "face_detection_ratio_below_threshold": _finite(raw["face_detection_ratio"]) and raw["face_detection_ratio"] >= t["minimum_face_detection_ratio"],
        "low_lighting": _finite(raw["mean_luminance"]) and raw["mean_luminance"] >= t["minimum_mean_luminance"],
        "blur_below_threshold": _finite(raw["mean_blur_variance"]) and raw["mean_blur_variance"] >= t["minimum_blur_variance"],
        "temporal_coverage_below_threshold": _finite(raw["temporal_coverage"]) and raw["temporal_coverage"] >= t["minimum_temporal_coverage"],
        "pose_unavailable": _finite(raw["yaw_proxy"]) and _finite(raw["pitch_proxy"]),
        "pose_away": _finite(raw["yaw_proxy"]) and _finite(raw["pitch_proxy"]) and abs(raw["yaw_proxy"]) <= t["maximum_absolute_yaw_proxy"] and abs(raw["pitch_proxy"]) <= t["maximum_absolute_pitch_proxy"],
        "audio_quality_failed": audio_quality_status is None or audio_quality_status == "available",
    }
    failed = [reason for reason, passed in quality_checks.items() if not passed]
    base = {"schema_version": "1.1", "status": "insufficient_quality" if failed else "available",
            "label": LABEL, "raw_measurements": raw, "thresholds_used": t,
            "config_version": t["version"], "config_sha256": t["config_sha256"]}
    if failed:
        base["reason_code"] = failed[0]
        base["abstention_reasons"] = failed
        return base
    required = ("short_clip_eye_closure_fraction", "blink_rate_per_minute", "mean_eye_closure_seconds", "max_eye_closure_seconds")
    if not all(_finite(raw[key]) for key in required) or not 0 <= raw["short_clip_eye_closure_fraction"] <= 1:
        base["status"] = "insufficient_quality"
        base["reason_code"] = "required_eye_measurement_unavailable_or_implausible"
        base["abstention_reasons"] = [base["reason_code"]]
        return base
    rules = t["states"]
    fraction = raw["short_clip_eye_closure_fraction"]
    if fraction >= rules["high_risk"]["short_clip_eye_closure_fraction_at_least"] or raw["max_eye_closure_seconds"] >= rules["high_risk"]["max_eye_closure_seconds_at_least"]:
        state, level = "High Risk", "high"
    elif fraction >= rules["elevated_fatigue"]["short_clip_eye_closure_fraction_at_least"] or raw["blink_rate_per_minute"] >= rules["elevated_fatigue"]["blink_rate_per_minute_at_least"] or raw["mean_eye_closure_seconds"] >= rules["elevated_fatigue"]["mean_eye_closure_seconds_at_least"]:
        state, level = "Elevated Fatigue", "medium"
    elif fraction >= rules["low_focus"]["short_clip_eye_closure_fraction_at_least"] or raw["blink_rate_per_minute"] >= rules["low_focus"]["blink_rate_per_minute_at_least"] or raw["mean_eye_closure_seconds"] >= rules["low_focus"]["mean_eye_closure_seconds_at_least"]:
        state, level = "Low Focus", "low"
    else:
        state, level = "Stable", "low"
    base.update({"state": state, "level": level})
    if prior_scans >= minimum_prior_scans and _finite(baseline_z_score):
        base["personal_baseline"] = {"status": "available", "z_score": baseline_z_score, "prior_scans": prior_scans, "baseline_version": "median-mad-v1", "label": "descriptive, non-diagnostic deviation from own baseline"}
    return base
