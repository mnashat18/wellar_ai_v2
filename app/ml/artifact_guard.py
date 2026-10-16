"""Fail-closed gate for any future operational model artifact loader."""
from __future__ import annotations

from collections.abc import Mapping


class ModelActivationDenied(RuntimeError):
    pass


def require_operational_artifact(manifest: Mapping[str, object]) -> None:
    """Reject research/evaluation artifacts; require an independently validated release manifest."""
    if not isinstance(manifest, Mapping):
        raise ModelActivationDenied("model artifact manifest is required")
    if manifest.get("artifact_class") != "validated_operational":
        raise ModelActivationDenied("research-only artifacts cannot be loaded by operational services")
    if manifest.get("research_only") is not False or manifest.get("validation_status") != "validated":
        raise ModelActivationDenied("operational model validation is missing or incomplete")
    required = ("artifact_sha256", "target", "protocol_sha256", "held_out_evaluation_id", "approval_reference")
    if any(not isinstance(manifest.get(key), str) or not manifest[key].strip() for key in required):
        raise ModelActivationDenied("operational model release evidence is incomplete")

