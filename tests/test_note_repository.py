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
