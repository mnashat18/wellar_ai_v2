"""Bounded, reproducible candidate trainer for separately labeled V2 targets.

The CLI accepts only consented, rights-attested research manifests. It writes a
safe JSON linear-softmax artifact and a research-only candidate manifest; these
are rejected by the operational artifact guard until independent release.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from app.ml.runtime import MODEL_INTERFACE_VERSION
from app.ml.feature_mapping import FEATURE_MAPPING_VERSION
from research.manifest_validation import fatigue_mapping_is_approved
from research.evaluation_v1 import (
    ResearchDataError,
    check_split_leakage,
    classification_metrics,
    load_jsonl,
    participant_disjoint_split,
    validate_record,
)

TRAINER_VERSION = "wellar-research-trainer/1.0"
ARTIFACT_FORMAT = "wellar-linear-softmax/1.0"
MAX_DATASET_BYTES = 16 * 1024 * 1024
MAX_SAMPLES = 5_000
MAX_FEATURES = 64
MAX_ARTIFACT_BYTES = 1 * 1024 * 1024
MAX_EPOCHS = 250
TARGET_LABELS = {
    "reference_sleepiness": "reference_sleepiness",
    "subjective_fatigue": "subjective_fatigue",
    "vigilance_impairment": "vigilance_performance",
    "eye_state_event": "eye_state_event",
    "mouth_opening_event": "mouth_opening_event",
}
VISUAL_EVENT_FEATURES = {
    "eye_state_event": {"image.eye_aspect_ratio", "image.gray_mean", "image.gray_std",
                         "video.eye_aspect_ratio", "video.mean_sampled_luminance"},
    "mouth_opening_event": {"image.mouth_aspect_ratio", "image.gray_mean", "image.gray_std",
                             "video.mouth_aspect_ratio", "video.mean_sampled_luminance"},
}


@dataclass(frozen=True)
class TrainingConfig:
    target: str
    feature_names: tuple[str, ...]
    seed: int = 2025
    proportions: tuple[float, float, float] = (0.70, 0.15, 0.15)
    learning_rate: float = 0.05
    epochs: int = 250
    l2: float = 0.001
    min_confidence: float = 0.60
    max_feature_missingness: float = 0.50

    def validate(self) -> None:
        if self.target not in TARGET_LABELS:
            raise ResearchDataError("unsupported independent prediction target")
        if not self.feature_names or len(self.feature_names) > MAX_FEATURES or len(set(self.feature_names)) != len(self.feature_names):
            raise ResearchDataError("feature list must contain 1 to 64 unique names")
        if any(not _valid_feature_name(name) for name in self.feature_names):
            raise ResearchDataError("features must use modality.feature_name identifiers")
        if self.target in VISUAL_EVENT_FEATURES and not set(self.feature_names).issubset(VISUAL_EVENT_FEATURES[self.target]):
            raise ResearchDataError("visual-event models may use only predeclared geometry and image-quality features")
        if len(self.proportions) != 3 or any(p <= 0 for p in self.proportions) or not math.isclose(sum(self.proportions), 1.0, abs_tol=1e-9):
            raise ResearchDataError("split proportions must be three positive values summing to one")
        if not 1 <= self.epochs <= MAX_EPOCHS or not 0 < self.learning_rate <= 1 or not 0 <= self.l2 <= 10:
            raise ResearchDataError("optimizer configuration exceeds safe bounds")
        if not 0.5 <= self.min_confidence < 1 or not 0 <= self.max_feature_missingness < 1:
            raise ResearchDataError("abstention thresholds are outside supported bounds")


def _valid_feature_name(value: str) -> bool:
    if not isinstance(value, str) or value.count(".") != 1:
        return False
    modality, name = value.split(".")
    return modality in {"video", "audio", "image", "reaction_task"} and bool(name) and all(c.isalnum() or c == "_" for c in name)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate_rights(rights: Mapping[str, Any], records: Sequence[Mapping[str, Any]]) -> None:
    required = ("dataset_id", "dataset_version", "license_id", "approval_reference", "protocol_sha256")
    if not isinstance(rights, Mapping) or any(not isinstance(rights.get(k), str) or not rights[k].strip() for k in required):
        raise ResearchDataError("rights manifest requires dataset, version, license, approval and protocol provenance")
    if rights.get("model_training_authorized") is not True or rights.get("consent_confirmed") is not True:
        raise ResearchDataError("dataset rights and consent must explicitly authorize model training")
    if not all(c in "0123456789abcdefABCDEF" for c in rights["protocol_sha256"]) or len(rights["protocol_sha256"]) != 64:
        raise ResearchDataError("protocol_sha256 must be a SHA-256 digest")
    for row in records:
        provenance = row["provenance"]
        if provenance.get("dataset_id") != rights["dataset_id"] or provenance.get("dataset_version") != rights["dataset_version"]:
            raise ResearchDataError("record dataset provenance does not match the rights manifest")
        permissions = row["consent"]["permissions"]
        if row.get("schema_version") == "1.2":
            if (permissions.get("media_analysis") is not True or permissions.get("media_storage") is not True or
                    permissions.get("model_training") is not True or permissions.get("evaluation") is not True):
                raise ResearchDataError("pilot records require separate media-analysis, storage, model-training and evaluation permission")
        elif permissions.get("model_training") is not True or permissions.get("research_evaluation") is not True:
            raise ResearchDataError("every record must explicitly allow model training and research evaluation")


def _feature_value(row: Mapping[str, Any], feature_name: str) -> float:
    modality, name = feature_name.split(".", 1)
    value = row["features"][modality]["values"].get(name)
    if value is None or isinstance(value, bool):
        return float("nan")
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ResearchDataError("feature values must be finite numbers or explicit nulls")
    return float(value)


def _softmax(logits: np.ndarray, temperature: float = 1.0) -> np.ndarray:
    scaled = logits / temperature
    scaled = scaled - np.max(scaled, axis=1, keepdims=True)
    exp = np.exp(np.clip(scaled, -60.0, 60.0))
    return exp / np.sum(exp, axis=1, keepdims=True)


def _fit_temperature(logits: np.ndarray, y: np.ndarray) -> float:
    candidates = np.geomspace(0.5, 5.0, 91)
    losses = []
    for temperature in candidates:
        probabilities = _softmax(logits, float(temperature))
        losses.append(float(-np.mean(np.log(np.clip(probabilities[np.arange(len(y)), y], 1e-12, 1.0)))))
    return float(candidates[int(np.argmin(losses))])


def train_target(
    records: Sequence[Mapping[str, Any]],
    rights: Mapping[str, Any],
    config: TrainingConfig,
    *,
    allow_unit_test_fixture: bool = False,
    source_data_sha256: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Train one target only; returns safe JSON artifact and non-scientific software report."""
    config.validate()
    if not records or len(records) > MAX_SAMPLES:
        raise ResearchDataError(f"training requires 1 to {MAX_SAMPLES} bounded records")
    for row in records:
        validate_record(row, allow_unit_test_fixture=allow_unit_test_fixture)
    _validate_rights(rights, records)
    if config.target == "subjective_fatigue":
        for row in records:
            if row.get("fixture_marker") == "unit_test_synthetic" and allow_unit_test_fixture:
                continue
            label = row.get("labels", {}).get("subjective_fatigue", {})
            mapping = (label.get("instrument_id"), label.get("subscale"), label.get("scoring_version"))
            if (label.get("approval_status") != "approved" or
                    not all(isinstance(value, str) and value.strip() for value in mapping) or
                    not fatigue_mapping_is_approved(*mapping)):
                raise ResearchDataError("fatigue target mapping is not scientifically approved")
    if len({row["participant_id"] for row in records}) < 12:
        raise ResearchDataError("at least 12 distinct participants are required for three-way participant splits")

    label_key = TARGET_LABELS[config.target]
    eligible = []
    for row in records:
        label = row["labels"].get(label_key)
        if label.get("status") != "observed" or label.get("class_label") is None:
            continue
        class_value = label["class_label"]
        if isinstance(class_value, bool) or not isinstance(class_value, int):
            raise ResearchDataError("trainer v1 requires protocol-defined integer class_label values")
        if (row.get("fixture_marker") != "unit_test_synthetic" and
                config.target not in {"eye_state_event", "mouth_opening_event"} and
                (label.get("classification_rule_approval_status") != "approved" or
                 not isinstance(label.get("classification_rule_approval_reference"), str) or
                 not label["classification_rule_approval_reference"].strip())):
            raise ResearchDataError("classification requires an approved target-specific threshold rule")
        eligible.append(row)
    if len(eligible) > MAX_SAMPLES or len({r["participant_id"] for r in eligible}) < 12:
        raise ResearchDataError("target has fewer than 12 eligible labeled participants")
    classes = sorted({int(row["labels"][label_key]["class_label"]) for row in eligible})
    if len(classes) < 2 or len(classes) > 32:
        raise ResearchDataError("target requires between 2 and 32 distinct integer classes")

    split_map = participant_disjoint_split(eligible, seed=config.seed, proportions=config.proportions)
    check_split_leakage(eligible, split_map)
    class_index = {label: index for index, label in enumerate(classes)}
    matrix = np.asarray([[_feature_value(row, name) for name in config.feature_names] for row in eligible], dtype=np.float64)
    if np.any(np.all(np.isnan(matrix), axis=0)):
        raise ResearchDataError("a configured feature is entirely missing")
    sample_missingness = np.mean(np.isnan(matrix), axis=1)
    keep = sample_missingness <= config.max_feature_missingness
    eligible = [row for row, accepted in zip(eligible, keep) if accepted]
    matrix = matrix[keep]
    if len(eligible) < 12 or len({r["participant_id"] for r in eligible}) < 12:
        raise ResearchDataError("quality gating leaves fewer than 12 eligible labeled participants")
    classes = sorted({int(row["labels"][label_key]["class_label"]) for row in eligible})
    if len(classes) < 2:
        raise ResearchDataError("quality gating leaves fewer than two target classes")
    class_index = {label: index for index, label in enumerate(classes)}
    y = np.asarray([class_index[int(row["labels"][label_key]["class_label"])] for row in eligible], dtype=np.int64)
    train_idx = np.asarray([i for i, row in enumerate(eligible) if split_map[row["sample_id"]] == "train"], dtype=np.int64)
    validation_idx = np.asarray([i for i, row in enumerate(eligible) if split_map[row["sample_id"]] == "validation"], dtype=np.int64)
    test_idx = np.asarray([i for i, row in enumerate(eligible) if split_map[row["sample_id"]] == "test"], dtype=np.int64)
    if min(len(train_idx), len(validation_idx), len(test_idx)) == 0:
        raise ResearchDataError("all three participant-disjoint splits must contain eligible labels")
    if set(y[train_idx]) != set(range(len(classes))) or set(y[validation_idx]) != set(range(len(classes))):
        raise ResearchDataError("train and validation splits must contain every target class")

    train_raw = matrix[train_idx]
    with np.errstate(all="ignore"):
        medians = np.nanmedian(train_raw, axis=0)
    if not np.all(np.isfinite(medians)):
        raise ResearchDataError("a configured feature is entirely missing in the training split")
    imputed = np.where(np.isnan(matrix), medians, matrix)
    means = np.mean(imputed[train_idx], axis=0)
    scales = np.std(imputed[train_idx], axis=0)
    scales = np.where(scales < 1e-12, 1.0, scales)
    normalized = np.clip((imputed - means) / scales, -20.0, 20.0)

    class_count = len(classes)
    weights = np.zeros((class_count, len(config.feature_names)), dtype=np.float64)
    bias = np.zeros(class_count, dtype=np.float64)
    one_hot = np.eye(class_count, dtype=np.float64)[y[train_idx]]
    x_train = normalized[train_idx]
    for _ in range(config.epochs):
        probabilities = _softmax(x_train @ weights.T + bias)
        error = probabilities - one_hot
        weights -= config.learning_rate * ((error.T @ x_train) / len(train_idx) + config.l2 * weights)
        bias -= config.learning_rate * np.mean(error, axis=0)
        if not np.all(np.isfinite(weights)) or not np.all(np.isfinite(bias)):
            raise ResearchDataError("optimizer diverged; no artifact was emitted")

    validation_logits = normalized[validation_idx] @ weights.T + bias
    temperature = _fit_temperature(validation_logits, y[validation_idx])
    test_logits = normalized[test_idx] @ weights.T + bias
    test_probabilities = _softmax(test_logits, temperature)
    predicted_indexes = np.argmax(test_probabilities, axis=1)
    confidences = np.max(test_probabilities, axis=1)
    predictions = [classes[index] if confidence >= config.min_confidence else None
                   for index, confidence in zip(predicted_indexes, confidences)]
    probability_rows = [{classes[j]: float(value) for j, value in enumerate(row)} for row in test_probabilities]
    y_true = [classes[int(index)] for index in y[test_idx]]
    metrics = classification_metrics(y_true, predictions, probabilities=probability_rows, abstain_value=None)

    provenance_bytes = json.dumps(dict(rights), sort_keys=True, separators=(",", ":")).encode()
    config_bytes = json.dumps(asdict(config), sort_keys=True, separators=(",", ":")).encode()
    artifact = {
        "format": ARTIFACT_FORMAT,
        "model_interface_version": MODEL_INTERFACE_VERSION,
        "target": config.target,
        "model_version": f"candidate-{_sha256(provenance_bytes)[:12]}-{config.seed}",
        "classes": classes,
        "feature_names": list(config.feature_names),
        "imputation_medians": medians.tolist(),
        "means": means.tolist(),
        "scales": scales.tolist(),
        "weights": weights.tolist(),
        "bias": bias.tolist(),
        "temperature": temperature,
        "min_confidence": config.min_confidence,
        "max_feature_missingness": config.max_feature_missingness,
        "calibrated": True,
        "calibration_method": "validation_temperature_scaling_grid_0.5_to_5.0",
        "required_modalities": sorted({name.split(".", 1)[0] for name in config.feature_names}),
        "metadata": {
            "trainer_version": TRAINER_VERSION,
            "dataset_id": rights["dataset_id"],
            "dataset_version": rights["dataset_version"],
            "license_id": rights["license_id"],
            "approval_reference": rights["approval_reference"],
            "rights_manifest_sha256": _sha256(provenance_bytes),
            "dataset_content_sha256": source_data_sha256 or "not_available_in_memory",
            "training_config_sha256": _sha256(config_bytes),
            "python_version": platform.python_version(),
            "numpy_version": np.__version__,
            "participant_counts": {name: len({r["participant_id"] for i, r in enumerate(eligible) if split_map[r["sample_id"]] == name}) for name in ("train", "validation", "test")},
            "sample_counts": {name: int(sum(split_map[r["sample_id"]] == name for r in eligible)) for name in ("train", "validation", "test")},
            "class_balance": {str(label): int(sum(y[i] == class_index[label] for i in range(len(eligible)))) for label in classes},
            "split_seed": config.seed,
            "scientific_validation": "not_assessed_by_training_software",
            "training_data_class": "unit_test_synthetic" if any(r.get("fixture_marker") for r in records) else "rights_attested_research_candidate",
        },
    }
    encoded = json.dumps(artifact, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if len(encoded) > MAX_ARTIFACT_BYTES:
        raise ResearchDataError("serialized model exceeds the 1 MiB artifact bound")
    manifest = {
        "artifact_class": "research_candidate",
        "research_only": True,
        "validation_status": "not_validated",
        "artifact_sha256": _sha256(encoded),
        "target": config.target,
        "feature_mapping_version": FEATURE_MAPPING_VERSION,
        "model_interface_version": MODEL_INTERFACE_VERSION,
        "model_version": artifact["model_version"],
        "required_modalities": artifact["required_modalities"],
        "max_missingness": config.max_feature_missingness,
        "calibrated": True,
        "protocol_sha256": rights["protocol_sha256"],
        "held_out_evaluation_id": "not_independently_assessed",
        "approval_reference": rights["approval_reference"],
    }
    report = {
        "status": "software_pipeline_executed_not_scientific_validation",
        "target": config.target,
        "artifact_sha256": manifest["artifact_sha256"],
        "split_counts": artifact["metadata"]["sample_counts"],
        "target_specific_test_metrics": metrics,
        "calibration_temperature": temperature,
        "scientific_validation": "not_assessed_by_training_software",
    }
    return artifact, {"manifest": manifest, "report": report}


def _write_new(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as output:
        output.write(content)


def train_files(data_path: Path, rights_path: Path, config: TrainingConfig, artifact_path: Path, report_path: Path) -> dict[str, Any]:
    if data_path.stat().st_size > MAX_DATASET_BYTES or rights_path.stat().st_size > 64 * 1024:
        raise ResearchDataError("dataset or rights manifest exceeds configured size limit")
    source_bytes = data_path.read_bytes()
    if len(source_bytes) > MAX_DATASET_BYTES:
        raise ResearchDataError("dataset exceeds configured size limit")
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    records = load_jsonl(data_path)
    rights = json.loads(rights_path.read_text(encoding="utf-8"))
    artifact, output = train_target(records, rights, config, source_data_sha256=source_hash)
    artifact_bytes = json.dumps(artifact, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    manifest_bytes = json.dumps(output["manifest"], sort_keys=True, indent=2, allow_nan=False).encode() + b"\n"
    report_bytes = json.dumps(output["report"], sort_keys=True, indent=2, allow_nan=False).encode() + b"\n"
    manifest_path = artifact_path.with_suffix(".manifest.json")
    _write_new(artifact_path, artifact_bytes)
    try:
        _write_new(manifest_path, manifest_bytes)
        _write_new(report_path, report_bytes)
    except Exception:
        # Never delete user output; leave any successfully written candidate for review.
        raise ResearchDataError("candidate output path already exists or could not be written") from None
    return output["report"]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train one research-only Wellar target candidate from rights-attested JSONL.")
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--rights", type=Path, required=True)
    parser.add_argument("--target", choices=sorted(TARGET_LABELS))
    parser.add_argument("--features", help="comma-separated predeclared modality.feature names")
    parser.add_argument("--config", type=Path, help="bounded JSON training configuration")
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=2025)
    args = parser.parse_args(argv)
    try:
        if args.config:
            if args.config.stat().st_size > 16 * 1024:
                raise ResearchDataError("training configuration exceeds the 16 KiB limit")
            raw = json.loads(args.config.read_text(encoding="utf-8"))
            allowed = {"target", "feature_names", "seed", "proportions", "learning_rate", "epochs", "l2", "min_confidence", "max_feature_missingness"}
            if not isinstance(raw, dict) or not set(raw).issubset(allowed) or not {"target", "feature_names"}.issubset(raw):
                raise ResearchDataError("training configuration fields are invalid")
            raw["feature_names"] = tuple(raw["feature_names"])
            if "proportions" in raw:
                raw["proportions"] = tuple(raw["proportions"])
            config = TrainingConfig(**raw)
        else:
            if not args.target or not args.features:
                raise ResearchDataError("provide --config or both --target and --features")
            config = TrainingConfig(target=args.target, feature_names=tuple(v.strip() for v in args.features.split(",")), seed=args.seed)
        config.validate()
        report = train_files(args.data, args.rights, config, args.artifact, args.report)
    except (OSError, json.JSONDecodeError, ResearchDataError) as exc:
        print(f"training blocked: {exc}")
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


# Keep both numeric research paths discoverable from the existing training module.
from research.regression_training import (  # noqa: E402
    RegressionConfig, train_regression, train_regression_files,
)


if __name__ == "__main__":
    raise SystemExit(main())
