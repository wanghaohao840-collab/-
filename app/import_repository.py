from __future__ import annotations

import re
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
    ImportTaskEventRecord,
    ImportTaskRecord,
)
from app.storage import UserStorage
from hello_agents.memory.rag.errors import sanitize_error_message


class InvalidImportTransition(ValueError):
    """Raised when an import task cannot make the requested state change."""


ACTIVE_STATUSES = (
    "queued",
    "running",
    "retry_wait",
    "pause_requested",
    "paused",
    "cancel_requested",
)

_URL_RE = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s,;]+", re.IGNORECASE)
_QUOTED_ABSOLUTE_PATH_RE = re.compile(
    r'''(["'])(?:(?:[a-z]:[\\/])|(?:\\\\)|/)[^"'\r\n]+\1''',
    re.IGNORECASE,
)
_UNC_PATH_RE = re.compile(r"(?<!\\)\\\\[^;\r\n]*")
_WINDOWS_PATH_RE = re.compile(r"(?<!\w)[a-z]:[\\/][^;\r\n]*", re.IGNORECASE)
_POSIX_PATH_RE = re.compile(r"(?<![:\w])/[^;\r\n]*")
_STAGED_IMPORT_PATH_RE = re.compile(
    r"(?<!\w)imports[\\/][^;\r\n]*", re.IGNORECASE
)
_UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
_PRIVATE_ID_RE = re.compile(
    r'''["']?((?:user|document|task|batch)_id)["']?\s*[:=]\s*["']?'''
    r'''[^"'\s,;}]+["']?''',
    re.IGNORECASE,
)
_ERROR_CLASS_RE = re.compile(r"^[\w.]+(?:Error|Exception):")


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

    def request_pause(
        self, user_id: str, task_id: str, now: str | None = None
    ) -> ImportTaskRecord:
        timestamp = now or _utc_now()
        with transaction(self.db_path) as conn:
            conn.execute("begin immediate")
            row = self._required_task_row(conn, user_id, task_id)
            if row["status"] in ("queued", "retry_wait"):
                allowed = ("queued", "retry_wait")
                target = "paused"
                event_type = "paused"
            elif row["status"] == "running":
                allowed = ("running",)
                target = "pause_requested"
                event_type = "pause_requested"
            else:
                raise InvalidImportTransition("import task is not in the required state")
            return self._set_control_state(
                conn,
                user_id,
                task_id,
                allowed=allowed,
                target=target,
                event_type=event_type,
                timestamp=timestamp,
            )

    def request_cancel(
        self, user_id: str, task_id: str, now: str | None = None
    ) -> ImportTaskRecord:
        timestamp = now or _utc_now()
        with transaction(self.db_path) as conn:
            conn.execute("begin immediate")
            row = self._required_task_row(conn, user_id, task_id)
            allowed = (
                "queued",
                "retry_wait",
                "paused",
                "running",
                "pause_requested",
            )
            if row["status"] == "failed" and row["error_code"] in {
                "pause_cleanup_failed",
                "cancel_cleanup_failed",
            }:
                allowed = ("failed",)
            elif row["status"] not in allowed:
                raise InvalidImportTransition("import task is not in the required state")
            return self._set_control_state(
                conn,
                user_id,
                task_id,
                allowed=allowed,
                target="cancel_requested",
                event_type="cancel_requested",
                timestamp=timestamp,
            )

    def resume_task(
        self, user_id: str, task_id: str, now: str | None = None
    ) -> ImportTaskRecord:
        timestamp = now or _utc_now()
        with transaction(self.db_path) as conn:
            conn.execute("begin immediate")
            return self._resume_task(conn, user_id, task_id, timestamp)

    def request_pause_batch(
        self, user_id: str, batch_id: str, now: str | None = None
    ) -> ImportBatchSummary:
        timestamp = now or _utc_now()
        with transaction(self.db_path) as conn:
            conn.execute("begin immediate")
            self._require_batch(conn, user_id, batch_id)
            rows = conn.execute(
                """
                select id, status from import_tasks
                where user_id = ? and batch_id = ?
                  and status in ('queued', 'retry_wait', 'running')
                order by created_at, id
                """,
                (user_id, batch_id),
            ).fetchall()
            for row in rows:
                direct_pause = row["status"] in ("queued", "retry_wait")
                self._set_control_state(
                    conn,
                    user_id,
                    row["id"],
                    allowed=("queued", "retry_wait") if direct_pause else ("running",),
                    target="paused" if direct_pause else "pause_requested",
                    event_type="paused" if direct_pause else "pause_requested",
                    timestamp=timestamp,
                )
            return self._get_batch(conn, user_id, batch_id)

    def request_cancel_batch(
        self, user_id: str, batch_id: str, now: str | None = None
    ) -> ImportBatchSummary:
        timestamp = now or _utc_now()
        with transaction(self.db_path) as conn:
            conn.execute("begin immediate")
            self._require_batch(conn, user_id, batch_id)
            rows = conn.execute(
                """
                select id, status from import_tasks
                where user_id = ? and batch_id = ?
                  and (
                    status in ('queued', 'retry_wait', 'paused', 'running',
                               'pause_requested')
                    or (status = 'failed' and error_code in
                        ('pause_cleanup_failed', 'cancel_cleanup_failed'))
                  )
                order by created_at, id
                """,
                (user_id, batch_id),
            ).fetchall()
            for row in rows:
                self._set_control_state(
                    conn,
                    user_id,
                    row["id"],
                    allowed=(row["status"],),
                    target="cancel_requested",
                    event_type="cancel_requested",
                    timestamp=timestamp,
                )
            return self._get_batch(conn, user_id, batch_id)

    def resume_batch(
        self, user_id: str, batch_id: str, now: str | None = None
    ) -> ImportBatchSummary:
        timestamp = now or _utc_now()
        with transaction(self.db_path) as conn:
            conn.execute("begin immediate")
            self._require_batch(conn, user_id, batch_id)
            rows = conn.execute(
                """
                select id from import_tasks
                where user_id = ? and batch_id = ? and status = 'paused'
                order by created_at, id
                """,
                (user_id, batch_id),
            ).fetchall()
            for row in rows:
                self._resume_task(conn, user_id, row["id"], timestamp)
            return self._get_batch(conn, user_id, batch_id)

    def claim_next(
        self,
        blocked_user_ids: set[str],
        now: str | None = None,
    ) -> ImportTaskRecord | None:
        timestamp = now or _utc_now()
        conn = connect(self.db_path)
        try:
            conn.execute("begin immediate")
            blocked_clause, blocked_params = _blocked_user_clause(
                blocked_user_ids, "t.user_id"
            )
            control = conn.execute(
                f"""
                select t.id, t.user_id, t.status from import_tasks t
                where t.status in ('pause_requested', 'cancel_requested')
                  and t.control_claimed_at is null
                  {blocked_clause}
                  and not exists (
                      select 1 from import_tasks x
                      where x.user_id = t.user_id and x.id <> t.id
                        and (x.status in ('running', 'pause_requested')
                             or x.control_claimed_at is not null)
                  )
                order by t.control_requested_at, t.created_at, t.id
                limit 1
                """,
                blocked_params,
            ).fetchone()
            if control is not None:
                updated = conn.execute(
                    """
                    update import_tasks set control_claimed_at = ?
                    where id = ? and user_id = ? and status = ?
                      and control_claimed_at is null
                    """,
                    (
                        timestamp,
                        control["id"],
                        control["user_id"],
                        control["status"],
                    ),
                )
                if updated.rowcount:
                    row = conn.execute(
                        "select * from import_tasks where id = ? and user_id = ?",
                        (control["id"], control["user_id"]),
                    ).fetchone()
                    conn.commit()
                    return _task_from_row(row)
            rows = conn.execute(
                f"""
                select t.id, t.user_id, t.status from import_tasks t
                where t.status in ('queued', 'retry_wait')
                  and (t.next_attempt_at is null or t.next_attempt_at <= ?)
                  {blocked_clause}
                  and not exists (
                      select 1 from import_tasks pending
                      where pending.user_id = t.user_id
                        and pending.status in ('pause_requested', 'cancel_requested')
                  )
                order by t.created_at, t.id
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
                        where id = ? and user_id = ? and status = ?
                        """,
                        (
                            timestamp,
                            timestamp,
                            candidate["id"],
                            candidate["user_id"],
                            candidate["status"],
                        ),
                    )
                except sqlite3.IntegrityError:
                    # Another running task for this user exists. Try another user.
                    continue
                if updated.rowcount:
                    conn.execute(
                        """
                        update import_batches set updated_at = ?
                        where id = (select batch_id from import_tasks where id = ?)
                          and user_id = ?
                        """,
                        (timestamp, candidate["id"], candidate["user_id"]),
                    )
                    row = conn.execute(
                        "select * from import_tasks where id = ? and user_id = ?",
                        (candidate["id"], candidate["user_id"]),
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

    def release_control_claim(
        self, user_id: str, task_id: str, now: str | None = None
    ) -> ImportTaskRecord:
        return self._transition_update(
            user_id,
            task_id,
            "status in ('pause_requested', 'cancel_requested') "
            "and control_claimed_at is not null",
            "control_claimed_at = null, updated_at = ?",
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
        error_summary = _safe_import_error_summary(error_summary)
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
        error_summary = _safe_import_error_summary(error_summary)
        return self._transition_update(
            user_id,
            task_id,
            "status = 'running'",
            """status = 'failed', stage = 'failed', error_code = ?,
               error_summary = ?, finished_at = ?, updated_at = ?""",
            (error_code, error_summary, timestamp, timestamp),
        )

    def mark_paused(
        self, user_id: str, task_id: str, now: str | None = None
    ) -> ImportTaskRecord:
        return self._finish_control(
            user_id,
            task_id,
            expected="pause_requested",
            target="paused",
            timestamp=now or _utc_now(),
        )

    def mark_cancelled(
        self, user_id: str, task_id: str, now: str | None = None
    ) -> ImportTaskRecord:
        return self._finish_control(
            user_id,
            task_id,
            expected="cancel_requested",
            target="cancelled",
            timestamp=now or _utc_now(),
        )

    def mark_control_failed(
        self,
        user_id: str,
        task_id: str,
        error_code: str,
        error_summary: str,
        now: str | None = None,
    ) -> ImportTaskRecord:
        if error_code not in {"pause_cleanup_failed", "cancel_cleanup_failed"}:
            raise ValueError("unsupported control error code")
        timestamp = now or _utc_now()
        safe_summary = _safe_import_error_summary(error_summary)
        with transaction(self.db_path) as conn:
            updated = conn.execute(
                """
                update import_tasks
                set status = 'failed', stage = 'failed', error_code = ?,
                    error_summary = ?, next_attempt_at = null,
                    control_requested_at = null, control_claimed_at = null,
                    finished_at = ?, updated_at = ?
                where id = ? and user_id = ?
                  and status in ('pause_requested', 'cancel_requested')
                """,
                (
                    error_code,
                    safe_summary,
                    timestamp,
                    timestamp,
                    task_id,
                    user_id,
                ),
            )
            if updated.rowcount != 1:
                self._raise_transition_error(conn, user_id, task_id)
            row = self._required_task_row(conn, user_id, task_id)
            self._insert_event(conn, row, "failed", safe_summary, timestamp)
            self._touch_batch(conn, row, timestamp)
            return _task_from_row(row)

    def list_task_events(
        self, user_id: str, task_id: str, limit: int = 200
    ) -> list[ImportTaskEventRecord]:
        if limit < 1:
            return []
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """
                select * from import_task_events
                where user_id = ? and task_id = ?
                order by created_at, id
                limit ?
                """,
                (user_id, task_id, min(limit, 200)),
            ).fetchall()
            return [_event_from_row(row) for row in rows]

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
            conn.execute(
                "update import_tasks set control_claimed_at = null "
                "where control_claimed_at is not null"
            )
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
        placeholders = ", ".join("?" for _ in ACTIVE_STATUSES)
        with connect(self.db_path) as conn:
            row = conn.execute(
                f"""
                select 1 from import_tasks
                where user_id = ? and status in ({placeholders})
                limit 1
                """,
                (user_id, *ACTIVE_STATUSES),
            ).fetchone()
            return row is not None

    def has_active_task_for_document(self, user_id: str, document_id: str) -> bool:
        placeholders = ", ".join("?" for _ in ACTIVE_STATUSES)
        with connect(self.db_path) as conn:
            row = conn.execute(
                f"""
                select 1 from import_tasks
                where user_id = ? and document_id = ?
                  and status in ({placeholders})
                limit 1
                """,
                (user_id, document_id, *ACTIVE_STATUSES),
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

    def _set_control_state(
        self,
        conn: sqlite3.Connection,
        user_id: str,
        task_id: str,
        *,
        allowed: tuple[str, ...],
        target: str,
        event_type: str,
        timestamp: str,
    ) -> ImportTaskRecord:
        placeholders = ", ".join("?" for _ in allowed)
        updated = conn.execute(
            f"""
            update import_tasks
            set status = ?, control_requested_at = ?, control_claimed_at = null,
                next_attempt_at = null, updated_at = ?
            where id = ? and user_id = ? and status in ({placeholders})
            """,
            (target, timestamp, timestamp, task_id, user_id, *allowed),
        )
        if updated.rowcount != 1:
            self._raise_transition_error(conn, user_id, task_id)
        row = self._required_task_row(conn, user_id, task_id)
        self._insert_event(conn, row, event_type, None, timestamp)
        self._touch_batch(conn, row, timestamp)
        return _task_from_row(row)

    def _resume_task(
        self, conn: sqlite3.Connection, user_id: str, task_id: str, timestamp: str
    ) -> ImportTaskRecord:
        updated = conn.execute(
            """
            update import_tasks
            set status = 'queued', stage = 'queued', next_attempt_at = null,
                control_requested_at = null, control_claimed_at = null,
                started_at = null, finished_at = null, updated_at = ?
            where id = ? and user_id = ? and status = 'paused'
            """,
            (timestamp, task_id, user_id),
        )
        if updated.rowcount != 1:
            self._raise_transition_error(conn, user_id, task_id)
        row = self._required_task_row(conn, user_id, task_id)
        self._insert_event(conn, row, "resumed", None, timestamp)
        self._touch_batch(conn, row, timestamp)
        return _task_from_row(row)

    def _finish_control(
        self,
        user_id: str,
        task_id: str,
        *,
        expected: str,
        target: str,
        timestamp: str,
    ) -> ImportTaskRecord:
        with transaction(self.db_path) as conn:
            updated = conn.execute(
                """
                update import_tasks
                set status = ?, stage = ?, next_attempt_at = null,
                    error_code = null, error_summary = null,
                    control_requested_at = null, control_claimed_at = null,
                    finished_at = ?, updated_at = ?
                where id = ? and user_id = ? and status = ?
                """,
                (target, target, timestamp, timestamp, task_id, user_id, expected),
            )
            if updated.rowcount != 1:
                self._raise_transition_error(conn, user_id, task_id)
            row = self._required_task_row(conn, user_id, task_id)
            self._insert_event(conn, row, target, None, timestamp)
            self._touch_batch(conn, row, timestamp)
            return _task_from_row(row)

    def _insert_event(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row,
        event_type: str,
        message: str | None,
        timestamp: str,
    ) -> None:
        safe_message = None
        if message is not None:
            safe_message = _safe_import_error_summary(message)
        conn.execute(
            """
            insert into import_task_events (
                batch_id, task_id, user_id, event_type, status, stage,
                message, created_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["batch_id"],
                row["id"],
                row["user_id"],
                event_type,
                row["status"],
                row["stage"],
                safe_message,
                timestamp,
            ),
        )

    def _required_task_row(
        self, conn: sqlite3.Connection, user_id: str, task_id: str
    ) -> sqlite3.Row:
        row = conn.execute(
            "select * from import_tasks where id = ? and user_id = ?",
            (task_id, user_id),
        ).fetchone()
        if row is None:
            raise KeyError("import task was not found")
        return row

    def _require_batch(
        self, conn: sqlite3.Connection, user_id: str, batch_id: str
    ) -> None:
        exists = conn.execute(
            "select 1 from import_batches where id = ? and user_id = ?",
            (batch_id, user_id),
        ).fetchone()
        if exists is None:
            raise KeyError("import batch was not found")

    def _touch_batch(
        self, conn: sqlite3.Connection, row: sqlite3.Row, timestamp: str
    ) -> None:
        conn.execute(
            """
            update import_batches set updated_at = ?
            where id = ? and user_id = ?
            """,
            (timestamp, row["batch_id"], row["user_id"]),
        )

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
            select b.id, b.user_id, b.created_at, b.updated_at, b.lifecycle_state,
                   count(t.id) as total,
                   coalesce(sum(t.status = 'queued'), 0) as queued,
                   coalesce(sum(t.status = 'running'), 0) as running,
                   coalesce(sum(t.status = 'retry_wait'), 0) as retry_wait,
                   coalesce(sum(t.status = 'succeeded'), 0) as succeeded,
                   coalesce(sum(t.status = 'failed'), 0) as failed,
                   coalesce(sum(t.status = 'paused'), 0) as paused,
                   coalesce(sum(t.status = 'pause_requested'), 0) as pause_requested,
                   coalesce(sum(t.status = 'cancel_requested'), 0) as cancel_requested,
                   coalesce(sum(t.status = 'cancelled'), 0) as cancelled
            from import_batches b
            left join import_tasks t on t.batch_id = b.id and t.user_id = b.user_id
            where b.id = ? and b.user_id = ?
            group by b.id, b.user_id, b.created_at, b.updated_at, b.lifecycle_state
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
            lifecycle_state=row["lifecycle_state"],
            paused=row["paused"],
            pause_requested=row["pause_requested"],
            cancel_requested=row["cancel_requested"],
            cancelled=row["cancelled"],
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
        control_requested_at=row["control_requested_at"],
        control_claimed_at=row["control_claimed_at"],
    )


def _event_from_row(row: sqlite3.Row) -> ImportTaskEventRecord:
    return ImportTaskEventRecord(
        event_id=row["id"],
        batch_id=row["batch_id"],
        task_id=row["task_id"],
        user_id=row["user_id"],
        event_type=row["event_type"],
        status=row["status"],
        stage=row["stage"],
        message=row["message"],
        created_at=row["created_at"],
    )


def _safe_import_error_summary(message: object) -> str:
    text = sanitize_error_message(message)
    safe_lines: list[str] = []
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("Traceback (most recent call last):"):
            continue
        if stripped.startswith("During handling of the above exception"):
            continue
        if stripped.startswith("File "):
            continue
        if raw_line[:1].isspace() and not _ERROR_CLASS_RE.match(stripped):
            continue
        safe_lines.append(stripped)

    text = "\n".join(safe_lines)
    text = text.replace("Traceback (most recent call last):", "")
    text = _URL_RE.sub("[redacted-url]", text)
    text = _QUOTED_ABSOLUTE_PATH_RE.sub("[redacted-path]", text)
    text = _STAGED_IMPORT_PATH_RE.sub("[redacted-path]", text)
    text = _UNC_PATH_RE.sub("[redacted-path]", text)
    text = _WINDOWS_PATH_RE.sub("[redacted-path]", text)
    text = _POSIX_PATH_RE.sub("[redacted-path]", text)
    text = _PRIVATE_ID_RE.sub(r"\1=[redacted-id]", text)
    text = _UUID_RE.sub("[redacted-id]", text)
    text = re.sub(r"\s+", " ", text).strip()
    return (text or "Import processing failed")[:500]


def _blocked_user_clause(
    user_ids: set[str], column: str = "user_id"
) -> tuple[str, tuple[str, ...]]:
    if not user_ids:
        return "", ()
    placeholders = ", ".join("?" for _ in user_ids)
    return f"and {column} not in ({placeholders})", tuple(user_ids)
