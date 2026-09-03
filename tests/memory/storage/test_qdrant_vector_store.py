from datetime import datetime
from types import SimpleNamespace

import pytest

from hello_agents.memory.rag.errors import RAGConnectionError, RAGOperationError
from hello_agents.memory.storage.vector_store import QdrantVectorStore, VectorRange


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


class DeleteClient:
    """Small concrete client exercising Qdrant filter/selector semantics."""

    def __init__(self, points):
        self.points = list(points)
        self.delete_calls = []

    @staticmethod
    def _matches(point, query_filter):
        if query_filter is None:
            return True
        for condition in getattr(query_filter, "must", []):
            actual = point.payload.get(condition.key)
            match = condition.match
            if hasattr(match, "any"):
                if actual not in match.any:
                    return False
            elif actual != match.value:
                return False
        return True

    def count(self, *, collection_name, count_filter, exact):
        return SimpleNamespace(
            count=sum(self._matches(point, count_filter) for point in self.points)
        )

    def delete(self, *, collection_name, points_selector, wait):
        self.delete_calls.append(points_selector)
        query_filter = getattr(points_selector, "filter", None)
        self.points = [
            point for point in self.points
            if not self._matches(point, query_filter)
        ]
        return SimpleNamespace(status="completed")


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

    with pytest.raises(ValueError, match=r"body.*text"):
        store.ensure_payload_indexes("documents", {"body": "text"})

    assert client.calls == []


def test_qdrant_payload_indexes_accept_float_and_datetime():
    client = PayloadIndexClient()
    store = QdrantVectorStore(client=client, retry_delays=())

    store.ensure_payload_indexes(
        "episodes",
        {"importance": "float", "timestamp": "datetime"},
    )

    assert [call["field_name"] for call in client.calls] == [
        "importance",
        "timestamp",
    ]
    assert [str(call["field_schema"].value).lower() for call in client.calls] == [
        "float",
        "datetime",
    ]


def test_qdrant_filter_maps_numeric_and_datetime_ranges():
    store = QdrantVectorStore(client=PayloadIndexClient(), retry_delays=())
    start = datetime.fromisoformat("2026-06-01T00:00:00")
    end = datetime.fromisoformat("2026-08-01T00:00:00")

    query_filter = store._filter(
        {
            "importance": VectorRange(gte=0.5),
            "timestamp": VectorRange(gte=start, lte=end),
        }
    )
    conditions = {condition.key: condition for condition in query_filter.must}

    assert conditions["importance"].range.gte == 0.5
    assert conditions["timestamp"].range.gte == start
    assert conditions["timestamp"].range.lte == end


def test_qdrant_delete_intersects_logical_id_and_user_and_reports_confirmed_matches():
    store = QdrantVectorStore(client=PayloadIndexClient(), retry_delays=())
    store.models = None
    points = [
        SimpleNamespace(
            id=store._qdrant_id("note:alice:one"),
            payload={"_vector_store_id": "note:alice:one", "user_id": "alice"},
        ),
        SimpleNamespace(
            id=store._qdrant_id("note:bob:one"),
            payload={"_vector_store_id": "note:bob:one", "user_id": "bob"},
        ),
    ]
    client = DeleteClient(points)
    store.client = client

    assert store.delete_by_filter(
        "notes", {"_id": ["note:alice:one"], "user_id": "alice"}
    ) == 1
    assert [point.payload["user_id"] for point in client.points] == ["bob"]
    selector = client.delete_calls[0]
    assert {condition.key for condition in selector.filter.must} == {
        "_vector_store_id", "user_id"
    }
    assert store.delete_by_filter(
        "notes", {"_id": ["note:alice:one"], "user_id": "alice"}
    ) == 0
    assert len(client.delete_calls) == 1


def test_qdrant_delete_missing_and_disappeared_targets_return_zero():
    missing_client = DeleteClient([])
    store = QdrantVectorStore(client=missing_client, retry_delays=())
    store.models = None
    assert store.delete_by_filter("notes", {"_id": ["missing"]}) == 0
    assert missing_client.delete_calls == []
