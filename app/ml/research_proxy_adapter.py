"""Explicit, offline-only Brightertime research model path.

This adapter is deliberately separate from the operational predictor registry.
It only loads bounded JSON research artifacts and never turns them into
employee-facing predictions.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping


TARGET_FILES = {
    "modified_kss_sleepiness": "modified_kss_sleepiness_ridge_v1.json",
    "brightertime_pvt_median_hit_rt": "brightertime_pvt_median_hit_rt_ridge_v1.json",
}
MAX_ARTIFACT_BYTES = 32 * 1024
FEATURE_SCHEMA = "wellar.brightertime-context-features/1.0"


class ResearchArtifactError(ValueError):
    pass


def _finite(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(float(value))


def _read_json(path: Path, max_bytes: int) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > max_bytes:
        raise ResearchArtifactError("research artifact unavailable or exceeds size limit")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ResearchArtifactError("research artifact is not valid JSON") from None
    if not isinstance(value, dict):
        raise ResearchArtifactError("research artifact root must be an object")
    return value


def _load_artifact(target: str, artifact_root: str | Path) -> tuple[dict[str, Any], str]:
    filename = TARGET_FILES.get(target)
    if filename is None:
        raise ResearchArtifactError("unsupported research target")
    root = Path(artifact_root).resolve(strict=True)
    manifest_path = root / "manifest.json"
    manifest = _read_json(manifest_path, MAX_ARTIFACT_BYTES)
    if manifest.get("artifact_class") != "research_only_not_operational":
        raise ResearchArtifactError("artifact manifest is not research-only")
    entries = manifest.get("artifacts")
    entry = next((row for row in entries if isinstance(row, dict) and row.get("file") == filename), None) if isinstance(entries, list) else None
    if entry is None or entry.get("target") != target:
        raise ResearchArtifactError("artifact is absent from the pinned research manifest")
    path = root / filename
    if path.resolve(strict=True).parent != root or path.is_symlink():
        raise ResearchArtifactError("artifact path is outside the pinned root")
    artifact = _read_json(path, MAX_ARTIFACT_BYTES)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if entry.get("sha256") != digest:
        raise ResearchArtifactError("research artifact SHA-256 verification failed")
    if artifact.get("schema_version") != "wellar.research-regression-artifact/1.0" or artifact.get("artifact_class") != "research_only" or artifact.get("target") != target or artifact.get("feature_schema") != FEATURE_SCHEMA:
        raise ResearchArtifactError("research artifact contract is incompatible")
    for name in ("means", "scales", "weights", "imputation_medians"):
        vector = artifact.get(name)
        if not isinstance(vector, list) or len(vector) != 3 or not all(_finite(item) for item in vector):
            raise ResearchArtifactError("research artifact coefficient shape is invalid")
    if any(value <= 0 for value in artifact["scales"]):
        raise ResearchArtifactError("research artifact scales are invalid")
    if not _finite(artifact.get("bias")) or not isinstance(artifact.get("model_version"), str):
        raise ResearchArtifactError("research artifact metadata is invalid")
    return artifact, digest


def predict_research_context(
    target: str,
    features: Mapping[str, Any],
    *,
    artifact_root: str | Path,
    research_mode: bool = False,
) -> dict[str, Any]:
    """Score a Brightertime-like record only when caller explicitly opts in."""
    if not research_mode:
        return {"target": target, "status": "abstained", "abstention_reason": "research_mode_not_explicitly_enabled", "value": None, "confidence": None}
    try:
        artifact, digest = _load_artifact(target, artifact_root)
    except (ResearchArtifactError, OSError, RuntimeError):
        return {"target": target, "status": "abstained", "abstention_reason": "research_artifact_unavailable_or_invalid", "value": None, "confidence": None}
    awake = features.get("timeawake_hours") if isinstance(features, Mapping) else None
    hour = features.get("dayhour") if isinstance(features, Mapping) else None
    if not _finite(awake) or not _finite(hour):
        return {"target": target, "status": "abstained", "abstention_reason": "required_research_context_feature_missing_or_nonfinite", "value": None, "confidence": None, "model_version": artifact["model_version"], "artifact_sha256": digest}
    if not 0 <= float(awake) <= 36 or not 0 <= float(hour) < 24:
        return {"target": target, "status": "abstained", "abstention_reason": "research_context_out_of_supported_range", "value": None, "confidence": None, "model_version": artifact["model_version"], "artifact_sha256": digest}
    x = [float(awake), math.sin(2 * math.pi * float(hour) / 24), math.cos(2 * math.pi * float(hour) / 24)]
    z = [(x[i] - artifact["means"][i]) / artifact["scales"][i] for i in range(3)]
    value = float(artifact["bias"] + sum(z[i] * artifact["weights"][i] for i in range(3)))
    limits = artifact.get("prediction_range")
    if not isinstance(limits, list) or len(limits) != 2 or not all(_finite(x) for x in limits) or not float(limits[0]) <= value <= float(limits[1]):
        return {"target": target, "status": "abstained", "abstention_reason": "research_prediction_outside_reference_range", "value": None, "confidence": None, "model_version": artifact["model_version"], "artifact_sha256": digest}
    passed = artifact.get("evaluation_gate") == "passed_research_candidate_criterion"
    return {
        "target": target,
        "target_semantics": artifact["target_semantics"],
        "status": "research_only_candidate" if passed else "research_only_not_promoted",
        "value": value,
        "units": artifact["units"],
        "model_version": artifact["model_version"],
        "artifact_sha256": digest,
        "confidence": None,
        "confidence_status": artifact["confidence_status"],
        "evaluation_gate": artifact["evaluation_gate"],
        "provenance": {"dataset_doi": artifact["source"]["doi"], "license": artifact["source"]["license"], "feature_schema": artifact["feature_schema"]},
        "operational_release": False,
    }


def stage1_model_abstentions(_feature_envelopes: Mapping[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """Return explicit non-inference statuses for current Stage 1 contracts."""
    return {
        "fatigue": {
            "status": "abstained",
            "value": None,
            "abstention_reason": "no_occupational_fatigue_reference_model; modified_KSS_sleepiness_proxy_is_a_distinct_target",
            "research_proxy_available": "modified_kss_sleepiness",
            "stage1_compatible": False,
        },
        "vigilance": {
            "status": "abstained",
            "value": None,
            "abstention_reason": "research_PVT_candidate_failed_predeclared_gate_and_requires_unavailable_timeawake_dayhour_context",
            "research_proxy_available": "brightertime_pvt_median_hit_rt",
            "stage1_compatible": False,
        },
    }
