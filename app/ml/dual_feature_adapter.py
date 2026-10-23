"""Versioned Stage 1 feature adapter for future, separately validated targets."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.ml.feature_mapping import FEATURE_MAPPING_VERSION, map_features


ADAPTER_SCHEMA_VERSION = "wellar.dual-predictor-input/1.0"
SUPPORTED_TARGETS = frozenset({"subjective_fatigue", "vigilance_impairment"})


def build_dual_predictor_input(
    target: str,
    envelopes: Mapping[str, Mapping[str, Any]],
    feature_names: Sequence[str],
) -> dict[str, Any]:
    """Select Stage 1 media features without invoking or approving a predictor.

    Direct reaction-task outputs and questionnaire references are excluded to
    prevent target leakage when training fatigue or vigilance predictors.
    """
    if target not in SUPPORTED_TARGETS:
        raise ValueError("unsupported dual-output model target")
    if any(not isinstance(name, str) or name.startswith("reaction_task.") for name in feature_names):
        raise ValueError("direct reaction-task measurements are not predictor inputs")
    mapped, modality_status, missingness = map_features(envelopes, feature_names)
    return {
        "schema_version": ADAPTER_SCHEMA_VERSION,
        "feature_mapping_version": FEATURE_MAPPING_VERSION,
        "target": target,
        "features": mapped,
        "modality_status": modality_status,
        "feature_missingness": missingness,
        "reference_data_excluded": True,
        "prediction_status": "not_invoked",
        "ai_validation_status": "not_validated",
        "release_status": "research_candidate_features_only",
    }
