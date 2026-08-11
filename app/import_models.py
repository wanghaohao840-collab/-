from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal, Sequence


ImportStatus = Literal[
    "queued",
    "running",
    "retry_wait",
    "pause_requested",
    "paused",
    "cancel_requested",
    "cancelled",
    "succeeded",
    "failed",
]
BatchLifecycleState = Literal["active", "deleting"]
ImportStage = Literal[
    "queued",
    "staged",
    "parsing",
    "chunking",
    "embedding",
    "persisting",
    "committing",
    "paused",
    "cancelled",
    "succeeded",
    "failed",
]
ProgressCallback = Callable[[str, int, int, str], None]


@dataclass(frozen=True)
class ImportLimits:
    max_files: int = 20
    max_file_bytes: int = 100 * 1024 * 1024
    max_batch_bytes: int = 500 * 1024 * 1024


@dataclass(frozen=True)
class ImportTaskCreate:
    task_id: str
    batch_id: str
    user_id: str
    document_id: str
    original_name: str
    file_suffix: str
    size_bytes: int
    staged_relative_path: str


@dataclass(frozen=True)
class ImportTaskRecord:
    task_id: str
    batch_id: str
    user_id: str
    document_id: str
    original_name: str
    file_suffix: str
    size_bytes: int
    staged_relative_path: str
    status: ImportStatus
    stage: ImportStage
    progress: int
    total_attempt_count: int
    auto_retry_count: int
    manual_retry_count: int
    max_auto_retries: int
    next_attempt_at: str | None
    error_code: str | None
    error_summary: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None
    updated_at: str
    control_requested_at: str | None = None
    control_claimed_at: str | None = None


@dataclass(frozen=True)
class ImportBatchSummary:
    batch_id: str
    user_id: str
    created_at: str
    updated_at: str
    total: int
    queued: int
    running: int
    retry_wait: int
    succeeded: int
    failed: int
    tasks: tuple[ImportTaskRecord, ...]
    lifecycle_state: BatchLifecycleState = "active"
    paused: int = 0
    pause_requested: int = 0
    cancel_requested: int = 0
    cancelled: int = 0


@dataclass(frozen=True)
class ImportTaskEventRecord:
    event_id: int
    batch_id: str
    task_id: str
    user_id: str
    event_type: str
    status: ImportStatus
    stage: str
    message: str | None
    created_at: str


@dataclass(frozen=True)
class ImportHistoryFilters:
    statuses: tuple[ImportStatus, ...] = ()
    filename_query: str = ""
    created_from: str | None = None
    created_to: str | None = None
    batch_id: str | None = None


@dataclass(frozen=True)
class ImportHistoryPage:
    batches: tuple[ImportBatchSummary, ...]
    next_cursor: str | None


def validate_batch_sizes(sizes: Sequence[int], limits: ImportLimits) -> None:
    if not sizes:
        raise ValueError("at least one file is required")
    if len(sizes) > limits.max_files:
        raise ValueError(f"batch cannot contain more than {limits.max_files} files")
    if any(size < 0 or size > limits.max_file_bytes for size in sizes):
        raise ValueError(f"each file must be at most {limits.max_file_bytes} bytes")
    if sum(sizes) > limits.max_batch_bytes:
        raise ValueError(f"batch must be at most {limits.max_batch_bytes} bytes")
