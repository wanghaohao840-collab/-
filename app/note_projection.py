from __future__ import annotations

import threading
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from app.database import connect
from app.note_models import Note, NoteProjectionTask
from app.note_repository import NoteRepository, utc_now
from app.runtime import UserRuntime, UserRuntimeRegistry


class NoteProjectionRepository:
    def __init__(
        self,
        db_path: Path | str,
        *,
        max_attempts: int = 3,
        retry_delay_seconds: int = 5,
    ) -> None:
        self.db_path = Path(db_path)
        self.max_attempts = max_attempts
        self.retry_delay_seconds = retry_delay_seconds

    def claim_next(
        self,
        owner: str,
        *,
        now: str | None = None,
        lease_seconds: int = 30,
    ) -> NoteProjectionTask | None:
        timestamp = now or utc_now()
        conn = connect(self.db_path)
        try:
            conn.execute("begin immediate")
            self._recover_expired(conn, timestamp)
            row = conn.execute(
                """
                select id from note_projection_tasks
                where status='queued' and available_at<=?
                order by created_at,id limit 1
                """,
                (timestamp,),
            ).fetchone()
            if row is None:
                conn.commit()
                return None
            lease_expires_at = _add_seconds(timestamp, lease_seconds)
            result = conn.execute(
                """
                update note_projection_tasks set status='running',attempt_count=attempt_count+1,
                    lease_owner=?,lease_expires_at=?
                where id=? and status='queued' and available_at<=?
                """,
                (owner, lease_expires_at, row["id"], timestamp),
            )
            if not result.rowcount:
                conn.commit()
                return None
            task = self._get_in_connection(conn, row["id"])
            conn.commit()
            return task
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def heartbeat(
        self,
        task_id: str,
        owner: str,
        *,
        now: str | None = None,
        lease_seconds: int = 30,
    ) -> bool:
        timestamp = now or utc_now()
        with connect(self.db_path) as conn:
            result = conn.execute(
                """
                update note_projection_tasks set lease_expires_at=?
                where id=? and status='running' and lease_owner=?
                  and lease_expires_at>=?
                """,
                (_add_seconds(timestamp, lease_seconds), task_id, owner, timestamp),
            )
            return bool(result.rowcount)

    def complete(
        self, task_id: str, owner: str, *, now: str | None = None
    ) -> bool:
        timestamp = now or utc_now()
        conn = connect(self.db_path)
        try:
            conn.execute("begin immediate")
            row = conn.execute(
                """
                select user_id,note_id,note_version from note_projection_tasks
                where id=? and status='running' and lease_owner=? and lease_expires_at>=?
                """,
                (task_id, owner, timestamp),
            ).fetchone()
            if row is None:
                conn.commit()
                return False
            conn.execute(
                """
                update note_projection_tasks set status='completed',lease_owner=null,
                    lease_expires_at=null,last_error_code=null,finished_at=? where id=?
                """,
                (timestamp, task_id),
            )
            pending_newer = conn.execute(
                """
                select 1 from note_projection_tasks
                where user_id=? and note_id=? and note_version>?
                  and status in ('queued','running','failed') limit 1
                """,
                (row["user_id"], row["note_id"], row["note_version"]),
            ).fetchone()
            if pending_newer is None:
                conn.execute(
                    """
                    update notes set projection_state='ready'
                    where id=? and user_id=? and version=?
                    """,
                    (row["note_id"], row["user_id"], row["note_version"]),
                )
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def fail_or_retry(
        self,
        task_id: str,
        owner: str,
        error_code: str,
        *,
        now: str | None = None,
    ) -> bool:
        timestamp = now or utc_now()
        conn = connect(self.db_path)
        try:
            conn.execute("begin immediate")
            row = conn.execute(
                """
                select * from note_projection_tasks
                where id=? and status='running' and lease_owner=? and lease_expires_at>=?
                """,
                (task_id, owner, timestamp),
            ).fetchone()
            if row is None:
                conn.commit()
                return False
            if row["attempt_count"] >= self.max_attempts:
                conn.execute(
                    """
                    update note_projection_tasks set status='failed',lease_owner=null,
                        lease_expires_at=null,last_error_code=?,finished_at=? where id=?
                    """,
                    (error_code, timestamp, task_id),
                )
                conn.execute(
                    """
                    update notes set projection_state='failed'
                    where id=? and user_id=? and version=?
                    """,
                    (row["note_id"], row["user_id"], row["note_version"]),
                )
            else:
                conn.execute(
                    """
                    update note_projection_tasks set status='queued',available_at=?,
                        lease_owner=null,lease_expires_at=null,last_error_code=? where id=?
                    """,
                    (_add_seconds(timestamp, self.retry_delay_seconds), error_code, task_id),
                )
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def recover_expired(self, *, now: str | None = None) -> int:
        timestamp = now or utc_now()
        conn = connect(self.db_path)
        try:
            conn.execute("begin immediate")
            recovered = self._recover_expired(conn, timestamp)
            conn.commit()
            return recovered
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _recover_expired(self, conn, timestamp: str) -> int:
        rows = conn.execute(
            """
            select * from note_projection_tasks
            where status='running' and lease_expires_at<=?
            """,
            (timestamp,),
        ).fetchall()
        for row in rows:
            if row["attempt_count"] >= self.max_attempts:
                conn.execute(
                    """
                    update note_projection_tasks set status='failed',lease_owner=null,
                        lease_expires_at=null,last_error_code='PROJECTION_LEASE_EXPIRED',
                        finished_at=? where id=?
                    """,
                    (timestamp, row["id"]),
                )
                conn.execute(
                    "update notes set projection_state='failed' where id=? and user_id=? and version=?",
                    (row["note_id"], row["user_id"], row["note_version"]),
                )
            else:
                conn.execute(
                    """
                    update note_projection_tasks set status='queued',available_at=?,
                        lease_owner=null,lease_expires_at=null,
                        last_error_code='PROJECTION_LEASE_EXPIRED' where id=?
                    """,
                    (timestamp, row["id"]),
                )
        return len(rows)

    def get(self, task_id: str) -> NoteProjectionTask | None:
        with connect(self.db_path) as conn:
            row = conn.execute(
                "select 1 from note_projection_tasks where id=?", (task_id,)
            ).fetchone()
            return self._get_in_connection(conn, task_id) if row is not None else None

    def list_for_note(self, user_id: str, note_id: str) -> tuple[NoteProjectionTask, ...]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "select * from note_projection_tasks where user_id=? and note_id=? order by note_version,created_at",
                (user_id, note_id),
            ).fetchall()
        return tuple(_task_from_row(row) for row in rows)

    @staticmethod
    def _get_in_connection(conn, task_id: str) -> NoteProjectionTask:
        return _task_from_row(
            conn.execute("select * from note_projection_tasks where id=?", (task_id,)).fetchone()
        )

    def mark_legacy_cleaned(
        self,
        task_id: str,
        owner: str,
        user_id: str,
        note_id: str,
        legacy_memory_id: str,
        *,
        now: str | None = None,
    ) -> bool:
        timestamp = now or utc_now()
        conn = connect(self.db_path)
        try:
            conn.execute("begin immediate")
            owned = conn.execute(
                """
                select 1 from note_projection_tasks
                where id=? and user_id=? and note_id=? and status='running'
                  and lease_owner=? and lease_expires_at>=?
                """,
                (task_id, user_id, note_id, owner, timestamp),
            ).fetchone()
            if owned is None:
                conn.commit()
                return False
            result = conn.execute(
                """
                update note_legacy_imports set legacy_memory_cleaned_at=?
                where user_id=? and note_id=? and legacy_memory_id=?
                  and legacy_memory_cleaned_at is null
                """,
                (timestamp, user_id, note_id, legacy_memory_id),
            )
            conn.commit()
            return bool(result.rowcount)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def legacy_memory_to_clean(self, user_id: str, note_id: str) -> str | None:
        with connect(self.db_path) as conn:
            row = conn.execute(
                """
                select legacy_memory_id from note_legacy_imports
                where user_id=? and note_id=? and legacy_memory_id is not null
                  and legacy_memory_cleaned_at is null
                """,
                (user_id, note_id),
            ).fetchone()
        return row["legacy_memory_id"] if row is not None else None


class NoteMemoryProjection(Protocol):
    def upsert(self, runtime: UserRuntime, note: Note) -> None: ...

    def remove(self, runtime: UserRuntime, note_id: str) -> None: ...


class DefaultNoteMemoryProjection:
    def upsert(self, runtime: UserRuntime, note: Note) -> None:
        memory_id = f"note:{note.user_id}:{note.id}"
        runtime.memory_tool.memory_manager.add_memory(
            content=note.body_markdown,
            memory_type="semantic",
            importance=0.85,
            metadata={
                "user_id": note.user_id,
                "note_id": note.id,
                "version": note.version,
                "concept": note.concept or "",
                "tags": list(note.tags),
                "knowledge_type": "learning_note",
            },
            memory_id=memory_id,
        )

    def remove(self, runtime: UserRuntime, note_id: str) -> None:
        runtime.memory_tool.memory_manager.remove_memory(
            f"note:{runtime.user_id}:{note_id}", memory_type="semantic"
        )


class NoteProjectionWorker:
    def __init__(
        self,
        task_repository: NoteProjectionRepository,
        note_repository: NoteRepository,
        runtime_registry: UserRuntimeRegistry,
        projection: NoteMemoryProjection,
        *,
        worker_id: str | None = None,
        poll_seconds: float = 1.0,
        lease_seconds: int = 30,
    ) -> None:
        self.tasks = task_repository
        self.notes = note_repository
        self.runtime_registry = runtime_registry
        self.projection = projection
        self.worker_id = worker_id or f"notes-{uuid4()}"
        self.poll_seconds = poll_seconds
        self.lease_seconds = lease_seconds
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self.tasks.recover_expired()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name=f"note-projection-{self.worker_id}", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
            if thread.is_alive():
                raise RuntimeError("Note projection worker did not stop")
        self._thread = None

    def notify(self) -> None:
        self._wake.set()

    def run_once(self) -> bool:
        task = self.tasks.claim_next(
            self.worker_id, lease_seconds=self.lease_seconds
        )
        if task is None:
            return False
        runtime = self.runtime_registry.acquire_background(task.user_id)
        try:
            lock = getattr(runtime, "lock", None)
            with lock if lock is not None else nullcontext():
                if not self.tasks.heartbeat(
                    task.id,
                    self.worker_id,
                    lease_seconds=self.lease_seconds,
                ):
                    return True
                note = self.notes.get_including_deleted(task.user_id, task.note_id)
                if task.operation == "upsert":
                    if (
                        note is None
                        or note.deleted_at is not None
                        or note.version != task.note_version
                    ):
                        self.tasks.complete(task.id, self.worker_id)
                        return True
                    self.projection.upsert(runtime, note)
                    legacy_id = self.tasks.legacy_memory_to_clean(
                        task.user_id, task.note_id
                    )
                    if legacy_id is not None:
                        if not self.tasks.heartbeat(
                            task.id,
                            self.worker_id,
                            lease_seconds=self.lease_seconds,
                        ):
                            return True
                        runtime.memory_tool.memory_manager.remove_memory(
                            legacy_id, memory_type="semantic"
                        )
                        self.tasks.mark_legacy_cleaned(
                            task.id,
                            self.worker_id,
                            task.user_id,
                            task.note_id,
                            legacy_id,
                        )
                else:
                    if (
                        note is not None
                        and note.deleted_at is None
                        and note.version > task.note_version
                    ):
                        self.tasks.complete(task.id, self.worker_id)
                        return True
                    self.projection.remove(runtime, task.note_id)
                self.tasks.complete(task.id, self.worker_id)
        except Exception:
            self.tasks.fail_or_retry(
                task.id, self.worker_id, "PROJECTION_FAILED"
            )
        finally:
            self.runtime_registry.release_background(task.user_id)
        return True

    def _loop(self) -> None:
        while not self._stop.is_set():
            if self.run_once():
                continue
            self._wake.wait(self.poll_seconds)
            self._wake.clear()


def _task_from_row(row) -> NoteProjectionTask:
    return NoteProjectionTask(
        id=row["id"],
        user_id=row["user_id"],
        note_id=row["note_id"],
        note_version=row["note_version"],
        operation=row["operation"],
        status=row["status"],
        attempt_count=row["attempt_count"],
        available_at=row["available_at"],
        lease_owner=row["lease_owner"],
        lease_expires_at=row["lease_expires_at"],
        last_error_code=row["last_error_code"],
        created_at=row["created_at"],
        finished_at=row["finished_at"],
    )


def _add_seconds(timestamp: str, seconds: int) -> str:
    parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (parsed + timedelta(seconds=seconds)).astimezone(timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )
