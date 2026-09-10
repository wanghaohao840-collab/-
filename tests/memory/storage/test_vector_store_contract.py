from datetime import datetime
from types import SimpleNamespace

import pytest

from hello_agents.memory.rag.errors import RAGCollectionError
from hello_agents.memory.storage.vector_store import (
    InMemoryVectorStore,
    QdrantVectorStore,
    VectorPoint,
    VectorRange,
    VectorStore,
)


@pytest.fixture
def store():
    return InMemoryVectorStore()


def test_in_memory_store_implements_vector_store_protocol(store):
    assert isinstance(store, VectorStore)


def test_vector_store_contract_covers_lifecycle(store):
    store.ensure_collection("documents", dimension=2)
    store.ensure_payload_indexes(
        "documents",
        {
            "rag_namespace": "keyword",
            "document_id": "keyword",
            "chunk_index": "integer",
        },
    )
    store.upsert(
        "documents",
        [
            VectorPoint("one", [1.0, 0.0], {"namespace": "a", "document_id": "1"}),
            VectorPoint("two", [0.9, 0.1], {"namespace": "a", "document_id": "2"}),
            VectorPoint("three", [0.0, 1.0], {"namespace": "b", "document_id": "3"}),
        ],
    )

    hits = store.search(
        "documents",
        [1.0, 0.0],
        filters={"namespace": "a", "document_id": ["1", "2"]},
        limit=10,
    )
    assert [hit.id for hit in hits] == ["one", "two"]
    assert store.count("documents", {"namespace": "a"}) == 2
    assert [point.id for point in store.scroll("documents", {"namespace": "b"})] == [
        "three"
    ]
    assert store.delete_by_filter("documents", {"document_id": "2"}) == 1
    assert store.count("documents") == 2


def test_vector_store_rejects_collection_dimension_changes(store):
    store.ensure_collection("documents", dimension=2)

    with pytest.raises(RAGCollectionError, match="expected 3"):
        store.ensure_collection("documents", dimension=3)


def test_in_memory_store_supports_numeric_and_datetime_ranges(store):
    store.ensure_collection("episodes", dimension=2)
    store.upsert(
        "episodes",
        [
            VectorPoint(
                "old",
                [1.0, 0.0],
                {"importance": 0.9, "timestamp": "2026-01-01T00:00:00"},
            ),
            VectorPoint(
                "target",
                [1.0, 0.0],
                {"importance": 0.8, "timestamp": "2026-07-01T00:00:00"},
            ),
            VectorPoint(
                "low",
                [1.0, 0.0],
                {"importance": 0.2, "timestamp": "2026-07-01T00:00:00"},
            ),
        ],
    )

    hits = store.search(
        "episodes",
        [1.0, 0.0],
        filters={
            "importance": VectorRange(gte=0.5),
            "timestamp": VectorRange(
                gte=datetime.fromisoformat("2026-06-01T00:00:00"),
                lte=datetime.fromisoformat("2026-08-01T00:00:00"),
            ),
        },
        limit=10,
    )

    assert [hit.id for hit in hits] == ["target"]
class ProbeClient:
    def __init__(self, *, exists, dimension=2, distance="Cosine"):
        self.exists = exists
        self.dimension = dimension
        self.distance = distance
        self.create_calls = []

    def collection_exists(self, name):
        return self.exists

    def get_collection(self, name):
        vectors = SimpleNamespace(
            size=self.dimension,
            distance=SimpleNamespace(value=self.distance),
        )
        return SimpleNamespace(
            config=SimpleNamespace(params=SimpleNamespace(vectors=vectors))
        )

    def create_collection(self, **kwargs):
        self.create_calls.append(kwargs)


def test_in_memory_require_collection_never_creates_and_checks_distance(store):
    with pytest.raises(RAGCollectionError, match="not found"):
        store.require_collection("documents", 2, "Cosine")
    assert "documents" not in store._collections

    store.ensure_collection("documents", 2, "Cosine")
    store.require_collection("documents", 2, "Cosine")
    with pytest.raises(RAGCollectionError, match="expected 2/Dot"):
        store.require_collection("documents", 2, "Dot")


def test_qdrant_require_collection_never_creates_missing_collection():
    client = ProbeClient(exists=False)
    store = QdrantVectorStore(client=client, retry_delays=())

    with pytest.raises(RAGCollectionError, match="not found"):
        store.require_collection("documents", 2, "Cosine")

    assert client.create_calls == []


@pytest.mark.parametrize(("dimension", "distance"), [
    (3, "Cosine"), (2, "Dot"),
])
def test_qdrant_require_collection_rejects_incompatible_existing_collection(
    dimension, distance
):
    client = ProbeClient(exists=True, dimension=dimension, distance=distance)
    store = QdrantVectorStore(client=client, retry_delays=())

    with pytest.raises(RAGCollectionError, match="incompatible"):
        store.require_collection("documents", 2, "Cosine")
    assert client.create_calls == []


def test_qdrant_require_collection_accepts_exact_existing_collection():
    client = ProbeClient(exists=True)
    store = QdrantVectorStore(client=client, retry_delays=())

    store.require_collection("documents", 2, "Cosine")

    assert client.create_calls == []
