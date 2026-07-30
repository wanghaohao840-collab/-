from types import SimpleNamespace

import pytest

from hello_agents.memory.rag.errors import RAGConnectionError, RAGOperationError
from hello_agents.memory.storage.vector_store import QdrantVectorStore


class HttpError(RuntimeError):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(message)


class UncertainCreateClient:
    def __init__(
        self,
        *,
        create_then_raise: bool = False,
        create_error: Exception | None = None,
    ):
        self.create_then_raise = create_then_raise
        self.create_error = create_error or TimeoutError("create timed out")
        self.created = False
        self.create_calls = 0
        self.get_calls = 0

    def collection_exists(self, collection_name):
        return self.created

    def create_collection(self, collection_name, vectors_config):
        self.create_calls += 1
        if self.create_then_raise:
            self.created = True
        raise self.create_error

    def get_collection(self, collection_name):
        self.get_calls += 1
        if not self.created:
            raise HttpError(404, "collection not found")
        return SimpleNamespace(
            config=SimpleNamespace(
                params=SimpleNamespace(
                    vectors=SimpleNamespace(size=2, distance="Cosine")
                )
            )
        )


class PayloadIndexClient:
    def __init__(self):
        self.calls = []

    def create_payload_index(self, **kwargs):
        self.calls.append(kwargs)


def test_uncertain_create_reconciles_by_reading_collection():
    client = UncertainCreateClient(create_then_raise=True)
    store = QdrantVectorStore(client=client, retry_delays=(0, 0, 0))

    store.ensure_collection("documents", dimension=2)

    assert client.create_calls == 1
    assert client.get_calls == 1


def test_uncertain_uncommitted_create_reports_original_failure_without_retry():
    client = UncertainCreateClient()
    store = QdrantVectorStore(client=client, retry_delays=(0, 0, 0))

    with pytest.raises(RAGConnectionError, match="create_collection"):
        store.ensure_collection("documents", dimension=2)

    assert client.create_calls == 1
    assert client.get_calls == 1


def test_non_retryable_create_error_is_not_reconciled():
    client = UncertainCreateClient(
        create_error=HttpError(400, "invalid collection request")
    )
    store = QdrantVectorStore(client=client, retry_delays=(0, 0, 0))

    with pytest.raises(RAGOperationError, match="create_collection"):
        store.ensure_collection("documents", dimension=2)

    assert client.create_calls == 1
    assert client.get_calls == 0


def test_unknown_payload_index_schema_fails_before_remote_call():
    client = PayloadIndexClient()
    store = QdrantVectorStore(client=client, retry_delays=())

    with pytest.raises(ValueError, match=r"published_at.*datetime"):
        store.ensure_payload_indexes("documents", {"published_at": "datetime"})

    assert client.calls == []
