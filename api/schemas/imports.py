from __future__ import annotations

from pydantic import BaseModel

from app.import_models import (
    ImportBatchSummary,
    ImportStage,
    ImportStatus,
    ImportTaskRecord,
)


class ImportCountsResponse(BaseModel):
    total: int
    queued: int
    running: int
    retry_wait: int
    succeeded: int
    failed: int
    cancelled: int


class ImportTaskResponse(BaseModel):
    task_id: str
    document_id: str
    original_name: str
    file_suffix: str
    size_bytes: int
    status: ImportStatus
    stage: ImportStage
    progress: int
    error_code: str | None
    error_summary: str | None
    cancel_requested_at: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None
    updated_at: str


class ImportBatchResponse(BaseModel):
    batch_id: str
    created_at: str
    updated_at: str
    counts: ImportCountsResponse
    tasks: list[ImportTaskResponse]


def _task_response(task: ImportTaskRecord) -> ImportTaskResponse:
    return ImportTaskResponse(
        task_id=task.task_id,
        document_id=task.document_id,
        original_name=task.original_name,
        file_suffix=task.file_suffix,
        size_bytes=task.size_bytes,
        status=task.status,
        stage=task.stage,
        progress=task.progress,
        error_code=task.error_code,
        error_summary=task.error_summary,
        cancel_requested_at=task.cancel_requested_at,
        created_at=task.created_at,
        started_at=task.started_at,
        finished_at=task.finished_at,
        updated_at=task.updated_at,
    )


def import_batch_response(summary: ImportBatchSummary) -> ImportBatchResponse:
    return ImportBatchResponse(
        batch_id=summary.batch_id,
        created_at=summary.created_at,
        updated_at=summary.updated_at,
        counts=ImportCountsResponse(
            total=summary.total,
            queued=summary.queued,
            running=summary.running,
            retry_wait=summary.retry_wait,
            succeeded=summary.succeeded,
            failed=summary.failed,
            cancelled=summary.cancelled,
        ),
        tasks=[_task_response(task) for task in summary.tasks],
    )
