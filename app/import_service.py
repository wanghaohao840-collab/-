from __future__ import annotations

import shutil
import uuid
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from app.import_models import (
    ImportBatchSummary,
    ImportHistoryFilters,
    ImportHistoryPage,
    ImportLimits,
    ImportTaskCreate,
    validate_batch_sizes,
)
from app.import_maintenance import ImportHistoryMaintenance
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
        maintenance: ImportHistoryMaintenance | None = None,
    ) -> None:
        self.session_registry = session_registry
        self.repository = repository
        self.storage = storage
        self.worker_pool = worker_pool
        self.limits = limits
        self.maintenance = maintenance or ImportHistoryMaintenance(repository, storage)

    def submit_batch(
        self,
        session_token: str,
        files: Iterable[Any],
        progress: Callable[..., Any] | None = None,
    ) -> ImportBatchSummary:
        session = self._session(session_token)
        user_id = str(session.user_id)
        pending = [self._inspect_file(item) for item in (files or [])]
        validate_batch_sizes([item.size_bytes for item in pending], self.limits)

        batch_id = str(uuid.uuid4())
        batch_dir: Path | None = None
        creates: list[ImportTaskCreate] = []
        with self._runtime_lock(session):
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
        if summary is None or summary.lifecycle_state != "active":
            raise KeyError("import batch was not found")
        return summary

    def list_task_history(
        self,
        session_token: str,
        filters: ImportHistoryFilters | dict[str, Any] | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> ImportHistoryPage:
        parsed = self._parse_history_filters(filters)
        return self.repository.list_history(
            self._user_id(session_token), parsed, cursor, limit
        )

    def list_task_events(
        self, session_token: str, task_id: str, limit: int = 200
    ):
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("task_id is required")
        return self.repository.list_task_events(
            self._user_id(session_token), task_id, limit=limit
        )

    def delete_batch_history(self, session_token: str, batch_id: str) -> None:
        if not isinstance(batch_id, str) or not batch_id:
            raise ValueError("batch_id is required")
        session = self._session(session_token)
        user_id = str(session.user_id)
        with self._runtime_lock(session):
            summary = self.repository.mark_batch_deleting(user_id, batch_id)
        self.maintenance._finish_marked_batch(user_id, summary)

    def retry_task(
        self,
        session_token: str,
        task_id: str,
        expected_batch_id: str | None = None,
    ) -> ImportBatchSummary:
        session = self._session(session_token)
        user_id = str(session.user_id)
        with self._runtime_lock(session):
            if expected_batch_id is not None:
                selected = self.repository.get_task(user_id, task_id)
                if selected is None:
                    raise KeyError("import task was not found")
                if selected.batch_id != expected_batch_id:
                    raise KeyError("import task is not in the displayed batch")
            task = self.repository.retry_task(user_id, task_id)
            summary = self.repository.get_batch(user_id, task.batch_id)
        if summary is None:  # pragma: no cover - guarded by the task foreign key
            raise KeyError("import batch was not found")
        self.worker_pool.notify()
        return summary

    def retry_failed_in_batch(
        self, session_token: str, batch_id: str
    ) -> ImportBatchSummary:
        session = self._session(session_token)
        user_id = str(session.user_id)
        with self._runtime_lock(session):
            current = self.repository.get_batch(user_id, batch_id)
            if current is None or current.lifecycle_state != "active":
                raise KeyError("import batch was not found")
            changed = self.repository.retry_failed_in_batch(user_id, batch_id)
            summary = self.repository.get_batch(user_id, batch_id)
        if changed:
            self.worker_pool.notify()
        return summary

    def pause_task(
        self, session_token: str, task_id: str
    ) -> ImportBatchSummary:
        session = self._session(session_token)
        user_id = str(session.user_id)
        with self._runtime_lock(session):
            task = self.repository.request_pause(user_id, task_id)
            summary = self.repository.get_batch(user_id, task.batch_id)
        if summary is None:  # pragma: no cover - guarded by the task foreign key
            raise KeyError("import batch was not found")
        self.worker_pool.notify()
        return summary

    def resume_task(
        self, session_token: str, task_id: str
    ) -> ImportBatchSummary:
        session = self._session(session_token)
        user_id = str(session.user_id)
        with self._runtime_lock(session):
            task = self.repository.resume_task(user_id, task_id)
            summary = self.repository.get_batch(user_id, task.batch_id)
        if summary is None:  # pragma: no cover - guarded by the task foreign key
            raise KeyError("import batch was not found")
        self.worker_pool.notify()
        return summary

    def cancel_task(
        self, session_token: str, task_id: str
    ) -> ImportBatchSummary:
        session = self._session(session_token)
        user_id = str(session.user_id)
        with self._runtime_lock(session):
            task = self.repository.request_cancel(user_id, task_id)
            summary = self.repository.get_batch(user_id, task.batch_id)
        if summary is None:  # pragma: no cover - guarded by the task foreign key
            raise KeyError("import batch was not found")
        self.worker_pool.notify()
        return summary

    def pause_batch(
        self, session_token: str, batch_id: str
    ) -> ImportBatchSummary:
        session = self._session(session_token)
        user_id = str(session.user_id)
        with self._runtime_lock(session):
            before = self.repository.get_batch(user_id, batch_id)
            summary = self.repository.request_pause_batch(user_id, batch_id)
            changed = summary != before
        if changed:
            self.worker_pool.notify()
        return summary

    def resume_batch(
        self, session_token: str, batch_id: str
    ) -> ImportBatchSummary:
        session = self._session(session_token)
        user_id = str(session.user_id)
        with self._runtime_lock(session):
            before = self.repository.get_batch(user_id, batch_id)
            summary = self.repository.resume_batch(user_id, batch_id)
            changed = summary != before
        if changed:
            self.worker_pool.notify()
        return summary

    def cancel_batch(
        self, session_token: str, batch_id: str
    ) -> ImportBatchSummary:
        session = self._session(session_token)
        user_id = str(session.user_id)
        with self._runtime_lock(session):
            before = self.repository.get_batch(user_id, batch_id)
            summary = self.repository.request_cancel_batch(user_id, batch_id)
            changed = summary != before
        if changed:
            self.worker_pool.notify()
        return summary

    def has_active_tasks(self, user_id: str) -> bool:
        return self.repository.has_active_tasks(user_id)

    def has_active_task_for_document(self, user_id: str, document_id: str) -> bool:
        return self.repository.has_active_task_for_document(user_id, document_id)

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

    @staticmethod
    def _parse_history_filters(
        values: ImportHistoryFilters | dict[str, Any] | None,
    ) -> ImportHistoryFilters:
        if values is None:
            values = {}
        if isinstance(values, ImportHistoryFilters):
            values = {
                "statuses": values.statuses,
                "filename_query": values.filename_query,
                "created_from": values.created_from,
                "created_to": values.created_to,
                "batch_id": values.batch_id,
            }
        if not isinstance(values, dict):
            raise ValueError("history filters are invalid")
        allowed = {
            "statuses",
            "filename_query",
            "created_from",
            "created_to",
            "batch_id",
        }
        if set(values) - allowed:
            raise ValueError("history filters are invalid")

        raw_statuses = values.get("statuses", ())
        if raw_statuses is None:
            raw_statuses = ()
        elif isinstance(raw_statuses, str):
            raw_statuses = (raw_statuses,) if raw_statuses else ()
        elif isinstance(raw_statuses, (list, tuple)):
            raw_statuses = tuple(raw_statuses)
        else:
            raise ValueError("history status filter is invalid")
        if any(not isinstance(status, str) for status in raw_statuses):
            raise ValueError("history status filter is invalid")

        filename_query = values.get("filename_query", "")
        if filename_query is None:
            filename_query = ""
        if not isinstance(filename_query, str):
            raise ValueError("history filename filter is invalid")

        parsed_dates: dict[str, str | None] = {}
        for name in ("created_from", "created_to"):
            value = values.get(name)
            if value in (None, ""):
                parsed_dates[name] = None
            elif isinstance(value, str):
                parsed_dates[name] = value
            else:
                raise ValueError("history date filter is invalid")
        batch_id = values.get("batch_id")
        if batch_id == "":
            batch_id = None
        if batch_id is not None and not isinstance(batch_id, str):
            raise ValueError("history batch filter is invalid")
        return ImportHistoryFilters(
            statuses=tuple(raw_statuses),
            filename_query=filename_query,
            created_from=parsed_dates["created_from"],
            created_to=parsed_dates["created_to"],
            batch_id=batch_id,
        )

    def _user_id(self, session_token: str) -> str:
        return str(self._session(session_token).user_id)

    def _session(self, session_token: str) -> Any:
        return self.session_registry.get_session(session_token)

    @staticmethod
    def _runtime_lock(session: Any):
        runtime = getattr(session, "runtime", None)
        lock = getattr(runtime, "lock", None)
        return lock if lock is not None else nullcontext()
