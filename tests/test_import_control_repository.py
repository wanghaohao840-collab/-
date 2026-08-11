import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.auth import AuthService
from app.database import connect, initialize_database
from app.import_models import ImportTaskCreate
from app.import_repository import ImportTaskRepository, InvalidImportTransition
from app.storage import UserStorage


NOW = "2026-08-11T00:00:00Z"


@pytest.fixture
def repository(tmp_path):
    db_path = tmp_path / "app.db"
    initialize_database(db_path)
    user_id = AuthService(db_path).register(
        "control-user", "correct horse battery"
    ).id
    return ImportTaskRepository(db_path), user_id


def _task(user_id, *, batch_id=None, task_id=None):
    batch_id = batch_id or str(uuid.uuid4())
    task_id = task_id or str(uuid.uuid4())
    return ImportTaskCreate(
        task_id=task_id,
        batch_id=batch_id,
        user_id=user_id,
        document_id=str(uuid.uuid4()),
        original_name=f"{task_id}.md",
        file_suffix=".md",
        size_bytes=3,
        staged_relative_path=f"imports/{batch_id}/{task_id}.md",
    )


def _force_state(repository, user_id, task_id, status, *, error_code=None):
    stage = status if status in {"paused", "cancelled", "failed"} else "queued"
    with connect(repository.db_path) as conn:
        conn.execute(
            """
            update import_tasks
            set status = ?, stage = ?, error_code = ?,
                next_attempt_at = case when ? = 'retry_wait'
                    then '2026-08-12T00:00:00Z' else null end,
                started_at = case when ? in ('running', 'pause_requested')
                    then '2026-08-10T00:00:00Z' else null end,
                control_requested_at = case
                    when ? in ('pause_requested', 'cancel_requested')
                    then '2026-08-10T00:00:00Z' else null end
            where id = ? and user_id = ?
            """,
            (status, stage, error_code, status, status, status, task_id, user_id),
        )


def _task_in_state(repository, user_id, status, *, error_code=None):
    task = _task(user_id)
    repository.create_batch(user_id, [task], now="2026-08-10T00:00:00Z")
    _force_state(repository, user_id, task.task_id, status, error_code=error_code)
    return repository.get_task(user_id, task.task_id)


@pytest.mark.parametrize(
    ("source", "target", "event_type"),
    [
        ("queued", "paused", "paused"),
        ("retry_wait", "paused", "paused"),
        ("running", "pause_requested", "pause_requested"),
    ],
)
def test_request_pause_moves_each_eligible_state(
    repository, source, target, event_type
):
    repo, user_id = repository
    task = _task_in_state(repo, user_id, source)

    changed = repo.request_pause(user_id, task.task_id, now=NOW)

    assert changed.status == target
    assert changed.control_requested_at == NOW
    assert changed.control_claimed_at is None
    assert changed.next_attempt_at is None
    event = repo.list_task_events(user_id, task.task_id)[-1]
    assert (event.event_type, event.status, event.created_at) == (
        event_type,
        target,
        NOW,
    )


@pytest.mark.parametrize(
    ("source", "error_code"),
    [
        ("queued", None),
        ("retry_wait", None),
        ("paused", None),
        ("running", None),
        ("pause_requested", None),
        ("failed", "pause_cleanup_failed"),
        ("failed", "cancel_cleanup_failed"),
    ],
)
def test_request_cancel_moves_each_eligible_state(repository, source, error_code):
    repo, user_id = repository
    task = _task_in_state(repo, user_id, source, error_code=error_code)

    changed = repo.request_cancel(user_id, task.task_id, now=NOW)

    assert changed.status == "cancel_requested"
    assert changed.control_requested_at == NOW
    assert changed.control_claimed_at is None
    event = repo.list_task_events(user_id, task.task_id)[-1]
    assert (event.event_type, event.status) == (
        "cancel_requested",
        "cancel_requested",
    )


@pytest.mark.parametrize(
    ("command", "source"),
    [
        ("request_pause", "paused"),
        ("request_pause", "succeeded"),
        ("request_cancel", "succeeded"),
        ("request_cancel", "failed"),
        ("resume_task", "queued"),
        ("resume_task", "cancelled"),
    ],
)
def test_illegal_and_foreign_user_commands_do_not_mutate(repository, command, source):
    repo, user_id = repository
    other_user_id = AuthService(repo.db_path).register(
        f"other-{command}-{source}", "correct horse battery"
    ).id
    task = _task_in_state(repo, user_id, source, error_code="ordinary_failure")
    before = repo.get_task(user_id, task.task_id)

    with pytest.raises(KeyError):
        getattr(repo, command)(other_user_id, task.task_id, now=NOW)
    with pytest.raises(InvalidImportTransition):
        getattr(repo, command)(user_id, task.task_id, now=NOW)

    assert repo.get_task(user_id, task.task_id) == before
    assert repo.list_task_events(user_id, task.task_id) == []
    assert repo.list_task_events(other_user_id, task.task_id) == []


def test_resume_retains_progress_and_attempts_but_clears_control_retry_fields(repository):
    repo, user_id = repository
    task = _task_in_state(repo, user_id, "paused")
    with connect(repo.db_path) as conn:
        conn.execute(
            """
            update import_tasks
            set progress = 61, total_attempt_count = 4, auto_retry_count = 2,
                manual_retry_count = 3, next_attempt_at = '2026-08-12T00:00:00Z',
                control_requested_at = '2026-08-10T00:00:00Z',
                control_claimed_at = '2026-08-10T00:01:00Z'
            where id = ? and user_id = ?
            """,
            (task.task_id, user_id),
        )

    resumed = repo.resume_task(user_id, task.task_id, now=NOW)

    assert resumed.status == "queued"
    assert resumed.stage == "queued"
    assert resumed.progress == 61
    assert (
        resumed.total_attempt_count,
        resumed.auto_retry_count,
        resumed.manual_retry_count,
    ) == (4, 2, 3)
    assert resumed.next_attempt_at is None
    assert resumed.control_requested_at is None
    assert resumed.control_claimed_at is None
    assert repo.list_task_events(user_id, task.task_id)[-1].event_type == "resumed"


def test_duplicate_concurrent_command_appends_exactly_one_event(repository):
    repo, user_id = repository
    task = _task_in_state(repo, user_id, "queued")
    start = threading.Barrier(2)

    def pause():
        start.wait()
        try:
            return repo.request_pause(user_id, task.task_id, now=NOW)
        except InvalidImportTransition:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: pause(), range(2)))

    assert len([result for result in results if result is not None]) == 1
    assert [event.event_type for event in repo.list_task_events(user_id, task.task_id)] == [
        "paused"
    ]


def test_batch_controls_are_atomic_user_scoped_and_noop_when_nothing_is_eligible(
    repository,
):
    repo, user_id = repository
    other_user_id = AuthService(repo.db_path).register(
        "batch-other", "correct horse battery"
    ).id
    batch_id = str(uuid.uuid4())
    queued = _task(user_id, batch_id=batch_id)
    running = _task(user_id, batch_id=batch_id)
    repo.create_batch(user_id, [queued, running], now="2026-08-10T00:00:00Z")
    _force_state(repo, user_id, running.task_id, "running")

    paused = repo.request_pause_batch(user_id, batch_id, now=NOW)

    assert (paused.paused, paused.pause_requested) == (1, 1)
    assert sorted(
        event.event_type
        for task in paused.tasks
        for event in repo.list_task_events(user_id, task.task_id)
    ) == ["pause_requested", "paused"]
    event_count = sum(
        len(repo.list_task_events(user_id, task.task_id)) for task in paused.tasks
    )
    assert repo.request_pause_batch(user_id, batch_id, now=NOW) == paused
    assert sum(
        len(repo.list_task_events(user_id, task.task_id)) for task in paused.tasks
    ) == event_count
    with pytest.raises(KeyError):
        repo.request_pause_batch(other_user_id, batch_id, now=NOW)

    cancelled = repo.request_cancel_batch(user_id, batch_id, now=NOW)
    assert cancelled.cancel_requested == 2
    with connect(repo.db_path) as conn:
        conn.execute(
            "update import_tasks set status = 'paused', stage = 'paused' "
            "where batch_id = ? and user_id = ?",
            (batch_id, user_id),
        )
    resumed = repo.resume_batch(user_id, batch_id, now=NOW)
    assert resumed.queued == 2


def test_control_claims_take_priority_and_preserve_one_slot_per_user(repository):
    repo, user_id = repository
    other_user_id = AuthService(repo.db_path).register(
        "claim-other", "correct horse battery"
    ).id
    batch_id = str(uuid.uuid4())
    first = _task(user_id, batch_id=batch_id, task_id="00000000-0000-0000-0000-000000000001")
    sibling = _task(user_id, batch_id=batch_id, task_id="00000000-0000-0000-0000-000000000002")
    queued = _task(user_id, batch_id=batch_id, task_id="00000000-0000-0000-0000-000000000003")
    other = _task(other_user_id, task_id="00000000-0000-0000-0000-000000000004")
    repo.create_batch(user_id, [first, sibling, queued], now="2026-08-10T00:00:00Z")
    repo.create_batch(other_user_id, [other], now="2026-08-10T00:00:00Z")
    repo.request_cancel(user_id, first.task_id, now="2026-08-10T00:00:01Z")
    repo.request_cancel(user_id, sibling.task_id, now="2026-08-10T00:00:02Z")

    claimed_control = repo.claim_next(set(), now=NOW)
    other_user_work = repo.claim_next(set(), now=NOW)

    assert claimed_control.task_id == first.task_id
    assert claimed_control.status == "cancel_requested"
    assert claimed_control.control_claimed_at == NOW
    assert repo.get_task(user_id, sibling.task_id).control_claimed_at is None
    assert repo.get_task(user_id, queued.task_id).status == "queued"
    assert other_user_work.user_id == other_user_id
    assert other_user_work.status == "running"


def test_concurrent_control_claimers_claim_distinct_users(repository):
    repo, first_user_id = repository
    second_user_id = AuthService(repo.db_path).register(
        "second-control-user", "correct horse battery"
    ).id
    tasks = [_task(first_user_id), _task(second_user_id)]
    repo.create_batch(first_user_id, [tasks[0]], now="2026-08-10T00:00:00Z")
    repo.create_batch(second_user_id, [tasks[1]], now="2026-08-10T00:00:00Z")
    repo.request_cancel(first_user_id, tasks[0].task_id, now=NOW)
    repo.request_cancel(second_user_id, tasks[1].task_id, now=NOW)
    start = threading.Barrier(2)

    def claim():
        start.wait()
        return repo.claim_next(set(), now=NOW)

    with ThreadPoolExecutor(max_workers=2) as executor:
        claimed = list(executor.map(lambda _: claim(), range(2)))

    assert {task.user_id for task in claimed} == {first_user_id, second_user_id}
    assert all(task.control_claimed_at == NOW for task in claimed)


def test_release_and_recovery_clear_stale_control_claims(repository, tmp_path):
    repo, user_id = repository
    task = _task_in_state(repo, user_id, "queued")
    repo.request_cancel(user_id, task.task_id, now=NOW)
    claimed = repo.claim_next(set(), now=NOW)

    released = repo.release_control_claim(user_id, task.task_id, now=NOW)

    assert released.status == "cancel_requested"
    assert released.control_claimed_at is None
    repo.claim_next(set(), now=NOW)
    assert repo.recover_running(UserStorage(tmp_path / "data"), now=NOW) == 0
    recovered = repo.get_task(user_id, task.task_id)
    assert recovered.status == "cancel_requested"
    assert recovered.control_claimed_at is None


@pytest.mark.parametrize(
    ("requested", "terminal", "event_type", "stage"),
    [
        ("pause_requested", "mark_paused", "paused", "paused"),
        ("cancel_requested", "mark_cancelled", "cancelled", "cancelled"),
    ],
)
@pytest.mark.parametrize("claimed", [False, True])
def test_terminal_control_state_predicate_is_authoritative(
    repository, requested, terminal, event_type, stage, claimed
):
    repo, user_id = repository
    task = _task_in_state(repo, user_id, requested)
    if claimed:
        with connect(repo.db_path) as conn:
            conn.execute(
                "update import_tasks set control_claimed_at = ? where id = ?",
                ("2026-08-10T00:01:00Z", task.task_id),
            )

    changed = getattr(repo, terminal)(user_id, task.task_id, now=NOW)

    assert changed.status == event_type
    assert changed.stage == stage
    assert changed.finished_at == NOW
    assert changed.control_requested_at is None
    assert changed.control_claimed_at is None
    assert repo.list_task_events(user_id, task.task_id)[-1].event_type == event_type


@pytest.mark.parametrize(
    ("requested", "error_code"),
    [
        ("pause_requested", "pause_cleanup_failed"),
        ("cancel_requested", "cancel_cleanup_failed"),
    ],
)
def test_mark_control_failed_whitelists_code_and_sanitizes_event_message(
    repository, requested, error_code
):
    repo, user_id = repository
    task = _task_in_state(repo, user_id, requested)
    unsafe = "api_key=super-secret C:\\Users\\private\\document.md " + "x" * 800

    failed = repo.mark_control_failed(
        user_id,
        task.task_id,
        error_code,
        unsafe,
        now=NOW,
    )

    event = repo.list_task_events(user_id, task.task_id)[-1]
    assert failed.status == "failed"
    assert failed.error_code == error_code
    assert failed.error_summary == event.message
    assert len(event.message) <= 500
    assert "super-secret" not in event.message
    with pytest.raises(ValueError, match="error code"):
        repo.mark_control_failed(
            user_id, task.task_id, "internal_exception", "raw", now=NOW
        )


def test_control_failure_persistence_redacts_private_diagnostics(repository):
    repo, user_id = repository
    task = _task_in_state(repo, user_id, "cancel_requested")
    private_uuid = "123e4567-e89b-12d3-a456-426614174000"
    unsafe = (
        "ValueError: cleanup failed safely\n"
        'password="correct horse battery" Bearer bearer-secret '
        "https://private-user:private-pass@example.com/internal "
        '"C:\\Users\\private folder\\document.pdf" '
        "\\\\private-server\\share\\document.pdf "
        "'/home/private folder/document.pdf' "
        f"imports/{private_uuid}/{private_uuid}.md "
        f'document_id={private_uuid} user_id=private-user-42 '
        '"task_id": "private-task-42"\n'
        "Traceback (most recent call last):\n"
        '  File "C:\\Users\\private\\worker.py", line 7, in cleanup\n'
        "    cleanup(private_uuid)\n"
        f"RuntimeError: cleanup failed for task_id={private_uuid}\n"
        + "x" * 800
    )

    repo.mark_control_failed(
        user_id,
        task.task_id,
        "cancel_cleanup_failed",
        unsafe,
        now=NOW,
    )

    with connect(repo.db_path) as conn:
        persisted = conn.execute(
            "select error_summary from import_tasks where id = ? and user_id = ?",
            (task.task_id, user_id),
        ).fetchone()["error_summary"]
        event_message = conn.execute(
            """
            select message from import_task_events
            where task_id = ? and user_id = ? order by created_at desc, id desc
            limit 1
            """,
            (task.task_id, user_id),
        ).fetchone()["message"]

    assert persisted == event_message
    assert persisted.startswith("ValueError: cleanup failed safely")
    assert len(persisted) <= 500
    for private_value in (
        "correct horse battery",
        "bearer-secret",
        "private-user",
        "private-pass",
        "private folder",
        "private-server",
        "/home/private",
        "imports/",
        private_uuid,
        "private-user-42",
        "private-task-42",
        "Traceback",
        'File "',
        "cleanup(private_uuid)",
    ):
        assert private_value not in persisted


def test_list_events_is_user_scoped_ordered_and_limit_is_capped(repository):
    repo, user_id = repository
    other_user_id = AuthService(repo.db_path).register(
        "event-other", "correct horse battery"
    ).id
    task = _task_in_state(repo, user_id, "queued")
    with connect(repo.db_path) as conn:
        row = conn.execute(
            "select batch_id, status, stage from import_tasks where id = ?",
            (task.task_id,),
        ).fetchone()
        conn.executemany(
            """
            insert into import_task_events
                (batch_id, task_id, user_id, event_type, status, stage, message, created_at)
            values (?, ?, ?, ?, ?, ?, null, ?)
            """,
            [
                (
                    row["batch_id"],
                    task.task_id,
                    user_id,
                    f"event-{index:03d}",
                    row["status"],
                    row["stage"],
                    "2026-08-10T00:00:00Z",
                )
                for index in range(205)
            ],
        )

    events = repo.list_task_events(user_id, task.task_id, limit=999)

    assert len(events) == 200
    assert [event.event_type for event in events[:2]] == ["event-000", "event-001"]
    assert repo.list_task_events(other_user_id, task.task_id) == []
    assert repo.list_task_events(user_id, task.task_id, limit=0) == []
