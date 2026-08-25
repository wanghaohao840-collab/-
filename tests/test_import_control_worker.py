from __future__ import annotations

import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from app.auth import AuthService
from app.database import initialize_database
from app.import_models import ImportTaskCreate
from app.import_repository import ImportTaskRepository
from app.import_worker import ImportTaskRunner, ImportWorkerPool
from app.storage import UserStorage


CHECKPOINTS = ["parsing", "chunking", "embedding", "persisting", "committing"]


class FakeRuntimeRegistry:
    def __init__(self, storage):
        self.storage = storage
        self.acquired = []
        self.released = []

    def acquire_background(self, user_id):
        self.acquired.append(user_id)
        return SimpleNamespace(paths=self.storage.ensure_user_dirs(user_id))

    def release_background(self, user_id):
        self.released.append(user_id)


@dataclass
class AssistantState:
    target_stage: str | None = None
    compensation_failure: BaseException | None = None
    reached: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)
    load_calls: list[dict] = field(default_factory=list)
    compensation_calls: list[tuple[str, str]] = field(default_factory=list)
    close_count: int = 0


class ControlledAssistant:
    def __init__(self, state):
        self.state = state

    def load_document(self, _path, **kwargs):
        self.state.load_calls.append(kwargs)
        checkpoint = kwargs["control_checkpoint"]
        progress = kwargs["progress_callback"]
        for stage in CHECKPOINTS:
            progress(stage, 1, 1, stage)
            if stage == self.state.target_stage:
                self.state.reached.set()
                assert self.state.release.wait(timeout=3)
            checkpoint(stage)
        if self.state.target_stage == "post_load":
            self.state.reached.set()
            assert self.state.release.wait(timeout=3)
        return "ok"

    def compensate_import(self, document_id, import_task_id):
        self.state.compensation_calls.append((document_id, import_task_id))
        if self.state.compensation_failure is not None:
            raise self.state.compensation_failure

    def close(self):
        self.state.close_count += 1


def make_runner(tmp_path, state=None):
    state = state or AssistantState()
    db_path = tmp_path / "app.db"
    initialize_database(db_path)
    user_id = AuthService(db_path).register(
        "worker-control-user", "correct horse battery"
    ).id
    repository = ImportTaskRepository(db_path)
    storage = UserStorage(tmp_path / "data")
    runtime_registry = FakeRuntimeRegistry(storage)
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
    )
    runner = ImportTaskRunner(
        repository,
        runtime_registry,
        storage,
        assistant_factory=lambda **_kwargs: ControlledAssistant(state),
    )
    return SimpleNamespace(
        state=state,
        runner=runner,
        repository=repository,
        storage=storage,
        runtimes=runtime_registry,
        user_id=user_id,
        task_id=task_id,
        document_id=document_id,
        staged=staged,
    )


def run_until_control(harness, action):
    task = harness.repository.claim_next(set())
    assert task is not None and task.status == "running"
    finished = threading.Event()
    errors = []

    def invoke():
        try:
            harness.runner.run(task)
        except BaseException as error:
            errors.append(error)
        finally:
            finished.set()

    thread = threading.Thread(target=invoke)
    thread.start()
    assert harness.state.reached.wait(timeout=3)
    getattr(harness.repository, f"request_{action}")(
        harness.user_id,
        harness.task_id,
    )
    harness.state.release.set()
    assert finished.wait(timeout=3)
    thread.join()
    assert errors == []
    return task


@pytest.mark.parametrize("target_stage", [*CHECKPOINTS, "post_load"])
@pytest.mark.parametrize("action", ["pause", "cancel"])
def test_control_at_every_checkpoint_compensates_without_retry(
    tmp_path, target_stage, action
):
    harness = make_runner(tmp_path, AssistantState(target_stage=target_stage))

    claimed = run_until_control(harness, action)

    current = harness.repository.get_task(harness.user_id, harness.task_id)
    assert current.status == ("paused" if action == "pause" else "cancelled")
    assert current.auto_retry_count == 0
    assert current.total_attempt_count == 1
    assert harness.staged.exists() is (action == "pause")
    formal = harness.storage.document_path(
        harness.user_id, claimed.document_id, claimed.file_suffix
    )
    assert not formal.exists()
    assert harness.state.compensation_calls == [
        (harness.document_id, harness.task_id)
    ]
    assert harness.runtimes.acquired == harness.runtimes.released == [harness.user_id]


@pytest.mark.parametrize(
    ("action", "expected_code"),
    [("pause", "pause_cleanup_failed"), ("cancel", "cancel_cleanup_failed")],
)
def test_compensation_failure_records_safe_control_error_and_keeps_staging(
    tmp_path, action, expected_code
):
    harness = make_runner(
        tmp_path,
        AssistantState(
            target_stage="embedding",
            compensation_failure=RuntimeError("backend secret=hidden"),
        ),
    )

    run_until_control(harness, action)

    current = harness.repository.get_task(harness.user_id, harness.task_id)
    assert current.status == "failed"
    assert current.error_code == expected_code
    assert "hidden" not in current.error_summary
    assert "***" in current.error_summary
    assert harness.staged.exists()
    assert current.auto_retry_count == 0


def test_cancel_staging_cleanup_failure_does_not_report_cancelled(tmp_path, monkeypatch):
    harness = make_runner(tmp_path, AssistantState(target_stage="embedding"))
    monkeypatch.setattr(
        harness.runner,
        "_cleanup_staged_file",
        lambda _path: (_ for _ in ()).throw(OSError("staging cleanup failed")),
    )

    run_until_control(harness, "cancel")

    current = harness.repository.get_task(harness.user_id, harness.task_id)
    assert current.status == "failed"
    assert current.error_code == "cancel_cleanup_failed"
    assert harness.staged.exists()


def test_claimed_cancel_request_runs_cleanup_only_without_import(tmp_path):
    harness = make_runner(tmp_path)
    harness.repository.request_cancel(harness.user_id, harness.task_id)
    claimed = harness.repository.claim_next(set())
    assert claimed is not None and claimed.status == "cancel_requested"

    harness.runner.run(claimed)

    current = harness.repository.get_task(harness.user_id, harness.task_id)
    assert current.status == "cancelled"
    assert current.total_attempt_count == 0
    assert harness.state.load_calls == []
    assert harness.state.compensation_calls == [
        (harness.document_id, harness.task_id)
    ]
    assert not harness.staged.exists()
    assert harness.runtimes.acquired == harness.runtimes.released == [harness.user_id]


def test_cancel_between_final_checkpoint_and_success_transition_is_compensated(
    tmp_path, monkeypatch
):
    harness = make_runner(tmp_path)
    claimed = harness.repository.claim_next(set())
    original_mark_succeeded = harness.repository.mark_succeeded

    def request_cancel_then_mark(user_id, task_id, **kwargs):
        harness.repository.request_cancel(user_id, task_id)
        return original_mark_succeeded(user_id, task_id, **kwargs)

    monkeypatch.setattr(
        harness.repository,
        "mark_succeeded",
        request_cancel_then_mark,
    )

    harness.runner.run(claimed)

    current = harness.repository.get_task(harness.user_id, harness.task_id)
    assert current.status == "cancelled"
    assert current.auto_retry_count == 0
    assert harness.state.compensation_calls == [
        (harness.document_id, harness.task_id)
    ]
    assert not harness.staged.exists()


def test_cancel_after_cleanup_failure_is_cleanup_only_and_never_reimports(tmp_path):
    state = AssistantState(
        target_stage="persisting",
        compensation_failure=RuntimeError("cleanup unavailable"),
    )
    harness = make_runner(tmp_path, state)
    run_until_control(harness, "cancel")
    assert len(state.load_calls) == 1

    state.compensation_failure = None
    harness.repository.request_cancel(harness.user_id, harness.task_id)
    cleanup_claim = harness.repository.claim_next(set())
    assert cleanup_claim is not None and cleanup_claim.status == "cancel_requested"
    harness.runner.run(cleanup_claim)

    current = harness.repository.get_task(harness.user_id, harness.task_id)
    assert current.status == "cancelled"
    assert len(state.load_calls) == 1
    assert state.compensation_calls == [
        (harness.document_id, harness.task_id),
        (harness.document_id, harness.task_id),
    ]
    assert not harness.staged.exists()


def test_runner_crash_marks_claimed_control_work_failed(tmp_path):
    harness = make_runner(tmp_path)
    harness.repository.request_cancel(harness.user_id, harness.task_id)
    claimed = harness.repository.claim_next(set())
    pool = ImportWorkerPool(
        harness.repository,
        harness.runtimes,
        harness.storage,
        runner=harness.runner,
    )

    pool._record_runner_failure(claimed)

    current = harness.repository.get_task(harness.user_id, harness.task_id)
    assert current.status == "failed"
    assert current.error_code == "cancel_cleanup_failed"


def test_runner_crash_never_overwrites_control_terminal_state(tmp_path):
    harness = make_runner(tmp_path)
    harness.repository.request_cancel(harness.user_id, harness.task_id)
    claimed = harness.repository.claim_next(set())
    harness.repository.mark_cancelled(harness.user_id, harness.task_id)
    pool = ImportWorkerPool(
        harness.repository,
        harness.runtimes,
        harness.storage,
        runner=harness.runner,
    )

    pool._record_runner_failure(claimed)

    assert harness.repository.get_task(harness.user_id, harness.task_id).status == "cancelled"


def test_runner_crash_releases_control_claim_after_persistent_database_busy(monkeypatch):
    task = SimpleNamespace(
        user_id="user-a",
        task_id="task-a",
        status="cancel_requested",
        control_claimed_at="claimed",
    )

    class BusyRepository:
        def __init__(self):
            self.released = []

        def get_task(self, _user_id, _task_id):
            return task

        def mark_control_failed(self, *_args, **_kwargs):
            raise sqlite3.OperationalError("database is locked")

        def release_control_claim(self, user_id, task_id):
            self.released.append((user_id, task_id))

    repository = BusyRepository()
    monkeypatch.setattr("app.import_worker.time.sleep", lambda _delay: None)
    pool = ImportWorkerPool(
        repository,
        SimpleNamespace(),
        SimpleNamespace(),
        runner=SimpleNamespace(),
    )

    pool._record_runner_failure(task)

    assert repository.released == [("user-a", "task-a")]
