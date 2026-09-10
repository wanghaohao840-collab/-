from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.database import connect, initialize_database
from app.note_models import (
    NewNoteSource,
    NoteIdempotencyConflict,
    NoteNotFoundError,
    NoteValidationError,
    NoteVersionConflict,
)
from app.note_repository import NoteRepository


@pytest.fixture
def repository(tmp_path: Path) -> NoteRepository:
    db_path = tmp_path / "app.db"
    initialize_database(db_path)
    with connect(db_path) as conn:
        for user_id in ("alice", "bob"):
            conn.execute(
                "insert into users (id, username, username_key, password_hash, created_at, updated_at) values (?, ?, ?, 'x', 't', 't')",
                (user_id, user_id, user_id),
            )
    return NoteRepository(db_path)


def test_note_schema_supports_fts_and_composite_ownership(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    initialize_database(db)
    initialize_database(db)
    with connect(db) as conn:
        names = {
            row["name"]
            for row in conn.execute(
                "select name from sqlite_master where type in ('table','index')"
            )
        }
        foreign_keys = conn.execute("pragma foreign_key_list('note_tags')").fetchall()
    assert {
        "notes",
        "note_tags",
        "note_sources",
        "note_projection_tasks",
        "note_legacy_imports",
        "notes_fts",
    } <= names
    assert len(foreign_keys) == 2


def document_source():
    return NewNoteSource(
        kind="document_chunk", qa_thread_id=None, qa_message_id=None, citation_id=None,
        document_id="00000000-0000-0000-0000-000000000001",
        locator={"chunk_id": "chunk-0", "chunk_index": 0, "content_sha256": "a" * 64, "page_number": 2},
        title_snapshot="paper.pdf", excerpt_snapshot="verified excerpt",
    )


def test_document_source_roundtrip_and_source_filter(repository):
    note = repository.create("alice", "edited note", None, (), "document-source", sources=(document_source(),))
    assert note.sources[0].kind == "document_chunk"
    assert note.sources[0].locator["content_sha256"] == "a" * 64
    assert repository.list_page("alice", source_kind="document_chunk").items == (note,)
    assert repository.list_page("bob", source_kind="document_chunk").items == ()
    with connect(repository.db_path) as conn:
        assert conn.execute("select count(*) from note_sources").fetchone()[0] == 0


def test_document_source_schema_is_additive_and_retry_safe(repository):
    old = NewNoteSource("qa_message", "thread", "message", None, None, None, None, "old excerpt")
    note = repository.create("alice", "old note", None, (), "legacy", sources=(old,))
    with connect(repository.db_path) as conn:
        before = tuple(conn.execute("select * from note_sources").fetchone())
        ddl = conn.execute("select sql from sqlite_master where name='note_sources'").fetchone()[0]
        # Simulate an existing database created before this additive table.
        conn.execute("drop table if exists note_document_sources")
    initialize_database(repository.db_path)
    with connect(repository.db_path) as conn:
        conn.execute("drop index ix_note_document_sources_document")
    initialize_database(repository.db_path)
    initialize_database(repository.db_path)
    with connect(repository.db_path) as conn:
        assert tuple(conn.execute("select * from note_sources").fetchone()) == before
        assert conn.execute("select sql from sqlite_master where name='note_sources'").fetchone()[0] == ddl
        assert conn.execute("select id from notes where id=?", (note.id,)).fetchone()[0] == note.id
        assert conn.execute("pragma foreign_key_check").fetchall() == []
        assert len(conn.execute("pragma foreign_key_list('note_document_sources')").fetchall()) == 2


def test_mixed_sources_scrub_once_and_preserve_other_users(repository):
    from app.note_repository import scrub_sources_in_transaction
    doc = document_source()
    qa = NewNoteSource("qa_citation", "thread", "message", "citation", doc.document_id, None, "title", "QA excerpt")
    note = repository.create("alice", "keep my text", None, (), "mixed", sources=(doc, qa))
    bob = repository.create("bob", "bob text", None, (), "bob", sources=(doc,))
    with connect(repository.db_path) as conn:
        assert scrub_sources_in_transaction(conn, user_id="alice", document_id=doc.document_id, deleted_at="later") == 1
        assert scrub_sources_in_transaction(conn, user_id="alice", document_id=doc.document_id, deleted_at="later") == 0
    current = repository.get("alice", note.id)
    assert current.version == 2 and current.body_markdown == "keep my text"
    assert all(s.deleted and s.locator is None and s.excerpt_snapshot is None for s in current.sources)
    assert not repository.get("bob", bob.id).sources[0].deleted
    with connect(repository.db_path) as conn:
        versions = conn.execute("select note_version from note_projection_tasks where user_id='alice' and note_id=? order by note_version", (note.id,)).fetchall()
        row = conn.execute("select * from note_document_sources where user_id='alice'").fetchone()
        assert all(row[key] is None for key in ("document_id", "chunk_id", "chunk_index", "content_sha256", "locator_json", "title_snapshot", "excerpt_snapshot"))
    assert [r[0] for r in versions] == [1, 2]


def test_document_source_composite_fk_and_live_deleted_constraints(repository):
    note = repository.create("alice", "body", None, (), "ownership", sources=(document_source(),))
    with connect(repository.db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("update note_document_sources set user_id='bob' where note_id=?", (note.id,))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("update note_document_sources set excerpt_snapshot=null where note_id=?", (note.id,))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("update note_document_sources set source_deleted_at='later' where note_id=?", (note.id,))


def test_document_source_does_not_copy_snapshot_into_memory_projection(repository):
    from types import SimpleNamespace
    from app.note_projection import DefaultNoteMemoryProjection
    note = repository.create("alice", "user authored text", None, (), "project", sources=(document_source(),))
    calls = []
    manager = SimpleNamespace(add_memory=lambda **kwargs: calls.append(kwargs))
    runtime = SimpleNamespace(memory_tool=SimpleNamespace(memory_manager=manager))
    DefaultNoteMemoryProjection().upsert(runtime, note)
    assert calls[0]["content"] == "user authored text"
    assert "verified excerpt" not in str(calls[0])
    assert calls[0]["metadata"]["version"] == 1


def test_document_sources_keep_ten_source_limit(repository):
    with pytest.raises(NoteValidationError):
        repository.create("alice", "body", None, (), "too-many", sources=(document_source(),) * 11)
    assert repository.list_page("alice").items == ()


def test_conversation_scrub_leaves_document_sources_intact(repository):
    from app.note_repository import scrub_sources_in_transaction
    qa = NewNoteSource("qa_message", "thread", "message", None, None, None, None, "answer")
    note = repository.create("alice", "body", None, (), "thread-only", sources=(document_source(), qa))
    with connect(repository.db_path) as conn:
        assert scrub_sources_in_transaction(conn, user_id="alice", thread_id="thread", deleted_at="later") == 1
    sources = {source.kind: source for source in repository.get("alice", note.id).sources}
    assert sources["qa_message"].deleted
    assert not sources["document_chunk"].deleted
    assert sources["document_chunk"].excerpt_snapshot == "verified excerpt"


def test_create_get_idempotency_and_payload_conflict(repository: NoteRepository) -> None:
    created = repository.create("alice", "body", "Concept", ("Tag",), "request-1")
    duplicate = repository.create("alice", " body ", "Concept", ("Tag",), "request-1")

    assert duplicate == created
    assert repository.get("bob", created.id) is None
    with pytest.raises(NoteIdempotencyConflict):
        repository.create("alice", "different", None, (), "request-1")


def test_update_delete_are_versioned_and_tombstones_are_safe(repository: NoteRepository) -> None:
    note = repository.create("alice", "first", None, (), "request-2")
    updated = repository.update(
        "alice", note.id, expected_version=1, body_markdown="second", concept=None, tags=()
    )
    assert updated.version == 2
    with pytest.raises(NoteVersionConflict):
        repository.update(
            "alice", note.id, expected_version=1, body_markdown="stale", concept=None, tags=()
        )
    with pytest.raises(NoteNotFoundError):
        repository.update(
            "bob", note.id, expected_version=2, body_markdown="private", concept=None, tags=()
        )

    deleted = repository.soft_delete("alice", note.id, expected_version=2)
    assert deleted.deleted_at is not None and deleted.version == 3
    assert repository.get("alice", note.id) is None
    with pytest.raises(NoteNotFoundError):
        repository.soft_delete("alice", note.id, expected_version=3)


def test_fts_search_tag_intersection_and_tombstone_scope(repository: NoteRepository) -> None:
    selected = repository.create(
        "alice", "量子纠缠 reference", "物理", ("论文", "重点"), "search-1"
    )
    repository.create("alice", "量子力学", "物理", ("论文",), "search-2")
    repository.create("bob", "量子纠缠", "私有", ("论文", "重点"), "search-3")

    result = repository.list_page("alice", query="量子", tags=("论文", "重点"))
    assert [item.id for item in result.items] == [selected.id]
    repository.soft_delete("alice", selected.id, expected_version=1)
    assert repository.list_page("alice", query="量子", tags=("论文", "重点")).items == ()


def test_paging_defaults_to_20_without_overlap_and_ties_are_stable(repository: NoteRepository) -> None:
    for index in range(55):
        repository.create(
            "alice", f"note {index}", None, (), f"page-{index}", now="2026-01-01T00:00:00Z"
        )

    first = repository.list_page("alice")
    second = repository.list_page("alice", cursor=first.next_cursor)
    assert len(first.items) == len(second.items) == 20
    assert not ({note.id for note in first.items} & {note.id for note in second.items})
    assert [note.id for note in first.items] == sorted((note.id for note in first.items), reverse=True)
    with pytest.raises(NoteValidationError):
        repository.list_page("alice", cursor="not-a-cursor")
    with pytest.raises(NoteValidationError):
        repository.list_page("alice", limit=51)


def test_two_connections_allow_only_one_expected_version_update(repository: NoteRepository) -> None:
    note = repository.create("alice", "base", None, (), "concurrent-1")

    def update(body: str) -> str:
        try:
            return repository.update(
                "alice", note.id, expected_version=1, body_markdown=body, concept=None, tags=()
            ).body_markdown
        except NoteVersionConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, ("left", "right")))
    assert results.count("conflict") == 1


def test_sources_are_stored_server_snapshot_only(repository: NoteRepository) -> None:
    source = NewNoteSource(
        kind="qa_citation",
        qa_thread_id="thread",
        qa_message_id="message",
        citation_id="citation",
        document_id="document",
        locator={"page_number": 3},
        title_snapshot="Doc",
        excerpt_snapshot="excerpt",
    )
    note = repository.create("alice", "source note", None, (), "source-1", sources=(source,))
    assert note.sources[0].qa_message_id == "message"
    assert note.sources[0].locator == {"page_number": 3}


def test_clear_all_only_tombstones_current_user(repository: NoteRepository) -> None:
    repository.create("alice", "one", None, (), "clear-1")
    repository.create("alice", "two", None, (), "clear-2")
    bob = repository.create("bob", "keep", None, (), "clear-3")
    assert repository.clear_all("alice") == 2
    assert repository.list_page("alice").items == ()
    assert repository.get("bob", bob.id) == bob
