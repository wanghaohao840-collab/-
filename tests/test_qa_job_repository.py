from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.database import connect, initialize_database
from app.qa_job_repository import QaJobRepository
from app.qa_models import QaConflictError, QaDocumentCandidate
from app.qa_repository import QaRepository


OWNER = "owner"
OTHER = "other"
NOW = datetime(2026, 8, 26, 8, 0, tzinfo=timezone.utc)


def iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


@pytest.fixture
def repositories(tmp_path):
    db_path = tmp_path / "app.db"
    initialize_database(db_path)
    with connect(db_path) as conn:
        conn.executemany(
            """
            insert into users (
                id, username, username_key, password_hash, created_at, updated_at
            ) values (?, ?, ?, 'hash', ?, ?)
            """,
            (
                (OWNER, "Owner", "owner", iso(NOW), iso(NOW)),
                (OTHER, "Other", "other", iso(NOW), iso(NOW)),
            ),
        )
    qa = QaRepository(db_path)
    jobs = QaJobRepository(db_path)
    return qa, jobs


def conversation(qa: QaRepository):
    return qa.create_conversation(
        OWNER, (QaDocumentCandidate("doc", "文档.pdf", OWNER),), now=iso(NOW)
    )


def test_summary_enqueue_is_atomic_and_idempotent(repositories) -> None:
    qa, jobs = repositories
    created = conversation(qa)
    first = jobs.create_summary_turn_and_job(
        OWNER, created.id, "总结文档", "client-1", now=iso(NOW)
    )
    duplicate = jobs.create_summary_turn_and_job(
        OWNER, created.id, "ignored", "client-1", now=iso(NOW)
    )
    assert duplicate.duplicate is True
    assert duplicate.job.id == first.job.id
    assert len(qa.list_messages(OWNER, created.id).items) == 2
    assert jobs.get(OTHER, first.job.id) is None


def test_active_job_discovery_is_user_conversation_and_fence_scoped(
    repositories,
) -> None:
    qa, jobs = repositories
    created = conversation(qa)
    enqueued = jobs.create_summary_turn_and_job(
        OWNER, created.id, "总结", "client-active", now=iso(NOW)
    )

    assert jobs.get_active_for_conversation(OWNER, created.id).id == enqueued.job.id
    assert jobs.get_active_for_conversation(OTHER, created.id) is None

    cancelled = jobs.request_cancel(OWNER, enqueued.job.id, now=iso(NOW))
    assert cancelled.status == "cancelled"
    assert jobs.get_active_for_conversation(OWNER, created.id) is None

    second = jobs.create_summary_turn_and_job(
        OWNER, created.id, "再次总结", "client-fenced", now=iso(NOW)
    )
    with connect(qa.db_path) as conn:
        conn.execute(
            """
            insert into qa_deletion_fences (
                id, user_id, target_type, target_id, status, stage,
                created_at, updated_at
            ) values ('fence', ?, 'conversation', ?, 'queued', 'fenced', ?, ?)
            """,
            (OWNER, created.id, iso(NOW), iso(NOW)),
        )
    assert jobs.get_active_for_conversation(OWNER, created.id) is None
    assert jobs.get(OWNER, second.job.id) is not None


def test_expired_running_job_is_reclaimed_and_stale_owner_loses(repositories) -> None:
    qa, jobs = repositories
    created = conversation(qa)
    enqueued = jobs.create_summary_turn_and_job(
        OWNER, created.id, "总结", "client-1", now=iso(NOW)
    )
    first = jobs.claim_next("worker-a", lease_seconds=30, now=iso(NOW))
    assert first.id == enqueued.job.id
    assert first.attempt_count == 1

    reclaimed_at = NOW + timedelta(seconds=31)
    second = jobs.claim_next("worker-b", lease_seconds=45, now=iso(reclaimed_at))
    assert second.id == first.id
    assert second.lease_owner == "worker-b"
    assert second.attempt_count == 2
    assert not jobs.heartbeat(
        first.id,
        "worker-a",
        progress=50,
        stage="generating",
        now=iso(reclaimed_at),
    )
    assert jobs.heartbeat(
        first.id,
        "worker-b",
        progress=50,
        stage="generating",
        now=iso(reclaimed_at),
    )


def test_one_active_job_and_queued_cancel_are_database_enforced(repositories) -> None:
    qa, jobs = repositories
    created = conversation(qa)
    first = jobs.create_summary_turn_and_job(
        OWNER, created.id, "总结", "client-1", now=iso(NOW)
    )
    with pytest.raises(QaConflictError) as conflict:
        jobs.create_summary_turn_and_job(
            OWNER, created.id, "再次总结", "client-2", now=iso(NOW)
        )
    assert conflict.value.code == "QA_CONVERSATION_BUSY"

    cancelled = jobs.request_cancel(OWNER, first.job.id, now=iso(NOW))
    assert cancelled.status == "cancelled"
    assert qa.get_message(OWNER, first.pending.assistant_message.id).status == "cancelled"
    assert jobs.claim_next("worker", lease_seconds=30, now=iso(NOW)) is None


def test_failed_job_insert_rolls_back_the_new_turn(repositories) -> None:
    qa, jobs = repositories
    created = conversation(qa)
    first = jobs.create_summary_turn_and_job(
        OWNER, created.id, "总结", "client-1", now=iso(NOW)
    )
    with connect(qa.db_path) as conn:
        conn.execute(
            """
            update qa_messages set status = 'failed'
            where id = ? and user_id = ?
            """,
            (first.pending.assistant_message.id, OWNER),
        )

    with pytest.raises(QaConflictError):
        jobs.create_summary_turn_and_job(
            OWNER, created.id, "另一个总结", "client-2", now=iso(NOW)
        )
    messages = qa.list_messages(OWNER, created.id).items
    assert {message.client_request_id for message in messages if message.role == "user"} == {
        "client-1"
    }


def test_running_cancel_wins_before_completion(repositories) -> None:
    qa, jobs = repositories
    created = conversation(qa)
    enqueued = jobs.create_summary_turn_and_job(
        OWNER, created.id, "总结", "client-1", now=iso(NOW)
    )
    claimed = jobs.claim_next("worker", lease_seconds=30, now=iso(NOW))
    requested = jobs.request_cancel(OWNER, claimed.id, now=iso(NOW))
    assert requested.status == "running"
    assert requested.cancel_requested_at == iso(NOW)
    assert not jobs.complete(claimed.id, "worker", now=iso(NOW))
    assert jobs.get(OWNER, claimed.id).status == "cancelled"
    assert qa.get_message(OWNER, enqueued.pending.assistant_message.id).status == "cancelled"


def test_job_completion_requires_the_answer_to_commit_first(repositories) -> None:
    qa, jobs = repositories
    created = conversation(qa)
    enqueued = jobs.create_summary_turn_and_job(
        OWNER, created.id, "总结", "client-1", now=iso(NOW)
    )
    claimed = jobs.claim_next("worker", lease_seconds=30, now=iso(NOW))
    assert not jobs.complete(claimed.id, "worker", now=iso(NOW))
    assert qa.complete_turn(
        OWNER,
        enqueued.pending.assistant_message.id,
        enqueued.pending.assistant_message.version,
        "总结结果",
        (),
        "none",
        None,
        now=iso(NOW),
    )
    assert jobs.complete(claimed.id, "worker", now=iso(NOW))


def test_transient_failure_retries_to_cap_then_fails_assistant(repositories) -> None:
    qa, jobs = repositories
    created = conversation(qa)
    enqueued = jobs.create_summary_turn_and_job(
        OWNER, created.id, "总结", "client-1", max_attempts=2, now=iso(NOW)
    )
    first = jobs.claim_next("worker-a", lease_seconds=30, now=iso(NOW))
    retried = jobs.fail_or_retry(
        first.id,
        "worker-a",
        "QA_ENGINE_UNAVAILABLE",
        "trace-1",
        now=iso(NOW),
    )
    assert retried.status == "queued"
    second = jobs.claim_next("worker-b", lease_seconds=30, now=iso(NOW))
    failed = jobs.fail_or_retry(
        second.id,
        "worker-b",
        "QA_ENGINE_UNAVAILABLE",
        "trace-2",
        now=iso(NOW),
    )
    assert failed.status == "failed"
    assert qa.get_message(OWNER, enqueued.pending.assistant_message.id).status == "failed"


def test_recover_expired_requeues_without_touching_terminal_jobs(repositories) -> None:
    qa, jobs = repositories
    created = conversation(qa)
    enqueued = jobs.create_summary_turn_and_job(
        OWNER, created.id, "总结", "client-1", now=iso(NOW)
    )
    jobs.claim_next("worker", lease_seconds=30, now=iso(NOW))
    assert jobs.recover_expired(now=iso(NOW + timedelta(seconds=31))) == 1
    assert jobs.get(OWNER, enqueued.job.id).status == "queued"
    assert jobs.recover_expired(now=iso(NOW + timedelta(seconds=32))) == 0


def test_claim_pass_terminalizes_expired_final_attempt_and_assistant(
    repositories,
) -> None:
    qa, jobs = repositories
    created = conversation(qa)
    enqueued = jobs.create_summary_turn_and_job(
        OWNER,
        created.id,
        "总结",
        "client-final-expiry",
        max_attempts=1,
        now=iso(NOW),
    )
    claimed = jobs.claim_next("worker-a", lease_seconds=30, now=iso(NOW))
    assert claimed is not None
    assert claimed.attempt_count == claimed.max_attempts == 1

    assert jobs.claim_next(
        "worker-b", lease_seconds=30, now=iso(NOW + timedelta(seconds=31))
    ) is None

    terminal = jobs.get(OWNER, enqueued.job.id)
    assistant = qa.get_message(OWNER, enqueued.pending.assistant_message.id)
    assert terminal.status == "failed"
    assert terminal.safe_error_code == "QA_JOB_INTERRUPTED"
    assert assistant.status == "failed"
    assert assistant.safe_error_code == "QA_JOB_INTERRUPTED"
    assert jobs.get_active_for_conversation(OWNER, created.id) is None
    assert not jobs.heartbeat(
        claimed.id,
        "worker-a",
        progress=50,
        stage="late",
        now=iso(NOW + timedelta(seconds=31)),
    )


def test_claim_pass_cancels_expired_cancel_requested_final_attempt(
    repositories,
) -> None:
    qa, jobs = repositories
    created = conversation(qa)
    enqueued = jobs.create_summary_turn_and_job(
        OWNER,
        created.id,
        "总结",
        "client-cancelled-expiry",
        max_attempts=1,
        now=iso(NOW),
    )
    claimed = jobs.claim_next("worker-a", lease_seconds=30, now=iso(NOW))
    assert claimed is not None
    requested = jobs.request_cancel(
        OWNER, claimed.id, now=iso(NOW + timedelta(seconds=1))
    )
    assert requested.status == "running"

    assert jobs.claim_next(
        "worker-b", lease_seconds=30, now=iso(NOW + timedelta(seconds=31))
    ) is None

    terminal = jobs.get(OWNER, enqueued.job.id)
    assistant = qa.get_message(OWNER, enqueued.pending.assistant_message.id)
    assert terminal.status == "cancelled"
    assert assistant.status == "cancelled"
    assert jobs.get_active_for_conversation(OWNER, created.id) is None
