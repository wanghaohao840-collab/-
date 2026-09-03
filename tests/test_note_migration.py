from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from app.database import connect, initialize_database
from app.note_migration import NoteMigrationService
from app.note_repository import NoteRepository
from app.storage import UserStorage


class Registry:
    def __init__(self, runtime):
        self.runtime = runtime

    def get_or_create(self, user_id):
        assert user_id == self.runtime.user_id
        return self.runtime


def test_duplicate_legacy_notes_migrate_once_each_without_json_mutation(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    initialize_database(db)
    with connect(db) as conn:
        conn.execute("insert into users values ('alice','alice','alice','x','active','t','t')")
    storage = UserStorage(tmp_path / "data")
    paths = storage.ensure_user_dirs("alice")
    payload = {
        "documents": [], "questions": [], "sessions": [],
        "notes": [
            {"note": "same", "concept": "x", "session_id": "s", "created_at": "2026-01-01T00:00:00"},
            {"note": "same", "concept": "x", "session_id": "s", "created_at": "2026-01-01T00:00:00"},
        ],
    }
    paths.history.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    before = paths.history.read_bytes()
    runtime = SimpleNamespace(
        user_id="alice",
        lock=__import__("threading").RLock(),
        history=SimpleNamespace(load=lambda: json.loads(paths.history.read_text(encoding="utf-8"))),
        memory_tool=SimpleNamespace(memory_manager=SimpleNamespace(memory_types={})),
    )
    repository = NoteRepository(db)
    migration = NoteMigrationService(db, storage, repository, Registry(runtime))

    first = migration.ensure_user_migrated("alice")
    second = migration.ensure_user_migrated("alice")

    assert first.imported_count == 2
    assert second.imported_count == 0
    assert len(repository.list_page("alice").items) == 2
    assert paths.history.read_bytes() == before


def test_migration_matches_exact_legacy_memory_but_leaves_unmatched(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    initialize_database(db)
    with connect(db) as conn:
        conn.execute("insert into users values ('alice','alice','alice','x','active','t','t')")
    storage = UserStorage(tmp_path / "data")
    storage.ensure_user_dirs("alice")
    record = {"note": "body", "content": "wrapped body", "concept": "c", "session_id": "s", "created_at": "t"}
    memories = {
        "legacy-exact": SimpleNamespace(content="wrapped body", metadata={"knowledge_type": "learning_note", "concept": "c", "session_id": "s"}),
        "legacy-other": SimpleNamespace(content="other", metadata={"knowledge_type": "learning_note", "concept": "c", "session_id": "s"}),
        "note:alice:already-projected": SimpleNamespace(content="body", metadata={"knowledge_type": "learning_note", "note_id": "already-projected"}),
    }
    runtime = SimpleNamespace(
        user_id="alice", lock=__import__("threading").RLock(),
        history=SimpleNamespace(load=lambda: {"notes": [record]}),
        memory_tool=SimpleNamespace(memory_manager=SimpleNamespace(memory_types={"semantic": SimpleNamespace(memories=memories)})),
    )
    migration = NoteMigrationService(db, storage, NoteRepository(db), Registry(runtime))

    result = migration.ensure_user_migrated("alice")

    assert result.matched_legacy_memory_count == 1
    assert result.unmatched_legacy_memory_count == 1
    with connect(db) as conn:
        row = conn.execute("select legacy_memory_id from note_legacy_imports").fetchone()
    assert row["legacy_memory_id"] == "legacy-exact"
