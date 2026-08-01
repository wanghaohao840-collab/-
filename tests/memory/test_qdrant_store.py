from concurrent.futures import ThreadPoolExecutor

import pytest

import hello_agents.memory.storage.qdrant_store as qdrant_store_module
from hello_agents.memory.storage.qdrant_store import QdrantConnectionManager
from hello_agents.memory.storage.vector_store import InMemoryVectorStore


def setup_function():
    QdrantConnectionManager._instances.clear()


@pytest.fixture(autouse=True)
def use_in_memory_store(monkeypatch):
    monkeypatch.setattr(
        qdrant_store_module,
        "QdrantVectorStore",
        lambda **kwargs: InMemoryVectorStore(),
    )


def test_connection_manager_isolates_connection_and_tenant_dimensions():
    base = QdrantConnectionManager.get_instance(
        qdrant_url="http://one",
        qdrant_api_key="key-1",
        collection_name="shared",
        tenant_id="tenant-1",
        rag_namespace="ns",
    )
    same = QdrantConnectionManager.get_instance(
        qdrant_url="http://one",
        qdrant_api_key="key-1",
        collection_name="shared",
        tenant_id="tenant-1",
        rag_namespace="ns",
    )
    other = QdrantConnectionManager.get_instance(
        qdrant_url="http://two",
        qdrant_api_key="key-2",
        collection_name="shared",
        tenant_id="tenant-2",
        rag_namespace="ns",
    )

    assert same is base
    assert other is not base


def test_connection_manager_initializes_once_under_concurrency():
    def get_store(_):
        return QdrantConnectionManager.get_instance(
            qdrant_url="http://one",
            collection_name="shared",
            rag_namespace="ns",
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        stores = list(executor.map(get_store, range(32)))

    assert len({id(store) for store in stores}) == 1


def test_same_collection_on_different_urls_does_not_share_store():
    first = QdrantConnectionManager.get_instance(
        qdrant_url="http://one",
        collection_name="shared",
    )
    second = QdrantConnectionManager.get_instance(
        qdrant_url="http://two",
        collection_name="shared",
    )

    assert first is not second
