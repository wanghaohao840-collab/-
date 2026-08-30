from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import app.qa_job_repository as qa_job_repository_module
from app.database import connect, initialize_database
from app.qa_answer_engine import QaAnswerResult, QaEngineError
from app.qa_context import QaContextBuilder
from app.qa_job_repository import QaJobRepository
from app.qa_memory import QaMemoryLinker
from app.qa_models import QaDocumentCandidate, QaSourceDraft
from app.qa_observability import InProcessQaTelemetry
from app.qa_repository import QaRepository
from app.qa_worker import QaWorkerPool


OWNER = "owner"


class RuntimeRegistry:
    def __init__(self):
        self.runtime = SimpleNamespace(name="runtime")
        self.acquired = []
        self.released = []

    def acquire_background(self, user_id):
        self.acquired.append(user_id)
        return self.runtime

    def release_background(self, user_id):
        self.released.append(user_id)


class Engine:
    def __init__(self, outcomes=None, *, block=False):
        self.outcomes = list(outcomes or [])
        self.calls = []
        self.started = threading.Event()
        self.release = threading.Event()
        if not block:
            self.release.set()

    def answer(self, runtime, request, **kwargs):
        self.calls.append((runtime, request, kwargs))
        self.started.set()
        assert self.release.wait(timeout=3)
        callback = kwargs.get("progress_callback")
        if callback:
            callback("generating", 1, 2, "safe")
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome or QaAnswerResult(
            "总结回答",
            (QaSourceDraft("S-1", "doc", "文档.pdf", excerpt="证据"),),
            "summary",
        )


@pytest.fixture
def worker_parts(tmp_path):
    db_path = tmp_path / "app.db"
    initialize_database(db_path)
    with connect(db_path) as conn:
        conn.execute(
            """
            insert into users (
                id, username, username_key, password_hash, created_at, updated_at
            ) values ('owner', 'Owner', 'owner', 'hash',
                      '2026-08-26T00:00:00Z', '2026-08-26T00:00:00Z')
            """
        )
    qa = QaRepository(db_path)
    jobs = QaJobRepository(db_path)
    conversation = qa.create_conversation(
        OWNER, (QaDocumentCandidate("doc", "文档.pdf", OWNER),)
    )
    return qa, jobs, conversation


def wait_for(predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("condition was not reached")


def make_pool(parts, engine, runtime_registry=None, summarizer=None):
    qa, jobs, _ = parts
    registry = runtime_registry or RuntimeRegistry()
    return QaWorkerPool(
        jobs,
        qa,
        registry,
        engine,
        QaContextBuilder(2000),
        InProcessQaTelemetry(),
        summarizer=summarizer,
        worker_count=1,
        lease_seconds=5,
        poll_interval=0.02,
    ), registry


def test_summary_worker_commits_answer_and_releases_runtime(worker_parts) -> None:
    qa, jobs, conversation = worker_parts
    enqueue = jobs.create_summary_turn_and_job(
        OWNER, conversation.id, "总结", "client-1"
    )
    pool, registry = make_pool(worker_parts, Engine())
    pool.start()
    try:
        pool.notify()
        completed = wait_for(
            lambda: (
                job
                if (job := jobs.get(OWNER, enqueue.job.id)).status == "completed"
                else None
            )
        )
    finally:
        pool.stop()
    message = qa.get_message(OWNER, enqueue.pending.assistant_message.id)
    assert completed.progress == 100
    assert message.status == "completed"
    assert message.content == "总结回答"
    assert [source.citation_id for source in message.sources] == ["S-1"]
    assert registry.acquired == registry.released == [OWNER]


def test_cancel_after_model_return_wins_before_atomic_commit(worker_parts) -> None:
    qa, jobs, conversation = worker_parts
    enqueue = jobs.create_summary_turn_and_job(
        OWNER, conversation.id, "总结", "client-1"
    )
    engine = Engine(block=True)
    pool, _ = make_pool(worker_parts, engine)
    pool.start()
    try:
        pool.notify()
        assert engine.started.wait(timeout=3)
        jobs.request_cancel(OWNER, enqueue.job.id)
        engine.release.set()
        wait_for(lambda: jobs.get(OWNER, enqueue.job.id).status == "cancelled")
    finally:
        pool.stop()
    assert qa.get_message(OWNER, enqueue.pending.assistant_message.id).status == "cancelled"


def test_retryable_engine_failure_is_requeued_then_succeeds(worker_parts) -> None:
    qa, jobs, conversation = worker_parts
    enqueue = jobs.create_summary_turn_and_job(
        OWNER, conversation.id, "总结", "client-1", max_attempts=2
    )
    engine = Engine([QaEngineError("rag_connection", True), None])
    pool, _ = make_pool(worker_parts, engine)
    pool.start()
    try:
        pool.notify()
        job = wait_for(
            lambda: (
                current
                if (current := jobs.get(OWNER, enqueue.job.id)).status == "completed"
                else None
            )
        )
    finally:
        pool.stop()
    assert job.attempt_count == 2
    assert len(engine.calls) == 2
    assert qa.get_message(OWNER, enqueue.pending.assistant_message.id).status == "completed"


def test_rolling_summary_refresh_is_deduplicated_and_compare_and_set(
    worker_parts,
) -> None:
    qa, jobs, conversation = worker_parts
    turn = qa.create_pending_turn(
        OWNER, conversation.id, "问题", "joint", "client-1"
    )
    qa.complete_turn(OWNER, turn.assistant_message.id, 0, "回答", (), "none", None)

    class Summarizer:
        def __init__(self):
            self.calls = []

        def summarize(self, runtime, previous_summary, completed_turns):
            self.calls.append((runtime, previous_summary, completed_turns))
            return "滚动摘要"

    summarizer = Summarizer()
    pool, _ = make_pool(worker_parts, Engine(), summarizer=summarizer)
    pool.schedule_summary_refresh(OWNER, conversation.id)
    pool.schedule_summary_refresh(OWNER, conversation.id)
    pool.start()
    try:
        updated = wait_for(
            lambda: (
                item
                if (item := qa.get_conversation(OWNER, conversation.id)).conversation.rolling_summary
                else None
            )
        )
    finally:
        pool.stop()
    assert updated.conversation.rolling_summary == "滚动摘要"
    assert len(summarizer.calls) == 1


def test_worker_processes_deterministic_memory_sync(worker_parts) -> None:
    qa, jobs, conversation = worker_parts
    turn = qa.create_pending_turn(
        OWNER, conversation.id, "问题", "joint", "client-memory"
    )
    qa.complete_turn(OWNER, turn.assistant_message.id, 0, "回答", (), "none", None)

    class Manager:
        def __init__(self):
            self.ids = []

        def add_memory(self, content, **kwargs):
            self.ids.append(kwargs["memory_id"])
            return kwargs["memory_id"]

        def remove_memory(self, memory_id, *, memory_type):
            return True

    registry = RuntimeRegistry()
    manager = Manager()
    registry.runtime.memory_tool = SimpleNamespace(memory_manager=manager)
    pool = QaWorkerPool(
        jobs,
        qa,
        registry,
        Engine(),
        QaContextBuilder(2000),
        InProcessQaTelemetry(),
        memory_linker=QaMemoryLinker(qa),
        worker_count=1,
        lease_seconds=5,
        poll_interval=0.02,
    )
    pool.start()
    try:
        linked = wait_for(
            lambda: (
                message
                if (message := qa.get_message(OWNER, turn.assistant_message.id)).memory_sync_status
                == "completed"
                else None
            )
        )
    finally:
        pool.stop()
    assert linked.memory_id == manager.ids[0]


def test_live_worker_reconciles_final_attempt_expiry_without_notification(
    worker_parts,
    monkeypatch,
) -> None:
    qa, jobs, conversation = worker_parts
    now = datetime(2026, 8, 27, 8, 0, tzinfo=timezone.utc)
    clock = {"value": now.isoformat().replace("+00:00", "Z")}
    monkeypatch.setattr(
        qa_job_repository_module, "_utc_now", lambda: clock["value"]
    )
    enqueued = jobs.create_summary_turn_and_job(
        OWNER,
        conversation.id,
        "总结",
        "client-live-expiry",
        max_attempts=1,
    )
    claimed = jobs.claim_next("lost-worker", lease_seconds=30)
    assert claimed is not None
    engine = Engine()
    pool, _ = make_pool(worker_parts, engine)

    pool.start()
    try:
        assert jobs.get(OWNER, enqueued.job.id).status == "running"
        clock["value"] = (now + timedelta(seconds=31)).isoformat().replace(
            "+00:00", "Z"
        )
        terminal = wait_for(
            lambda: (
                current
                if (current := jobs.get(OWNER, enqueued.job.id)).status == "failed"
                else None
            )
        )
    finally:
        pool.stop()

    assistant = qa.get_message(OWNER, enqueued.pending.assistant_message.id)
    assert terminal.safe_error_code == "QA_JOB_INTERRUPTED"
    assert assistant.status == "failed"
    assert assistant.safe_error_code == "QA_JOB_INTERRUPTED"
    assert jobs.get_active_for_conversation(OWNER, conversation.id) is None
    assert engine.calls == []


def test_live_worker_reconciles_cancel_requested_expiry_without_notification(
    worker_parts,
    monkeypatch,
) -> None:
    qa, jobs, conversation = worker_parts
    now = datetime(2026, 8, 27, 8, 0, tzinfo=timezone.utc)
    clock = {"value": now.isoformat().replace("+00:00", "Z")}
    monkeypatch.setattr(
        qa_job_repository_module, "_utc_now", lambda: clock["value"]
    )
    enqueued = jobs.create_summary_turn_and_job(
        OWNER,
        conversation.id,
        "总结",
        "client-live-cancelled-expiry",
        max_attempts=1,
    )
    claimed = jobs.claim_next("lost-worker", lease_seconds=30)
    assert claimed is not None
    requested = jobs.request_cancel(
        OWNER,
        claimed.id,
        now=(now + timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
    )
    assert requested.status == "running"
    engine = Engine()
    pool, _ = make_pool(worker_parts, engine)

    pool.start()
    try:
        clock["value"] = (now + timedelta(seconds=31)).isoformat().replace(
            "+00:00", "Z"
        )
        terminal = wait_for(
            lambda: (
                current
                if (current := jobs.get(OWNER, enqueued.job.id)).status
                == "cancelled"
                else None
            )
        )
    finally:
        pool.stop()

    assistant = qa.get_message(OWNER, enqueued.pending.assistant_message.id)
    assert terminal.safe_error_code is None
    assert assistant.status == "cancelled"
    assert assistant.safe_error_code is None
    assert jobs.get_active_for_conversation(OWNER, conversation.id) is None
    assert engine.calls == []
