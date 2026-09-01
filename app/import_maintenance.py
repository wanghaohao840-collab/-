from __future__ import annotations

import logging
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.import_models import ImportBatchSummary


_FAILURE_RECORD_RETRY_DELAYS = (0.0, 0.02, 0.05)
logger = logging.getLogger(__name__)


class ImportHistoryMaintenance:
    """Own safe, restartable deletion of terminal import history."""

    def __init__(self, repository, storage):
        self.repository = repository
        self.storage = storage

    def delete_batch(self, user_id: str, batch_id: str) -> None:
        summary = self.repository.mark_batch_deleting(user_id, batch_id)
        self._finish_marked_batch(user_id, summary)

    def resume_batch_deletion(self, user_id: str, batch_id: str) -> None:
        summary = self.repository.get_deleting_batch(user_id, batch_id)
        if summary is None:
            return
        self._finish_marked_batch(user_id, summary)

    def recover_deleting(self, limit: int = 20) -> int:
        batches = self.repository.list_deleting_batches(limit=limit)
        recovered = 0
        for summary in batches:
            try:
                self.resume_batch_deletion(summary.user_id, summary.batch_id)
            except Exception:
                # One corrupt or inaccessible staged batch must not prevent
                # recovery of later batches in this bounded pass.
                logger.error("import history deletion recovery failed for a batch")
            else:
                recovered += 1
        return recovered

    def run_retention(
        self,
        retention_days: int,
        *,
        limit: int = 10,
        now: datetime | None = None,
    ) -> int:
        if (
            isinstance(retention_days, bool)
            or not isinstance(retention_days, int)
            or retention_days < 0
        ):
            raise ValueError("retention_days must be a non-negative integer")
        if retention_days == 0:
            return 0
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        cutoff = (current.astimezone(timezone.utc) - timedelta(days=retention_days))
        cutoff_text = cutoff.isoformat().replace("+00:00", "Z")
        candidates = self.repository.list_retention_candidates(cutoff_text, limit=limit)
        deleted = 0
        for summary in candidates:
            self.delete_batch(summary.user_id, summary.batch_id)
            deleted += 1
        return deleted

    def _finish_marked_batch(
        self, user_id: str, summary: ImportBatchSummary
    ) -> None:
        validated_parents: set[Path] = set()
        try:
            for task in summary.tasks:
                staged = self.storage.resolve_staged_import_path(
                    user_id,
                    summary.batch_id,
                    task.task_id,
                    task.file_suffix,
                    task.staged_relative_path,
                )
                staged.unlink(missing_ok=True)
                validated_parents.add(staged.parent)
            for parent in validated_parents:
                try:
                    parent.rmdir()
                except FileNotFoundError:
                    pass
                except OSError:
                    # An unexpected file is never recursively removed.
                    pass
            self.repository.finish_batch_deletion(user_id, summary.batch_id)
        except Exception as error:
            self._record_cleanup_failure(user_id, summary.batch_id, error)
            raise

    def _record_cleanup_failure(
        self, user_id: str, batch_id: str, cleanup_error: BaseException
    ) -> None:
        for index, delay in enumerate(_FAILURE_RECORD_RETRY_DELAYS):
            if delay:
                time.sleep(delay)
            try:
                self.repository.record_batch_cleanup_failure(
                    user_id, batch_id, cleanup_error
                )
                return
            except sqlite3.OperationalError as record_error:
                busy = any(
                    marker in str(record_error).lower()
                    for marker in ("locked", "busy")
                )
                if not busy or index == len(_FAILURE_RECORD_RETRY_DELAYS) - 1:
                    raise
