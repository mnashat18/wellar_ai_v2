"""Research validation utilities. No training, model loading, or worker integration.

Only standard-library modules are used. Scientific labels must be supplied from
an authorized study; the unit-test fixture marker is rejected by default.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

EVALUATION_VERSION = "1.1"
LEGACY_TARGETS = ("reference_sleepiness", "vigilance_performance")
TARGETS = ("reference_sleepiness", "subjective_fatigue", "vigilance_performance")
VISUAL_EVENT_TARGETS = ("eye_state_event", "mouth_opening_event")
PILOT_TARGETS = (*TARGETS, *VISUAL_EVENT_TARGETS)
SUPPORTED_SCHEMA_TARGETS = {"1.0": LEGACY_TARGETS, "1.1": TARGETS, "1.2": PILOT_TARGETS}
MODALITIES = ("video", "audio", "image", "reaction_task")
AVAILABILITY = {"available", "missing", "failed", "not_collected", "unknown"}
SPLIT_NAMES = ("train", "validation", "test")
_MISSING = {None, "", "unknown"}
_ALLOWED_RECORD_KEYS = {"schema_version", "fixture_marker", "sample_id", "participant_id", "session_id",
                        "identity_linkage", "provenance", "consent", "context", "modality_availability",
                        "features", "labels", "media_fingerprints"}
_FORBIDDEN_KEYS = {"name", "firstname", "lastname", "email", "phone", "employeeid", "employee_number",
                   "rawmedia", "mediabytes", "faceimage", "voice_recording", "token", "password", "credential"}
_FORBIDDEN_NORMALIZED = {"".join(ch for ch in key.lower() if ch.isalnum()) for key in _FORBIDDEN_KEYS}


class ResearchDataError(ValueError):
    """Manifest or evaluation input violates the versioned research contract."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ResearchDataError(message)


def _iso_time(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _check_sensitive_keys(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = "".join(ch for ch in str(key).lower() if ch.isalnum())
            _require(normalized not in _FORBIDDEN_NORMALIZED,
                     "potentially identifying, credential, or raw-media fields are forbidden")
            _check_sensitive_keys(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _check_sensitive_keys(child)


def validate_record(record: Mapping[str, Any], *, allow_unit_test_fixture: bool = False) -> None:
    """Validate one de-identified multimodal sample and its independent labels."""
    _require(isinstance(record, Mapping), "record must be an object")
    _check_sensitive_keys(record)
    _require(set(record).issubset(_ALLOWED_RECORD_KEYS), "record contains unsupported or potentially identifying fields")
    schema_version = record.get("schema_version")
    _require(isinstance(schema_version, str) and schema_version in SUPPORTED_SCHEMA_TARGETS, "unsupported schema_version")
    _require(not record.get("fixture_marker") or
             (allow_unit_test_fixture and record.get("fixture_marker") == "unit_test_synthetic"),
             "synthetic/unit-test fixtures are not eligible research data")
    for field in ("sample_id", "participant_id", "session_id"):
        _require(isinstance(record.get(field), str) and record[field].strip() not in _MISSING,
                 f"{field} is required and must be a pseudonymous study identifier")
    _require("media_fingerprints" not in record or
             (isinstance(record["media_fingerprints"], list) and all(isinstance(v, str) for v in record["media_fingerprints"])),
             "media_fingerprints must be an optional list of opaque strings")

    linkage = record.get("identity_linkage")
    _require(isinstance(linkage, Mapping) and linkage.get("pseudonymized") is True and
             linkage.get("linkage_key_stored_separately") is True and
             isinstance(linkage.get("linkage_governance_id"), str) and linkage["linkage_governance_id"].strip(),
             "identity linkage must be pseudonymous with its key governed separately")

    provenance = record.get("provenance")
    required_provenance = ("study_id", "dataset_id", "dataset_version", "source_reference",
                           "acquisition_protocol_id", "curator_role")
    _require(isinstance(provenance, Mapping) and all(
        isinstance(provenance.get(key), str) and provenance[key].strip() not in _MISSING
        for key in required_provenance), "documented study, source, version, protocol and curator provenance is required")

    consent = record.get("consent")
    _require(isinstance(consent, Mapping) and consent.get("status") == "granted",
             "active documented consent is required")
    permissions = consent.get("permissions") if isinstance(consent, Mapping) else None
    if schema_version == "1.2":
        permission_names = ("media_analysis", "media_storage", "model_training", "evaluation", "cross_site_transfer")
        _require(isinstance(permissions, Mapping) and set(permissions) == set(permission_names) and
                 all(isinstance(permissions[key], bool) for key in permission_names),
                 "all pilot permissions must be explicit independent booleans")
        _require(permissions["media_analysis"], "media-analysis permission is required for extracted records")
        _require(permissions["media_storage"], "media-storage permission is required to persist pilot feature records")
        _require(isinstance(consent.get("consent_reference"), str) and
                 re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{2,63}", consent["consent_reference"]) is not None and
                 _iso_time(consent.get("consented_at_utc")),
                 "pilot records require an opaque consent reference and timestamp")
    else:
        _require(isinstance(permissions, Mapping) and all(
            key in permissions and isinstance(permissions[key], bool)
            for key in ("research_evaluation", "model_training", "cross_site_transfer")),
            "all research-evaluation, training and cross-site permissions must be explicit booleans")
        _require(permissions["research_evaluation"], "research-evaluation permission is required")

    context = record.get("context")
    required_context = ("recorded_at_utc", "site_id", "device_id", "industry", "occupation")
    _require(isinstance(context, Mapping) and all(key in context for key in required_context),
             "time, site, device, industry and occupation fields must be present, using 'unknown' when unavailable")
    _require(context["recorded_at_utc"] == "unknown" or _iso_time(context["recorded_at_utc"]),
             "recorded_at_utc must be ISO-8601 or 'unknown'")
    for key in ("site_id", "device_id", "industry", "occupation"):
        _require(context[key] is None or isinstance(context[key], str), f"context.{key} must be a category or null")

    modalities = record.get("modality_availability")
    _require(isinstance(modalities, Mapping) and all(key in modalities for key in MODALITIES),
             "availability for video, audio, image and reaction_task must be explicit")
    _require(all(modalities[key] in AVAILABILITY for key in MODALITIES), "invalid modality availability value")

    features = record.get("features")
    _require(isinstance(features, Mapping) and all(key in features for key in MODALITIES),
             "feature containers for all modalities must be present")
    for modality, envelope in features.items():
        _require(isinstance(envelope, Mapping), f"features.{modality} must be an object")
        _require(isinstance(envelope.get("provenance"), Mapping) and
                 all(isinstance(v, str) and v.strip() for v in envelope["provenance"].values()),
                 f"feature provenance is required for {modality}")
        values = envelope.get("values")
        _require(isinstance(values, Mapping) and all(
            v is None or (isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v))
            for v in values.values()), f"{modality} features must be finite numeric summaries or null")
        _require("raw_media" not in envelope and "media_bytes" not in envelope,
                 "raw media must not be embedded in research manifests")

    labels = record.get("labels")
    version_targets = SUPPORTED_SCHEMA_TARGETS[schema_version]
    _require(isinstance(labels, Mapping) and set(labels) == set(version_targets),
             "sleepiness, fatigue, and vigilance labels must remain separate by schema version")
    for target in version_targets:
        label = labels[target]
        _require(isinstance(label, Mapping) and label.get("status") in {"observed", "missing", "unknown"},
                 f"{target} label status must explicitly say observed, missing, or unknown")
        _require(isinstance(label.get("label_definition_id"), str) and label["label_definition_id"].strip(),
                 f"{target} requires a documented label_definition_id")
        if label["status"] != "observed":
            _require(label.get("raw_value") is None and label.get("class_label") is None and
                     isinstance(label.get("missing_reason"), str) and label["missing_reason"].strip(),
                     f"missing {target} labels require null values and an explicit reason")
            continue
        _require(isinstance(label.get("source_reference"), str) and label["source_reference"].strip() and
                 isinstance(label.get("assessor_role"), str) and label["assessor_role"].strip() and
                 _iso_time(label.get("measured_at_utc")), f"observed {target} requires source, assessor role and measurement time")
        _require(isinstance(label.get("raw_value"), (int, float)) and not isinstance(label["raw_value"], bool) and
                 math.isfinite(label["raw_value"]), f"observed {target} requires a finite reference value")
        _require(isinstance(label.get("unit"), str) and label["unit"].strip(), f"observed {target} requires units")
        if label.get("class_label") is not None:
            _require(isinstance(label.get("classification_rule_id"), str) and label["classification_rule_id"].strip(),
                     "class labels require a preregistered classification_rule_id")
        if target == "reference_sleepiness":
            _require(label.get("instrument") == "KSS" and label.get("qualified") is True and
                     1 <= label["raw_value"] <= 9,
                     "sleepiness reference requires explicitly qualified KSS values from 1 through 9")
            _require(isinstance(label.get("qualification_reference"), str) and label["qualification_reference"].strip(),
                     "qualified KSS requires a documented qualification reference")
        elif target == "subjective_fatigue":
            _require(isinstance(label.get("instrument"), str) and label["instrument"].strip() and
                     label.get("instrument", "").upper() != "KSS" and label.get("qualified") is True,
                     "subjective fatigue requires a separately qualified non-KSS instrument")
            _require(isinstance(label.get("qualification_reference"), str) and label["qualification_reference"].strip(),
                     "subjective fatigue requires an instrument qualification reference")
        elif target == "vigilance_performance":
            _require(label.get("instrument") == "PVT" and
                     label.get("metric") in {"lapse_count", "median_reaction_time_ms", "reciprocal_mean_reaction_time_s"} and
                     isinstance(label.get("protocol_id"), str) and label["protocol_id"].strip(),
                     "vigilance reference requires an independently defined PVT metric and protocol")
        else:
            expected_event = "eye_state" if target == "eye_state_event" else "mouth_opening"
            expected_value = {0, 1}
            _require(label.get("instrument") == "direct_visual_annotation" and
                     label.get("event_type") == expected_event and
                     isinstance(label.get("class_label"), int) and not isinstance(label.get("class_label"), bool) and
                     label.get("class_label") in expected_value and
                     label.get("raw_value") == label.get("class_label"),
                     f"{target} requires its own directly observed binary annotation")


def load_jsonl(path: str | Path, *, allow_unit_test_fixture: bool = False) -> list[dict[str, Any]]:
    """Load and validate authorized JSONL records; never load media/model files."""
    records = []
    with Path(path).open("r", encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ResearchDataError(f"invalid JSON on line {line_number}") from exc
            try:
                validate_record(row, allow_unit_test_fixture=allow_unit_test_fixture)
            except ResearchDataError as exc:
                raise ResearchDataError(f"invalid record on line {line_number}: {exc}") from None
            records.append(row)
    _require(bool(records), "manifest contains no records")
    _require(len({row["sample_id"] for row in records}) == len(records), "sample_id values must be unique")
    return records


def participant_disjoint_split(records: Sequence[Mapping[str, Any]], *, seed: int = 2025,
                               proportions: Sequence[float] = (0.70, 0.15, 0.15)) -> dict[str, str]:
    """Deterministically assign whole participants; return sample_id -> split."""
    _require(len(proportions) == 3 and all(p > 0 for p in proportions) and
             math.isclose(sum(proportions), 1.0, abs_tol=1e-9), "split proportions must be three positive values summing to one")
    participants: dict[str, list[str]] = defaultdict(list)
    for row in records:
        participants[str(row["participant_id"])].append(str(row["sample_id"]))
    _require(len(participants) >= 3, "at least three distinct participants are required for three-way splits")
    ordered = sorted(participants, key=lambda p: hashlib.sha256(f"{seed}:{p}".encode()).digest())
    names = list(SPLIT_NAMES)
    counts = [int(len(ordered) * p) for p in proportions]
    for i in range(len(ordered) - sum(counts)):
        counts[i % len(counts)] += 1
    # Ensure every split is populated while preserving all participants.
    for i in range(len(counts)):
        if counts[i] == 0:
            donor = max(range(len(counts)), key=counts.__getitem__)
            counts[donor] -= 1
            counts[i] += 1
    result: dict[str, str] = {}
    offset = 0
    for name, count in zip(names, counts):
        for participant in ordered[offset:offset+count]:
            result.update((sample_id, name) for sample_id in participants[participant])
        offset += count
    return result


def check_split_leakage(records: Sequence[Mapping[str, Any]], split_by_sample: Mapping[str, str]) -> None:
    """Raise if participant, session, or exact media fingerprint crosses splits."""
    by_key: dict[tuple[str, str], str] = {}
    for row in records:
        sample = str(row["sample_id"])
        _require(sample in split_by_sample and split_by_sample[sample] in SPLIT_NAMES,
                 "every sample must have exactly one known split")
        split = split_by_sample[sample]
        for field in ("participant_id", "session_id"):
            value = row.get(field)
            key = (field, str(value))
            previous = by_key.setdefault(key, split)
            _require(previous == split, f"{field} leakage across splits")
        for fingerprint in row.get("media_fingerprints", []):
            key = ("media_fingerprint", str(fingerprint))
            previous = by_key.setdefault(key, split)
            _require(previous == split, "media fingerprint leakage across splits")


def participant_bootstrap_ci(y_true: Sequence[Any], y_pred: Sequence[Any],
                             participant_ids: Sequence[str], *, metric: str = "accuracy",
                             replicates: int = 1000, seed: int = 2025,
                             confidence: float = 0.95) -> dict[str, float | int]:
    """Cluster bootstrap CI: resample participants, retaining all their rows."""
    _require(len(y_true) == len(y_pred) == len(participant_ids) > 0, "bootstrap inputs must have equal nonzero length")
    _require(metric in {"accuracy", "balanced_accuracy"}, "unsupported bootstrap metric")
    _require(replicates >= 100 and 0 < confidence < 1, "use at least 100 replicates and a valid confidence level")
    groups: dict[str, list[int]] = defaultdict(list)
    for index, participant in enumerate(participant_ids):
        groups[str(participant)].append(index)
    names = list(groups)
    _require(len(names) >= 2, "participant bootstrap requires at least two participants")
    target_classes = set(y_true)
    rng = random.Random(seed)
    values = []
    attempts = 0
    while len(values) < replicates and attempts < replicates * 20:
        attempts += 1
        sampled = [rng.choice(names) for _ in names]
        indexes = [i for name in sampled for i in groups[name]]
        sampled_labels = [y_true[i] for i in indexes]
        if metric == "balanced_accuracy" and set(sampled_labels) != target_classes:
            continue
        name = "accuracy_non_abstained" if metric == "accuracy" else "balanced_accuracy_non_abstained"
        value = classification_metrics(sampled_labels, [y_pred[i] for i in indexes])[name]
        if value is not None:
            values.append(value)
    _require(len(values) == replicates, "could not draw enough valid participant bootstrap replicates")
    values.sort()
    alpha = (1 - confidence) / 2
    low = values[max(0, int(math.floor(alpha * replicates)))]
    high = values[min(replicates - 1, int(math.ceil((1 - alpha) * replicates)) - 1)]
    return {"confidence": confidence, "lower": low, "upper": high, "replicates": replicates,
            "participant_clusters": len(names), "seed": seed}


def regression_metrics(y_true: Sequence[float], y_pred: Sequence[float | None]) -> dict[str, Any]:
    """Continuous-target errors with explicit abstention coverage; no thresholds are inferred."""
    _require(len(y_true) == len(y_pred) > 0, "regression inputs must have equal nonzero length")
    _require(all(isinstance(y, (int, float)) and not isinstance(y, bool) and math.isfinite(y) for y in y_true),
             "regression reference values must be finite numbers")
    _require(all(p is None or isinstance(p, (int, float)) and not isinstance(p, bool) and math.isfinite(p)
                 for p in y_pred), "regression predictions must be finite numbers or explicit abstentions")
    errors = [float(p) - float(y) for y, p in zip(y_true, y_pred) if p is not None]
    count = len(errors)
    return {"sample_count": len(y_true), "evaluated_count": count, "abstention_count": len(y_true) - count,
            "coverage": count / len(y_true), "mae": sum(abs(e) for e in errors) / count if count else None,
            "rmse": math.sqrt(sum(e * e for e in errors) / count) if count else None,
            "mean_error": sum(errors) / count if count else None}


def participant_cluster_regression_ci(y_true: Sequence[float], y_pred: Sequence[float | None],
                                      participant_ids: Sequence[str], *, metric: str = "mae",
                                      replicates: int = 1000, seed: int = 2025,
                                      confidence: float = 0.95) -> dict[str, float | int]:
    """Participant-cluster bootstrap interval for continuous-target MAE or RMSE."""
    _require(len(y_true) == len(y_pred) == len(participant_ids) > 0,
             "regression bootstrap inputs must have equal nonzero length")
    _require(metric in {"mae", "rmse"}, "unsupported regression bootstrap metric")
    _require(replicates >= 100 and 0 < confidence < 1, "use at least 100 replicates and valid confidence")
    groups: dict[str, list[int]] = defaultdict(list)
    for index, participant in enumerate(participant_ids):
        groups[str(participant)].append(index)
    names = list(groups)
    _require(len(names) >= 2, "participant bootstrap requires at least two participants")
    rng = random.Random(seed)
    values = []
    for _ in range(replicates):
        indexes = [i for name in (rng.choice(names) for _ in names) for i in groups[name]]
        evaluated = [(float(y_true[i]), float(y_pred[i])) for i in indexes if y_pred[i] is not None]
        if not evaluated:
            continue
        errors = [pred - truth for truth, pred in evaluated]
        values.append(sum(abs(e) for e in errors) / len(errors) if metric == "mae"
                       else math.sqrt(sum(e * e for e in errors) / len(errors)))
    _require(len(values) >= max(100, replicates // 2), "too few bootstrap replicates had covered predictions")
    values.sort()
    alpha = (1 - confidence) / 2
    return {"confidence": confidence,
            "lower": values[max(0, int(math.floor(alpha * len(values))))],
            "upper": values[min(len(values) - 1, int(math.ceil((1 - alpha) * len(values))) - 1)],
            "replicates": len(values), "participant_clusters": len(names), "seed": seed}


def classification_metrics(y_true: Sequence[Any], y_pred: Sequence[Any], *,
                           probabilities: Sequence[Mapping[Any, float] | None] | None = None,
                           abstain_value: Any = None, calibration_bins: int = 10) -> dict[str, Any]:
    """Classification, abstention, balance and optional probability calibration."""
    _require(len(y_true) == len(y_pred) > 0, "classification inputs must have equal nonzero length")
    _require(probabilities is None or len(probabilities) == len(y_true), "probability rows must align with labels")
    _require(all(label not in _MISSING for label in y_true), "evaluation reference labels must be observed, not missing/unknown")
    _require(calibration_bins >= 1, "calibration_bins must be positive")
    classes = sorted(set(y_true) | {p for p in y_pred if p != abstain_value}, key=str)
    _require(bool(classes), "at least one non-abstained class is required")
    counts = Counter(y_true)
    evaluated = [(truth, pred) for truth, pred in zip(y_true, y_pred) if pred != abstain_value]
    correct = sum(truth == pred for truth, pred in evaluated)
    per_class = {}
    recalls = []
    f1_values = []
    for label in classes:
        tp = sum(t == label and p == label for t, p in evaluated)
        fn = sum(t == label and p != label for t, p in evaluated)
        fp = sum(t != label and p == label for t, p in evaluated)
        recall = tp / (tp + fn) if tp + fn else None
        precision = tp / (tp + fp) if tp + fp else 0.0
        f1 = 2 * precision * recall / (precision + recall) if recall is not None and precision + recall else 0.0
        per_class[str(label)] = {"support": counts[label], "precision": precision, "recall": recall, "f1": f1}
        if recall is not None:
            recalls.append(recall)
            f1_values.append(f1)
    output: dict[str, Any] = {
        "sample_count": len(y_true), "class_balance": {str(k): v for k, v in sorted(counts.items(), key=lambda x: str(x[0]))},
        "accuracy_non_abstained": correct / len(evaluated) if evaluated else None,
        "balanced_accuracy_non_abstained": sum(recalls) / len(recalls) if recalls else None,
        "macro_f1_non_abstained": sum(f1_values) / len(f1_values) if f1_values else None,
        "coverage": len(evaluated) / len(y_true), "abstention_count": len(y_true) - len(evaluated),
        "per_class": per_class,
    }
    if probabilities is not None:
        brier_sum = 0.0
        calibration = []
        for truth, row in zip(y_true, probabilities):
            if row is None:
                continue
            _require(set(row).issubset(set(classes)) and all(isinstance(v, (int, float)) and math.isfinite(v) and 0 <= v <= 1 for v in row.values()),
                     "probabilities must be finite values in [0,1] for known classes")
            _require(math.isclose(sum(row.values()), 1.0, abs_tol=1e-6), "class probabilities must sum to one")
            brier_sum += sum((row.get(label, 0.0) - (1.0 if label == truth else 0.0)) ** 2 for label in classes)
            top_class, confidence = max(row.items(), key=lambda item: item[1])
            calibration.append((confidence, float(top_class == truth)))
        if calibration:
            bins: list[list[tuple[float, float]]] = [[] for _ in range(calibration_bins)]
            for item in calibration:
                bins[min(calibration_bins-1, int(item[0] * calibration_bins))].append(item)
            ece = sum(len(bucket) / len(calibration) * abs(
                sum(x[0] for x in bucket) / len(bucket) - sum(x[1] for x in bucket) / len(bucket)
            ) for bucket in bins if bucket)
            output["calibration"] = {"brier_score": brier_sum / len(calibration), "expected_calibration_error": ece,
                                     "probability_sample_count": len(calibration), "bins": calibration_bins}
        else:
            output["calibration"] = None
    return output


def evaluate_target(records: Sequence[Mapping[str, Any]], predictions: Mapping[str, Mapping[str, Any]], *,
                    target: str, probability_field: str = "probabilities", seed: int = 2025,
                    bootstrap_replicates: int = 1000, allow_unit_test_fixture: bool = False) -> dict[str, Any]:
    """Evaluate predictions for exactly one target using its independent reference labels."""
    _require(target in PILOT_TARGETS, "target must be one supported independent research target")
    selected = []
    for row in records:
        validate_record(row, allow_unit_test_fixture=allow_unit_test_fixture)
        if row.get("schema_version") == "1.2":
            _require(row["consent"]["permissions"]["evaluation"] is True,
                     "evaluation permission is required for evaluation")
        _require(target in row["labels"], f"{target} is not present in this record schema version")
        label = row["labels"][target]
        _require(label["status"] == "observed" and label.get("class_label") is not None,
                 f"{target} requires observed, preregistered class labels; missing labels are not imputed")
        _require(row["sample_id"] in predictions and "prediction" in predictions[row["sample_id"]],
                 "a prediction entry is required for every sample")
        selected.append((row, label, predictions[row["sample_id"]]))
    _require(bool(selected), "no eligible labeled records")
    _require(len({str(item[0]["participant_id"]) for item in selected}) >= 2,
             "generalization evaluation requires at least two distinct participants")
    truth = [item[1]["class_label"] for item in selected]
    predicted = [item[2]["prediction"] for item in selected]
    probability_rows = [item[2].get(probability_field) for item in selected]
    metrics = classification_metrics(truth, predicted, probabilities=probability_rows)
    metrics["participant_clustered_accuracy_ci"] = participant_bootstrap_ci(
        truth, predicted, [str(item[0]["participant_id"]) for item in selected],
        metric="accuracy", seed=seed, replicates=bootstrap_replicates)
    return {"target": target, "label_definition_ids": sorted({item[1]["label_definition_id"] for item in selected}),
            "metrics": metrics, "research_only": True}


def eligible_subgroup_metrics(records: Sequence[Mapping[str, Any]], y_true: Sequence[Any], y_pred: Sequence[Any], *,
                              fields: Sequence[str] = ("site_id", "device_id", "industry"),
                              min_participants: int = 20, min_class_count: int = 5) -> dict[str, Any]:
    """Report only sufficiently supported aggregate groups; suppress small groups."""
    _require(len(records) == len(y_true) == len(y_pred), "subgroup rows must align with predictions")
    result: dict[str, Any] = {}
    for field in fields:
        grouped: dict[str, list[int]] = defaultdict(list)
        for index, row in enumerate(records):
            value = row.get("context", {}).get(field, "unknown")
            if value not in _MISSING:
                grouped[str(value)].append(index)
        result[field] = {}
        for group, indexes in grouped.items():
            subset_labels = [y_true[i] for i in indexes]
            participant_count = len({str(records[i]["participant_id"]) for i in indexes})
            balance = Counter(subset_labels)
            if participant_count < min_participants or min(balance.values(), default=0) < min_class_count:
                result[field][group] = {"suppressed": True, "participant_count": participant_count,
                                         "sample_count": len(indexes), "reason": "minimum_support_not_met"}
            else:
                result[field][group] = {"suppressed": False, "participant_count": participant_count,
                                        **classification_metrics(subset_labels, [y_pred[i] for i in indexes])}
    return result


def external_domain_holdout(records: Sequence[Mapping[str, Any]], *, domain_field: str,
                            held_out_value: str) -> dict[str, Any]:
    """Build a separate site/device holdout, removing participants seen in both domains."""
    _require(domain_field in {"site_id", "device_id"}, "external domain must be site_id or device_id")
    held = [r for r in records if r.get("context", {}).get(domain_field) == held_out_value]
    other = [r for r in records if r.get("context", {}).get(domain_field) != held_out_value]
    held_people = {str(r["participant_id"]) for r in held}
    other_people = {str(r["participant_id"]) for r in other}
    overlap = held_people & other_people
    train = [r for r in other if str(r["participant_id"]) not in overlap]
    test = [r for r in held if str(r["participant_id"]) not in overlap]
    _require(bool(train) and bool(test), "external holdout is infeasible after participant overlap removal")
    check_split_leakage(train + test, {**{str(r["sample_id"]): "train" for r in train},
                                       **{str(r["sample_id"]): "test" for r in test}})
    return {"assessment": "site_disjoint" if domain_field == "site_id" else "device_disjoint",
            "held_out_value": held_out_value, "train": train, "test": test,
            "participants_removed_for_overlap": len(overlap)}


def class_balance(labels: Iterable[Any]) -> dict[str, int]:
    return {str(label): count for label, count in sorted(Counter(labels).items(), key=lambda x: str(x[0]))}


def experiment_record(*, protocol: Mapping[str, Any], results: Mapping[str, Any],
                      split_by_sample: Mapping[str, str], code_files: Sequence[str | Path] = ()) -> dict[str, Any]:
    """Build a metrics-only, deterministic experiment record; excludes raw rows/features."""
    def check_aggregate(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                normalized = "".join(ch for ch in str(key).lower() if ch.isalnum())
                _require(normalized not in _FORBIDDEN_NORMALIZED and normalized not in {"sampleid", "participantid", "sessionid", "mediafingerprints", "features"},
                         "experiment tracking accepts aggregate metrics only; identifiers/features are forbidden")
                check_aggregate(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                check_aggregate(child)
    check_aggregate(results)
    protocol_json = json.dumps(protocol, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    code_hashes = {}
    for file in code_files:
        path = Path(file)
        code_hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    split_json = json.dumps(dict(sorted(split_by_sample.items())), sort_keys=True, separators=(",", ":"))
    return {"evaluation_version": EVALUATION_VERSION, "protocol_sha256": hashlib.sha256(protocol_json.encode()).hexdigest(),
            "split_assignment_sha256": hashlib.sha256(split_json.encode()).hexdigest(),
            "code_sha256": code_hashes, "results": dict(results), "artifact_class": "research_only"}


def validate_preregistration(protocol: Mapping[str, Any]) -> None:
    required = ("protocol_id", "version", "targets", "primary_metrics", "split_policy", "inclusion_criteria",
                "exclusion_criteria", "missing_data_policy", "abstention_policy", "subgroup_policy",
                "calibration_policy", "held_out_test_policy", "analysis_code_version",
                "target_definitions", "sample_size_and_power_analysis")
    _require(all(protocol.get(key) not in (None, "", []) for key in required), "protocol lacks required preregistration fields")
    _require(not _contains_protocol_placeholder(protocol), "protocol contains unresolved placeholders")
    protocol_targets = SUPPORTED_SCHEMA_TARGETS.get(str(protocol.get("version")))
    _require(protocol_targets is not None and set(protocol["targets"]) == set(protocol_targets),
             "protocol targets must match its schema version and remain independently specified")
    _require(all(isinstance(protocol["target_definitions"].get(target), Mapping) and
                 all(value not in (None, "", "REPLACE_BEFORE_STUDY", "TODO")
                     for value in protocol["target_definitions"][target].values()) for target in protocol_targets),
             "each target requires complete independent instrument, label and threshold definitions")
    _require("REPLACE" not in str(protocol["protocol_id"]).upper() and protocol.get("status") != "template_only_not_preregistered",
             "template protocol must be completed and preregistered before evaluation")
    _require(isinstance(protocol["held_out_test_policy"], Mapping), "held_out_test_policy must be an object")
    _require(protocol["held_out_test_policy"].get("previously_inspected_data_reused_as_locked_test") is False,
             "previously inspected locked data cannot be represented as an untouched holdout")
    _require(protocol["held_out_test_policy"].get("one_time_evaluation") is True,
             "held-out evaluation must be one-time")


def _contains_protocol_placeholder(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(_contains_protocol_placeholder(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_protocol_placeholder(item) for item in value)
    if isinstance(value, str):
        normalized = value.strip().upper()
        return normalized in {"TODO", "TBD", "REPLACE_BEFORE_STUDY", "REPLACE BEFORE STUDY"} or "REPLACE_" in normalized
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline Wellar V2 research-manifest validator (does not train/load models)")
    parser.add_argument("manifest", help="authorized de-identified JSONL research manifest")
    args = parser.parse_args()
    rows = load_jsonl(args.manifest)
    print(json.dumps({"evaluation_version": EVALUATION_VERSION, "validated_records": len(rows),
                      "participants": len({row['participant_id'] for row in rows}),
                      "status": "manifest_validated_no_model_training_or_scoring"}, sort_keys=True))


if __name__ == "__main__":
    main()
