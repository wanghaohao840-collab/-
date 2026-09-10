from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar('T')


@dataclass(frozen=True)
class Page(Generic[T]):
    items: tuple[T, ...]
    next_cursor: str | None


class LearningError(Exception):
    """Learning-domain failure; public adapters map to safe messages."""


class LearningValidationError(LearningError):
    pass


class LearningNotFound(LearningError):
    pass


class LearningVersionConflict(LearningError):
    pass


class LearningIdempotencyConflict(LearningError):
    pass


class LearningDocumentDeleting(LearningError):
    pass


class LearningMigrationRequired(LearningError):
    pass


@dataclass(frozen=True)
class CreateLearningPlan:
    request_id: str
    document_id: str
    title: str
    days: int
    daily_minutes: int
    timezone: str


@dataclass(frozen=True)
class SetLearningTaskState:
    request_id: str
    expected_version: int
    completed: bool


@dataclass(frozen=True)
class LearningPlan:
    id: str
    document_id: str
    document_name: str
    title: str
    timezone: str
    start_date: str
    target_date: str
    daily_minutes: int
    status: str
    version: int
    created_at: str
    updated_at: str
    task_count: int
    completed_count: int


@dataclass(frozen=True)
class LearningTask:
    id: str
    plan_id: str
    document_id: str
    due_date: str
    phase: str
    title: str
    duration_minutes: int
    completed: bool
    completed_at: str | None
    version: int


@dataclass(frozen=True)
class LearningPlanMutation:
    plan: LearningPlan
    replayed: bool


@dataclass(frozen=True)
class LearningTaskMutation:
    task: LearningTask
    replayed: bool
