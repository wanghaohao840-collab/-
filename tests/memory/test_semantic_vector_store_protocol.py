from hello_agents.memory.base import MemoryConfig, MemoryItem
from hello_agents.memory.storage.vector_store import InMemoryVectorStore
from hello_agents.memory.types.semantic import SemanticMemory


class RecordingVectorStore(InMemoryVectorStore):
    def __init__(self):
        super().__init__()
        self.payload_index_requests = []

    def ensure_payload_indexes(self, collection_name, indexes):
        super().ensure_payload_indexes(collection_name, indexes)
        self.payload_index_requests.append((collection_name, dict(indexes)))


def test_semantic_memory_uses_injected_vector_store_protocol(tmp_path):
    store = RecordingVectorStore()
    config = MemoryConfig(
        database_path=str(tmp_path / "memory.db"),
        qdrant_collection="semantic",
        qdrant_vector_size=384,
    )
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
