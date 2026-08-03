from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from app.database import connect, transaction
from app.import_models import (
    ImportBatchSummary,
    ImportStage,
    ImportStatus,
    ImportTaskCreate,
    ImportTaskRecord,
)
from app.storage import UserStorage
from hello_agents.memory.rag.errors import sanitize_error_message


class InvalidImportTransition(ValueError):
    """Raised when an import task cannot make the requested state change."""


ACTIVE_STATUSES = ("queued", "running", "retry_wait")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class ImportTaskRepository:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)

    def create_batch(
        self,
        user_id: str,
        tasks: Iterable[ImportTaskCreate],
        now: str | None = None,
    ) -> ImportBatchSummary:
        task_list = list(tasks)
        if not task_list:
            raise ValueError("an import batch requires at least one task")
        batch_ids = {task.batch_id for task in task_list}
        if len(batch_ids) != 1 or any(task.user_id != user_id for task in task_list):
            raise ValueError("all import tasks must belong to one user and batch")

        timestamp = now or _utc_now()
        batch_id = task_list[0].batch_id
        with transaction(self.db_path) as conn:
            conn.execute(
                """
                insert into import_batches (id, user_id, created_at, updated_at)
                values (?, ?, ?, ?)
                """,
                (batch_id, user_id, timestamp, timestamp),
            )
            conn.executemany(
                """
                insert into import_tasks (
                    id, batch_id, user_id, document_id, original_name, file_suffix,
                    size_bytes, staged_relative_path, status, stage, progress,
                    created_at, updated_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, 'queued', 'queued', 0, ?, ?)
                """,
                [
                    (
                        task.task_id,
                        task.batch_id,
                        task.user_id,
                        task.document_id,
                        task.original_name,
                        task.file_suffix,
                        task.size_bytes,
                        task.staged_relative_path,
                        timestamp,
                        timestamp,
                    )
                    for task in task_list
                ],
            )
            return self._get_batch(conn, user_id, batch_id)

    def list_batches(self, user_id: str, limit: int = 50) -> list[ImportBatchSummary]:
        if limit < 1:
            return []
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """
                select id from import_batches
                where user_id = ?
                order by created_at desc, id desc
                limit ?
                """,
                (user_id, limit),
            ).fetchall()
            return [self._get_batch(conn, user_id, row["id"]) for row in rows]

    def get_batch(self, user_id: str, batch_id: str) -> ImportBatchSummary | None:
        with connect(self.db_path) as conn:
            return self._get_batch(conn, user_id, batch_id)

    def get_task(self, user_id: str, task_id: str) -> ImportTaskRecord | None:
        with connect(self.db_path) as conn:
            row = conn.execute(
                "select * from import_tasks where id = ? and user_id = ?",
                (task_id, user_id),
            ).fetchone()
            return _task_from_row(row) if row is not None else None

    def claim_next(
        self,
        blocked_user_ids: set[str],
        now: str | None = None,
    ) -> ImportTaskRecord | None:
        timestamp = now or _utc_now()
        conn = connect(self.db_path)
        try:
            conn.execute("begin immediate")
            blocked_clause, blocked_params = _blocked_user_clause(blocked_user_ids)
            rows = conn.execute(
                f"""
                select id, status from import_tasks
                where status in ('queued', 'retry_wait')
                  and (next_attempt_at is null or next_attempt_at <= ?)
                  {blocked_clause}
                order by created_at, id
                """,
                (timestamp, *blocked_params),
            ).fetchall()
            for candidate in rows:
                try:
                    updated = conn.execute(
                        """
                        update import_tasks
                        set status = 'running', next_attempt_at = null,
                            total_attempt_count = total_attempt_count + 1,
                            started_at = ?, updated_at = ?
                        where id = ? and status = ?
                        """,
                        (timestamp, timestamp, candidate["id"], candidate["status"]),
                    )
                except sqlite3.IntegrityError:
                    # Another running task for this user exists. Try another user.
                    continue
                if updated.rowcount:
                    conn.execute(
                        """
                        update import_batches set updated_at = ?
                        where id = (select batch_id from import_tasks where id = ?)
                          and user_id = (select user_id from import_tasks where id = ?)
                        """,
                        (timestamp, candidate["id"], candidate["id"]),
                    )
                    row = conn.execute(
                        "select * from import_tasks where id = ?", (candidate["id"],)
                    ).fetchone()
                    conn.commit()
                    return _task_from_row(row)
            conn.commit()
            return None
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def update_progress(
        self,
        user_id: str,
        task_id: str,
        stage: ImportStage,
        progress: int,
        now: str | None = None,
    ) -> ImportTaskRecord:
        if not 0 <= progress <= 100:
            raise ValueError("progress must be between 0 and 100")
        return self._transition_update(
            user_id,
            task_id,
            "status = 'running'",
            "stage = ?, progress = ?, updated_at = ?",
            (stage, progress, now or _utc_now()),
        )

    def release_claim(
        self, user_id: str, task_id: str, now: str | None = None
    ) -> ImportTaskRecord:
        """Requeue a claimed task when its attempt never started."""
        return self._transition_update(
            user_id,
            task_id,
            "status = 'running'",
            """status = 'queued', stage = 'queued', progress = 0,
               next_attempt_at = null, started_at = null,
               total_attempt_count = case
                   when total_attempt_count > 0 then total_attempt_count - 1
                   else 0
               end,
               updated_at = ?""",
            (now or _utc_now(),),
        )

    def mark_succeeded(
        self, user_id: str, task_id: str, now: str | None = None
    ) -> ImportTaskRecord:
        timestamp = now or _utc_now()
        return self._transition_update(
            user_id,
            task_id,
            "status = 'running'",
            """status = 'succeeded', stage = 'succeeded', progress = 100,
               next_attempt_at = null, error_code = null, error_summary = null,
               finished_at = ?, updated_at = ?""",
            (timestamp, timestamp),
        )

    def mark_retry_wait(
        self,
        user_id: str,
        task_id: str,
        next_attempt_at: str,
        error_code: str,
        error_summary: str,
        now: str | None = None,
    ) -> ImportTaskRecord:
        timestamp = now or _utc_now()
        error_summary = sanitize_error_message(error_summary)[:500]
        return self._transition_update(
            user_id,
            task_id,
            "status = 'running'",
            """status = 'retry_wait', stage = 'queued',
               auto_retry_count = auto_retry_count + 1, next_attempt_at = ?,
               error_code = ?, error_summary = ?, updated_at = ?""",
            (next_attempt_at, error_code, error_summary, timestamp),
        )

    def mark_failed(
        self,
        user_id: str,
        task_id: str,
        error_code: str,
        error_summary: str,
        now: str | None = None,
    ) -> ImportTaskRecord:
        timestamp = now or _utc_now()
        error_summary = sanitize_error_message(error_summary)[:500]
        return self._transition_update(
            user_id,
            task_id,
            "status = 'running'",
            """status = 'failed', stage = 'failed', error_code = ?,
               error_summary = ?, finished_at = ?, updated_at = ?""",
            (error_code, error_summary, timestamp, timestamp),
        )

    def retry_task(
        self, user_id: str, task_id: str, now: str | None = None
    ) -> ImportTaskRecord:
        timestamp = now or _utc_now()
        return self._transition_update(
            user_id,
            task_id,
            "status = 'failed'",
            """status = 'queued', stage = 'queued', progress = 0,
               auto_retry_count = 0, manual_retry_count = manual_retry_count + 1,
               next_attempt_at = null, error_code = null, error_summary = null,
               started_at = null, finished_at = null, updated_at = ?""",
            (timestamp,),
        )

    def retry_failed_in_batch(
        self, user_id: str, batch_id: str, now: str | None = None
    ) -> int:
        timestamp = now or _utc_now()
        with transaction(self.db_path) as conn:
            updated = conn.execute(
                """
                update import_tasks
                set status = 'queued', stage = 'queued', progress = 0,
                    auto_retry_count = 0, manual_retry_count = manual_retry_count + 1,
                    next_attempt_at = null, error_code = null, error_summary = null,
                    started_at = null, finished_at = null, updated_at = ?
                where user_id = ? and batch_id = ? and status = 'failed'
                """,
                (timestamp, user_id, batch_id),
            )
            if updated.rowcount:
                conn.execute(
                    "update import_batches set updated_at = ? where id = ? and user_id = ?",
                    (timestamp, batch_id, user_id),
                )
            return updated.rowcount

    def recover_running(self, storage: UserStorage, now: str | None = None) -> int:
        timestamp = now or _utc_now()
        recovered = 0
        with transaction(self.db_path) as conn:
            rows = conn.execute(
                "select id, user_id, staged_relative_path from import_tasks where status = 'running'"
            ).fetchall()
            for row in rows:
                user_root = storage.user_paths(row["user_id"]).root
                staged_path = storage.assert_within_user(
                    row["user_id"], user_root / row["staged_relative_path"]
                )
                if staged_path.is_file():
                    conn.execute(
                        """
                        update import_tasks
                        set status = 'queued', stage = 'queued', next_attempt_at = null,
                            error_code = 'process_interrupted',
                            error_summary = 'Import processing was interrupted',
                            started_at = null, updated_at = ?
                        where id = ? and status = 'running'
                        """,
                        (timestamp, row["id"]),
                    )
                else:
                    conn.execute(
                        """
                        update import_tasks
                        set status = 'failed', stage = 'failed',
                            error_code = 'staged_file_missing',
                            error_summary = 'Staged import file is missing',
                            finished_at = ?, updated_at = ?
                        where id = ? and status = 'running'
                        """,
                        (timestamp, timestamp, row["id"]),
                    )
                conn.execute(
                    """
                    update import_batches set updated_at = ?
                    where id = (select batch_id from import_tasks where id = ?)
                      and user_id = ?
                    """,
                    (timestamp, row["id"], row["user_id"]),
                )
                recovered += 1
        return recovered

    def cleanup_succeeded_staging(self, storage: UserStorage) -> int:
        """Remove only staging files whose persisted succeeded task path is exact."""

        with connect(self.db_path) as conn:
            rows = conn.execute(
                """
                select id, batch_id, user_id, file_suffix, staged_relative_path
                from import_tasks where status = 'succeeded'
                """
            ).fetchall()

        removed = 0
        for row in rows:
            try:
                staged_path = storage.resolve_staged_import_path(
                    row["user_id"],
                    row["batch_id"],
                    row["id"],
                    row["file_suffix"],
                    row["staged_relative_path"],
                )
                existed = staged_path.is_file()
                staged_path.unlink(missing_ok=True)
            except (OSError, ValueError):
                continue
            if existed:
                removed += 1
            try:
                staged_path.parent.rmdir()
            except OSError:
                pass
        return removed

    def has_active_tasks(self, user_id: str) -> bool:
        with connect(self.db_path) as conn:
            row = conn.execute(
                """
                select 1 from import_tasks
                where user_id = ? and status in ('queued', 'running', 'retry_wait')
                limit 1
                """,
                (user_id,),
            ).fetchone()
            return row is not None

    def has_active_task_for_document(self, user_id: str, document_id: str) -> bool:
        with connect(self.db_path) as conn:
            row = conn.execute(
                """
                select 1 from import_tasks
                where user_id = ? and document_id = ?
                  and status in ('queued', 'running', 'retry_wait')
                limit 1
                """,
                (user_id, document_id),
            ).fetchone()
            return row is not None

    def _transition_update(
        self,
        user_id: str,
        task_id: str,
        expected_condition: str,
        set_clause: str,
        values: tuple[object, ...],
    ) -> ImportTaskRecord:
        with transaction(self.db_path) as conn:
            updated = conn.execute(
                f"""
                update import_tasks set {set_clause}
                where id = ? and user_id = ? and {expected_condition}
                """,
                (*values, task_id, user_id),
            )
            if not updated.rowcount:
                self._raise_transition_error(conn, user_id, task_id)
            conn.execute(
                """
                update import_batches set updated_at = (
                    select updated_at from import_tasks where id = ? and user_id = ?
                ) where id = (
                    select batch_id from import_tasks where id = ? and user_id = ?
                ) and user_id = ?
                """,
                (task_id, user_id, task_id, user_id, user_id),
            )
            row = conn.execute(
                "select * from import_tasks where id = ? and user_id = ?", (task_id, user_id)
            ).fetchone()
            return _task_from_row(row)

    def _raise_transition_error(
        self, conn: sqlite3.Connection, user_id: str, task_id: str
    ) -> None:
        exists = conn.execute(
            "select 1 from import_tasks where id = ? and user_id = ?", (task_id, user_id)
        ).fetchone()
        if exists is None:
            raise KeyError("import task was not found")
        raise InvalidImportTransition("import task is not in the required state")

    def _get_batch(
        self, conn: sqlite3.Connection, user_id: str, batch_id: str
    ) -> ImportBatchSummary | None:
        row = conn.execute(
            """
            select b.id, b.user_id, b.created_at, b.updated_at,
                   count(t.id) as total,
                   coalesce(sum(t.status = 'queued'), 0) as queued,
                   coalesce(sum(t.status = 'running'), 0) as running,
                   coalesce(sum(t.status = 'retry_wait'), 0) as retry_wait,
                   coalesce(sum(t.status = 'succeeded'), 0) as succeeded,
                   coalesce(sum(t.status = 'failed'), 0) as failed
            from import_batches b
            left join import_tasks t on t.batch_id = b.id and t.user_id = b.user_id
            where b.id = ? and b.user_id = ?
            group by b.id, b.user_id, b.created_at, b.updated_at
            """,
            (batch_id, user_id),
        ).fetchone()
        if row is None:
            return None
        task_rows = conn.execute(
            """
            select * from import_tasks
            where batch_id = ? and user_id = ?
            order by created_at, id
            """,
            (batch_id, user_id),
        ).fetchall()
        return ImportBatchSummary(
            batch_id=row["id"],
            user_id=row["user_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            total=row["total"],
            queued=row["queued"],
            running=row["running"],
            retry_wait=row["retry_wait"],
            succeeded=row["succeeded"],
            failed=row["failed"],
            tasks=tuple(_task_from_row(task_row) for task_row in task_rows),
        )


def _task_from_row(row: sqlite3.Row) -> ImportTaskRecord:
    return ImportTaskRecord(
        task_id=row["id"],
        batch_id=row["batch_id"],
        user_id=row["user_id"],
        document_id=row["document_id"],
        original_name=row["original_name"],
        file_suffix=row["file_suffix"],
        size_bytes=row["size_bytes"],
        staged_relative_path=row["staged_relative_path"],
        status=row["status"],
        stage=row["stage"],
        progress=row["progress"],
        total_attempt_count=row["total_attempt_count"],
        auto_retry_count=row["auto_retry_count"],
        manual_retry_count=row["manual_retry_count"],
        max_auto_retries=row["max_auto_retries"],
        next_attempt_at=row["next_attempt_at"],
        error_code=row["error_code"],
        error_summary=row["error_summary"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        updated_at=row["updated_at"],
    )


def _blocked_user_clause(user_ids: set[str]) -> tuple[str, tuple[str, ...]]:
    if not user_ids:
        return "", ()
    placeholders = ", ".join("?" for _ in user_ids)
    return f"and user_id not in ({placeholders})", tuple(user_ids)
