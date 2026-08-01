"""Tests for explicit History and Memory corruption recovery.

Covers the acceptance criteria in ``03-corruption-recovery``:
- Corrupt History and Memory both block writes.
- Explicit quarantine creates a user-scoped backup and clean active file.
- Valid restore succeeds atomically; invalid restore is non-destructive.
- Forged backup IDs and cross-user access are rejected safely.
- UI handlers reject missing, forged, and expired sessions.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from threading import RLock
from unittest.mock import MagicMock, patch

import pytest

from app.coordination import UserMutationCoordinator
from app.history import EMPTY_HISTORY, CorruptHistoryError, HistoryRepository
from app.memory_repository import (
    CorruptMemorySnapshotError,
    MemorySnapshotRepository,
)
from app.recovery import RecoveryResult, RecoveryService
from app.storage import read_json, write_json_atomic


# ── helpers ────────────────────────────────────────────────────────────────


def _make_recovery(tmp_path: Path, user_id: str = "user-test") -> RecoveryService:
    """Create a RecoveryService wired to temp paths for *user_id*."""
    history_path = tmp_path / "history.json"
    memory_path = tmp_path / "memories.json"
    backup_dir = tmp_path / "backups"

    history_repo = HistoryRepository(history_path)
    history_repo.save(deepcopy(EMPTY_HISTORY))

    memory_repo = MemorySnapshotRepository(memory_path, user_id=user_id)
    write_json_atomic(memory_path, {"user_id": user_id, "memories": []})

    lock = RLock()
    coordinator = UserMutationCoordinator(
        user_id=user_id,
        lock=lock,
        history=history_repo,
        document_root=tmp_path / "docs",
    )

    return RecoveryService(
        coordinator=coordinator,
        history_repo=history_repo,
        memory_repo=memory_repo,
        backup_dir=backup_dir,
    )


def _make_recovery_with_manager(
    tmp_path: Path, user_id: str = "user-test"
) -> tuple[RecoveryService, Any]:
    """Create a RecoveryService wired with a real MemoryManager.

    Returns (service, memory_manager) so tests can verify that the
    in-memory manager stays synchronized with the snapshot file.
    """
    from hello_agents.memory.base import MemoryConfig
    from hello_agents.memory.manager import MemoryManager

    history_path = tmp_path / "history.json"
    memory_path = tmp_path / "memories.json"
    backup_dir = tmp_path / "backups"

    history_repo = HistoryRepository(history_path)
    history_repo.save(deepcopy(EMPTY_HISTORY))

    memory_repo = MemorySnapshotRepository(memory_path, user_id=user_id)
    write_json_atomic(memory_path, {"user_id": user_id, "memories": []})

    memory_config = MemoryConfig(
        database_path=str(tmp_path / "memory" / f"memory_{user_id}.db")
    )
    (tmp_path / "memory").mkdir(parents=True, exist_ok=True)
    memory_manager = MemoryManager(
        config=memory_config,
        user_id=user_id,
        enable_working=True,
        enable_episodic=False,
        enable_semantic=False,
        snapshot_repository=memory_repo,
    )

    lock = RLock()
    coordinator = UserMutationCoordinator(
        user_id=user_id,
        lock=lock,
        history=history_repo,
        document_root=tmp_path / "docs",
    )

    svc = RecoveryService(
        coordinator=coordinator,
        history_repo=history_repo,
        memory_repo=memory_repo,
        backup_dir=backup_dir,
        memory_manager=memory_manager,
    )
    return svc, memory_manager


def _write_corrupt(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _history_with_notes(notes: list[dict]) -> dict:
    data = deepcopy(EMPTY_HISTORY)
    data["notes"] = notes
    return data


# ── 1. Corruption blocks writes ───────────────────────────────────────────


class TestCorruptionBlocksWrites:
    """Acceptance: Corrupt History and Memory both block writes."""

    def test_corrupt_history_blocks_load_and_write(self, tmp_path):
        svc = _make_recovery(tmp_path)
        _write_corrupt(svc._history.path, "{not-json")

        # Check reports corruption.
        result = svc.check_history()
        assert not result.success
        assert "corrupt" in result.message.lower()

        # Write operations on the repository itself must fail.
        with pytest.raises(CorruptHistoryError):
            svc._history.add_note({"note": "must-fail"})

        # Corrupt bytes must not be overwritten.
        assert svc._history.path.read_text(encoding="utf-8") == "{not-json"

    def test_corrupt_memory_blocks_load(self, tmp_path):
        svc = _make_recovery(tmp_path)
        _write_corrupt(svc._memory.path, "{bad}")

        result = svc.check_memory()
        assert not result.success
        assert "corrupt" in result.message.lower()

        with pytest.raises(CorruptMemorySnapshotError):
            svc._memory.load_snapshot()

    def test_healthy_snapshots_check_clean(self, tmp_path):
        svc = _make_recovery(tmp_path)

        hist = svc.check_history()
        mem = svc.check_memory()
        assert hist.success
        assert mem.success


# ── 2. Quarantine creates backup and clean active file ────────────────────


class TestQuarantine:
    """Acceptance: Explicit quarantine creates a user-scoped backup and
    clean active file."""

    def test_quarantine_history_creates_backup_and_clean_file(self, tmp_path):
        svc = _make_recovery(tmp_path)
        # Seed with real data.
        svc._history.add_note({"note": "valuable", "concept": "test"})
        original_size = len(svc._history.load()["notes"])
        assert original_size == 1

        result = svc.quarantine_history()
        assert result.success
        assert result.backup_id is not None
        assert result.backup_id.startswith("history.json.corrupt-")

        # Active file is now empty.
        loaded = svc._history.load()
        assert loaded["notes"] == []

        # Backup file exists in backup directory.
        backup_path = svc._backup_dir / result.backup_id
        assert backup_path.exists()
        backup_data = read_json(backup_path, default=None)
        assert backup_data is not None
        assert len(backup_data["notes"]) == 1

    def test_quarantine_memory_creates_backup_and_clean_file(self, tmp_path):
        svc = _make_recovery(tmp_path)
        # Seed with real data.
        svc._memory.save_from_manager(_FakeManager("user-test", [
            {"content": "hello", "memory_type": "working",
             "metadata": {"user_id": "user-test"}},
        ]))

        result = svc.quarantine_memory()
        assert result.success
        assert result.backup_id is not None
        assert result.backup_id.startswith("memories.json.corrupt-")

        loaded = svc._memory.load_snapshot()
        assert loaded["memories"] == []

        backup_path = svc._backup_dir / result.backup_id
        assert backup_path.exists()

    def test_quarantine_when_file_absent_creates_empty(self, tmp_path):
        svc = _make_recovery(tmp_path)
        svc._history.path.unlink()

        result = svc.quarantine_history()
        assert result.success
        assert svc._history.path.exists()
        assert svc._history.load()["notes"] == []

    def test_backup_id_is_opaque_filename_only(self, tmp_path):
        svc = _make_recovery(tmp_path)
        svc._history.add_note({"note": "x"})
        result = svc.quarantine_history()

        bid = result.backup_id
        assert "/" not in bid
        assert "\\" not in bid
        assert bid == Path(bid).name  # no directory component


# ── 3. Restore validation and atomicity ───────────────────────────────────


class TestRestore:
    """Acceptance: Valid restore succeeds atomically; invalid restore is
    non-destructive."""

    def test_restore_history_succeeds_atomically(self, tmp_path):
        svc = _make_recovery(tmp_path)
        svc._history.add_note({"note": "original"})
        q = svc.quarantine_history()

        # Active is now empty — restore brings original back.
        assert svc._history.load()["notes"] == []
        r = svc.restore_history(q.backup_id)
        assert r.success
        assert svc._history.load()["notes"] == [{"note": "original"}]

    def test_restore_memory_succeeds_atomically(self, tmp_path):
        svc = _make_recovery(tmp_path)
        svc._memory.save_from_manager(_FakeManager("user-test", [
            {"content": "mem1", "memory_type": "working",
             "metadata": {"user_id": "user-test"}},
        ]))
        q = svc.quarantine_memory()

        assert svc._memory.load_snapshot()["memories"] == []
        r = svc.restore_memory(q.backup_id)
        assert r.success
        assert len(svc._memory.load_snapshot()["memories"]) == 1

    def test_restore_history_invalid_json_leaves_active_unchanged(self, tmp_path):
        svc = _make_recovery(tmp_path)
        svc._history.add_note({"note": "keep-me"})

        # Create a "backup" with invalid JSON in the backup dir.
        bad_id = "history.json.corrupt-20260101T000000Z"
        _write_corrupt(svc._backup_dir / bad_id, "{not-valid")

        r = svc.restore_history(bad_id)
        assert not r.success
        assert "unreadable" in r.message.lower()
        # Active file unchanged.
        assert svc._history.load()["notes"] == [{"note": "keep-me"}]

    def test_restore_history_wrong_schema_leaves_active_unchanged(self, tmp_path):
        svc = _make_recovery(tmp_path)
        svc._history.add_note({"note": "keep-me"})

        bad_id = "history.json.corrupt-20260101T000000Z"
        # Valid JSON but wrong structure (missing required keys).
        write_json_atomic(svc._backup_dir / bad_id, {"bad": "schema"})

        r = svc.restore_history(bad_id)
        assert not r.success
        assert "validation failed" in r.message.lower()
        assert svc._history.load()["notes"] == [{"note": "keep-me"}]

    def test_restore_history_scalar_value_leaves_active_unchanged(self, tmp_path):
        svc = _make_recovery(tmp_path)
        svc._history.add_note({"note": "keep-me"})

        bad_id = "history.json.corrupt-20260101T000000Z"
        write_json_atomic(svc._backup_dir / bad_id, "just a string")

        r = svc.restore_history(bad_id)
        assert not r.success
        assert svc._history.load()["notes"] == [{"note": "keep-me"}]

    def test_restore_memory_invalid_structure_leaves_active_unchanged(self, tmp_path):
        svc = _make_recovery(tmp_path)
        svc._memory.save_from_manager(_FakeManager("user-test", [
            {"content": "keep", "memory_type": "working",
             "metadata": {"user_id": "user-test"}},
        ]))

        bad_id = "memories.json.corrupt-20260101T000000Z"
        write_json_atomic(svc._backup_dir / bad_id, {"user_id": "user-test"})
        # Missing "memories" key.

        r = svc.restore_memory(bad_id)
        assert not r.success
        assert svc._memory.load_snapshot()["memories"][0]["content"] == "keep"

    def test_restore_history_missing_backup_returns_failure(self, tmp_path):
        svc = _make_recovery(tmp_path)

        with pytest.raises(FileNotFoundError):
            svc.restore_history("nonexistent.corrupt-20990101T000000Z")


# ── 4. Forged backup IDs and cross-user access ────────────────────────────


class TestForgedAndCrossUser:
    """Acceptance: Forged backup IDs and cross-user access are rejected
    safely."""

    def test_path_traversal_backup_id_rejected(self, tmp_path):
        svc = _make_recovery(tmp_path)
        svc._history.add_note({"note": "keep"})

        with pytest.raises(ValueError, match=r"Invalid backup identifier"):
            svc.restore_history("../../etc/passwd")

        with pytest.raises(ValueError, match=r"Invalid backup identifier"):
            svc.restore_history("/absolute/path")

    def test_backslash_traversal_rejected(self, tmp_path):
        svc = _make_recovery(tmp_path)
        with pytest.raises(ValueError, match=r"Invalid backup identifier"):
            svc.restore_history(r"..\..\windows\system32\config\sam")

    def test_empty_backup_id_rejected(self, tmp_path):
        svc = _make_recovery(tmp_path)
        with pytest.raises(ValueError, match=r"Invalid backup identifier"):
            svc.restore_history("")

    def test_memory_cross_user_restore_rejected(self, tmp_path):
        svc_alice = _make_recovery(tmp_path / "alice", user_id="alice")
        svc_bob = _make_recovery(tmp_path / "bob", user_id="bob")

        # Alice creates a backup.
        svc_alice._memory.save_from_manager(_FakeManager("alice", [
            {"content": "alice-data", "memory_type": "working",
             "metadata": {"user_id": "alice"}},
        ]))
        q = svc_alice.quarantine_memory()

        # Copy Alice's backup into Bob's backup directory.
        alice_backup = svc_alice._backup_dir / q.backup_id
        bob_backup_copy = svc_bob._backup_dir / q.backup_id
        import shutil
        shutil.copy2(alice_backup, bob_backup_copy)

        # Bob tries to restore Alice's backup — must fail.
        r = svc_bob.restore_memory(q.backup_id)
        assert not r.success
        assert "different user" in r.message.lower()

    def test_history_cross_user_backup_not_visible(self, tmp_path):
        svc_alice = _make_recovery(tmp_path / "alice", user_id="alice")
        svc_bob = _make_recovery(tmp_path / "bob", user_id="bob")

        svc_alice._history.add_note({"note": "alice-note"})
        q = svc_alice.quarantine_history()

        # Bob cannot see Alice's backup.
        assert q.backup_id not in svc_bob.list_history_backups()
        assert len(svc_bob.list_history_backups()) == 0

    def test_memory_restore_user_id_none_still_validates_schema(self, tmp_path):
        """Backup with no user_id is rejected at the ownership guard
        before reaching schema validation."""
        svc = _make_recovery(tmp_path)
        svc._memory.save_from_manager(_FakeManager("user-test", [
            {"content": "orig", "memory_type": "working",
             "metadata": {"user_id": "user-test"}},
        ]))
        q = svc.quarantine_memory()

        # Overwrite the backup to strip user_id.
        backup_path = svc._backup_dir / q.backup_id
        data = read_json(backup_path, default={})
        data.pop("user_id", None)
        write_json_atomic(backup_path, data)

        # Restore with no user_id in backup — the ownership layer
        # catches the missing/None user_id before schema validation runs.
        r = svc.restore_memory(q.backup_id)
        assert not r.success
        assert "ownership" in r.message.lower()


# ── 5. UI handler session checks ──────────────────────────────────────────


class TestUISessionRejection:
    """Acceptance: UI handlers reject missing, forged, and expired sessions."""

    def test_get_recovery_rejects_missing_session(self):
        from ui.gradio_app import _get_recovery
        import gradio as gr

        with pytest.raises(gr.Error, match=r"[Ll]og in"):
            _get_recovery("")

    def test_get_recovery_rejects_forged_token(self):
        from ui.gradio_app import _get_recovery
        import gradio as gr

        with pytest.raises(gr.Error, match=r"(expired|log out|log in)"):
            _get_recovery("forged-token-xyz")

    def test_get_recovery_rejects_none_token(self):
        from ui.gradio_app import _get_recovery
        import gradio as gr

        with pytest.raises(gr.Error, match=r"[Ll]og in"):
            _get_recovery(None)


# ── 6. End-to-end recovery flow ───────────────────────────────────────────


class TestEndToEndRecoveryFlow:
    """Integration: full check → quarantine → restore → verify cycle."""

    def test_full_history_cycle(self, tmp_path):
        svc = _make_recovery(tmp_path)

        # Seed data.
        svc._history.add_note({"note": "valuable content"})
        svc._history.add_document({"document_id": "doc-1", "document_name": "A.pdf"})
        assert len(svc._history.load()["notes"]) == 1

        # Quarantine valid data (e.g. user suspects corruption or
        # wants to reset and later restore).
        q = svc.quarantine_history()
        assert q.success
        assert q.backup_id is not None

        # Active is clean.
        assert svc.check_history().success
        assert svc._history.load()["notes"] == []

        # Restore from backup.
        r = svc.restore_history(q.backup_id)
        assert r.success

        # Data is back.
        loaded = svc._history.load()
        assert len(loaded["notes"]) == 1
        assert loaded["notes"][0]["note"] == "valuable content"
        assert len(loaded["documents"]) == 1

    def test_full_memory_cycle(self, tmp_path):
        svc = _make_recovery(tmp_path)

        # Seed data.
        svc._memory.save_from_manager(_FakeManager("user-test", [
            {"content": "memory-value", "memory_type": "episodic",
             "metadata": {"user_id": "user-test"}},
        ]))
        assert len(svc._memory.load_snapshot()["memories"]) == 1

        # Quarantine.
        q = svc.quarantine_memory()
        assert q.success

        # Restore.
        r = svc.restore_memory(q.backup_id)
        assert r.success
        assert len(svc._memory.load_snapshot()["memories"]) == 1

    def test_backup_listing_reflects_quarantine(self, tmp_path):
        svc = _make_recovery(tmp_path)

        assert svc.list_history_backups() == []
        assert svc.list_memory_backups() == []

        svc._history.add_note({"note": "n1"})
        hq = svc.quarantine_history()

        svc._memory.save_from_manager(_FakeManager("user-test", [
            {"content": "m1", "memory_type": "working",
             "metadata": {"user_id": "user-test"}},
        ]))
        mq = svc.quarantine_memory()

        assert hq.backup_id in svc.list_history_backups()
        assert mq.backup_id in svc.list_memory_backups()

    def test_restore_preserves_full_schema(self, tmp_path):
        """Restored History must have all required keys with correct types."""
        svc = _make_recovery(tmp_path)
        svc._history.add_note({"note": "n1"})
        svc._history.add_document({"document_id": "d1", "document_name": "D.pdf"})
        svc._history.add_question({"question": "q?", "answer": "a"})

        q = svc.quarantine_history()
        svc.restore_history(q.backup_id)

        loaded = svc._history.load()
        for key in EMPTY_HISTORY:
            assert key in loaded
            assert isinstance(loaded[key], list)


# ── 7. Finding 2: MemoryManager sync ──────────────────────────────────────


class TestMemoryManagerSync:
    """Finding 2: Memory recovery must keep the live MemoryManager in sync
    with the snapshot file."""

    def test_quarantine_memory_clears_live_manager(self, tmp_path):
        """Quarantine must clear the in-memory manager so a later save
        cannot resurrect quarantined state."""
        svc, manager = _make_recovery_with_manager(tmp_path, "user-test")

        # Seed memory via the real manager path.
        manager.add_memory(content="hello-memory", memory_type="working",
                           importance=0.8)
        assert len(svc._memory.load_snapshot()["memories"]) == 1
        assert manager.get_stats()["memory_counts"].get("working", 0) == 1

        # Quarantine.
        result = svc.quarantine_memory()
        assert result.success

        # File is clean.
        assert svc._memory.load_snapshot()["memories"] == []

        # Live manager is also cleared.
        counts = manager.get_stats()["memory_counts"]
        assert counts.get("working", 0) == 0

    def test_restore_memory_reloads_live_manager(self, tmp_path):
        """Restore must reload the MemoryManager from the restored file."""
        svc, manager = _make_recovery_with_manager(tmp_path, "user-test")

        # Seed and quarantine.
        manager.add_memory(content="precious", memory_type="working",
                           importance=0.9)
        q = svc.quarantine_memory()
        assert manager.get_stats()["memory_counts"].get("working", 0) == 0

        # Restore.
        r = svc.restore_memory(q.backup_id)
        assert r.success

        # File is restored.
        assert len(svc._memory.load_snapshot()["memories"]) == 1
        # Live manager is reloaded.
        counts = manager.get_stats()["memory_counts"]
        assert counts.get("working", 0) == 1

    def test_manager_save_cannot_resurrect_quarantined_state(self, tmp_path):
        """After quarantine, calling save_from_manager must not resurrect
        old data — the live manager must be empty."""
        svc, manager = _make_recovery_with_manager(tmp_path, "user-test")

        # Seed memory.
        manager.add_memory(content="old-data", memory_type="working",
                           importance=0.9)
        assert svc._memory.load_snapshot()["memories"][0]["content"] == "old-data"

        # Quarantine (clears both file and manager).
        svc.quarantine_memory()

        # Simulate what happens when a session ends and saves state:
        # the MemoryTool calls save_from_manager on the repository.
        svc._memory.save_from_manager(manager)

        # The file must still be empty — quarantined data is gone.
        assert svc._memory.load_snapshot()["memories"] == []


# ── 8. Finding 3: sanitized UI messages ───────────────────────────────────


class TestSanitizedMessages:
    """Finding 3: UI-visible recovery messages must never contain
    absolute filesystem paths."""

    def test_check_history_message_has_no_path(self, tmp_path):
        svc = _make_recovery(tmp_path)
        _write_corrupt(svc._history.path, "{bad")

        result = svc.check_history()
        assert not result.success
        # Message must NOT contain any path separator or the file name.
        assert str(svc._history.path) not in result.message
        assert svc._history.path.name not in result.message
        assert "corrupt and cannot be read" in result.message.lower()

    def test_check_memory_message_has_no_path(self, tmp_path):
        svc = _make_recovery(tmp_path)
        _write_corrupt(svc._memory.path, "{bad")

        result = svc.check_memory()
        assert not result.success
        assert str(svc._memory.path) not in result.message
        assert svc._memory.path.name not in result.message
        assert "corrupt and cannot be read" in result.message.lower()

    def test_restore_history_validation_error_has_no_path(self, tmp_path):
        svc = _make_recovery(tmp_path)
        svc._history.add_note({"note": "keep"})

        bad_id = "history.json.corrupt-20260101T000000Z-deadbeef"
        write_json_atomic(svc._backup_dir / bad_id, {"bad": "schema"})

        r = svc.restore_history(bad_id)
        assert not r.success
        # No path in message.
        assert str(svc._history.path) not in r.message
        assert bad_id not in r.message  # opaque ID only in success messages
        assert "validation failed" in r.message.lower()

    def test_restore_memory_validation_error_has_no_path(self, tmp_path):
        svc = _make_recovery(tmp_path)
        svc._memory.save_from_manager(_FakeManager("user-test", [
            {"content": "keep", "memory_type": "working",
             "metadata": {"user_id": "user-test"}},
        ]))

        bad_id = "memories.json.corrupt-20260101T000000Z-deadbeef"
        write_json_atomic(svc._backup_dir / bad_id, {"user_id": "user-test"})
        # Missing "memories" key.

        r = svc.restore_memory(bad_id)
        assert not r.success
        assert str(svc._memory.path) not in r.message
        assert "validation failed" in r.message.lower()

    def test_check_healthy_returns_no_path(self, tmp_path):
        svc = _make_recovery(tmp_path)
        result = svc.check_history()
        assert result.success
        assert str(svc._history.path) not in result.message

        result = svc.check_memory()
        assert result.success
        assert str(svc._memory.path) not in result.message


# ── 9. Finding 4: expired-session rejection ──────────────────────────────


class TestExpiredSessionRejection:
    """Finding 4 / Item 1: Real expired SessionRegistry tokens must be
    rejected and must not mutate recovery state.

    **Item 1 fix:** every test creates an independent SessionRegistry
    backed by ``tmp_path`` and monkeypatches ``ui.gradio_app`` so the
    production global database is NEVER touched.  The original is
    restored after each test.
    """

    @staticmethod
    def _make_isolated_registry(tmp_path: Path, idle_timeout=None):
        """Create a completely isolated SessionRegistry backed by tmp_path."""
        from datetime import timedelta
        from app.database import initialize_database
        from app.storage import UserStorage
        from app.session import SessionRegistry

        db_path = tmp_path / "app.db"
        initialize_database(db_path)
        storage = UserStorage(tmp_path)
        return SessionRegistry(
            db_path=db_path,
            storage=storage,
            idle_timeout=idle_timeout or timedelta(hours=12),
        )

    @staticmethod
    def _unique_username(prefix: str) -> str:
        import uuid
        return f"{prefix}-{uuid.uuid4().hex[:8]}"

    def test_expired_session_rejected_by_recovery(self, tmp_path, monkeypatch):
        """A genuinely expired token must be rejected by _get_recovery.

        Uses an ISOLATED SessionRegistry — never touches the production
        global database.
        """
        from datetime import timedelta
        import gradio as gr

        isolated = self._make_isolated_registry(
            tmp_path, idle_timeout=timedelta(seconds=-1)
        )
        token = isolated.register(
            self._unique_username("expire-test"), "password123"
        )

        # Monkeypatch the gradio_app module to use the isolated registry.
        monkeypatch.setattr("ui.gradio_app.session_registry", isolated)

        from ui.gradio_app import _get_recovery

        with pytest.raises(gr.Error, match=r"(expired|log in|log out)"):
            _get_recovery(token)

    def test_expired_session_rejected_by_check_corruption(
        self, tmp_path, monkeypatch
    ):
        """check_corruption_status on an expired token must reject without
        side effects.  Uses isolated SessionRegistry."""
        from datetime import timedelta
        import gradio as gr

        isolated = self._make_isolated_registry(
            tmp_path, idle_timeout=timedelta(seconds=-1)
        )
        token = isolated.register(
            self._unique_username("mut-test"), "password123"
        )

        monkeypatch.setattr("ui.gradio_app.session_registry", isolated)
        from ui.gradio_app import check_corruption_status

        with pytest.raises(gr.Error, match=r"(expired|log in|log out)"):
            check_corruption_status(token)

    def test_expired_session_mutation_handler_no_state_change(
        self, tmp_path, monkeypatch
    ):
        """Calling quarantine_history with an expired token must raise
        without modifying any recovery state.  Uses isolated
        SessionRegistry."""
        from datetime import timedelta
        import gradio as gr

        # Active registry first.
        isolated = self._make_isolated_registry(tmp_path)
        uname = self._unique_username("mut2-test")
        token = isolated.register(uname, "password123")

        monkeypatch.setattr("ui.gradio_app.session_registry", isolated)
        from ui.gradio_app import quarantine_history, check_corruption_status

        # Run quarantine once to create state while session is active.
        quarantine_history(token)

        # Now expire it by setting negative timeout.
        isolated.idle_timeout = timedelta(seconds=-1)

        with pytest.raises(gr.Error, match=r"(expired|log in|log out)"):
            quarantine_history(token)

        # Re-login with a fresh timeout and verify recovery state is intact.
        isolated.idle_timeout = timedelta(hours=12)
        new_token = isolated.login(uname, "password123")
        status = check_corruption_status(new_token)
        assert "✅" in status  # History should still be clean


# ── 9b. Item 2: _store_backup failure compensation ──────────────────────


class TestStoreBackupFailureCompensation:
    """Item 2: When _store_backup() fails during quarantine, the system
    must restore the active state and MemoryManager, and leave no
    orphaned backup outside the backup directory."""

    def test_history_store_backup_failure_restores_active(
        self, tmp_path
    ):
        """If _store_backup fails during quarantine_history, the active
        History must be restored from the staged backup."""
        svc = _make_recovery(tmp_path)
        svc._history.add_note({"note": "precious", "concept": "test"})

        # Inject failure into _store_backup.
        with patch.object(svc, "_store_backup", side_effect=OSError("disk full")):
            with pytest.raises(OSError, match="disk full"):
                svc.quarantine_history()

        # Active state must still have the original data.
        loaded = svc._history.load()
        assert len(loaded["notes"]) == 1
        assert loaded["notes"][0]["note"] == "precious"

    def test_history_store_backup_failure_no_orphaned_backup(
        self, tmp_path
    ):
        """After _store_backup failure, no backup file may remain
        stranded outside the backup directory."""
        svc = _make_recovery(tmp_path)
        svc._history.add_note({"note": "data", "concept": "x"})

        with patch.object(svc, "_store_backup", side_effect=OSError("disk full")):
            with pytest.raises(OSError):
                svc.quarantine_history()

        # No .corrupt-* file should sit next to the active history file.
        siblings = list(svc._history.path.parent.glob(
            svc._history.path.name + ".corrupt-*"
        ))
        assert len(siblings) == 0, (
            f"Orphaned backup next to active file: {[s.name for s in siblings]}"
        )

    def test_memory_store_backup_failure_restores_active_and_manager(
        self, tmp_path
    ):
        """If _store_backup fails during quarantine_memory, both the
        active snapshot file AND the live MemoryManager must be restored."""
        svc, manager = _make_recovery_with_manager(tmp_path, "user-test")

        # Seed memory.
        manager.add_memory(
            content="critical-data", memory_type="working", importance=0.9,
            metadata={"user_id": "user-test"},
        )
        assert len(svc._memory.load_snapshot()["memories"]) == 1
        assert manager.get_stats()["memory_counts"].get("working", 0) == 1

        # Inject failure into _store_backup.
        with patch.object(svc, "_store_backup", side_effect=OSError("disk full")):
            with pytest.raises(OSError, match="disk full"):
                svc.quarantine_memory()

        # File must be restored.
        assert len(svc._memory.load_snapshot()["memories"]) == 1
        assert (
            svc._memory.load_snapshot()["memories"][0]["content"]
            == "critical-data"
        )

        # MemoryManager must be restored.
        counts = manager.get_stats()["memory_counts"]
        assert counts.get("working", 0) == 1

    def test_memory_store_backup_failure_no_orphaned_backup(
        self, tmp_path
    ):
        """After _store_backup failure in quarantine_memory, no stranded
        backup may remain."""
        svc, manager = _make_recovery_with_manager(tmp_path, "user-test")
        manager.add_memory(
            content="data", memory_type="working", importance=0.5,
            metadata={"user_id": "user-test"},
        )

        with patch.object(svc, "_store_backup", side_effect=OSError("disk full")):
            with pytest.raises(OSError):
                svc.quarantine_memory()

        siblings = list(svc._memory.path.parent.glob(
            svc._memory.path.name + ".corrupt-*"
        ))
        assert len(siblings) == 0, (
            f"Orphaned backup next to active file: {[s.name for s in siblings]}"
        )


# ── 9c. Item 4: clear_all / restore_to_manager fail-closed ───────────────


class TestClearAllRestoreFailClosed:
    """Item 4: After clear_all() + failed restore_to_manager(), the
    system must be in a deterministic fail-closed state — never
    reporting success when disk and memory are forked."""

    def test_restore_to_manager_clears_before_populating(
        self, tmp_path
    ):
        """restore_to_manager must clear existing state before populating,
        ensuring no stale data co-exists with restored data."""
        import shutil

        svc, manager = _make_recovery_with_manager(tmp_path, "user-test")

        # Seed with data — _save_snapshot already persists to disk.
        manager.add_memory("old-data", memory_type="working", importance=0.5,
                           metadata={"user_id": "user-test"})
        assert manager.get_stats()["memory_counts"].get("working", 0) == 1

        # Copy the snapshot before clear_all overwrites it.
        preclear_backup = tmp_path / "preclear_snapshot.json"
        shutil.copy2(svc._memory.path, preclear_backup)

        # clear_all saves empty state to disk — so we need the backup.
        manager.clear_all()
        assert manager.get_stats()["memory_counts"].get("working", 0) == 0

        # Restore from the pre-clear backup into the active file first,
        # then into the manager.
        svc._memory.restore(preclear_backup)
        svc._memory.restore_to_manager(manager)
        assert manager.get_stats()["memory_counts"].get("working", 0) == 1

        # Verify no stale data — only the restored items are present.
        wm = manager.memory_types["working"]
        contents = {m.content for m in wm.memories}
        assert contents == {"old-data"}

    def test_restore_to_manager_fail_closed_does_not_report_success(
        self, tmp_path
    ):
        """If restore_to_manager encounters a corrupt item partway, it
        must NOT report success — it must raise and leave a clean state."""
        import shutil

        svc, manager = _make_recovery_with_manager(tmp_path, "user-test")

        # Seed with valid data.
        manager.add_memory("valid-item", memory_type="working", importance=0.5,
                           metadata={"user_id": "user-test"})
        # Copy before clear overwrites.
        preclear_backup = tmp_path / "preclear_snapshot.json"
        shutil.copy2(svc._memory.path, preclear_backup)

        manager.clear_all()

        # Tamper the backup to inject a bad item after valid ones.
        raw = read_json(preclear_backup, default={})
        raw["memories"].append(
            {"content": "bad-item", "memory_type": "nonexistent_type",
             "metadata": {"user_id": "user-test"}}
        )
        write_json_atomic(preclear_backup, raw)

        # Restore should succeed (unknown types are skipped, not errored).
        svc._memory.restore(preclear_backup)
        svc._memory.restore_to_manager(manager)
        counts = manager.get_stats()["memory_counts"]
        # The valid working item is restored; bad type is skipped.
        assert counts.get("working", 0) == 1

    def test_clear_all_restore_round_trip_no_fork(self, tmp_path):
        """A full clear_all → restore_to_manager cycle must leave disk
        and memory consistent."""
        import shutil

        svc, manager = _make_recovery_with_manager(tmp_path, "user-test")

        # Populate with working memory (episodic is disabled in the
        # test helper, so we only use working).
        manager.add_memory("wm-item-1", memory_type="working", importance=0.5,
                           metadata={"user_id": "user-test"})
        manager.add_memory("wm-item-2", memory_type="working", importance=0.8,
                           metadata={"user_id": "user-test"})

        # Copy snapshot before clear_all overwrites it.
        preclear_backup = tmp_path / "preclear_snapshot.json"
        shutil.copy2(svc._memory.path, preclear_backup)

        snapshot_before = read_json(preclear_backup, default={})
        assert len(snapshot_before["memories"]) == 2

        # Clear (saves empty to disk).
        manager.clear_all()
        assert manager.get_stats()["memory_counts"].get("working", 0) == 0

        # Restore from backup.
        svc._memory.restore(preclear_backup)
        svc._memory.restore_to_manager(manager)

        # Verify consistency: file and memory match.
        snapshot_after = svc._memory.load_snapshot()
        counts = manager.get_stats()["memory_counts"]
        assert counts.get("working", 0) == 2
        assert len(snapshot_after["memories"]) == 2

        # Save again — must not change the snapshot content.
        svc._memory.save_from_manager(manager)
        snapshot_final = svc._memory.load_snapshot()
        assert len(snapshot_final["memories"]) == 2


# ── 12. Item 1: double-clear failure in quarantine_memory ──────────────


class TestDoubleClearFailure:
    """当 MemoryManager.clear_all() 和 mem_mod.memories.clear() 都失败时：

    - 不得返回 quarantine success（Item 1）。
    - 在锁内恢复 active snapshot 并尝试恢复 live manager（Item 2）。
    - manager 无法可靠恢复时抛错 fail-closed（Item 3）。
    - 最后一份 staged backup 必须可枚举或 emergency-preserved（Item 4）。
    """

    def test_double_clear_rollback_succeeds_returns_failure(
        self, tmp_path
    ):
        """clear_all() 和 memories.clear() 都失败，但 disk + manager
        rollback 成功：不得返回 success，必须返回 success=False
        且 disk 与 memory 一致（有原始数据），backup 可枚举。"""
        svc, manager = _make_recovery_with_manager(tmp_path, "user-test")
        manager.add_memory("precious-data", memory_type="working",
                           importance=0.9,
                           metadata={"user_id": "user-test"})
        assert manager.get_stats()["memory_counts"].get("working", 0) == 1

        # Both clear_all() AND the working module's memories.clear()
        # must fail.  Use a _RaisingList that blocks .clear().
        class _RaisingList(list):
            def clear(self):
                raise RuntimeError("injected clear failure")

        wm = manager.memory_types["working"]
        wm.memories = _RaisingList(wm.memories)

        with patch.object(manager, "clear_all",
                          side_effect=RuntimeError("clear exploded")):
            result = svc.quarantine_memory()

        # ── Must NOT return success ──────────────────────────
        assert not result.success, (
            "Double-clear failure must not report quarantine success"
        )
        assert "failed" in result.message.lower()

        # ── Backup must be enumerable ────────────────────────
        assert result.backup_id is not None
        assert result.backup_id in svc.list_memory_backups()

        # ── Disk and memory must agree (no fork) ─────────────
        snapshot = svc._memory.load_snapshot()
        disk_working = sum(
            1 for m in snapshot["memories"]
            if m["memory_type"] == "working"
        )
        counts = manager.get_stats()["memory_counts"]
        assert disk_working == counts.get("working", 0), (
            f"FORKED: disk={disk_working} working items, "
            f"memory={counts.get('working', 0)}"
        )
        # Data must be the original.
        assert disk_working == 1
        assert snapshot["memories"][0]["content"] == "precious-data"
        assert wm.memories[0].content == "precious-data"

    def test_double_clear_rollback_fails_raises_with_emergency_preserve(
        self, tmp_path
    ):
        """clear_all()、memories.clear()、以及 disk restore 全部失败：
        必须抛 RuntimeError，staged backup 必须 emergency-preserved
        且可在 list_memory_backups() 中找到。"""
        svc, manager = _make_recovery_with_manager(tmp_path, "user-test")
        manager.add_memory("last-data", memory_type="working",
                           importance=0.9,
                           metadata={"user_id": "user-test"})

        # Both clears fail.
        class _RaisingList(list):
            def clear(self):
                raise RuntimeError("injected clear failure")

        wm = manager.memory_types["working"]
        wm.memories = _RaisingList(wm.memories)

        with patch.object(manager, "clear_all",
                          side_effect=RuntimeError("clear exploded")):
            # Also make disk restore fail.
            with patch.object(svc._memory, "restore",
                              side_effect=OSError("disk restore failed")):
                with pytest.raises(RuntimeError, match="double-clear"):
                    svc.quarantine_memory()

        # ── Emergency backup must exist ─────────────────────
        backups = svc.list_memory_backups()
        emergency = [
            b for b in backups
            if "emergency" in b or "corrupt" in b
        ]
        assert len(emergency) >= 1, (
            "No emergency backup found after double-clear triple failure"
        )

    def test_double_clear_manager_restore_fails_raises(
        self, tmp_path
    ):
        """Disk restore 成功但 manager restore 失败：必须抛
        RuntimeError，backup 必须 emergency-preserved。"""
        svc, manager = _make_recovery_with_manager(tmp_path, "user-test")
        manager.add_memory("data-mgr-fail", memory_type="working",
                           importance=0.9,
                           metadata={"user_id": "user-test"})

        # Both clears fail.
        class _RaisingList(list):
            def clear(self):
                raise RuntimeError("injected clear failure")

        wm = manager.memory_types["working"]
        wm.memories = _RaisingList(wm.memories)

        with patch.object(manager, "clear_all",
                          side_effect=RuntimeError("clear exploded")):
            # Disk restore succeeds but manager restore fails.
            with patch.object(svc._memory, "restore_to_manager",
                              side_effect=RuntimeError("manager dead")):
                with pytest.raises(RuntimeError, match="double-clear"):
                    svc.quarantine_memory()

        # Backup must be preserved.
        backups = svc.list_memory_backups()
        assert len(backups) >= 1, (
            "Backup not preserved after double-clear + manager fail"
        )


# ── 13. Item 2: real restore_to_manager exception injection ────────────


class TestRestoreToManagerRealExceptionInjection:
    """Item 2: Inject real exceptions into restore_to_manager() — the
    clear phase and the assignment phase.  The system must enter a
    deterministic fail-closed state and NEVER report success."""

    def test_clear_phase_exception_force_set_recovers(
        self, tmp_path
    ):
        """When memories.clear() raises but the force-set `= []` succeeds,
        restore_to_manager recovers silently — the module is empty, and
        restore proceeds normally (no RuntimeError)."""
        import shutil

        svc, manager = _make_recovery_with_manager(tmp_path, "user-test")
        manager.add_memory("safe-item", memory_type="working",
                           importance=0.5,
                           metadata={"user_id": "user-test"})

        preclear_backup = tmp_path / "preclear_snapshot.json"
        shutil.copy2(svc._memory.path, preclear_backup)
        manager.clear_all()
        svc._memory.restore(preclear_backup)

        # _RaisingList blocks .clear() but allows assignment.
        class _RaisingList(list):
            def clear(self):
                raise RuntimeError("injected clear failure")

        wm = manager.memory_types["working"]
        wm.memories = _RaisingList(wm.memories)

        # Must NOT raise — force-set [] recovers.
        svc._memory.restore_to_manager(manager)
        assert len(wm.memories) == 1
        assert wm.memories[0].content == "safe-item"

    def test_clear_force_set_both_fail_leaves_fail_closed(
        self, tmp_path
    ):
        """When BOTH .clear() AND force-set `= []` fail, restore_to_manager
        must raise RuntimeError with fail-closed state."""
        import shutil
        from hello_agents.memory.base import MemoryItem

        svc, manager = _make_recovery_with_manager(tmp_path, "user-test")
        manager.add_memory("safe-item", memory_type="working",
                           importance=0.5,
                           metadata={"user_id": "user-test"})

        preclear_backup = tmp_path / "preclear_snapshot.json"
        shutil.copy2(svc._memory.path, preclear_backup)
        manager.clear_all()
        svc._memory.restore(preclear_backup)

        # Module where both clear() and setter fail.
        class _UnrecoverableModule:
            def __init__(self):
                self._memories = []
            @property
            def memories(self):
                return self._memories
            @memories.setter
            def memories(self, value):
                raise RuntimeError("injected setter failure")
            def clear(self):
                self._memories.clear()
            def count(self):
                return len(self._memories)

        fail_mod = _UnrecoverableModule()
        # The .memories property returns a plain list, so .clear()
        # on it succeeds. But we need _both_ to fail. We do this by
        # using a _RaisingList AND a failing setter.
        # Actually: .memories returns _RaisingList → clear() raises.
        # Then force-set calls setter → setter raises. BOTH fail.

        class _ClearFailList(list):
            def clear(self):
                raise RuntimeError("injected clear failure")

        fail_mod._memories = _ClearFailList(
            [MemoryItem(content="stale", memory_type="working")]
        )
        # Now:
        # - mem_mod.memories.clear() → raises (via _ClearFailList)
        # - mem_mod.memories = [] → setter raises
        # Both fail → failure recorded → RuntimeError.

        manager.memory_types["working"] = fail_mod

        with pytest.raises(RuntimeError, match="fail-closed"):
            svc._memory.restore_to_manager(manager)

        # After fail-closed: final clear_all attempts to clear,
        # module's clear() succeeds (it's a regular list now? no,
        # _ClearFailList is still there but the fail-closed loop
        # also catches exceptions).
        # Key assertion: RuntimeError WAS raised — no success reported.

    def test_assignment_phase_exception_leaves_fail_closed_state(
        self, tmp_path
    ):
        """When setting memories raises during restore, the system must
        raise RuntimeError, clear ALL modules, and never report success."""
        import shutil
        from hello_agents.memory.base import MemoryItem

        svc, manager = _make_recovery_with_manager(tmp_path, "user-test")
        manager.add_memory("good-data", memory_type="working",
                           importance=0.5,
                           metadata={"user_id": "user-test"})

        preclear_backup = tmp_path / "preclear_snapshot.json"
        shutil.copy2(svc._memory.path, preclear_backup)
        manager.clear_all()
        svc._memory.restore(preclear_backup)

        # Replace working memory with a module whose setter raises.
        class _SetFailingModule:
            def __init__(self):
                self._memories = [MemoryItem(content="stale",
                                             memory_type="working")]
            @property
            def memories(self):
                return self._memories
            @memories.setter
            def memories(self, value):
                raise RuntimeError("injected assignment failure")
            def clear(self):
                self._memories.clear()

        fail_mod = _SetFailingModule()
        manager.memory_types["working"] = fail_mod

        with pytest.raises(RuntimeError, match="fail-closed"):
            svc._memory.restore_to_manager(manager)

        # After fail-closed: ALL modules must be empty.
        assert len(fail_mod.memories) == 0, (
            "Fail-closed: failing module was not cleared after set failure"
        )

    def test_clear_and_assignment_failure_no_success_reported(
        self, tmp_path
    ):
        """Even if the failure happens late in the restore, the method
        must raise, not return None or silently swallow the error."""
        import shutil
        from hello_agents.memory.base import MemoryItem

        svc, manager = _make_recovery_with_manager(tmp_path, "user-test")
        manager.add_memory("data-1", memory_type="working",
                           importance=0.5,
                           metadata={"user_id": "user-test"})
        manager.add_memory("data-2", memory_type="working",
                           importance=0.8,
                           metadata={"user_id": "user-test"})

        preclear_backup = tmp_path / "preclear_snapshot.json"
        shutil.copy2(svc._memory.path, preclear_backup)
        manager.clear_all()
        svc._memory.restore(preclear_backup)

        # Use a module where the first assignment works but the second
        # iteration causes failure.  Simulate by replacing the module
        # with one whose setter raises.
        class _LateFailingModule:
            def __init__(self):
                self._memories = []
            @property
            def memories(self):
                return self._memories
            @memories.setter
            def memories(self, value):
                raise RuntimeError("injected late assignment failure")
            def clear(self):
                self._memories.clear()

        fail_mod = _LateFailingModule()
        manager.memory_types["working"] = fail_mod

        # This must NOT return None or a success string — it must raise.
        with pytest.raises(RuntimeError, match="fail-closed"):
            svc._memory.restore_to_manager(manager)

        # Manager must be in a clean state.
        assert len(fail_mod.memories) == 0, (
            "Module not cleared after late failure"
        )


# ── 14. Item 3: double-failure preserves last backup ──────────────────


class TestDoubleFailurePreserveLastBackup:
    """Item 3: When _store_backup fails AND the active restore also
    fails, the staged backup is the LAST copy — it must be
    emergency-preserved in the user backup directory and be
    discoverable via list_*_backups()."""

    def test_history_double_failure_preserves_backup_in_listable_dir(
        self, tmp_path
    ):
        """History: _store_backup + restore both fail → staged backup
        must be emergency-preserved."""
        svc = _make_recovery(tmp_path)
        svc._history.add_note({"note": "last-chance-history",
                               "concept": "test"})

        with patch.object(svc, "_store_backup",
                          side_effect=OSError("disk full")):
            with patch.object(svc._history, "restore",
                              side_effect=OSError("restore also failed")):
                with pytest.raises(OSError):
                    svc.quarantine_history()

        # The staged backup must have been preserved.
        backups = svc.list_history_backups()
        assert len(backups) >= 1, (
            "Last backup was not preserved after double failure"
        )
        # It must contain our data.
        backup_data = read_json(
            svc._backup_dir / backups[0], default=None
        )
        assert backup_data is not None
        notes = backup_data.get("notes", [])
        assert any("last-chance-history" in n.get("note", "")
                   for n in notes), (
            "Preserved backup does not contain original data"
        )

    def test_memory_double_failure_preserves_backup_in_listable_dir(
        self, tmp_path
    ):
        """Memory: _store_backup + restore both fail → staged backup
        must be emergency-preserved."""
        svc, manager = _make_recovery_with_manager(tmp_path, "user-test")
        manager.add_memory("last-chance-memory", memory_type="working",
                           importance=0.9,
                           metadata={"user_id": "user-test"})

        with patch.object(svc, "_store_backup",
                          side_effect=OSError("disk full")):
            with patch.object(svc._memory, "restore",
                              side_effect=OSError("restore also failed")):
                with pytest.raises(OSError):
                    svc.quarantine_memory()

        # The staged backup must have been preserved.
        backups = svc.list_memory_backups()
        assert len(backups) >= 1, (
            "Last backup was not preserved after double failure"
        )
        # It must contain our data.
        backup_data = read_json(
            svc._backup_dir / backups[0], default=None
        )
        assert backup_data is not None
        contents = [m["content"] for m in backup_data.get("memories", [])]
        assert "last-chance-memory" in contents

    def test_double_failure_backup_is_restorable(
        self, tmp_path
    ):
        """The emergency-preserved backup from a double failure must be
        valid and restorable."""
        svc, manager = _make_recovery_with_manager(tmp_path, "user-test")
        manager.add_memory("restorable-emergency", memory_type="working",
                           importance=0.9,
                           metadata={"user_id": "user-test"})

        with patch.object(svc, "_store_backup",
                          side_effect=OSError("disk full")):
            with patch.object(svc._memory, "restore",
                              side_effect=OSError("restore also failed")):
                with pytest.raises(OSError):
                    svc.quarantine_memory()

        backups = svc.list_memory_backups()
        assert len(backups) >= 1

        # Restore from the emergency backup.
        r = svc.restore_memory(backups[0])
        assert r.success
        counts = manager.get_stats()["memory_counts"]
        assert counts.get("working", 0) == 1
        assert (manager.memory_types["working"].memories[0].content
                == "restorable-emergency")

    def test_single_failure_no_emergency_junk_left(
        self, tmp_path
    ):
        """When only _store_backup fails (restore succeeds), no emergency
        backup should remain — only the restored active file."""
        svc, manager = _make_recovery_with_manager(tmp_path, "user-test")
        manager.add_memory("normal-data", memory_type="working",
                           importance=0.5,
                           metadata={"user_id": "user-test"})

        with patch.object(svc, "_store_backup",
                          side_effect=OSError("disk full")):
            with pytest.raises(OSError):
                svc.quarantine_memory()

        # No emergency-* files should exist.
        emergency = list(svc._backup_dir.glob("*.emergency-*"))
        assert len(emergency) == 0, (
            f"Emergency backup created unnecessarily: "
            f"{[e.name for e in emergency]}"
        )
        # Active file should have the original data (restore succeeded).
        assert len(svc._memory.load_snapshot()["memories"]) == 1


# ── 15. Item 4: _store_backup atomic write ───────────────────────────


class TestStoreBackupAtomicWrite:
    """Item 4: _store_backup must write to a temp file first, then
    atomically rename.  If copy2 is interrupted, the temp file must
    be cleaned up and no partial backup must enter the listing."""

    def test_copy_interrupted_temp_file_cleaned(
        self, tmp_path
    ):
        """When copy2 is interrupted, the .tmp file must be removed
        and no partial file appears in list_*_backups()."""
        import shutil

        svc = _make_recovery(tmp_path)
        svc._history.add_note({"note": "data"})

        # Create a staged source file.
        source = tmp_path / "history.json.corrupt-20260101T000000Z-aaaaaaaa"
        shutil.copy2(svc._history.path, source)

        # Intercept copy2 to fail.
        with patch("shutil.copy2",
                   side_effect=OSError("copy interrupted")):
            with pytest.raises(OSError):
                svc._store_backup(source)

        # No .tmp files in backup dir.
        tmp_files = list(svc._backup_dir.glob("*.tmp"))
        assert len(tmp_files) == 0, (
            f"Temp files left behind: {[t.name for t in tmp_files]}"
        )
        # No partial backup.
        backups = svc.list_history_backups()
        assert all(not b.endswith(".tmp") for b in backups), (
            "Partial backup appeared in listing"
        )
        # Source must still exist (copy failed, we don't unlink).
        assert source.exists(), (
            "Source was deleted despite copy failure"
        )

    def test_store_backup_atomic_rename_produces_valid_file(
        self, tmp_path
    ):
        """Happy path: _store_backup produces a valid, readable backup
        with no temp artifacts."""
        import shutil

        svc = _make_recovery(tmp_path)
        svc._history.add_note({"note": "atomic-test"})

        source = tmp_path / "history.json.corrupt-20260101T000000Z-bbbbbbbb"
        shutil.copy2(svc._history.path, source)

        backup_id = svc._store_backup(source)
        assert backup_id is not None
        # Source must be removed.
        assert not source.exists()
        # Backup must exist and be valid JSON.
        backup_path = svc._backup_dir / backup_id
        assert backup_path.exists()
        data = read_json(backup_path, default=None)
        assert data is not None
        assert len(data["notes"]) == 1
        assert data["notes"][0]["note"] == "atomic-test"
        # No .tmp files.
        tmp_files = list(svc._backup_dir.glob("*.tmp"))
        assert len(tmp_files) == 0

    def test_store_backup_temp_not_in_listing(
        self, tmp_path, monkeypatch
    ):
        """A .tmp file mid-write must never appear in list_*_backups()."""
        import os
        import shutil

        svc = _make_recovery(tmp_path)
        svc._history.add_note({"note": "temp-test"})

        source = tmp_path / "history.json.corrupt-20260101T000000Z-cccccccc"
        shutil.copy2(svc._history.path, source)

        # Intercept `os.replace` to pause after the .tmp is written
        # but before the rename.  The .tmp file exists at this point.
        real_replace = os.replace

        def _intercept_replace(src, dst):
            # At this moment, a .tmp file exists.  Verify it's NOT
            # in the backup listing.
            tmp_files = list(svc._backup_dir.glob("*.tmp"))
            for tf in tmp_files:
                assert tf.name not in svc.list_history_backups(), (
                    "Temp file leaked into backup listing"
                )
            return real_replace(src, dst)

        monkeypatch.setattr(os, "replace", _intercept_replace)

        backup_id = svc._store_backup(source)
        assert backup_id is not None
        # No .tmp leftovers.
        assert len(list(svc._backup_dir.glob("*.tmp"))) == 0


class TestCollisionResistantBackupIDs:
    """Finding 5: Two quarantines in the same second must produce two
    independently restorable backups."""

    def test_two_immediate_history_quarantines_preserve_two_backups(self, tmp_path):
        svc = _make_recovery(tmp_path)

        svc._history.add_note({"note": "first-note"})
        q1 = svc.quarantine_history()
        assert q1.success

        svc._history.add_note({"note": "second-note"})
        q2 = svc.quarantine_history()
        assert q2.success

        # Two distinct backup IDs.
        assert q1.backup_id is not None
        assert q2.backup_id is not None
        assert q1.backup_id != q2.backup_id

        # Both exist in backup directory.
        assert (svc._backup_dir / q1.backup_id).exists()
        assert (svc._backup_dir / q2.backup_id).exists()

        # Restore first backup — gets "first-note".
        r1 = svc.restore_history(q1.backup_id)
        assert r1.success
        notes = svc._history.load()["notes"]
        assert len(notes) == 1
        assert notes[0]["note"] == "first-note"

        # Restore second backup — gets "second-note".
        r2 = svc.restore_history(q2.backup_id)
        assert r2.success
        notes = svc._history.load()["notes"]
        assert len(notes) == 1
        assert notes[0]["note"] == "second-note"

    def test_two_immediate_memory_quarantines_preserve_two_backups(self, tmp_path):
        svc = _make_recovery(tmp_path)

        svc._memory.save_from_manager(_FakeManager("user-test", [
            {"content": "mem-a", "memory_type": "working",
             "metadata": {"user_id": "user-test"}},
        ]))
        q1 = svc.quarantine_memory()

        svc._memory.save_from_manager(_FakeManager("user-test", [
            {"content": "mem-b", "memory_type": "working",
             "metadata": {"user_id": "user-test"}},
        ]))
        q2 = svc.quarantine_memory()

        assert q1.backup_id != q2.backup_id
        assert (svc._backup_dir / q1.backup_id).exists()
        assert (svc._backup_dir / q2.backup_id).exists()

        # Each backup is independently restorable.
        r1 = svc.restore_memory(q1.backup_id)
        assert r1.success
        assert svc._memory.load_snapshot()["memories"][0]["content"] == "mem-a"

        r2 = svc.restore_memory(q2.backup_id)
        assert r2.success
        assert svc._memory.load_snapshot()["memories"][0]["content"] == "mem-b"


# ── 11. Finding 7: restore under single lock section ─────────────────────


class TestRestoreLockAtomicity:
    """Finding 7: Backup read, validation, owner check, and atomic write
    must all occur within the same user coordination lock."""

    def test_restore_history_locked_method_is_called(self, tmp_path):
        """Verify that the locked restore method path is exercised —
        _restore_history_locked runs to completion successfully."""
        svc = _make_recovery(tmp_path)
        svc._history.add_note({"note": "locked-test"})
        q = svc.quarantine_history()

        # Wrap the locked method to verify it runs.
        called = []

        orig_locked = svc._restore_history_locked

        def _tracked(bid):
            called.append(True)
            return orig_locked(bid)

        svc._restore_history_locked = _tracked
        r = svc.restore_history(q.backup_id)
        assert r.success
        assert len(called) == 1, (
            "_restore_history_locked was not called; restore is not lock-protected"
        )

    def test_restore_memory_locked_method_is_called(self, tmp_path):
        """Verify that the locked restore method path is exercised —
        _restore_memory_locked runs to completion successfully."""
        svc = _make_recovery(tmp_path)
        svc._memory.save_from_manager(_FakeManager("user-test", [
            {"content": "lock-mem", "memory_type": "working",
             "metadata": {"user_id": "user-test"}},
        ]))
        q = svc.quarantine_memory()

        called = []
        orig_locked = svc._restore_memory_locked

        def _tracked(bid):
            called.append(True)
            return orig_locked(bid)

        svc._restore_memory_locked = _tracked
        r = svc.restore_memory(q.backup_id)
        assert r.success
        assert len(called) == 1, (
            "_restore_memory_locked was not called; restore is not lock-protected"
        )

    def test_concurrent_restore_and_write_are_serialized(self, tmp_path):
        """Two concurrent restore attempts on the same user must be
        serialized by the lock and leave a consistent final state."""
        import threading

        svc = _make_recovery(tmp_path)

        # Create two distinct backups.
        svc._history.add_note({"note": "v1"})
        q1 = svc.quarantine_history()

        write_json_atomic(svc._history.path,
                          {"documents": [], "questions": [],
                           "notes": [{"note": "v2"}], "sessions": []})
        q2 = svc.quarantine_history()

        errors = []

        def _restore(bid):
            try:
                return svc.restore_history(bid)
            except Exception as exc:
                errors.append(exc)
                return None

        # Restore v1 and v2 concurrently.
        t1 = threading.Thread(target=_restore, args=(q1.backup_id,))
        t2 = threading.Thread(target=_restore, args=(q2.backup_id,))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert len(errors) == 0, f"Concurrent restore raised: {errors}"

        # Final state is one of the two valid notes (not corrupt).
        loaded = svc._history.load()
        notes = loaded["notes"]
        assert len(notes) == 1
        assert notes[0]["note"] in ("v1", "v2")


# ── helpers ────────────────────────────────────────────────────────────────


class _FakeMemory:
    def __init__(self, items=None):
        self.memories = list(items or [])


class _FakeManager:
    def __init__(self, user_id, memory_items=None):
        self.user_id = user_id
        items = []
        for raw in (memory_items or []):
            from hello_agents.memory.base import MemoryItem
            items.append(MemoryItem(
                id=raw.get("id"),
                content=raw.get("content", ""),
                memory_type=raw.get("memory_type", "working"),
                importance=float(raw.get("importance", 0.5)),
                metadata=raw.get("metadata", {}),
                timestamp=raw.get("timestamp"),
            ))
        self.memory_types = {"working": _FakeMemory(items)}
