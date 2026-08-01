from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from app.import_models import (
    ImportBatchSummary,
    ImportLimits,
    ImportTaskCreate,
    validate_batch_sizes,
)
from app.import_repository import ImportTaskRepository
from app.storage import UserStorage


class ImportTasksActiveError(RuntimeError):
    """Raised when a destructive operation conflicts with active imports."""


@dataclass(frozen=True)
class _PendingImport:
    source: Path
    original_name: str
    suffix: str
    size_bytes: int
    task_id: str
    document_id: str


class ImportTaskService:
    """Authenticated boundary for durable batch-import operations."""

    def __init__(
        self,
        session_registry: Any,
        repository: ImportTaskRepository,
        storage: UserStorage,
        worker_pool: Any,
        limits: ImportLimits = ImportLimits(),
    ) -> None:
        self.session_registry = session_registry
        self.repository = repository
        self.storage = storage
        self.worker_pool = worker_pool
        self.limits = limits

    def submit_batch(
        self,
        session_token: str,
        files: Iterable[Any],
        progress: Callable[..., Any] | None = None,
    ) -> ImportBatchSummary:
        user_id = self._user_id(session_token)
        pending = [self._inspect_file(item) for item in (files or [])]
        validate_batch_sizes([item.size_bytes for item in pending], self.limits)

        batch_id = str(uuid.uuid4())
        batch_dir: Path | None = None
        creates: list[ImportTaskCreate] = []
        try:
            for index, item in enumerate(pending, start=1):
                target = self.storage.staged_import_path(
                    user_id,
                    batch_id,
                    item.task_id,
                    item.suffix,
                )
                batch_dir = target.parent
                shutil.copyfile(item.source, target)
                creates.append(
                    ImportTaskCreate(
                        task_id=item.task_id,
                        batch_id=batch_id,
                        user_id=user_id,
                        document_id=item.document_id,
                        original_name=item.original_name,
                        file_suffix=item.suffix,
                        size_bytes=item.size_bytes,
                        staged_relative_path=str(
                            target.relative_to(self.storage.user_paths(user_id).root)
                        ),
                    )
                )
                if progress is not None:
                    progress(
                        (index, len(pending)),
                        desc=f"Staging document {index} of {len(pending)}",
                    )
        except Exception:
            if batch_dir is not None:
                shutil.rmtree(batch_dir, ignore_errors=True)
            raise ValueError("could not stage uploaded files") from None

        try:
            summary = self.repository.create_batch(user_id, creates)
        except Exception:
            if batch_dir is not None:
                shutil.rmtree(batch_dir, ignore_errors=True)
            raise

        self.worker_pool.notify()
        return summary

    def list_batches(
        self, session_token: str, limit: int = 50
    ) -> list[ImportBatchSummary]:
        return self.repository.list_batches(self._user_id(session_token), limit=limit)

    def get_batch(self, session_token: str, batch_id: str) -> ImportBatchSummary:
        summary = self.repository.get_batch(self._user_id(session_token), batch_id)
        if summary is None:
            raise KeyError("import batch was not found")
        return summary

    def retry_task(self, session_token: str, task_id: str) -> ImportBatchSummary:
        user_id = self._user_id(session_token)
        task = self.repository.retry_task(user_id, task_id)
        summary = self.repository.get_batch(user_id, task.batch_id)
        if summary is None:  # pragma: no cover - guarded by the task foreign key
            raise KeyError("import batch was not found")
        self.worker_pool.notify()
        return summary

    def retry_failed_in_batch(
        self, session_token: str, batch_id: str
    ) -> ImportBatchSummary:
        user_id = self._user_id(session_token)
        if self.repository.get_batch(user_id, batch_id) is None:
            raise KeyError("import batch was not found")
        changed = self.repository.retry_failed_in_batch(user_id, batch_id)
        summary = self.repository.get_batch(user_id, batch_id)
        if changed:
            self.worker_pool.notify()
        return summary

    def has_active_tasks(self, user_id: str) -> bool:
        return self.repository.has_active_tasks(user_id)

    def _inspect_file(self, value: Any) -> _PendingImport:
        raw_path = getattr(value, "name", value)
        source = Path(str(raw_path))
        if not source.is_file():
            raise ValueError("Uploaded document is not available")
        suffix = self.storage.validate_suffix(source.suffix)
        try:
            size_bytes = source.stat().st_size
        except OSError as exc:
            raise ValueError("Uploaded document is not available") from exc
        return _PendingImport(
            source=source,
            original_name=source.name,
            suffix=suffix,
            size_bytes=size_bytes,
            task_id=str(uuid.uuid4()),
            document_id=str(uuid.uuid4()),
        )

    def _user_id(self, session_token: str) -> str:
        return str(self.session_registry.get_session(session_token).user_id)
