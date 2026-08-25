from __future__ import annotations

import inspect
import threading
import uuid
from types import SimpleNamespace

import pytest

from app.auth import AuthService
from app.database import connect, initialize_database
from app.import_models import ImportTaskCreate
from app.import_repository import ImportTaskRepository, InvalidImportTransition
from app.import_service import ImportTaskService
from app.storage import UserStorage


TASK_METHODS = ("pause_task", "resume_task", "cancel_task")
BATCH_METHODS = ("pause_batch", "resume_batch", "cancel_batch")
CONTROL_METHODS = TASK_METHODS + BATCH_METHODS


class FakeSessionRegistry:
    def __init__(self, sessions):
        self.sessions = sessions
        self.calls = []

    def get_session(self, token):
        self.calls.append(token)
        if not token:
            raise ValueError("missing session")
        if token == "expired-token":
            raise ValueError("expired session")
        try:
            return self.sessions[token]
        except KeyError:
            raise ValueError("invalid session") from None


class FakeWorkerPool:
    def __init__(self):
        self.notify_count = 0

    def notify(self):
        self.notify_count += 1


class GateLock:
    """A deterministic lock probe that reports an attempted acquisition."""

    def __init__(self, on_enter=None):
        self._lock = threading.Lock()
        self.attempted = threading.Event()
        self.on_enter = on_enter
        self.owned = False

    def hold(self):
        self._lock.acquire()

    def release(self):
        self._lock.release()

    def __enter__(self):
        self.attempted.set()
        self._lock.acquire()
        self.owned = True
        if self.on_enter is not None:
            self.on_enter()
        return self

    def __exit__(self, *_args):
        self.owned = False
        self._lock.release()


@pytest.fixture
def harness(tmp_path):
    db_path = tmp_path / "app.db"
    initialize_database(db_path)
    auth = AuthService(db_path)
    user_id = auth.register("control-user", "correct horse battery").id
    other_user_id = auth.register("control-other", "correct horse battery").id
    repository = ImportTaskRepository(db_path)
    storage = UserStorage(tmp_path / "data")
    user_session = SimpleNamespace(
        user_id=user_id, runtime=SimpleNamespace(lock=threading.RLock())
    )
    other_session = SimpleNamespace(
        user_id=other_user_id, runtime=SimpleNamespace(lock=threading.RLock())
    )
    sessions = FakeSessionRegistry(
        {"valid-token": user_session, "other-token": other_session}
    )
    workers = FakeWorkerPool()
    service = ImportTaskService(sessions, repository, storage, workers)
    return SimpleNamespace(
        service=service,
        repository=repository,
        storage=storage,
        workers=workers,
        sessions=sessions,
        session=user_session,
        user_id=user_id,
        other_user_id=other_user_id,
    )


def _create_task(harness, status="queued"):
    task_id = str(uuid.uuid4())
    batch_id = str(uuid.uuid4())
    document_id = str(uuid.uuid4())
    staged = harness.storage.staged_import_path(
        harness.user_id, batch_id, task_id, ".md"
    )
    staged.write_bytes(b"staged-control-input")
    create = ImportTaskCreate(
        task_id=task_id,
        batch_id=batch_id,
        user_id=harness.user_id,
        document_id=document_id,
        original_name="control.md",
        file_suffix=".md",
        size_bytes=20,
        staged_relative_path=str(
            staged.relative_to(harness.storage.user_paths(harness.user_id).root)
        ),
    )
    harness.repository.create_batch(harness.user_id, [create])
    _force_state(harness.repository, harness.user_id, task_id, status)
    return harness.repository.get_task(harness.user_id, task_id), staged


def _force_state(repository, user_id, task_id, status):
    stage = status if status in {"paused", "cancelled", "succeeded", "failed"} else "queued"
    with connect(repository.db_path) as conn:
        conn.execute(
            """
            update import_tasks
            set status = ?, stage = ?,
                started_at = case when ? in ('running', 'pause_requested')
                    then '2026-08-25T00:00:00Z' else null end,
                finished_at = case when ? in ('cancelled', 'succeeded', 'failed')
                    then '2026-08-25T00:01:00Z' else null end,
                control_requested_at = case
                    when ? in ('pause_requested', 'cancel_requested')
                    then '2026-08-25T00:00:30Z' else null end
            where id = ? and user_id = ?
            """,
            (status, stage, status, status, status, task_id, user_id),
        )


def _identifier(method_name, task):
    return task.task_id if method_name in TASK_METHODS else task.batch_id


def _snapshot(harness, task, staged):
    return (
        harness.repository.get_batch(harness.user_id, task.batch_id),
        harness.repository.list_task_events(harness.user_id, task.task_id),
        staged.read_bytes(),
    )


def _call_in_thread(errors, result, call, *args):
    try:
        result.append(call(*args))
    except Exception as exc:  # pragma: no cover - surfaced by assertions
        errors.append(exc)


def test_control_signatures_expose_only_authenticated_identifiers():
    expected = {
        **{name: ["self", "session_token", "task_id"] for name in TASK_METHODS},
        **{name: ["self", "session_token", "batch_id"] for name in BATCH_METHODS},
    }

    for method_name, parameter_names in expected.items():
        method = getattr(ImportTaskService, method_name)
        assert list(inspect.signature(method).parameters) == parameter_names


@pytest.mark.parametrize("method_name", CONTROL_METHODS)
@pytest.mark.parametrize("token", [None, "expired-token"])
def test_control_auth_rejection_has_zero_repository_file_or_notify_mutation(
    harness, method_name, token
):
    task, staged = _create_task(harness, "queued")
    before = _snapshot(harness, task, staged)

    with pytest.raises(ValueError, match="session"):
        getattr(harness.service, method_name)(token, _identifier(method_name, task))

    assert _snapshot(harness, task, staged) == before
    assert harness.workers.notify_count == 0


@pytest.mark.parametrize("method_name", CONTROL_METHODS)
def test_control_cross_user_identifier_is_not_disclosed_or_mutated(
    harness, method_name
):
    task, staged = _create_task(harness, "queued")
    before = _snapshot(harness, task, staged)

    with pytest.raises(KeyError, match="not found"):
        getattr(harness.service, method_name)(
            "other-token", _identifier(method_name, task)
        )

    assert _snapshot(harness, task, staged) == before
    assert harness.workers.notify_count == 0


@pytest.mark.parametrize("method_name", CONTROL_METHODS)
def test_nonexistent_control_identifier_is_not_disclosed_or_mutated(
    harness, method_name
):
    task, staged = _create_task(harness, "queued")
    before = _snapshot(harness, task, staged)

    with pytest.raises(KeyError, match="not found"):
        getattr(harness.service, method_name)("valid-token", str(uuid.uuid4()))

    assert _snapshot(harness, task, staged) == before
    assert harness.workers.notify_count == 0


@pytest.mark.parametrize(
    ("method_name", "source_status", "raises"),
    [
        ("pause_task", "succeeded", True),
        ("resume_task", "queued", True),
        ("cancel_task", "succeeded", True),
        ("pause_batch", "succeeded", False),
        ("resume_batch", "queued", False),
        ("cancel_batch", "succeeded", False),
    ],
)
def test_stale_control_is_a_zero_mutation_noop(
    harness, method_name, source_status, raises
):
    task, staged = _create_task(harness, source_status)
    before = _snapshot(harness, task, staged)

    if raises:
        with pytest.raises(InvalidImportTransition):
            getattr(harness.service, method_name)(
                "valid-token", _identifier(method_name, task)
            )
    else:
        result = getattr(harness.service, method_name)(
            "valid-token", _identifier(method_name, task)
        )
        assert result == before[0]

    assert _snapshot(harness, task, staged) == before
    assert harness.workers.notify_count == 0


@pytest.mark.parametrize(
    ("method_name", "source_status", "changed_status", "count_field", "raises"),
    [
        ("pause_task", "queued", "paused", "paused", True),
        ("resume_task", "paused", "queued", "queued", True),
        ("cancel_task", "queued", "cancel_requested", "cancel_requested", True),
        ("pause_batch", "queued", "paused", "paused", False),
        ("resume_batch", "paused", "queued", "queued", False),
        ("cancel_batch", "queued", "cancel_requested", "cancel_requested", False),
    ],
)
def test_duplicate_control_notifies_only_for_the_successful_change(
    harness, method_name, source_status, changed_status, count_field, raises
):
    task, staged = _create_task(harness, source_status)
    identifier = _identifier(method_name, task)

    changed = getattr(harness.service, method_name)("valid-token", identifier)
    after_first = _snapshot(harness, task, staged)

    assert changed.user_id == harness.user_id
    assert getattr(changed, count_field) == 1
    assert changed.tasks[0].status == changed_status
    assert harness.workers.notify_count == 1
    if raises:
        with pytest.raises(InvalidImportTransition):
            getattr(harness.service, method_name)("valid-token", identifier)
    else:
        assert getattr(harness.service, method_name)("valid-token", identifier) == changed

    assert _snapshot(harness, task, staged) == after_first
    assert harness.workers.notify_count == 1


@pytest.mark.parametrize(
    ("method_name", "source_status", "repository_method", "forbidden_method"),
    [
        ("pause_batch", "queued", "request_pause_batch", "request_pause"),
        ("resume_batch", "paused", "resume_batch", "resume_task"),
        ("cancel_batch", "queued", "request_cancel_batch", "request_cancel"),
    ],
)
def test_batch_control_delegates_once_without_a_service_task_loop(
    harness,
    monkeypatch,
    method_name,
    source_status,
    repository_method,
    forbidden_method,
):
    task, _ = _create_task(harness, source_status)
    original = getattr(harness.repository, repository_method)
    calls = []

    def tracked(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("service must use the repository batch transaction")

    monkeypatch.setattr(harness.repository, repository_method, tracked)
    monkeypatch.setattr(harness.repository, forbidden_method, forbidden)

    result = getattr(harness.service, method_name)("valid-token", task.batch_id)

    assert result.batch_id == task.batch_id
    assert calls == [((harness.user_id, task.batch_id), {})]
    assert harness.workers.notify_count == 1


@pytest.mark.parametrize(
    ("method_name", "source_status"),
    [
        ("pause_task", "queued"),
        ("resume_task", "paused"),
        ("cancel_task", "queued"),
        ("pause_batch", "queued"),
        ("resume_batch", "paused"),
        ("cancel_batch", "queued"),
    ],
)
def test_every_control_derives_user_before_and_transitions_under_runtime_lock(
    harness, method_name, source_status
):
    task, staged = _create_task(harness, source_status)
    before = _snapshot(harness, task, staged)
    gate = GateLock(on_enter=lambda: setattr(harness.session, "user_id", harness.other_user_id))
    harness.session.runtime.lock = gate
    def notify_after_unlock():
        assert gate.owned is False
        harness.workers.notify_count += 1

    harness.workers.notify = notify_after_unlock
    gate.hold()
    errors = []
    results = []
    thread = threading.Thread(
        target=_call_in_thread,
        args=(
            errors,
            results,
            getattr(harness.service, method_name),
            "valid-token",
            _identifier(method_name, task),
        ),
    )

    thread.start()
    assert gate.attempted.wait(timeout=3)
    assert _snapshot(harness, task, staged) == before
    gate.release()
    thread.join(timeout=3)

    assert not thread.is_alive()
    assert errors == []
    assert results[0].user_id == harness.user_id
    assert harness.workers.notify_count == 1


@pytest.mark.parametrize(
    "status",
    ["queued", "running", "retry_wait", "pause_requested", "paused", "cancel_requested"],
)
def test_requested_and_paused_tasks_remain_destructive_guard_active(harness, status):
    task, _ = _create_task(harness, status)

    assert harness.service.has_active_tasks(harness.user_id)
    assert harness.service.has_active_task_for_document(
        harness.user_id, task.document_id
    )
