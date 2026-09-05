"""Offline end-to-end acceptance for import controls and history.

The tests keep the production SQLite repository, authenticated service,
worker-pool scheduler, user runtime registry, storage, and maintenance code.
Assistant, RAG, and Memory adapters are deterministic in-process fakes, and
all concurrency coordination uses ``threading.Event`` barriers.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.runtime as runtime_module
from app.auth import AuthService
from app.database import connect, initialize_database
from app.import_maintenance import ImportHistoryMaintenance
from app.import_models import ImportHistoryFilters
from app.import_repository import ImportTaskRepository, InvalidImportTransition
from app.import_service import ImportTaskService
from app.import_worker import (
    ImportTaskRunner,
    ImportWorkerPool,
    parse_import_task_retention_days,
)
from app.runtime import UserRuntimeRegistry
from app.storage import UserStorage


CHECKPOINTS = ("staged", "parsing", "chunking", "embedding", "persisting", "committing")


class OfflineRAGTool:
    """User-scoped, idempotent RAG fake with no backend constructors."""

    def __init__(self, *, rag_namespace, **_kwargs):
        self.rag_namespace = rag_namespace
        self.document_ids: set[str] = set()
        self.pipeline = SimpleNamespace(
            list_document_ids=lambda: sorted(self.document_ids)
        )

    def record_document(self, document_id):
        self.document_ids.add(document_id)

    def execute(self, action, **kwargs):
        assert action == "delete_document"
        self.document_ids.discard(kwargs["document_id"])
        return "ok"

    def close(self):
        pass


class OfflineMemoryTool:
    """Minimal import-event fake used at the real runtime boundary."""

    def __init__(self, *, user_id, **_kwargs):
        self.user_id = user_id
        self.import_events: dict[str, dict] = {}
        self.coordination_lock = None
        self.memory_manager = SimpleNamespace(memory_types={})

    def ensure_import_event(self, import_task_id, content, metadata, session_id):
        self.import_events.setdefault(
            import_task_id,
            {"content": content, "metadata": dict(metadata), "session_id": session_id},
        )
        return import_task_id

    def remove_import_event(self, import_task_id):
        return self.import_events.pop(import_task_id, None) is not None

    def close(self):
        pass


class OfflineAssistantState:
    def __init__(self):
        self.attempts = defaultdict(int)
        self.cleanup_failure_enabled = False
        self.cleanup_failure_task_id = None
        self.cleanup_order: list[str] = []
        self.cleanup_active = 0
        self.max_cleanup_active = 0
        self.lock = threading.Lock()


class OfflineAssistant:
    """Artifact-producing assistant fake with production compensation shape."""

    def __init__(self, *, user_id, runtime, state, **_kwargs):
        self.user_id = user_id
        self.runtime = runtime
        self.state = state

    def load_document(
        self,
        path,
        *,
        document_id,
        original_name,
        import_task_id,
        progress_callback,
        control_checkpoint,
    ):
        self.state.attempts[import_task_id] += 1
        for stage in ("parsing", "chunking", "embedding", "persisting"):
            progress_callback(stage, 1, 1, stage)
        self.runtime.rag_tool.record_document(document_id)
        control_checkpoint("committing")
        self.runtime.history.upsert_document(
            {
                "document_id": document_id,
                "document_name": original_name,
                "document_path": str(path),
                "import_task_id": import_task_id,
            }
        )
        control_checkpoint("committing")
        self.runtime.memory_tool.ensure_import_event(
            import_task_id=import_task_id,
            content=f"imported {original_name}",
            metadata={"document_id": document_id, "import_task_id": import_task_id},
            session_id="offline-acceptance",
        )
        return "ok"

    def compensate_import(self, document_id, import_task_id):
        with self.state.lock:
            self.state.cleanup_active += 1
            self.state.max_cleanup_active = max(
                self.state.max_cleanup_active, self.state.cleanup_active
            )
        try:
            if (
                self.state.cleanup_failure_enabled
                and self.state.cleanup_failure_task_id == import_task_id
            ):
                raise RuntimeError("offline cleanup failed")
            self.runtime.rag_tool.execute(
                "delete_document", document_id=document_id
            )
            self.runtime.history.delete_document(document_id)
            self.runtime.memory_tool.remove_import_event(import_task_id)
            with self.state.lock:
                self.state.cleanup_order.append(import_task_id)
        finally:
            with self.state.lock:
                self.state.cleanup_active -= 1

    def close(self):
        pass


class BarrierRunner(ImportTaskRunner):
    """Production runner with a one-shot, task-scoped checkpoint barrier."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.block_stage: str | None = None
        self.block_task_ids: set[str] = set()
        self.reached = threading.Event()
        self.release = threading.Event()
        self._blocked: set[tuple[str, str]] = set()
        self._barrier_lock = threading.Lock()

    def arm(self, stage: str, task_ids):
        assert stage in CHECKPOINTS
        self.block_stage = stage
        self.block_task_ids = set(task_ids)
        self.reached = threading.Event()
        self.release = threading.Event()

    def _control_checkpoint(self, task):
        production_check = super()._control_checkpoint(task)

        def check(stage):
            production_check(stage)
            key = (task.task_id, stage)
            should_block = False
            with self._barrier_lock:
                if (
                    stage == self.block_stage
                    and task.task_id in self.block_task_ids
                    and key not in self._blocked
                ):
                    self._blocked.add(key)
                    should_block = True
            if should_block:
                self.reached.set()
                assert self.release.wait(timeout=10), "checkpoint release timed out"
            production_check(stage)

        return check


class ObservableRepository(ImportTaskRepository):
    """Real SQLite repository plus Event notifications after durable writes."""

    def __init__(self, db_path):
        super().__init__(db_path)
        self._events: dict[tuple[str, str], threading.Event] = {}
        self._events_lock = threading.Lock()

    def status_event(self, task_id, status):
        with self._events_lock:
            return self._events.setdefault((task_id, status), threading.Event())

    def _signal(self, task):
        self.status_event(task.task_id, task.status).set()
        return task

    def mark_paused(self, *args, **kwargs):
        return self._signal(super().mark_paused(*args, **kwargs))

    def mark_cancelled(self, *args, **kwargs):
        return self._signal(super().mark_cancelled(*args, **kwargs))

    def mark_succeeded(self, *args, **kwargs):
        return self._signal(super().mark_succeeded(*args, **kwargs))

    def mark_failed(self, *args, **kwargs):
        return self._signal(super().mark_failed(*args, **kwargs))

    def mark_control_failed(self, *args, **kwargs):
        return self._signal(super().mark_control_failed(*args, **kwargs))


class OfflineSessions:
    def __init__(self, db_path, storage):
        self.auth = AuthService(db_path)
        self.runtime_registry = UserRuntimeRegistry(db_path, storage)
        self.sessions = {}

    def register(self, username):
        user = self.auth.register(username, "correct horse battery")
        token = f"token-{user.id}"
        runtime = self.runtime_registry.acquire_session(user.id)
        self.sessions[token] = SimpleNamespace(user_id=user.id, runtime=runtime)
        return token

    def get_session(self, token):
        if token not in self.sessions:
            raise ValueError("invalid session")
        return self.sessions[token]


class OfflineImportHarness:
    def __init__(self, tmp_path):
        self.db_path = tmp_path / "app.db"
        initialize_database(self.db_path)
        self.storage = UserStorage(tmp_path / "data")
        self.sessions = OfflineSessions(self.db_path, self.storage)
        self.state = OfflineAssistantState()
        self.repository = ObservableRepository(self.db_path)
        self.maintenance = ImportHistoryMaintenance(self.repository, self.storage)
        self.pool = self._make_pool(self.repository)
        self.service = ImportTaskService(
            self.sessions,
            self.repository,
            self.storage,
            self.pool,
            maintenance=self.maintenance,
        )
        self.sessions.runtime_registry.set_import_task_service(self.service)

    def _make_pool(self, repository):
        runner = BarrierRunner(
            repository,
            self.sessions.runtime_registry,
            self.storage,
            assistant_factory=lambda **kwargs: OfflineAssistant(
                state=self.state, **kwargs
            ),
        )
        return ImportWorkerPool(
            repository,
            self.sessions.runtime_registry,
            self.storage,
            runner=runner,
            worker_count=3,
            retention_days=0,
        )

    def replace_pool(self):
        repository = ObservableRepository(self.db_path)
        self.repository = repository
        self.maintenance = ImportHistoryMaintenance(repository, self.storage)
        self.pool = self._make_pool(repository)
        self.service.repository = repository
        self.service.maintenance = self.maintenance
        self.service.worker_pool = self.pool
        return self.pool

    def upload(self, name, content=b"offline"):
        path = self.db_path.parent / "uploads" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return SimpleNamespace(name=str(path))

    def run_one(self):
        task = self.repository.claim_next(set())
        assert task is not None
        self.pool.runner.run(task)
        return task

    def task(self, token, batch_id, index=0):
        return self.service.get_batch(token, batch_id).tasks[index]

    def runtime(self, token):
        return self.sessions.get_session(token).runtime


@pytest.fixture(autouse=True)
def offline_backend_constructors(monkeypatch):
    """Any runtime construction is forced through explicit offline fakes."""

    monkeypatch.setattr(runtime_module, "RAGTool", OfflineRAGTool)
    monkeypatch.setattr(runtime_module, "MemoryTool", OfflineMemoryTool)


def _wait(event, reason):
    assert event.wait(timeout=10), reason


def _event_types(repository, user_id, task_id):
    return [event.event_type for event in repository.list_task_events(user_id, task_id)]


def _assert_order(values, *ordered):
    positions = [values.index(value) for value in ordered]
    assert positions == sorted(positions)


def test_pause_restart_resume_preserves_identity_without_duplicates(tmp_path):
    app = OfflineImportHarness(tmp_path)
    token = app.sessions.register("pause-owner")
    user_id = app.sessions.get_session(token).user_id
    batch = app.service.submit_batch(token, [app.upload("pause.md")])
    task = batch.tasks[0]
    app.pool.runner.arm("embedding", [task.task_id])
    app.pool.start()
    try:
        _wait(app.pool.runner.reached, "import never reached embedding")
        app.service.pause_task(token, task.task_id)
        assert app.repository.get_task(user_id, task.task_id).status == "pause_requested"
        paused_event = app.repository.status_event(task.task_id, "paused")
        app.pool.runner.release.set()
        _wait(paused_event, "pause cleanup did not finish")
    finally:
        app.pool.runner.release.set()
        app.pool.stop()

    assert app.repository.get_task(user_id, task.task_id).status == "paused"
    restarted = app.replace_pool()
    succeeded_event = app.repository.status_event(task.task_id, "succeeded")
    restarted.start()
    try:
        resumed = app.service.resume_task(token, task.task_id)
        assert resumed.tasks[0].task_id == task.task_id
        _wait(succeeded_event, "resumed task did not succeed")
    finally:
        restarted.stop()

    completed = app.repository.get_task(user_id, task.task_id)
    runtime = app.runtime(token)
    formal = app.storage.document_path(
        user_id, task.document_id, task.file_suffix
    )
    assert completed.status == "succeeded"
    assert completed.task_id == task.task_id
    assert app.state.attempts[task.task_id] == 2
    assert [path.name for path in formal.parent.glob(f"{task.document_id}.*")] == [formal.name]
    assert runtime.rag_tool.pipeline.list_document_ids() == [task.document_id]
    assert [item["document_id"] for item in runtime.history.load()["documents"]] == [
        task.document_id
    ]
    assert list(runtime.memory_tool.import_events) == [task.task_id]
    with connect(app.db_path) as connection:
        assert connection.execute(
            "select count(*) from import_tasks where id = ?", (task.task_id,)
        ).fetchone()[0] == 1
    events = _event_types(app.repository, user_id, task.task_id)
    _assert_order(events, "pause_requested", "paused", "resumed")


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_cancel_during_each_checkpoint_removes_all_task_owned_artifacts(
    tmp_path, checkpoint
):
    app = OfflineImportHarness(tmp_path)
    token = app.sessions.register(f"cancel-{checkpoint}")
    user_id = app.sessions.get_session(token).user_id
    batch = app.service.submit_batch(token, [app.upload(f"{checkpoint}.md")])
    task = batch.tasks[0]
    app.pool.runner.arm(checkpoint, [task.task_id])
    cancelled_event = app.repository.status_event(task.task_id, "cancelled")
    app.pool.start()
    try:
        _wait(app.pool.runner.reached, f"import never reached {checkpoint}")
        app.service.cancel_task(token, task.task_id)
        assert app.repository.get_task(user_id, task.task_id).status == "cancel_requested"
        app.pool.runner.release.set()
        _wait(cancelled_event, "cancel cleanup did not finish")
    finally:
        app.pool.runner.release.set()
        app.pool.stop()

    cancelled = app.repository.get_task(user_id, task.task_id)
    runtime = app.runtime(token)
    staged = app.storage.user_paths(user_id).root / task.staged_relative_path
    formal = app.storage.document_path(user_id, task.document_id, task.file_suffix)
    temporary = app.storage.temporary_document_path(
        user_id, task.document_id, task.file_suffix
    )
    assert cancelled.status == "cancelled"
    assert not staged.exists()
    assert not formal.exists()
    assert not temporary.exists()
    assert task.document_id not in runtime.rag_tool.document_ids
    assert all(
        item.get("document_id") != task.document_id
        for item in runtime.history.load()["documents"]
    )
    assert task.task_id not in runtime.memory_tool.import_events
    events = _event_types(app.repository, user_id, task.task_id)
    _assert_order(events, "cancel_requested", "cancelled")


def test_batch_cancel_serializes_cleanup_and_preserves_succeeded_tasks(tmp_path):
    app = OfflineImportHarness(tmp_path)
    token = app.sessions.register("batch-cancel-owner")
    user_id = app.sessions.get_session(token).user_id
    batch = app.service.submit_batch(
        token,
        [app.upload("one.md"), app.upload("two.md"), app.upload("three.md")],
    )
    first_claim = app.run_one()
    assert app.repository.get_task(user_id, first_claim.task_id).status == "succeeded"
    remaining = {task.task_id for task in batch.tasks} - {first_claim.task_id}
    app.pool.runner.arm("parsing", remaining)
    cancelled_events = [
        app.repository.status_event(task_id, "cancelled") for task_id in remaining
    ]
    app.pool.start()
    try:
        _wait(app.pool.runner.reached, "second task did not enter parsing")
        app.service.cancel_batch(token, batch.batch_id)
        app.pool.runner.release.set()
        for event in cancelled_events:
            _wait(event, "batch cancellation did not finish")
    finally:
        app.pool.runner.release.set()
        app.pool.stop()

    final = app.service.get_batch(token, batch.batch_id)
    assert next(task for task in final.tasks if task.task_id == first_claim.task_id).status == "succeeded"
    assert {task.task_id for task in final.tasks if task.status == "cancelled"} == remaining
    assert app.state.max_cleanup_active == 1
    assert set(app.state.cleanup_order) == remaining
    assert first_claim.task_id not in app.state.cleanup_order
    runtime = app.runtime(token)
    assert first_claim.document_id in runtime.rag_tool.document_ids
    assert first_claim.task_id in runtime.memory_tool.import_events
    for task_id in remaining:
        _assert_order(
            _event_types(app.repository, user_id, task_id),
            "cancel_requested",
            "cancelled",
        )


def test_compensation_failure_can_be_cancelled_with_cleanup_only(tmp_path):
    app = OfflineImportHarness(tmp_path)
    token = app.sessions.register("cleanup-retry-owner")
    user_id = app.sessions.get_session(token).user_id
    batch = app.service.submit_batch(token, [app.upload("cleanup.md")])
    task = batch.tasks[0]
    app.state.cleanup_failure_enabled = True
    app.state.cleanup_failure_task_id = task.task_id
    app.pool.runner.arm("persisting", [task.task_id])
    failed_event = app.repository.status_event(task.task_id, "failed")
    app.pool.start()
    try:
        _wait(app.pool.runner.reached, "import did not reach persisting")
        app.service.cancel_task(token, task.task_id)
        app.pool.runner.release.set()
        _wait(failed_event, "cleanup failure was not recorded")
    finally:
        app.pool.runner.release.set()
        app.pool.stop()

    failed = app.repository.get_task(user_id, task.task_id)
    assert (failed.status, failed.error_code) == ("failed", "cancel_cleanup_failed")
    assert "offline cleanup failed" in failed.error_summary
    assert app.state.attempts[task.task_id] == 1

    app.state.cleanup_failure_enabled = False
    cancelled_event = app.repository.status_event(task.task_id, "cancelled")
    app.service.cancel_task(token, task.task_id)
    app.pool.start()
    try:
        _wait(cancelled_event, "cleanup-only cancellation did not finish")
    finally:
        app.pool.stop()

    assert app.repository.get_task(user_id, task.task_id).status == "cancelled"
    assert app.state.attempts[task.task_id] == 1
    runtime = app.runtime(token)
    assert task.document_id not in runtime.rag_tool.document_ids
    assert task.task_id not in runtime.memory_tool.import_events
    assert all(
        item.get("document_id") != task.document_id
        for item in runtime.history.load()["documents"]
    )


def test_history_filters_events_and_cross_user_controls_are_isolated(tmp_path):
    app = OfflineImportHarness(tmp_path)
    token_a = app.sessions.register("history-owner-a")
    token_b = app.sessions.register("history-owner-b")
    user_a = app.sessions.get_session(token_a).user_id
    user_b = app.sessions.get_session(token_b).user_id
    batch_a1 = app.service.submit_batch(token_a, [app.upload("a/alpha.md")])
    batch_a2 = app.service.submit_batch(token_a, [app.upload("a/beta.md")])
    batch_b = app.service.submit_batch(token_b, [app.upload("b/foreign.md")])
    app.service.pause_task(token_a, batch_a1.tasks[0].task_id)
    app.service.resume_task(token_a, batch_a1.tasks[0].task_id)
    app.run_one()
    app.run_one()
    app.run_one()

    owner_ids = {batch_a1.batch_id, batch_a2.batch_id}
    seen = set()
    cursor = None
    while True:
        page = app.service.list_task_history(token_a, {}, cursor, limit=1)
        seen.update(batch.batch_id for batch in page.batches)
        if page.next_cursor is None:
            break
        cursor = page.next_cursor
    assert seen == owner_ids

    created_date = batch_a1.created_at[:10]
    filter_cases = (
        {"statuses": ["succeeded"]},
        {"filename_query": "alpha"},
        {"created_from": created_date, "created_to": created_date},
        {"batch_id": batch_a1.batch_id},
    )
    for filters in filter_cases:
        page = app.service.list_task_history(token_a, filters, None, limit=10)
        assert batch_b.batch_id not in {item.batch_id for item in page.batches}
        assert all(item.user_id == user_a for item in page.batches)

    task_a = app.repository.get_batch(user_a, batch_a1.batch_id).tasks[0]
    task_b = app.repository.get_batch(user_b, batch_b.batch_id).tasks[0]
    assert app.service.list_task_events(token_a, task_a.task_id)
    assert app.service.list_task_events(token_a, task_b.task_id) == []
    assert app.service.list_task_events(token_b, task_a.task_id) == []
    before_a = app.repository.get_task(user_a, task_a.task_id)
    before_b = app.repository.get_task(user_b, task_b.task_id)

    foreign_operations = (
        lambda: app.service.pause_task(token_b, task_a.task_id),
        lambda: app.service.cancel_task(token_b, task_a.task_id),
        lambda: app.service.cancel_batch(token_b, batch_a1.batch_id),
        lambda: app.service.delete_batch_history(token_b, batch_a1.batch_id),
    )
    for operation in foreign_operations:
        with pytest.raises((KeyError, InvalidImportTransition)) as error:
            operation()
        assert task_a.task_id not in str(error.value)
        assert batch_a1.batch_id not in str(error.value)

    assert app.repository.get_task(user_a, task_a.task_id) == before_a
    assert app.repository.get_task(user_b, task_b.task_id) == before_b


def test_interrupted_history_delete_recovers_without_deleting_document(tmp_path):
    app = OfflineImportHarness(tmp_path)
    token = app.sessions.register("delete-recovery-owner")
    user_id = app.sessions.get_session(token).user_id
    batch = app.service.submit_batch(token, [app.upload("formal.md")])
    app.run_one()
    task = app.repository.get_batch(user_id, batch.batch_id).tasks[0]
    formal = app.storage.document_path(user_id, task.document_id, task.file_suffix)
    staged = app.storage.user_paths(user_id).root / task.staged_relative_path
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_bytes(b"recoverable staging")
    assert formal.exists()
    assert app.runtime(token).history.load()["documents"]

    app.repository.mark_batch_deleting(user_id, batch.batch_id)
    restarted_repository = ImportTaskRepository(app.db_path)
    restarted_maintenance = ImportHistoryMaintenance(
        restarted_repository, app.storage
    )

    assert restarted_maintenance.recover_deleting(limit=20) == 1
    assert restarted_repository.get_batch(user_id, batch.batch_id) is None
    assert restarted_repository.list_task_events(user_id, task.task_id) == []
    assert not staged.exists()
    assert formal.exists()
    assert [
        item["document_id"] for item in app.runtime(token).history.load()["documents"]
    ] == [task.document_id]


def test_retention_is_disabled_by_default_and_bounded_when_enabled(
    tmp_path, monkeypatch
):
    app = OfflineImportHarness(tmp_path)
    token = app.sessions.register("retention-owner")
    user_id = app.sessions.get_session(token).user_id
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    batches = []
    for index in range(12):
        batch = app.service.submit_batch(
            token, [app.upload(f"retention/{index:02d}.md")]
        )
        app.run_one()
        created_at = (now - timedelta(days=60 - index)).isoformat().replace(
            "+00:00", "Z"
        )
        with connect(app.db_path) as connection:
            connection.execute(
                "update import_batches set created_at = ?, updated_at = ? where id = ?",
                (created_at, created_at, batch.batch_id),
            )
            connection.execute(
                "update import_tasks set created_at = ?, updated_at = ?, finished_at = ? where batch_id = ?",
                (created_at, created_at, created_at, batch.batch_id),
            )
        batches.append((created_at, batch))

    monkeypatch.delenv("IMPORT_TASK_RETENTION_DAYS", raising=False)
    assert parse_import_task_retention_days() == 0
    assert app.maintenance.run_retention(0, limit=10, now=now) == 0
    assert len(app.repository.list_history(user_id, ImportHistoryFilters(), None, 100).batches) == 12

    deleted = app.maintenance.run_retention(30, limit=10, now=now)
    remaining = {
        batch.batch_id
        for batch in app.repository.list_history(
            user_id, ImportHistoryFilters(), None, 100
        ).batches
    }
    expected_deleted = {
        batch.batch_id for _created, batch in sorted(batches, key=lambda item: item[0])[:10]
    }
    assert deleted == 10
    assert remaining == {batch.batch_id for _, batch in batches} - expected_deleted
    assert len(remaining) == 2
