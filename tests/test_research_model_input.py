"""Synthetic software-contract tests; no research labels or accuracy evidence."""
import copy

import pytest

from research.evaluation_v1 import ResearchDataError
from research.media_feature_adapter import build_feature_record
from research.model_input_v1 import (
    FEATURE_ORDER, MODEL_INPUT_SCHEMA_VERSION, build_model_input_records,
    validate_model_input_dataset,
)


def _feature(index: int, participant: str | None = None):
    return build_feature_record(
        participant_id=f"p_{(participant or format(index, '016x'))}",
        session_id=f"s_{index:016x}", sample_id=f"sample_{index:016x}",
        extractor_outputs={},
    )


def _label(index: int):
    return {"status": "observed", "value": float(index % 3), "definition_id": "synthetic-reference-v1",
            "source_reference": "synthetic-test-only", "measured_at_utc": "2026-01-01T00:00:00Z",
            "approval_status": "synthetic_test_only"}


def test_synthetic_adapter_to_versioned_model_input_contract():
    features = [_feature(i) for i in range(6)]
    labels = {row["sample_id"]: _label(i) for i, row in enumerate(features)}
    splits = {row["sample_id"]: ("train", "validation", "test")[i % 3] for i, row in enumerate(features)}
    records = build_model_input_records(features, labels, splits, target="reference_sleepiness",
                                        allow_synthetic_fixture=True)
    validate_model_input_dataset(records, allow_synthetic_fixture=True)
    assert records[0]["schema_version"] == MODEL_INPUT_SCHEMA_VERSION
    assert records[0]["feature_order"] == list(FEATURE_ORDER)
    assert records[0]["feature_available_mask"] == {name: False for name in FEATURE_ORDER}
    assert all(records[0]["feature_missingness"].values())
    assert records[0]["modality_quality"]["image"]["available"] is False
    assert "participant_id" not in records[0]["feature_values"]
    assert "session_id" not in records[0]["feature_values"]


def test_model_input_rejects_split_leakage_and_mask_inconsistency():
    features = [_feature(i) for i in range(6)]
    labels = {row["sample_id"]: _label(i) for i, row in enumerate(features)}
    splits = {row["sample_id"]: ("train", "validation", "test")[i % 3] for i, row in enumerate(features)}
    with pytest.raises(ResearchDataError, match="participant_id leakage"):
        shared = [_feature(i, participant="a" * 16) for i in range(3)]
        build_model_input_records(shared, {r["sample_id"]: _label(i) for i, r in enumerate(shared)},
                                  {r["sample_id"]: ("train", "test", "validation")[i] for i, r in enumerate(shared)},
                                  target="reference_sleepiness", allow_synthetic_fixture=True)
    record = build_model_input_records(features, labels, splits, target="reference_sleepiness",
                                       allow_synthetic_fixture=True)[0]
    record = copy.deepcopy(record)
    record["feature_available_mask"][FEATURE_ORDER[0]] = True
    with pytest.raises(ResearchDataError, match="mask"):
        validate_model_input_dataset([record], allow_synthetic_fixture=True)


def test_unapproved_fatigue_target_is_rejected():
    row = _feature(1)
    fatigue = {**_label(1), "approval_status": "approved", "approval_reference": "not-authoritative",
               "instrument_id": "scale-x", "subscale": "total", "scoring_version": "1"}
    with pytest.raises(ResearchDataError, match="not scientifically approved"):
        build_model_input_records([row], {row["sample_id"]: fatigue}, {row["sample_id"]: "train"},
                                  target="subjective_fatigue")
