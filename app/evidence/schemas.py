from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

QualityScore = Annotated[float, Field(ge=0.0, le=1.0)]


class EvidenceOutcome(BaseModel):
    """Fail-closed output for unregistered or unvalidated targets."""

    model_config = ConfigDict(extra="forbid")
    status: Literal["insufficient_evidence"] = "insufficient_evidence"
    reason: str = Field(min_length=1, max_length=256)


class EvidenceAbstention(BaseModel):
    model_config = ConfigDict(extra="forbid")
    modality: Literal["video", "image", "audio", "reaction_task"] | None = None
    target: str | None = None
    reason: str = Field(min_length=1, max_length=256)


class ModalityEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["available", "insufficient_quality", "missing", "not_processed"]
    measurements: dict[str, float | int | bool | str | None] = Field(default_factory=dict)
    quality_scores: dict[str, QualityScore] = Field(default_factory=dict)
    missingness_flags: list[str] = Field(default_factory=list)


class EvidenceV1(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scan_id: str = Field(min_length=1, max_length=128)
    schema_version: Literal["1.0"] = "1.0"
    model_versions: dict[str, str | None] = Field(default_factory=dict)
    modalities: dict[Literal["video", "image", "audio", "reaction_task"], ModalityEvidence]
    quality_scores: dict[str, QualityScore] = Field(default_factory=dict)
    missingness_flags: list[str] = Field(default_factory=list)
    fatigue: EvidenceOutcome
    focus: EvidenceOutcome
    risk_states: dict[Literal["drowsiness", "fatigue", "distraction", "impairment"], EvidenceOutcome]
    abstentions: list[EvidenceAbstention] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


def unvalidated_outcomes(reason: str = "no_validated_classifier_registered") -> dict:
    """Return all requested human-state targets in the only safe default state."""
    outcome = EvidenceOutcome(reason=reason).model_dump(mode="json")
    return {
        "fatigue": dict(outcome),
        "focus": dict(outcome),
        "risk_states": {name: dict(outcome) for name in ("drowsiness", "fatigue", "distraction", "impairment")},
    }
