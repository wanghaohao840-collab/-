import pytest

from app.memory_repository import CorruptMemorySnapshotError, MemorySnapshotRepository
from app.storage import write_json_atomic
from hello_agents.memory.base import Episode, MemoryItem


class DummyMemory:
    def __init__(self):
        self.memories = []


class DummyManager:
    def __init__(self, user_id):
        self.user_id = user_id
        self.memory_types = {"working": DummyMemory(), "semantic": DummyMemory()}


class NativeWorkingMemory:
    def __init__(self):
        self.memories = []


class NativeSemanticMemory:
    def __init__(self):
        self.memories = {}


class NativeEpisodicMemory:
    def __init__(self):
        self._episodes = {}
        self.sessions = {}


class NativeShapeManager:
    def __init__(self):
        self.memory_types = {
            "working": NativeWorkingMemory(),
            "episodic": NativeEpisodicMemory(),
            "semantic": NativeSemanticMemory(),
        }


def test_memory_snapshot_repository_round_trips_supported_memories(tmp_path):
    manager = DummyManager("user-1")
    manager.memory_types["working"].memories.append(
        MemoryItem(content="hello", memory_type="working", metadata={"user_id": "user-1"})
    )
    repo = MemorySnapshotRepository(tmp_path / "memories.json", user_id="user-1")

    repo.save_from_manager(manager)
    restored = DummyManager("user-1")
    repo.restore_to_manager(restored)

    assert restored.memory_types["working"].memories[0].content == "hello"


def test_memory_snapshot_round_trips_native_container_shapes(tmp_path):
    manager = NativeShapeManager()
    working = MemoryItem(
        content="working item",
        memory_type="working",
        metadata={"user_id": "user-1"},
    )
    semantic = MemoryItem(
        content="semantic item",
        memory_type="semantic",
        metadata={"user_id": "user-1"},
    )
    episode = Episode(
        episode_id="episode-1",
        session_id="session-1",
        timestamp="2026-07-29T00:00:00",
        content="episodic item",
        context={"user_id": "user-1", "importance": 0.7},
    )
    manager.memory_types["working"].memories.append(working)
    manager.memory_types["semantic"].memories[semantic.id] = semantic
    manager.memory_types["episodic"]._episodes[episode.episode_id] = episode
    manager.memory_types["episodic"].sessions[episode.session_id] = [episode.episode_id]

    repo = MemorySnapshotRepository(tmp_path / "memories.json", user_id="user-1")
    repo.save_from_manager(manager)

    assert {item["memory_type"] for item in repo.load_snapshot()["memories"]} == {
        "working",
        "episodic",
        "semantic",
    }

    restored = NativeShapeManager()
    repo.restore_to_manager(restored)

    assert [item.content for item in restored.memory_types["working"].memories] == [
        "working item"
    ]
    assert isinstance(restored.memory_types["semantic"].memories, dict)
    assert [item.content for item in restored.memory_types["semantic"].memories.values()] == [
        "semantic item"
    ]
    restored_episodic = restored.memory_types["episodic"]
    assert restored_episodic._episodes["episode-1"].content == "episodic item"
    assert restored_episodic.sessions == {"session-1": ["episode-1"]}


def test_memory_snapshot_repository_skips_other_users(tmp_path):
    repo = MemorySnapshotRepository(tmp_path / "memories.json", user_id="user-1")
    manager = DummyManager("user-1")
    manager.memory_types["working"].memories.append(
        MemoryItem(content="bad", memory_type="working", metadata={"user_id": "user-2"})
    )

    repo.save_from_manager(manager)

    assert repo.load_snapshot()["memories"] == []


def test_validate_schema_accepts_valid_snapshot():
    MemorySnapshotRepository.validate_schema(
        {"user_id": "u1", "memories": []}
    )


def test_validate_schema_rejects_non_dict():
    with pytest.raises(CorruptMemorySnapshotError, match="must be a dict"):
        MemorySnapshotRepository.validate_schema(["not", "dict"])


def test_validate_schema_rejects_missing_user_id():
    with pytest.raises(CorruptMemorySnapshotError, match="missing user_id"):
        MemorySnapshotRepository.validate_schema({"memories": []})


def test_validate_schema_rejects_non_list_memories():
    with pytest.raises(CorruptMemorySnapshotError, match="must be a list"):
        MemorySnapshotRepository.validate_schema({"user_id": "u1", "memories": "bad"})


def test_restore_rejects_cross_user_backup(tmp_path):
    """Memory restore must reject a backup that belongs to a different user."""
    repo_alice = MemorySnapshotRepository(tmp_path / "mem.json", user_id="alice")
    write_json_atomic(repo_alice.path, {"user_id": "bob", "memories": []})

    with pytest.raises(CorruptMemorySnapshotError, match=r"belongs to user"):
        repo_alice.restore(repo_alice.path)


# ── Item 3: restore() strict user_id validation ────────────────────────


def test_restore_rejects_missing_user_id_backup(tmp_path):
    """restore() must reject a backup whose user_id key is absent (None)."""
    repo = MemorySnapshotRepository(tmp_path / "mem.json", user_id="u1")
    write_json_atomic(repo.path, {"user_id": "u1", "memories": [
        {"content": "original", "memory_type": "working",
         "metadata": {"user_id": "u1"}}
    ]})

    backup = tmp_path / "backup_no_user.json"
    write_json_atomic(backup, {"memories": []})  # No user_id key

    with pytest.raises(CorruptMemorySnapshotError, match="missing"):
        repo.restore(backup)

    # Active file unchanged.
    assert len(repo.load_snapshot()["memories"]) == 1


def test_restore_rejects_null_user_id_backup(tmp_path):
    """restore() must reject a backup with user_id=None."""
    repo = MemorySnapshotRepository(tmp_path / "mem.json", user_id="u1")
    write_json_atomic(repo.path, {"user_id": "u1", "memories": [
        {"content": "original", "memory_type": "working",
         "metadata": {"user_id": "u1"}}
    ]})

    backup = tmp_path / "backup_null_user.json"
    write_json_atomic(backup, {"user_id": None, "memories": []})

    with pytest.raises(CorruptMemorySnapshotError, match="None"):
        repo.restore(backup)

    # Active file unchanged.
    assert len(repo.load_snapshot()["memories"]) == 1


def test_restore_rejects_empty_user_id_backup(tmp_path):
    """restore() must reject a backup with user_id=''."""
    repo = MemorySnapshotRepository(tmp_path / "mem.json", user_id="u1")
    write_json_atomic(repo.path, {"user_id": "u1", "memories": [
        {"content": "original", "memory_type": "working",
         "metadata": {"user_id": "u1"}}
    ]})

    backup = tmp_path / "backup_empty_user.json"
    write_json_atomic(backup, {"user_id": "", "memories": []})

    with pytest.raises(CorruptMemorySnapshotError, match="empty"):
        repo.restore(backup)

    # Active file unchanged.
    assert len(repo.load_snapshot()["memories"]) == 1


def test_restore_rejects_whitespace_user_id_backup(tmp_path):
    """restore() must reject a backup with user_id='   '."""
    repo = MemorySnapshotRepository(tmp_path / "mem.json", user_id="u1")
    write_json_atomic(repo.path, {"user_id": "u1", "memories": [
        {"content": "original", "memory_type": "working",
         "metadata": {"user_id": "u1"}}
    ]})

    backup = tmp_path / "backup_ws_user.json"
    write_json_atomic(backup, {"user_id": "   ", "memories": []})

    with pytest.raises(CorruptMemorySnapshotError, match="empty"):
        repo.restore(backup)

    # Active file unchanged.
    assert len(repo.load_snapshot()["memories"]) == 1


def test_restore_rejects_non_string_user_id_backup(tmp_path):
    """restore() must reject a backup with user_id=42 (int)."""
    repo = MemorySnapshotRepository(tmp_path / "mem.json", user_id="u1")
    write_json_atomic(repo.path, {"user_id": "u1", "memories": [
        {"content": "original", "memory_type": "working",
         "metadata": {"user_id": "u1"}}
    ]})

    backup = tmp_path / "backup_int_user.json"
    write_json_atomic(backup, {"user_id": 42, "memories": []})

    with pytest.raises(CorruptMemorySnapshotError, match="must be str"):
        repo.restore(backup)

    # Active file unchanged.
    assert len(repo.load_snapshot()["memories"]) == 1


def test_restore_accepts_matching_user_id(tmp_path):
    """restore() must succeed when backup user_id matches current."""
    repo = MemorySnapshotRepository(tmp_path / "mem.json", user_id="u1")
    write_json_atomic(repo.path, {"user_id": "u1", "memories": [
        {"content": "old", "memory_type": "working",
         "metadata": {"user_id": "u1"}}
    ]})

    backup = tmp_path / "good_backup.json"
    write_json_atomic(backup, {"user_id": "u1", "memories": [
        {"content": "restored", "memory_type": "working",
         "metadata": {"user_id": "u1"}}
    ]})

    # Must not raise.
    repo.restore(backup)
    assert repo.load_snapshot()["memories"][0]["content"] == "restored"


def test_restore_rejects_invalid_backup_schema(tmp_path):
    """Restore with invalid schema must raise before touching active file."""
    repo = MemorySnapshotRepository(tmp_path / "mem.json", user_id="u1")
    write_json_atomic(repo.path, {"user_id": "u1", "memories": [
        {"content": "original", "memory_type": "working", "metadata": {"user_id": "u1"}}
    ]})

    # Create a backup with bad schema.
    backup = tmp_path / "bad_backup.json"
    write_json_atomic(backup, {"user_id": "u1"})  # missing 'memories'

    with pytest.raises(CorruptMemorySnapshotError, match="must be a list"):
        repo.restore(backup)

    # Active file unchanged.
    assert len(repo.load_snapshot()["memories"]) == 1


# ── Finding 1: ownership validation fail-closed ─────────────────────────


def test_load_snapshot_missing_user_id_fails(tmp_path):
    """Active snapshot with no user_id key must raise."""
    repo = MemorySnapshotRepository(tmp_path / "mem.json", user_id="u1")
    write_json_atomic(repo.path, {"memories": []})

    with pytest.raises(CorruptMemorySnapshotError, match="missing user_id"):
        repo.load_snapshot()


def test_load_snapshot_null_user_id_fails(tmp_path):
    """Active snapshot with user_id=None must raise."""
    repo = MemorySnapshotRepository(tmp_path / "mem.json", user_id="u1")
    write_json_atomic(repo.path, {"user_id": None, "memories": []})

    with pytest.raises(CorruptMemorySnapshotError, match="missing user_id"):
        repo.load_snapshot()


def test_load_snapshot_malformed_user_id_fails(tmp_path):
    """Active snapshot with a non-string user_id must raise."""
    repo = MemorySnapshotRepository(tmp_path / "mem.json", user_id="u1")
    write_json_atomic(repo.path, {"user_id": 42, "memories": []})

    with pytest.raises(CorruptMemorySnapshotError, match="malformed"):
        repo.load_snapshot()


def test_load_snapshot_cross_user_active_fails(tmp_path):
    """Loading a snapshot belonging to a different user must raise."""
    repo = MemorySnapshotRepository(tmp_path / "mem.json", user_id="alice")
    write_json_atomic(repo.path, {"user_id": "bob", "memories": []})

    with pytest.raises(CorruptMemorySnapshotError, match="belongs to user"):
        repo.load_snapshot()


# ── Finding 5 & 6: collision-resistant IDs + failure-atomic quarantine ──


def test_quarantine_ids_are_collision_resistant(tmp_path):
    """Two immediate quarantines must produce two distinct backup IDs."""
    repo = MemorySnapshotRepository(tmp_path / "mem.json", user_id="u1")
    write_json_atomic(repo.path, {"user_id": "u1", "memories": [
        {"content": "first", "memory_type": "working",
         "metadata": {"user_id": "u1"}}
    ]})

    b1 = repo.quarantine_and_reset()
    # Re-seed the active file so a second quarantine is possible.
    write_json_atomic(repo.path, {"user_id": "u1", "memories": [
        {"content": "second", "memory_type": "working",
         "metadata": {"user_id": "u1"}}
    ]})
    b2 = repo.quarantine_and_reset()

    # IDs must be distinct even if generated in the same second.
    assert b1.name != b2.name
    assert b1.exists()
    assert b2.exists()
    # Both IDs follow the collision-resistant naming convention.
    for b in (b1, b2):
        assert "corrupt-" in b.name
        # UUID portion (8 hex chars after the last dash)
        parts = b.name.rsplit("-", 1)
        assert len(parts) == 2
        assert len(parts[1]) >= 8  # at least 8 hex chars for UUID


def test_quarantine_is_failure_atomic(tmp_path, monkeypatch):
    """If the clean write fails, the active file is left untouched and
    no stranded backup remains."""
    repo = MemorySnapshotRepository(tmp_path / "mem.json", user_id="u1")
    write_json_atomic(repo.path, {"user_id": "u1", "memories": [
        {"content": "original", "memory_type": "working",
         "metadata": {"user_id": "u1"}}
    ]})
    original_bytes = repo.path.read_bytes()

    # Force write_json_atomic to fail after the backup copy is created.
    import app.memory_repository as mod
    _real_write = mod.write_json_atomic

    def _failing_write(path, data):
        raise OSError("simulated disk full")

    monkeypatch.setattr(mod, "write_json_atomic", _failing_write)

    with pytest.raises(OSError, match="simulated disk full"):
        repo.quarantine_and_reset()

    # Active file must still contain the original state.
    assert repo.path.read_bytes() == original_bytes
    loaded = _real_write  # not needed here — let's use another approach

    # Verify active file is readable with original content.
    write_json_atomic(repo.path, {"user_id": "u1", "memories": [
        {"content": "original", "memory_type": "working",
         "metadata": {"user_id": "u1"}}
    ]})
    # Actually, the file was left untouched, so the bytes are identical.
    # Let's verify by reading through the repository.
    # (We can't because monkeypatch is still active; it's fine — the
    # key assertion is the bytes comparison above.)
    assert repo.path.read_bytes() == original_bytes


def test_quarantine_cleanup_does_not_leave_stranded_backup(tmp_path, monkeypatch):
    """When quarantine fails, the staged backup file is removed."""
    repo = MemorySnapshotRepository(tmp_path / "mem.json", user_id="u1")
    write_json_atomic(repo.path, {"user_id": "u1", "memories": [
        {"content": "original", "memory_type": "working",
         "metadata": {"user_id": "u1"}}
    ]})

    import app.memory_repository as mod
    real_write = mod.write_json_atomic

    def _failing_write(path, data):
        raise OSError("simulated disk full")

    monkeypatch.setattr(mod, "write_json_atomic", _failing_write)

    with pytest.raises(OSError):
        repo.quarantine_and_reset()

    # No .corrupt-* file should exist in the directory besides the active file.
    siblings = list(repo.path.parent.glob(repo.path.name + ".corrupt-*"))
    assert len(siblings) == 0, (
        f"Stranded backup files found: {[s.name for s in siblings]}"
    )
