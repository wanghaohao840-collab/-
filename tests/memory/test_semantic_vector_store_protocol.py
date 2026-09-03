from hello_agents.memory.base import MemoryConfig, MemoryItem
from hello_agents.memory.manager import MemoryManager
from hello_agents.memory.storage.vector_store import InMemoryVectorStore
from hello_agents.memory.types.semantic import SemanticMemory


class RecordingVectorStore(InMemoryVectorStore):
    def __init__(self):
        super().__init__()
        self.payload_index_requests = []
        self.delete_calls = []

    def ensure_payload_indexes(self, collection_name, indexes):
        super().ensure_payload_indexes(collection_name, indexes)
        self.payload_index_requests.append((collection_name, dict(indexes)))

    def delete_by_filter(self, collection_name, filters=None):
        self.delete_calls.append((collection_name, dict(filters or {})))
        return super().delete_by_filter(collection_name, filters)


class FailingDeleteVectorStore(RecordingVectorStore):
    def delete_by_filter(self, collection_name, filters):
        raise RuntimeError("vector delete failed")


class DisappearingDeleteVectorStore(RecordingVectorStore):
    def delete_by_filter(self, collection_name, filters):
        super().delete_by_filter(collection_name, filters)
        return 0


def _semantic_config(tmp_path, collection="semantic"):
    return MemoryConfig(
        database_path=str(tmp_path / "memory.db"),
        qdrant_collection=collection,
        qdrant_vector_size=384,
    )


def test_semantic_memory_uses_injected_vector_store_protocol(tmp_path):
    store = RecordingVectorStore()
    config = _semantic_config(tmp_path)
    memory = SemanticMemory(config, storage_backend=store)
    assert store.payload_index_requests == [
        (
            "semantic",
            {"memory_type": "keyword", "user_id": "keyword"},
        )
    ]

    item = MemoryItem(
        content="protocol-backed semantic memory",
        memory_type="semantic",
        metadata={"user_id": "user-1"},
    )

    memory.add(item)

    assert store.count(
        "semantic",
        {"memory_type": "semantic", "user_id": "user-1"},
    ) == 1


def test_semantic_memory_remove_deletes_only_exact_vector_and_cache(tmp_path):
    store = RecordingVectorStore()
    memory = SemanticMemory(_semantic_config(tmp_path), storage_backend=store)
    target = MemoryItem(
        content="target semantic memory",
        memory_type="semantic",
        id="note:alice:target",
        metadata={"user_id": "alice"},
    )
    survivor = MemoryItem(
        content="survivor semantic memory",
        memory_type="semantic",
        id="note:bob:survivor",
        metadata={"user_id": "bob"},
    )
    memory.add(target)
    memory.add(survivor)

    assert memory.remove(target.id) is True
    assert target.id not in memory.memories
    assert store.count("semantic", {"_id": [target.id]}) == 0
    assert store.count("semantic", {"_id": [survivor.id]}) == 1
    assert memory.remove(target.id) is False

    assert store.delete_calls == [("semantic", {"_id": [target.id]})]


def test_memory_manager_remove_memory_uses_real_semantic_memory(monkeypatch, tmp_path):
    store = RecordingVectorStore()
    monkeypatch.setattr(
        "hello_agents.memory.storage.qdrant_store.QdrantConnectionManager.get_instance",
        lambda **_: store,
    )
    manager = MemoryManager(
        config=_semantic_config(tmp_path, "manager-semantic"),
        user_id="alice",
        enable_working=False,
        enable_episodic=False,
        enable_semantic=True,
    )

    target_id = "note:alice:target"
    survivor_id = "note:bob:survivor"
    manager.add_memory(
        "target note",
        memory_type="semantic",
        metadata={"knowledge_type": "learning_note"},
        memory_id=target_id,
    )
    manager.add_memory(
        "survivor note",
        memory_type="semantic",
        metadata={"user_id": "bob"},
        memory_id=survivor_id,
    )

    assert manager.remove_memory(target_id, memory_type="semantic") is True
    assert manager.remove_memory(target_id, memory_type="semantic") is False
    assert store.count("manager-semantic", {"_id": [target_id]}) == 0
    assert store.count("manager-semantic", {"_id": [survivor_id]}) == 1
    manager.close()


def test_memory_manager_remove_memory_is_user_scoped(monkeypatch, tmp_path):
    store = RecordingVectorStore()
    monkeypatch.setattr(
        "hello_agents.memory.storage.qdrant_store.QdrantConnectionManager.get_instance",
        lambda **_: store,
    )
    manager = MemoryManager(
        config=_semantic_config(tmp_path, "scoped-semantic"),
        user_id="alice",
        enable_working=False,
        enable_episodic=False,
        enable_semantic=True,
    )
    bob_id = "note:bob:legacy"
    manager.add_memory(
        "bob note",
        memory_type="semantic",
        metadata={"user_id": "bob", "knowledge_type": "learning_note"},
        memory_id=bob_id,
    )

    assert manager.remove_memory(
        bob_id, memory_type="semantic", missing_ok=True
    ) is False
    assert store.count("scoped-semantic", {"_id": [bob_id]}) == 1
    assert bob_id in manager.memory_types["semantic"].memories

    assert manager.remove_memory(
        "missing", memory_type="semantic", missing_ok=True
    ) is True
    assert manager.remove_memory("missing", memory_type="semantic") is False
    manager.close()


def test_semantic_memory_remove_preserves_cache_when_vector_delete_fails(tmp_path):
    store = FailingDeleteVectorStore()
    memory = SemanticMemory(_semantic_config(tmp_path), storage_backend=store)
    item = MemoryItem(
        content="delete failure must be retryable",
        memory_type="semantic",
        id="note:alice:retry",
        metadata={"user_id": "alice"},
    )
    memory.add(item)

    assert memory.remove(item.id) is False
    assert item.id in memory.memories
    assert store.count("semantic", {"_id": [item.id]}) == 1


def test_semantic_memory_missing_ok_converges_after_concurrent_disappearance(tmp_path):
    store = DisappearingDeleteVectorStore()
    memory = SemanticMemory(_semantic_config(tmp_path), storage_backend=store)
    item = MemoryItem(
        content="concurrent disappearance is retry-safe",
        memory_type="semantic",
        id="note:alice:disappearing",
        metadata={"user_id": "alice"},
    )
    memory.add(item)

    assert memory.remove(item.id, user_id="alice", missing_ok=True) is True
    assert item.id not in memory.memories
    assert store.count("semantic", {"_id": [item.id]}) == 0
