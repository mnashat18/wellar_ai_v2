from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
import re


class ReactionTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = "1.0"
    task_type: str = Field(pattern=r"^(pvt|simple_reaction)$")
    trials: list["ReactionTrial"] = Field(min_length=1, max_length=100)
    timing_metadata: dict[str, str | int | float | bool] = Field(default_factory=dict, max_length=16)

    @model_validator(mode="after")
    def ordered_trials(self):
        indexes=[trial.trial_index for trial in self.trials]
        stimuli=[trial.stimulus_timestamp_ms for trial in self.trials]
        if len(set(indexes))!=len(indexes): raise ValueError("reaction trial indexes must be unique")
        if stimuli!=sorted(stimuli) or len(set(stimuli))!=len(stimuli): raise ValueError("stimulus timestamps must be strictly increasing")
        return self


class ReactionTrial(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trial_index: int = Field(ge=0, le=99)
    stimulus_timestamp_ms: int = Field(ge=0, le=86_400_000)
    # Earlier timestamps are retained as false starts; never silently discard them.
    response_timestamp_ms: int | None = Field(default=None, ge=0, le=86_400_000)


class ScanSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = "1.0"
    media_refs: list[str] = Field(default_factory=list, max_length=8)
    reaction_task: ReactionTask | None = None
    subjective_fatigue: "SubjectiveFatigue | None" = None
    consent: "MediaConsent | None" = None

    @model_validator(mode="after")
    def require_consent_for_media(self):
        if (self.media_refs or self.reaction_task is not None) and (self.consent is None or not self.consent.media_processing):
            raise ValueError("media-processing consent is required for media and reaction_task inputs")
        if len(set(self.media_refs)) != len(self.media_refs):
            raise ValueError("duplicate media_refs are not allowed")
        return self

    @field_validator("media_refs")
    @classmethod
    def only_opaque_refs(cls, refs: list[str]) -> list[str]:
        for ref in refs:
            if not re.fullmatch(r"media://[A-Za-z0-9_-]{1,64}/[A-Fa-f0-9-]{16,128}", ref):
                raise ValueError("media_refs must be opaque media:// references")
        return refs


class MediaConsent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    media_processing: bool
    # Research/dataset use is a separate permission and is never implied by
    # permission to process a user's operational scan.
    research_use: bool = False


class SubjectiveFatigue(BaseModel):
    """Optional, unvalidated Wellar research item; kept separate from predictions."""
    model_config = ConfigDict(extra="forbid")
    schema_version: str = "1.0"
    instrument: str = "wellar_custom_fatigue_rating_0_10"
    score: int | None = Field(default=None, ge=0, le=10)
    collection_consent: bool = False

    @model_validator(mode="after")
    def enforce_collection_opt_in(self):
        if self.collection_consent and self.score is None:
            raise ValueError("a fatigue score is required after collection opt-in")
        if not self.collection_consent:
            # Discard any supplied value before it can be persisted in the queue.
            self.score = None
        return self


class ScanStatus(BaseModel):
    scan_id: str
    status: str
    created_at: str
    updated_at: str
    retryable: bool = False


class ScanResult(BaseModel):
    schema_version: str = "2.0"
    scan_id: str
    status: str
    model_version: str | None = None
    model_invocation_status: str = "not_invoked_no_validated_model"
    targets: dict[str, object] = Field(default_factory=dict)
    prediction_extension: dict[str, object] | None = None
    subjective_reference: dict[str, str | int | bool] | None = None
    evidence: dict[str, object] | None = None
    drowsiness_indicator: dict[str, object] | None = None
    quality: dict[str, object] = Field(default_factory=dict)
    features: dict[str, object] = Field(default_factory=dict)
    message: str
