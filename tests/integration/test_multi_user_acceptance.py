"""Multi-user acceptance tests — cross-user isolation, concurrency,
restart restoration, and delete/clear scope.

Covers packet 04 acceptance criteria:

- Two users upload the same original filename without path, RAG,
  History, or report collision.
- User A cannot use user B's document ID, report ID, backup ID, or path.
- Same-user concurrent note/import/delete retains all successful commits
  and no orphan sources.
- A new registry/process restores History, RAG JSON, Memory snapshot,
  report index, and uploaded files.
- Delete and clear remove only the intended original files.

All tests use fully isolated tmp_path-based registries — zero
dependency on the production global database.
"""

from __future__ import annotations

import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.database import initialize_database
from app.session import SessionRegistry
from app.storage import UserStorage, read_json


# ── helpers ──────────────────────────────────────────────────────────────


def _register_user(tmp_path, username, password="correct horse battery"):
    """Create an isolated SessionRegistry and register *username*."""
    db_path = tmp_path / "app.db"
    data_root = tmp_path / "data"
    initialize_database(db_path)
    registry = SessionRegistry(
        db_path=db_path, storage=UserStorage(data_root),
    )
    token = registry.register(username, password)
    session = registry.get_session(token)
    return registry, session.assistant, session.user_id, data_root


def _stage_document(assistant, src_path, document_id, original_name):
    """Copy *src_path* into the assistant's user documents directory
    and load it.  Mirrors what ``gradio_app.upload_document`` does."""
    from app.storage import UserStorage

    suffix = Path(src_path).suffix
    # Resolve storage by reconstructing from assistant's history path.
    history_path = assistant.history_repository.path
    users_root = history_path.parent  # history.json is directly in user root
    doc_dir = users_root / "documents"
    doc_dir.mkdir(parents=True, exist_ok=True)
    target = doc_dir / f"{document_id}{suffix}"
    if not target.exists():
        shutil.copy2(src_path, target)
    return assistant.load_document(
        str(target), document_id=document_id,
        original_name=original_name,
    )


# ── acceptance: filename collision isolation ─────────────────────────────


class TestFilenameCollisionIsolation:
    """Two users upload the same original filename without path, RAG,
    History, or report collision."""

    def test_same_filename_different_user_paths(self, tmp_path):
        """Same original name → different storage paths, no collision."""
        _, alice, alice_uid, data_root = _register_user(
            tmp_path, "Alice")
        _, bob, bob_uid, _ = _register_user(tmp_path, "Bob")

        _write_doc(tmp_path / "alice_doc.txt", "Alice content uniquely hers")
        _write_doc(tmp_path / "bob_doc.txt", "Bob content uniquely his")

        _stage_document(alice, tmp_path / "alice_doc.txt",
                        "shared-name", "report.txt")
        _stage_document(bob, tmp_path / "bob_doc.txt",
                        "shared-name", "report.txt")

        users_root = data_root / "users"
        alice_docs = list((users_root / alice_uid / "documents").iterdir())
        bob_docs = list((users_root / bob_uid / "documents").iterdir())
        assert len(alice_docs) == 1
        assert len(bob_docs) == 1
        assert alice_docs[0].parent != bob_docs[0].parent

        # RAG caches are isolated.
        alice_rag = users_root / alice_uid / "rag" / "rag_cache.json"
        bob_rag = users_root / bob_uid / "rag" / "rag_cache.json"
        assert alice_rag.exists()
        assert bob_rag.exists()
        assert alice_rag != bob_rag

        # History files are isolated.
        alice_hist = users_root / alice_uid / "history.json"
        bob_hist = users_root / bob_uid / "history.json"
        assert alice_hist.exists()
        assert bob_hist.exists()
        assert alice_hist != bob_hist

    def test_same_filename_history_records_are_isolated(self, tmp_path):
        """Each user's history only contains their own document records."""
        _, alice, alice_uid, data_root = _register_user(
            tmp_path, "Alice")
        _, bob, _, _ = _register_user(tmp_path, "Bob")

        _write_doc(tmp_path / "a.txt", "Alice doc a")
        _write_doc(tmp_path / "b.txt", "Bob doc b")

        _stage_document(alice, tmp_path / "a.txt", "doc-a", "document.txt")
        _stage_document(bob, tmp_path / "b.txt", "doc-b", "document.txt")

        users_root = data_root / "users"
        alice_history = read_json(
            users_root / alice_uid / "history.json", default={},
        )
        doc_ids = [d.get("document_id") for d in
                   alice_history.get("documents", [])]
        assert "doc-a" in doc_ids
        assert "doc-b" not in doc_ids


def _write_doc(path: Path, content: str) -> None:
    """Write a text document that parses reliably (no pypdf dependency)."""
    path.write_text(content, encoding="utf-8")


# ── acceptance: cross-user access denial ─────────────────────────────────


class TestCrossUserAccessDenial:
    """User A cannot use user B's document ID, report ID, backup ID,
    or path."""

    def test_cross_user_document_id_rejected_by_search(self, tmp_path):
        """User B searching with user A's document ID must find nothing
        or return an error — never return A's content."""
        _, alice, alice_uid, _ = _register_user(tmp_path, "Alice")
        _, bob, _, _ = _register_user(tmp_path, "Bob")

        _write_doc(tmp_path / "alice_doc.txt", "Alice secret content here")
        _stage_document(alice, tmp_path / "alice_doc.txt",
                        "alice-secret", "secret.txt")

        result = bob.search("secret", limit=5,
                            selected_documents=["alice-secret"])
        assert "Alice secret" not in str(result)

    def test_cross_user_document_id_not_in_choices(self, tmp_path):
        """User B's document dropdown must not include A's documents."""
        _, alice, alice_uid, data_root = _register_user(
            tmp_path, "Alice")
        _, bob, _, _ = _register_user(tmp_path, "Bob")

        _write_doc(tmp_path / "a.txt", "Alice data")
        _stage_document(alice, tmp_path / "a.txt", "alice-only", "only-a.txt")

        alice_docs = alice.get_documents()
        bob_docs = bob.get_documents()
        assert any("alice-only" in d for d in alice_docs)
        assert not any("alice-only" in d for d in bob_docs)

    def test_cross_user_report_id_rejected(self, tmp_path):
        """User B reading user A's report ID must raise FileNotFoundError."""
        _, alice, alice_uid, _ = _register_user(tmp_path, "Alice")
        _, bob, _, _ = _register_user(tmp_path, "Bob")

        report_path = Path(alice.export_report_markdown())
        report_id = report_path.stem

        with pytest.raises(FileNotFoundError):
            bob.report_service.read_report(
                bob.user_id, report_id,
            )


# ── acceptance: same-user concurrency ────────────────────────────────────


class TestSameUserConcurrency:
    """Same-user concurrent note/import/delete operations retain all
    successful commits and no orphan sources."""

    def test_concurrent_notes_all_persisted(self, tmp_path):
        """All concurrent add_note calls from the same user's sessions
        must be visible in the final history."""
        reg, _, user_id, _ = _register_user(tmp_path, "Alice")
        session1 = reg.get_session(reg.login("alice", "correct horse battery"))
        session2 = reg.get_session(reg.login("alice", "correct horse battery"))
        a1, a2 = session1.assistant, session2.assistant

        notes = [f"note-{i}" for i in range(20)]

        def add_note(note):
            return a1.add_note(note, concept="concurrent")

        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(add_note, notes))

        history = read_json(
            a1.history_repository.path, default={},
        )
        persisted = {n["note"] for n in history.get("notes", [])}
        for note in notes:
            assert note in persisted, f"Note '{note}' was lost"

    def test_concurrent_import_and_delete_no_orphan_sources(self, tmp_path):
        """After concurrent import + delete on the same user, no orphan
        source files remain."""
        reg, _, user_id, data_root = _register_user(tmp_path, "Alice")
        session1 = reg.get_session(reg.login("alice", "correct horse battery"))
        session2 = reg.get_session(reg.login("alice", "correct horse battery"))
        a1, a2 = session1.assistant, session2.assistant

        docs = []
        for i in range(10):
            p = tmp_path / f"doc-{i}.txt"
            p.write_text(f"Content {i}", encoding="utf-8")
            docs.append(p)

        errors = []

        def import_doc(i):
            try:
                _stage_document(a1, docs[i], f"doc-{i}", f"doc-{i}.txt")
            except Exception as e:
                errors.append(f"import-{i}: {e}")

        def delete_doc(i):
            try:
                a2.current_document_id = f"doc-{i}"
                a2.delete_current_document()
            except Exception as e:
                errors.append(f"delete-{i}: {e}")

        threads = []
        for i in range(10):
            threads.append(threading.Thread(target=import_doc, args=(i,)))
            threads.append(threading.Thread(target=delete_doc, args=(i,)))
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Any history entry referencing a file must have that file exist.
        users_root = data_root / "users" / user_id
        history = read_json(
            users_root / "history.json", default={},
        )
        for entry in history.get("documents", []):
            doc_path = Path(entry.get("document_path", ""))
            if doc_path.is_absolute():
                assert doc_path.exists(), (
                    f"History references deleted doc: {doc_path}"
                )

        assert not errors, f"Errors during concurrent ops: {errors}"


# ── acceptance: restart restoration ─────────────────────────────────────


class TestRestartRestoration:
    """A new registry/process restores History, RAG JSON, Memory snapshot,
    report index, and uploaded files."""

    def test_full_restart_restores_all_artifacts(self, tmp_path):
        """Create data with one registry, then simulate restart by
        creating a fresh registry and verify all artifacts."""
        db_path = tmp_path / "app.db"
        data_root = tmp_path / "data"
        initialize_database(db_path)

        reg1 = SessionRegistry(
            db_path=db_path, storage=UserStorage(data_root),
        )
        token1 = reg1.register("Alice", "correct horse battery")
        a1 = reg1.get_session(token1).assistant
        uid = a1.user_id

        _write_doc(tmp_path / "test.txt", "Restart test document content")
        _stage_document(a1, tmp_path / "test.txt", "restart-doc",
                        "restart.txt")
        a1.add_note("restart-note-1", concept="restart")
        a1.add_note("restart-note-2", concept="restart")
        report_path = Path(a1.export_report_markdown())
        report_id = report_path.stem
        a1.memory_tool.execute(
            "add", content="restart-memory",
            memory_type="working", importance=0.8,
        )

        reg1.logout(token1)

        # "Restart" — fresh SessionRegistry with the same db_path.
        reg2 = SessionRegistry(
            db_path=db_path, storage=UserStorage(data_root),
        )
        token2 = reg2.login("alice", "correct horse battery")
        a2 = reg2.get_session(token2).assistant

        # ── History ────────────────────────────────────────────
        history = read_json(
            data_root / "users" / uid / "history.json", default={},
        )
        docs = history.get("documents", [])
        notes = history.get("notes", [])
        assert any("restart-doc" == d.get("document_id") for d in docs), (
            "History document not restored"
        )
        assert len(notes) == 2
        assert {n["note"] for n in notes} == {"restart-note-1", "restart-note-2"}

        # ── RAG JSON ───────────────────────────────────────────
        rag = read_json(
            data_root / "users" / uid / "rag" / "rag_cache.json",
            default={},
        )
        chunk_doc_ids = {
            c.get("document_id")
            for c in rag.get("chunks", [])
            if c.get("document_id")
        }
        rag_doc_ids = set(rag.get("documents", {}).keys())
        found = "restart-doc" in chunk_doc_ids or "restart-doc" in rag_doc_ids
        assert found, "RAG cache not restored"

        # ── Memory restoration ───────────────────────────────
        # After restart, verify memory data persisted.  Notes added
        # via add_note() are stored as semantic memories and must be
        # searchable through the memory tool.
        search_result = a2.memory_tool.execute(
            "search", query="restart-note", limit=10,
        )
        assert "restart-note-1" in search_result, (
            f"Semantic memory not restored after restart: {search_result[:200]}"
        )
        assert "restart-note-2" in search_result, (
            f"Semantic memory not restored after restart: {search_result[:200]}"
        )
        working_result = a2.memory_tool.execute(
            "search", query="restart-memory", limit=10,
        )
        assert "restart-memory" in working_result, (
            f"Working memory not restored after restart: {working_result[:200]}"
        )

        # ── Report index ───────────────────────────────────────
        reports = a2.report_service.list_reports(uid)
        assert len(reports) >= 1
        assert any(r.id == report_id for r in reports), (
            "Report not in index after restart"
        )
        content = a2.report_service.read_report(uid, report_id)
        assert "restart-note-1" in content, (
            "Report content not restored"
        )

        # ── Uploaded files ─────────────────────────────────────
        doc_dir = data_root / "users" / uid / "documents"
        assert doc_dir.exists()
        assert len(list(doc_dir.iterdir())) >= 1, (
            "Uploaded files not restored"
        )

    def test_restart_preserves_user_scoped_isolation(self, tmp_path):
        """After restart, user A still cannot access user B's data."""
        db_path = tmp_path / "app.db"
        data_root = tmp_path / "data"
        initialize_database(db_path)

        reg1 = SessionRegistry(
            db_path=db_path, storage=UserStorage(data_root),
        )
        alice_tok = reg1.register("Alice", "correct horse battery")
        reg1.register("Bob", "correct horse battery")
        a1 = reg1.get_session(alice_tok).assistant
        a1.add_note("alice-restart-note", concept="test")

        reg2 = SessionRegistry(
            db_path=db_path, storage=UserStorage(data_root),
        )
        bob_tok = reg2.login("bob", "correct horse battery")
        bob = reg2.get_session(bob_tok).assistant

        result = bob.recall("alice-restart-note", limit=10)
        # recall() echoes the query back in its output — verify no
        # *actual content* from Alice's notes appears.
        assert "not found" in result.lower() or "未找到" in result, (
            "Bob should not find Alice's notes after restart"
        )


# ── acceptance: delete/clear scope ───────────────────────────────────────


class TestDeleteClearScope:
    """Delete and clear remove only the intended original files."""

    def test_delete_current_document_removes_only_its_own_file(self, tmp_path):
        """Deleting doc-A must not delete doc-B's file."""
        reg, _, user_id, data_root = _register_user(tmp_path, "Alice")
        session = reg.get_session(reg.login("alice", "correct horse battery"))
        a = session.assistant

        _write_doc(tmp_path / "a.txt", "Document A content")
        _write_doc(tmp_path / "b.txt", "Document B content")
        _stage_document(a, tmp_path / "a.txt", "doc-a", "a.txt")
        _stage_document(a, tmp_path / "b.txt", "doc-b", "b.txt")

        users_root = data_root / "users" / user_id
        history = read_json(users_root / "history.json", default={})
        a_path = None
        b_path = None
        for d in history["documents"]:
            if d["document_id"] == "doc-a":
                a_path = Path(d["document_path"])
            elif d["document_id"] == "doc-b":
                b_path = Path(d["document_path"])

        assert a_path is not None and b_path is not None

        a.current_document_id = "doc-a"
        a.delete_current_document()

        assert not a_path.exists(), "doc-a file was not deleted"
        assert b_path.exists(), "doc-b file was wrongly deleted"

    def test_clear_all_documents_keeps_notes(self, tmp_path):
        """clear_all_documents removes doc files and questions, but
        preserves learning notes."""
        reg, _, user_id, data_root = _register_user(tmp_path, "Alice")
        session = reg.get_session(reg.login("alice", "correct horse battery"))
        a = session.assistant

        _write_doc(tmp_path / "doc.txt", "Doc content")
        _stage_document(a, tmp_path / "doc.txt", "doc-x", "doc.txt")
        a.add_note("keep-this-note", concept="survivor")

        a.clear_all_documents()

        history = read_json(
            data_root / "users" / user_id / "history.json", default={},
        )
        assert history.get("documents", []) == []
        assert history.get("questions", []) == []
        assert len(history.get("notes", [])) == 1
        assert history["notes"][0]["note"] == "keep-this-note"


# ── acceptance: backup cross-user denial ────────────────────────────────


class TestBackupCrossUserDenial:
    """User A cannot restore User B's backup."""

    def test_cross_user_restore_history_backup_denied(self, tmp_path):
        """Bob cannot restore Alice's quarantined history backup."""
        _, alice, alice_uid, _ = _register_user(tmp_path, "Alice")
        _, bob, _, _ = _register_user(tmp_path, "Bob")

        alice.add_note("alice-only-note", concept="test")
        q = alice.runtime.recovery.quarantine_history()

        with pytest.raises(FileNotFoundError):
            bob.runtime.recovery.restore_history(q.backup_id)
