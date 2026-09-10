from __future__ import annotations

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import RLock
from types import SimpleNamespace

import pytest

from app.database import connect, initialize_database
from app.note_models import (
    DocumentChunkSourceSelector,
    NoteSourceUnavailableError,
    NoteFilters,
    NoteIdempotencyConflict,
    NoteNotFoundError,
    NoteSourceSelector,
)
from app.note_repository import NoteRepository
from app.note_service import NoteService
from app.qa_repository import QaRepository
from app.document_search import SearchChunkLocator, ResolvedDocumentChunk, DocumentSearchSourceStaleError, DocumentSearchUnavailableError


class Migration:
    def __init__(self):
        self.users = []
        runtime = SimpleNamespace(lock=RLock())
        self.runtime_registry = SimpleNamespace(get_or_create=lambda _user_id: runtime)
    def ensure_user_migrated(self, user_id):
        self.users.append(user_id)


class Worker:
    def __init__(self, fail=False):
        self.notifications = 0
        self.fail = fail
    def notify(self):
        self.notifications += 1
        if self.fail:
            raise RuntimeError("worker unavailable")


def seed_qa(db: Path) -> None:
    with connect(db) as conn:
        for user in ("alice", "bob"):
            conn.execute("insert into users values (?,?,?,?, 'active','t','t')", (user, user, user, "x"))
            conn.execute("insert into qa_conversations (id,user_id,title,origin,created_at,updated_at,last_message_at) values (?,?,?,'product','t','t','t')", (f"thread-{user}", user, "thread"))
            conn.execute("insert into qa_conversation_documents values (?,?,?,?,0)", (f"thread-{user}", user, f"doc-{user}", "Document"))
            conn.execute("insert into qa_messages (id,conversation_id,user_id,turn_id,role,status,content,source_state,created_at,updated_at) values (?,?,?,?, 'assistant','completed',?,'available','t','t')", (f"message-{user}", f"thread-{user}", user, "turn", "answer body"))
            conn.execute("insert into qa_message_sources (id,assistant_message_id,conversation_id,user_id,position,citation_id,document_id,document_name,page_number,section,excerpt,reference,truncated,source_type) values (?,?,?,?,0,?,?,?,?,?,?,?,0,'rag')", (f"source-{user}", f"message-{user}", f"thread-{user}", user, f"citation-{user}", f"doc-{user}", "Document", 3, "S", "excerpt", "ref"))


@pytest.fixture
def service(tmp_path: Path):
    db = tmp_path / "app.db"
    initialize_database(db)
    seed_qa(db)
    migration = Migration()
    worker = Worker()
    service = NoteService(NoteRepository(db), migration, QaRepository(db), worker)
    return service, migration, worker


def session(user_id="alice"):
    return SimpleNamespace(user_id=user_id)


def document_selector():
    return DocumentChunkSourceSelector("document_chunk", SearchChunkLocator(
        "00000000-0000-0000-0000-000000000001", "chunk-0", 0, "a" * 64,
    ))


def test_document_source_resolves_outside_lock_and_replays_after_deletion(service):
    facade, _, _ = service
    active = SimpleNamespace(user_id="alice", token="token", runtime=SimpleNamespace(lock=RLock()))
    selector = document_selector()
    calls = []
    def resolve(token, locator):
        assert token == "token"
        calls.append(locator)
        def probe():
            acquired = active.runtime.lock.acquire(blocking=False)
            if acquired:
                active.runtime.lock.release()
            return acquired
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(probe).result(timeout=3)
        return ResolvedDocumentChunk(locator.document_id, "server.pdf", "server excerpt", 2, None, locator)
    facade.document_search = SimpleNamespace(resolve_chunk=resolve)
    args = dict(body_markdown="my edited text", concept=None, tags=(), client_request_id="document", source=selector)
    note = facade.create(active, **args)
    assert note.body_markdown == "my edited text"
    assert note.sources[0].title_snapshot == "server.pdf"
    assert note.sources[0].excerpt_snapshot == "server excerpt"
    with connect(facade.repository.db_path) as conn:
        from app.note_repository import scrub_sources_in_transaction
        scrub_sources_in_transaction(conn, user_id="alice", document_id=selector.locator.document_id, deleted_at="later")
    replay = facade.create(active, **args)
    assert replay.id == note.id and replay.sources[0].deleted
    assert len(calls) == 1
    with pytest.raises(NoteIdempotencyConflict):
        facade.create(active, **(args | {"body_markdown": "changed"}))
    assert len(calls) == 1


@pytest.mark.parametrize("failure,expected", [
    (DocumentSearchSourceStaleError, NoteNotFoundError),
    (DocumentSearchUnavailableError, NoteSourceUnavailableError),
])
def test_document_resolution_error_is_safe_and_does_not_write(service, failure, expected):
    facade, _, _ = service
    def resolve(*args):
        raise failure("private backend details")
    facade.document_search = SimpleNamespace(resolve_chunk=resolve)
    with pytest.raises(expected):
        facade.create(SimpleNamespace(user_id="alice", token="token"), body_markdown="body", concept=None, tags=(), client_request_id="failure", source=document_selector())
    assert facade.repository.list_page("alice").items == ()


@pytest.mark.parametrize("status", ["queued", "running", "failed", "completed"])
def test_document_deletion_between_resolution_and_insert_rejects_source(service, status):
    facade, _, _ = service
    selector = document_selector()
    def resolve(token, locator):
        with connect(facade.repository.db_path) as conn:
            conn.execute("""insert into qa_deletion_fences
                (id,user_id,target_type,target_id,status,stage,created_at,updated_at)
                values ('fence','alice','document',?,?,'fenced','t','t')""", (locator.document_id, status))
        return ResolvedDocumentChunk(locator.document_id, "server.pdf", "source", None, None, locator)
    facade.document_search = SimpleNamespace(resolve_chunk=resolve)
    from app.note_models import NoteSourceDeletingError
    with pytest.raises((NoteSourceDeletingError, NoteNotFoundError)):
        facade.create(SimpleNamespace(user_id="alice", token="token"), body_markdown="body", concept=None, tags=(), client_request_id="race", source=selector)
    assert facade.repository.list_page("alice").items == ()


def test_qa_request_digest_remains_byte_compatible():
    import hashlib
    import json
    from app.note_service import _service_request_digest
    source = NoteSourceSelector("qa_citation", "message", "citation")
    payload = {"body_markdown": "body", "concept": None, "tags": ["tag"], "source": {
        "kind": "qa_citation", "qa_message_id": "message", "citation_id": "citation",
    }}
    expected = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert _service_request_digest("body", None, ("tag",), source) == expected


def test_create_from_citation_uses_server_owned_snapshot(service) -> None:
    facade, migration, worker = service
    note = facade.create(
        session(), body_markdown="study", concept="RAG", tags=("search",),
        client_request_id="req", source=NoteSourceSelector(
            kind="qa_citation", qa_message_id="message-alice", citation_id="citation-alice"
        ),
    )
    assert note.sources[0].title_snapshot == "Document"
    assert note.sources[0].excerpt_snapshot == "excerpt"
    assert note.sources[0].locator == {"page_number": 3, "section": "S"}
    assert migration.users == ["alice"]
    assert worker.notifications == 1


def test_answer_source_and_cross_user_source_resolution(service) -> None:
    facade, _, _ = service
    answer = facade.create(
        session(), body_markdown="answer note", concept=None, tags=(), client_request_id="answer",
        source=NoteSourceSelector(kind="qa_answer", qa_message_id="message-alice"),
    )
    assert answer.sources[0].excerpt_snapshot == "answer body"
    with pytest.raises(NoteNotFoundError):
        facade.create(
            session(), body_markdown="private", concept=None, tags=(), client_request_id="cross",
            source=NoteSourceSelector(kind="qa_citation", qa_message_id="message-bob", citation_id="citation-bob"),
        )


def test_create_replay_checks_idempotency_before_deleted_qa_lookup(service) -> None:
    facade, _, _ = service
    selector = NoteSourceSelector(kind="qa_answer", qa_message_id="message-alice")
    first = facade.create(
        session(), body_markdown="answer note", concept="RAG", tags=("qa",),
        client_request_id="deleted-source-replay", source=selector,
    )
    with connect(facade.repository.db_path) as conn:
        conn.execute("delete from qa_messages where id='message-alice' and user_id='alice'")

    replay = facade.create(
        session(), body_markdown="answer note", concept="RAG", tags=("qa",),
        client_request_id="deleted-source-replay", source=selector,
    )
    assert replay == first
    with pytest.raises(NoteIdempotencyConflict):
        facade.create(
            session(), body_markdown="different", concept="RAG", tags=("qa",),
            client_request_id="deleted-source-replay", source=selector,
        )


def test_service_crud_search_count_recent_clear_and_retry(service) -> None:
    facade, _, _ = service
    first = facade.create(session(), body_markdown="alpha term", concept=None, tags=("x",), client_request_id="1")
    facade.create(session(), body_markdown="beta", concept=None, tags=(), client_request_id="2")
    assert facade.get_note(session(), first.id) == first
    assert facade.search(session(), "alpha").items[0].id == first.id
    assert facade.count(session()) == 2
    assert len(facade.recent(session(), limit=1)) == 1
    updated = facade.update(session(), first.id, expected_version=1, body_markdown="updated", concept=None, tags=())
    facade.delete(session(), updated.id, expected_version=2)
    with pytest.raises(NoteNotFoundError):
        facade.get_note(session(), first.id)
    assert facade.clear_all(session(), confirmation="清空全部笔记") == 1
    with pytest.raises(ValueError, match="confirmation"):
        facade.clear_all(session(), confirmation="wrong")
    assert facade.retry_projection(session()) == 0


def test_worker_notification_failure_does_not_fail_create(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    initialize_database(db)
    with connect(db) as conn:
        conn.execute("insert into users values ('alice','alice','alice','x','active','t','t')")
    facade = NoteService(NoteRepository(db), Migration(), QaRepository(db), Worker(fail=True))
    assert facade.create(session(), body_markdown="saved", concept=None, tags=(), client_request_id="r").body_markdown == "saved"


def test_list_filters_are_passed_as_immutable_value(service) -> None:
    facade, _, _ = service
    facade.create(session(), body_markdown="term", concept=None, tags=("a",), client_request_id="r")
    page = facade.list_notes(session(), NoteFilters(query="term", tags=("a",), limit=20))
    assert len(page.items) == 1


def test_trusted_internal_facade_covers_legacy_operations(service) -> None:
    facade, _, _ = service
    created = facade.create_for_user(
        "alice",
        body_markdown="legacy term",
        concept=None,
        tags=("legacy",),
        client_request_id="legacy-create",
    )
    assert facade.get_for_user("alice", created.id) == created
    assert facade.search_for_user("alice", "legacy").items[0].id == created.id
    assert facade.count_for_user("alice") == 1
    assert facade.recent_for_user("alice", limit=1) == (created,)
    updated = facade.update_for_user(
        "alice",
        created.id,
        expected_version=1,
        body_markdown="legacy updated",
        concept=None,
        tags=(),
    )
    facade.delete_for_user("alice", created.id, expected_version=updated.version)
    assert facade.clear_for_user("alice") == 0
    assert facade.retry_for_user("alice") == 0
