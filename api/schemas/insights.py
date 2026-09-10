from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ActivityDay(BaseModel):
    date: str
    documents: int = Field(ge=0)
    questions: int = Field(ge=0)
    notes: int = Field(ge=0)
    total: int = Field(ge=0)


class LearningStatsResponse(BaseModel):
    document_count: int = Field(ge=0)
    completed_question_count: int = Field(ge=0)
    note_count: int = Field(ge=0)
    report_count: int = Field(ge=0)
    active_days: int = Field(ge=0)
    window_days: int = Field(ge=7, le=90)
    activity: list[ActivityDay]


class RecentDocument(BaseModel):
    document_id: str
    name: str
    loaded_at: str | None


class RecentQuestion(BaseModel):
    question: str
    asked_at: str
    document_names: list[str]


class OverviewResponse(BaseModel):
    stats: LearningStatsResponse
    recent_documents: list[RecentDocument]
    recent_questions: list[RecentQuestion]


class LearningReportItem(BaseModel):
    id: str
    title: str
    created_at: str


class LearningReportResponse(LearningReportItem):
    content: str


class CreateReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
