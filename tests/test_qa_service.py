from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.database import connect, initialize_database
from app.document_library import DocumentLibraryItem
from app.qa_answer_engine import QaAnswerResult, QaEngineError
from app.qa_context import QaContextBuilder
from app.qa_models import QaSourceDraft, QaValidationError
from app.qa_observability import InProcessQaTelemetry
from app.qa_repository import QaRepository
from app.qa_service import (
    QaEngineUnavailableError,
    QaNotFoundError,
    QaRetryNotAllowedError,
    QaService,
)


OWNER = "owner"
OTHER = "other"
TOKEN = "owner-token"


class Sessions:
    def __init__(self):
        self.sessions = {
            TOKEN: SimpleNamespace(user_id=OWNER, runtime=SimpleNamespace(name="owner")),
            "other-token": SimpleNamespace(
                user_id=OTHER, runtime=SimpleNamespace(name="other")
            ),
        }

    def get_session(self, token):
        return self.sessions[token]


class Library:
    def __init__(self):
        self.items = {
            TOKEN: (
                DocumentLibraryItem("doc-1", "一.pdf", ".pdf", 1, None),
                DocumentLibraryItem("doc-2", "二.pdf", ".pdf", 1, None),
            ),
            "other-token": (
                DocumentLibraryItem("other-doc", "他.pdf", ".pdf", 1, None),
            ),
        }

    def list_documents(self, token):
        return self.items[token]


class Engine:
    def __init__(self, outcomes=None):
        self.outcomes = list(outcomes or [])
        self.calls = []

    def answer(self, runtime, request, **kwargs):
        self.calls.append((runtime, request, kwargs))
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome or QaAnswerResult(
            answer="回答",
            sources=(
                QaSourceDraft(
                    "S-1", "doc-1", "一.pdf", excerpt="证据", reference="[S-1]"
                ),
            ),
            mode=request.mode,
        )


@pytest.fixture
def service_parts(tmp_path):
    db_path = tmp_path / "app.db"
    initialize_database(db_path)
    with connect(db_path) as conn:
        conn.executemany(
            """
            insert into users (
                id, username, username_key, password_hash, created_at, updated_at
            ) values (?, ?, ?, 'hash', '2026-08-26T00:00:00Z', '2026-08-26T00:00:00Z')
            """,
            ((OWNER, "Owner", "owner"), (OTHER, "Other", "other")),
        )
    repository = QaRepository(db_path)
    sessions = Sessions()
    library = Library()
    telemetry = InProcessQaTelemetry()
    return db_path, repository, sessions, library, telemetry


def make_service(parts, engine=None):
    _, repository, sessions, library, telemetry = parts
    return QaService(
        sessions,
        library,
        repository,
        engine or Engine(),
        QaContextBuilder(max_input_tokens=2000),
        telemetry,
    )


class Jobs:
    def __init__(self, result=None):
        self.result = result
        self.calls = []

    def create_summary_turn_and_job(self, *args, **kwargs):
        self.calls.append(("create", args, kwargs))
        return self.result

    def get(self, *args, **kwargs):
        self.calls.append(("get", args, kwargs))
        return self.result.job if self.result else None

    def request_cancel(self, *args, **kwargs):
        self.calls.append(("cancel", args, kwargs))
        return self.result.job if self.result else None


class Worker:
    def __init__(self):
        self.notifications = 0

    def notify(self):
        self.notifications += 1

    def schedule_summary_refresh(self, user_id, conversation_id):
        pass


def test_create_conversation_preserves_verified_scope_and_is_user_scoped(
    service_parts,
) -> None:
    service = make_service(service_parts)
    created = service.create_conversation(TOKEN, ("doc-2", "doc-1"))
    assert [item.document_id for item in created.documents] == ["doc-2", "doc-1"]
    with pytest.raises(QaNotFoundError):
        service.get_conversation("other-token", created.id)
    with pytest.raises(QaNotFoundError):
        service.create_conversation(TOKEN, ("other-doc",))


def test_legacy_gradio_submission_uses_shared_qa_domain_once(
    service_parts,
) -> None:
    service = make_service(service_parts)
    conversation = service.create_legacy_single_turn_conversation(
        TOKEN, ["一.pdf | doc-1"]
    )
    message = service.ask(
        TOKEN, conversation.id, "问题", "joint", "gradio-request"
    )

    assert conversation.conversation.origin == "legacy_gradio"
    assert message.content == "回答"
    report_turns = service.report_turns(TOKEN)
    assert len(report_turns) == 1
    assert report_turns[0].question == "问题"
    assert report_turns[0].answer == "回答"


def test_duplicate_request_calls_engine_once_and_returns_persisted_answer(
    service_parts,
) -> None:
    engine = Engine()
    service = make_service(service_parts, engine)
    conversation = service.create_conversation(TOKEN, ("doc-1",))
    first = service.ask(TOKEN, conversation.id, "问题", "joint", "client-1")
    duplicate = service.ask(TOKEN, conversation.id, "ignored", "joint", "client-1")
    assert duplicate.id == first.id
    assert duplicate.status == "completed"
    assert len(engine.calls) == 1
    assert engine.calls[0][1].question == "问题"
    assert engine.calls[0][1].document_ids == ("doc-1",)
    assert service.telemetry.snapshot()["qa_idempotent_hit"] == 1


def test_compare_and_missing_fixed_scope_fail_before_engine(service_parts) -> None:
    engine = Engine()
    service = make_service(service_parts, engine)
    conversation = service.create_conversation(TOKEN, ("doc-1",))
    with pytest.raises(QaValidationError) as compare:
        service.ask(TOKEN, conversation.id, "比较", "compare", "client-1")
    assert compare.value.code == "QA_COMPARE_REQUIRES_MULTIPLE_DOCUMENTS"

    service.document_library.items[TOKEN] = ()
    with pytest.raises(QaNotFoundError):
        service.ask(TOKEN, conversation.id, "问题", "joint", "client-2")
    assert engine.calls == []


def test_engine_failure_persists_only_safe_code_and_trace(service_parts) -> None:
    engine = Engine([RuntimeError("C:/secret/raw prompt")])
    service = make_service(service_parts, engine)
    conversation = service.create_conversation(TOKEN, ("doc-1",))
    with pytest.raises(QaEngineUnavailableError) as failure:
        service.ask(TOKEN, conversation.id, "敏感问题", "joint", "client-1")
    messages = service.list_messages(TOKEN, conversation.id).items
    assistant = next(item for item in messages if item.role == "assistant")
    assert assistant.status == "failed"
    assert assistant.safe_error_code == "QA_ENGINE_UNAVAILABLE"
    assert assistant.trace_id == failure.value.trace_id
    assert "secret" not in repr(assistant)


def test_lowercase_rag_error_is_mapped_to_retryable_safe_domain_code(
    service_parts,
) -> None:
    engine = Engine([QaEngineError("rag_connection", retryable=True)])
    service = make_service(service_parts, engine)
    conversation = service.create_conversation(TOKEN, ("doc-1",))
    with pytest.raises(QaEngineUnavailableError) as failure:
        service.ask(TOKEN, conversation.id, "问题", "joint", "client-1")
    assistant = next(
        item
        for item in service.list_messages(TOKEN, conversation.id).items
        if item.role == "assistant"
    )
    assert failure.value.code == "QA_RAG_CONNECTION"
    assert assistant.safe_error_code == "QA_RAG_CONNECTION"


def test_retry_reuses_question_and_links_new_assistant(service_parts) -> None:
    engine = Engine(
        [
            QaEngineError("RAG_CONNECTION_FAILED", retryable=True),
            QaAnswerResult("恢复回答", (), "joint"),
        ]
    )
    service = make_service(service_parts, engine)
    conversation = service.create_conversation(TOKEN, ("doc-1",))
    with pytest.raises(QaEngineUnavailableError):
        service.ask(TOKEN, conversation.id, "原问题", "joint", "client-1")
    failed = next(
        item
        for item in service.list_messages(TOKEN, conversation.id).items
        if item.role == "assistant"
    )

    retried = service.retry(TOKEN, failed.id, "client-2")
    duplicate = service.retry(TOKEN, failed.id, "client-2")
    assert retried.status == "completed"
    assert duplicate.id == retried.id
    assert retried.retry_of_message_id == failed.id
    assert engine.calls[-1][1].question == "原问题"
    assert len(engine.calls) == 2
    messages = service.list_messages(TOKEN, conversation.id).items
    assert [message.role for message in messages].count("user") == 1
    assert [message.role for message in messages].count("assistant") == 2
    report_turns = service.report_turns(TOKEN)
    assert [(turn.question, turn.answer) for turn in report_turns] == [
        ("原问题", "恢复回答")
    ]

    with pytest.raises(QaRetryNotAllowedError):
        service.retry(TOKEN, retried.id, "client-3")


def test_engine_runs_after_pending_transaction_has_committed(service_parts) -> None:
    db_path = service_parts[0]

    class LockCheckingEngine(Engine):
        def answer(self, runtime, request, **kwargs):
            with connect(db_path) as conn:
                conn.execute("begin immediate")
                conn.rollback()
            return super().answer(runtime, request, **kwargs)

    service = make_service(service_parts, LockCheckingEngine())
    conversation = service.create_conversation(TOKEN, ("doc-1",))
    assert service.ask(
        TOKEN, conversation.id, "问题", "joint", "client-1"
    ).status == "completed"


def test_summary_service_methods_are_user_scoped_and_notify_worker(
    service_parts,
) -> None:
    from app.qa_job_repository import QaJobRepository

    db_path, repository, sessions, library, telemetry = service_parts
    real_jobs = QaJobRepository(db_path)
    worker = Worker()
    service = QaService(
        sessions,
        library,
        repository,
        Engine(),
        QaContextBuilder(2000),
        telemetry,
        job_repository=real_jobs,
        worker_pool=worker,
    )
    conversation = service.create_conversation(TOKEN, ("doc-1",))
    job = service.start_summary(TOKEN, conversation.id, "", "summary-1")
    assert job.status == "queued"
    assert worker.notifications == 1
    assert service.get_job(TOKEN, job.id).id == job.id
    assert service.get_job("other-token", job.id) is None
    assert service.cancel_job(TOKEN, job.id).status == "cancelled"
