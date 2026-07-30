import pytest

from hello_agents.memory.rag.errors import RAGCollectionError
from hello_agents.memory.storage.vector_store import (
    InMemoryVectorStore,
    VectorPoint,
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
