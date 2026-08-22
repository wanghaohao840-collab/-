import sqlite3
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

import app.import_repository as import_repository
from app.auth import AuthService
from app.database import connect, initialize_database
from app.import_models import ImportTaskCreate
from app.import_repository import ImportTaskRepository, InvalidImportTransition
from app.storage import UserStorage


PRE_CANCELLATION_IMPORT_SCHEMA = """
create table if not exists users (
    id text primary key,
    username text not null,
    username_key text not null unique,
    password_hash text not null,
    status text not null default 'active',
    created_at text not null,
    updated_at text not null
);

create table if not exists import_batches (
    id text primary key,
    user_id text not null references users(id) on delete cascade,
    created_at text not null,
    updated_at text not null,
    unique(id, user_id)
);

create table if not exists import_tasks (
    id text primary key,
    batch_id text not null,
    user_id text not null,
    document_id text not null,
    original_name text not null,
    file_suffix text not null,
    size_bytes integer not null,
    staged_relative_path text not null,
    status text not null check(status in ('queued','running','retry_wait','succeeded','failed')),
    stage text not null,
    progress integer not null check(progress between 0 and 100),
    total_attempt_count integer not null default 0,
    auto_retry_count integer not null default 0,
    manual_retry_count integer not null default 0,
    max_auto_retries integer not null default 3,
    next_attempt_at text,
    error_code text,
    error_summary text,
    created_at text not null,
    started_at text,
    finished_at text,
    updated_at text not null,
    foreign key(batch_id, user_id) references import_batches(id, user_id)
        on delete cascade,
    unique(user_id, document_id)
);

create unique index if not exists uq_import_tasks_running_user
on import_tasks(user_id) where status = 'running';
create index if not exists ix_import_tasks_scheduler
on import_tasks(status, next_attempt_at, created_at);
create index if not exists ix_import_tasks_user_created
on import_tasks(user_id, created_at);
"""


def test_new_import_schema_has_cancellation_contract(tmp_path):
    db_path = tmp_path / "app.db"
    initialize_database(db_path)

    with connect(db_path) as conn:
        sql = conn.execute(
            "select sql from sqlite_master where type='table' and name='import_tasks'"
        ).fetchone()["sql"]
        columns = {
            row["name"] for row in conn.execute("pragma table_info('import_tasks')")
        }

    assert "cancelled" in sql
    assert "cancel_requested_at" in columns


def test_upgrade_pre_cancellation_import_tasks_preserves_data_and_indexes(tmp_path):
    db_path = tmp_path / "app.db"
    user_id = "00000000-0000-0000-0000-000000000101"
    batch_id = "00000000-0000-0000-0000-000000000102"
    task_id = "00000000-0000-0000-0000-000000000103"
    document_id = "00000000-0000-0000-0000-000000000104"
    expected_indexes = {
        "uq_import_tasks_running_user",
        "ix_import_tasks_scheduler",
        "ix_import_tasks_user_created",
    }

    with connect(db_path) as conn:
        conn.executescript(PRE_CANCELLATION_IMPORT_SCHEMA)
        conn.execute(
            """
            insert into users (
                id, username, username_key, password_hash, status,
                created_at, updated_at
            ) values (?, 'legacy', 'legacy', 'hash', 'active', ?, ?)
            """,
            (user_id, "2026-07-01T00:00:00Z", "2026-07-01T00:00:01Z"),
        )
        conn.execute(
            """
            insert into import_batches (id, user_id, created_at, updated_at)
            values (?, ?, ?, ?)
            """,
            (
                batch_id,
                user_id,
                "2026-07-02T00:00:00Z",
                "2026-07-02T00:01:00Z",
            ),
        )
        conn.execute(
            """
            insert into import_tasks (
                id, batch_id, user_id, document_id, original_name, file_suffix,
                size_bytes, staged_relative_path, status, stage, progress,
                total_attempt_count, auto_retry_count, manual_retry_count,
                max_auto_retries, next_attempt_at, error_code, error_summary,
                created_at, started_at, finished_at, updated_at
            ) values (
                ?, ?, ?, ?, 'legacy.md', '.md', 17, ?, 'failed', 'failed', 63,
                4, 2, 1, 7, null, 'legacy_error', 'legacy failure', ?, ?, ?, ?
            )
            """,
            (
                task_id,
                batch_id,
                user_id,
                document_id,
                f"imports/{batch_id}/{task_id}.md",
                "2026-07-02T00:00:00Z",
                "2026-07-02T00:00:10Z",
                "2026-07-02T00:00:20Z",
                "2026-07-02T00:00:20Z",
            ),
        )
        legacy_row = dict(
            conn.execute("select * from import_tasks where id = ?", (task_id,)).fetchone()
        )

    initialize_database(db_path)
    initialize_database(db_path)

    with connect(db_path) as conn:
        upgraded_row = dict(
            conn.execute("select * from import_tasks where id = ?", (task_id,)).fetchone()
        )
        index_names = {
            row["name"] for row in conn.execute("pragma index_list('import_tasks')")
        }
        foreign_key_tables = {
            row["table"] for row in conn.execute("pragma foreign_key_list('import_tasks')")
        }
        foreign_key_violations = conn.execute("pragma foreign_key_check").fetchall()

    assert upgraded_row.pop("cancel_requested_at") is None
    assert upgraded_row == legacy_row
    assert expected_indexes <= index_names
    assert foreign_key_tables == {"import_batches"}
    assert foreign_key_violations == []


def make_repo(tmp_path):
    db_path = tmp_path / "app.db"
    initialize_database(db_path)
    user = AuthService(db_path).register("import-user", "correct horse battery")
    return ImportTaskRepository(db_path), user.id


def make_task(user_id, *, batch_id=None, task_id=None, document_id=None):
    batch_id = batch_id or str(uuid.uuid4())
    task_id = task_id or str(uuid.uuid4())
    return ImportTaskCreate(
        task_id=task_id,
        batch_id=batch_id,
        user_id=user_id,
        document_id=document_id or str(uuid.uuid4()),
        original_name="a.md",
        file_suffix=".md",
        size_bytes=3,
        staged_relative_path=f"imports/{batch_id}/{task_id}.md",
    )


def test_cancel_queued_task_is_immediate_durable_and_not_retryable(tmp_path):
    repo, user_id = make_repo(tmp_path)
    task = make_task(user_id)
    repo.create_batch(user_id, [task], now="2026-08-15T00:00:00Z")

    decision = repo.request_cancel(
        user_id,
        task.batch_id,
        task.task_id,
        now="2026-08-15T00:00:01Z",
    )

    assert decision.outcome == "cancelled"
    assert decision.task.status == "cancelled"
    assert decision.task.stage == "cancelled"
    assert decision.task.cancel_requested_at == "2026-08-15T00:00:01Z"
    assert decision.task.finished_at == "2026-08-15T00:00:01Z"
    assert repo.get_batch(user_id, task.batch_id).cancelled == 1
    assert repo.claim_next(set()) is None
    with pytest.raises(InvalidImportTransition):
        repo.retry_task(user_id, task.task_id)

    reloaded = ImportTaskRepository(repo.db_path).get_task(user_id, task.task_id)
    assert reloaded == decision.task


def test_cancel_retry_wait_task_is_immediate_and_not_claimable(tmp_path):
    repo, user_id = make_repo(tmp_path)
    task = make_task(user_id)
    repo.create_batch(user_id, [task])
    running = repo.claim_next(set())
    repo.mark_retry_wait(
        user_id,
        running.task_id,
        next_attempt_at="2026-08-15T00:10:00Z",
        error_code="temporary",
        error_summary="retry later",
    )

    decision = repo.request_cancel(
        user_id,
        task.batch_id,
        task.task_id,
        now="2026-08-15T00:00:02Z",
    )

    assert decision.outcome == "cancelled"
    assert decision.task.status == "cancelled"
    assert decision.task.next_attempt_at is None
    assert decision.task.auto_retry_count == 1
    assert repo.claim_next(set(), now="2026-08-15T00:10:00Z") is None


def test_cancel_wins_before_committing(tmp_path):
    repo, user_id = make_repo(tmp_path)
    task = make_task(user_id)
    repo.create_batch(user_id, [task])
    running = repo.claim_next(set())

    decision = repo.request_cancel(
        running.user_id,
        running.batch_id,
        running.task_id,
        now="2026-08-15T00:00:01Z",
    )

    assert decision.outcome == "cancel_requested"
    assert repo.is_cancel_requested(running.user_id, running.task_id) is True
    assert (
        repo.try_begin_committing(
            running.user_id,
            running.task_id,
            now="2026-08-15T00:00:02Z",
        )
        is False
    )
    cancelled = repo.mark_cancelled(
        running.user_id,
        running.task_id,
        now="2026-08-15T00:00:03Z",
    )
    assert cancelled.status == "cancelled"
    assert cancelled.stage == "cancelled"
    assert cancelled.finished_at == "2026-08-15T00:00:03Z"


def test_committing_wins_before_cancel(tmp_path):
    repo, user_id = make_repo(tmp_path)
    task = make_task(user_id)
    repo.create_batch(user_id, [task])
    running = repo.claim_next(set())

    assert (
        repo.try_begin_committing(
            running.user_id,
            running.task_id,
            now="2026-08-15T00:00:01Z",
        )
        is True
    )
    decision = repo.request_cancel(
        running.user_id,
        running.batch_id,
        running.task_id,
        now="2026-08-15T00:00:02Z",
    )

    assert decision.outcome == "not_cancellable"
    assert decision.task.stage == "committing"
    assert decision.task.cancel_requested_at is None
    assert repo.is_cancel_requested(running.user_id, running.task_id) is False


def test_mark_cancelled_requires_running_task_with_cancel_request(tmp_path):
    repo, user_id = make_repo(tmp_path)
    task = make_task(user_id)
    repo.create_batch(user_id, [task])

    with pytest.raises(InvalidImportTransition):
        repo.mark_cancelled(user_id, task.task_id)

    repo.claim_next(set())
    with pytest.raises(InvalidImportTransition):
        repo.mark_cancelled(user_id, task.task_id)

    repo.request_cancel(user_id, task.batch_id, task.task_id)
    assert repo.mark_cancelled(user_id, task.task_id).status == "cancelled"


def test_cancel_is_idempotent_for_all_terminal_states(tmp_path):
    repo, user_id = make_repo(tmp_path)
    batch_id = str(uuid.uuid4())
    succeeded = make_task(
        user_id,
        batch_id=batch_id,
        task_id="00000000-0000-0000-0000-000000000210",
    )
    failed = make_task(
        user_id,
        batch_id=batch_id,
        task_id="00000000-0000-0000-0000-000000000211",
    )
    cancelled = make_task(
        user_id,
        batch_id=batch_id,
        task_id="00000000-0000-0000-0000-000000000212",
    )
    repo.create_batch(user_id, [succeeded, failed, cancelled])
    repo.claim_next(set())
    repo.mark_succeeded(user_id, succeeded.task_id)
    repo.claim_next(set())
    repo.mark_failed(user_id, failed.task_id, "invalid", "bad document")
    repo.request_cancel(user_id, batch_id, cancelled.task_id)

    for task in (succeeded, failed, cancelled):
        before = repo.get_task(user_id, task.task_id)
        decision = repo.request_cancel(
            user_id,
            batch_id,
            task.task_id,
            now="2026-08-15T01:00:00Z",
        )
        assert decision.outcome == "unchanged"
        assert decision.task == before


def test_cancel_and_commit_are_user_batch_and_task_scoped(tmp_path):
    repo, user_id = make_repo(tmp_path)
    other_user_id = AuthService(repo.db_path).register(
        "cancel-other-user", "correct horse battery"
    ).id
    task = make_task(user_id)
    repo.create_batch(user_id, [task])
    repo.claim_next(set())

    for scoped_user_id, batch_id, task_id in (
        (other_user_id, task.batch_id, task.task_id),
        (user_id, str(uuid.uuid4()), task.task_id),
        (user_id, task.batch_id, str(uuid.uuid4())),
    ):
        with pytest.raises(KeyError, match="not found"):
            repo.request_cancel(scoped_user_id, batch_id, task_id)

    assert repo.is_cancel_requested(other_user_id, task.task_id) is False
    assert repo.try_begin_committing(other_user_id, task.task_id) is False
    unchanged = repo.get_task(user_id, task.task_id)
    assert unchanged.stage == "queued"
    assert unchanged.cancel_requested_at is None


def test_two_connections_racing_cancel_and_commit_have_one_legal_winner(tmp_path):
    repo, user_id = make_repo(tmp_path)
    task = make_task(user_id)
    repo.create_batch(user_id, [task])
    repo.claim_next(set())
    cancel_repository = ImportTaskRepository(repo.db_path)
    commit_repository = ImportTaskRepository(repo.db_path)
    start = threading.Barrier(2)

    def cancel_from_connection():
        start.wait()
        return cancel_repository.request_cancel(
            user_id,
            task.batch_id,
            task.task_id,
            now="2026-08-15T00:00:01Z",
        )

    def commit_from_connection():
        start.wait()
        return commit_repository.try_begin_committing(
            user_id,
            task.task_id,
            now="2026-08-15T00:00:02Z",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        cancel_future = executor.submit(cancel_from_connection)
        commit_future = executor.submit(commit_from_connection)
        cancel_decision = cancel_future.result()
        commit_won = commit_future.result()

    final_task = repo.get_task(user_id, task.task_id)
    if commit_won:
        assert cancel_decision.outcome == "not_cancellable"
        assert final_task.stage == "committing"
        assert final_task.cancel_requested_at is None
    else:
        assert cancel_decision.outcome == "cancel_requested"
        assert final_task.stage != "committing"
        assert final_task.cancel_requested_at == "2026-08-15T00:00:01Z"


def test_create_claim_and_complete_task(tmp_path):
    repo, user_id = make_repo(tmp_path)
    task = make_task(user_id)

    created = repo.create_batch(user_id, [task], now="2026-07-30T00:00:00Z")
    claimed = repo.claim_next(blocked_user_ids=set(), now="2026-07-30T00:00:00Z")

    assert created.batch_id == task.batch_id
    assert claimed is not None
    assert claimed.task_id == task.task_id
    assert claimed.status == "running"
    assert claimed.total_attempt_count == 1
    repo.mark_succeeded(user_id, task.task_id, now="2026-07-30T00:01:00Z")
    assert repo.get_batch(user_id, task.batch_id).succeeded == 1


def test_release_claim_requeues_without_counting_an_attempt(tmp_path):
    repo, user_id = make_repo(tmp_path)
    task = make_task(user_id)
    repo.create_batch(user_id, [task], now="2026-07-30T00:00:00Z")
    claimed = repo.claim_next(set(), now="2026-07-30T00:00:01Z")
    repo.update_progress(
        user_id,
        claimed.task_id,
        "embedding",
        70,
        now="2026-07-30T00:00:02Z",
    )

    released = repo.release_claim(
        user_id, claimed.task_id, now="2026-07-30T00:00:03Z"
    )

    assert released.status == "queued"
    assert released.stage == "queued"
    assert released.progress == 0
    assert released.started_at is None
    assert released.next_attempt_at is None
    assert released.total_attempt_count == 0
    assert released.updated_at == "2026-07-30T00:00:03Z"
    assert repo.get_batch(user_id, task.batch_id).updated_at == released.updated_at


def test_release_claim_is_user_scoped_and_requires_running_state(tmp_path):
    repo, user_id = make_repo(tmp_path)
    other_user = AuthService(repo.db_path).register(
        "release-other-user", "correct horse battery"
    ).id
    task = make_task(user_id)
    repo.create_batch(user_id, [task])

    with pytest.raises(InvalidImportTransition):
        repo.release_claim(user_id, task.task_id)

    repo.claim_next(set())
    with pytest.raises(KeyError):
        repo.release_claim(other_user, task.task_id)
    assert repo.get_task(user_id, task.task_id).status == "running"

    repo.release_claim(user_id, task.task_id)
    with pytest.raises(InvalidImportTransition):
        repo.release_claim(user_id, task.task_id)


def test_invalid_succeeded_to_queued_transition_is_rejected(tmp_path):
    repo, user_id = make_repo(tmp_path)
    task = make_task(user_id)
    repo.create_batch(user_id, [task])
    repo.claim_next(blocked_user_ids=set())
    repo.mark_succeeded(user_id, task.task_id)

    with pytest.raises(InvalidImportTransition):
        repo.retry_task(user_id, task.task_id)


def test_claim_respects_blocked_user_and_claims_due_retry(tmp_path):
    repo, user_id = make_repo(tmp_path)
    task = make_task(user_id)
    repo.create_batch(user_id, [task])

    assert repo.claim_next({user_id}, now="2026-07-30T00:00:00Z") is None
    claimed = repo.claim_next(set(), now="2026-07-30T00:00:00Z")
    repo.mark_retry_wait(
        user_id,
        claimed.task_id,
        next_attempt_at="2026-07-30T00:00:02Z",
        error_code="temporary",
        error_summary="retry later",
        now="2026-07-30T00:00:00Z",
    )

    assert repo.claim_next(set(), now="2026-07-30T00:00:01Z") is None
    assert repo.claim_next(set(), now="2026-07-30T00:00:02Z").task_id == task.task_id


def test_competing_claims_preserve_one_running_task_per_user(tmp_path):
    repo, user_id = make_repo(tmp_path)
    batch_id = str(uuid.uuid4())
    first = make_task(
        user_id,
        batch_id=batch_id,
        task_id="00000000-0000-0000-0000-000000000010",
    )
    second = make_task(
        user_id,
        batch_id=batch_id,
        task_id="00000000-0000-0000-0000-000000000011",
    )
    repo.create_batch(user_id, [first, second], now="2026-07-30T00:00:00Z")
    start = threading.Barrier(2)

    def claim():
        start.wait()
        return repo.claim_next(set(), now="2026-07-30T00:00:00Z")

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: claim(), range(2)))

    claimed = [task for task in results if task is not None]
    assert len(claimed) == 1
    assert claimed[0].task_id == first.task_id
    summary = repo.get_batch(user_id, batch_id)
    assert summary.running == 1
    assert summary.queued == 1
    assert [task.total_attempt_count for task in summary.tasks] == [1, 0]


def test_claim_next_skips_candidate_when_running_user_index_conflicts(tmp_path, monkeypatch):
    repo, user_id = make_repo(tmp_path)
    other_user = AuthService(repo.db_path).register("fallback-user", "correct horse battery").id
    first = make_task(
        user_id,
        task_id="00000000-0000-0000-0000-000000000020",
    )
    second = make_task(
        other_user,
        task_id="00000000-0000-0000-0000-000000000021",
    )
    repo.create_batch(user_id, [first], now="2026-07-30T00:00:00Z")
    repo.create_batch(other_user, [second], now="2026-07-30T00:00:00Z")
    original_connect = import_repository.connect

    class FailingFirstClaimConnection:
        fail_next_claim = True

        def __init__(self, connection):
            self._connection = connection

        def execute(self, sql, parameters=()):
            if self.fail_next_claim and "set status = 'running'" in sql:
                self.fail_next_claim = False
                raise sqlite3.IntegrityError("uq_import_tasks_running_user")
            return self._connection.execute(sql, parameters)

        def __getattr__(self, name):
            return getattr(self._connection, name)

    def connect_with_failed_first_claim(db_path):
        return FailingFirstClaimConnection(original_connect(db_path))

    monkeypatch.setattr(import_repository, "connect", connect_with_failed_first_claim)

    claimed = repo.claim_next(set(), now="2026-07-30T00:00:00Z")
    monkeypatch.setattr(import_repository, "connect", original_connect)

    assert claimed.task_id == second.task_id
    assert repo.get_task(user_id, first.task_id).status == "queued"


def test_user_scoped_reads_and_failed_retry_reset_retry_state(tmp_path):
    repo, user_id = make_repo(tmp_path)
    other_user = AuthService(repo.db_path).register("other-user", "correct horse battery").id
    task = make_task(user_id)
    repo.create_batch(user_id, [task])
    claimed = repo.claim_next(set())
    repo.mark_failed(user_id, claimed.task_id, "invalid", "bad document")

    assert repo.get_task(other_user, task.task_id) is None
    assert repo.get_batch(other_user, task.batch_id) is None
    assert repo.retry_task(user_id, task.task_id).manual_retry_count == 1
    retried = repo.get_task(user_id, task.task_id)
    assert retried.status == "queued"
    assert retried.auto_retry_count == 0
    assert retried.error_code is None


def test_active_document_lookup_is_user_and_document_scoped(tmp_path):
    repo, user_id = make_repo(tmp_path)
    other_user = AuthService(repo.db_path).register(
        "active-document-other", "correct horse battery"
    ).id
    task = make_task(user_id)
    repo.create_batch(user_id, [task])

    assert repo.has_active_task_for_document(user_id, task.document_id) is True
    assert repo.has_active_task_for_document(other_user, task.document_id) is False
    assert repo.has_active_task_for_document(user_id, str(uuid.uuid4())) is False

    repo.claim_next(set())
    repo.mark_succeeded(user_id, task.task_id)
    assert repo.has_active_task_for_document(user_id, task.document_id) is False


def test_recover_running_requeues_existing_stage_and_fails_missing_stage(tmp_path):
    repo, user_id = make_repo(tmp_path)
    staged_task = make_task(user_id, task_id="00000000-0000-0000-0000-000000000001")
    missing_task = make_task(
        user_id,
        batch_id=staged_task.batch_id,
        task_id="00000000-0000-0000-0000-000000000002",
    )
    repo.create_batch(user_id, [staged_task, missing_task])
    storage = UserStorage(tmp_path / "data")
    storage.staged_import_path(
        user_id, staged_task.batch_id, staged_task.task_id, staged_task.file_suffix
    ).write_bytes(b"staged")
    repo.claim_next(set())
    repo.mark_failed(user_id, staged_task.task_id, "temporary", "free worker")
    repo.claim_next(set())

    assert repo.recover_running(storage) == 1
    recovered = repo.get_task(user_id, missing_task.task_id)
    assert recovered.status == "failed"
    assert recovered.error_code == "staged_file_missing"

    repo.retry_task(user_id, staged_task.task_id)
    repo.claim_next(set())
    assert repo.recover_running(storage) == 1
    recovered = repo.get_task(user_id, staged_task.task_id)
    assert recovered.status == "queued"
    assert recovered.error_code == "process_interrupted"


def test_storage_creates_uuid_scoped_staged_import_path(tmp_path):
    storage = UserStorage(tmp_path / "data")
    user_id, batch_id, task_id = (str(uuid.uuid4()) for _ in range(3))

    path = storage.staged_import_path(user_id, batch_id, task_id, ".MD")

    assert path == storage.import_batch_dir(user_id, batch_id) / f"{task_id}.md"
    assert path.parent.is_dir()


def test_storage_rejects_non_uuid_import_identifiers(tmp_path):
    storage = UserStorage(tmp_path / "data")

    with pytest.raises(ValueError, match="UUID"):
        storage.import_batch_dir("not-a-uuid", str(uuid.uuid4()))


def test_storage_rejects_staged_path_recorded_for_another_task(tmp_path):
    storage = UserStorage(tmp_path / "data")
    user_id, batch_id, task_id, other_task_id = (
        str(uuid.uuid4()) for _ in range(4)
    )
    other = storage.staged_import_path(user_id, batch_id, other_task_id, ".md")

    with pytest.raises(ValueError, match="does not match"):
        storage.resolve_staged_import_path(
            user_id,
            batch_id,
            task_id,
            ".md",
            str(other.relative_to(storage.user_paths(user_id).root)),
        )
