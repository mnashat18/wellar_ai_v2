from typing import Literal
from pydantic import BaseModel


class TargetEstimate(BaseModel):
    target: Literal["reference_sleepiness", "vigilance_impairment"]
    status: Literal["available", "abstained", "unavailable"]
    value: float | int | None = None
    probability: float | None = None
    calibrated: bool = False
    model_version: str | None = None
    provenance: str = "research_only"


class ResearchInference(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    sleepiness: TargetEstimate
    vigilance: TargetEstimate
    decision_use: Literal["research_only", "disabled"] = "disabled"
