from __future__ import annotations

from types import SimpleNamespace

from app.database import connect, initialize_database
from app.note_models import NewNoteSource
from app.note_repository import NoteRepository
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
