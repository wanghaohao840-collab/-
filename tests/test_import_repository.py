import uuid

import pytest

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
