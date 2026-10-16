"""Fail-closed, versioned adapter boundary for future operational predictors.

This module does not provide a model or load code from an artifact. A trusted,
versioned backend loader must be supplied by the application after the release
manifest and artifact digest pass validation. Activation is disabled by default.
"""
from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from app.ml.artifact_guard import ModelActivationDenied, require_operational_artifact

MODEL_INTERFACE_VERSION = "1.0"
TARGETS = frozenset({"reference_sleepiness", "subjective_fatigue", "vigilance_impairment"})
MODALITIES = frozenset({"video", "image", "audio", "reaction_task"})
_ABSTENTION_REASONS = frozenset({
    "multimodal_disagreement", "uncertainty_threshold", "out_of_distribution",
    "quality_failure", "model_abstained",
})


@dataclass(frozen=True)
class PredictionRequest:
    """Target and already-extracted values; contains no tenant or subject identifiers."""

    target: str
    features: Mapping[str, Mapping[str, Any]]
    modality_status: Mapping[str, str]
    missingness: float

    def validate(self) -> None:
        if self.target not in TARGETS:
            raise ValueError("unsupported prediction target")
        if not isinstance(self.features, Mapping) or not isinstance(self.modality_status, Mapping):
            raise ValueError("prediction features and modality status must be mappings")
        if isinstance(self.missingness, bool) or not isinstance(self.missingness, (int, float)) or not math.isfinite(self.missingness) or not 0 <= self.missingness <= 1:
            raise ValueError("missingness must be finite and between zero and one")
        for modality, values in self.features.items():
            if modality not in MODALITIES or not isinstance(values, Mapping):
                raise ValueError("prediction features contain an unsupported modality")
            for value in values.values():
                if value is None or isinstance(value, (str, bool)):
                    continue
                if not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError("prediction features must be finite scalar values or null")
        if any(modality not in MODALITIES or not isinstance(status, str) for modality, status in self.modality_status.items()):
            raise ValueError("prediction modality statuses are invalid")


@dataclass(frozen=True)
class PredictionResponse:
    schema_version: str
    target: str
    status: str
    value: float | None
    probability: float | None
    calibrated: bool
    model_version: str | None
    reason: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "target": self.target,
            "status": self.status,
            "value": self.value,
            "probability": self.probability,
            "calibrated": self.calibrated,
            "model_version": self.model_version,
            "reason": self.reason,
        }


class PredictionBackend(Protocol):
    interface_version: str
    model_version: str

    def predict(self, target: str, features: Mapping[str, Mapping[str, Any]]) -> Mapping[str, Any]: ...


class Predictor(Protocol):
    def predict(self, request: PredictionRequest) -> PredictionResponse: ...


class DisabledPredictor:
    """Default operational state: no model load and no prediction."""

    def predict(self, request: PredictionRequest) -> PredictionResponse:
        request.validate()
        return PredictionResponse(MODEL_INTERFACE_VERSION, request.target, "unavailable", None, None, False, None, "activation_disabled")


class _ActivatedPredictor:
    def __init__(self, backend: PredictionBackend, manifest: Mapping[str, Any]):
        self.backend = backend
        self.model_version = str(manifest["model_version"])
        self.target = str(manifest["target"])
        self.artifact_sha256 = str(manifest["artifact_sha256"])
        self.manifest = dict(manifest)
        self.required_modalities = tuple(manifest["required_modalities"])
        self.max_missingness = float(manifest["max_missingness"])
        self.manifest_calibrated = manifest["calibrated"]
        self.feature_names = tuple(getattr(backend, "feature_names", ()))

    def predict(self, request: PredictionRequest) -> PredictionResponse:
        request.validate()
        if request.target != self.target:
            return _abstained(request.target, self.model_version, "target_not_supported")
        if request.missingness > self.max_missingness:
            return _abstained(request.target, self.model_version, "quality_failure")
        if any(request.modality_status.get(modality) != "available" for modality in self.required_modalities):
            return _abstained(request.target, self.model_version, "quality_failure")
        try:
            raw = self.backend.predict(request.target, request.features)
            return _validate_backend_response(raw, request.target, self.model_version, self.manifest_calibrated)
        except Exception:
            # Do not leak model exceptions, feature values, or backend details to callers.
            return _abstained(request.target, self.model_version, "inference_failed")


def _abstained(target: str, model_version: str | None, reason: str) -> PredictionResponse:
    return PredictionResponse(MODEL_INTERFACE_VERSION, target, "abstained", None, None, False, model_version, reason)


def _finite_optional(value: Any, *, probability: bool = False) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("prediction output is not finite numeric data")
    number = float(value)
    if probability and not 0 <= number <= 1:
        raise ValueError("probability is outside [0, 1]")
    return number


def _validate_backend_response(raw: Mapping[str, Any], target: str, model_version: str, manifest_calibrated: bool) -> PredictionResponse:
    if not isinstance(raw, Mapping) or raw.get("status") not in {"available", "abstained"}:
        return _abstained(target, model_version, "invalid_model_response")
    if raw["status"] == "abstained":
        reason = raw.get("reason")
        return _abstained(target, model_version, reason if reason in _ABSTENTION_REASONS else "model_abstained")
    try:
        value = _finite_optional(raw.get("value"))
        probability = _finite_optional(raw.get("probability"), probability=True)
        calibrated = raw.get("calibrated", False)
        if not isinstance(calibrated, bool) or (calibrated and not manifest_calibrated):
            raise ValueError("calibration claim is not backed by the release manifest")
        if value is None and probability is None:
            raise ValueError("available prediction has no output")
        return PredictionResponse(MODEL_INTERFACE_VERSION, target, "available", value, probability, calibrated, model_version, None)
    except (TypeError, ValueError):
        return _abstained(target, model_version, "invalid_model_response")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_predictor(
    *,
    enabled: bool = False,
    manifest: Mapping[str, Any] | None = None,
    artifact_path: str | Path | None = None,
    backend_loader: Callable[[Path, Mapping[str, Any]], PredictionBackend] | None = None,
) -> Predictor:
    """Create a disabled predictor unless an explicitly enabled artifact is eligible.

    The loader is trusted application code selected by deployment configuration;
    artifact files are never imported or deserialized as executable code here.
    """
    if not enabled:
        return DisabledPredictor()
    require_operational_artifact(manifest or {})
    if artifact_path is None or backend_loader is None:
        raise ModelActivationDenied("artifact path and trusted backend loader are required")
    artifact = Path(artifact_path)
    digest = manifest.get("artifact_sha256")  # type: ignore[union-attr]
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
        raise ModelActivationDenied("artifact SHA-256 must be a 64-character hexadecimal digest")
    protocol_digest = manifest.get("protocol_sha256")
    if not isinstance(protocol_digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", protocol_digest):
        raise ModelActivationDenied("protocol SHA-256 must be a 64-character hexadecimal digest")
    if not artifact.is_file():
        raise ModelActivationDenied("model artifact is unavailable")
    try:
        actual_digest = _sha256(artifact)
    except OSError:
        raise ModelActivationDenied("model artifact cannot be read") from None
    if actual_digest.lower() != digest.lower():
        raise ModelActivationDenied("model artifact digest does not match release manifest")

    required = manifest.get("required_modalities")
    max_missingness = manifest.get("max_missingness")
    calibrated = manifest.get("calibrated")
    if manifest.get("model_interface_version") != MODEL_INTERFACE_VERSION:
        raise ModelActivationDenied("unsupported model interface version")
    if manifest.get("target") not in TARGETS:
        raise ModelActivationDenied("unsupported model target")
    if not isinstance(manifest.get("model_version"), str) or not manifest["model_version"].strip():
        raise ModelActivationDenied("model version is required")
    if not isinstance(required, list) or not required or any(m not in MODALITIES for m in required) or len(set(required)) != len(required):
        raise ModelActivationDenied("required modalities must be a nonempty unique supported list")
    if isinstance(max_missingness, bool) or not isinstance(max_missingness, (int, float)) or not math.isfinite(max_missingness) or not 0 <= max_missingness <= 1:
        raise ModelActivationDenied("maximum missingness must be between zero and one")
    if not isinstance(calibrated, bool):
        raise ModelActivationDenied("calibration status must be explicit")

    # The loader runs only after the release gate and file digest have passed.
    try:
        backend = backend_loader(artifact, dict(manifest))
    except Exception:
        raise ModelActivationDenied("trusted backend rejected the model artifact") from None
    if getattr(backend, "interface_version", None) != MODEL_INTERFACE_VERSION:
        raise ModelActivationDenied("backend interface version does not match release manifest")
    if getattr(backend, "model_version", None) != manifest["model_version"] or not callable(getattr(backend, "predict", None)):
        raise ModelActivationDenied("backend version or prediction interface does not match release manifest")
    return _ActivatedPredictor(backend, manifest)
