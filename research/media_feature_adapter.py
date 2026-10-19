"""Research-only adapter for versioned, label-free V2 media features.

The records are software inputs, not qualified measurements or training data.
"""
from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from research.evaluation_v1 import ResearchDataError, check_split_leakage

SCHEMA_VERSION = "wellar-media-features/1.1"
MODALITY_ORDER = ("image", "video", "audio")
FEATURES = {
    "image": ("eye_aspect_ratio", "mouth_aspect_ratio", "face_detection_ratio", "mean_luminance",
              "mean_blur_variance", "gray_std", "clipped_pixel_fraction"),
    "video": ("short_eye_closure_fraction", "blink_rate_per_minute", "closure_duration_mean_seconds",
              "mouth_open_event_count", "face_coverage", "sampling_coverage", "overall_coverage",
              "mean_sampled_luminance", "mean_blur_variance"),
    "audio": ("duration_seconds", "sample_rate_hz", "channels", "rms_dbfs", "peak_dbfs",
              "dc_offset_max", "ac_rms_dbfs", "silence_window_fraction", "clipped_sample_fraction"),
}
_ID = re.compile(r"^[ps]_[a-f0-9]{16,64}$")
_PROVENANCE_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$")
_VERSIONED_RULE = re.compile(r"^wellar-[A-Za-z0-9_.-]+/[0-9]+(?:\.[0-9]+)*$")
_STATUSES = {"available", "insufficient_quality", "not_provided", "not_processed", "failed", "invalid_media"}
_PROVENANCE_FIELDS = {
    "image": ("extractor", "version", "eye_aspect_ratio", "mouth_aspect_ratio"),
    "video": ("extractor", "version", "short_eye_feature_version", "eye_state_rule",
              "mouth_open_rule", "blink_event_rule", "mouth_event_rule"),
    "audio": ("extractor", "version", "quality_scope"),
}
_FORBIDDEN = {"name", "email", "phone", "employeeid", "employee_number", "rawmedia", "mediabytes",
              "mediapayload", "imagebytes", "videobytes", "audiobytes", "path", "filename",
              "filepath", "url", "uri", "participantid", "sessionid", "sampleid", "target", "labels",
              "fatigue", "sleepiness", "vigilance"}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ResearchDataError(message)


def _scan(value: Any, *, root: bool = False) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = "".join(c for c in str(key).lower() if c.isalnum())
            canonical_identity = root and normalized in {"participantid", "sessionid", "sampleid"}
            _require(canonical_identity or normalized not in {"".join(c for c in k if c.isalnum()) for k in _FORBIDDEN},
                     "direct identifiers, labels, and raw media fields are forbidden")
            _scan(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _scan(child)


def _finite_or_none(value: Any) -> float | None:
    if value is None:
        return None
    _require(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value),
             "extractor feature must be a finite number or null")
    return float(value)


def _safe_provenance(name: str, value: Any) -> bool:
    return (isinstance(value, str) and bool(_PROVENANCE_VALUE.fullmatch(value))) or (
        name == "short_eye_feature_version" and isinstance(value, str) and bool(_VERSIONED_RULE.fullmatch(value)))


def build_feature_record(*, participant_id: str, session_id: str, sample_id: str,
                         extractor_outputs: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Adapt actual extractor envelopes. IDs must be opaque precomputed pseudonyms."""
    _require(bool(_ID.fullmatch(participant_id)) and participant_id.startswith("p_"),
             "participant_id must be an opaque p_ pseudonym (16+ lowercase hex chars)")
    _require(bool(_ID.fullmatch(session_id)) and session_id.startswith("s_"),
             "session_id must be an opaque s_ pseudonym (16+ lowercase hex chars)")
    _require(bool(re.fullmatch(r"sample_[a-f0-9]{16,64}", sample_id)), "sample_id must be an opaque sample pseudonym")
    _require(isinstance(extractor_outputs, Mapping) and set(extractor_outputs).issubset(MODALITY_ORDER),
             "only image, video, and audio extractor outputs are supported")
    _scan(extractor_outputs)
    modalities: dict[str, Any] = {}
    provenance: dict[str, Any] = {}
    flat: dict[str, float | None] = {}
    available_count = 0
    for modality in MODALITY_ORDER:
        output = extractor_outputs.get(modality)
        names = FEATURES[modality]
        values = {name: None for name in names}
        if output is None:
            status, reason, coverage = "not_provided", "modality_not_provided", 0.0
        else:
            _require(isinstance(output, Mapping), f"{modality} output must be an object")
            # Worker results wrap the extractor envelope in the public V2 FeatureEnvelope.
            # Normalize that existing contract here; no API contract is changed.
            if "extraction_status" in output:
                _require(set(output).issubset({"schema_version", "modality", "presence", "extraction_status",
                                                "quality", "features", "provenance"}) and
                         output.get("modality") == modality and output.get("schema_version") == "2.0" and
                         output.get("presence") in {"present", "missing"},
                         f"{modality} worker feature envelope is malformed")
                _require(isinstance(output.get("quality"), Mapping) and
                         output["quality"].get("modality") == modality and
                         output["quality"].get("status") == output.get("extraction_status") and
                         ((output.get("presence") == "missing") ==
                          (output.get("extraction_status") == "not_provided")),
                         f"{modality} worker status and quality status disagree")
                output = {"status": output.get("extraction_status"), "quality": output.get("quality"),
                          "features": output.get("features"), "provenance": output.get("provenance", {})}
            _require(set(output).issubset({"status", "quality", "features", "provenance", "timing_ms"}),
                     f"{modality} output has unsupported fields")
            status = output.get("status")
            _require(status in _STATUSES, f"{modality} has unsupported extractor status")
            quality, feats, prov = output.get("quality"), output.get("features"), output.get("provenance")
            _require(isinstance(quality, Mapping) and isinstance(feats, Mapping) and isinstance(prov, Mapping),
                     f"{modality} extractor envelope is malformed")
            if status != "not_provided":
                _require(all(isinstance(prov.get(k), str) and prov[k] for k in ("extractor", "version")),
                         f"{modality} extractor/version provenance is required")
            reason = quality.get("reason") or (feats.get("error_code") if isinstance(feats, Mapping) else None)
            _require(reason is None or isinstance(reason, str), f"{modality} quality reason must be text or null")
            source_coverage: float | None = None
            if status == "available":
                _require(reason is None, f"{modality} available output cannot carry a rejection reason")
                required_features = {"image": ("face_detection_ratio", "mean_luminance", "mean_blur_variance"),
                                     "video": ("overall_coverage", "missingness", "face_coverage", "sampling_coverage"),
                                     "audio": ("duration_seconds", "ac_rms_dbfs", "clipped_sample_fraction")}[modality]
                _require(all(k in feats for k in required_features),
                         f"{modality} available output is missing required extractor features")
                for name in names:
                    if name in feats:
                        values[name] = _finite_or_none(feats[name])
                coverage_value = feats.get("overall_coverage", quality.get("overall_coverage", 1.0))
                if modality == "image":
                    coverage_value = feats.get("face_detection_ratio", 1.0)
                elif modality == "audio":
                    coverage_value = 1.0
                coverage = _finite_or_none(coverage_value)
                _require(coverage is not None and 0 <= coverage <= 1, f"{modality} coverage must be in [0,1]")
                source_coverage = coverage
                available_count += 1
            elif status == "not_provided":
                coverage = 0.0
                source_coverage = 0.0
                reason = reason or "modality_not_provided"
            else:
                # Rejected outputs retain their reason and coverage, never their measurements.
                raw_coverage = quality.get("coverage", quality.get("overall_coverage",
                               quality.get("face_observation_coverage", None)))
                if modality == "image":
                    raw_coverage = feats.get("face_detection_ratio", 1.0 if quality.get("face_visible") else 0.0)
                elif modality == "audio" and status == "insufficient_quality":
                    raw_coverage = 1.0 if quality.get("duration_seconds", feats.get("duration_seconds")) else 0.0
                coverage = _finite_or_none(raw_coverage)
                if coverage is None:
                    coverage = 0.0
                _require(0 <= coverage <= 1, f"{modality} coverage must be in [0,1]")
                source_coverage = coverage
                coverage = 0.0
                _require(reason is not None, f"{modality} rejected output must retain a reason")
            provenance[modality] = {k: prov[k] for k in _PROVENANCE_FIELDS[modality] if k in prov}
            _require(all(_safe_provenance(k, v) for k, v in provenance[modality].items()),
                     f"{modality} provenance values must be safe version/rule tokens")
        modalities[modality] = {"status": status, "reason": reason,
                                "coverage": coverage, "source_coverage": source_coverage if output is not None else 0.0,
                                "missingness": 1.0 - coverage,
                                "features": values}
        flat.update((f"{modality}.{name}", values[name]) for name in names)
    record = {"schema_version": SCHEMA_VERSION, "sample_id": sample_id, "participant_id": participant_id,
              "session_id": session_id, "modality_order": list(MODALITY_ORDER), "feature_order": list(flat),
              "modalities": modalities, "feature_values": flat, "modality_coverage":
              {m: modalities[m]["coverage"] for m in MODALITY_ORDER},
              "overall_modality_coverage": available_count / len(MODALITY_ORDER), "provenance": provenance}
    validate_feature_record(record)
    return record


def build_feature_record_from_worker_result(*, participant_id: str, session_id: str, sample_id: str,
                                            worker_result: Mapping[str, Any]) -> dict[str, Any]:
    """Adapt the current worker result's ``features`` envelopes, excluding targets and other output."""
    _require(isinstance(worker_result, Mapping) and isinstance(worker_result.get("features"), Mapping),
             "worker result must contain a features object")
    envelopes = worker_result["features"]
    supported = {m: envelopes[m] for m in MODALITY_ORDER if m in envelopes}
    return build_feature_record(participant_id=participant_id, session_id=session_id, sample_id=sample_id,
                                extractor_outputs=supported)


def validate_feature_record(record: Mapping[str, Any]) -> None:
    """Validate schema shape, stable ordering, missingness, and exclusion of labels/PII."""
    _require(isinstance(record, Mapping), "feature record must be an object")
    _scan(record, root=True)
    required = {"schema_version", "sample_id", "participant_id", "session_id", "modality_order",
                "feature_order", "modalities", "feature_values", "modality_coverage",
                "overall_modality_coverage", "provenance"}
    _require(set(record) == required, "feature record has missing or unsupported fields")
    _require(record["schema_version"] == SCHEMA_VERSION, "unsupported media feature schema version")
    _require(bool(re.fullmatch(r"p_[a-f0-9]{16,64}", str(record["participant_id"]))) and
             bool(re.fullmatch(r"s_[a-f0-9]{16,64}", str(record["session_id"]))) and
             bool(re.fullmatch(r"sample_[a-f0-9]{16,64}", str(record["sample_id"]))), "invalid pseudonymous identifiers")
    _require(record["modality_order"] == list(MODALITY_ORDER), "modality order differs from schema")
    expected = [f"{m}.{name}" for m in MODALITY_ORDER for name in FEATURES[m]]
    _require(record["feature_order"] == expected and list(record["feature_values"]) == expected,
             "feature order differs from versioned schema")
    _require(set(record["modalities"]) == set(MODALITY_ORDER), "all modality states are required")
    _require(set(record["modality_coverage"]) == set(MODALITY_ORDER), "all modality coverage values are required")
    for modality in MODALITY_ORDER:
        row = record["modalities"][modality]
        _require(set(row) == {"status", "reason", "coverage", "source_coverage", "missingness", "features"}, "invalid modality state")
        _require(row["status"] in _STATUSES, "invalid modality status")
        _require(isinstance(row["coverage"], (int, float)) and math.isfinite(row["coverage"]) and 0 <= row["coverage"] <= 1 and
                 isinstance(row["source_coverage"], (int, float)) and math.isfinite(row["source_coverage"]) and
                 0 <= row["source_coverage"] <= 1 and
                 isinstance(row["missingness"], (int, float)) and math.isfinite(row["missingness"]) and
                 math.isclose(row["missingness"], 1 - row["coverage"], abs_tol=1e-12), "invalid coverage/missingness")
        _require(set(row["features"]) == set(FEATURES[modality]), "modality feature keys differ from schema")
        if row["status"] != "available":
            _require(row["coverage"] == 0 and all(v is None for v in row["features"].values()),
                     "unavailable/rejected modality must have zero valid coverage and null features")
        else:
            _require(row["reason"] is None and row["coverage"] > 0, "available modality needs positive coverage and no rejection reason")
        for name, value in row["features"].items():
            _require(value is None or isinstance(value, (int, float)) and math.isfinite(value), "feature values must be finite numbers or null")
            _require(record["feature_values"][f"{modality}.{name}"] == value, "flat feature values differ from modality values")
        _require(record["modality_coverage"][modality] == row["coverage"], "coverage map differs from modality state")
    _require(isinstance(record["overall_modality_coverage"], (int, float)) and
             math.isclose(record["overall_modality_coverage"], sum(r["status"] == "available" for r in record["modalities"].values()) / 3),
             "invalid overall modality coverage")
    _require(set(record["provenance"]).issubset(MODALITY_ORDER), "invalid provenance modality")
    for modality, values in record["provenance"].items():
        _require(isinstance(values, Mapping) and set(values).issubset(_PROVENANCE_FIELDS[modality]) and
                 all(_safe_provenance(name, value) for name, value in values.items()),
                 "provenance must contain only safe extractor/version/rule tokens")


def validate_dataset_readiness(records: Sequence[Mapping[str, Any]], split_by_sample: Mapping[str, str]) -> None:
    """Check records and use the established participant/session leakage checker."""
    _require(bool(records), "at least one feature record is required")
    ids: set[str] = set()
    for record in records:
        validate_feature_record(record)
        _require(record["sample_id"] not in ids, "sample_id values must be unique")
        ids.add(record["sample_id"])
    _require(set(split_by_sample) == ids, "each sample must have exactly one split")
    check_split_leakage(records, split_by_sample)
