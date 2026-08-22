from __future__ import annotations

import io
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.auth import AuthService
from app.database import initialize_database
from app.import_repository import ImportTaskRepository, InvalidImportTransition
import app.import_service as import_service
from app.import_models import ImportLimits
from app.import_service import ImportTaskService
from app.storage import UserStorage


class FakeSessionRegistry:
    def __init__(self, sessions):
        self.sessions = sessions
        self.calls = []

    def get_session(self, token):
        self.calls.append(token)
        if token not in self.sessions:
            raise ValueError("invalid session")
        session = self.sessions[token]
        return session if hasattr(session, "user_id") else SimpleNamespace(user_id=session)


class FakeWorkerPool:
    def __init__(self):
        self.notify_count = 0

    def notify(self):
        self.notify_count += 1


class FakeFile:
    def __init__(self, path: Path):
        self.name = str(path)


class TrackingStream(io.BytesIO):
    def __init__(self, value: bytes):
        super().__init__(value)
        self.close_count = 0

    def close(self):
        self.close_count += 1
        super().close()


class FailingStream(io.BytesIO):
    def read(self, size=-1):
        raise OSError(r"D:\private\upload.md token=secret")


def make_import_service(tmp_path):
    db_path = tmp_path / "app.db"
    initialize_database(db_path)
    user = AuthService(db_path).register("import-user", "correct horse battery")
    other = AuthService(db_path).register("other-user", "correct horse battery")
    repository = ImportTaskRepository(db_path)
    storage = UserStorage(tmp_path / "data")
    sessions = FakeSessionRegistry({"valid-token": user.id, "other-token": other.id})
    workers = FakeWorkerPool()
    service = ImportTaskService(sessions, repository, storage, workers)
    return service, repository, storage, workers, user.id, other.id


def uploaded_file(tmp_path: Path, name: str, content: bytes) -> FakeFile:
    path = tmp_path / "uploads" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return FakeFile(path)


def test_submit_batch_stages_all_files_before_creating_tasks(tmp_path):
    service, repo, storage, workers, user_id, _ = make_import_service(tmp_path)
    files = [
        uploaded_file(tmp_path, "a.md", b"alpha"),
        uploaded_file(tmp_path, "b.md", b"beta"),
    ]
    progress = []

    result = service.submit_batch(
        "valid-token", files, progress=lambda value, **kwargs: progress.append((value, kwargs))
    )

    assert result.total == 2
    assert repo.get_batch(user_id, result.batch_id).queued == 2
    staged = list((storage.user_paths(user_id).imports / result.batch_id).iterdir())
    assert sorted(path.read_bytes() for path in staged) == [b"alpha", b"beta"]
    assert {task.original_name for task in result.tasks} == {"a.md", "b.md"}
    assert all(Path(task.staged_relative_path).parts[0] == "imports" for task in result.tasks)
    assert progress[-1][0] == (2, 2)
    assert workers.notify_count == 1


def test_submit_batch_cleans_partial_stage_on_replace_failure(tmp_path, monkeypatch):
    service, repo, storage, workers, user_id, _ = make_import_service(tmp_path)
    files = [
        uploaded_file(tmp_path, "a.md", b"a"),
        uploaded_file(tmp_path, "b.md", b"b"),
    ]
    real_replace = os.replace

    calls = 0

    def fail_on_second_replace(source, target):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("sensitive server detail")
        return real_replace(source, target)

    monkeypatch.setattr(import_service.os, "replace", fail_on_second_replace)

    with pytest.raises(ValueError, match="could not stage") as error:
        service.submit_batch("valid-token", files)

    assert "sensitive" not in str(error.value)
    assert repo.list_batches(user_id) == []
    imports = storage.user_paths(user_id).imports
    assert not imports.exists() or list(imports.iterdir()) == []
    assert workers.notify_count == 0


def test_path_and_stream_inputs_share_durable_staging_core(tmp_path):
    service, repo, storage, workers, user_id, _ = make_import_service(tmp_path)
    path_result = service.submit_batch(
        "valid-token", [uploaded_file(tmp_path, "same.md", b"same bytes")]
    )
    stream = TrackingStream(b"same bytes")

    stream_result = service.submit_uploads(
        "valid-token", [import_service.ImportUpload("same.md", stream)]
    )

    path_task = path_result.tasks[0]
    stream_task = stream_result.tasks[0]
    assert (
        path_task.original_name,
        path_task.file_suffix,
        path_task.size_bytes,
    ) == (
        stream_task.original_name,
        stream_task.file_suffix,
        stream_task.size_bytes,
    )
    assert (
        storage.user_paths(user_id).root / path_task.staged_relative_path
    ).read_bytes() == (
        storage.user_paths(user_id).root / stream_task.staged_relative_path
    ).read_bytes()
    assert stream.closed is False
    assert stream.close_count == 0
    assert len(repo.list_batches(user_id)) == 2
    assert workers.notify_count == 2


def test_submit_uploads_enforces_actual_file_bytes_and_cleans_partial(tmp_path):
    service, repo, storage, workers, user_id, _ = make_import_service(tmp_path)
    service.limits = ImportLimits(max_files=2, max_file_bytes=10, max_batch_bytes=20)
    stream = TrackingStream(b"x" * 11)

    with pytest.raises(import_service.ImportLimitError) as captured:
        service.submit_uploads(
            "valid-token", [import_service.ImportUpload("too-large.txt", stream)]
        )

    assert captured.value.code == "import_file_too_large"
    assert captured.value.status_code == 413
    assert repo.list_batches(user_id) == []
    imports = storage.user_paths(user_id).imports
    assert not imports.exists() or list(imports.rglob("*")) == []
    assert stream.closed is False
    assert workers.notify_count == 0


def test_submit_batch_ignores_lying_path_size_metadata(tmp_path, monkeypatch):
    service, repo, storage, _, user_id, _ = make_import_service(tmp_path)
    source = uploaded_file(tmp_path, "lying.md", b"x" * 11)
    service.limits = ImportLimits(max_files=2, max_file_bytes=10, max_batch_bytes=20)
    source_path = Path(source.name)
    real_stat = Path.stat

    def lying_stat(path, *args, **kwargs):
        result = real_stat(path, *args, **kwargs)
        if path == source_path:
            values = list(result)
            values[6] = 1
            return os.stat_result(values)
        return result

    monkeypatch.setattr(Path, "stat", lying_stat)

    with pytest.raises(import_service.ImportLimitError) as captured:
        service.submit_batch("valid-token", [source])

    assert captured.value.code == "import_file_too_large"
    assert repo.list_batches(user_id) == []
    imports = storage.user_paths(user_id).imports
    assert not imports.exists() or list(imports.rglob("*")) == []


def test_submit_uploads_enforces_actual_batch_bytes(tmp_path):
    service, repo, storage, _, user_id, _ = make_import_service(tmp_path)
    service.limits = ImportLimits(max_files=2, max_file_bytes=10, max_batch_bytes=10)

    with pytest.raises(import_service.ImportLimitError) as captured:
        service.submit_uploads(
            "valid-token",
            [
                import_service.ImportUpload("a.txt", io.BytesIO(b"a" * 6)),
                import_service.ImportUpload("b.txt", io.BytesIO(b"b" * 5)),
            ],
        )

    assert captured.value.code == "import_batch_too_large"
    assert repo.list_batches(user_id) == []
    imports = storage.user_paths(user_id).imports
    assert not imports.exists() or list(imports.rglob("*")) == []


@pytest.mark.parametrize(
    ("count", "code"),
    [
        (0, "import_no_files"),
        (3, "import_too_many_files"),
    ],
)
def test_submit_uploads_enforces_file_count(tmp_path, count, code):
    service, repo, storage, workers, user_id, _ = make_import_service(tmp_path)
    service.limits = ImportLimits(max_files=2, max_file_bytes=10, max_batch_bytes=20)
    uploads = [
        import_service.ImportUpload(f"{index}.md", io.BytesIO(b"x"))
        for index in range(count)
    ]

    with pytest.raises(import_service.ImportLimitError) as captured:
        service.submit_uploads("valid-token", uploads)

    assert captured.value.code == code
    assert repo.list_batches(user_id) == []
    assert not storage.user_paths(user_id).imports.exists()
    assert workers.notify_count == 0


def test_submit_uploads_sanitizes_client_name_and_generates_paths(tmp_path):
    service, _, storage, _, user_id, _ = make_import_service(tmp_path)

    result = service.submit_uploads(
        "valid-token",
        [import_service.ImportUpload(r"..\private\notes.md", io.BytesIO(b"body"))],
    )

    task = result.tasks[0]
    staged = storage.user_paths(user_id).root / task.staged_relative_path
    assert task.original_name == "notes.md"
    assert "private" not in task.staged_relative_path
    assert staged.name == f"{task.task_id}.md"


@pytest.mark.parametrize("name", ["bad.exe", "", ".", ".."])
def test_submit_uploads_rejects_invalid_name_or_suffix_without_staging(tmp_path, name):
    service, repo, storage, _, user_id, _ = make_import_service(tmp_path)

    with pytest.raises(ValueError):
        service.submit_uploads(
            "valid-token", [import_service.ImportUpload(name, io.BytesIO(b"body"))]
        )

    assert repo.list_batches(user_id) == []
    assert not storage.user_paths(user_id).imports.exists()


def test_stream_read_failure_is_safe_and_leaves_no_batch_or_files(tmp_path):
    service, repo, storage, _, user_id, _ = make_import_service(tmp_path)
    stream = FailingStream(b"ignored")

    with pytest.raises(ValueError, match="could not stage") as captured:
        service.submit_uploads(
            "valid-token", [import_service.ImportUpload("notes.md", stream)]
        )

    assert "private" not in str(captured.value)
    assert "secret" not in str(captured.value)
    assert stream.closed is False
    assert repo.list_batches(user_id) == []
    imports = storage.user_paths(user_id).imports
    assert not imports.exists() or list(imports.rglob("*")) == []


def test_staging_flushes_fsyncs_and_replaces_partial_file(tmp_path, monkeypatch):
    service, _, storage, _, user_id, _ = make_import_service(tmp_path)
    fsynced = []
    replacements = []
    real_fsync = os.fsync
    real_replace = os.replace

    monkeypatch.setattr(import_service.os, "fsync", lambda fd: fsynced.append(fd) or real_fsync(fd))
    monkeypatch.setattr(
        import_service.os,
        "replace",
        lambda source, target: replacements.append((Path(source), Path(target)))
        or real_replace(source, target),
    )

    result = service.submit_uploads(
        "valid-token", [import_service.ImportUpload("notes.md", io.BytesIO(b"body"))]
    )

    staged = storage.user_paths(user_id).root / result.tasks[0].staged_relative_path
    assert fsynced
    assert replacements == [(staged.with_name(f"{staged.name}.partial"), staged)]
    assert staged.read_bytes() == b"body"
    assert not replacements[0][0].exists()


def test_database_failure_removes_exact_staged_files_and_leaves_no_batch(
    tmp_path, monkeypatch
):
    service, repo, storage, workers, user_id, _ = make_import_service(tmp_path)
    monkeypatch.setattr(
        repo,
        "create_batch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError(r"D:\private\app.db password=secret")
        ),
    )

    with pytest.raises(RuntimeError):
        service.submit_uploads(
            "valid-token", [import_service.ImportUpload("notes.md", io.BytesIO(b"body"))]
        )

    assert repo.list_batches(user_id) == []
    imports = storage.user_paths(user_id).imports
    assert not imports.exists() or list(imports.rglob("*")) == []
    assert workers.notify_count == 0


def test_submit_batch_closes_only_service_owned_path_stream(tmp_path, monkeypatch):
    service, _, _, _, _, _ = make_import_service(tmp_path)
    source = uploaded_file(tmp_path, "owned.md", b"owned")
    owned = TrackingStream(b"owned")
    real_open = Path.open

    def tracked_open(path, *args, **kwargs):
        if path == Path(source.name):
            return owned
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", tracked_open)

    service.submit_batch("valid-token", [source])

    assert owned.closed is True
    assert owned.close_count == 1


def test_submit_batch_open_failure_closes_prior_owned_stream_and_is_safe(
    tmp_path, monkeypatch
):
    service, repo, storage, _, user_id, _ = make_import_service(tmp_path)
    first = uploaded_file(tmp_path, "first.md", b"first")
    second = uploaded_file(tmp_path, "second.md", b"second")
    owned = TrackingStream(b"first")
    real_open = Path.open

    def fail_second_open(path, *args, **kwargs):
        if path == Path(first.name):
            return owned
        if path == Path(second.name):
            raise OSError(r"D:\private\second.md token=secret")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_second_open)

    with pytest.raises(ValueError, match="could not stage") as captured:
        service.submit_batch("valid-token", [first, second])

    assert "private" not in str(captured.value)
    assert "secret" not in str(captured.value)
    assert owned.closed is True
    assert owned.close_count == 1
    assert repo.list_batches(user_id) == []
    assert not storage.user_paths(user_id).imports.exists()


def test_cancel_queued_and_retry_wait_remove_only_their_staging(tmp_path):
    service, repo, storage, workers, user_id, _ = make_import_service(tmp_path)
    queued = service.submit_batch(
        "valid-token", [uploaded_file(tmp_path, "queued.md", b"queued")]
    )
    queued_task = queued.tasks[0]
    queued_staged = storage.user_paths(user_id).root / queued_task.staged_relative_path
    kept = storage.user_paths(user_id).imports / "keep.txt"
    kept.write_bytes(b"keep")

    cancelled = service.cancel_task(
        "valid-token", queued.batch_id, queued_task.task_id
    )

    assert cancelled.cancelled == 1
    assert not queued_staged.exists()
    assert kept.read_bytes() == b"keep"

    retry = service.submit_batch(
        "valid-token", [uploaded_file(tmp_path, "retry.md", b"retry")]
    )
    claimed = repo.claim_next(set())
    repo.mark_retry_wait(
        user_id,
        claimed.task_id,
        "2999-01-01T00:00:00Z",
        "rag_connection",
        "temporary",
    )
    retry_staged = storage.user_paths(user_id).root / claimed.staged_relative_path

    cancelled_retry = service.cancel_task(
        "valid-token", retry.batch_id, claimed.task_id
    )

    assert cancelled_retry.cancelled == 1
    assert not retry_staged.exists()
    assert workers.notify_count == 4


def test_cancel_running_requests_cancellation_and_notifies_pool(tmp_path):
    service, repo, storage, workers, user_id, _ = make_import_service(tmp_path)
    batch = service.submit_batch(
        "valid-token", [uploaded_file(tmp_path, "running.md", b"body")]
    )
    running = repo.claim_next(set())
    staged = storage.user_paths(user_id).root / running.staged_relative_path

    summary = service.cancel_task("valid-token", batch.batch_id, running.task_id)

    assert summary.running == 1
    assert summary.tasks[0].cancel_requested_at is not None
    assert staged.exists()
    assert workers.notify_count == 2


def test_cancel_committing_is_safe_error_and_terminal_is_unchanged(tmp_path):
    service, repo, _, workers, user_id, _ = make_import_service(tmp_path)
    batch = service.submit_batch(
        "valid-token", [uploaded_file(tmp_path, "commit.md", b"body")]
    )
    running = repo.claim_next(set())
    assert repo.try_begin_committing(user_id, running.task_id)

    with pytest.raises(import_service.ImportTaskNotCancellableError) as captured:
        service.cancel_task("valid-token", batch.batch_id, running.task_id)

    assert str(captured.value) == "import task is committing"
    repo.mark_succeeded(user_id, running.task_id)
    summary = service.cancel_task("valid-token", batch.batch_id, running.task_id)
    assert summary.succeeded == 1
    assert summary.cancelled == 0
    assert workers.notify_count == 2


def test_cancel_scope_mismatch_is_indistinguishable_from_missing(tmp_path):
    service, _, _, workers, _, _ = make_import_service(tmp_path)
    batch = service.submit_batch(
        "valid-token", [uploaded_file(tmp_path, "scoped.md", b"body")]
    )

    with pytest.raises(KeyError, match="not found"):
        service.cancel_task("other-token", batch.batch_id, batch.tasks[0].task_id)

    assert workers.notify_count == 1


def test_cancel_cleanup_failure_keeps_durable_cancelled_status(
    tmp_path, monkeypatch
):
    service, repo, storage, _, user_id, _ = make_import_service(tmp_path)
    batch = service.submit_batch(
        "valid-token", [uploaded_file(tmp_path, "cleanup.md", b"body")]
    )
    staged = storage.user_paths(user_id).root / batch.tasks[0].staged_relative_path
    real_unlink = Path.unlink

    def fail_exact(path, *args, **kwargs):
        if path == staged:
            raise OSError(r"D:\private\staging token=secret")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_exact)

    summary = service.cancel_task(
        "valid-token", batch.batch_id, batch.tasks[0].task_id
    )

    assert summary.cancelled == 1
    assert repo.get_task(user_id, batch.tasks[0].task_id).status == "cancelled"
    assert staged.exists()


def test_submit_validates_entire_batch_before_creating_stage_directory(tmp_path):
    service, repo, storage, workers, user_id, _ = make_import_service(tmp_path)
    files = [
        uploaded_file(tmp_path, "a.md", b"a"),
        uploaded_file(tmp_path, "bad.exe", b"b"),
    ]

    with pytest.raises(ValueError, match="Unsupported document type"):
        service.submit_batch("valid-token", files)

    assert repo.list_batches(user_id) == []
    assert not storage.user_paths(user_id).imports.exists()
    assert workers.notify_count == 0


def test_queries_and_retries_are_scoped_to_authenticated_user(tmp_path):
    service, repo, _, workers, user_id, _ = make_import_service(tmp_path)
    result = service.submit_batch(
        "valid-token", [uploaded_file(tmp_path, "a.md", b"alpha")]
    )
    claimed = repo.claim_next(set())
    repo.mark_failed(user_id, claimed.task_id, "document_invalid", "bad document")

    with pytest.raises(KeyError):
        service.get_batch("other-token", result.batch_id)
    with pytest.raises(KeyError):
        service.retry_task("other-token", claimed.task_id)

    retried = service.retry_task("valid-token", claimed.task_id)

    assert retried.queued == 1
    assert retried.tasks[0].manual_retry_count == 1
    assert workers.notify_count == 2


def test_succeeded_task_cannot_be_retried(tmp_path):
    service, repo, _, _, user_id, _ = make_import_service(tmp_path)
    result = service.submit_batch(
        "valid-token", [uploaded_file(tmp_path, "a.md", b"alpha")]
    )
    claimed = repo.claim_next(set())
    repo.mark_succeeded(user_id, claimed.task_id)

    with pytest.raises(InvalidImportTransition):
        service.retry_task("valid-token", result.tasks[0].task_id)


def test_retry_task_rejects_task_outside_displayed_batch(tmp_path):
    service, repository, _, worker, user_id, _ = make_import_service(tmp_path)
    first = service.submit_batch(
        "valid-token", [uploaded_file(tmp_path, "first.md", b"first")]
    )
    second = service.submit_batch(
        "valid-token", [uploaded_file(tmp_path, "second.md", b"second")]
    )
    task = repository.claim_next(set())
    repository.mark_failed(user_id, task.task_id, "document_invalid", "bad")
    notifications_before = worker.notify_count

    with pytest.raises(KeyError, match="displayed batch"):
        service.retry_task(
            "valid-token", first.tasks[0].task_id, expected_batch_id=second.batch_id
        )

    assert repository.get_task(user_id, task.task_id).status == "failed"
    assert worker.notify_count == notifications_before


def _attach_runtime_lock(service, user_id):
    runtime = SimpleNamespace(lock=threading.RLock())
    service.session_registry.sessions["valid-token"] = SimpleNamespace(
        user_id=user_id, runtime=runtime
    )
    return runtime


def test_submit_batch_holds_runtime_lock_through_durable_creation(tmp_path):
    service, repository, _, _, user_id, _ = make_import_service(tmp_path)
    runtime = _attach_runtime_lock(service, user_id)
    creation_started = threading.Event()
    allow_creation = threading.Event()
    clear_entered = threading.Event()
    errors = []
    original_create = repository.create_batch

    def blocked_create(*args, **kwargs):
        creation_started.set()
        assert allow_creation.wait(timeout=3)
        return original_create(*args, **kwargs)

    repository.create_batch = blocked_create
    submitter = threading.Thread(
        target=lambda: _capture_error(
            errors,
            service.submit_batch,
            "valid-token",
            [uploaded_file(tmp_path, "atomic.md", b"body")],
        )
    )
    submitter.start()
    assert creation_started.wait(timeout=3)
    clearer = threading.Thread(target=lambda: _enter_lock(runtime, clear_entered))
    clearer.start()
    assert not clear_entered.wait(timeout=0.1)
    allow_creation.set()
    submitter.join(timeout=3)
    clearer.join(timeout=3)
    assert not errors
    assert clear_entered.is_set()


@pytest.mark.parametrize("method_name", ["retry_task", "retry_failed_in_batch"])
def test_retry_requeue_holds_runtime_lock(tmp_path, method_name):
    service, repository, _, _, user_id, _ = make_import_service(tmp_path)
    result = service.submit_batch(
        "valid-token", [uploaded_file(tmp_path, "retry.md", b"body")]
    )
    task = repository.claim_next(set())
    repository.mark_failed(user_id, task.task_id, "document_invalid", "bad")
    runtime = _attach_runtime_lock(service, user_id)
    requeue_started = threading.Event()
    allow_requeue = threading.Event()
    clear_entered = threading.Event()
    errors = []
    original_requeue = getattr(repository, method_name)

    def blocked_requeue(*args, **kwargs):
        requeue_started.set()
        assert allow_requeue.wait(timeout=3)
        return original_requeue(*args, **kwargs)

    setattr(repository, method_name, blocked_requeue)
    identifier = task.task_id if method_name == "retry_task" else result.batch_id
    retry_thread = threading.Thread(
        target=lambda: _capture_error(
            errors,
            getattr(service, method_name),
            "valid-token",
            identifier,
        )
    )
    retry_thread.start()
    assert requeue_started.wait(timeout=3)
    clearer = threading.Thread(target=lambda: _enter_lock(runtime, clear_entered))
    clearer.start()
    assert not clear_entered.wait(timeout=0.1)
    allow_requeue.set()
    retry_thread.join(timeout=3)
    clearer.join(timeout=3)
    assert not errors
    assert clear_entered.is_set()


def _capture_error(errors, call, *args):
    try:
        call(*args)
    except Exception as error:  # pragma: no cover - surfaced by assertion
        errors.append(error)


def _enter_lock(runtime, entered):
    with runtime.lock:
        entered.set()
