"""Small, research-only continuous-target baseline trainer for model-input v1.0."""
from __future__ import annotations

import hashlib
import json
import math
import platform
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from research.evaluation_v1 import ResearchDataError
from research.model_input_v1 import (
    FEATURE_ORDER, MODEL_INPUT_SCHEMA_VERSION, validate_model_input_dataset,
)

TRAINER_VERSION = "wellar-research-regression-trainer/1.0"
ARTIFACT_FORMAT = "wellar-research-ridge-regression/1.0"
MAX_RECORDS = 10_000
MAX_INPUT_BYTES = 32 * 1024 * 1024
MAX_OUTPUT_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class RegressionConfig:
    target: str
    feature_names: tuple[str, ...]
    seed: int = 2025
    ridge_alphas: tuple[float, ...] = (0.01, 0.1, 1.0, 10.0)
    max_feature_missingness: float = 0.5
    bootstrap_replicates: int = 1000

    def validate(self) -> None:
        if self.target not in {"reference_sleepiness", "subjective_fatigue", "vigilance_performance"}:
            raise ResearchDataError("unsupported continuous research target")
        if not self.feature_names or len(set(self.feature_names)) != len(self.feature_names):
            raise ResearchDataError("regression feature list must be nonempty and unique")
        canonical = tuple(name for name in FEATURE_ORDER if name in set(self.feature_names))
        if canonical != self.feature_names:
            raise ResearchDataError("regression features must follow model-input schema order")
        if not self.ridge_alphas or any(not math.isfinite(a) or a <= 0 for a in self.ridge_alphas):
            raise ResearchDataError("ridge alphas must be finite positive values")
        if len(set(self.ridge_alphas)) != len(self.ridge_alphas):
            raise ResearchDataError("ridge alpha candidates must be unique")
        if not 0 <= self.max_feature_missingness < 1:
            raise ResearchDataError("maximum feature missingness must be in [0,1)")
        if self.bootstrap_replicates < 100:
            raise ResearchDataError("participant-cluster bootstrap requires at least 100 replicates")


def _label_value(record: Mapping[str, Any], *, allow_synthetic_fixture: bool) -> float | None:
    label = record["label"]
    if label["status"] != "observed":
        return None
    synthetic = allow_synthetic_fixture and label.get("approval_status") == "synthetic_test_only"
    if label.get("continuous_target_approved") is not True:
        raise ResearchDataError("continuous target must be explicitly designated for regression")
    if (record["target"] == "subjective_fatigue" and not synthetic and
            label.get("approval_status") != "approved"):
        raise ResearchDataError("fatigue target is not scientifically approved")
    return float(label["value"])


def _usable(record: Mapping[str, Any], config: RegressionConfig) -> bool:
    selected = [record["feature_values"][name] for name in config.feature_names]
    missingness = sum(value is None for value in selected) / len(selected)
    if missingness > config.max_feature_missingness:
        return False
    modalities = {name.split(".", 1)[0] for name in config.feature_names}
    return any(record["modality_quality"][modality]["quality_accepted"] for modality in modalities)


def _participant_aggregates(y_true: Sequence[float], y_pred: Sequence[float | None],
                            participants: Sequence[str]) -> dict[str, dict[str, float | int]]:
    grouped: dict[str, list[tuple[float, float]]] = {}
    for truth, prediction, participant in zip(y_true, y_pred, participants):
        if prediction is not None:
            grouped.setdefault(participant, []).append((float(truth), float(prediction)))
    result = {}
    for participant, pairs in grouped.items():
        errors = [pred - truth for truth, pred in pairs]
        result[participant] = {
            "sample_count": len(pairs),
            "mae": sum(abs(error) for error in errors) / len(errors),
            "rmse": math.sqrt(sum(error * error for error in errors) / len(errors)),
        }
    return result


def _cluster_ci(values: Mapping[str, float], *, seed: int, replicates: int) -> dict[str, Any] | None:
    if len(values) < 2:
        return None
    participants = list(values)
    rng = random.Random(seed)
    means = [sum(values[rng.choice(participants)] for _ in participants) / len(participants)
             for _ in range(replicates)]
    means.sort()
    low_index = max(0, math.floor(0.025 * replicates))
    high_index = min(replicates - 1, math.ceil(0.975 * replicates) - 1)
    return {"method": "participant_cluster_percentile_bootstrap", "confidence": 0.95,
            "lower": means[low_index], "upper": means[high_index],
            "replicates": replicates, "participant_clusters": len(participants), "seed": seed}


def _metrics(records: Sequence[Mapping[str, Any]], truths: Sequence[float],
             predictions: Sequence[float | None], config: RegressionConfig, seed_offset: int) -> dict[str, Any]:
    participants = [str(row["participant_id"]) for row in records]
    aggregates = _participant_aggregates(truths, predictions, participants)
    primary_values = {key: float(value["mae"]) for key, value in aggregates.items()}
    secondary_values = {key: float(value["rmse"]) for key, value in aggregates.items()}
    covered = sum(value is not None for value in predictions)
    return {
        "estimand": {
            "primary": "equal_weight_mean_of_participant_level_MAE",
            "secondary": "equal_weight_mean_of_participant_level_RMSE",
            "aggregation": "compute_each_participant_metric_then_average_participants",
        },
        "sample_count": len(records), "covered_sample_count": covered,
        "abstention_count": len(records) - covered, "coverage": covered / len(records),
        "participant_count": len({str(row["participant_id"]) for row in records}),
        "covered_participant_count": len(aggregates),
        "participant_level_mae_mean": sum(primary_values.values()) / len(primary_values) if primary_values else None,
        "participant_level_rmse_mean": sum(secondary_values.values()) / len(secondary_values) if secondary_values else None,
        "participant_level_mae_ci95": _cluster_ci(primary_values, seed=config.seed + seed_offset,
                                                    replicates=config.bootstrap_replicates),
        "participant_level_rmse_ci95": _cluster_ci(secondary_values, seed=config.seed + seed_offset + 1,
                                                     replicates=config.bootstrap_replicates),
    }


def _matrix(records: Sequence[Mapping[str, Any]], config: RegressionConfig) -> np.ndarray:
    return np.asarray([[record["feature_values"][name] if record["feature_values"][name] is not None
                        else np.nan for name in config.feature_names] for record in records], dtype=np.float64)


def _fit_preprocessor(train_records: Sequence[Mapping[str, Any]], config: RegressionConfig):
    matrix = _matrix(train_records, config)
    if matrix.size == 0 or np.any(np.all(np.isnan(matrix), axis=0)):
        raise ResearchDataError("each configured feature needs an accepted training observation")
    medians = np.nanmedian(matrix, axis=0)
    imputed = np.where(np.isnan(matrix), medians, matrix)
    means = np.mean(imputed, axis=0)
    scales = np.std(imputed, axis=0)
    scales = np.where(scales < 1e-12, 1.0, scales)
    return medians, means, scales


def _transform(records: Sequence[Mapping[str, Any]], config: RegressionConfig,
               preprocessor: tuple[np.ndarray, np.ndarray, np.ndarray]) -> np.ndarray:
    medians, means, scales = preprocessor
    raw = _matrix(records, config)
    imputed = np.where(np.isnan(raw), medians, raw)
    return np.clip((imputed - means) / scales, -20.0, 20.0)


def _fit_ridge(x: np.ndarray, y: np.ndarray, alpha: float) -> tuple[float, list[float]]:
    design = np.column_stack((np.ones(len(x)), x))
    penalty = np.eye(design.shape[1], dtype=np.float64) * alpha
    penalty[0, 0] = 0.0
    try:
        coefficients = np.linalg.solve(design.T @ design + penalty, design.T @ y)
    except np.linalg.LinAlgError:
        coefficients = np.linalg.pinv(design.T @ design + penalty) @ design.T @ y
    if not np.all(np.isfinite(coefficients)):
        raise ResearchDataError("ridge regression produced non-finite coefficients")
    return float(coefficients[0]), coefficients[1:].astype(float).tolist()


def train_regression(
    records: Sequence[Mapping[str, Any]], config: RegressionConfig, *,
    allow_synthetic_fixture: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fit median and ridge candidates, select on validation, then evaluate test once."""
    config.validate()
    if not records or len(records) > MAX_RECORDS:
        raise ResearchDataError("regression requires a bounded nonempty model-input dataset")
    validate_model_input_dataset(records, allow_synthetic_fixture=allow_synthetic_fixture)
    if any(row["target"] != config.target for row in records):
        raise ResearchDataError("all model-input rows must use the configured target")
    split_names = {row["split"] for row in records}
    if split_names != {"train", "validation", "test"}:
        raise ResearchDataError("train, validation, and test splits are all required")
    by_split = {name: [row for row in records if row["split"] == name] for name in split_names}
    for name, rows in by_split.items():
        if len({row["participant_id"] for row in rows}) < 2:
            raise ResearchDataError(f"{name} requires at least two participant clusters")

    labeled: dict[str, list[tuple[Mapping[str, Any], float]]] = {}
    for name, rows in by_split.items():
        labeled[name] = []
        for row in rows:
            value = _label_value(row, allow_synthetic_fixture=allow_synthetic_fixture)
            if value is not None:
                labeled[name].append((row, value))
    train_pairs = [(row, value) for row, value in labeled["train"] if _usable(row, config)]
    validation_pairs = [(row, value) for row, value in labeled["validation"] if _usable(row, config)]
    if len(train_pairs) < 2 or len(validation_pairs) < 2:
        raise ResearchDataError("training and validation need at least two labeled quality-accepted records")
    train_rows = [row for row, _ in train_pairs]
    y_train = np.asarray([value for _, value in train_pairs], dtype=np.float64)
    preprocessor = _fit_preprocessor(train_rows, config)
    x_train = _transform(train_rows, config, preprocessor)
    validation_rows = [row for row, _ in validation_pairs]
    y_validation = [value for _, value in validation_pairs]
    x_validation = _transform(validation_rows, config, preprocessor)

    baseline_value = float(np.median(y_train))
    candidates: list[dict[str, Any]] = [{"name": "constant_median", "median": baseline_value}]
    for alpha in config.ridge_alphas:
        intercept, coefficients = _fit_ridge(x_train, y_train, alpha)
        candidates.append({"name": "ridge_linear", "alpha": float(alpha),
                           "intercept": intercept, "coefficients": coefficients})

    # Candidate selection uses only validation labels and participant-level MAE.
    selection_scores = []
    for candidate in candidates:
        if candidate["name"] == "constant_median":
            pred = [baseline_value] * len(validation_rows)
        else:
            pred = (x_validation @ np.asarray(candidate["coefficients"]) + candidate["intercept"]).tolist()
        grouped = _participant_aggregates(y_validation, pred,
                                          [str(row["participant_id"]) for row in validation_rows])
        score = sum(float(value["mae"]) for value in grouped.values()) / len(grouped) if grouped else math.inf
        selection_scores.append({"candidate": candidate["name"], "alpha": candidate.get("alpha"),
                                 "validation_participant_mae": score,
                                 "validation_covered_samples": len(pred),
                                 "validation_observed_samples": len(labeled["validation"]),
                                 "validation_coverage": len(pred) / len(labeled["validation"])})
    selected_index = min(range(len(candidates)), key=lambda i: (selection_scores[i]["validation_participant_mae"], i))
    selected = candidates[selected_index]

    # Test rows are examined only after candidate selection and train-only preprocessing are frozen.
    test_pairs = labeled["test"]
    test_rows = [row for row, _ in test_pairs]
    y_test = [value for _, value in test_pairs]
    if not test_rows:
        raise ResearchDataError("test split requires observed target labels")
    usable_test = [_usable(row, config) for row in test_rows]
    predictions: list[float | None] = [None] * len(test_rows)
    if any(usable_test):
        usable_rows = [row for row, accepted in zip(test_rows, usable_test) if accepted]
        x_test = _transform(usable_rows, config, preprocessor)
        if selected["name"] == "constant_median":
            selected_values = [baseline_value] * len(usable_rows)
        else:
            selected_values = (x_test @ np.asarray(selected["coefficients"]) + selected["intercept"]).tolist()
        iterator = iter(float(value) for value in selected_values)
        predictions = [next(iterator) if accepted else None for accepted in usable_test]
    test_metrics = _metrics(test_rows, y_test, predictions, config, seed_offset=100)
    baseline_predictions = [baseline_value if accepted else None for accepted in usable_test]
    baseline_test_metrics = _metrics(test_rows, y_test, baseline_predictions, config, seed_offset=100)

    preprocessor_json = {"training_medians": preprocessor[0].tolist(),
                         "training_means": preprocessor[1].tolist(),
                         "training_scales": preprocessor[2].tolist(),
                         "fit_split": "train"}
    input_payload = json.dumps(list(records), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    config_payload = json.dumps(asdict(config), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    split_payload = json.dumps(sorted((str(row["sample_id"]), row["split"]) for row in records),
                               separators=(",", ":")).encode()
    code_files = {"regression_training.py": Path(__file__),
                  "model_input_v1.py": Path(__file__).with_name("model_input_v1.py"),
                  "media_feature_adapter.py": Path(__file__).with_name("media_feature_adapter.py")}
    artifact = {
        "format": ARTIFACT_FORMAT, "artifact_class": "research_candidate", "research_only": True,
        "validation_status": "not_validated", "model_input_schema_version": MODEL_INPUT_SCHEMA_VERSION,
        "target": config.target, "feature_order": list(config.feature_names),
        "selected_candidate": selected["name"], "selected_ridge_alpha": selected.get("alpha"),
        "baseline_median": baseline_value, "baseline_selection_score": selection_scores[0]["validation_participant_mae"],
        "selected_intercept": selected.get("intercept"), "selected_coefficients": selected.get("coefficients"),
        "candidate_models": candidates, "validation_selection": selection_scores,
        "preprocessor": preprocessor_json, "config": asdict(config),
        "metadata": {"trainer_version": TRAINER_VERSION, "python_version": platform.python_version(),
                     "numpy_version": np.__version__, "input_records_sha256": hashlib.sha256(input_payload).hexdigest(),
                     "training_config_sha256": hashlib.sha256(config_payload).hexdigest(),
                     "split_assignment_sha256": hashlib.sha256(split_payload).hexdigest(),
                     "split_sample_counts": {name: sum(row["split"] == name for row in records)
                                             for name in ("train", "validation", "test")},
                     "split_participant_counts": {name: len({row["participant_id"] for row in records
                                                             if row["split"] == name})
                                                  for name in ("train", "validation", "test")},
                     "code_sha256": {name: hashlib.sha256(path.read_bytes()).hexdigest()
                                     for name, path in code_files.items()},
                     "media_feature_schema_version": records[0]["provenance"]["media_feature_schema_version"],
                     "extractor_provenance_values": sorted({json.dumps(row["provenance"]["extractors"],
                                                                       sort_keys=True, separators=(",", ":"))
                                                            for row in records}),
                     "train_sample_count": len(train_rows), "train_participant_count": len({r["participant_id"] for r in train_rows}),
                     "validation_sample_count": len(validation_rows),
                     "validation_participant_count": len({r["participant_id"] for r in validation_rows}),
                     "test_access": "after_validation_selection_only",
                     "scientific_validity": "not_assessed_software_test_only" if allow_synthetic_fixture else "requires_independent_review"},
    }
    report = {
        "status": "synthetic_software_pipeline_only" if allow_synthetic_fixture else "research_candidate_not_scientifically_validated",
        "target": config.target, "selected_candidate": selected["name"],
        "validation_candidate_selection": selection_scores,
        "test_metrics_constant_median_baseline_once": baseline_test_metrics,
        "test_metrics_selected_candidate_once": test_metrics,
        "test_split_consumed_for_fit_or_selection": False,
        "research_only": True,
    }
    return artifact, report


def persist_research_artifact(directory: str | Path, artifact: Mapping[str, Any],
                              report: Mapping[str, Any]) -> dict[str, Path]:
    """Persist new outputs only under a research-named directory; never overwrite."""
    output_dir = Path(directory)
    if output_dir.name.lower() not in {"research", "research_artifacts"}:
        raise ResearchDataError("regression artifacts must be saved in a research-only directory")
    output_dir.mkdir(parents=True, exist_ok=True)
    artifact_bytes = json.dumps(artifact, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    manifest = {"artifact_class": "research_candidate", "research_only": True,
                "validation_status": "not_validated", "artifact_format": ARTIFACT_FORMAT,
                "artifact_sha256": hashlib.sha256(artifact_bytes).hexdigest(),
                "model_input_schema_version": MODEL_INPUT_SCHEMA_VERSION,
                "target": artifact["target"], "approval_reference": "research_candidate_no_operational_approval"}
    files = {
        "model": output_dir / "regression_model.json",
        "manifest": output_dir / "regression_manifest.json",
        "report": output_dir / "regression_report.json",
        "config": output_dir / "regression_config.json",
        "schema": output_dir / "regression_schema.json",
    }
    schema = {"schema_version": MODEL_INPUT_SCHEMA_VERSION,
              "media_feature_schema_version": artifact["metadata"].get("media_feature_schema_version"),
              "feature_order": artifact["feature_order"],
              "feature_masks": "feature_available_mask and feature_missingness are input metadata, not predictors",
              "modality_quality_masks": "only quality_accepted modality values may contribute; rejected values remain missing"}
    payloads = {"model": artifact_bytes,
                "manifest": (json.dumps(manifest, sort_keys=True, indent=2, allow_nan=False) + "\n").encode(),
                "report": (json.dumps(dict(report), sort_keys=True, indent=2, allow_nan=False) + "\n").encode(),
                "config": (json.dumps(artifact["config"], sort_keys=True, indent=2, allow_nan=False) + "\n").encode(),
                "schema": (json.dumps(schema, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()}
    if any(len(value) > MAX_OUTPUT_BYTES for value in payloads.values()):
        raise ResearchDataError("regression output exceeds the 2 MiB bound")
    if any(path.exists() for path in files.values()):
        raise ResearchDataError("refusing to overwrite existing research artifact output")
    created = []
    try:
        for key, path in files.items():
            with path.open("xb") as stream:
                stream.write(payloads[key])
            created.append(path)
    except OSError as exc:
        # Leave successful outputs intact for inspection; never delete user data.
        raise ResearchDataError("research artifact persistence failed without replacing existing files") from exc
    return files


def train_regression_files(data_path: str | Path, config_path: str | Path, output_dir: str | Path,
                           *, allow_synthetic_fixture: bool = False) -> dict[str, Any]:
    """Bounded JSONL/config entry point suitable for offline, research-only use."""
    data_path, config_path = Path(data_path), Path(config_path)
    if data_path.stat().st_size > MAX_INPUT_BYTES or config_path.stat().st_size > 16 * 1024:
        raise ResearchDataError("input dataset or regression config exceeds size limits")
    records = []
    for line_no, line in enumerate(data_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            raise ResearchDataError(f"invalid model-input JSON on line {line_no}") from None
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    allowed = {"target", "feature_names", "seed", "ridge_alphas", "max_feature_missingness", "bootstrap_replicates"}
    if not isinstance(raw, dict) or not set(raw).issubset(allowed) or not {"target", "feature_names"}.issubset(raw):
        raise ResearchDataError("regression config fields are invalid")
    raw["feature_names"] = tuple(raw["feature_names"])
    if "ridge_alphas" in raw:
        raw["ridge_alphas"] = tuple(raw["ridge_alphas"])
    config = RegressionConfig(**raw)
    artifact, report = train_regression(records, config, allow_synthetic_fixture=allow_synthetic_fixture)
    persist_research_artifact(output_dir, artifact, report)
    return report
