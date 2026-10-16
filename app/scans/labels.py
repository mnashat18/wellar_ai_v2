from pydantic import BaseModel, ConfigDict, Field


class KSSLabelSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kss: int = Field(ge=1, le=9)
    fatigue: int | None = Field(default=None, ge=1, le=10)
    focus: int | None = Field(default=None, ge=1, le=5)
    shift: str | None = Field(default=None, min_length=1, max_length=64)
    time_of_day: str | None = Field(default=None, min_length=1, max_length=32)
    training_consent: bool
