from datetime import datetime

from hello_agents.memory.base import MemoryConfig, MemoryItem
from hello_agents.memory.storage.vector_store import (
    InMemoryVectorStore,
    VectorPoint,
    VectorRange,
)
from hello_agents.memory.types.episodic import EpisodicMemory


class FixedEmbedder:
    def encode(self, text):
        return [1.0, 0.0]


class RecordingVectorStore(InMemoryVectorStore):
    def __init__(self):
        super().__init__(collection_name="episodic")
        self.payload_index_requests = []
        self.search_filters = []
        self.scroll_filters = []

    def ensure_payload_indexes(self, collection_name, indexes):
        super().ensure_payload_indexes(collection_name, indexes)
        self.payload_index_requests.append((collection_name, dict(indexes)))

    def search(
        self,
        collection_name,
        query_vector,
        filters=None,
        limit=5,
        score_threshold=None,
    ):
        self.search_filters.append(dict(filters or {}))
        return super().search(
            collection_name,
            query_vector,
            filters=filters,
            limit=limit,
            score_threshold=score_threshold,
        )

    def scroll(
        self,
        collection_name,
        filters=None,
        with_vectors=False,
        payload_fields=None,
    ):
        self.scroll_filters.append(dict(filters or {}))
        return super().scroll(
            collection_name,
            filters=filters,
            with_vectors=with_vectors,
            payload_fields=payload_fields,
        )


def test_episodic_memory_uses_vector_store_protocol_and_scoped_filters(
    monkeypatch,
    tmp_path,
):
    import hello_agents.memory.types.episodic as episodic_module

    monkeypatch.setattr(
        episodic_module,
        "create_embedding_model_with_fallback",
        lambda: FixedEmbedder(),
    )
    store = RecordingVectorStore()
    config = MemoryConfig(
        database_path=str(tmp_path / "memory.db"),
        qdrant_collection="episodic",
        qdrant_vector_size=2,
    )
    memory = EpisodicMemory(config, storage_backend=store)
    try:
        assert store.payload_index_requests == [
            (
                "episodic",
                {
                    "memory_type": "keyword",
                    "user_id": "keyword",
                    "session_id": "keyword",
                    "importance": "float",
                    "timestamp": "datetime",
                },
            )
        ]

        item = MemoryItem(
            content="alpha episode",
            memory_type="episodic",
            importance=0.8,
            metadata={"user_id": "user-1", "session_id": "session-1"},
        )
        item.timestamp = "2026/07/01 00:00:00"
        memory.add(item)
        assert store.vectors[item.id]["metadata"]["timestamp"] == (
            "2026-07-01T00:00:00"
        )

        assert [result.id for result in memory.retrieve(
            "alpha",
            user_id="user-1",
            session_id="session-1",
            min_importance=0.6,
            start_time="2026-06-01T00:00:00",
            end_time="2026-08-01T00:00:00",
        )] == [item.id]
        assert store.search_filters[-1] == {
            "memory_type": "episodic",
            "user_id": "user-1",
            "session_id": "session-1",
            "importance": VectorRange(gte=0.6),
            "timestamp": VectorRange(
                gte=datetime.fromisoformat("2026-06-01T00:00:00"),
                lte=datetime.fromisoformat("2026-08-01T00:00:00"),
            ),
        }
    finally:
        memory.close()


def test_time_range_search_normalizes_legacy_timestamps_once_per_user(
    monkeypatch,
    tmp_path,
):
    import hello_agents.memory.types.episodic as episodic_module

    monkeypatch.setattr(
        episodic_module,
        "create_embedding_model_with_fallback",
        lambda: FixedEmbedder(),
    )
    store = RecordingVectorStore()
    config = MemoryConfig(
        database_path=str(tmp_path / "memory.db"),
        qdrant_collection="episodic",
        qdrant_vector_size=2,
    )
    memory = EpisodicMemory(config, storage_backend=store)
    try:
        store.upsert(
            "episodic",
            [
                VectorPoint(
                    "legacy-user-1",
                    [1.0, 0.0],
                    {
                        "memory_type": "episodic",
                        "user_id": "user-1",
                        "session_id": "session-1",
                        "importance": 0.8,
                        "timestamp": "2026/07/01 10:00:00",
                        "content": "alpha legacy",
                    },
                ),
                VectorPoint(
                    "legacy-user-2",
                    [1.0, 0.0],
                    {
                        "memory_type": "episodic",
                        "user_id": "user-2",
                        "session_id": "session-2",
                        "importance": 0.8,
                        "timestamp": "2026/07/02 10:00:00",
                        "content": "alpha other user",
                    },
                ),
            ],
        )

        hits = memory._vector_search(
            "alpha",
            user_id="user-1",
            start_time="2026-07-01T00:00:00",
            end_time="2026-07-01T23:59:59",
        )

        assert [hit["id"] for hit in hits] == ["legacy-user-1"]
        assert store.scroll_filters == [
            {"memory_type": "episodic", "user_id": "user-1"}
        ]
        assert store.vectors["legacy-user-1"]["metadata"]["timestamp"] == (
            "2026-07-01T10:00:00"
        )
        assert store.vectors["legacy-user-2"]["metadata"]["timestamp"] == (
            "2026/07/02 10:00:00"
        )

        memory._vector_search(
            "alpha",
            user_id="user-1",
            start_time="2026-07-01T00:00:00",
            end_time="2026-07-01T23:59:59",
        )
        assert len(store.scroll_filters) == 1
    finally:
        memory.close()
