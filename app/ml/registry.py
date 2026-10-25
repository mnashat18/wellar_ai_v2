"""Load only explicitly enabled, guarded, bounded JSON model releases."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.ml.artifact_guard import ModelActivationDenied
from app.ml.feature_mapping import FEATURE_MAPPING_VERSION, SUPPORTED_FEATURES
from app.ml.json_backend import load_json_backend
from app.ml.runtime import TARGETS, DisabledPredictor, Predictor, create_predictor

MAX_MANIFEST_BYTES = 64 * 1024


def _read_manifest(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_MANIFEST_BYTES:
        raise ModelActivationDenied("model release manifest is unavailable or exceeds bounds")

    def reject_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate manifest keys")
            result[key] = value
        return result

    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid JSON constant")),
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        raise ModelActivationDenied("model release manifest is invalid") from None
    if not isinstance(value, dict):
        raise ModelActivationDenied("model release manifest is invalid")
    return value


def load_predictors(*, enabled: bool = False, artifact_dir: str | Path | None = None) -> dict[str, Predictor]:
    """Return disabled target predictors by default; partial releases are supported.

    Release files use `<target>.model.json` and `<target>.manifest.json`. A
    manifest goes through `create_predictor`, which applies the existing
    operational artifact guard and digest check before invoking the trusted
    JSON backend loader. Missing target pairs remain disabled; half-present or
    malformed pairs fail worker/API startup closed.
    """
    if not enabled:
        return {target: DisabledPredictor() for target in sorted(TARGETS)}
    if artifact_dir is None:
        raise ModelActivationDenied("model artifact directory is required when inference is enabled")
    root = Path(artifact_dir)
    if root.is_symlink() or not root.is_dir():
        raise ModelActivationDenied("model artifact directory is unavailable")

    predictors: dict[str, Predictor] = {}
    for target in sorted(TARGETS):
        artifact = root / f"{target}.model.json"
        manifest_path = root / f"{target}.manifest.json"
        if not artifact.exists() and not manifest_path.exists():
            predictors[target] = DisabledPredictor()
            continue
        if artifact.is_symlink():
            raise ModelActivationDenied("model artifact is unavailable")
        manifest = _read_manifest(manifest_path)
        if manifest.get("target") != target or manifest.get("feature_mapping_version") != FEATURE_MAPPING_VERSION:
            raise ModelActivationDenied("model release target or feature mapping version is incompatible")
        predictor = create_predictor(
            enabled=True,
            manifest=manifest,
            artifact_path=artifact,
            backend_loader=load_json_backend,
        )
        feature_names = tuple(getattr(predictor, "feature_names", ()))
        if not feature_names or any(name not in SUPPORTED_FEATURES for name in feature_names):
            raise ModelActivationDenied("model release uses an unsupported feature schema")
        feature_modalities = {name.split(".", 1)[0] for name in feature_names}
        manifest_modalities = set(manifest["required_modalities"])
        if feature_modalities != manifest_modalities:
            raise ModelActivationDenied("model release modalities do not match its feature schema")
        predictors[target] = predictor
    if all(isinstance(predictor, DisabledPredictor) for predictor in predictors.values()):
        raise ModelActivationDenied("inference is enabled but no model release artifacts are present")
    return predictors
