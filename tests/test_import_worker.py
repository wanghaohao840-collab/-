from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.auth import AuthService
from app.database import initialize_database
from app.import_models import ImportTaskCreate
from app.import_repository import ImportTaskRepository
from app.import_worker import (
    ImportTaskRunner,
    ImportWorkerPool,
    classify_import_failure,
)
from app.storage import UserStorage
from hello_agents.memory.rag.errors import (
    RAGAuthenticationError,
    RAGConfigError,
    RAGConnectionError,
)


class MutableClock:
    def __init__(self):
        self.value = datetime(2026, 7, 30, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)

    def iso(self):
        return self.value.isoformat().replace("+00:00", "Z")


class FakeRuntimeRegistry:
    def __init__(self, storage):
        self.storage = storage
        self.acquired = []
        self.released = []

    def acquire_background(self, user_id):
        self.acquired.append(user_id)
        return SimpleNamespace(
            paths=self.storage.ensure_user_dirs(user_id),
            lock=threading.RLock(),
            memory_tool=SimpleNamespace(),
            rag_tool=SimpleNamespace(),
            history=SimpleNamespace(),
            reports=SimpleNamespace(),
            coordinator=SimpleNamespace(),
        )

    def release_background(self, user_id):
        self.released.append(user_id)


class FakeAssistant:
    failures = []
    result = "ok"
    calls = []
    close_count = 0

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def load_document(self, path, **kwargs):
        type(self).calls.append((path, kwargs))
        if self.failures:
            raise self.failures.pop(0)
        callback = kwargs["progress_callback"]
        callback("parsing", 1, 1, "parsed")
        callback("embedding", 1, 2, "embedded")
        return type(self).result

    def close(self):
        type(self).close_count += 1


@pytest.fixture(autouse=True)
def reset_fake_assistant():
    FakeAssistant.failures = []
    FakeAssistant.result = "ok"
    FakeAssistant.calls = []
    FakeAssistant.close_count = 0


def make_runner(tmp_path, *, failures=()):
    db_path = tmp_path / "app.db"
    initialize_database(db_path)
    user_id = AuthService(db_path).register(
        "worker-user", "correct horse battery"
    ).id
    repository = ImportTaskRepository(db_path)
    storage = UserStorage(tmp_path / "data")
    runtime_registry = FakeRuntimeRegistry(storage)
    clock = MutableClock()
    batch_id, task_id, document_id = (str(uuid.uuid4()) for _ in range(3))
    staged = storage.staged_import_path(user_id, batch_id, task_id, ".md")
    staged.write_bytes(b"content")
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
                size_bytes=7,
                staged_relative_path=str(
                    staged.relative_to(storage.user_paths(user_id).root)
                ),
            )
        ],
        now=clock.iso(),
    )
    FakeAssistant.failures = list(failures)
    runner = ImportTaskRunner(
        repository,
        runtime_registry,
        storage,
        assistant_factory=FakeAssistant,
        clock=clock,
    )
    return runner, repository, storage, runtime_registry, clock, user_id, task_id


def claim(repository, clock):
    task = repository.claim_next(set(), now=clock.iso())
    assert task is not None
    return task


def test_transient_failure_schedules_2_second_retry(tmp_path):
    runner, repository, _, runtimes, clock, user_id, task_id = make_runner(
        tmp_path, failures=[RAGConnectionError("temporary token=secret")]
    )

    runner.run(claim(repository, clock))

    task = repository.get_task(user_id, task_id)
    assert task.status == "retry_wait"
    assert task.next_attempt_at == "2026-07-30T00:00:02Z"
    assert task.auto_retry_count == 1
    assert "secret" not in task.error_summary
    assert runtimes.acquired == runtimes.released == [user_id]
    assert FakeAssistant.close_count == 1


def test_three_auto_retries_then_failed(tmp_path):
    runner, repository, _, _, clock, user_id, task_id = make_runner(
        tmp_path, failures=[RAGConnectionError("temporary")] * 4
    )

    for delay in (2, 10, 30):
        runner.run(claim(repository, clock))
        clock.advance(delay)
    runner.run(claim(repository, clock))

    task = repository.get_task(user_id, task_id)
    assert task.status == "failed"
    assert task.auto_retry_count == 3
    assert task.total_attempt_count == 4


def test_success_moves_staged_file_to_formal_path_and_records_progress(tmp_path):
    runner, repository, storage, runtimes, clock, user_id, task_id = make_runner(tmp_path)
    claimed = claim(repository, clock)
    staged = storage.user_paths(user_id).root / claimed.staged_relative_path
    formal = storage.document_path(user_id, claimed.document_id, claimed.file_suffix)

    runner.run(claimed)

    completed = repository.get_task(user_id, task_id)
    assert completed.status == "succeeded"
    assert completed.progress == 100
    assert formal.read_bytes() == b"content"
    assert not staged.exists()
    assert FakeAssistant.calls[0][0] == str(formal)
    assert FakeAssistant.calls[0][1]["document_id"] == claimed.document_id
    assert FakeAssistant.calls[0][1]["original_name"] == "source.md"
    assert FakeAssistant.calls[0][1]["import_task_id"] == task_id
    assert runtimes.acquired == runtimes.released == [user_id]


def test_failure_removes_formal_file_but_preserves_staged_copy(tmp_path):
    runner, repository, storage, _, clock, user_id, task_id = make_runner(
        tmp_path, failures=[ValueError("corrupt document")]
    )
    claimed = claim(repository, clock)
    staged = storage.user_paths(user_id).root / claimed.staged_relative_path
    formal = storage.document_path(user_id, claimed.document_id, claimed.file_suffix)

    runner.run(claimed)

    failed = repository.get_task(user_id, task_id)
    assert failed.status == "failed"
    assert failed.error_code == "document_invalid"
    assert staged.exists()
    assert not formal.exists()
    assert not storage.temporary_document_path(
        user_id, claimed.document_id, claimed.file_suffix
    ).exists()


def test_legacy_failure_string_does_not_mark_task_succeeded(tmp_path):
    FakeAssistant.result = "❌ 文件不存在"
    runner, repository, storage, _, clock, user_id, task_id = make_runner(tmp_path)
    claimed = claim(repository, clock)
    staged = storage.user_paths(user_id).root / claimed.staged_relative_path
    formal = storage.document_path(user_id, claimed.document_id, claimed.file_suffix)

    runner.run(claimed)

    failed = repository.get_task(user_id, task_id)
    assert failed.status == "failed"
    assert failed.error_code == "document_invalid"
    assert staged.exists()
    assert not formal.exists()


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (RAGConnectionError("down"), ("rag_connection", True, "down")),
        (
            RAGAuthenticationError("key=secret"),
            ("rag_authentication", False, "RAG authentication failed"),
        ),
        (RAGConfigError("bad"), ("rag_config", False, "RAG configuration is invalid")),
        (ValueError("token=secret"), ("document_invalid", False, "token=***")),
        (RuntimeError("private"), ("unexpected_error", False, "RuntimeError")),
    ],
)
def test_classify_import_failure(error, expected):
    assert classify_import_failure(error) == expected


class BlockingRunner:
    def __init__(self):
        self.started = []
        self.release = threading.Event()
        self.lock = threading.Lock()

    def run(self, task):
        with self.lock:
            self.started.append(task)
        self.release.wait(timeout=5)


def wait_for(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not met before timeout")


def test_worker_pool_starts_four_non_daemon_workers_and_serializes_each_user(tmp_path):
    db_path = tmp_path / "app.db"
    initialize_database(db_path)
    storage = UserStorage(tmp_path / "data")
    repository = ImportTaskRepository(db_path)
    users = [
        AuthService(db_path).register(f"user-{index}", "correct horse battery").id
        for index in range(2)
    ]
    for user_id in users:
        batch_id = str(uuid.uuid4())
        creates = []
        for index in range(2):
            task_id = str(uuid.uuid4())
            staged = storage.staged_import_path(user_id, batch_id, task_id, ".md")
            staged.write_bytes(b"x")
            creates.append(
                ImportTaskCreate(
                    task_id=task_id,
                    batch_id=batch_id,
                    user_id=user_id,
                    document_id=str(uuid.uuid4()),
                    original_name=f"{index}.md",
                    file_suffix=".md",
                    size_bytes=1,
                    staged_relative_path=str(
                        staged.relative_to(storage.user_paths(user_id).root)
                    ),
                )
            )
        repository.create_batch(user_id, creates)
    blocking_runner = BlockingRunner()
    pool = ImportWorkerPool(
        repository,
        FakeRuntimeRegistry(storage),
        storage,
        runner=blocking_runner,
    )

    pool.start()
    try:
        wait_for(lambda: len(blocking_runner.started) == 2)
        assert {task.user_id for task in blocking_runner.started} == set(users)
        assert len(pool._worker_threads) == 4
        assert all(not thread.daemon for thread in pool._worker_threads)
    finally:
        blocking_runner.release.set()
        pool.stop(wait=True)
