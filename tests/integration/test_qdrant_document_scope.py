import os
import time
import uuid
from datetime import datetime

import pytest

from hello_agents.memory.base import MemoryConfig, MemoryItem
from hello_agents.memory.rag.contracts import DocumentSegment
from hello_agents.memory.rag.qdrant_pipeline import QdrantRAGPipeline
from hello_agents.memory.storage.vector_store import (
    QdrantVectorStore,
    VectorPoint,
    VectorRange,
)
from hello_agents.memory.types.episodic import EpisodicMemory
from hello_agents.memory.types.semantic import SemanticMemory


QDRANT_TEST_URL = os.getenv("QDRANT_TEST_URL")


def _delete_collection_with_retry(
    client,
    collection_name,
    retry_delays=(0.25, 0.5, 1.0),
):
    for attempt in range(1 + len(retry_delays)):
        try:
            if not client.collection_exists(collection_name=collection_name):
                return
            client.delete_collection(collection_name=collection_name)
            return
        except Exception:
            if attempt >= len(retry_delays):
                raise
            time.sleep(retry_delays[attempt])


def test_delete_collection_retries_transient_cleanup_failure():
    class FlakyDeleteClient:
        def __init__(self):
            self.delete_calls = 0
            self.deleted = False

        def collection_exists(self, collection_name):
            return not self.deleted

        def delete_collection(self, collection_name):
            self.delete_calls += 1
            if self.delete_calls < 3:
                raise RuntimeError("500 temporary Windows file lock")
            self.deleted = True

    client = FlakyDeleteClient()

    _delete_collection_with_retry(client, "documents", retry_delays=(0, 0))

    assert client.delete_calls == 3
    assert client.deleted


class LiveTestEmbedder:
    def encode(self, text):
        return [1.0, 0.0] if "alpha" in str(text).lower() else [0.0, 1.0]


@pytest.mark.skipif(
    not QDRANT_TEST_URL,
    reason="Set QDRANT_TEST_URL to run against an explicitly authorized Qdrant service",
)
def test_real_qdrant_match_any_does_not_leak_other_documents():
    from qdrant_client import QdrantClient, models

    client = QdrantClient(url=QDRANT_TEST_URL, api_key=os.getenv("QDRANT_TEST_API_KEY") or None)
    collection = f"rag_scope_test_{uuid.uuid4().hex}"
    client.create_collection(
        collection_name=collection,
        vectors_config=models.VectorParams(size=2, distance=models.Distance.COSINE),
    )
    try:
        client.upsert(
            collection_name=collection,
            wait=True,
            points=[
                models.PointStruct(
                    id=index,
                    vector=[1.0, 0.0],
                    payload={"rag_namespace": "ns", "document_id": document_id},
                )
                for index, document_id in enumerate(["doc-1", "doc-2", "doc-3"], start=1)
            ],
        )
        points, _ = client.scroll(
            collection_name=collection,
            scroll_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="rag_namespace",
                        match=models.MatchValue(value="ns"),
                    ),
                    models.FieldCondition(
                        key="document_id",
                        match=models.MatchAny(any=["doc-1", "doc-2"]),
                    ),
                ]
            ),
            limit=10,
            with_payload=True,
        )

        assert {point.payload["document_id"] for point in points} == {"doc-1", "doc-2"}
    finally:
        _delete_collection_with_retry(client, collection)


@pytest.mark.skipif(
    not QDRANT_TEST_URL,
    reason="Set QDRANT_TEST_URL to run against an explicitly authorized Qdrant service",
)
def test_real_qdrant_vector_store_contract():
    collection = f"vector_store_test_{uuid.uuid4().hex}"
    store = QdrantVectorStore(
        url=QDRANT_TEST_URL,
        api_key=os.getenv("QDRANT_TEST_API_KEY") or None,
        retry_delays=(),
    )
    store.ensure_collection(collection, dimension=2)
    try:
        store.upsert(
            collection,
            [
                VectorPoint("one", [1.0, 0.0], {"namespace": "a"}),
                VectorPoint("two", [0.0, 1.0], {"namespace": "b"}),
            ],
        )

        assert store.count(collection, {"namespace": "a"}) == 1
        assert [hit.id for hit in store.search(
            collection,
            [1.0, 0.0],
            filters={"namespace": "a"},
        )] == ["one"]
        assert [point.id for point in store.scroll(
            collection,
            {"namespace": "b"},
        )] == ["two"]
        assert store.delete_by_filter(collection, {"namespace": "a"}) == 1
        assert store.count(collection) == 1
    finally:
        _delete_collection_with_retry(store.client, collection)


@pytest.mark.skipif(
    not QDRANT_TEST_URL,
    reason="Set QDRANT_TEST_URL to run against an explicitly authorized Qdrant service",
)
def test_real_qdrant_semantic_memory_creates_filter_indexes(monkeypatch, tmp_path):
    from qdrant_client import QdrantClient
    import hello_agents.memory.types.semantic as semantic_module

    monkeypatch.setattr(
        semantic_module,
        "get_text_embedder",
        lambda: LiveTestEmbedder(),
    )

    client = QdrantClient(
        url=QDRANT_TEST_URL,
        api_key=os.getenv("QDRANT_TEST_API_KEY") or None,
    )
    collection = f"semantic_index_test_{uuid.uuid4().hex}"
    store = QdrantVectorStore(
        url=QDRANT_TEST_URL,
        api_key=os.getenv("QDRANT_TEST_API_KEY") or None,
        client=client,
        retry_delays=(),
    )
    config = MemoryConfig(
        database_path=str(tmp_path / "memory.db"),
        qdrant_collection=collection,
        qdrant_vector_size=2,
    )
    try:
        SemanticMemory(config, storage_backend=store)
        payload_schema = client.get_collection(
            collection_name=collection
        ).payload_schema

        def schema_name(field_name):
            data_type = getattr(
                payload_schema[field_name],
                "data_type",
                payload_schema[field_name],
            )
            return str(getattr(data_type, "value", data_type)).lower()

        assert schema_name("memory_type") == "keyword"
        assert schema_name("user_id") == "keyword"
    finally:
        _delete_collection_with_retry(client, collection)


@pytest.mark.skipif(
    not QDRANT_TEST_URL,
    reason="Set QDRANT_TEST_URL to run against an explicitly authorized Qdrant service",
)
def test_real_qdrant_episodic_memory_preserves_shared_collection_scope(
    monkeypatch,
    tmp_path,
):
    from qdrant_client import QdrantClient
    import hello_agents.memory.types.episodic as episodic_module

    monkeypatch.setattr(
        episodic_module,
        "create_embedding_model_with_fallback",
        lambda: LiveTestEmbedder(),
    )

    client = QdrantClient(
        url=QDRANT_TEST_URL,
        api_key=os.getenv("QDRANT_TEST_API_KEY") or None,
    )
    collection = f"episodic_scope_test_{uuid.uuid4().hex}"
    store = QdrantVectorStore(
        url=QDRANT_TEST_URL,
        api_key=os.getenv("QDRANT_TEST_API_KEY") or None,
        client=client,
        retry_delays=(),
    )
    config = MemoryConfig(
        database_path=str(tmp_path / "memory.db"),
        qdrant_collection=collection,
        qdrant_vector_size=2,
    )
    memory = None
    try:
        memory = EpisodicMemory(config, storage_backend=store)
        payload_schema = client.get_collection(
            collection_name=collection
        ).payload_schema

        def schema_name(field_name):
            data_type = getattr(
                payload_schema[field_name],
                "data_type",
                payload_schema[field_name],
            )
            return str(getattr(data_type, "value", data_type)).lower()

        assert schema_name("memory_type") == "keyword"
        assert schema_name("user_id") == "keyword"
        assert schema_name("session_id") == "keyword"
        assert schema_name("importance") == "float"
        assert schema_name("timestamp") == "datetime"

        episode = MemoryItem(
            content="alpha episode",
            memory_type="episodic",
            importance=0.8,
            metadata={"user_id": "user-1", "session_id": "session-1"},
        )
        episode.timestamp = "2026-07-01T00:00:00"
        memory.add(episode)
        assert [item.id for item in memory.retrieve(
            "alpha",
            user_id="user-1",
            session_id="session-1",
            min_importance=0.6,
            start_time="2026-06-01T00:00:00",
            end_time="2026-08-01T00:00:00",
        )] == [episode.id]
        assert store.count(
            collection,
            {
                "memory_type": "episodic",
                "importance": VectorRange(gte=0.6),
                "timestamp": VectorRange(
                    gte=datetime.fromisoformat("2026-06-01T00:00:00"),
                    lte=datetime.fromisoformat("2026-08-01T00:00:00"),
                ),
            },
        ) == 1

        store.upsert(
            collection,
            [
                VectorPoint(
                    "legacy-episode",
                    [1.0, 0.0],
                    {
                        "memory_type": "episodic",
                        "user_id": "legacy-user",
                        "session_id": "legacy-session",
                        "importance": 0.8,
                        "timestamp": "2026/07/01 10:00:00",
                        "content": "alpha legacy episode",
                    },
                )
            ],
        )
        assert [hit["id"] for hit in memory._vector_search(
            "alpha",
            user_id="legacy-user",
            start_time="2026-07-01T00:00:00",
            end_time="2026-07-01T23:59:59",
        )] == ["legacy-episode"]
        migrated = store.scroll(
            collection,
            {"memory_type": "episodic", "user_id": "legacy-user"},
        )
        assert [point.payload["timestamp"] for point in migrated] == [
            "2026-07-01T10:00:00"
        ]
        store.delete_by_filter(collection, {"_id": ["legacy-episode"]})

        store.upsert(
            collection,
            [
                VectorPoint(
                    "semantic-survivor",
                    [0.0, 1.0],
                    {
                        "memory_type": "semantic",
                        "user_id": "user-1",
                    },
                )
            ],
        )
        memory.clear()

        assert memory.doc_store.get_document(episode.id) is None
        assert store.count(collection, {"memory_type": "episodic"}) == 0
        assert store.count(collection, {"memory_type": "semantic"}) == 1
    finally:
        if memory is not None:
            memory.close()
        _delete_collection_with_retry(client, collection)


@pytest.mark.skipif(
    not QDRANT_TEST_URL,
    reason="Set QDRANT_TEST_URL to run against an explicitly authorized Qdrant service",
)
def test_real_qdrant_rag_pipeline_preserves_namespace_lifecycle(monkeypatch):
    from qdrant_client import QdrantClient
    import hello_agents.memory.rag.qdrant_pipeline as qdrant_pipeline_module

    monkeypatch.setattr(
        qdrant_pipeline_module,
        "get_text_embedder",
        lambda: LiveTestEmbedder(),
    )
    monkeypatch.setattr(qdrant_pipeline_module, "get_dimension", lambda default: 2)

    client = QdrantClient(
        url=QDRANT_TEST_URL,
        api_key=os.getenv("QDRANT_TEST_API_KEY") or None,
    )
    collection = f"rag_lifecycle_test_{uuid.uuid4().hex}"
    ns_a = QdrantRAGPipeline(
        collection_name=collection,
        rag_namespace="ns-a",
        qdrant_client=client,
        retry_delays=(),
    )
    ns_b = QdrantRAGPipeline(
        collection_name=collection,
        rag_namespace="ns-b",
        qdrant_client=client,
        retry_delays=(),
    )
    try:
        payload_schema = client.get_collection(
            collection_name=collection
        ).payload_schema

        def schema_name(field_name):
            data_type = getattr(
                payload_schema[field_name],
                "data_type",
                payload_schema[field_name],
            )
            return str(getattr(data_type, "value", data_type)).lower()

        assert schema_name("rag_namespace") == "keyword"
        assert schema_name("document_id") == "keyword"
        assert schema_name("chunk_index") == "integer"

        assert ns_a.add_text("alpha source", document_id="doc-a")["success"]
        assert ns_b.add_text("beta source", document_id="doc-b")["success"]

        assert {
            item["metadata"]["document_id"]
            for item in ns_a.search("alpha", limit=10)
        } == {"doc-a"}
        assert ns_b.stats()["chunk_count"] == 1

        replaced = ns_a.replace_document(
            "doc-a",
            [DocumentSegment("alpha replacement", {})],
        )
        assert replaced["success"]
        assert ns_a.delete_document("doc-a")["chunks_removed"] == 1
        assert ns_b.stats()["chunk_count"] == 1
        assert ns_b.clear()["chunks_removed"] == 1
    finally:
        _delete_collection_with_retry(client, collection)
