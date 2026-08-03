"""Offline acceptance coverage for durable batch imports.

The harness deliberately uses the real SQLite repository, authenticated
service, storage, runtime registry, and worker-pool scheduling boundary.  It
replaces document/RAG processing and time with deterministic fakes, so these
tests neither require credentials nor contact Qdrant, Neo4j, or an LLM.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.runtime as runtime_module
from app.auth import AuthService
from app.database import initialize_database
from app.import_repository import ImportTaskRepository
from app.import_service import ImportTaskService
from app.import_worker import ImportTaskRunner, ImportWorkerPool
from app.runtime import UserRuntimeRegistry
from app.storage import UserStorage
from assistants.pdf_learning_assistant import PDFLearningAssistant
from hello_agents.memory.rag.errors import RAGConnectionError


class FakeClock:
    def __init__(self):
        self.value = datetime(2026, 7, 30, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)

    def iso(self) -> str:
        return self.value.isoformat().replace("+00:00", "Z")


class FakeAssistant:
    outcomes: list[BaseException] = []
    loaded: dict[str, list[str]] = {}
    completed = threading.Event()
    entered = threading.Event()
    release = threading.Event()
    block = False

    def __init__(self, *, user_id, runtime, **_kwargs):
        self.user_id = user_id
        self.runtime = runtime

    def load_document(self, _path, *, document_id, progress_callback, **_kwargs):
        type(self).entered.set()
        if type(self).block:
            assert type(self).release.wait(timeout=5)
        if type(self).outcomes:
            raise type(self).outcomes.pop(0)
        progress_callback("parsing", 1, 1, "parsed")
        progress_callback("chunking", 1, 1, "chunked")
        progress_callback("embedding", 1, 1, "embedded")
        progress_callback("persisting", 1, 1, "persisted")
        self.runtime.rag_tool.record_document(document_id)
        type(self).loaded.setdefault(self.user_id, []).append(document_id)
        type(self).completed.set()
        return "ok"

    def close(self):
        pass


@pytest.fixture(autouse=True)
def reset_fake_assistant(monkeypatch):
    monkeypatch.setattr(runtime_module, "RAGTool", OfflineRAGTool)
    FakeAssistant.outcomes = []
    FakeAssistant.loaded = {}
    FakeAssistant.completed = threading.Event()
    FakeAssistant.entered = threading.Event()
    FakeAssistant.release = threading.Event()
    FakeAssistant.block = False


class OfflineRAGTool:
    """In-memory RAG adapter used only at the real runtime boundary."""

    def __init__(self, *, rag_namespace, **_kwargs):
        self.rag_namespace = rag_namespace
        self.document_ids = []

    def record_document(self, document_id):
        self.document_ids.append(document_id)

    def list_documents(self):
        return list(self.document_ids)

    def close(self):
        pass


class OfflineSessions:
    """Authenticated sessions that bind only the real clear guard."""

    def __init__(self, db_path, storage):
        self.auth = AuthService(db_path)
        self.runtime_registry = UserRuntimeRegistry(db_path, storage)
        self._sessions = {}

    def register(self, username, password):
        user = self.auth.register(username, password)
        token = f"token-{user.id}"
        runtime = self.runtime_registry.acquire_session(user.id)
        assistant = object.__new__(PDFLearningAssistant)
        assistant.user_id = user.id
        assistant.runtime = runtime
        assistant.close = lambda: None
        assistant._clear_documents_coordinated = lambda: "cleared"
        self._sessions[token] = SimpleNamespace(
            user_id=user.id, runtime=runtime, assistant=assistant
        )
        return token

    def get_session(self, token):
        return self._sessions[token]

    def logout(self, token):
        session = self._sessions.pop(token)
        session.assistant.close()
        self.runtime_registry.release_session(session.user_id)


class TrackingImportTaskRepository(ImportTaskRepository):
    def __init__(self, db_path):
        super().__init__(db_path)
        self.progress_updates = []

    def update_progress(self, user_id, task_id, stage, progress, now=None):
        self.progress_updates.append((task_id, stage, progress))
        return super().update_progress(
            user_id, task_id, stage, progress, now=now
        )


class RecoveryTrackingImportTaskRepository(ImportTaskRepository):
    """Captures the synchronous recovery transition performed by pool.start()."""

    def __init__(self, db_path):
        super().__init__(db_path)
        self.recovery_results = []

    def recover_running(self, storage, now=None):
        recovered = super().recover_running(storage, now=now)
        from app.database import connect

        with connect(self.db_path) as connection:
            rows = connection.execute(
                "select status, error_code from import_tasks order by id"
            ).fetchall()
        self.recovery_results = [
            (row["status"], row["error_code"]) for row in rows
        ]
        return recovered


class OfflineImportApp:
    def __init__(self, tmp_path):
        self.db_path = tmp_path / "app.db"
        initialize_database(self.db_path)
        self.storage = UserStorage(tmp_path / "data")
        self.sessions = OfflineSessions(self.db_path, self.storage)
        self.repository = TrackingImportTaskRepository(self.db_path)
        self.clock = FakeClock()
        self.runner = ImportTaskRunner(
            self.repository,
            self.sessions.runtime_registry,
            self.storage,
            assistant_factory=FakeAssistant,
            clock=self.clock,
        )
        self.pool = ImportWorkerPool(
            self.repository,
            self.sessions.runtime_registry,
            self.storage,
            runner=self.runner,
            worker_count=1,
        )
        self.service = ImportTaskService(
            self.sessions, self.repository, self.storage, self.pool
        )
        self.sessions.runtime_registry.set_import_task_service(self.service)

    def upload(self, name: str, content: bytes) -> SimpleNamespace:
        path = self.db_path.parent / "uploads" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return SimpleNamespace(name=str(path))

    def run_one(self):
        task = self.repository.claim_next(set(), now=self.clock.iso())
        assert task is not None
        self.pool.runner.run(task)
        return task

    def restarted_pool(self):
        repository = RecoveryTrackingImportTaskRepository(self.db_path)
        runner = ImportTaskRunner(
            repository,
            self.sessions.runtime_registry,
            self.storage,
            assistant_factory=FakeAssistant,
            clock=self.clock,
        )
        return repository, ImportWorkerPool(
            repository,
            self.sessions.runtime_registry,
            self.storage,
            runner=runner,
            worker_count=1,
        )


def _task(app: OfflineImportApp, user_id: str, batch_id: str):
    summary = app.repository.get_batch(user_id, batch_id)
    assert summary is not None
    return summary.tasks[0]


def test_two_users_import_same_name_without_cross_scope_access(tmp_path):
    app = OfflineImportApp(tmp_path)
    token_a = app.sessions.register("UserA", "correct horse battery")
    token_b = app.sessions.register("UserB", "correct horse battery")
    user_a = app.sessions.get_session(token_a).user_id
    user_b = app.sessions.get_session(token_b).user_id
    batch_a = app.service.submit_batch(token_a, [app.upload("a/same.md", b"A")])
    batch_b = app.service.submit_batch(token_b, [app.upload("b/same.md", b"B")])

    app.run_one()
    app.run_one()

    assert app.service.get_batch(token_a, batch_a.batch_id).succeeded == 1
    assert app.service.get_batch(token_b, batch_b.batch_id).succeeded == 1
    with pytest.raises(KeyError):
        app.service.get_batch(token_a, batch_b.batch_id)
    assert FakeAssistant.loaded[user_a] == [batch_a.tasks[0].document_id]
    assert FakeAssistant.loaded[user_b] == [batch_b.tasks[0].document_id]
    runtime_a = app.sessions.runtime_registry.get_or_create(user_a)
    runtime_b = app.sessions.runtime_registry.get_or_create(user_b)
    assert runtime_a.rag_tool.rag_namespace == f"pdf_{user_a}"
    assert runtime_b.rag_tool.rag_namespace == f"pdf_{user_b}"
    assert runtime_a.rag_tool.list_documents() == [batch_a.tasks[0].document_id]
    assert runtime_b.rag_tool.list_documents() == [batch_b.tasks[0].document_id]
    assert app.storage.user_paths(user_a).root != app.storage.user_paths(user_b).root
    path_a = app.storage.document_path(user_a, batch_a.tasks[0].document_id, ".md")
    path_b = app.storage.document_path(user_b, batch_b.tasks[0].document_id, ".md")
    assert path_a.read_bytes() == b"A"
    assert path_b.read_bytes() == b"B"


def test_active_import_rejects_clear_before_mutating_documents(tmp_path, monkeypatch):
    app = OfflineImportApp(tmp_path)
    token = app.sessions.register("UserA", "correct horse battery")
    assistant = app.sessions.get_session(token).assistant
    called = []
    monkeypatch.setattr(
        assistant, "_clear_documents_coordinated", lambda: called.append("clear")
    )
    batch = app.service.submit_batch(token, [app.upload("active.md", b"A")])

    result = assistant.clear_all_documents()

    assert result == "Cannot clear documents while imports are active; wait for them to finish."
    assert called == []
    assert _task(app, assistant.user_id, batch.batch_id).status == "queued"


def test_restart_recovers_running_task_with_staged_file_and_completes(tmp_path):
    app = OfflineImportApp(tmp_path)
    token = app.sessions.register("UserA", "correct horse battery")
    user_id = app.sessions.get_session(token).user_id
    batch = app.service.submit_batch(token, [app.upload("recover.md", b"A")])
    claimed = app.repository.claim_next(set(), now=app.clock.iso())
    assert claimed is not None and claimed.status == "running"
    recovered_repository, restarted = app.restarted_pool()

    restarted.start()
    try:
        assert recovered_repository.recovery_results == [
            ("queued", "process_interrupted")
        ]
        assert FakeAssistant.completed.wait(timeout=5)
    finally:
        restarted.stop()

    assert _task(app, user_id, batch.batch_id).status == "succeeded"


def test_restart_marks_missing_staged_source_failed(tmp_path):
    app = OfflineImportApp(tmp_path)
    token = app.sessions.register("UserA", "correct horse battery")
    user_id = app.sessions.get_session(token).user_id
    batch = app.service.submit_batch(token, [app.upload("missing.md", b"A")])
    claimed = app.repository.claim_next(set(), now=app.clock.iso())
    assert claimed is not None
    (app.storage.user_paths(user_id).root / claimed.staged_relative_path).unlink()
    recovered_repository, restarted = app.restarted_pool()

    restarted.start()
    try:
        assert recovered_repository.recovery_results == [
            ("failed", "staged_file_missing")
        ]
    finally:
        restarted.stop()

    task = _task(app, user_id, batch.batch_id)
    assert task.status == "failed"
    assert task.error_code == "staged_file_missing"


def test_four_transient_attempts_fail_then_batch_retry_resets_automatic_count(tmp_path):
    app = OfflineImportApp(tmp_path)
    token = app.sessions.register("UserA", "correct horse battery")
    user_id = app.sessions.get_session(token).user_id
    batch = app.service.submit_batch(token, [app.upload("retry.md", b"A")])
    FakeAssistant.outcomes = [RAGConnectionError("temporary")] * 4

    for expected_auto_retry_count, delay in enumerate((2, 10, 30), start=1):
        attempt_started_at = app.clock()
        app.run_one()
        waiting = _task(app, user_id, batch.batch_id)
        assert (
            waiting.status,
            waiting.auto_retry_count,
            waiting.total_attempt_count,
            waiting.next_attempt_at,
        ) == (
            "retry_wait",
            expected_auto_retry_count,
            expected_auto_retry_count,
            (attempt_started_at + timedelta(seconds=delay))
            .isoformat()
            .replace("+00:00", "Z"),
        )
        app.clock.advance(delay)
    app.run_one()

    failed = _task(app, user_id, batch.batch_id)
    assert (failed.status, failed.auto_retry_count, failed.total_attempt_count) == (
        "failed", 3, 4,
    )
    staged = app.storage.user_paths(user_id).root / failed.staged_relative_path
    formal = app.storage.document_path(user_id, failed.document_id, failed.file_suffix)
    assert staged.read_bytes() == b"A"
    assert not formal.exists()

    retried = app.service.retry_failed_in_batch(token, batch.batch_id)
    reset = retried.tasks[0]
    assert (
        reset.status,
        reset.auto_retry_count,
        reset.manual_retry_count,
        reset.total_attempt_count,
        reset.next_attempt_at,
    ) == (
        "queued", 0, 1, 4, None,
    )

    app.run_one()

    completed = _task(app, user_id, batch.batch_id)
    assert (
        completed.status,
        completed.auto_retry_count,
        completed.manual_retry_count,
        completed.total_attempt_count,
    ) == ("succeeded", 0, 1, 5)
    assert formal.read_bytes() == b"A"
    assert not staged.exists()
    stages = [
        stage
        for task_id, stage, _progress in app.repository.progress_updates
        if task_id == completed.task_id
    ]
    assert stages[-6:] == [
        "staged", "parsing", "chunking", "embedding", "persisting", "committing"
    ]


def test_logout_does_not_cancel_running_background_import(tmp_path):
    app = OfflineImportApp(tmp_path)
    token = app.sessions.register("UserA", "correct horse battery")
    user_id = app.sessions.get_session(token).user_id
    runtime = app.sessions.get_session(token).runtime
    batch = app.service.submit_batch(token, [app.upload("logout.md", b"A")])
    FakeAssistant.block = True
    app.pool.start()
    try:
        assert FakeAssistant.entered.wait(timeout=5)
        assert runtime.active_background_count == 1

        app.sessions.logout(token)

        assert app.sessions.runtime_registry.has_runtime(user_id) is True
        assert runtime.active_session_count == 0
        assert runtime.active_background_count == 1
        FakeAssistant.release.set()
        assert FakeAssistant.completed.wait(timeout=5)
    finally:
        FakeAssistant.release.set()
        app.pool.stop()

    assert _task(app, user_id, batch.batch_id).status == "succeeded"
    assert app.sessions.runtime_registry.has_runtime(user_id) is False
