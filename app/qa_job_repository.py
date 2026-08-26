from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from app.database import connect
from app.qa_models import (
    QaConflictError,
    QaJob,
    QaValidationError,
    SummaryEnqueueResult,
)
from app.qa_repository import QaRepository, _utc_now


class QaJobRepository:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.qa_repository = QaRepository(self.db_path)

    def create_summary_turn_and_job(
        self,
        user_id: str,
        conversation_id: str,
        question: str,
        client_request_id: str,
        *,
        max_attempts: int = 3,
        now: str | None = None,
    ) -> SummaryEnqueueResult:
        if max_attempts < 1:
            raise QaValidationError(
                "QA_JOB_MAX_ATTEMPTS_INVALID", "max attempts must be positive"
            )
        timestamp = now or _utc_now()
        conn = connect(self.db_path)
        try:
            conn.execute("begin immediate")
            pending = self.qa_repository._create_pending_turn_in_connection(
                conn,
                user_id,
                conversation_id,
                question,
                "summary",
                client_request_id,
                retry_of_message_id=None,
                timestamp=timestamp,
            )
            existing = conn.execute(
                """
                select * from qa_jobs
                where user_id = ? and input_message_id = ?
                """,
                (user_id, pending.user_message.id),
            ).fetchone()
            if existing is not None:
                conn.commit()
                return SummaryEnqueueResult(
                    pending, _job_from_row(existing), True
                )

            job_id = str(uuid4())
            try:
                conn.execute(
                    """
                    insert into qa_jobs (
                        id, conversation_id, user_id, input_message_id,
                        assistant_message_id, status, stage, progress,
                        max_attempts, created_at, updated_at
                    ) values (
                        ?, ?, ?, ?, ?, 'queued', 'queued', 0, ?, ?, ?
                    )
                    """,
                    (
                        job_id,
                        conversation_id,
                        user_id,
                        pending.user_message.id,
                        pending.assistant_message.id,
                        max_attempts,
                        timestamp,
                        timestamp,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise QaConflictError("QA_CONVERSATION_BUSY") from exc
            row = conn.execute(
                "select * from qa_jobs where id = ?", (job_id,)
            ).fetchone()
            conn.commit()
            return SummaryEnqueueResult(pending, _job_from_row(row), False)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def claim_next(
        self,
        worker_id: str,
        *,
        lease_seconds: int,
        now: str | None = None,
    ) -> QaJob | None:
        if lease_seconds < 1:
            raise QaValidationError(
                "QA_JOB_LEASE_INVALID", "lease seconds must be positive"
            )
        timestamp = now or _utc_now()
        expires_at = _add_seconds(timestamp, lease_seconds)
        conn = connect(self.db_path)
        try:
            conn.execute("begin immediate")
            candidates = conn.execute(
                """
                select id, status, version from qa_jobs
                where attempt_count < max_attempts
                  and (
                      status = 'queued'
                      or (status = 'running' and lease_expires_at <= ?)
                  )
                order by created_at, id
                """,
                (timestamp,),
            ).fetchall()
            for candidate in candidates:
                updated = conn.execute(
                    """
                    update qa_jobs
                    set status = 'running', stage = 'starting',
                        attempt_count = attempt_count + 1,
                        lease_owner = ?, lease_expires_at = ?,
                        lease_duration_seconds = ?,
                        started_at = coalesce(started_at, ?), updated_at = ?,
                        version = version + 1
                    where id = ? and version = ? and (
                        status = 'queued'
                        or (status = 'running' and lease_expires_at <= ?)
                    )
                    """,
                    (
                        worker_id,
                        expires_at,
                        lease_seconds,
                        timestamp,
                        timestamp,
                        candidate["id"],
                        candidate["version"],
                        timestamp,
                    ),
                )
                if updated.rowcount:
                    row = conn.execute(
                        "select * from qa_jobs where id = ?", (candidate["id"],)
                    ).fetchone()
                    conn.commit()
                    return _job_from_row(row)
            conn.commit()
            return None
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def heartbeat(
        self,
        job_id: str,
        worker_id: str,
        *,
        progress: int,
        stage: str,
        now: str | None = None,
    ) -> bool:
        if not 0 <= progress <= 100:
            raise QaValidationError(
                "QA_JOB_PROGRESS_INVALID", "progress must be between 0 and 100"
            )
        timestamp = now or _utc_now()
        conn = connect(self.db_path)
        try:
            conn.execute("begin immediate")
            row = conn.execute(
                """
                select lease_duration_seconds from qa_jobs
                where id = ? and status = 'running' and lease_owner = ?
                  and lease_expires_at > ? and cancel_requested_at is null
                """,
                (job_id, worker_id, timestamp),
            ).fetchone()
            if row is None:
                conn.commit()
                return False
            expires_at = _add_seconds(timestamp, row["lease_duration_seconds"])
            updated = conn.execute(
                """
                update qa_jobs
                set progress = ?, stage = ?, lease_expires_at = ?,
                    updated_at = ?, version = version + 1
                where id = ? and status = 'running' and lease_owner = ?
                  and lease_expires_at > ? and cancel_requested_at is null
                """,
                (
                    progress,
                    str(stage),
                    expires_at,
                    timestamp,
                    job_id,
                    worker_id,
                    timestamp,
                ),
            )
            conn.commit()
            return bool(updated.rowcount)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def request_cancel(
        self,
        user_id: str,
        job_id: str,
        *,
        now: str | None = None,
    ) -> QaJob | None:
        timestamp = now or _utc_now()
        conn = connect(self.db_path)
        try:
            conn.execute("begin immediate")
            row = conn.execute(
                "select * from qa_jobs where id = ? and user_id = ?",
                (job_id, user_id),
            ).fetchone()
            if row is None:
                conn.commit()
                return None
            if row["status"] == "queued":
                conn.execute(
                    """
                    update qa_jobs
                    set status = 'cancelled', stage = 'cancelled',
                        cancel_requested_at = ?, finished_at = ?, updated_at = ?,
                        version = version + 1
                    where id = ? and user_id = ? and status = 'queued'
                    """,
                    (timestamp, timestamp, timestamp, job_id, user_id),
                )
                self._finish_assistant(
                    conn,
                    row["assistant_message_id"],
                    user_id,
                    "cancelled",
                    None,
                    None,
                    timestamp,
                )
            elif row["status"] == "running" and row["cancel_requested_at"] is None:
                conn.execute(
                    """
                    update qa_jobs
                    set cancel_requested_at = ?, updated_at = ?, version = version + 1
                    where id = ? and user_id = ? and status = 'running'
                      and cancel_requested_at is null
                    """,
                    (timestamp, timestamp, job_id, user_id),
                )
            updated_row = conn.execute(
                "select * from qa_jobs where id = ? and user_id = ?",
                (job_id, user_id),
            ).fetchone()
            conn.commit()
            return _job_from_row(updated_row)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def complete(
        self,
        job_id: str,
        worker_id: str,
        *,
        now: str | None = None,
    ) -> bool:
        timestamp = now or _utc_now()
        conn = connect(self.db_path)
        try:
            conn.execute("begin immediate")
            row = conn.execute(
                "select * from qa_jobs where id = ?", (job_id,)
            ).fetchone()
            if not self._owns_live_lease(row, worker_id, timestamp):
                conn.commit()
                return False
            if row["cancel_requested_at"] is not None:
                self._cancel_running(conn, row, timestamp)
                conn.commit()
                return False
            updated = conn.execute(
                """
                update qa_jobs
                set status = 'completed', stage = 'completed', progress = 100,
                    lease_owner = null, lease_expires_at = null,
                    lease_duration_seconds = null, finished_at = ?, updated_at = ?,
                    version = version + 1
                where id = ? and status = 'running' and lease_owner = ?
                  and lease_expires_at > ? and cancel_requested_at is null
                  and exists (
                      select 1 from qa_messages
                      where id = qa_jobs.assistant_message_id
                        and user_id = qa_jobs.user_id
                        and status = 'completed'
                  )
                """,
                (timestamp, timestamp, job_id, worker_id, timestamp),
            )
            conn.commit()
            return bool(updated.rowcount)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def fail_or_retry(
        self,
        job_id: str,
        worker_id: str,
        error_code: str,
        trace_id: str | None,
        *,
        now: str | None = None,
    ) -> QaJob:
        timestamp = now or _utc_now()
        conn = connect(self.db_path)
        try:
            conn.execute("begin immediate")
            row = conn.execute(
                "select * from qa_jobs where id = ?", (job_id,)
            ).fetchone()
            if not self._owns_live_lease(row, worker_id, timestamp):
                raise QaConflictError("QA_JOB_LEASE_LOST")
            if row["cancel_requested_at"] is not None:
                self._cancel_running(conn, row, timestamp)
            elif row["attempt_count"] < row["max_attempts"]:
                conn.execute(
                    """
                    update qa_jobs
                    set status = 'queued', stage = 'queued', progress = 0,
                        lease_owner = null, lease_expires_at = null,
                        lease_duration_seconds = null, safe_error_code = ?,
                        trace_id = ?, updated_at = ?, version = version + 1
                    where id = ? and status = 'running' and lease_owner = ?
                      and lease_expires_at > ?
                    """,
                    (error_code, trace_id, timestamp, job_id, worker_id, timestamp),
                )
            else:
                conn.execute(
                    """
                    update qa_jobs
                    set status = 'failed', stage = 'failed',
                        lease_owner = null, lease_expires_at = null,
                        lease_duration_seconds = null, safe_error_code = ?,
                        trace_id = ?, finished_at = ?, updated_at = ?,
                        version = version + 1
                    where id = ? and status = 'running' and lease_owner = ?
                      and lease_expires_at > ?
                    """,
                    (
                        error_code,
                        trace_id,
                        timestamp,
                        timestamp,
                        job_id,
                        worker_id,
                        timestamp,
                    ),
                )
                self._finish_assistant(
                    conn,
                    row["assistant_message_id"],
                    row["user_id"],
                    "failed",
                    error_code,
                    trace_id,
                    timestamp,
                )
            updated_row = conn.execute(
                "select * from qa_jobs where id = ?", (job_id,)
            ).fetchone()
            conn.commit()
            return _job_from_row(updated_row)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def recover_expired(self, *, now: str | None = None) -> int:
        timestamp = now or _utc_now()
        conn = connect(self.db_path)
        try:
            conn.execute("begin immediate")
            rows = conn.execute(
                """
                select * from qa_jobs
                where status = 'running' and lease_expires_at <= ?
                order by created_at, id
                """,
                (timestamp,),
            ).fetchall()
            for row in rows:
                if row["cancel_requested_at"] is not None:
                    self._cancel_running(conn, row, timestamp)
                elif row["attempt_count"] < row["max_attempts"]:
                    conn.execute(
                        """
                        update qa_jobs
                        set status = 'queued', stage = 'queued', progress = 0,
                            lease_owner = null, lease_expires_at = null,
                            lease_duration_seconds = null, updated_at = ?,
                            version = version + 1
                        where id = ? and status = 'running'
                          and lease_expires_at <= ?
                        """,
                        (timestamp, row["id"], timestamp),
                    )
                else:
                    conn.execute(
                        """
                        update qa_jobs
                        set status = 'failed', stage = 'failed',
                            lease_owner = null, lease_expires_at = null,
                            lease_duration_seconds = null,
                            safe_error_code = 'QA_JOB_INTERRUPTED',
                            finished_at = ?, updated_at = ?, version = version + 1
                        where id = ? and status = 'running'
                          and lease_expires_at <= ?
                        """,
                        (timestamp, timestamp, row["id"], timestamp),
                    )
                    self._finish_assistant(
                        conn,
                        row["assistant_message_id"],
                        row["user_id"],
                        "failed",
                        "QA_JOB_INTERRUPTED",
                        None,
                        timestamp,
                    )
            conn.commit()
            return len(rows)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get(self, user_id: str, job_id: str) -> QaJob | None:
        with connect(self.db_path) as conn:
            row = conn.execute(
                "select * from qa_jobs where id = ? and user_id = ?",
                (job_id, user_id),
            ).fetchone()
            return _job_from_row(row) if row is not None else None

    @staticmethod
    def _owns_live_lease(
        row: sqlite3.Row | None, worker_id: str, timestamp: str
    ) -> bool:
        return bool(
            row is not None
            and row["status"] == "running"
            and row["lease_owner"] == worker_id
            and row["lease_expires_at"] is not None
            and row["lease_expires_at"] > timestamp
        )

    def _cancel_running(
        self, conn: sqlite3.Connection, row: sqlite3.Row, timestamp: str
    ) -> None:
        conn.execute(
            """
            update qa_jobs
            set status = 'cancelled', stage = 'cancelled',
                lease_owner = null, lease_expires_at = null,
                lease_duration_seconds = null, finished_at = ?, updated_at = ?,
                version = version + 1
            where id = ? and status = 'running'
            """,
            (timestamp, timestamp, row["id"]),
        )
        self._finish_assistant(
            conn,
            row["assistant_message_id"],
            row["user_id"],
            "cancelled",
            None,
            None,
            timestamp,
        )

    @staticmethod
    def _finish_assistant(
        conn: sqlite3.Connection,
        assistant_message_id: str,
        user_id: str,
        status: str,
        error_code: str | None,
        trace_id: str | None,
        timestamp: str,
    ) -> None:
        conn.execute(
            """
            update qa_messages
            set status = ?, safe_error_code = ?, trace_id = ?,
                memory_sync_status = 'not_required', version = version + 1,
                completed_at = ?, updated_at = ?
            where id = ? and user_id = ? and role = 'assistant'
              and status = 'pending'
            """,
            (
                status,
                error_code,
                trace_id,
                timestamp,
                timestamp,
                assistant_message_id,
                user_id,
            ),
        )


def _add_seconds(timestamp: str, seconds: int) -> str:
    parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (parsed + timedelta(seconds=seconds)).astimezone(timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


def _job_from_row(row: sqlite3.Row) -> QaJob:
    return QaJob(
        id=row["id"],
        conversation_id=row["conversation_id"],
        user_id=row["user_id"],
        input_message_id=row["input_message_id"],
        assistant_message_id=row["assistant_message_id"],
        status=row["status"],
        stage=row["stage"],
        progress=row["progress"],
        cancel_requested_at=row["cancel_requested_at"],
        attempt_count=row["attempt_count"],
        max_attempts=row["max_attempts"],
        lease_owner=row["lease_owner"],
        lease_expires_at=row["lease_expires_at"],
        lease_duration_seconds=row["lease_duration_seconds"],
        safe_error_code=row["safe_error_code"],
        trace_id=row["trace_id"],
        version=row["version"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        updated_at=row["updated_at"],
    )
