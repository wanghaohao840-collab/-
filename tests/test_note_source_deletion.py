from __future__ import annotations

from types import SimpleNamespace
import threading
from threading import Event, Thread
from threading import RLock

import pytest

from app.database import connect, initialize_database
from app.note_models import NewNoteSource
from app.note_repository import NoteRepository
import app.note_repository as note_repository_module
from app.note_models import NoteSourceDeletingError, NoteSourceSelector
from app.note_service import NoteService
from app.qa_deletion import QaDeletionRepository, QaDeletionWorker
from app.qa_models import QaDocumentCandidate
from app.qa_repository import QaRepository


def _user(db, user_id: str = "alice"):
    with connect(db) as conn:
        conn.execute(
            "insert into users (id, username, username_key, password_hash, created_at, updated_at) values (?, ?, ?, 'hash', 't', 't')",
            (user_id, user_id, user_id),
        )


def test_conversation_fence_scrubs_source_and_enqueues_latest_projection(tmp_path):
    db = tmp_path / "app.db"
    initialize_database(db)
    _user(db)
    qa = QaRepository(db)
    conversation = qa.create_conversation(
        "alice", (QaDocumentCandidate("doc", "doc.md", "alice"),)
    )
    notes = NoteRepository(db)
    note = notes.create(
        "alice", "author content", "concept", ("tag",), "request",
        sources=(NewNoteSource(
            kind="qa_message", qa_thread_id=conversation.id, qa_message_id="message",
            citation_id=None, document_id=None, locator={"page": 3},
            title_snapshot="private title", excerpt_snapshot="private excerpt",
        ),),
    )
    deletions = QaDeletionRepository(db, notes)
    deletion = deletions.create_conversation_deletion("alice", conversation.id)
    runtime_registry = SimpleNamespace()
    worker = QaDeletionWorker(
        deletions, runtime_registry, SimpleNamespace(), SimpleNamespace(),
    )

    assert worker.run_once("worker")
    assert deletions.get_deletion("alice", deletion.id).status == "completed"
    current = notes.get("alice", note.id)
    assert current.body_markdown == "author content"
    assert current.concept == "concept"
    assert current.tags == ("tag",)
    assert current.version == 2
    assert current.sources[0].deleted is True
    assert current.sources[0].qa_message_id is None
    assert current.sources[0].title_snapshot is None
    assert current.sources[0].excerpt_snapshot is None
    with connect(db) as conn:
        tasks = conn.execute(
            "select note_version, operation from note_projection_tasks where user_id=? and note_id=? order by note_version",
            ("alice", note.id),
        ).fetchall()
    assert [(task["note_version"], task["operation"]) for task in tasks] == [(1, "upsert"), (2, "upsert")]


class _Migration:
    def __init__(self):
        self.runtime_registry = SimpleNamespace(get_or_create=lambda _user: SimpleNamespace(lock=RLock()))

    def ensure_user_migrated(self, _user):
        return None


class _Worker:
    def notify(self):
        return None


class _DeletionRuntimeRegistry:
    def __init__(self):
        self.runtime = SimpleNamespace(
            history=SimpleNamespace(),
            memory_tool=SimpleNamespace(memory_manager=SimpleNamespace(remove_memory=lambda *args, **kwargs: True)),
        )
    def acquire_background(self, _user):
        return self.runtime
    def release_background(self, _user):
        return None


class _DeletionDocuments:
    def perform_document_delete(self, *args, **kwargs):
        return None


class _DeletionSessions:
    def clear_document_selection(self, *args, **kwargs):
        return 0


def _seed_completed_answer(db, *, user_id="alice", document_id="doc"):
    qa = QaRepository(db)
    conversation = qa.create_conversation(
        user_id, (QaDocumentCandidate(document_id, "doc.md", user_id),)
    )
    turn = qa.create_pending_turn(user_id, conversation.id, "question", "joint", "request")
    from app.qa_models import QaSourceDraft
    qa.complete_turn(
        user_id, turn.assistant_message.id, 0, "answer", (
            QaSourceDraft("citation", document_id, "doc.md", 4, "section", "excerpt", "ref"),
        ), "available", None,
    )
    return qa, conversation, turn.assistant_message.id


def _source_service(db):
    return NoteService(NoteRepository(db), _Migration(), QaRepository(db), _Worker())


@pytest.mark.parametrize("scope", ["conversation", "document"])
def test_source_create_after_fence_is_distinguished_from_absent_source(tmp_path, scope):
    db = tmp_path / f"{scope}.db"
    initialize_database(db)
    _user(db)
    qa, conversation, message_id = _seed_completed_answer(db)
    deletions = QaDeletionRepository(db, NoteRepository(db))
    target = conversation.id if scope == "conversation" else "doc"
    if scope == "conversation":
        deletions.create_conversation_deletion("alice", target)
        selector = NoteSourceSelector(kind="qa_answer", qa_message_id=message_id)
    else:
        deletions.create_document_deletion("alice", target)
        selector = NoteSourceSelector(kind="qa_citation", qa_message_id=message_id, citation_id="citation")
    service = NoteService(NoteRepository(db), _Migration(), qa, _Worker())
    with pytest.raises(NoteSourceDeletingError):
        service.create(
            SimpleNamespace(user_id="alice"), body_markdown="body", concept="concept",
            tags=("tag",), client_request_id="create", source=selector,
        )


@pytest.mark.parametrize("scope", ["conversation", "document"])
def test_source_read_toctou_rechecks_fence_after_hidden_qa_read(tmp_path, scope, monkeypatch):
    db = tmp_path / f"toctou-{scope}.db"
    initialize_database(db)
    _user(db)
    qa, conversation, message_id = _seed_completed_answer(db)
    deletions = QaDeletionRepository(db)
    target = conversation.id if scope == "conversation" else "doc"
    selector = (
        NoteSourceSelector(kind="qa_answer", qa_message_id=message_id)
        if scope == "conversation"
        else NoteSourceSelector(kind="qa_citation", qa_message_id=message_id, citation_id="citation")
    )
    fence_committed = Event()

    def hidden_qa_read(user_id, source_id):
        state = {}
        def create_fence():
            state["deletion"] = (
                deletions.create_conversation_deletion(user_id, target)
                if scope == "conversation"
                else deletions.create_document_deletion(user_id, target)
            )
            fence_committed.set()
        deleter = Thread(target=create_fence, name="toctou-fence")
        deleter.start()
        assert fence_committed.wait(timeout=3)
        deleter.join(timeout=3)
        return None

    monkeypatch.setattr(qa, "get_message", hidden_qa_read)
    service = NoteService(NoteRepository(db), _Migration(), qa, _Worker())
    with pytest.raises(NoteSourceDeletingError):
        service.create(
            SimpleNamespace(user_id="alice"), body_markdown="body", concept=None,
            tags=(), client_request_id="toctou", source=selector,
        )


@pytest.mark.parametrize("scope", ["conversation", "document"])
def test_source_create_wins_race_then_deletion_scrubs_it(tmp_path, scope, monkeypatch):
    db = tmp_path / f"race-{scope}.db"
    initialize_database(db)
    _user(db)
    _, conversation, message_id = _seed_completed_answer(db)
    deletions = QaDeletionRepository(db, NoteRepository(db))
    selector = (
        NoteSourceSelector(kind="qa_answer", qa_message_id=message_id)
        if scope == "conversation"
        else NoteSourceSelector(kind="qa_citation", qa_message_id=message_id, citation_id="citation")
    )
    service = _source_service(db)
    begun = Event()
    release = Event()
    original_connect = note_repository_module.connect

    class BlockingConnection:
        def __init__(self, connection):
            self.connection = connection
            self.blocked = False
        def execute(self, statement, *args, **kwargs):
            cursor = self.connection.execute(statement, *args, **kwargs)
            if statement.strip().lower() == "begin immediate" and not self.blocked:
                self.blocked = True
                begun.set()
                assert release.wait(timeout=3)
            return cursor
        def __enter__(self):
            self.connection.__enter__()
            return self
        def __exit__(self, *args):
            return self.connection.__exit__(*args)
        def __getattr__(self, name):
            return getattr(self.connection, name)

    def synchronized_connect(path):
        if threading.current_thread().name == "note-create":
            return BlockingConnection(original_connect(path))
        return original_connect(path)

    monkeypatch.setattr(note_repository_module, "connect", synchronized_connect)
    state = {}
    def create():
        try:
            state["note"] = service.create(
                SimpleNamespace(user_id="alice"), body_markdown="body", concept="concept",
                tags=("tag",), client_request_id="create", source=selector,
            )
        except BaseException as exc:
            state["error"] = exc

    creator = Thread(target=create, name="note-create")
    creator.start()
    assert begun.wait(timeout=3)
    target = conversation.id if scope == "conversation" else "doc"
    deletion_state = {}
    def fence():
        try:
            deletion_state["deletion"] = (
                deletions.create_conversation_deletion("alice", target)
                if scope == "conversation"
                else deletions.create_document_deletion("alice", target)
            )
        except BaseException as exc:
            deletion_state["error"] = exc
    deleter = Thread(target=fence, name="deletion-fence")
    deleter.start()
    release.set()
    creator.join(timeout=3)
    deleter.join(timeout=3)
    assert not creator.is_alive()
    assert not deleter.is_alive()
    assert "error" not in state
    assert "error" not in deletion_state
    deletion = deletion_state["deletion"]
    note = state["note"]
    worker = QaDeletionWorker(
        deletions, _DeletionRuntimeRegistry(), _DeletionDocuments(), _DeletionSessions()
    )
    assert worker.run_once("worker")
    current = NoteRepository(db).get("alice", note.id)
    assert current is not None
    assert (current.body_markdown, current.concept, current.tags, current.version) == ("body", "concept", ("tag",), 2)
    source = current.sources[0]
    assert source.deleted and all(value is None for value in (
        source.qa_thread_id, source.qa_message_id, source.citation_id,
        source.document_id, source.locator, source.title_snapshot, source.excerpt_snapshot,
    ))
    with connect(db) as conn:
        tasks = conn.execute(
            "select note_version, operation from note_projection_tasks where user_id=? and note_id=? and note_version=2",
            ("alice", note.id),
        ).fetchall()
    assert len(tasks) == 1 and tasks[0]["operation"] == "upsert"
    assert deletions.get_deletion("alice", deletion.id).status == "completed"


@pytest.mark.parametrize("scope", ["conversation", "document"])
def test_source_scrub_rejects_stale_owner_and_replays_safely(tmp_path, scope):
    db = tmp_path / f"stale-{scope}.db"
    initialize_database(db)
    _user(db)
    _, conversation, message_id = _seed_completed_answer(db)
    service = _source_service(db)
    selector = (
        NoteSourceSelector(kind="qa_answer", qa_message_id=message_id)
        if scope == "conversation"
        else NoteSourceSelector(kind="qa_citation", qa_message_id=message_id, citation_id="citation")
    )
    note = service.create(
        SimpleNamespace(user_id="alice"), body_markdown="body", concept="concept",
        tags=("tag",), client_request_id="create", source=selector,
    )
    notes = NoteRepository(db)
    deletions = QaDeletionRepository(db, notes)
    target = conversation.id if scope == "conversation" else "doc"
    deletion = (
        deletions.create_conversation_deletion("alice", target)
        if scope == "conversation"
        else deletions.create_document_deletion("alice", target)
    )
    claimed = deletions.claim_next_deletion("owner", lease_seconds=1)
    assert claimed is not None
    assert deletions.remove_qa_rows(deletion.id, "stale-owner") is False
    before = notes.get("alice", note.id)
    assert before.sources[0].deleted is False
    expired = "2099-01-01T00:00:00Z"
    assert deletions.remove_qa_rows(deletion.id, "owner", now=expired) is False
    assert deletions.recover_expired(now=expired) == 1
    reclaimed = deletions.claim_next_deletion("new-owner", now=expired, lease_seconds=300)
    assert reclaimed is not None
    assert deletions.remove_qa_rows(deletion.id, "new-owner", now=expired) is True
    assert deletions.remove_qa_rows(deletion.id, "new-owner", now=expired) is False
    after = notes.get("alice", note.id)
    assert after.version == 2 and after.body_markdown == "body"
    assert after.sources[0].deleted is True
