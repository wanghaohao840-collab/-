import logging

import pytest

from hello_agents.memory.base import MemoryItem
from hello_agents.memory.rag.errors import RAGAuthenticationError
from hello_agents.memory.storage.vector_store import InMemoryVectorStore
from hello_agents.memory.types.semantic import SemanticMemory


class Embedder:
    def encode(self, text):
        return [1.0, 0.0]


class FailingStore:
    def __init__(self, error):
        self.error = error

    def search(self, *args, **kwargs):
        raise self.error


def semantic_with_store(store):
    memory = SemanticMemory.__new__(SemanticMemory)
    memory.embedding_model = Embedder()
    memory.vector_store = store
    memory.vector_collection = "semantic"
    item = MemoryItem(content="alpha knowledge", memory_type="semantic")
    memory.memories = {item.id: item}
    return memory


def test_connection_failure_logs_and_marks_keyword_fallback(caplog):
    memory = semantic_with_store(FailingStore(ConnectionError("secret host unavailable")))

    with caplog.at_level(logging.WARNING):
        results = memory._vector_search("alpha")

    assert results[0]["metadata"]["retrieval_backend"] == "fallback_keyword"
    assert "ConnectionError" in caplog.text
    assert "secret host" not in caplog.text


@pytest.mark.parametrize(
    "error",
    [RuntimeError("bug"), RAGAuthenticationError("credentials rejected")],
)
def test_programming_and_authentication_errors_are_not_downgraded(error):
    memory = semantic_with_store(FailingStore(error))

    with pytest.raises(type(error)):
        memory._vector_search("alpha")


def test_semantic_forget_and_clear_remove_vector_entries():
    store = InMemoryVectorStore(collection_name="semantic")
    memory = SemanticMemory.__new__(SemanticMemory)
    memory.vector_store = store
    memory.vector_collection = "semantic"
    memory.entities = {}
    memory.relations = []
    low = MemoryItem(content="low", memory_type="semantic", importance=0.1)
    high = MemoryItem(content="high", memory_type="semantic", importance=0.9)
    memory.memories = {low.id: low, high.id: high}
    store.add_vectors(
        vectors=[[1.0], [2.0]],
        metadata=[
            {"memory_type": "semantic"},
            {"memory_type": "semantic"},
        ],
        ids=[low.id, high.id],
    )

    assert memory.forget(threshold=0.5) == 1
    assert set(store.vectors) == {high.id}

    memory.clear()
    assert store.vectors == {}
