from __future__ import annotations

import base64
import binascii
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from app.database import connect, transaction
from app.import_models import (
    ImportBatchSummary,
    ImportHistoryFilters,
    ImportHistoryPage,
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
TERMINAL_STATUSES = ("cancelled", "succeeded", "failed")
_IMPORT_STATUSES = frozenset((*ACTIVE_STATUSES, *TERMINAL_STATUSES))
_HISTORY_CURSOR_KEYS = frozenset(("created_at", "batch_id"))
_HISTORY_CURSOR_RE = re.compile(r"^[A-Za-z0-9_-]+$")

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
                where user_id = ? and lifecycle_state = 'active'
                order by created_at desc, id desc
                limit ?
                """,
                (user_id, limit),
            ).fetchall()
            return [self._get_batch(conn, user_id, row["id"]) for row in rows]

    def list_history(
        self,
        user_id: str,
        filters: ImportHistoryFilters,
        cursor: str | None,
        limit: int,
    ) -> ImportHistoryPage:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("history limit must be between 1 and 100")
        normalized = _validate_history_filters(filters)
        cursor_values = _decode_history_cursor(cursor) if cursor is not None else None

        clauses = ["b.user_id = ?", "b.lifecycle_state = 'active'"]
        parameters: list[object] = [user_id]
        if normalized.batch_id is not None:
            clauses.append("b.id = ?")
            parameters.append(normalized.batch_id)
        if normalized.created_from is not None:
            clauses.append("b.created_at >= ?")
            parameters.append(normalized.created_from)
        if normalized.created_to is not None:
            clauses.append("b.created_at <= ?")
            parameters.append(normalized.created_to)

        task_filters: list[str] = []
        if normalized.statuses:
            placeholders = ", ".join("?" for _ in normalized.statuses)
            task_filters.append(f"t.status in ({placeholders})")
            parameters.extend(normalized.statuses)
        if normalized.filename_query:
            task_filters.append("t.original_name like ? escape '\\'")
            parameters.append(
                f"%{_escape_like(normalized.filename_query)}%"
            )
        if task_filters:
            clauses.append(
                "exists (select 1 from import_tasks t "
                "where t.batch_id = b.id and t.user_id = b.user_id and "
                + " and ".join(task_filters)
                + ")"
            )
        if cursor_values is not None:
            clauses.append(
                "(b.created_at < ? or (b.created_at = ? and b.id < ?))"
            )
            parameters.extend(
                (
                    cursor_values["created_at"],
                    cursor_values["created_at"],
                    cursor_values["batch_id"],
                )
            )

        with connect(self.db_path) as conn:
            rows = conn.execute(
                f"""
                select b.id, b.created_at from import_batches b
                where {' and '.join(clauses)}
                order by b.created_at desc, b.id desc
                limit ?
                """,
                (*parameters, limit + 1),
            ).fetchall()
            page_rows = rows[:limit]
            batches = tuple(
                self._get_batch(conn, user_id, row["id"]) for row in page_rows
            )

        next_cursor = None
        if len(rows) > limit and page_rows:
            last = page_rows[-1]
            next_cursor = _encode_history_cursor(last["created_at"], last["id"])
        return ImportHistoryPage(batches=batches, next_cursor=next_cursor)

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
                  and exists (
                      select 1 from import_batches b
                      where b.id = t.batch_id and b.user_id = t.user_id
                        and b.lifecycle_state = 'active'
                  )
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
                  and exists (
                      select 1 from import_batches b
                      where b.id = t.batch_id and b.user_id = t.user_id
                        and b.lifecycle_state = 'active'
                  )
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
                  and exists (
                      select 1 from import_batches b
                      where b.id = import_tasks.batch_id
                        and b.user_id = import_tasks.user_id
                        and b.lifecycle_state = 'active'
                  )
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
                  and exists (
                      select 1 from import_batches b
                      where b.id = import_task_events.batch_id
                        and b.user_id = import_task_events.user_id
                        and b.lifecycle_state = 'active'
                  )
                order by created_at, id
                limit ?
                """,
                (user_id, task_id, min(limit, 200)),
            ).fetchall()
            return [_event_from_row(row) for row in rows]

    def mark_batch_deleting(
        self, user_id: str, batch_id: str, now: str | None = None
    ) -> ImportBatchSummary:
        timestamp = now or _utc_now()
        with transaction(self.db_path) as conn:
            conn.execute("begin immediate")
            row = conn.execute(
                """
                select lifecycle_state from import_batches
                where id = ? and user_id = ?
                """,
                (batch_id, user_id),
            ).fetchone()
            if row is None:
                raise KeyError("import batch was not found")
            if row["lifecycle_state"] != "active":
                raise InvalidImportTransition("import batch is not active")
            nonterminal = conn.execute(
                """
                select 1 from import_tasks
                where batch_id = ? and user_id = ?
                  and status not in ('cancelled', 'succeeded', 'failed')
                limit 1
                """,
                (batch_id, user_id),
            ).fetchone()
            if nonterminal is not None:
                raise InvalidImportTransition("import batch is not fully terminal")
            updated = conn.execute(
                """
                update import_batches
                set lifecycle_state = 'deleting', delete_requested_at = ?,
                    cleanup_error_code = null, cleanup_error_summary = null,
                    updated_at = ?
                where id = ? and user_id = ? and lifecycle_state = 'active'
                """,
                (timestamp, timestamp, batch_id, user_id),
            )
            if updated.rowcount != 1:  # pragma: no cover - serialized by begin immediate
                raise InvalidImportTransition("import batch is not active")
            return self._get_batch(conn, user_id, batch_id)

    def get_deleting_batch(
        self, user_id: str, batch_id: str
    ) -> ImportBatchSummary | None:
        with connect(self.db_path) as conn:
            row = conn.execute(
                """
                select 1 from import_batches
                where id = ? and user_id = ? and lifecycle_state = 'deleting'
                """,
                (batch_id, user_id),
            ).fetchone()
            if row is None:
                return None
            return self._get_batch(conn, user_id, batch_id)

    def list_deleting_batches(self, limit: int = 20) -> list[ImportBatchSummary]:
        _validate_maintenance_limit(limit)
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """
                select id, user_id from import_batches
                where lifecycle_state = 'deleting'
                order by delete_requested_at, created_at, id
                limit ?
                """,
                (limit,),
            ).fetchall()
            return [
                self._get_batch(conn, row["user_id"], row["id"]) for row in rows
            ]

    def list_retention_candidates(
        self, cutoff: str, limit: int = 10
    ) -> list[ImportBatchSummary]:
        normalized_cutoff = _validate_history_date(cutoff, "retention cutoff")
        _validate_maintenance_limit(limit)
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """
                select b.id, b.user_id from import_batches b
                where b.lifecycle_state = 'active' and b.created_at < ?
                  and not exists (
                      select 1 from import_tasks t
                      where t.batch_id = b.id and t.user_id = b.user_id
                        and t.status not in ('cancelled', 'succeeded', 'failed')
                  )
                order by b.created_at, b.id
                limit ?
                """,
                (normalized_cutoff, limit),
            ).fetchall()
            return [
                self._get_batch(conn, row["user_id"], row["id"]) for row in rows
            ]

    def finish_batch_deletion(self, user_id: str, batch_id: str) -> None:
        with transaction(self.db_path) as conn:
            conn.execute(
                """
                delete from import_batches
                where id = ? and user_id = ? and lifecycle_state = 'deleting'
                """,
                (batch_id, user_id),
            )

    def record_batch_cleanup_failure(
        self, user_id: str, batch_id: str, error: object
    ) -> None:
        summary = _safe_import_error_summary(error)
        with transaction(self.db_path) as conn:
            conn.execute(
                """
                update import_batches
                set cleanup_error_code = 'staged_cleanup_failed',
                    cleanup_error_summary = ?
                where id = ? and user_id = ? and lifecycle_state = 'deleting'
                """,
                (summary, batch_id, user_id),
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
                  and exists (
                      select 1 from import_batches b
                      where b.id = import_tasks.batch_id
                        and b.user_id = import_tasks.user_id
                        and b.lifecycle_state = 'active'
                  )
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
                  and exists (
                      select 1 from import_batches b
                      where b.id = import_tasks.batch_id
                        and b.user_id = import_tasks.user_id
                        and b.lifecycle_state = 'active'
                  )
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
                  and exists (
                      select 1 from import_batches b
                      where b.id = import_tasks.batch_id
                        and b.user_id = import_tasks.user_id
                        and b.lifecycle_state = 'active'
                  )
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
                  and exists (
                      select 1 from import_batches b
                      where b.id = import_tasks.batch_id
                        and b.user_id = import_tasks.user_id
                        and b.lifecycle_state = 'active'
                  )
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
              and exists (
                  select 1 from import_batches b
                  where b.id = import_tasks.batch_id
                    and b.user_id = import_tasks.user_id
                    and b.lifecycle_state = 'active'
              )
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
              and exists (
                  select 1 from import_batches b
                  where b.id = import_tasks.batch_id
                    and b.user_id = import_tasks.user_id
                    and b.lifecycle_state = 'active'
              )
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
                  and exists (
                      select 1 from import_batches b
                      where b.id = import_tasks.batch_id
                        and b.user_id = import_tasks.user_id
                        and b.lifecycle_state = 'active'
                  )
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
            """
            select 1 from import_batches
            where id = ? and user_id = ? and lifecycle_state = 'active'
            """,
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


def _validate_history_filters(filters: ImportHistoryFilters) -> ImportHistoryFilters:
    if not isinstance(filters, ImportHistoryFilters):
        raise ValueError("history filters are invalid")
    statuses = filters.statuses
    if not isinstance(statuses, tuple) or any(
        not isinstance(status, str) or status not in _IMPORT_STATUSES
        for status in statuses
    ):
        raise ValueError("history status filter is invalid")
    if not isinstance(filters.filename_query, str):
        raise ValueError("history filename filter is invalid")
    if filters.batch_id is not None and (
        not isinstance(filters.batch_id, str) or not filters.batch_id
    ):
        raise ValueError("history batch filter is invalid")
    created_from = (
        _validate_history_date(filters.created_from, "created_from", end_of_day=False)
        if filters.created_from is not None
        else None
    )
    created_to = (
        _validate_history_date(filters.created_to, "created_to", end_of_day=True)
        if filters.created_to is not None
        else None
    )
    if created_from is not None and created_to is not None and created_from > created_to:
        raise ValueError("history date range is invalid")
    return ImportHistoryFilters(
        statuses=tuple(dict.fromkeys(statuses)),
        filename_query=filters.filename_query,
        created_from=created_from,
        created_to=created_to,
        batch_id=filters.batch_id,
    )


def _validate_history_date(
    value: str, field_name: str, *, end_of_day: bool = False
) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"history {field_name} date is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"history {field_name} date is invalid") from error
    if len(value) == 10:
        if end_of_day:
            parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
        parsed = parsed.replace(tzinfo=timezone.utc)
    elif parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _encode_history_cursor(created_at: str, batch_id: str) -> str:
    payload = json.dumps(
        {"created_at": created_at, "batch_id": batch_id},
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_history_cursor(cursor: str) -> dict[str, str]:
    invalid = ValueError("invalid import history cursor")
    if (
        not isinstance(cursor, str)
        or not cursor
        or not _HISTORY_CURSOR_RE.fullmatch(cursor)
    ):
        raise invalid
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.b64decode(padded, altchars=b"-_", validate=True)
        payload = json.loads(raw.decode("utf-8"))
    except (binascii.Error, ValueError, UnicodeError, json.JSONDecodeError) as error:
        raise invalid from error
    if not isinstance(payload, dict) or frozenset(payload) != _HISTORY_CURSOR_KEYS:
        raise invalid
    if any(not isinstance(payload[key], str) or not payload[key] for key in payload):
        raise invalid
    try:
        created_at = _validate_history_date(payload["created_at"], "cursor")
    except ValueError as error:
        raise invalid from error
    # Batch timestamps and cursors use one canonical UTC representation.  Do
    # not let a semantically equivalent spelling (for example ``+00:00``)
    # become a different SQLite keyset value.
    if created_at != payload["created_at"]:
        raise invalid
    return {"created_at": created_at, "batch_id": payload["batch_id"]}


def _validate_maintenance_limit(limit: int) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("maintenance limit must be between 1 and 100")


def _blocked_user_clause(
    user_ids: set[str], column: str = "user_id"
) -> tuple[str, tuple[str, ...]]:
    if not user_ids:
        return "", ()
    placeholders = ", ".join("?" for _ in user_ids)
    return f"and {column} not in ({placeholders})", tuple(user_ids)
