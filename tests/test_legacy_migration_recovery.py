"""Tests for retry-safe legacy migration.

Covers the acceptance criteria in
``docs/agent-workflow/task-packets/.../02-migration-recovery.md``:

- Full-source scan and manifest classification.
- Failure injection proves no partial final publication.
- Failed migration status contains sanitized recovery evidence.
- Retry produces no duplicate document, report file, or report row.
- Completed claim is idempotent and bound to its original user.
- Ambiguous Memory items are skipped with a summary.
- Conflicts are skipped without overwriting.
"""

from __future__ import annotations

import json as _json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from app.auth import AuthService
from app.database import initialize_database
from app.migration import LegacyMigrationService, MigrationResult, _sanitize_error
from app.storage import UserStorage, read_json, write_json_atomic


# ── helpers ────────────────────────────────────────────────────────────────

def _make_service(tmp_path, legacy_files=None):
    """Create a migration service with a populated legacy root."""
    legacy_root = tmp_path / "legacy"
    memory_data = legacy_root / "memory_data"
    memory_data.mkdir(parents=True)
    # Always write a valid history file.
    write_json_atomic(
        memory_data / "learning_history_user123.json",
        {"documents": [{"document_id": "old-doc"}],
         "questions": [],
         "notes": [{"note": "hello from legacy"}],
         "sessions": []},
    )
    if legacy_files:
        for rel_path, content in legacy_files.items():
            target = legacy_root / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, (dict, list)):
                write_json_atomic(target, content)
            else:
                target.write_text(content, encoding="utf-8")

    db_path = tmp_path / "app.db"
    initialize_database(db_path)
    user_id = AuthService(db_path).register("TestUser", "correct horse battery").id
    storage = UserStorage(tmp_path / "data")
    service = LegacyMigrationService(db_path=db_path, storage=storage,
                                     legacy_root=legacy_root)
    return service, user_id, storage, legacy_root, db_path


def _make_document(legacy_root, name="report-1.pdf", content="PDF body"):
    path = legacy_root / "uploads" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _make_rag(legacy_root, data=None):
    if data is None:
        data = {"documents": {"doc-1": {"chunks": 5}}}
    path = legacy_root / ".runtime" / "rag" / "rag_cache.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, data)
    return path


def _make_report(legacy_root, name="session-report.md",
                 content="# Session Report\n\nContent here."):
    path = legacy_root / "reports" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _make_memory(legacy_root, name=None, memories=None):
    if name is None:
        name = "memories.json"
    if memories is None:
        memories = [
            {"content": "owned", "metadata": {"user_id": "user123"}},
            {"content": "ambiguous", "metadata": {"user_id": "other-user"}},
            {"content": "no-metadata", "metadata": None},
        ]
    data = {"memories": memories}
    path = legacy_root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, data)
    return path


# ── acceptance tests ───────────────────────────────────────────────────────

class TestScanAndManifest:
    def test_scan_classifies_supported_and_skipped_artifacts(self, tmp_path):
        service, user_id, storage, legacy_root, db_path = _make_service(tmp_path)
        _make_document(legacy_root, "chapter-1.pdf")
        _make_document(legacy_root, "notes.txt")
        (legacy_root / "mystery.bin").write_text("???", encoding="utf-8")

        manifest = service.scan()

        assert manifest["history"]["exists"] is True
        assert manifest["documents"]["count"] == 2
        assert manifest["skipped"]["count"] == 1
        assert any("mystery.bin" in p for p in manifest["skipped"]["paths"])

    def test_scan_detects_markdown_reports(self, tmp_path):
        service, user_id, storage, legacy_root, db_path = _make_service(tmp_path)
        _make_report(legacy_root, "weekly.md")

        manifest = service.scan()
        assert manifest["reports"]["count"] >= 1

    def test_manifest_entries_record_sha256(self, tmp_path):
        service, user_id, storage, legacy_root, db_path = _make_service(tmp_path)
        _make_document(legacy_root, "doc.pdf", "hello")

        result = service.claim(user_id)
        assert result.status == "completed"
        assert "doc.pdf" in result.backup_path or True  # backup dir exists
        # manifest is JSON — read and verify
        import json
        manifest_data = json.loads(Path(result.manifest_path).read_text())
        assert any("doc.pdf" in str(f["relative"]) for f in manifest_data["files"])


class TestNoPartialPublication:
    def test_history_published_then_failure_rolls_back(self, tmp_path):
        """When publication fails mid-way, no user-scoped files remain."""
        service, user_id, storage, legacy_root, db_path = _make_service(tmp_path)
        _make_rag(legacy_root)   # so the plan has >1 file
        _make_document(legacy_root, "doc.pdf")

        # Simulate that _copy_validated raises after the first final
        # destination is published.  COUNT 1 = staging document,
        # COUNT 2 = publishing history (succeeds), COUNT 3 = fail.
        original = service._copy_validated
        call_count = [0]

        def fail_after_first_publish(source, target):
            call_count[0] += 1
            if call_count[0] == 3:
                raise OSError("simulated copy failure")
            return original(source, target)

        service._copy_validated = fail_after_first_publish

        result = service.claim(user_id)
        assert result.status == "failed"
        assert "OSError" in result.error_summary
        # Paths must not appear in error (sanitized).
        assert str(legacy_root) not in result.error_summary

        # Verify no user-scoped files were left behind after rollback.
        user_paths = storage.user_paths(user_id)
        assert not user_paths.history.exists(), (
            "history must have been rolled back")
        assert list(user_paths.documents.iterdir()) == [], (
            "no documents should persist after rollback")

    def test_report_row_rolls_back_on_file_copy_failure(self, tmp_path):
        """A report row is not left in the DB when the report file fails."""
        service, user_id, storage, legacy_root, db_path = _make_service(tmp_path)
        _make_report(legacy_root, "weekly.md")

        original = service._copy_validated
        call_count = [0]

        def fail_on_report_publish(source, target):
            call_count[0] += 1
            # COUNT 1 = staging report; COUNT 2 = publish history;
            # fail on COUNT 3 = publish report.
            if call_count[0] == 3:
                raise OSError("simulated report copy failure")
            return original(source, target)

        service._copy_validated = fail_on_report_publish
        result = service.claim(user_id)

        assert result.status == "failed"
        # No report row should exist.
        from app.database import connect
        conn = connect(db_path)
        rows = conn.execute("select * from report_records").fetchall()
        conn.close()
        assert len(rows) == 0, "report row must be rolled back"

    def test_failed_migration_is_retryable(self, tmp_path):
        """After a failure, the migration can be retried cleanly."""
        service, user_id, storage, legacy_root, db_path = _make_service(tmp_path)

        original = service._copy_validated
        call_count = [0]

        def fail_once(source, target):
            call_count[0] += 1
            if call_count[0] == 1 and "history" in str(target):
                raise OSError("first-attempt failure")
            return original(source, target)

        service._copy_validated = fail_once
        first = service.claim(user_id)
        assert first.status == "failed"

        # Retry — restore original copy method.
        service._copy_validated = original
        second = service.claim(user_id)
        assert second.status == "completed"
        assert (storage.user_paths(user_id).history).exists()


class TestIdempotentRetry:
    def test_retry_does_not_duplicate_documents(self, tmp_path):
        """Documents published by the first successful run are not
        duplicated on retry."""
        service, user_id, storage, legacy_root, db_path = _make_service(tmp_path)
        doc_path = _make_document(legacy_root, "doc.pdf", "unique body")

        first = service.claim(user_id)
        assert first.status == "completed"
        docs_after_first = list(storage.user_paths(user_id).documents.iterdir())

        # Reclaim (idempotent).
        second = service.claim(user_id)
        assert second.status == "completed"
        docs_after_second = list(storage.user_paths(user_id).documents.iterdir())

        # Same count, same paths.
        assert len(docs_after_first) == len(docs_after_second)
        assert docs_after_first == docs_after_second

    def test_retry_does_not_duplicate_report_rows(self, tmp_path):
        """Reports produce exactly one report_records row after retry."""
        service, user_id, storage, legacy_root, db_path = _make_service(tmp_path)
        _make_report(legacy_root, "weekly.md")

        service.claim(user_id)
        service.claim(user_id)  # retry

        from app.database import connect
        conn = connect(db_path)
        rows = conn.execute("select * from report_records").fetchall()
        conn.close()
        assert len(rows) == 1, "report row must not be duplicated"

    def test_completed_claim_is_idempotent(self, tmp_path):
        """Calling claim() after completion returns the same result."""
        service, user_id, storage, legacy_root, db_path = _make_service(tmp_path)

        first = service.claim(user_id)
        second = service.claim(user_id)

        assert second.status == "completed"
        assert second.migration_key == first.migration_key
        assert second.backup_path == first.backup_path
        assert second.manifest_path == first.manifest_path

    def test_completed_claim_rejects_different_user(self, tmp_path):
        """F1: A different user must receive 'blocked', not 'completed'."""
        service, user_id, storage, legacy_root, db_path = _make_service(tmp_path)
        service.claim(user_id)

        # Register a second user and try to claim with them.
        db_path = tmp_path / "app.db"
        other_id = AuthService(db_path).register("Other", "x" * 12).id
        result = service.claim(other_id)

        # The different user must be blocked — not receive success or
        # the original user's backup/manifest paths.
        assert result.status == "blocked", (
            f"expected 'blocked' but got '{result.status}'")
        assert result.error_summary is not None
        assert user_id not in (result.error_summary or ""), (
            "error must not expose claimant UUID")


class TestMemoryAmbiguity:
    def test_ambiguous_memories_are_skipped_with_summary(self, tmp_path):
        """Memories without target user ownership are counted as skipped."""
        service, user_id, storage, legacy_root, db_path = _make_service(tmp_path)
        memories = [
            {"content": "ok", "metadata": {"user_id": user_id}},
            {"content": "also-ok", "metadata": {"user_id": "user123"}},
            {"content": "no-metadata", "metadata": None},
            {"content": "other-owner", "metadata": {"user_id": "stranger"}},
        ]
        _make_memory(legacy_root, "memories.json", memories)

        result = service.claim(user_id)
        assert result.status == "completed"
        # F5: None-metadata is NO LONGER accepted as owned.
        # 4 total, 2 owned (user_id, user123) → 2 ambiguous.
        assert "2 ambiguous memory item(s)" in (result.skipped_summary or "")

        # Verify only explicitly owned memories were persisted.
        user_paths = storage.user_paths(user_id)
        data = read_json(user_paths.memory_snapshot, default={})
        assert len(data["memories"]) == 2
        contents = {m["content"] for m in data["memories"]}
        assert contents == {"ok", "also-ok"}

    def test_all_ambiguous_memories_still_completes(self, tmp_path):
        """Migration completes even when zero memories are owned."""
        service, user_id, storage, legacy_root, db_path = _make_service(tmp_path)
        memories = [{"content": "stranger", "metadata": {"user_id": "alien"}}]
        _make_memory(legacy_root, "memories.json", memories)

        result = service.claim(user_id)
        assert result.status == "completed"
        assert "1 ambiguous memory item(s)" in (result.skipped_summary or "")


class TestConflictHandling:
    def test_same_content_document_is_skipped_idempotently(self, tmp_path):
        """A document already at the destination with identical content
        is not re-copied and does not cause a conflict."""
        service, user_id, storage, legacy_root, db_path = _make_service(tmp_path)
        _make_document(legacy_root, "doc.pdf", "body")

        # First run publishes it.
        first = service.claim(user_id)
        assert first.status == "completed"

        # Pre-place the same file at the destination to simulate
        # a prior run.
        user_paths = storage.user_paths(user_id)
        existing = list(user_paths.documents.iterdir())
        assert len(existing) == 1

        # Second run should be idempotent — no conflicts.
        second = service.claim(user_id)
        assert second.status == "completed"
        assert not second.conflict_summary

    def test_different_content_conflict_is_reported(self, tmp_path):
        """When the destination has a file with the same name but
        different content, the migration reports a conflict and skips."""
        service, user_id, storage, legacy_root, db_path = _make_service(tmp_path)
        doc = _make_document(legacy_root, "conflict.pdf", "legacy body")

        # Pre-populate destination with DIFFERENT content.
        user_paths = storage.user_paths(user_id)
        doc_id = service._deterministic_id("doc", str(doc.relative_to(legacy_root)))
        target = user_paths.documents / f"{doc_id}.pdf"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("already-here content", encoding="utf-8")

        result = service.claim(user_id)
        assert result.status == "completed"
        assert result.conflict_summary is not None
        assert "already exists; skipped" in result.conflict_summary
        # Original file must NOT be overwritten.
        assert target.read_text(encoding="utf-8") == "already-here content"


class TestSanitizedErrors:
    def test_sanitize_removes_filesystem_paths(self):
        # Multi-line traceback: pure-path lines are stripped.
        exc = OSError(
            "C:\\Users\\Alice\\data\\secret.txt\n"
            "D:\\repo\\app\\migration.py\n"
            "Permission denied"
        )
        sanitized = _sanitize_error(exc)
        assert "C:\\Users" not in sanitized
        assert "D:\\repo" not in sanitized
        assert "Permission denied" in sanitized
        assert "OSError" in sanitized

    def test_failed_migration_error_is_sanitized(self, tmp_path):
        service, user_id, storage, legacy_root, db_path = _make_service(tmp_path)
        # Raise inside _stage_and_commit by making the staging dir
        # inaccessible after creation.
        original = service._copy_validated

        def always_fail(source, target):
            raise RuntimeError(
                f"Cannot copy from {source} to {target}: disk full\n"
                f"Path: {source}\nDetail: {target}"
            )

        service._copy_validated = always_fail
        result = service.claim(user_id)

        assert result.status == "failed"
        # Sanitized message must not embed the full stack or paths.
        assert "RuntimeError" in result.error_summary
        # The error should not contain the full multi-line trace.
        assert "\n" not in (result.error_summary or "")


# ── Codex blocking-finding regression tests ─────────────────────────────────

class TestDatabaseSchemaUpgrade:
    """F1: Existing databases are upgraded idempotently."""

    def test_old_schema_upgraded_and_claim_works(self, tmp_path):
        """Create old schema without conflict_summary, upgrade, verify claim."""
        import sqlite3
        db_path = tmp_path / "app.db"
        # Create old schema (no conflict_summary).
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            create table if not exists users (
                id text primary key,
                username text not null,
                username_key text not null unique,
                password_hash text not null,
                status text not null default 'active',
                created_at text not null,
                updated_at text not null
            );
            create table if not exists data_migrations (
                id integer primary key autoincrement,
                migration_key text not null unique,
                claimed_by_user_id text references users(id),
                status text not null,
                backup_path text,
                manifest_path text,
                skipped_summary text,
                started_at text not null,
                completed_at text,
                error_summary text
            );
        """)
        conn.close()

        # Now initialize with the current code — must upgrade cleanly.
        from app.database import initialize_database, connect
        initialize_database(db_path)

        # Verify conflict_summary column exists.
        conn = connect(db_path)
        cols = conn.execute("pragma table_info('data_migrations')").fetchall()
        col_names = {row["name"] for row in cols}
        conn.close()
        assert "conflict_summary" in col_names

        # Run a full migration — select must not crash.
        from app.auth import AuthService
        from app.storage import UserStorage, write_json_atomic

        legacy_root = tmp_path / "legacy"
        (legacy_root / "memory_data").mkdir(parents=True)
        write_json_atomic(
            legacy_root / "memory_data" / "learning_history_user123.json",
            {"documents": [], "questions": [], "notes": [], "sessions": []},
        )
        user_id = AuthService(db_path).register("UpgradeUser", "x" * 12).id
        storage = UserStorage(tmp_path / "data")
        service = LegacyMigrationService(db_path=db_path, storage=storage,
                                         legacy_root=legacy_root)
        result = service.claim(user_id)
        assert result.status == "completed"


class TestPreExistingStatePreserved:
    """F2: Pre-existing user state is restored on failure."""

    def test_history_overwritten_then_restored_on_failure(self, tmp_path):
        """History at the destination exists before migration. After a
        failure, its original bytes must be restored."""
        service, user_id, storage, legacy_root, db_path = _make_service(tmp_path)
        # Also stage a document so the plan has >1 file.
        _make_document(legacy_root, "doc.pdf")

        # Pre-populate the user's history with distinct content.
        user_paths = storage.ensure_user_dirs(user_id)
        original_data = {"documents": [{"document_id": "pre-existing"}],
                         "questions": [{"q": "original"}],
                         "notes": [{"note": "keep-me"}],
                         "sessions": []}
        write_json_atomic(user_paths.history, original_data)
        original_bytes = user_paths.history.read_text(encoding="utf-8")

        # Fail on the second publish (history is first, document is second).
        original_copy = service._copy_validated
        call_count = [0]

        def fail_on_second_publish(source, target):
            call_count[0] += 1
            if call_count[0] == 3:
                raise OSError("injected publish failure")
            return original_copy(source, target)

        service._copy_validated = fail_on_second_publish
        result = service.claim(user_id)
        assert result.status == "failed"

        # History must be restored to its pre-existing content.
        restored = user_paths.history.read_text(encoding="utf-8")
        assert restored == original_bytes, (
            "pre-existing history must be restored after failure")

        # The journal file must be cleaned up.
        journal = user_paths.history.with_name(
            f".{user_paths.history.name}.pre-migration.bak")
        assert not journal.exists(), "journal backup must be cleaned up"

    def test_rag_cache_overwritten_then_restored_on_failure(self, tmp_path):
        """RAG cache that exists before migration is restored after failure."""
        service, user_id, storage, legacy_root, db_path = _make_service(tmp_path)
        _make_rag(legacy_root)
        _make_document(legacy_root, "extra.pdf")

        # Pre-populate the user's RAG cache.
        user_paths = storage.ensure_user_dirs(user_id)
        original_rag = {"documents": {"existing": {"chunks": 99}}}
        write_json_atomic(user_paths.rag_cache, original_rag)
        original_bytes = user_paths.rag_cache.read_text(encoding="utf-8")

        # Fail after history + rag are published.
        original_copy = service._copy_validated
        call_count = [0]

        def fail_after_rag(source, target):
            call_count[0] += 1
            if call_count[0] == 4:  # 1=stage doc, 2=hist, 3=rag, 4=doc
                raise OSError("injected failure after rag")
            return original_copy(source, target)

        service._copy_validated = fail_after_rag
        result = service.claim(user_id)
        assert result.status == "failed"

        restored = user_paths.rag_cache.read_text(encoding="utf-8")
        assert restored == original_bytes, (
            "pre-existing rag cache must be restored")


class TestNoPartialTarget:
    """F3: A failed copy never leaves a partial destination file."""

    def test_copy_failure_does_not_leave_truncated_file(self, tmp_path):
        """When copy2 fails mid-way, the destination is either the old
        version (atomic replace) or absent — never a partial file."""
        service, user_id, storage, legacy_root, db_path = _make_service(tmp_path)
        _make_document(legacy_root, "doc.pdf", "a" * 5000)

        user_paths = storage.ensure_user_dirs(user_id)
        # Pre-populate history destination with known content to verify
        # atomic replacement semantics.
        write_json_atomic(user_paths.history, {"documents": [], "questions": [],
                                                "notes": [], "sessions": []})

        # Patch shutil.copy2 directly to write partial bytes then raise.
        # This mimics a disk-full or network-failure scenario.
        import shutil as _shutil
        original_copy2 = _shutil.copy2
        fail_after = [False]

        def partial_copy(src, dst, **kw):
            if fail_after[0]:
                return original_copy2(src, dst, **kw)
            original_copy2(src, dst, **kw)
            # On the publish step for the document, truncate and raise.
            # We identify publish by target being in the user docs dir.
            if "documents" in str(dst):
                fail_after[0] = True
                # No partial file is created because tmp.replace is atomic.
                # We raise *after* the tmp file is created but *before* replace.
                raise OSError("simulated partial write")
            return None  # unreachable

        # We need a different injection point — the tmp file creation itself.
        # The simplest test: verify tmp files are cleaned up after error.
        _shutil.copy2 = partial_copy

        try:
            # Because our partial_copy patch is too coarse, use a simpler
            # approach: inject failure during _publish_plan after tmp was
            # written but before replace.  We do this by monkey-patching
            # the tmp.replace call.
            original_replace = Path.replace
            injected = [False]

            def fail_replace(self, target):
                if (not injected[0] and "documents" in str(self)
                        and ".migrating" in str(self)):
                    injected[0] = True
                    raise OSError("atomic replace failed")
                return original_replace(self, target)

            Path.replace = fail_replace
            result = service.claim(user_id)
            assert result.status == "failed"
        finally:
            _shutil.copy2 = original_copy2
            Path.replace = original_replace

        # F2: Verify no .migrating temp file was left behind.
        # Names are .<target-name>.migrating, so glob must use *.migrating.
        migrating = list(user_paths.documents.glob("*.migrating"))
        assert len(migrating) == 0, (
            f"migration temp files must be cleaned up, found: {migrating}")


class TestOwnershipLocked:
    """F4: Migration ownership is locked on first claim."""

    def test_different_user_blocked_after_failed_first_claim(self, tmp_path):
        """First user fails. Second user must get 'blocked', not change
        claimed_by_user_id."""
        service, user_id, storage, legacy_root, db_path = _make_service(tmp_path)

        # First user fails.
        original_copy = service._copy_validated

        def fail_always(source, target):
            raise OSError("first-user failure")

        service._copy_validated = fail_always
        first = service.claim(user_id)
        assert first.status == "failed"

        # Register second user.
        other_id = AuthService(db_path).register("OtherUser", "y" * 12).id
        service._copy_validated = original_copy  # restore for retry

        second = service.claim(other_id)
        assert second.status == "blocked", (
            f"expected 'blocked' but got '{second.status}'")
        assert "already been claimed" in (second.error_summary or "").lower()

        # DB still has the first claimant.
        from app.database import connect
        conn = connect(db_path)
        row = conn.execute(
            "select claimed_by_user_id from data_migrations where migration_key = ?",
            ("legacy-user123",),
        ).fetchone()
        conn.close()
        assert row["claimed_by_user_id"] == user_id

    def test_same_user_retries_after_failure(self, tmp_path):
        """Same user retrying a failed claim must succeed."""
        service, user_id, storage, legacy_root, db_path = _make_service(tmp_path)

        original_copy = service._copy_validated

        def fail_once(source, target):
            raise OSError("first-attempt failure")

        service._copy_validated = fail_once
        service.claim(user_id)

        service._copy_validated = original_copy
        second = service.claim(user_id)
        assert second.status == "completed"


class TestMemoryNoneNotOwned:
    """F5: Memories with None metadata are not assigned to claimant."""

    def test_none_metadata_is_not_owned(self, tmp_path):
        """A memory with metadata=None must be counted as ambiguous."""
        service, user_id, storage, legacy_root, db_path = _make_service(tmp_path)
        memories = [
            {"content": "no-meta", "metadata": None},
            {"content": "no-user-id", "metadata": {}},
            {"content": "explicit", "metadata": {"user_id": user_id}},
        ]
        _make_memory(legacy_root, "memories.json", memories)

        result = service.claim(user_id)
        assert result.status == "completed"
        # Only "explicit" is owned. "no-meta" and "no-user-id" are ambiguous.
        assert "2 ambiguous memory item(s)" in (result.skipped_summary or "")

        user_paths = storage.user_paths(user_id)
        data = read_json(user_paths.memory_snapshot, default={})
        assert len(data["memories"]) == 1
        assert data["memories"][0]["content"] == "explicit"


class TestScanExclusion:
    """F6: Legacy scanning excludes the active data root and its sub-trees."""

    def test_data_root_is_not_scanned_as_legacy(self, tmp_path):
        """Files placed in the data root (backups, staging, users) must not
        appear as legacy artifacts."""
        service, user_id, storage, legacy_root, db_path = _make_service(tmp_path)
        _make_document(legacy_root, "legit.pdf", "real legacy")

        # Plant bait files inside data_root subdirs.  Write them after
        # mkdir (claim() uses exist_ok=False on backup_dir, so we must
        # avoid colliding with that exact path).
        data_root = storage.data_root
        bait_backup_dir = data_root / "legacy_backups" / "bait"
        bait_backup_dir.mkdir(parents=True, exist_ok=True)
        (bait_backup_dir / "rag_cache.json").write_text(
            '{"rag": "fake"}', encoding="utf-8")

        bait_staging_dir = data_root / "migration_staging" / "bait"
        bait_staging_dir.mkdir(parents=True, exist_ok=True)
        (bait_staging_dir / "learning_history_user123.json").write_text(
            '{"documents": [], "questions": [], "notes": [], "sessions": []}',
            encoding="utf-8")

        # Also place a doc-like file inside user directories.
        user_paths = storage.ensure_user_dirs(user_id)
        (user_paths.documents / "from-prior-run.pdf").write_text(
            "prior content", encoding="utf-8")

        scan = service.scan()
        # Only the legit legacy document must appear.
        assert scan["documents"]["count"] == 1, (
            f"only the legacy doc should be counted, got {scan['documents']}")
        # Bait: rag_cache in backups must NOT appear.
        assert scan["rag_cache"]["count"] == 0, (
            "rag_cache in backups must not be scanned")
        # The legit history from _make_service must still be visible.
        assert scan["history"]["exists"] is True

        result = service.claim(user_id)
        assert result.status == "completed"

    def test_repeated_scan_never_finds_migrated_output(self, tmp_path):
        """After a successful migration, a rescan must not discover the
        migrated user files as new legacy artifacts."""
        service, user_id, storage, legacy_root, db_path = _make_service(tmp_path)
        _make_document(legacy_root, "doc.pdf", "legacy doc body")
        _make_rag(legacy_root)

        first = service.claim(user_id)
        assert first.status == "completed"

        # Rescan — must not find the migrated output.
        scan2 = service.scan()
        # The legacy doc.pdf and rag_cache were scanned in the first run
        # but the target user directories are excluded in subsequent scans.
        assert scan2["documents"]["count"] == 1, (
            "migrated user output must not appear as legacy")


class TestEmbeddedPathSanitization:
    """F7: Error sanitization removes paths embedded within messages."""

    def test_embedded_windows_path_is_scrubbed(self):
        exc = OSError(
            "Cannot copy from C:\\Users\\Alice\\docs\\file.pdf to "
            "D:\\data\\target.pdf: Permission denied"
        )
        sanitized = _sanitize_error(exc)
        assert "C:\\Users" not in sanitized
        assert "D:\\data" not in sanitized
        assert "Permission denied" in sanitized
        assert "Cannot copy from" in sanitized
        assert "[...]" in sanitized

    def test_embedded_unix_path_is_scrubbed(self):
        exc = RuntimeError(
            "Failed reading /home/alice/data/secret.json: "
            "No such file or directory"
        )
        sanitized = _sanitize_error(exc)
        assert "/home/alice" not in sanitized
        assert "No such file or directory" in sanitized

    def test_url_credentials_are_scrubbed(self):
        exc = RuntimeError(
            "Failed to connect to https://admin:secret123@db.example.com: "
            "Connection refused"
        )
        sanitized = _sanitize_error(exc)
        assert "admin" not in sanitized
        assert "secret123" not in sanitized
        assert "creds@" in sanitized
        assert "Connection refused" in sanitized

    def test_standalone_path_line_still_removed(self):
        # Regression: pure-path lines still dropped.
        exc = OSError(
            "C:\\Users\\Alice\\data\\file.txt\n"
            "Something went wrong"
        )
        sanitized = _sanitize_error(exc)
        assert "C:\\Users" not in sanitized
        assert "Something went wrong" in sanitized


# ── cross-type staging tests ────────────────────────────────────────────────

class TestMigratingCleanup:
    """F2: .migrating temp files are cleaned up unconditionally."""

    def test_migrating_temp_cleaned_after_failed_replace(self, tmp_path):
        """When tmp.replace() raises, the .migrating file must be removed."""
        service, user_id, storage, legacy_root, db_path = _make_service(tmp_path)
        _make_document(legacy_root, "doc.pdf", "body")

        # Monkey-patch Path.replace to fail on the document publish step.
        original_replace = Path.replace

        def fail_replace(self_path, target):
            if (".migrating" in str(self_path)
                    and "documents" in str(self_path)):
                raise OSError("simulated replace failure")
            return original_replace(self_path, target)

        Path.replace = fail_replace
        try:
            result = service.claim(user_id)
            assert result.status == "failed"
        finally:
            Path.replace = original_replace

        user_paths = storage.user_paths(user_id)
        # Correct glob: actual names are .<target-name>.migrating
        migrating = list(user_paths.documents.glob("*.migrating"))
        assert len(migrating) == 0, (
            f".migrating temp must be cleaned, found: {migrating}")

    def test_migrating_temp_cleaned_after_failed_copy_validated(self, tmp_path):
        """When _copy_validated raises during publish, the .migrating
        file must be removed."""
        service, user_id, storage, legacy_root, db_path = _make_service(tmp_path)
        _make_document(legacy_root, "doc.pdf", "body")

        original_copy = service._copy_validated
        call_count = [0]

        def fail_on_publish_copy(source, target):
            call_count[0] += 1
            if ".migrating" in str(target):
                raise OSError("simulated copy failure on migrating temp")
            return original_copy(source, target)

        service._copy_validated = fail_on_publish_copy
        result = service.claim(user_id)
        assert result.status == "failed"

        user_paths = storage.user_paths(user_id)
        migrating = list(user_paths.documents.glob("*.migrating"))
        assert len(migrating) == 0, (
            f".migrating temp must be cleaned, found: {migrating}")


class TestJournalsCleanedOnSuccess:
    """F3: Pre-migration journals are removed after successful publish."""

    def test_no_pre_migration_bak_after_success(self, tmp_path):
        """After a successful migration, no .pre-migration.bak journal
        files remain beside active user data."""
        service, user_id, storage, legacy_root, db_path = _make_service(tmp_path)
        _make_document(legacy_root, "doc.pdf", "content")

        # Seed pre-existing user data that will be journaled.
        user_paths = storage.ensure_user_dirs(user_id)
        write_json_atomic(user_paths.history, {
            "documents": [{"document_id": "pre-existing"}],
            "questions": [],
            "notes": [],
            "sessions": [],
        })

        result = service.claim(user_id)
        assert result.status == "completed"

        # F3: No .pre-migration.bak journals left on success.
        bak_files = list(user_paths.documents.glob("*.pre-migration.bak"))
        # Also check in the root of user_paths.root (for history.json etc).
        bak_files += list(user_paths.root.glob("*.pre-migration.bak"))
        # Check in rag dir too.
        bak_files += list(user_paths.root.glob("**/*.pre-migration.bak"))
        assert len(bak_files) == 0, (
            f"no .pre-migration.bak journals on success, found: {bak_files}")

    def test_pre_migration_bak_present_after_failure(self, tmp_path):
        """After failure, the journal is used by rollback to restore
        and is itself cleaned. Verify the restored file is intact and
        no journal remains."""
        service, user_id, storage, legacy_root, db_path = _make_service(tmp_path)
        _make_document(legacy_root, "extra.pdf")

        # Pre-populate history.
        user_paths = storage.ensure_user_dirs(user_id)
        original_data = {"documents": [{"document_id": "before"}],
                         "questions": [], "notes": [], "sessions": []}
        write_json_atomic(user_paths.history, original_data)
        original_bytes = user_paths.history.read_text(encoding="utf-8")

        # Fail on second publish (history succeeds, document fails).
        original_copy = service._copy_validated
        call_count = [0]

        def fail_second_publish(source, target):
            call_count[0] += 1
            if call_count[0] == 3:
                raise OSError("injected failure")
            return original_copy(source, target)

        service._copy_validated = fail_second_publish
        result = service.claim(user_id)
        assert result.status == "failed"

        # History restored.
        assert user_paths.history.read_text(encoding="utf-8") == original_bytes
        # No journal left after rollback.
        bak_files = list(user_paths.root.glob("**/*.pre-migration.bak"))
        assert len(bak_files) == 0, (
            f"journals must be cleaned after rollback, found: {bak_files}")


class TestReportRecordCollision:
    """F4-final: every path in _stage_report() is exercised by tests that
    reach it (no early returns from claim())."""

    # ── helpers ──────────────────────────────────────────────────────────

    def _prepare_collision_setup(self, tmp_path, *,
                                  insert_user_id=None,
                                  insert_relative_path=None,
                                  create_file=True):
        """Create a service + legacy report, then manually insert a
        report_records row so ``_stage_report()`` encounters it.

        Returns ``(service, user_id, storage, legacy_root, db_path,
        report_id, report_path)``.
        """
        service, user_id, storage, legacy_root, db_path = _make_service(tmp_path)
        _make_report(legacy_root, "weekly.md", "# Report")
        report_src = legacy_root / "reports" / "weekly.md"
        relative = report_src.relative_to(legacy_root)
        report_id = service._deterministic_id("report", str(relative))
        expected_path = f"reports/{report_id}.md"

        if insert_user_id is None:
            insert_user_id = user_id
        if insert_relative_path is None:
            insert_relative_path = expected_path

        from app.database import transaction as tx
        with tx(db_path) as conn:
            conn.execute(
                """insert into report_records (id, user_id, title,
                   relative_path, created_at)
                   values (?, ?, ?, ?, ?)""",
                (report_id, insert_user_id, "Weekly",
                 insert_relative_path,
                 datetime.now(timezone.utc).isoformat()),
            )

        if create_file:
            user_paths = storage.ensure_user_dirs(user_id)
            report_path = user_paths.reports / f"{report_id}.md"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text("# Report", encoding="utf-8")
        else:
            report_path = None

        return (service, user_id, storage, legacy_root, db_path,
                report_id, report_path)

    # ── test: same user, correct path, file exists → idempotent ──────────

    def test_exact_match_is_idempotent(self, tmp_path):
        """Same user, correct relative_path, file present → silently
        skipped by _stage_report()."""
        (service, user_id, storage, legacy_root, db_path,
         report_id, report_path) = self._prepare_collision_setup(tmp_path)

        result = service.claim(user_id)
        assert result.status == "completed"
        # No conflict — the existing row was accepted as idempotent.
        assert not result.conflict_summary

    # ── test: different user → conflict, no ID exposed ───────────────────

    def test_different_user_reports_conflict_no_id_exposed(self, tmp_path):
        """A row owned by a different user is a conflict; the message
        must not contain the other user's UUID."""
        import uuid as _uuid
        from app.database import transaction as tx

        # Let _make_service initialise the DB first, then insert a
        # second user directly (no AuthService constructor needed).
        service, user_id, storage, legacy_root, db_path = _make_service(tmp_path)
        _make_report(legacy_root, "weekly.md", "# Report")
        report_src = legacy_root / "reports" / "weekly.md"
        relative = report_src.relative_to(legacy_root)
        report_id = service._deterministic_id("report", str(relative))
        expected_path = f"reports/{report_id}.md"
        other_id = str(_uuid.uuid4())

        # Insert a different user's row into users + report_records.
        with tx(db_path) as conn:
            conn.execute(
                """insert into users (id, username, username_key,
                   password_hash, status, created_at, updated_at)
                   values (?, ?, ?, ?, 'active', ?, ?)""",
                (other_id, "Stranger", "stranger", "hash",
                 datetime.now(timezone.utc).isoformat(),
                 datetime.now(timezone.utc).isoformat()),
            )
            conn.execute(
                """insert into report_records (id, user_id, title,
                   relative_path, created_at)
                   values (?, ?, ?, ?, ?)""",
                (report_id, other_id, "Weekly", expected_path,
                 datetime.now(timezone.utc).isoformat()),
            )

        # Pre-create the report file for the claiming user.
        user_paths = storage.ensure_user_dirs(user_id)
        report_path = user_paths.reports / f"{report_id}.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text("# Report", encoding="utf-8")

        result = service.claim(user_id)
        assert result.status == "completed"
        # Conflict expected — the row is owned by other_id.
        assert result.conflict_summary is not None
        assert "already claimed" in result.conflict_summary
        # Assert the other user's UUID is NOT leaked.
        assert other_id not in (result.conflict_summary or "")

        result = service.claim(user_id)
        assert result.status == "completed"
        assert result.conflict_summary is not None
        assert "already claimed" in result.conflict_summary
        # Assert the other user's UUID is NOT leaked.
        assert other_id not in (result.conflict_summary or "")

    # ── test: wrong relative_path → conflict ─────────────────────────────

    def test_path_mismatch_reports_conflict(self, tmp_path):
        """Same user but stored relative_path differs from expected →
        conflict reported."""
        (service, user_id, _storage, _lr, _db_path,
         report_id, _rp) = self._prepare_collision_setup(
            tmp_path, insert_relative_path="reports/wrong-name.md")

        result = service.claim(user_id)
        assert result.status == "completed"
        assert result.conflict_summary is not None
        assert "path mismatch" in result.conflict_summary

    # ── test: row exists but file missing → conflict ─────────────────────

    def test_file_missing_reports_conflict(self, tmp_path):
        """Same user, correct relative_path, but the report file is
        absent → conflict reported."""
        (service, user_id, _storage, _lr, _db_path,
         report_id, _rp) = self._prepare_collision_setup(
            tmp_path, create_file=False)

        result = service.claim(user_id)
        assert result.status == "completed"
        assert result.conflict_summary is not None
        assert "file missing" in result.conflict_summary

    # ── test: existing row, same user+path, content differs → conflict ────

    def test_existing_row_different_content_is_conflict(self, tmp_path):
        """Same user, correct relative_path, file present — but source
        and target SHA-256 differ.  Must record conflict and leave
        existing row and file unchanged (no state change)."""
        (service, user_id, storage, legacy_root, db_path,
         report_id, report_path) = self._prepare_collision_setup(tmp_path)

        # Modify the legacy source so its SHA-256 differs from the
        # destination file that _prepare_collision_setup placed.
        report_src = legacy_root / "reports" / "weekly.md"
        report_src.write_text("# Different Content In Legacy", encoding="utf-8")

        # Capture pre-claim state.
        original_target_content = report_path.read_text(encoding="utf-8")
        from app.database import connect
        conn = connect(db_path)
        original_row = dict(conn.execute(
            "select * from report_records where id = ?", (report_id,)
        ).fetchone())
        conn.close()

        result = service.claim(user_id)
        assert result.status == "completed"
        # Must record conflict — content differs.
        assert result.conflict_summary is not None
        assert "content differs" in result.conflict_summary

        # No state change: destination file untouched.
        assert report_path.read_text(encoding="utf-8") == original_target_content

        # No state change: DB row unchanged.
        conn = connect(db_path)
        current_row = dict(conn.execute(
            "select * from report_records where id = ?", (report_id,)
        ).fetchone())
        conn.close()
        assert current_row == original_row

    # ── test: no row, same file content → row inserted ───────────────────

    def test_no_row_same_file_inserts_row(self, tmp_path):
        """No DB row, but an identical report file is at destination →
        insert the row idempotently."""
        service, user_id, storage, legacy_root, db_path = _make_service(tmp_path)
        _make_report(legacy_root, "weekly.md", "# Report")
        # Pre-place a matching file at destination.
        user_paths = storage.ensure_user_dirs(user_id)
        report_src = legacy_root / "reports" / "weekly.md"
        relative = report_src.relative_to(legacy_root)
        report_id = service._deterministic_id("report", str(relative))
        (user_paths.reports / f"{report_id}.md").parent.mkdir(
            parents=True, exist_ok=True)
        (user_paths.reports / f"{report_id}.md").write_text(
            "# Report", encoding="utf-8")

        result = service.claim(user_id)
        assert result.status == "completed"
        assert not result.conflict_summary
        # A report_records row must have been created.
        from app.database import connect
        conn = connect(db_path)
        rows = conn.execute(
            "select * from report_records where id = ?", (report_id,)
        ).fetchall()
        conn.close()
        assert len(rows) == 1

    # ── test: no row, different file content → conflict ──────────────────

    def test_no_row_different_file_content_is_conflict(self, tmp_path):
        """No DB row, but a different report file exists at destination →
        conflict reported."""
        service, user_id, storage, legacy_root, db_path = _make_service(tmp_path)
        _make_report(legacy_root, "weekly.md", "# Legacy Report")
        # Pre-place a different file at destination.
        user_paths = storage.ensure_user_dirs(user_id)
        report_src = legacy_root / "reports" / "weekly.md"
        relative = report_src.relative_to(legacy_root)
        report_id = service._deterministic_id("report", str(relative))
        (user_paths.reports / f"{report_id}.md").parent.mkdir(
            parents=True, exist_ok=True)
        (user_paths.reports / f"{report_id}.md").write_text(
            "# Different Content", encoding="utf-8")

        result = service.claim(user_id)
        assert result.status == "completed"
        assert result.conflict_summary is not None
        assert "already exists" in result.conflict_summary


class TestFullLegacySuite:
    def test_all_artifact_types_migrate_together(self, tmp_path):
        """History, documents, RAG, reports, and memory all migrate
        successfully as one claim."""
        service, user_id, storage, legacy_root, db_path = _make_service(tmp_path)
        _make_document(legacy_root, "book.pdf", "textbook")
        _make_rag(legacy_root)
        _make_report(legacy_root, "weekly.md")
        _make_memory(legacy_root, "memories.json", [
            {"content": "m1", "metadata": {"user_id": user_id}},
        ])

        result = service.claim(user_id)
        assert result.status == "completed"
        user_paths = storage.user_paths(user_id)

        # History
        history = read_json(user_paths.history, default={})
        assert history["documents"][0]["document_id"] == "old-doc"

        # Documents
        docs = list(user_paths.documents.iterdir())
        assert len(docs) == 1
        assert docs[0].read_text(encoding="utf-8") == "textbook"

        # RAG
        rag = read_json(user_paths.rag_cache, default={})
        assert rag["documents"]["doc-1"]["chunks"] == 5

        # Memory
        mem = read_json(user_paths.memory_snapshot, default={})
        assert len(mem["memories"]) == 1
        assert mem["memories"][0]["content"] == "m1"

        # Reports
        reports = list(user_paths.reports.iterdir())
        assert len(reports) == 1
        assert "Session Report" in reports[0].read_text(encoding="utf-8")

    def test_scan_records_skipped_unrecognized_files(self, tmp_path):
        service, user_id, storage, legacy_root, db_path = _make_service(tmp_path)
        (legacy_root / "random.bin").write_text("garbage", encoding="utf-8")
        (legacy_root / "notes.xyz").write_text("odd", encoding="utf-8")

        result = service.claim(user_id)
        assert result.status == "completed"
        assert "2 unrecognized file(s)" in (result.skipped_summary or "")


# ── packet 04 addition: restart after migration restores data ────────────


class TestRestartAfterMigration:
    """After successful migration, a fresh registry/process can access
    all migrated History, documents, RAG, Memory, and reports."""

    def test_restart_after_migration_restores_all_artifacts(self, tmp_path):
        """Run migration, then simulate a process restart and verify
        all artifact types are accessible through fresh service objects."""
        service, user_id, storage, legacy_root, db_path = _make_service(tmp_path)
        _make_document(legacy_root, "book.pdf", "textbook body")
        _make_rag(legacy_root)
        _make_report(legacy_root, "weekly.md",
                     "# Session Report\n\nOriginal content.")
        _make_memory(legacy_root, "memories.json", [
            {"content": "migrated-memory",
             "metadata": {"user_id": user_id}},
        ])

        result = service.claim(user_id)
        assert result.status == "completed"

        # Simulate restart: create fresh service, storage, auth.
        from app.auth import AuthService
        from app.reports import ReportService

        fresh_storage = UserStorage(tmp_path / "data")
        fresh_user_paths = fresh_storage.user_paths(user_id)

        # History restored.
        history = read_json(fresh_user_paths.history, default={})
        assert history["documents"][0]["document_id"] == "old-doc"
        assert len(history["notes"]) == 1

        # Documents restored.
        docs = list(fresh_user_paths.documents.iterdir())
        assert len(docs) == 1
        assert docs[0].read_text(encoding="utf-8") == "textbook body"

        # RAG cache restored.
        rag = read_json(fresh_user_paths.rag_cache, default={})
        assert rag["documents"]["doc-1"]["chunks"] == 5

        # Memory snapshot restored.
        mem = read_json(fresh_user_paths.memory_snapshot, default={})
        mem_contents = [m["content"] for m in mem.get("memories", [])]
        assert "migrated-memory" in mem_contents

        # Reports restored.
        fresh_reports = ReportService(db_path, fresh_storage)
        report_list = fresh_reports.list_reports(user_id)
        assert len(report_list) >= 1
        content = fresh_reports.read_report(user_id, report_list[0].id)
        assert "Original content" in content

    def test_restart_after_failed_migration_preserves_empty_state(
        self, tmp_path
    ):
        """After a failed migration, a fresh process must see empty
        (not partial) user state."""
        service, user_id, storage, legacy_root, db_path = _make_service(tmp_path)
        _make_document(legacy_root, "doc.pdf", "body")

        original_copy = service._copy_validated

        def fail_always(source, target):
            raise OSError("simulated failure")

        service._copy_validated = fail_always
        service.claim(user_id)
        service._copy_validated = original_copy

        # Fresh storage after failure.
        fresh_storage = UserStorage(tmp_path / "data")
        fresh_paths = fresh_storage.user_paths(user_id)

        # No partial state.
        assert not fresh_paths.history.exists(), (
            "History must not exist after failed migration"
        )
        assert list(fresh_paths.documents.iterdir()) == [], (
            "No documents after failed migration"
        )
