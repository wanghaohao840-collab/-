from __future__ import annotations

from pathlib import Path
from threading import RLock
from types import SimpleNamespace

from app.database import connect, initialize_database
from app.note_projection import (
    DefaultNoteMemoryProjection,
    NoteProjectionRepository,
    NoteProjectionWorker,
)
from app.note_repository import NoteRepository


class RuntimeRegistry:
    def __init__(self, runtime):
        self.runtime = runtime

    def acquire_background(self, user_id):
        assert user_id == self.runtime.user_id
        return self.runtime

    def release_background(self, user_id):
        assert user_id == self.runtime.user_id


class FakeManager:
    def __init__(self):
        self.calls = []

    def add_memory(self, **kwargs):
        self.calls.append(("upsert", kwargs["memory_id"], kwargs))
        return kwargs["memory_id"]

    def remove_memory(self, memory_id, *, memory_type):
        self.calls.append(("remove", memory_id, memory_type))
        return True


def setup_domain(tmp_path: Path):
    db = tmp_path / "app.db"
    initialize_database(db)
    with connect(db) as conn:
        conn.execute("insert into users values ('alice','alice','alice','x','active','t','t')")
    notes = NoteRepository(db)
    tasks = NoteProjectionRepository(db, max_attempts=3, retry_delay_seconds=0)
    return db, notes, tasks


def test_expired_final_attempt_is_terminalized_and_stale_owner_rejected(tmp_path: Path) -> None:
    db, notes, tasks = setup_domain(tmp_path)
    notes.create("alice", "body", None, (), "r", now="2026-01-01T00:00:00Z")
    claimed = tasks.claim_next("old", now="2026-01-01T00:00:01Z", lease_seconds=1)
    assert claimed is not None
    with connect(db) as conn:
        conn.execute("update note_projection_tasks set attempt_count=3, lease_expires_at='2026-01-01T00:00:00Z'")

    assert tasks.claim_next("new", now="2026-01-01T00:00:02Z") is None
    assert tasks.get(claimed.id).status == "failed"
    assert tasks.complete(claimed.id, "old", now="2026-01-01T00:00:02Z") is False


def test_reclaimed_task_cannot_be_completed_by_stale_worker(tmp_path: Path) -> None:
    _, notes, tasks = setup_domain(tmp_path)
    notes.create("alice", "body", None, (), "r", now="2026-01-01T00:00:00Z")
    old = tasks.claim_next("old", now="2026-01-01T00:00:00Z", lease_seconds=1)
    replacement = tasks.claim_next("new", now="2026-01-01T00:00:02Z")
    assert old is not None and replacement is not None
    assert tasks.complete(old.id, "old", now="2026-01-01T00:00:03Z") is False
    assert tasks.complete(replacement.id, "new", now="2026-01-01T00:00:03Z") is True


def test_worker_uses_stable_id_and_cleans_exact_legacy_only_after_upsert(tmp_path: Path) -> None:
    db, notes, tasks = setup_domain(tmp_path)
    note = notes.create("alice", "body", "c", ("tag",), "r")
    with connect(db) as conn:
        conn.execute(
            "insert into note_legacy_imports (user_id, legacy_import_key, note_id, source_digest, legacy_memory_id, imported_at) values ('alice','k',?,'d','legacy-id','t')",
            (note.id,),
        )
    manager = FakeManager()
    runtime = SimpleNamespace(user_id="alice", lock=RLock(), memory_tool=SimpleNamespace(memory_manager=manager))
    worker = NoteProjectionWorker(
        tasks, notes, RuntimeRegistry(runtime), DefaultNoteMemoryProjection(), worker_id="worker"
    )

    assert worker.run_once() is True
    assert [(call[0], call[1]) for call in manager.calls] == [
        ("upsert", f"note:alice:{note.id}"), ("remove", "legacy-id")
    ]
    assert tasks.list_for_note("alice", note.id)[0].status == "completed"
    with connect(db) as conn:
        assert conn.execute("select legacy_memory_cleaned_at from note_legacy_imports").fetchone()[0]


def test_stale_upsert_after_delete_is_noop_and_cannot_revive(tmp_path: Path) -> None:
    _, notes, tasks = setup_domain(tmp_path)
    note = notes.create("alice", "body", None, (), "r")
    notes.soft_delete("alice", note.id, expected_version=1)
    manager = FakeManager()
    runtime = SimpleNamespace(user_id="alice", lock=RLock(), memory_tool=SimpleNamespace(memory_manager=manager))
    worker = NoteProjectionWorker(tasks, notes, RuntimeRegistry(runtime), DefaultNoteMemoryProjection(), worker_id="w")

    assert worker.run_once() is True
    assert manager.calls == []
    assert worker.run_once() is True
    assert manager.calls == [("remove", f"note:alice:{note.id}", "semantic")]


def test_failure_does_not_change_note_fact_and_retry_is_bounded(tmp_path: Path) -> None:
    _, notes, tasks = setup_domain(tmp_path)
    note = notes.create("alice", "body", None, (), "r")
    runtime = SimpleNamespace(user_id="alice", lock=RLock())

    class Broken:
        def upsert(self, runtime, note):
            raise OSError("secret path")
        def remove(self, runtime, note_id):
            raise OSError("secret path")

    worker = NoteProjectionWorker(tasks, notes, RuntimeRegistry(runtime), Broken(), worker_id="w")
    for _ in range(3):
        assert worker.run_once() is True
    assert notes.get("alice", note.id).body_markdown == "body"
    assert notes.get("alice", note.id).projection_state == "failed"
    assert tasks.list_for_note("alice", note.id)[0].last_error_code == "PROJECTION_FAILED"
    assert tasks.claim_next("later") is None


def test_worker_does_not_project_after_heartbeat_ownership_loss(tmp_path: Path) -> None:
    _, notes, tasks = setup_domain(tmp_path)
    notes.create("alice", "body", None, (), "r")
    manager = FakeManager()
    runtime = SimpleNamespace(
        user_id="alice", lock=RLock(), memory_tool=SimpleNamespace(memory_manager=manager)
    )
    tasks.heartbeat = lambda *args, **kwargs: False  # type: ignore[method-assign]
    worker = NoteProjectionWorker(
        tasks,
        notes,
        RuntimeRegistry(runtime),
        DefaultNoteMemoryProjection(),
        worker_id="lost-owner",
    )

    assert worker.run_once() is True
    assert manager.calls == []
