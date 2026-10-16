from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class ClientMeasurements(BaseModel):
    model_config = ConfigDict(extra="forbid")
    short_clip_eye_closure_fraction: float = Field(ge=0, le=1)
    blink_rate_per_minute: float = Field(ge=0, le=120)
    mean_eye_closure_seconds: float = Field(ge=0, le=5)
    max_eye_closure_seconds: float = Field(ge=0, le=10)
    face_detection_ratio: float = Field(ge=0, le=1)
    mean_luminance: float = Field(ge=0, le=255)
    mean_blur_variance: float = Field(ge=0, le=1_000_000)
    temporal_coverage: float = Field(ge=0, le=1)
    yaw_proxy: float = Field(ge=-5, le=5)
    pitch_proxy: float = Field(ge=-5, le=5)
    audio_quality_status: Literal["available", "insufficient_quality", "failed", "not_provided"] | None = None
