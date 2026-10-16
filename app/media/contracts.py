from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class ModalityQuality(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    modality: Literal["video", "image", "audio", "reaction_task"]
    status: Literal["not_provided", "not_processed", "insufficient_quality", "available", "failed", "invalid_media"]
    coverage: float | None = None
    missingness: float | None = None
    provenance: str | None = None
    reason: str | None = None


class MediaObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"] = "1.0"
    modality: Literal["video", "image", "audio"]
    timestamp_ms: int | None
    quality: ModalityQuality
    features: dict[str, float | int | bool | None]


class FeatureEnvelope(BaseModel):
    schema_version: Literal["2.0"] = "2.0"
    modality: Literal["video", "image", "audio", "reaction_task"]
    presence: Literal["present", "missing"]
    extraction_status: Literal["not_provided", "available", "insufficient_quality", "failed", "invalid_media"]
    quality: ModalityQuality
    features: dict[str, float | int | bool | str | None | dict[str, str | int | float | bool]]
    provenance: dict[str, str] = Field(default_factory=dict)


class TargetOutput(BaseModel):
    status: Literal["unavailable", "abstained"] = "unavailable"
    value: None = None
    reason: str = "model_unavailable"


class VersionedTargetPrediction(BaseModel):
    """Additive V3 target output; the legacy V2 target map remains unchanged."""
    status: Literal["available", "abstained", "unavailable"]
    value: float | int | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence_calibrated: bool = False
    model_version: str | None = None
    quality_status: Literal["passed", "failed", "not_evaluated"]
    abstention_reason: str | None = None


class PredictionExtension(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    inference_status: Literal["disabled", "available", "abstained", "partial"]
    targets: dict[Literal["reference_sleepiness", "subjective_fatigue", "vigilance_impairment"], VersionedTargetPrediction]


class AnalysisResult(BaseModel):
    schema_version: Literal["2.0"] = "2.0"
    status: Literal["insufficient_evidence", "model_unavailable"]
    scan_id: str
    model_version: None = None
    model_invocation_status: Literal["not_invoked_no_validated_model"] = "not_invoked_no_validated_model"
    targets: dict[Literal["reference_sleepiness", "vigilance_impairment"], TargetOutput]
    prediction_extension: PredictionExtension
    subjective_reference: dict[str, str | int | bool] | None = None
    evidence: dict[str, object] | None = None
    drowsiness_indicator: dict[str, object] | None = None
    features: dict[str, FeatureEnvelope]
    quality: dict[str, str]
    message: str
