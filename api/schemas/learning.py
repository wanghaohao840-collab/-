from pydantic import BaseModel, ConfigDict, Field, field_validator
from uuid import UUID


class LearningRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    request_id: str

    @field_validator('request_id')
    @classmethod
    def valid_request_id(cls, value):
        return str(UUID(value))


class PlanCreateRequest(LearningRequest):
    document_id: str = Field(min_length=1, max_length=256)
    title: str = Field(min_length=1, max_length=100)
    days: int = Field(strict=True, ge=1, le=365)
    daily_minutes: int = Field(strict=True, ge=5, le=480)
    timezone: str = Field(min_length=1, max_length=128)


class TaskStateRequest(LearningRequest):
    expected_version: int = Field(strict=True, ge=1)
    completed: bool = Field(strict=True)
