import sqlite3
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

import app.import_repository as import_repository
from app.auth import AuthService
from app.database import initialize_database
from app.import_models import ImportTaskCreate
from app.import_repository import ImportTaskRepository, InvalidImportTransition
from app.storage import UserStorage


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
