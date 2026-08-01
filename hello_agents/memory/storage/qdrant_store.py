"""Compatibility imports for the unified vector-store boundary."""

from __future__ import annotations

import threading

from hello_agents.memory.storage.vector_store import (
    InMemoryVectorStore,
    QdrantVectorStore,
    VectorStore,
)


class QdrantConnectionManager:
    """Cache VectorStore instances by connection and isolation scope."""

    _instances: dict[tuple, VectorStore] = {}
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls, **kwargs) -> VectorStore:
        collection_name = kwargs.get("collection_name", "default")
        url = kwargs.get("qdrant_url")
        api_key = kwargs.get("qdrant_api_key")
        dimension = int(kwargs.get("vector_size") or kwargs.get("dimension") or 384)
        key = (
            url,
            api_key,
            collection_name,
            kwargs.get("tenant_id"),
            kwargs.get("rag_namespace"),
        )
        with cls._lock:
            if key not in cls._instances:
                if url:
                    store: VectorStore = QdrantVectorStore(url=url, api_key=api_key)
                else:
                    store = InMemoryVectorStore()
                store.ensure_collection(collection_name, dimension)
                store.collection_name = collection_name
                cls._instances[key] = store
            return cls._instances[key]


def _create_default_vector_store(dimension: int = 384) -> InMemoryVectorStore:
    return InMemoryVectorStore(collection_name="default", dimension=dimension)
