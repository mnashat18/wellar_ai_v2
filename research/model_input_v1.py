"""Versioned research-only model-input contract for adapter records and labels.

This module assembles typed numeric inputs; it does not infer or approve targets.
Participant/session identifiers and quality masks are metadata, never predictors.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from research.evaluation_v1 import ResearchDataError, check_split_leakage
from research.manifest_validation import fatigue_mapping_is_approved
from research.media_feature_adapter import (
    FEATURES, MODALITY_ORDER, SCHEMA_VERSION as MEDIA_FEATURE_SCHEMA_VERSION,
    validate_feature_record,
)

MODEL_INPUT_SCHEMA_VERSION = "wellar-research-model-input/1.0"
FEATURE_ORDER = tuple(f"{modality}.{name}" for modality in MODALITY_ORDER for name in FEATURES[modality])
SPLITS = {"train", "validation", "test"}


def _fail(condition: bool, message: str) -> None:
    if not condition:
        raise ResearchDataError(message)


def _validate_label(target: str, label: Mapping[str, Any], *, allow_synthetic_fixture: bool) -> None:
    _fail(target in {"reference_sleepiness", "subjective_fatigue", "vigilance_performance"},
          "unsupported independent research target")
    _fail(label.get("status") in {"observed", "missing", "unknown"}, "label status must be explicit")
    if label["status"] != "observed":
        _fail(label.get("value") is None and isinstance(label.get("missing_reason"), str)
              and bool(label["missing_reason"].strip()), "missing target requires null value and reason")
        return
    value = label.get("value")
    _fail(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value),
          "observed target value must be finite numeric")
    for field in ("definition_id", "source_reference", "measured_at_utc"):
        _fail(isinstance(label.get(field), str) and bool(label[field].strip()),
              f"observed target requires {field} provenance")
    approval = label.get("approval_status")
    if allow_synthetic_fixture and approval == "synthetic_test_only":
        return
    _fail(approval == "approved" and isinstance(label.get("approval_reference"), str)
          and bool(label["approval_reference"].strip()),
          "real model inputs require an approved label definition and approval reference")
    if target == "subjective_fatigue":
        mapping = (label.get("instrument_id"), label.get("subscale"), label.get("scoring_version"))
        _fail(all(isinstance(v, str) and v.strip() for v in mapping)
              and fatigue_mapping_is_approved(*mapping),
              "fatigue target mapping is not scientifically approved")


def build_model_input_records(
    feature_records: Sequence[Mapping[str, Any]],
    labels_by_sample: Mapping[str, Mapping[str, Any]],
    split_by_sample: Mapping[str, str],
    *, target: str,
    allow_synthetic_fixture: bool = False,
) -> list[dict[str, Any]]:
    """Create contract records from adapter output, independent labels and frozen splits."""
    _fail(bool(feature_records), "at least one feature record is required")
    sample_ids = [str(row.get("sample_id")) for row in feature_records]
    _fail(len(sample_ids) == len(set(sample_ids)), "feature sample IDs must be unique")
    _fail(set(sample_ids) == set(labels_by_sample) == set(split_by_sample),
          "features, independent labels, and split assignments must have identical sample IDs")
    outputs = []
    for row in feature_records:
        validate_feature_record(row)
        sample_id = row["sample_id"]
        split = split_by_sample[sample_id]
        _fail(split in SPLITS, "unknown train/validation/test split")
        label = labels_by_sample[sample_id]
        _fail(isinstance(label, Mapping), "target label must be a structured object")
        _validate_label(target, label, allow_synthetic_fixture=allow_synthetic_fixture)
        values = row["feature_values"]
        mask = {name: values[name] is not None for name in FEATURE_ORDER}
        modalities = {
            modality: {
                "available": row["modalities"][modality]["status"] == "available",
                "quality_accepted": row["modalities"][modality]["status"] == "available"
                and row["modalities"][modality]["coverage"] > 0,
                "coverage": row["modalities"][modality]["coverage"],
                "missingness": row["modalities"][modality]["missingness"],
                "status": row["modalities"][modality]["status"],
                "reason": row["modalities"][modality]["reason"],
            }
            for modality in MODALITY_ORDER
        }
        outputs.append({
            "schema_version": MODEL_INPUT_SCHEMA_VERSION,
            "target": target,
            "sample_id": sample_id,
            "participant_id": row["participant_id"],
            "session_id": row["session_id"],
            "split": split,
            "feature_order": list(FEATURE_ORDER),
            "feature_values": {name: values[name] for name in FEATURE_ORDER},
            "feature_available_mask": mask,
            "feature_missingness": {name: not present for name, present in mask.items()},
            "modality_quality": modalities,
            "label": dict(label),
            "provenance": {
                "media_feature_schema_version": MEDIA_FEATURE_SCHEMA_VERSION,
                "model_input_schema_version": MODEL_INPUT_SCHEMA_VERSION,
                "extractors": row["provenance"],
            },
        })
    check_split_leakage(outputs, split_by_sample)
    for record in outputs:
        validate_model_input_record(record, allow_synthetic_fixture=allow_synthetic_fixture)
    return outputs


def validate_model_input_record(record: Mapping[str, Any], *, allow_synthetic_fixture: bool = False) -> None:
    """Fail closed on ordering, masks, missingness, provenance, and target metadata."""
    _fail(isinstance(record, Mapping) and set(record) == {
        "schema_version", "target", "sample_id", "participant_id", "session_id", "split",
        "feature_order", "feature_values", "feature_available_mask", "feature_missingness",
        "modality_quality", "label", "provenance",
    }, "model-input record has missing or unsupported fields")
    _fail(record["schema_version"] == MODEL_INPUT_SCHEMA_VERSION, "unsupported model-input schema")
    _fail(record["feature_order"] == list(FEATURE_ORDER), "model feature order differs from schema")
    _fail(record["split"] in SPLITS, "unknown dataset split")
    _fail(set(record["feature_values"]) == set(FEATURE_ORDER), "feature vector shape differs from schema")
    _fail(set(record["feature_available_mask"]) == set(FEATURE_ORDER)
          and set(record["feature_missingness"]) == set(FEATURE_ORDER), "feature masks differ from schema")
    for name in FEATURE_ORDER:
        value = record["feature_values"][name]
        present = record["feature_available_mask"][name]
        _fail(isinstance(present, bool) and record["feature_missingness"][name] is (not present),
              "availability mask and explicit missingness disagree")
        _fail((value is not None) == present and (value is None or
              isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)),
              "feature value and availability mask disagree")
    _fail(set(record["modality_quality"]) == set(MODALITY_ORDER), "all modality quality masks are required")
    for modality, state in record["modality_quality"].items():
        _fail(set(state) == {"available", "quality_accepted", "coverage", "missingness", "status", "reason"}
              and isinstance(state["available"], bool) and isinstance(state["quality_accepted"], bool)
              and isinstance(state["coverage"], (int, float)) and math.isfinite(state["coverage"])
              and 0 <= state["coverage"] <= 1 and isinstance(state["missingness"], (int, float))
              and math.isclose(state["missingness"], 1 - state["coverage"], abs_tol=1e-12),
              f"invalid {modality} quality/coverage mask")
        _fail(state["quality_accepted"] == (state["available"] and state["coverage"] > 0),
              f"{modality} availability and quality acceptance disagree")
        _fail(all(record["feature_available_mask"][name] is False for name in FEATURE_ORDER
                  if name.startswith(modality + ".")) if not state["quality_accepted"] else True,
              f"rejected {modality} features cannot be marked available")
    _fail(record["provenance"].get("media_feature_schema_version") == MEDIA_FEATURE_SCHEMA_VERSION
          and record["provenance"].get("model_input_schema_version") == MODEL_INPUT_SCHEMA_VERSION,
          "extractor/model-input schema provenance is required")
    _validate_label(record["target"], record["label"], allow_synthetic_fixture=allow_synthetic_fixture)


def validate_model_input_dataset(records: Sequence[Mapping[str, Any]], *, allow_synthetic_fixture: bool = False) -> None:
    """Validate records and participant/session split isolation before fitting."""
    _fail(bool(records), "model-input dataset must not be empty")
    sample_ids = []
    split_map = {}
    for row in records:
        validate_model_input_record(row, allow_synthetic_fixture=allow_synthetic_fixture)
        sample_ids.append(row["sample_id"])
        split_map[row["sample_id"]] = row["split"]
    _fail(len(sample_ids) == len(set(sample_ids)), "model-input sample IDs must be unique")
    check_split_leakage(records, split_map)
