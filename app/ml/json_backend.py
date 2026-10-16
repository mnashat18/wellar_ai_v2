"""Resource-bounded inference for non-executable linear-softmax JSON artifacts."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from app.ml.runtime import MODEL_INTERFACE_VERSION

ARTIFACT_FORMAT = "wellar-linear-softmax/1.0"
MAX_ARTIFACT_BYTES = 1 * 1024 * 1024
MAX_FEATURES = 128
MAX_CLASSES = 32


class JsonLinearBackend:
    """Small fixed-model backend; it parses data only and never executes artifact content."""

    def __init__(self, artifact: Mapping[str, Any]):
        expected = {
            "format", "model_interface_version", "target", "model_version", "classes", "feature_names",
            "imputation_medians", "means", "scales", "weights", "bias", "temperature", "min_confidence",
            "max_feature_missingness", "calibrated", "calibration_method", "required_modalities", "metadata",
        }
        if not isinstance(artifact, Mapping) or set(artifact) != expected:
            raise ValueError("artifact fields do not match the supported model format")
        if artifact["format"] != ARTIFACT_FORMAT or artifact["model_interface_version"] != MODEL_INTERFACE_VERSION:
            raise ValueError("unsupported artifact or model interface version")
        if artifact["target"] not in {"reference_sleepiness", "subjective_fatigue", "vigilance_impairment",
                                      "eye_state_event", "mouth_opening_event"}:
            raise ValueError("unsupported artifact target")
        if artifact["calibration_method"] != "validation_temperature_scaling_grid_0.5_to_5.0":
            raise ValueError("unsupported calibration method")
        if not isinstance(artifact["target"], str) or not isinstance(artifact["model_version"], str) or not artifact["model_version"]:
            raise ValueError("artifact target and model version are required")
        classes = artifact["classes"]
        names = artifact["feature_names"]
        if not isinstance(classes, list) or not 2 <= len(classes) <= MAX_CLASSES or any(type(c) is not int for c in classes) or len(set(classes)) != len(classes):
            raise ValueError("artifact classes are invalid")
        if not isinstance(names, list) or not 1 <= len(names) <= MAX_FEATURES or any(not isinstance(n, str) for n in names) or len(set(names)) != len(names):
            raise ValueError("artifact feature names are invalid")
        allowed_modalities = {"video", "audio", "image", "reaction_task"}
        if any(n.count(".") != 1 or n.split(".", 1)[0] not in allowed_modalities or not n.split(".", 1)[1] for n in names):
            raise ValueError("artifact feature identifiers are invalid")
        count = len(names)
        self.medians = self._finite_vector(artifact["imputation_medians"], count)
        self.means = self._finite_vector(artifact["means"], count)
        self.scales = self._finite_vector(artifact["scales"], count)
        if np.any(self.scales <= 0):
            raise ValueError("artifact scales must be positive")
        self.weights = np.asarray(artifact["weights"], dtype=np.float64)
        self.bias = self._finite_vector(artifact["bias"], len(classes))
        if self.weights.shape != (len(classes), count) or not np.all(np.isfinite(self.weights)):
            raise ValueError("artifact weight dimensions are invalid")
        self.temperature = self._bounded(artifact["temperature"], 0.1, 10.0, "temperature")
        self.min_confidence = self._bounded(artifact["min_confidence"], 0.5, 1.0, "minimum confidence", upper_open=True)
        self.max_missingness = self._bounded(artifact["max_feature_missingness"], 0.0, 1.0, "maximum feature missingness", upper_open=True)
        if artifact["calibrated"] is not True:
            raise ValueError("calibration must be explicit for this backend")
        modalities = artifact["required_modalities"]
        if not isinstance(modalities, list) or not modalities or any(m not in allowed_modalities for m in modalities) or set(modalities) != {n.split(".", 1)[0] for n in names}:
            raise ValueError("required modality list is invalid")
        if not isinstance(artifact["metadata"], Mapping):
            raise ValueError("artifact metadata must be a JSON object")
        self.classes = tuple(classes)
        self.feature_names = tuple(names)
        self.required_modalities = tuple(modalities)
        self.interface_version = MODEL_INTERFACE_VERSION
        self.model_version = artifact["model_version"]
        self.target = artifact["target"]
        self.metadata = dict(artifact["metadata"]) if isinstance(artifact["metadata"], Mapping) else {}

    @staticmethod
    def _finite_vector(value: Any, length: int) -> np.ndarray:
        vector = np.asarray(value, dtype=np.float64)
        if vector.shape != (length,) or not np.all(np.isfinite(vector)):
            raise ValueError("artifact vector is malformed")
        return vector

    @staticmethod
    def _bounded(value: Any, low: float, high: float, name: str, *, upper_open: bool = False) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"artifact {name} is invalid")
        result = float(value)
        if result < low or result > high or (upper_open and result == high):
            raise ValueError(f"artifact {name} is out of bounds")
        return result

    @classmethod
    def from_file(cls, path: str | Path, manifest: Mapping[str, Any] | None = None) -> "JsonLinearBackend":
        artifact_path = Path(path)
        with artifact_path.open("rb") as source:
            encoded = source.read(MAX_ARTIFACT_BYTES + 1)
        if len(encoded) < 2 or len(encoded) > MAX_ARTIFACT_BYTES:
            raise ValueError("artifact size is outside safe bounds")

        def reject_duplicates(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate artifact keys are not permitted")
                result[key] = value
            return result

        value = json.loads(encoded.decode("utf-8"), object_pairs_hook=reject_duplicates,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite JSON constant")))
        backend = cls(value)
        if manifest is not None and (manifest.get("target") != backend.target or manifest.get("model_version") != backend.model_version):
            raise ValueError("artifact identity does not match release manifest")
        return backend

    def predict(self, target: str, features: Mapping[str, Mapping[str, Any]]) -> Mapping[str, Any]:
        if target != self.target:
            return {"status": "abstained", "reason": "model_abstained"}
        row = []
        missing = 0
        for identifier in self.feature_names:
            modality, name = identifier.split(".", 1)
            values = features.get(modality, {})
            value = values.get(name) if isinstance(values, Mapping) else None
            if value is None or isinstance(value, bool):
                row.append(float("nan"))
                missing += 1
            elif not isinstance(value, (int, float)) or not math.isfinite(value):
                return {"status": "abstained", "reason": "quality_failure"}
            else:
                row.append(float(value))
        if missing / len(row) > self.max_missingness:
            return {"status": "abstained", "reason": "quality_failure"}
        vector = np.asarray(row, dtype=np.float64)
        vector = np.where(np.isnan(vector), self.medians, vector)
        vector = np.clip((vector - self.means) / self.scales, -20.0, 20.0)
        logits = (self.weights @ vector + self.bias) / self.temperature
        logits -= np.max(logits)
        exponents = np.exp(np.clip(logits, -60.0, 60.0))
        probabilities = exponents / np.sum(exponents)
        index = int(np.argmax(probabilities))
        confidence = float(probabilities[index])
        if confidence < self.min_confidence:
            return {"status": "abstained", "reason": "uncertainty_threshold"}
        return {
            "status": "available",
            "value": self.classes[index],
            "probability": confidence,
            "calibrated": True,
        }


def load_json_backend(path: Path, manifest: Mapping[str, Any]) -> JsonLinearBackend:
    return JsonLinearBackend.from_file(path, manifest)
