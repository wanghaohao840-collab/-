from __future__ import annotations

import threading
import time
import uuid
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.auth import AuthService
from app.database import initialize_database
from app.import_models import ImportTaskCreate
from app.import_repository import ImportTaskRepository
import app.import_worker as import_worker
from app.import_worker import (
    ImportTaskRunner,
    ImportWorkerPool,
    classify_import_failure,
)
from app.storage import UserStorage
from assistants.pdf_learning_assistant import PDFLearningAssistant
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
    before_progress = None
    after_progress = None
    commit_count = 0

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def load_document(self, path, **kwargs):
        type(self).calls.append((path, kwargs))
        if self.failures:
            raise self.failures.pop(0)
        callback = kwargs["progress_callback"]
        for update in (
            ("parsing", 1, 1, "parsed"),
            ("embedding", 1, 2, "embedded"),
            ("committing", 0, 1, "committing"),
        ):
            if type(self).before_progress is not None:
                type(self).before_progress(update[0])
            callback(*update)
            if type(self).after_progress is not None:
                type(self).after_progress(update[0])
        type(self).commit_count += 1
        return type(self).result

    def close(self):
        type(self).close_count += 1


@pytest.fixture(autouse=True)
def reset_fake_assistant():
    FakeAssistant.failures = []
    FakeAssistant.result = "ok"
    FakeAssistant.calls = []
    FakeAssistant.close_count = 0
    FakeAssistant.before_progress = None
    FakeAssistant.after_progress = None
    FakeAssistant.commit_count = 0


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


def test_staged_progress_is_first_and_percentages_are_monotonic(tmp_path):
    runner, repository, _, _, clock, user_id, task_id = make_runner(tmp_path)
    claimed = claim(repository, clock)
    updates = []
    original_update = repository.update_progress

    def record_update(*args, **kwargs):
        updates.append((args[2], args[3]))
        return original_update(*args, **kwargs)

    repository.update_progress = record_update
    runner.run(claimed)

    assert updates[0] == ("staged", 10)
    assert [progress for _, progress in updates] == sorted(
        progress for _, progress in updates
    )
    assert repository.get_task(user_id, task_id).progress == 100


def test_import_cancelled_is_direct_baseexception_control_signal():
    assert issubclass(import_worker.ImportCancelled, BaseException)
    assert not issubclass(import_worker.ImportCancelled, Exception)


@pytest.mark.parametrize(
    "stage", ["staged", "parsing", "chunking", "embedding", "persisting"]
)
def test_progress_callback_observes_cancel_at_every_non_committing_stage(
    tmp_path, stage
):
    runner, repository, _, _, clock, user_id, _ = make_runner(tmp_path)
    task = claim(repository, clock)
    repository.request_cancel(user_id, task.batch_id, task.task_id)

    with pytest.raises(import_worker.ImportCancelled):
        runner._progress_callback(task)(stage, 1, 1, stage)

    current = repository.get_task(user_id, task.task_id)
    assert current.status == "running"
    assert current.cancel_requested_at is not None


def test_cancel_before_staging_removes_staged_file_and_never_retries(tmp_path):
    runner, repository, storage, _, clock, user_id, task_id = make_runner(tmp_path)
    task = claim(repository, clock)
    staged = storage.user_paths(user_id).root / task.staged_relative_path
    repository.request_cancel(user_id, task.batch_id, task.task_id)

    runner.run(task)

    cancelled = repository.get_task(user_id, task_id)
    assert cancelled.status == "cancelled"
    assert cancelled.stage == "cancelled"
    assert cancelled.auto_retry_count == 0
    assert cancelled.next_attempt_at is None
    assert not staged.exists()
    assert FakeAssistant.calls == []


def test_running_cancel_during_embedding_removes_attempt_files_and_never_commits(
    tmp_path
):
    runner, repository, storage, _, clock, user_id, task_id = make_runner(tmp_path)
    task = claim(repository, clock)
    staged = storage.user_paths(user_id).root / task.staged_relative_path
    formal = storage.document_path(user_id, task.document_id, task.file_suffix)
    temporary = storage.temporary_document_path(
        user_id, task.document_id, task.file_suffix
    )

    def cancel_at_embedding(stage):
        if stage == "embedding":
            repository.request_cancel(user_id, task.batch_id, task.task_id)

    FakeAssistant.before_progress = cancel_at_embedding

    runner.run(task)

    cancelled = repository.get_task(user_id, task_id)
    assert cancelled.status == "cancelled"
    assert cancelled.auto_retry_count == 0
    assert cancelled.next_attempt_at is None
    assert FakeAssistant.commit_count == 0
    assert not staged.exists()
    assert not formal.exists()
    assert not temporary.exists()


def test_cancel_wins_commit_boundary_and_prevents_assistant_commit(tmp_path):
    runner, repository, storage, _, clock, user_id, task_id = make_runner(tmp_path)
    task = claim(repository, clock)
    staged = storage.user_paths(user_id).root / task.staged_relative_path

    def cancel_at_gate(stage):
        if stage == "committing":
            decision = repository.request_cancel(
                user_id, task.batch_id, task.task_id
            )
            assert decision.outcome == "cancel_requested"

    FakeAssistant.before_progress = cancel_at_gate

    runner.run(task)

    cancelled = repository.get_task(user_id, task_id)
    assert cancelled.status == "cancelled"
    assert cancelled.auto_retry_count == 0
    assert FakeAssistant.commit_count == 0
    assert not staged.exists()


def test_commit_gate_wins_and_later_cancel_is_rejected(tmp_path):
    runner, repository, _, _, clock, user_id, task_id = make_runner(tmp_path)
    task = claim(repository, clock)
    decisions = []

    def cancel_after_gate(stage):
        if stage == "committing":
            decisions.append(
                repository.request_cancel(user_id, task.batch_id, task.task_id)
            )

    FakeAssistant.after_progress = cancel_after_gate

    runner.run(task)

    succeeded = repository.get_task(user_id, task_id)
    assert decisions[0].outcome == "not_cancellable"
    assert succeeded.status == "succeeded"
    assert succeeded.cancel_requested_at is None
    assert FakeAssistant.commit_count == 1


def test_cancel_cleanup_failure_still_persists_cancelled(tmp_path, monkeypatch):
    runner, repository, storage, _, clock, user_id, task_id = make_runner(tmp_path)
    task = claim(repository, clock)
    staged = storage.user_paths(user_id).root / task.staged_relative_path
    repository.request_cancel(user_id, task.batch_id, task.task_id)
    monkeypatch.setattr(
        runner,
        "_cleanup_staged_file",
        lambda _path: (_ for _ in ()).throw(
            OSError(r"D:\private\staging api_key=secret")
        ),
    )

    runner.run(task)

    cancelled = repository.get_task(user_id, task_id)
    assert cancelled.status == "cancelled"
    assert cancelled.error_code is None
    assert cancelled.error_summary is None
    assert staged.exists()


@pytest.mark.parametrize("residue_kind", ["temporary", "formal"])
def test_restart_reconciles_exact_cancelled_attempt_file_after_unlink_failure(
    tmp_path, monkeypatch, residue_kind
):
    runner, repository, storage, runtimes, clock, user_id, task_id = make_runner(
        tmp_path
    )
    task = claim(repository, clock)
    staged = storage.user_paths(user_id).root / task.staged_relative_path
    formal = storage.document_path(user_id, task.document_id, task.file_suffix)
    temporary = storage.temporary_document_path(
        user_id, task.document_id, task.file_suffix
    )
    target = temporary if residue_kind == "temporary" else formal
    cleanup_blocked = True
    real_unlink = Path.unlink

    def cancel_with_both_attempt_paths(stage):
        if stage == "embedding":
            temporary.write_bytes(b"temporary residue")
            repository.request_cancel(user_id, task.batch_id, task.task_id)

    def fail_selected(path, *args, **kwargs):
        if cleanup_blocked and path == target:
            raise OSError("attempt cleanup unavailable")
        return real_unlink(path, *args, **kwargs)

    FakeAssistant.before_progress = cancel_with_both_attempt_paths
    monkeypatch.setattr(Path, "unlink", fail_selected)

    runner.run(task)

    assert repository.get_task(user_id, task_id).status == "cancelled"
    assert target.exists()
    assert not staged.exists()

    cleanup_blocked = False
    restarted = ImportWorkerPool(
        repository, runtimes, storage, runner=BlockingRunner(), worker_count=1
    )
    restarted.start()
    restarted.stop(wait=True)

    assert not temporary.exists()
    assert not formal.exists()
    assert repository.get_task(user_id, task_id).status == "cancelled"


def test_staged_cleanup_failure_preserves_success_and_staged_copy(
    tmp_path, monkeypatch
):
    runner, repository, storage, _, clock, user_id, task_id = make_runner(tmp_path)
    claimed = claim(repository, clock)
    staged = storage.user_paths(user_id).root / claimed.staged_relative_path
    formal = storage.document_path(user_id, claimed.document_id, claimed.file_suffix)

    def fail_cleanup(_path):
        raise OSError("staging volume unavailable")

    monkeypatch.setattr(runner, "_cleanup_staged_file", fail_cleanup)
    runner.run(claimed)

    completed = repository.get_task(user_id, task_id)
    assert completed.status == "succeeded"
    assert completed.error_code is None
    assert staged.exists()
    assert formal.exists()


def test_pool_start_reconciles_only_valid_succeeded_staging(tmp_path, monkeypatch):
    runner, repository, storage, runtimes, clock, user_id, task_id = make_runner(
        tmp_path
    )
    succeeded = claim(repository, clock)
    succeeded_staged = (
        storage.user_paths(user_id).root / succeeded.staged_relative_path
    )
    monkeypatch.setattr(
        runner,
        "_cleanup_staged_file",
        lambda _path: (_ for _ in ()).throw(OSError("staging volume unavailable")),
    )
    runner.run(succeeded)
    assert repository.get_task(user_id, task_id).status == "succeeded"
    assert succeeded_staged.exists()

    failed_batch_id = str(uuid.uuid4())
    failed_task_id = str(uuid.uuid4())
    failed_staged = storage.staged_import_path(
        user_id, failed_batch_id, failed_task_id, ".md"
    )
    failed_staged.write_bytes(b"retry me")
    repository.create_batch(
        user_id,
        [
            ImportTaskCreate(
                task_id=failed_task_id,
                batch_id=failed_batch_id,
                user_id=user_id,
                document_id=str(uuid.uuid4()),
                original_name="failed.md",
                file_suffix=".md",
                size_bytes=8,
                staged_relative_path=str(
                    failed_staged.relative_to(storage.user_paths(user_id).root)
                ),
            )
        ],
    )
    failed = repository.claim_next(set())
    assert failed is not None
    repository.mark_failed(user_id, failed.task_id, "document_invalid", "bad")

    restarted = ImportWorkerPool(
        repository, runtimes, storage, runner=BlockingRunner(), worker_count=1
    )
    restarted.start()
    restarted.stop(wait=True)

    assert not succeeded_staged.exists()
    assert failed_staged.read_bytes() == b"retry me"


def test_pool_start_reconciles_only_exact_cancelled_staging(tmp_path):
    _, repository, storage, runtimes, _, user_id, _ = make_runner(tmp_path)
    first = repository.claim_next(set())
    assert first is not None
    repository.mark_failed(user_id, first.task_id, "document_invalid", "keep")
    failed_staged = storage.user_paths(user_id).root / first.staged_relative_path
    failed_temporary = storage.temporary_document_path(
        user_id, first.document_id, first.file_suffix
    )
    failed_formal = storage.document_path(
        user_id, first.document_id, first.file_suffix
    )
    failed_temporary.write_bytes(b"failed temporary")
    failed_formal.write_bytes(b"failed formal")

    batch_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    staged = storage.staged_import_path(user_id, batch_id, task_id, ".md")
    staged.write_bytes(b"cancelled residue")
    unrelated = staged.parent / "unrelated.keep"
    unrelated.write_bytes(b"keep")
    repository.create_batch(
        user_id,
        [
            ImportTaskCreate(
                task_id=task_id,
                batch_id=batch_id,
                user_id=user_id,
                document_id=str(uuid.uuid4()),
                original_name="cancelled.md",
                file_suffix=".md",
                size_bytes=17,
                staged_relative_path=str(
                    staged.relative_to(storage.user_paths(user_id).root)
                ),
            )
        ],
    )
    decision = repository.request_cancel(user_id, batch_id, task_id)
    assert decision.outcome == "cancelled"

    restarted = ImportWorkerPool(
        repository, runtimes, storage, runner=BlockingRunner(), worker_count=1
    )
    restarted.start()
    restarted.stop(wait=True)

    assert not staged.exists()
    assert unrelated.read_bytes() == b"keep"
    assert failed_staged.read_bytes() == b"content"
    assert failed_temporary.read_bytes() == b"failed temporary"
    assert failed_formal.read_bytes() == b"failed formal"


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


def test_structured_error_code_is_whitelisted_before_persistence(tmp_path):
    error = RuntimeError("backend rejected token=secret")
    error.error_code = "token=secret"
    error.retryable = False

    runner, repository, _, _, clock, user_id, task_id = make_runner(
        tmp_path, failures=[error]
    )
    runner.run(claim(repository, clock))
    task = repository.get_task(user_id, task_id)

    assert task.error_code == "unexpected_error"
    assert task.error_summary is not None
    assert "secret" not in task.error_code
    assert "secret" not in task.error_summary


def test_persisted_error_summary_redacts_common_credential_formats(tmp_path):
    _, repository, _, _, clock, user_id, task_id = make_runner(tmp_path)
    claimed = claim(repository, clock)
    repository.mark_failed(
        user_id,
        claimed.task_id,
        "unexpected_error",
        "backend rejected password: p@ss token=tok Authorization: Bearer bearer "
        'url=https://alice:secret@host.local/api json={"client_secret":"json-secret"}',
        now=clock.iso(),
    )

    task = repository.get_task(user_id, task_id)
    assert task is not None
    assert "backend rejected" in task.error_summary
    for secret in ("p@ss", "token=tok", "Bearer bearer", "alice:secret", "json-secret"):
        assert secret not in task.error_summary


@pytest.mark.parametrize(
    ("error_code", "retryable"),
    [
        ("rag_collection", False),
        ("rag_document_too_large", False),
        ("rag_embedding", True),
        ("memory_import_event", True),
    ],
)
def test_classify_import_failure_preserves_known_structured_codes(
    error_code, retryable
):
    error = RuntimeError("structured backend failure")
    error.error_code = error_code
    error.retryable = retryable

    assert classify_import_failure(error) == (
        error_code,
        retryable,
        "structured backend failure",
    )


class BlockingRunner:
    def __init__(self):
        self.started = []
        self.release = threading.Event()
        self.lock = threading.Lock()

    def run(self, task):
        with self.lock:
            self.started.append(task)
        self.release.wait(timeout=5)


class CrashingRunner:
    def run(self, _task):
        raise RuntimeError("runner token=private-token")


class TerminalThenCrashRunner:
    def __init__(self, repository):
        self.repository = repository

    def run(self, task):
        self.repository.mark_succeeded(task.user_id, task.task_id)
        raise RuntimeError("crashed after success")


class ActiveDocumentImportService:
    def __init__(self, repository, task_id):
        self.repository = repository
        self.task_id = task_id

    def has_active_task_for_document(self, user_id, document_id):
        task = self.repository.get_task(user_id, self.task_id)
        return task is not None and task.document_id == document_id and task.status in {
            "queued",
            "running",
            "retry_wait",
        }


def wait_for(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not met before timeout")


def test_worker_pool_marks_crashed_runner_task_failed_and_stays_alive(tmp_path):
    _, repository, storage, runtimes, _, user_id, task_id = make_runner(tmp_path)
    pool = ImportWorkerPool(
        repository, runtimes, storage, runner=CrashingRunner(), worker_count=1
    )

    pool.start()
    try:
        wait_for(lambda: repository.get_task(user_id, task_id).status == "failed")
        task = repository.get_task(user_id, task_id)
        assert task.error_code == "unexpected_error"
        assert "private-token" not in task.error_summary
        assert pool._worker_threads[0].is_alive()
    finally:
        pool.stop(wait=True)


def test_worker_pool_preserves_terminal_transition_before_runner_crash(tmp_path):
    _, repository, storage, runtimes, _, user_id, task_id = make_runner(tmp_path)
    pool = ImportWorkerPool(
        repository,
        runtimes,
        storage,
        runner=TerminalThenCrashRunner(repository),
        worker_count=1,
    )

    pool.start()
    try:
        wait_for(lambda: repository.get_task(user_id, task_id).status == "succeeded")
        task = repository.get_task(user_id, task_id)
        assert task.error_code is None
        assert task.error_summary is None
    finally:
        pool.stop(wait=True)


def test_worker_pool_retries_busy_failure_transition(tmp_path, monkeypatch):
    _, repository, storage, runtimes, _, user_id, task_id = make_runner(tmp_path)
    calls = 0
    original_mark_failed = repository.mark_failed

    def busy_then_mark_failed(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise sqlite3.OperationalError("database is locked")
        return original_mark_failed(*args, **kwargs)

    monkeypatch.setattr(repository, "mark_failed", busy_then_mark_failed)
    pool = ImportWorkerPool(
        repository, runtimes, storage, runner=CrashingRunner(), worker_count=1
    )

    pool.start()
    try:
        wait_for(lambda: repository.get_task(user_id, task_id).status == "failed")
        assert calls == 3
    finally:
        pool.stop(wait=True)


def test_worker_pool_recovers_first_terminal_write_failure_and_runs_next_task(
    tmp_path, monkeypatch
):
    runner, repository, storage, runtimes, _, user_id, first_task_id = make_runner(
        tmp_path, failures=[ValueError("corrupt document")]
    )
    second_batch_id = str(uuid.uuid4())
    second_task_id = str(uuid.uuid4())
    second_staged = storage.staged_import_path(
        user_id, second_batch_id, second_task_id, ".md"
    )
    second_staged.write_bytes(b"second")
    repository.create_batch(
        user_id,
        [
            ImportTaskCreate(
                task_id=second_task_id,
                batch_id=second_batch_id,
                user_id=user_id,
                document_id=str(uuid.uuid4()),
                original_name="second.md",
                file_suffix=".md",
                size_bytes=6,
                staged_relative_path=str(
                    second_staged.relative_to(storage.user_paths(user_id).root)
                ),
            )
        ],
    )
    original_mark_failed = repository.mark_failed
    failed_once = False

    def fail_first_terminal_write(*args, **kwargs):
        nonlocal failed_once
        if not failed_once:
            failed_once = True
            raise OSError("terminal state store unavailable")
        return original_mark_failed(*args, **kwargs)

    monkeypatch.setattr(repository, "mark_failed", fail_first_terminal_write)
    pool = ImportWorkerPool(
        repository, runtimes, storage, runner=runner, worker_count=1
    )

    pool.start()
    try:
        wait_for(
            lambda: repository.get_task(user_id, second_task_id).status == "succeeded"
        )
    finally:
        pool.stop(wait=True)

    assert failed_once is True
    assert repository.get_task(user_id, first_task_id).status == "failed"
    assert repository.get_task(user_id, second_task_id).status == "succeeded"


def test_two_session_delete_during_import_commit_is_refused(tmp_path, monkeypatch):
    runner, repository, storage, runtimes, clock, user_id, task_id = make_runner(
        tmp_path
    )
    claimed = claim(repository, clock)
    shared_runtime = SimpleNamespace(
        paths=storage.ensure_user_dirs(user_id),
        lock=threading.RLock(),
        memory_tool=SimpleNamespace(),
        rag_tool=SimpleNamespace(),
        history=SimpleNamespace(),
        reports=SimpleNamespace(),
        coordinator=SimpleNamespace(),
    )
    shared_runtime.import_task_service = ActiveDocumentImportService(
        repository, task_id
    )
    monkeypatch.setattr(
        runtimes, "acquire_background", lambda _user_id: shared_runtime
    )
    committing = threading.Event()
    allow_success = threading.Event()
    original_try_begin_committing = repository.try_begin_committing

    def pause_after_commit_gate(*args, **kwargs):
        result = original_try_begin_committing(*args, **kwargs)
        committing.set()
        assert allow_success.wait(timeout=3)
        return result

    monkeypatch.setattr(
        repository, "try_begin_committing", pause_after_commit_gate
    )
    formal = storage.document_path(
        user_id, claimed.document_id, claimed.file_suffix
    )
    import_thread = threading.Thread(target=runner.run, args=(claimed,))
    import_thread.start()
    assert committing.wait(timeout=3)

    foreground = object.__new__(PDFLearningAssistant)
    foreground.user_id = user_id
    foreground.runtime = shared_runtime
    foreground._lock = shared_runtime.lock
    foreground.current_document_id = claimed.document_id
    foreground.current_document = str(formal)

    def delete_formal(_document_id):
        formal.unlink(missing_ok=True)
        return "deleted"

    monkeypatch.setattr(foreground, "_delete_document_coordinated", delete_formal)
    try:
        result = foreground.delete_current_document()
    finally:
        allow_success.set()
        import_thread.join(timeout=3)

    assert not import_thread.is_alive()
    assert "import is active" in result
    assert repository.get_task(user_id, task_id).status == "succeeded"
    assert formal.exists()
    assert foreground.current_document_id == claimed.document_id


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


def test_worker_pool_notify_wakes_scheduler_promptly(tmp_path):
    db_path = tmp_path / "app.db"
    initialize_database(db_path)
    storage = UserStorage(tmp_path / "data")
    repository = ImportTaskRepository(db_path)
    user_id = AuthService(db_path).register(
        "notify-user", "correct horse battery"
    ).id
    batch_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    staged = storage.staged_import_path(user_id, batch_id, task_id, ".md")
    staged.write_bytes(b"x")
    blocking_runner = BlockingRunner()
    pool = ImportWorkerPool(
        repository,
        FakeRuntimeRegistry(storage),
        storage,
        runner=blocking_runner,
    )

    original_claim_next = repository.claim_next
    first_claim = True

    def claim_next_with_queued_task(blocked_user_ids, *args, **kwargs):
        nonlocal first_claim
        if first_claim:
            first_claim = False
            # Queue the task and notify before the scheduler enters its idle
            # wait.  A generation predicate must observe this notification;
            # a plain Condition wait would miss it and sleep for its timeout.
            repository.create_batch(
                user_id,
                [
                    ImportTaskCreate(
                        task_id=task_id,
                        batch_id=batch_id,
                        user_id=user_id,
                        document_id=str(uuid.uuid4()),
                        original_name="notify.md",
                        file_suffix=".md",
                        size_bytes=1,
                        staged_relative_path=str(
                            staged.relative_to(storage.user_paths(user_id).root)
                        ),
                    )
                ],
            )
            pool.notify()
            return None
        return original_claim_next(blocked_user_ids, *args, **kwargs)

    repository.claim_next = claim_next_with_queued_task
    pool.start()
    try:
        started_at = time.monotonic()
        pool.notify()
        wait_for(lambda: bool(blocking_runner.started), timeout=0.75)
        assert time.monotonic() - started_at < 0.75
    finally:
        blocking_runner.release.set()
        pool.stop(wait=True)


def test_worker_pool_requeues_claim_returned_after_stop_begins(tmp_path):
    db_path = tmp_path / "app.db"
    initialize_database(db_path)
    storage = UserStorage(tmp_path / "data")
    repository = ImportTaskRepository(db_path)
    user_id = AuthService(db_path).register(
        "shutdown-race-user", "correct horse battery"
    ).id
    batch_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    staged = storage.staged_import_path(user_id, batch_id, task_id, ".md")
    staged.write_bytes(b"x")
    repository.create_batch(
        user_id,
        [
            ImportTaskCreate(
                task_id=task_id,
                batch_id=batch_id,
                user_id=user_id,
                document_id=str(uuid.uuid4()),
                original_name="shutdown.md",
                file_suffix=".md",
                size_bytes=1,
                staged_relative_path=str(
                    staged.relative_to(storage.user_paths(user_id).root)
                ),
            )
        ],
    )
    claim_entered = threading.Event()
    allow_claim = threading.Event()
    original_claim_next = repository.claim_next

    def blocked_claim_next(blocked_user_ids, *args, **kwargs):
        claim_entered.set()
        assert allow_claim.wait(timeout=3)
        return original_claim_next(blocked_user_ids, *args, **kwargs)

    repository.claim_next = blocked_claim_next
    blocking_runner = BlockingRunner()
    pool = ImportWorkerPool(
        repository,
        FakeRuntimeRegistry(storage),
        storage,
        runner=blocking_runner,
        worker_count=1,
    )

    pool.start()
    assert claim_entered.wait(timeout=3)
    pool.stop(wait=False)
    allow_claim.set()
    assert pool._scheduler_thread is not None
    pool._scheduler_thread.join(timeout=3)
    blocking_runner.release.set()
    pool.stop(wait=True)

    task = repository.get_task(user_id, task_id)
    assert blocking_runner.started == []
    assert task.status == "queued"
    assert task.stage == "queued"
    assert task.progress == 0
    assert task.started_at is None
    assert task.total_attempt_count == 0
