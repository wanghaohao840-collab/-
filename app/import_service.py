from __future__ import annotations

import logging
import os
import uuid
from contextlib import ExitStack, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Callable, Iterable

from app.import_models import (
    ImportBatchSummary,
    ImportLimits,
    ImportTaskCreate,
)
from app.import_repository import ImportTaskRepository
from app.storage import UserStorage


class ImportTasksActiveError(RuntimeError):
    """Raised when a destructive operation conflicts with active imports."""


class ImportLimitError(ValueError):
    def __init__(self, code: str, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class ImportTaskNotCancellableError(RuntimeError):
    """Raised when durable commit arbitration has already completed."""


class ImportStagingCleanupError(RuntimeError):
    """Raised when exact pre-batch staging rollback remains incomplete."""

    code = "import_staging_cleanup_failed"
    status_code = 500

    def __init__(self) -> None:
        super().__init__("could not clean up staged import files")


class ImportBatchCommitConfirmationError(RuntimeError):
    """Raised when a failed create call cannot be reconciled with SQLite."""

    code = "import_batch_commit_confirmation_failed"
    status_code = 500

    def __init__(self) -> None:
        super().__init__("could not confirm import batch creation")


@dataclass(frozen=True)
class ImportUpload:
    original_name: str
    stream: BinaryIO


@dataclass(frozen=True)
class _PathImport:
    source: Path
    original_name: str


@dataclass(frozen=True)
class _PendingImport:
    upload: ImportUpload
    original_name: str
    suffix: str
    task_id: str
    document_id: str


logger = logging.getLogger(__name__)
_STAGING_CHUNK_BYTES = 1024 * 1024


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
        session = self._session(session_token)
        paths = [self._inspect_path(item) for item in (files or [])]
        self._validate_file_count(len(paths))
        with ExitStack() as stack:
            try:
                uploads = [
                    ImportUpload(
                        item.original_name,
                        stack.enter_context(item.source.open("rb")),
                    )
                    for item in paths
                ]
            except OSError:
                raise ValueError("could not stage uploaded files") from None
            return self._submit_uploads(session, uploads, progress=progress)

    def submit_uploads(
        self,
        session_token: str,
        uploads: Iterable[ImportUpload],
        progress: Callable[..., Any] | None = None,
    ) -> ImportBatchSummary:
        return self._submit_uploads(
            self._session(session_token),
            list(uploads or []),
            progress=progress,
        )

    def _submit_uploads(
        self,
        session: Any,
        uploads: Iterable[ImportUpload],
        *,
        progress: Callable[..., Any] | None,
    ) -> ImportBatchSummary:
        user_id = str(session.user_id)
        upload_list = list(uploads)
        self._validate_file_count(len(upload_list))
        pending = [self._inspect_upload(upload) for upload in upload_list]
        batch_id = str(uuid.uuid4())
        owned_paths: list[Path] = []
        creates: list[ImportTaskCreate] = []
        batch_bytes = 0

        with self._runtime_lock(session):
            try:
                rollback_marker = self.storage.write_import_rollback_journal(
                    user_id,
                    batch_id,
                    [(item.task_id, item.suffix) for item in pending],
                )
            except Exception:
                raise ValueError("could not stage uploaded files") from None
            try:
                for index, item in enumerate(pending, start=1):
                    target = self.storage.staged_import_path(
                        user_id, batch_id, item.task_id, item.suffix
                    )
                    partial = self.storage.partial_staged_import_path(
                        user_id, batch_id, item.task_id, item.suffix
                    )
                    owned_paths.extend((partial, target))
                    file_bytes, batch_bytes = self._stage_stream(
                        item.upload.stream,
                        partial,
                        target,
                        batch_bytes,
                    )
                    creates.append(
                        ImportTaskCreate(
                            task_id=item.task_id,
                            batch_id=batch_id,
                            user_id=user_id,
                            document_id=item.document_id,
                            original_name=item.original_name,
                            file_suffix=item.suffix,
                            size_bytes=file_bytes,
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
            except ImportLimitError:
                self._rollback_staging_or_raise(owned_paths, rollback_marker)
                raise
            except Exception:
                self._rollback_staging_or_raise(owned_paths, rollback_marker)
                raise ValueError("could not stage uploaded files") from None

            try:
                summary = self.repository.create_batch(user_id, creates)
            except Exception:
                try:
                    persisted = self.repository.get_batch(user_id, batch_id)
                except Exception:
                    # SQLite reality is unknown.  Preserve the exact marker
                    # and staged files so startup reconciliation can decide
                    # from a later authoritative read.
                    raise ImportBatchCommitConfirmationError() from None
                if persisted is None:
                    self._rollback_staging_or_raise(owned_paths, rollback_marker)
                    raise
                summary = persisted
            self._remove_committed_rollback_marker(rollback_marker)

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
            if self.repository.get_batch(user_id, batch_id) is None:
                raise KeyError("import batch was not found")
            changed = self.repository.retry_failed_in_batch(user_id, batch_id)
            summary = self.repository.get_batch(user_id, batch_id)
        if changed:
            self.worker_pool.notify()
        return summary

    def cancel_task(
        self,
        session_token: str,
        batch_id: str,
        task_id: str,
    ) -> ImportBatchSummary:
        session = self._session(session_token)
        user_id = str(session.user_id)
        decision = self.repository.request_cancel(user_id, batch_id, task_id)
        if decision.outcome == "not_cancellable":
            raise ImportTaskNotCancellableError("import task is committing")
        if decision.outcome == "cancelled":
            self._cleanup_cancelled_staging(decision.task)
        summary = self.repository.get_batch(user_id, batch_id)
        if summary is None:  # pragma: no cover - guarded by request_cancel
            raise KeyError("import batch was not found")
        self.worker_pool.notify()
        return summary

    def has_active_tasks(self, user_id: str) -> bool:
        return self.repository.has_active_tasks(user_id)

    def has_active_task_for_document(self, user_id: str, document_id: str) -> bool:
        return self.repository.has_active_task_for_document(user_id, document_id)

    def _inspect_path(self, value: Any) -> _PathImport:
        raw_path = getattr(value, "name", value)
        source = Path(str(raw_path))
        if not source.is_file():
            raise ValueError("Uploaded document is not available")
        self.storage.validate_suffix(source.suffix)
        return _PathImport(source=source, original_name=source.name)

    def _inspect_upload(self, upload: ImportUpload) -> _PendingImport:
        if not isinstance(upload, ImportUpload):
            raise ValueError("Uploaded document is invalid")
        raw_name = str(upload.original_name or "").replace("\\", "/")
        original_name = Path(raw_name).name
        if (
            not original_name
            or original_name in {".", ".."}
            or "\x00" in original_name
            or not hasattr(upload.stream, "read")
        ):
            raise ValueError("Uploaded document name is invalid")
        suffix = self.storage.validate_suffix(Path(original_name).suffix)
        return _PendingImport(
            upload=upload,
            original_name=original_name,
            suffix=suffix,
            task_id=str(uuid.uuid4()),
            document_id=str(uuid.uuid4()),
        )

    def _validate_file_count(self, count: int) -> None:
        if count == 0:
            raise ImportLimitError(
                "import_no_files",
                "at least one file is required",
                status_code=400,
            )
        if count > self.limits.max_files:
            raise ImportLimitError(
                "import_too_many_files",
                f"batch cannot contain more than {self.limits.max_files} files",
                status_code=413,
            )

    def _stage_stream(
        self,
        stream: BinaryIO,
        partial: Path,
        target: Path,
        batch_bytes: int,
    ) -> tuple[int, int]:
        file_bytes = 0
        with partial.open("xb") as staged:
            while True:
                chunk = stream.read(_STAGING_CHUNK_BYTES)
                if not chunk:
                    break
                if not isinstance(chunk, (bytes, bytearray, memoryview)):
                    raise ValueError("Uploaded document stream must be binary")
                chunk_size = len(chunk)
                file_bytes += chunk_size
                batch_bytes += chunk_size
                if file_bytes > self.limits.max_file_bytes:
                    raise ImportLimitError(
                        "import_file_too_large",
                        f"each file must be at most {self.limits.max_file_bytes} bytes",
                        status_code=413,
                    )
                if batch_bytes > self.limits.max_batch_bytes:
                    raise ImportLimitError(
                        "import_batch_too_large",
                        f"batch must be at most {self.limits.max_batch_bytes} bytes",
                        status_code=413,
                    )
                staged.write(chunk)
            staged.flush()
            os.fsync(staged.fileno())
        os.replace(partial, target)
        return file_bytes, batch_bytes

    @staticmethod
    def _cleanup_owned_staging(paths: Iterable[Path]) -> bool:
        parents: set[Path] = set()
        complete = True
        for path in reversed(list(paths)):
            parents.add(path.parent)
            removed = False
            for _attempt in range(2):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    continue
                removed = True
                break
            if not removed:
                try:
                    absent = not path.exists()
                except OSError:
                    absent = False
                if not absent:
                    complete = False
                    logger.warning("could not remove import staging file")
        for parent in parents:
            try:
                parent.rmdir()
            except OSError:
                pass
        return complete

    def _rollback_staging_or_raise(
        self, paths: Iterable[Path], marker: Path
    ) -> None:
        complete = self._cleanup_owned_staging(paths)
        if complete:
            complete = self._unlink_with_retry(marker)
        if complete:
            try:
                marker.parent.rmdir()
            except OSError:
                pass
            return
        raise ImportStagingCleanupError() from None

    @staticmethod
    def _unlink_with_retry(path: Path) -> bool:
        for _attempt in range(2):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                continue
            return True
        try:
            return not path.exists()
        except OSError:
            return False

    def _remove_committed_rollback_marker(self, marker: Path) -> None:
        if not self._unlink_with_retry(marker):
            logger.warning("could not remove committed import rollback marker")

    def _cleanup_cancelled_staging(self, task: Any) -> None:
        try:
            self.storage.remove_staged_import_file(
                task.user_id,
                task.batch_id,
                task.task_id,
                task.file_suffix,
                task.staged_relative_path,
            )
        except (OSError, ValueError):
            logger.warning("could not remove cancelled import staging file")

    def _user_id(self, session_token: str) -> str:
        return str(self._session(session_token).user_id)

    def _session(self, session_token: str) -> Any:
        return self.session_registry.get_session(session_token)

    @staticmethod
    def _runtime_lock(session: Any):
        runtime = getattr(session, "runtime", None)
        lock = getattr(runtime, "lock", None)
        return lock if lock is not None else nullcontext()
