from __future__ import annotations

import os
import sqlite3
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.auth import AuthService
import app.database as database_module
from app.database import connect, initialize_database
from app.import_maintenance import ImportHistoryMaintenance
from app.import_models import ImportTaskCreate
from app.import_repository import ImportTaskRepository, InvalidImportTransition
from app.import_service import ImportTaskService
from app.import_worker import ImportWorkerPool, parse_import_task_retention_days
from app.storage import UnsafePathError, UserStorage


NOW = "2026-08-20T12:00:00Z"


def _maintenance_harness(tmp_path):
    db_path = tmp_path / "app.db"
    initialize_database(db_path)
    auth = AuthService(db_path)
    user_a = auth.register("maintenance-a", "correct horse battery").id
    user_b = auth.register("maintenance-b", "correct horse battery").id
    repository = ImportTaskRepository(db_path)
    storage = UserStorage(tmp_path / "data")
    return repository, storage, ImportHistoryMaintenance(repository, storage), user_a, user_b


def _create_batch(
    repository,
    storage,
    user_id,
    *,
    status="failed",
    created_at=NOW,
    staged=True,
):
    batch_id, task_id, document_id = (str(uuid.uuid4()) for _ in range(3))
    relative = f"imports/{batch_id}/{task_id}.md"
    repository.create_batch(
        user_id,
        [
            ImportTaskCreate(
                task_id=task_id,
                batch_id=batch_id,
                user_id=user_id,
                document_id=document_id,
                original_name="source.md",
                file_suffix=".md",
                size_bytes=4,
                staged_relative_path=relative,
            )
        ],
        now=created_at,
    )
    if staged:
        path = storage.staged_import_path(user_id, batch_id, task_id, ".md")
        path.write_bytes(b"body")
    else:
        path = storage.user_paths(user_id).root / relative
    if status != "queued":
        with connect(repository.db_path) as connection:
            connection.execute(
                """
                update import_tasks set status = ?, stage = ?,
                    progress = ?, finished_at = ?, updated_at = ?
                where id = ? and user_id = ?
                """,
                (
                    status,
                    status,
                    100 if status == "succeeded" else 0,
                    created_at,
                    created_at,
                    task_id,
                    user_id,
                ),
            )
            connection.execute(
                """
                insert into import_task_events (
                    batch_id, task_id, user_id, event_type, status, stage,
                    message, created_at
                ) values (?, ?, ?, 'finished', ?, ?, null, ?)
                """,
                (batch_id, task_id, user_id, status, status, created_at),
            )
    return batch_id, task_id, document_id, path


def test_nonterminal_batch_cannot_be_marked_deleting(tmp_path):
    repository, storage, _, user_a, _ = _maintenance_harness(tmp_path)
    batch_id, *_ = _create_batch(repository, storage, user_a, status="queued")

    with pytest.raises(InvalidImportTransition, match="terminal"):
        repository.mark_batch_deleting(user_a, batch_id)

    assert repository.get_batch(user_a, batch_id).lifecycle_state == "active"


@pytest.mark.parametrize("status", ["failed", "cancelled", "succeeded"])
def test_delete_removes_only_staging_and_history_rows_and_preserves_formal_document(
    tmp_path, status
):
    repository, storage, maintenance, user_a, _ = _maintenance_harness(tmp_path)
    batch_id, task_id, document_id, staged = _create_batch(
        repository, storage, user_a, status=status
    )
    formal = storage.document_path(user_a, document_id, ".md")
    formal.write_text("formal document must remain", encoding="utf-8")
    history = storage.user_paths(user_a).history
    history.write_text('{"notes":["must remain"]}', encoding="utf-8")
    report = storage.report_path(user_a, str(uuid.uuid4()))
    report.write_text("must remain", encoding="utf-8")

    maintenance.delete_batch(user_a, batch_id)

    assert repository.get_batch(user_a, batch_id) is None
    assert repository.get_task(user_a, task_id) is None
    assert repository.list_task_events(user_a, task_id) == []
    assert not staged.exists()
    assert formal.read_text(encoding="utf-8") == "formal document must remain"
    assert history.read_text(encoding="utf-8") == '{"notes":["must remain"]}'
    assert report.read_text(encoding="utf-8") == "must remain"


def test_recovery_resumes_marked_batch_without_remarking_and_missing_file_is_idempotent(
    tmp_path, monkeypatch
):
    repository, storage, maintenance, user_a, _ = _maintenance_harness(tmp_path)
    batch_id, _, _, staged = _create_batch(
        repository, storage, user_a, status="failed", staged=False
    )
    repository.mark_batch_deleting(user_a, batch_id, now=NOW)
    calls = 0

    def forbidden_remark(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("recovery must not re-mark a deleting batch")

    monkeypatch.setattr(repository, "mark_batch_deleting", forbidden_remark)

    assert not staged.exists()
    assert maintenance.recover_deleting(limit=20) == 1
    assert calls == 0
    assert repository.get_batch(user_a, batch_id) is None
    assert maintenance.resume_batch_deletion(user_a, batch_id) is None


def test_unsafe_recorded_staging_path_keeps_deleting_and_persists_safe_bounded_error(
    tmp_path,
):
    repository, storage, maintenance, user_a, _ = _maintenance_harness(tmp_path)
    batch_id, task_id, *_ = _create_batch(
        repository, storage, user_a, status="failed"
    )
    with connect(repository.db_path) as connection:
        connection.execute(
            "update import_tasks set staged_relative_path = ? where id = ? and user_id = ?",
            ("../documents/private-token.md", task_id, user_a),
        )
    repository.mark_batch_deleting(user_a, batch_id, now=NOW)

    with pytest.raises(ValueError, match="does not match"):
        maintenance.resume_batch_deletion(user_a, batch_id)

    with connect(repository.db_path) as connection:
        row = connection.execute(
            """
            select lifecycle_state, cleanup_error_code, cleanup_error_summary
            from import_batches where id = ? and user_id = ?
            """,
            (batch_id, user_a),
        ).fetchone()
    assert row["lifecycle_state"] == "deleting"
    assert row["cleanup_error_code"] == "staged_cleanup_failed"
    assert len(row["cleanup_error_summary"]) <= 500
    assert "private-token" not in row["cleanup_error_summary"]
    assert "documents" not in row["cleanup_error_summary"]


def test_batch_deletion_is_cross_user_scoped(tmp_path):
    repository, storage, maintenance, user_a, user_b = _maintenance_harness(tmp_path)
    batch_id, _, _, staged = _create_batch(
        repository, storage, user_a, status="failed"
    )

    with pytest.raises(KeyError):
        maintenance.delete_batch(user_b, batch_id)

    assert repository.get_batch(user_a, batch_id) is not None
    assert staged.exists()


def test_mark_deleting_fails_within_a_bounded_time_when_sqlite_writer_is_busy(
    tmp_path, monkeypatch
):
    repository, storage, _, user_a, _ = _maintenance_harness(tmp_path)
    batch_id, *_ = _create_batch(repository, storage, user_a, status="failed")
    writer = sqlite3.connect(repository.db_path)
    writer.execute("begin immediate")
    original_connect = database_module.connect

    def short_timeout_connect(path):
        connection = sqlite3.connect(path, timeout=0.01)
        connection.row_factory = sqlite3.Row
        connection.execute("pragma foreign_keys = on")
        return connection

    monkeypatch.setattr(database_module, "connect", short_timeout_connect)
    started = time.monotonic()
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked|busy"):
            repository.mark_batch_deleting(user_a, batch_id)
    finally:
        writer.rollback()
        writer.close()
        monkeypatch.setattr(database_module, "connect", original_connect)

    assert time.monotonic() - started < 1
    assert repository.get_batch(user_a, batch_id).lifecycle_state == "active"


def _replace_batch_with_link(storage, user_id, batch_id, target):
    batch = storage.user_paths(user_id).imports / batch_id
    for child in batch.iterdir():
        child.unlink()
    batch.rmdir()
    try:
        batch.symlink_to(target, target_is_directory=True)
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"symbolic links are unavailable: {error}")


def test_maintenance_rejects_symlinked_staging_and_preserves_formal_document(tmp_path):
    repository, storage, maintenance, user_a, _ = _maintenance_harness(tmp_path)
    batch_id, _, document_id, _ = _create_batch(
        repository, storage, user_a, status="succeeded"
    )
    formal = storage.document_path(user_a, document_id, ".md")
    formal.write_text("formal", encoding="utf-8")
    _replace_batch_with_link(storage, user_a, batch_id, formal.parent)
    repository.mark_batch_deleting(user_a, batch_id)

    with pytest.raises(UnsafePathError, match="link or reparse"):
        maintenance.resume_batch_deletion(user_a, batch_id)

    assert formal.read_text(encoding="utf-8") == "formal"
    assert repository.get_deleting_batch(user_a, batch_id) is not None


@pytest.mark.skipif(os.name != "nt", reason="Windows junction regression")
def test_maintenance_rejects_junction_staging_and_preserves_formal_document(tmp_path):
    repository, storage, maintenance, user_a, _ = _maintenance_harness(tmp_path)
    batch_id, _, document_id, staged = _create_batch(
        repository, storage, user_a, status="succeeded"
    )
    formal = storage.document_path(user_a, document_id, ".md")
    formal.write_text("formal", encoding="utf-8")
    staged.unlink()
    staged.parent.rmdir()
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(staged.parent), str(formal.parent)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip("Windows junction creation is unavailable")
    repository.mark_batch_deleting(user_a, batch_id)

    with pytest.raises(UnsafePathError, match="link or reparse"):
        maintenance.resume_batch_deletion(user_a, batch_id)

    assert formal.read_text(encoding="utf-8") == "formal"
    assert repository.get_deleting_batch(user_a, batch_id) is not None


def test_retention_disabled_threshold_and_per_pass_limit(tmp_path):
    repository, storage, maintenance, user_a, _ = _maintenance_harness(tmp_path)
    old = [
        _create_batch(
            repository,
            storage,
            user_a,
            status="failed",
            created_at=f"2026-07-{day:02d}T00:00:00Z",
        )[0]
        for day in range(1, 13)
    ]
    recent, *_ = _create_batch(
        repository,
        storage,
        user_a,
        status="failed",
        created_at="2026-08-19T00:00:00Z",
    )
    now = datetime(2026, 8, 20, 12, tzinfo=timezone.utc)

    assert maintenance.run_retention(0, limit=10, now=now) == 0
    assert all(repository.get_batch(user_a, batch_id) for batch_id in (*old, recent))
    assert maintenance.run_retention(30, limit=10, now=now) == 10
    assert sum(repository.get_batch(user_a, batch_id) is None for batch_id in old) == 10
    assert repository.get_batch(user_a, recent) is not None


@pytest.mark.parametrize("value", [None, "", "0"])
def test_retention_env_default_is_disabled(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("IMPORT_TASK_RETENTION_DAYS", raising=False)
    else:
        monkeypatch.setenv("IMPORT_TASK_RETENTION_DAYS", value)

    assert parse_import_task_retention_days() == 0


@pytest.mark.parametrize("value", ["-1", "+1", "1.5", " 1", "1 ", "abc"])
def test_invalid_retention_env_raises_exact_safe_message(monkeypatch, value):
    monkeypatch.setenv("IMPORT_TASK_RETENTION_DAYS", value)

    with pytest.raises(ValueError) as error:
        parse_import_task_retention_days()

    assert str(error.value) == "IMPORT_TASK_RETENTION_DAYS must be a non-negative integer"


def test_worker_start_recovery_failure_is_safely_logged_and_does_not_block_scheduler(
    tmp_path, caplog
):
    repository, storage, _, _, _ = _maintenance_harness(tmp_path)

    class FailingMaintenance:
        def __init__(self):
            self.recovery_limits = []

        def recover_deleting(self, limit):
            self.recovery_limits.append(limit)
            raise sqlite3.OperationalError("database busy at C:\\private\\token.db")

    maintenance = FailingMaintenance()
    pool = ImportWorkerPool(
        repository,
        SimpleNamespace(),
        storage,
        maintenance=maintenance,
        retention_days=0,
        worker_count=1,
    )

    pool.start()
    try:
        assert maintenance.recovery_limits == [20]
        assert pool._scheduler_thread.is_alive()
    finally:
        pool.stop(wait=True)

    assert "private" not in caplog.text
    assert "token.db" not in caplog.text


def test_idle_retention_is_bounded_hourly_and_failure_does_not_escape(
    tmp_path, monkeypatch
):
    repository, storage, _, _, _ = _maintenance_harness(tmp_path)

    class RetentionMaintenance:
        def __init__(self):
            self.calls = []

        def run_retention(self, retention_days, *, limit):
            self.calls.append((retention_days, limit))
            if len(self.calls) == 1:
                raise sqlite3.OperationalError("database is busy")

    maintenance = RetentionMaintenance()
    pool = ImportWorkerPool(
        repository,
        SimpleNamespace(),
        storage,
        maintenance=maintenance,
        retention_days=30,
        worker_count=1,
    )
    moments = iter((100.0, 200.0, 3700.0))
    monkeypatch.setattr("app.import_worker.time.monotonic", lambda: next(moments))

    pool._run_idle_retention()
    pool._run_idle_retention()
    pool._run_idle_retention()

    assert maintenance.calls == [(30, 10), (30, 10)]


def test_manual_service_delete_marks_under_lock_and_finishes_after_unlock(tmp_path):
    repository, storage, maintenance, user_a, _ = _maintenance_harness(tmp_path)
    batch_id, *_ = _create_batch(repository, storage, user_a, status="failed")

    class OwnershipLock:
        def __init__(self):
            self._lock = threading.RLock()
            self.owned = False

        def __enter__(self):
            self._lock.acquire()
            self.owned = True
            return self

        def __exit__(self, *_args):
            self.owned = False
            self._lock.release()

    lock = OwnershipLock()
    sessions = SimpleNamespace(
        get_session=lambda token: SimpleNamespace(
            user_id=user_a, runtime=SimpleNamespace(lock=lock)
        )
    )
    original_mark = repository.mark_batch_deleting
    original_finish = maintenance._finish_marked_batch

    def checked_mark(*args, **kwargs):
        assert lock.owned is True
        return original_mark(*args, **kwargs)

    def checked_finish(*args, **kwargs):
        assert lock.owned is False
        return original_finish(*args, **kwargs)

    repository.mark_batch_deleting = checked_mark
    maintenance._finish_marked_batch = checked_finish
    service = ImportTaskService(
        sessions,
        repository,
        storage,
        SimpleNamespace(notify=lambda: None),
        maintenance=maintenance,
    )

    assert service.delete_batch_history("token", batch_id) is None
    assert repository.get_batch(user_a, batch_id) is None
