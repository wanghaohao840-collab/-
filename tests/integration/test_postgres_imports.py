"""Shared import submission and control against the real PostgreSQL schema."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from psycopg.errors import UniqueViolation

from app.import_models import ImportTaskCreate
from app.import_persistence import ImportStore
from app.import_repository import InvalidImportTransition, PostgresImportTaskRepository
from tests.integration.test_postgres_auth_sessions import shared_database


@pytest.fixture
def repositories(shared_database):
    open_pool, _ = shared_database
    first, second = open_pool(), open_pool()
    with first.transaction() as cursor:
        for user in ("owner", "other"):
            cursor.execute(
                "insert into users values (%s,%s,%s,%s,%s,%s,%s)",
                (user, user, user, "hash", "active", "now", "now"),
            )
    return first, PostgresImportTaskRepository(first), PostgresImportTaskRepository(second), open_pool


def task(task_id="task-1", batch_id="batch-1", user_id="owner"):
    return ImportTaskCreate(task_id, batch_id, user_id, "doc-" + task_id,
                            "file.txt", ".txt", 3, "imports/file.txt")


def test_submit_read_across_pools_and_restart(repositories):
    database, writer, reader, open_pool = repositories
    created = writer.create_batch("owner", [task(), task("task-2")], now="T1")
    assert created.total == created.queued == 2
    assert [item.task_id for item in created.tasks] == ["task-1", "task-2"]
    assert reader.get_batch("owner", "batch-1") == created
    assert reader.list_batches("owner") == [created]
    assert reader.get_task("owner", "task-1") == created.tasks[0]
    assert reader.has_active_tasks("owner")
    assert reader.has_active_task_for_document("owner", "doc-task-1")
    assert not reader.has_active_task_for_document("other", "doc-task-1")
    assert PostgresImportTaskRepository(open_pool()).get_batch("owner", "batch-1") == created


def test_validation_scope_and_atomic_failure(repositories):
    database, writer, reader, _ = repositories
    with pytest.raises(ValueError, match="at least one"):
        writer.create_batch("owner", [])
    with pytest.raises(ValueError, match="one user and batch"):
        writer.create_batch("owner", [task(), task("task-2", "batch-2")])
    with pytest.raises(ValueError, match="one user and batch"):
        writer.create_batch("owner", [task(), task("task-2", user_id="other")])
    with pytest.raises(KeyError):
        writer.create_batch("missing", [task(user_id="missing")])
    with database.transaction() as cursor:
        assert cursor.execute("select count(*) as n from import_batches").fetchone()["n"] == 0
        assert cursor.execute("select count(*) as n from import_tasks").fetchone()["n"] == 0
    writer.create_batch("owner", [task()], now="T1")
    with pytest.raises(UniqueViolation):
        writer.create_batch("owner", [task("task-2", "batch-2"), task("task-1", "batch-2")])
    assert reader.list_batches("owner")[0].total == 1
    assert reader.get_batch("owner", "batch-2") is None
    assert reader.get_batch("other", "batch-1") is None
    assert reader.get_task("other", "task-1") is None
    with pytest.raises(KeyError, match="import task was not found"):
        reader.request_cancel("other", "batch-1", "task-1")
    with pytest.raises(KeyError, match="import task was not found"):
        reader.request_cancel("owner", "wrong-batch", "task-1")


def test_caller_owned_transaction_visibility_and_rollback(repositories):
    database, writer, reader, _ = repositories
    with pytest.raises(ValueError, match="caller-owned"):
        with database.connection() as connection:
            writer.create_batch_in_transaction(connection.cursor(), "owner", [task()])
    with pytest.raises(RuntimeError, match="rollback"):
        with database.transaction() as cursor:
            cursor.execute("begin")
            summary = writer.create_batch_in_transaction(cursor, "owner", [task()], now="T1")
            assert summary.total == 1
            assert reader.get_batch("owner", "batch-1") is None
            raise RuntimeError("rollback")
    assert reader.get_batch("owner", "batch-1") is None
    with database.transaction() as cursor:
        cursor.execute("begin")
        writer.create_batch_in_transaction(cursor, "owner", [task()], now="T2")
    assert reader.get_batch("owner", "batch-1").created_at == "T2"


def set_state(database, task_id, status, stage=None, **fields):
    values = {"status": status, "stage": stage or status, **fields}
    with database.transaction() as cursor:
        cursor.execute("update import_tasks set " + ",".join(f"{name}=%s" for name in values)
                       + " where id=%s", (*values.values(), task_id))


def test_cancel_branches_and_activity(repositories):
    database, writer, reader, _ = repositories
    writer.create_batch("owner", [task("queued"), task("retry"), task("run"),
                                  task("commit"), task("done")], now="T1")
    set_state(database, "retry", "retry_wait", next_attempt_at="later")
    set_state(database, "run", "running", "embedding")
    set_state(database, "done", "succeeded")
    assert writer.request_cancel("owner", "batch-1", "queued", now="T2").outcome == "cancelled"
    assert writer.request_cancel("owner", "batch-1", "retry", now="T2").outcome == "cancelled"
    assert reader.get_task("owner", "retry").next_attempt_at is None
    assert writer.request_cancel("owner", "batch-1", "run", now="T3").outcome == "cancel_requested"
    assert reader.is_cancel_requested("owner", "run")
    assert writer.request_cancel("owner", "batch-1", "run", now="T4").outcome == "cancel_requested"
    assert reader.get_task("owner", "run").cancel_requested_at == "T3"
    set_state(database, "run", "cancelled")
    set_state(database, "commit", "running", "committing")
    assert writer.request_cancel("owner", "batch-1", "commit").outcome == "not_cancellable"
    assert writer.request_cancel("owner", "batch-1", "done").outcome == "unchanged"
    summary = reader.get_batch("owner", "batch-1")
    assert (summary.total, summary.queued, summary.running, summary.retry_wait,
            summary.succeeded, summary.cancelled) == (5, 0, 1, 0, 1, 3)
    assert summary.updated_at == "T3"
    assert reader.has_active_tasks("owner")
    assert not reader.has_active_task_for_document("owner", "doc-queued")
    assert not reader.has_active_tasks("other")


def test_parallel_cancel_and_retry_once(repositories):
    database, first, second, _ = repositories
    first.create_batch("owner", [task("cancel"), task("failed")], now="T1")
    set_state(database, "failed", "failed", error_code="parse", error_summary="bad",
              auto_retry_count=3, total_attempt_count=4, finished_at="T0")
    with ThreadPoolExecutor(2) as pool:
        decisions = list(pool.map(lambda repo: repo.request_cancel("owner", "batch-1", "cancel", now="T2"),
                                  (first, second)))
    assert all(item.outcome in ("cancelled", "unchanged") for item in decisions)
    assert first.get_batch("owner", "batch-1").cancelled == 1

    def retry(repo):
        try:
            return repo.retry_task("owner", "failed", now="T3")
        except InvalidImportTransition:
            return None

    with ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(retry, (first, second)))
    assert sum(item is not None for item in outcomes) == 1
    record = first.get_task("owner", "failed")
    assert (record.status, record.stage, record.progress, record.auto_retry_count,
            record.manual_retry_count, record.total_attempt_count) == ("queued", "queued", 0, 0, 1, 4)
    assert (record.error_code, record.error_summary, record.finished_at) == (None, None, None)
    with pytest.raises(InvalidImportTransition):
        first.retry_task("owner", "failed")
    with pytest.raises(KeyError):
        first.retry_task("other", "failed")
    set_state(database, "failed", "failed")
    with ThreadPoolExecutor(2) as pool:
        counts = list(pool.map(lambda repo: repo.retry_failed_in_batch("owner", "batch-1", now="T4"),
                               (first, second)))
    assert sorted(counts) == [0, 1]
    assert first.get_task("owner", "failed").manual_retry_count == 2


def test_summary_and_children_share_snapshot_during_mutation(repositories, monkeypatch):
    _, writer, reader, _ = repositories
    writer.create_batch("owner", [task()], now="T1")
    original = ImportStore.batch_tasks
    mutated = False

    def batch_tasks(store, user_id, batch_id):
        nonlocal mutated
        if not mutated:
            mutated = True
            writer.request_cancel("owner", "batch-1", "task-1", now="T2")
        return original(store, user_id, batch_id)

    monkeypatch.setattr(ImportStore, "batch_tasks", batch_tasks)
    snapshot = reader.get_batch("owner", "batch-1")
    assert snapshot.queued == 1
    assert snapshot.cancelled == 0
    assert snapshot.tasks[0].status == "queued"
    current = reader.get_batch("owner", "batch-1")
    assert current.queued == 0
    assert current.cancelled == 1
    assert current.tasks[0].status == "cancelled"


def test_fail_closed_worker_methods(repositories):
    _, repo, _, _ = repositories
    for name in ("claim_next", "try_begin_committing", "update_progress", "release_claim",
                 "mark_succeeded", "mark_cancelled", "mark_retry_wait", "mark_failed",
                 "recover_running", "cleanup_succeeded_staging", "_transition_update",
                 "_raise_transition_error", "_get_batch"):
        with pytest.raises(RuntimeError, match="lease is not implemented"):
            getattr(repo, name)()
