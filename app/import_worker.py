from __future__ import annotations

import logging
import os
import queue
import re
import shutil
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from app.import_maintenance import ImportHistoryMaintenance
from app.import_models import ImportTaskRecord
from app.import_repository import ImportTaskRepository, InvalidImportTransition
from app.storage import UserStorage
from assistants.pdf_learning_assistant import PDFLearningAssistant
from hello_agents.memory.rag.contracts import ImportControlSignal
from hello_agents.memory.rag.errors import (
    RAGAuthenticationError,
    RAGCollectionError,
    RAGConfigError,
    RAGConnectionError,
    RAGDocumentTooLargeError,
    RAGEmbeddingError,
    RAGOperationError,
    sanitize_error_message,
)


logger = logging.getLogger(__name__)
_RETRY_DELAYS = (2, 10, 30)
_RUNNER_FAILURE_RETRY_DELAYS = (0.02, 0.05, 0.1)
_STOP = object()
_RETENTION_ENV = "IMPORT_TASK_RETENTION_DAYS"
_RETENTION_ERROR = "IMPORT_TASK_RETENTION_DAYS must be a non-negative integer"
_RETENTION_INTERVAL_SECONDS = 60 * 60
_SAFE_STRUCTURED_ERROR_CODES = {
    "document_invalid",
    "rag_connection",
    "rag_authentication",
    "rag_config",
    "rag_collection",
    "rag_document_too_large",
    "rag_embedding",
    "rag_collectionerror",
    "rag_documenttoolargeerror",
    "rag_embeddingerror",
    "rag_operation",
    "memory_import_event",
    "database_busy",
    "staged_cleanup_failed",
    "staged_file_missing",
    "process_interrupted",
    "pause_cleanup_failed",
    "cancel_cleanup_failed",
    "unexpected_error",
}
_STAGE_RANGES = {
    "parsing": (10, 25),
    "chunking": (25, 40),
    "embedding": (40, 80),
    "persisting": (80, 92),
    "committing": (92, 99),
}


def parse_import_task_retention_days(value: str | None = None) -> int:
    raw = os.environ.get(_RETENTION_ENV) if value is None else value
    if raw in (None, ""):
        return 0
    if not isinstance(raw, str) or re.fullmatch(r"[0-9]+", raw) is None:
        raise ValueError(_RETENTION_ERROR)
    try:
        return int(raw, 10)
    except ValueError as error:
        raise ValueError(_RETENTION_ERROR) from error


def classify_import_failure(error: BaseException) -> tuple[str, bool, str]:
    error_code = getattr(error, "error_code", None)
    retryable = getattr(error, "retryable", None)
    if error_code and retryable is not None:
        summary = sanitize_error_message(error)[:500] or error.__class__.__name__
        # Structured errors can originate in backend responses.  Persist only
        # known, stable codes so arbitrary values (for example
        # ``token=secret``) cannot become public task metadata.
        normalized_code = str(error_code).strip().lower()
        if normalized_code not in _SAFE_STRUCTURED_ERROR_CODES:
            normalized_code = "unexpected_error"
        return normalized_code, bool(retryable), summary
    if isinstance(error, (RAGConnectionError, TimeoutError, ConnectionError)):
        return "rag_connection", True, sanitize_error_message(error)[:500]
    if isinstance(error, RAGAuthenticationError):
        return "rag_authentication", False, "RAG authentication failed"
    if isinstance(error, RAGConfigError):
        return "rag_config", False, "RAG configuration is invalid"
    if isinstance(
        error,
        (RAGCollectionError, RAGDocumentTooLargeError, RAGEmbeddingError),
    ):
        name = error.__class__.__name__
        return name.lower(), False, name
    if isinstance(error, RAGOperationError):
        summary = sanitize_error_message(error)[:500]
        return "rag_operation", bool(getattr(error, "retryable", False)), summary
    if isinstance(error, sqlite3.OperationalError) and any(
        marker in str(error).lower() for marker in ("locked", "busy")
    ):
        return "database_busy", True, "Import database is temporarily busy"
    if isinstance(error, (FileNotFoundError, ValueError)):
        return "document_invalid", False, sanitize_error_message(error)[:500]
    return "unexpected_error", False, error.__class__.__name__


class ImportTaskRunner:
    def __init__(
        self,
        repository: ImportTaskRepository,
        runtime_registry: Any,
        storage: UserStorage,
        *,
        assistant_factory: Callable[..., Any] = PDFLearningAssistant,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.runtime_registry = runtime_registry
        self.storage = storage
        self.assistant_factory = assistant_factory
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def run(self, task: ImportTaskRecord) -> None:
        runtime = None
        assistant = None
        formal_path: Path | None = None
        temporary_path: Path | None = None
        staged_path: Path | None = None
        current = self.repository.get_task(task.user_id, task.task_id)
        if current is None or current.status in {
            "paused",
            "cancelled",
            "succeeded",
            "failed",
        }:
            return
        requested_action = self._control_action(current.status)
        try:
            staged_path = self._resolve_staged_path(task)
            if requested_action is not None:
                runtime = self.runtime_registry.acquire_background(task.user_id)
                formal_path = self.storage.document_path(
                    task.user_id, task.document_id, task.file_suffix
                )
                temporary_path = self.storage.temporary_document_path(
                    task.user_id, task.document_id, task.file_suffix
                )
                assistant = self.assistant_factory(
                    user_id=task.user_id,
                    runtime_dir=runtime.paths.root,
                    runtime=runtime,
                )
                self._finish_control(
                    task,
                    assistant,
                    staged_path,
                    temporary_path,
                    formal_path,
                )
                return

            if not staged_path.is_file():
                raise FileNotFoundError("Staged import file is missing")

            runtime = self.runtime_registry.acquire_background(task.user_id)
            assistant = self.assistant_factory(
                user_id=task.user_id,
                runtime_dir=runtime.paths.root,
                runtime=runtime,
            )
            control_checkpoint = self._control_checkpoint(task)
            control_checkpoint("staged")
            # Record that the durable staged copy is ready before any formal
            # document copy or parsing begins.  The progress callback starts
            # from this value and never reports a lower percentage.
            try:
                self.repository.update_progress(
                    task.user_id, task.task_id, "staged", 10, now=self._now_iso()
                )
            except InvalidImportTransition:
                control_checkpoint("staged")
                raise
            formal_path = self.storage.document_path(
                task.user_id, task.document_id, task.file_suffix
            )
            temporary_path = self.storage.temporary_document_path(
                task.user_id, task.document_id, task.file_suffix
            )
            shutil.copyfile(staged_path, temporary_path)
            temporary_path.replace(formal_path)

            result = assistant.load_document(
                str(formal_path),
                document_id=task.document_id,
                original_name=task.original_name,
                import_task_id=task.task_id,
                progress_callback=self._progress_callback(task),
                control_checkpoint=control_checkpoint,
            )
            if isinstance(result, str) and result.lstrip().startswith("❌"):
                raise ValueError(result)
            control_checkpoint("committing")
            try:
                self.repository.update_progress(
                    task.user_id,
                    task.task_id,
                    "committing",
                    99,
                    now=self._now_iso(),
                )
                self.repository.mark_succeeded(
                    task.user_id, task.task_id, now=self._now_iso()
                )
            except InvalidImportTransition:
                control_checkpoint("committing")
                raise
            try:
                self._cleanup_staged_file(staged_path)
            except OSError:
                # The durable task transition is committed before cleanup;
                # retaining a staged copy is safe and recoverable if deletion
                # fails, so do not downgrade a succeeded task.
                logger.warning(
                    "could not remove completed import staging file", exc_info=True
                )
            else:
                self._remove_empty_batch_dir(staged_path.parent)
        except ImportControlSignal:
            self._finish_control(
                task,
                assistant,
                staged_path,
                temporary_path,
                formal_path,
            )
        except Exception as error:
            current = self.repository.get_task(task.user_id, task.task_id)
            if current is None or current.status in {
                "paused",
                "cancelled",
                "succeeded",
                "failed",
            }:
                return
            current_action = self._control_action(current.status)
            if current_action is not None:
                self._finish_control(
                    task,
                    assistant,
                    staged_path,
                    temporary_path,
                    formal_path,
                )
                return
            self._remove_attempt_files(temporary_path, formal_path)
            error_code, retryable, summary = classify_import_failure(error)
            if retryable and task.auto_retry_count < task.max_auto_retries:
                delay = _RETRY_DELAYS[
                    min(task.auto_retry_count, len(_RETRY_DELAYS) - 1)
                ]
                next_attempt = self.clock() + timedelta(seconds=delay)
                try:
                    self.repository.mark_retry_wait(
                        task.user_id,
                        task.task_id,
                        next_attempt_at=_as_utc_iso(next_attempt),
                        error_code=error_code,
                        error_summary=summary,
                        now=self._now_iso(),
                    )
                except InvalidImportTransition:
                    if self._finish_control_after_transition_conflict(
                        task,
                        assistant,
                        staged_path,
                        temporary_path,
                        formal_path,
                    ):
                        return
                    raise
            else:
                try:
                    self.repository.mark_failed(
                        task.user_id,
                        task.task_id,
                        error_code,
                        summary,
                        now=self._now_iso(),
                    )
                except InvalidImportTransition:
                    if self._finish_control_after_transition_conflict(
                        task,
                        assistant,
                        staged_path,
                        temporary_path,
                        formal_path,
                    ):
                        return
                    raise
        finally:
            if assistant is not None:
                try:
                    assistant.close()
                except Exception:
                    logger.warning(
                        "background import assistant close failed", exc_info=True
                    )
            if runtime is not None:
                self.runtime_registry.release_background(task.user_id)

    def _finish_control(
        self,
        task: ImportTaskRecord,
        assistant: Any,
        staged_path: Path | None,
        temporary_path: Path | None,
        formal_path: Path | None,
    ) -> None:
        try:
            if assistant is None:
                raise RuntimeError("Import assistant is unavailable for cleanup")
            assistant.compensate_import(task.document_id, task.task_id)
            latest_action = self._current_control_action(task)
            if latest_action is None:
                return
            self._remove_control_attempt_files(temporary_path, formal_path)
            if latest_action == "cancel" and staged_path is not None:
                self._cleanup_staged_file(staged_path)
                self._remove_empty_batch_dir(staged_path.parent)
            terminal = (
                self.repository.mark_paused
                if latest_action == "pause"
                else self.repository.mark_cancelled
            )
            terminal(task.user_id, task.task_id, now=self._now_iso())
        except Exception as error:
            self._remove_attempt_files(temporary_path, formal_path)
            failure_action = self._current_control_action(task)
            if failure_action is None:
                return
            self._mark_control_failed(task, failure_action, error)

    def _current_control_action(self, task: ImportTaskRecord) -> str | None:
        current = self.repository.get_task(task.user_id, task.task_id)
        if current is None:
            return None
        return self._control_action(current.status)

    def _finish_control_after_transition_conflict(
        self,
        task: ImportTaskRecord,
        assistant: Any,
        staged_path: Path | None,
        temporary_path: Path | None,
        formal_path: Path | None,
    ) -> bool:
        if self._current_control_action(task) is None:
            return False
        self._finish_control(
            task,
            assistant,
            staged_path,
            temporary_path,
            formal_path,
        )
        return True

    @staticmethod
    def _control_action(status: str) -> str | None:
        return {
            "pause_requested": "pause",
            "cancel_requested": "cancel",
        }.get(status)

    def _mark_control_failed(
        self,
        task: ImportTaskRecord,
        action: str,
        error: BaseException,
    ) -> None:
        summary = sanitize_error_message(error)[:500] or error.__class__.__name__
        self.repository.mark_control_failed(
            task.user_id,
            task.task_id,
            f"{action}_cleanup_failed",
            summary,
            now=self._now_iso(),
        )

    def _control_checkpoint(self, task: ImportTaskRecord):
        def check(_stage: str) -> None:
            current = self.repository.get_task(task.user_id, task.task_id)
            if current is None:
                raise ImportControlSignal("cancel")
            if current.status == "pause_requested":
                raise ImportControlSignal("pause")
            if current.status == "cancel_requested":
                raise ImportControlSignal("cancel")

        return check

    def _resolve_staged_path(self, task: ImportTaskRecord) -> Path:
        return self.storage.resolve_staged_import_path(
            task.user_id,
            task.batch_id,
            task.task_id,
            task.file_suffix,
            task.staged_relative_path,
        )

    @staticmethod
    def _remove_attempt_files(*paths: Path | None) -> None:
        for path in paths:
            if path is None:
                continue
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning("could not remove failed import file", exc_info=True)

    @staticmethod
    def _remove_control_attempt_files(*paths: Path | None) -> None:
        for path in paths:
            if path is not None:
                path.unlink(missing_ok=True)

    @staticmethod
    def _cleanup_staged_file(path: Path) -> None:
        """Remove the durable staged source, surfacing cleanup failures."""
        path.unlink(missing_ok=True)

    def _progress_callback(self, task: ImportTaskRecord):
        last_progress = 10
        last_state: tuple[str, int, str] | None = None
        check_control = self._control_checkpoint(task)

        def update(stage: str, completed: int, total: int, message: str) -> None:
            nonlocal last_progress, last_state
            check_control(stage)
            if stage not in _STAGE_RANGES:
                return
            start, end = _STAGE_RANGES[stage]
            ratio = 0.0 if total <= 0 else min(1.0, max(0.0, completed / total))
            progress = max(last_progress, start + int((end - start) * ratio))
            state = (stage, progress, str(message))
            if state == last_state:
                return
            try:
                self.repository.update_progress(
                    task.user_id,
                    task.task_id,
                    stage,
                    progress,
                    now=self._now_iso(),
                )
            except InvalidImportTransition:
                check_control(stage)
                raise
            last_progress = progress
            last_state = state

        return update

    def _now_iso(self) -> str:
        return _as_utc_iso(self.clock())

    @staticmethod
    def _remove_empty_batch_dir(path: Path) -> None:
        try:
            path.rmdir()
        except OSError:
            pass


class ImportWorkerPool:
    def __init__(
        self,
        repository: ImportTaskRepository,
        runtime_registry: Any,
        storage: UserStorage,
        *,
        runner: Any = None,
        worker_count: int = 4,
        maintenance: ImportHistoryMaintenance | None = None,
        retention_days: int | None = None,
    ) -> None:
        if worker_count < 1:
            raise ValueError("worker_count must be at least 1")
        if retention_days is None:
            retention_days = parse_import_task_retention_days()
        elif (
            isinstance(retention_days, bool)
            or not isinstance(retention_days, int)
            or retention_days < 0
        ):
            raise ValueError(_RETENTION_ERROR)
        self.repository = repository
        self.storage = storage
        self.runner = runner or ImportTaskRunner(
            repository, runtime_registry, storage
        )
        self.worker_count = worker_count
        self.maintenance = maintenance or ImportHistoryMaintenance(repository, storage)
        self.retention_days = retention_days
        self._condition = threading.Condition()
        self._stop_event = threading.Event()
        self._blocked_user_ids: set[str] = set()
        self._worker_threads: list[threading.Thread] = []
        self._scheduler_thread: threading.Thread | None = None
        self._task_queue: queue.Queue[ImportTaskRecord | object] = queue.Queue()
        self._active_count = 0
        self._notify_generation = 0
        self._last_retention_at: float | None = None

    def start(self) -> None:
        with self._condition:
            if any(thread.is_alive() for thread in self._worker_threads) or (
                self._scheduler_thread is not None
                and self._scheduler_thread.is_alive()
            ):
                return
            try:
                self.maintenance.recover_deleting(limit=20)
            except Exception:
                logger.error("import history deletion recovery failed")
            self.repository.recover_running(self.storage)
            self.repository.cleanup_succeeded_staging(self.storage)
            self._stop_event.clear()
            self._blocked_user_ids.clear()
            self._active_count = 0
            self._notify_generation = 0
            self._last_retention_at = None
            self._task_queue = queue.Queue()
            self._worker_threads = [
                threading.Thread(
                    target=self._worker_loop,
                    name=f"import-worker-{index + 1}",
                    daemon=False,
                )
                for index in range(self.worker_count)
            ]
            self._scheduler_thread = threading.Thread(
                target=self._scheduler_loop,
                name="import-scheduler",
                daemon=False,
            )
            workers = list(self._worker_threads)
            scheduler = self._scheduler_thread
        for thread in workers:
            thread.start()
        scheduler.start()

    def stop(self, wait: bool = True) -> None:
        with self._condition:
            self._stop_event.set()
            self._notify_generation += 1
            self._condition.notify_all()
            scheduler = self._scheduler_thread
            workers = list(self._worker_threads)
        if wait and scheduler is not None:
            scheduler.join()
            self._task_queue.join()
            for thread in workers:
                thread.join()

    def notify(self) -> None:
        with self._condition:
            self._notify_generation += 1
            self._condition.notify_all()

    def _scheduler_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                with self._condition:
                    if self._active_count >= self.worker_count:
                        self._condition.wait(timeout=1.0)
                        continue
                    blocked = set(self._blocked_user_ids)
                    observed_generation = self._notify_generation
                try:
                    task = self.repository.claim_next(blocked)
                except sqlite3.OperationalError:
                    task = None
                except Exception:
                    logger.exception("import scheduler could not claim a task")
                    task = None
                if task is None:
                    with self._condition:
                        awakened = self._condition.wait_for(
                            lambda: self._stop_event.is_set()
                            or self._notify_generation != observed_generation,
                            timeout=1.0,
                        )
                        idle = (
                            not awakened
                            and not self._stop_event.is_set()
                            and self._active_count == 0
                        )
                    if idle:
                        self._run_idle_retention()
                    continue
                with self._condition:
                    if self._stop_event.is_set():
                        release_claim = True
                    else:
                        release_claim = False
                        self._blocked_user_ids.add(task.user_id)
                        self._active_count += 1
                        self._task_queue.put(task)
                if release_claim:
                    try:
                        release = (
                            self.repository.release_control_claim
                            if task.control_claimed_at is not None
                            else self.repository.release_claim
                        )
                        release(task.user_id, task.task_id)
                    except Exception:
                        logger.exception(
                            "import scheduler could not release a shutdown claim"
                        )
                    break
        finally:
            for _ in self._worker_threads:
                self._task_queue.put(_STOP)

    def _run_idle_retention(self) -> None:
        if self.retention_days == 0:
            return
        current = time.monotonic()
        if (
            self._last_retention_at is not None
            and current - self._last_retention_at < _RETENTION_INTERVAL_SECONDS
        ):
            return
        self._last_retention_at = current
        try:
            self.maintenance.run_retention(self.retention_days, limit=10)
        except Exception:
            logger.error("import history retention maintenance failed")

    def _worker_loop(self) -> None:
        while True:
            item = self._task_queue.get()
            try:
                if item is _STOP:
                    return
                task = item
                try:
                    self.runner.run(task)
                except Exception:
                    logger.exception("import task runner failed unexpectedly")
                    self._record_runner_failure(task)
                finally:
                    with self._condition:
                        self._blocked_user_ids.discard(task.user_id)
                        self._active_count = max(0, self._active_count - 1)
                        self._notify_generation += 1
                        self._condition.notify_all()
            finally:
                self._task_queue.task_done()

    def _record_runner_failure(self, task: ImportTaskRecord) -> None:
        """Record a crash without overwriting a task that already finished."""

        for delay in (*_RUNNER_FAILURE_RETRY_DELAYS, None):
            try:
                current = self.repository.get_task(task.user_id, task.task_id)
                if current is None:
                    return
                if current.status == "running":
                    self.repository.mark_failed(
                        task.user_id,
                        task.task_id,
                        "unexpected_error",
                        "Import worker failed unexpectedly",
                    )
                elif current.status in {"pause_requested", "cancel_requested"}:
                    action = (
                        "pause" if current.status == "pause_requested" else "cancel"
                    )
                    self.repository.mark_control_failed(
                        task.user_id,
                        task.task_id,
                        f"{action}_cleanup_failed",
                        "Import control cleanup failed unexpectedly",
                    )
                else:
                    return
                return
            except sqlite3.OperationalError as error:
                if not _is_sqlite_busy(error):
                    logger.exception("could not record import runner failure")
                    return
                if delay is not None:
                    time.sleep(delay)
            except InvalidImportTransition:
                return
            except Exception:
                logger.exception("could not record import runner failure")
                return

        # If a transient writer lock survives all failure writes, release the
        # appropriate claim so a later worker can process it.
        for delay in (*_RUNNER_FAILURE_RETRY_DELAYS, None):
            try:
                current = self.repository.get_task(task.user_id, task.task_id)
                if current is None:
                    return
                if current.status == "running":
                    self.repository.release_claim(task.user_id, task.task_id)
                elif current.status in {"pause_requested", "cancel_requested"}:
                    if current.control_claimed_at is not None:
                        self.repository.release_control_claim(
                            task.user_id, task.task_id
                        )
                else:
                    return
                return
            except sqlite3.OperationalError as error:
                if not _is_sqlite_busy(error):
                    logger.exception("could not release crashed import task")
                    return
                if delay is not None:
                    time.sleep(delay)
            except InvalidImportTransition:
                return
            except Exception:
                logger.exception("could not release crashed import task")
                return
        logger.error("import task claim remains after worker failure")


def _as_utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _is_sqlite_busy(error: sqlite3.OperationalError) -> bool:
    return any(marker in str(error).lower() for marker in ("locked", "busy"))
