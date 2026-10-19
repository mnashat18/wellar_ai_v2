"""Opt-in research training interface for paired five-second video and KSS.

Input rows must be produced by the consent-gated pilot importer. This module
filters records; it never creates or transforms labels and never activates a
model in API or worker inference.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from research.evaluation_v1 import ResearchDataError, load_jsonl, validate_record
from research.training import TrainingConfig, train_target

SHORT_FEATURE_VERSION = "wellar-short-mobile-eye/1.0"
SHORT_FEATURE_NAMES = ("video.short_eye_closure_fraction",)
TARGET_NAME = "reference_sleepiness"
MAX_DATASET_BYTES = 16 * 1024 * 1024
MAX_RIGHTS_BYTES = 64 * 1024
MIN_CAPTURE_SECONDS = 4.0
MAX_CAPTURE_SECONDS = 7.0
MAX_KSS_DELAY_AFTER_CLIP_SECONDS = 15.0


def _number(values: Mapping[str, Any], key: str) -> float | None:
    value = values.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def short_feature_rejection(values: Mapping[str, Any], provenance: Mapping[str, Any]) -> str | None:
    """Return a rejection reason unless this row contains quality-gated mobile evidence."""
    if provenance.get("short_eye_feature_version") != SHORT_FEATURE_VERSION:
        return "feature_version_mismatch"
    if provenance.get("status") != "available":
        return "video_extraction_not_available"
    fraction = _number(values, "short_eye_closure_fraction")
    duration = _number(values, "video_duration_seconds")
    observed = _number(values, "short_eye_closure_observed_seconds")
    valid_count = _number(values, "short_eye_valid_observation_count")
    coverage = _number(values, "short_eye_time_coverage")
    sampling_hz = _number(values, "actual_sample_hz")
    if None in (fraction, duration, observed, valid_count, coverage, sampling_hz):
        return "missing_or_invalid_short_eye_features"
    if not MIN_CAPTURE_SECONDS <= duration <= MAX_CAPTURE_SECONDS:
        return "capture_duration_outside_mobile_protocol"
    if (not 0 <= fraction <= 1 or observed < 1 or valid_count < 4 or not valid_count.is_integer() or
            coverage < .5 or sampling_hz < 3.0):
        return "short_eye_quality_gate_failed"
    if coverage > 1 or observed > duration:
        return "short_eye_measurement_inconsistent"
    return None


def paired_kss_rejection(record: Mapping[str, Any]) -> str | None:
    """Require an observed, qualified KSS response after this exact mobile clip."""
    labels = record.get("labels", {})
    label = labels.get(TARGET_NAME, {}) if isinstance(labels, Mapping) else {}
    if not isinstance(label, Mapping) or label.get("status") != "observed":
        return "kss_label_not_observed"
    raw = label.get("raw_value")
    class_label = label.get("class_label")
    if (label.get("instrument") != "KSS" or label.get("qualified") is not True or
            isinstance(raw, bool) or not isinstance(raw, int) or not 1 <= raw <= 9 or
            isinstance(class_label, bool) or not isinstance(class_label, int) or class_label != raw):
        return "kss_label_must_be_qualified_observed_1_to_9_without_remapping"
    try:
        captured = datetime.fromisoformat(str(record["context"]["recorded_at_utc"]).replace("Z", "+00:00"))
        measured = datetime.fromisoformat(str(label["measured_at_utc"]).replace("Z", "+00:00"))
        duration = float(record["features"]["video"]["values"]["video_duration_seconds"])
    except (KeyError, TypeError, ValueError):
        return "capture_or_label_timestamp_missing"
    if captured.tzinfo is None or measured.tzinfo is None:
        return "capture_or_label_timestamp_not_timezone_aware"
    delay = (measured.astimezone(timezone.utc) - captured.astimezone(timezone.utc)).total_seconds() - duration
    if delay < 0 or delay > MAX_KSS_DELAY_AFTER_CLIP_SECONDS:
        return "kss_not_collected_within_15_seconds_after_clip"
    return None


def qualify_rows(records: Sequence[Mapping[str, Any]]) -> tuple[list[Mapping[str, Any]], dict[str, int]]:
    eligible = []
    reasons: dict[str, int] = {}
    for record in records:
        validate_record(record)
        envelope = record["features"]["video"]
        feature_reason = short_feature_rejection(envelope["values"], envelope["provenance"])
        reason = feature_reason or paired_kss_rejection(record)
        if reason:
            reasons[reason] = reasons.get(reason, 0) + 1
        else:
            eligible.append(record)
    return eligible, reasons


def _write_new(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as output:
        output.write(content)


def train_files(data_path: Path, rights_path: Path, artifact_path: Path, report_path: Path) -> dict[str, Any]:
    if data_path.stat().st_size > MAX_DATASET_BYTES or rights_path.stat().st_size > MAX_RIGHTS_BYTES:
        raise ResearchDataError("short-mobile data or rights file exceeds its configured size limit")
    source = data_path.read_bytes()
    records = load_jsonl(data_path)
    eligible, exclusions = qualify_rows(records)
    if len(eligible) < 12:
        raise ResearchDataError("short-mobile KSS training requires at least 12 eligible distinct participants")
    if len({row["participant_id"] for row in eligible}) < 12:
        raise ResearchDataError("short-mobile KSS training requires at least 12 eligible distinct participants")
    rights = json.loads(rights_path.read_text(encoding="utf-8"))
    if not isinstance(rights, Mapping):
        raise ResearchDataError("rights manifest must be a JSON object")
    config = TrainingConfig(target=TARGET_NAME, feature_names=SHORT_FEATURE_NAMES)
    artifact, output = train_target(eligible, rights, config,
                                   source_data_sha256=hashlib.sha256(source).hexdigest())
    output["manifest"]["feature_mapping_version"] = SHORT_FEATURE_VERSION
    output["manifest"]["feature_contract"] = "video.short_eye_closure_fraction:fraction_0_to_1"
    artifact_path = Path(artifact_path)
    report_path = Path(report_path)
    manifest_path = artifact_path.with_suffix(".manifest.json")
    if any(path.exists() for path in (artifact_path, manifest_path, report_path)):
        raise ResearchDataError("candidate output path already exists; choose new output paths")
    artifact_bytes = json.dumps(artifact, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    manifest_bytes = json.dumps(output["manifest"], sort_keys=True, indent=2, allow_nan=False).encode() + b"\n"
    result = {**output["report"], "training_interface": "wellar-short-mobile-kss/1.0",
              "eligible_record_count": len(eligible), "excluded_record_count": len(records) - len(eligible),
              "exclusion_reasons": exclusions, "label_policy": "qualified_KSS_class_label_used_verbatim_no_remapping",
              "scientific_validation": "requires_untouched_participant_evaluation_before_any_use"}
    report_bytes = json.dumps(result, sort_keys=True, indent=2, allow_nan=False).encode() + b"\n"
    _write_new(artifact_path, artifact_bytes)
    _write_new(manifest_path, manifest_bytes)
    _write_new(report_path, report_bytes)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train a research candidate only from qualified paired short-mobile-video/KSS records.")
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--rights", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(train_files(args.data, args.rights, args.artifact, args.report), sort_keys=True))
    except (OSError, json.JSONDecodeError, ResearchDataError) as exc:
        print(f"short-mobile training blocked: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
