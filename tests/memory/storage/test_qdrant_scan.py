from types import SimpleNamespace
import uuid

import pytest

from hello_agents.memory.rag.errors import RAGAuthenticationError, RAGConnectionError
from hello_agents.memory.storage.vector_scan import VectorScanError
from hello_agents.memory.storage.vector_store import QdrantVectorStore


def raw(identifier, logical=None):
    payload = {"rag_namespace": "user-a", "metadata": {"nested": ["kept"]}}
    if logical is not None:
        payload["_vector_store_id"] = logical
    return SimpleNamespace(id=identifier, payload=payload, vector=[999.0])


class Client:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def scroll(self, **kwargs):
        self.calls.append(kwargs)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def store_for(responses, **kwargs):
    client = Client(responses)
    return QdrantVectorStore(client=client, retry_delays=(), **kwargs), client


def test_single_page_is_bounded_and_preserves_both_ids_and_native_zero_cursor():
    row = raw(0, "logical-zero")
    store, client = store_for([([row], 7)])
    page = store.scroll_page("source", {"rag_namespace": "user-a"}, offset=0, page_size=1)
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["offset"] == 0 and type(call["offset"]) is int
    assert call["limit"] == 1
    assert call["with_vectors"] is False and call["with_payload"] is True
    assert call["scroll_filter"].must[0].match.value == "user-a"
    assert page.next_offset == 7
    assert page.records[0].storage_id == 0
    assert page.records[0].logical_id == "logical-zero"
    page.records[0].payload["metadata"]["nested"].append("changed")
    assert row.payload["metadata"]["nested"] == ["kept"]
    assert not hasattr(page.records[0], "vector")


def test_iteration_is_lazy_and_can_resume_from_checkpoint():
    key = str(uuid.uuid4())
    store, client = store_for([([raw(0)], key), ([raw(key)], None)])
    pages = store.iter_scroll_pages("source", page_size=1)
    assert client.calls == []
    first = next(pages)
    assert len(client.calls) == 1 and first.next_offset == key
    pages.close()
    assert len(client.calls) == 1
    resumed = list(store.iter_scroll_pages("source", offset=first.next_offset, page_size=1))
    assert len(resumed) == 1 and resumed[0].records[0].storage_id == key
    assert client.calls[1]["offset"] == key


def test_iteration_freezes_scope_after_first_page():
    store, client = store_for([([raw(0)], 1), ([raw(1)], None)])
    filters = {"rag_namespace": ["user-a"]}
    pages = store.iter_scroll_pages("source", filters)
    next(pages)
    filters["rag_namespace"].append("user-b")
    next(pages)
    assert client.calls[1]["scroll_filter"].must[0].match.any == ["user-a"]


@pytest.mark.parametrize("size", [0, -1, 257, True, 1.0, "1"])
def test_invalid_page_size_makes_no_remote_call(size):
    store, client = store_for([])
    with pytest.raises(VectorScanError, match="page_size"):
        store.scroll_page("source", page_size=size)
    assert client.calls == []


@pytest.mark.parametrize("offset", [True, -1, 2**64, 1.5, "private-invalid", {}, []])
def test_invalid_cursor_is_rejected_without_exposing_value(offset):
    store, client = store_for([])
    with pytest.raises(VectorScanError) as caught:
        store.scroll_page("source", offset=offset)
    assert "private-invalid" not in str(caught.value)
    assert client.calls == []


@pytest.mark.parametrize("response,code", [
    (None, "response"),
    (([],), "response"),
    (([raw(1), raw(2)], None), "page_size"),
    (([], 5), "cursor"),
    (([raw(1)], True), "point_id"),
    (([SimpleNamespace(id=1, payload=None)], None), "payload"),
    (([raw(True)], None), "point_id"),
    (([raw(1, "")], None), "logical_id"),
    (([raw(1, "private\nvalue")], None), "logical_id"),
])
def test_malformed_response_fails_closed(response, code):
    store, _ = store_for([response])
    with pytest.raises(VectorScanError, match=code):
        store.scroll_page("source", page_size=1)


def test_duplicate_physical_ids_are_rejected_even_with_different_logical_ids():
    store, _ = store_for([([raw(1, "a"), raw(1, "b")], None)])
    with pytest.raises(VectorScanError, match="duplicate_point"):
        store.scroll_page("source")


def test_uuid_objects_and_strings_remain_distinct_from_integer_ids():
    key = uuid.uuid4()
    store, _ = store_for([([raw(key)], key), ([raw(0)], None)])
    first = store.scroll_page("source")
    assert first.records[0].storage_id == str(key)
    assert first.next_offset == str(key)
    assert store.scroll_page("source", offset=first.next_offset).records[0].storage_id == 0


def test_repeated_cursor_and_multi_page_cycle_are_errors():
    store, _ = store_for([([raw(0)], 0)])
    with pytest.raises(VectorScanError, match="cursor"):
        store.scroll_page("source", offset=0)
    store, client = store_for([([raw(0)], 1), ([raw(1)], 2), ([raw(2)], 1)])
    pages = store.iter_scroll_pages("source")
    next(pages)
    next(pages)
    with pytest.raises(VectorScanError, match="cursor"):
        next(pages)
    assert len(client.calls) == 3


@pytest.mark.parametrize("maximum", [0, -1, True, 1.0, 1_000_001])
def test_invalid_page_budget_makes_no_remote_call(maximum):
    store, client = store_for([])
    with pytest.raises(VectorScanError, match="max_pages"):
        list(store.iter_scroll_pages("source", max_pages=maximum))
    assert client.calls == []


def test_page_budget_does_not_silently_validate_a_truncated_inventory():
    store, client = store_for([([raw(0)], 1)])
    pages = store.iter_scroll_pages("source", max_pages=1)
    assert next(pages).next_offset == 1
    with pytest.raises(VectorScanError, match="page_limit"):
        next(pages)
    assert len(client.calls) == 1


def test_terminal_empty_page_is_valid_even_at_budget_boundary():
    store, _ = store_for([([], None)])
    assert list(store.iter_scroll_pages("source", max_pages=1))[0].records == ()


def test_transport_failure_after_a_page_is_not_hidden_as_end_of_scan():
    store, client = store_for([([raw(0)], 1), TimeoutError("timed out")])
    pages = store.iter_scroll_pages("source")
    next(pages)
    with pytest.raises(RAGConnectionError):
        next(pages)
    assert len(client.calls) == 2


def test_authentication_error_uses_existing_sanitization():
    error = RuntimeError("api_key=private-key")
    error.status_code = 401
    store, _ = store_for([error], api_key="private-key")
    with pytest.raises(RAGAuthenticationError) as caught:
        store.scroll_page("source")
    assert "private-key" not in str(caught.value)


def test_uuid_spelling_variants_cannot_hide_cursor_cycles_or_duplicate_records():
    key = uuid.uuid4()
    store, _ = store_for([([raw(0)], key.hex.upper())])
    with pytest.raises(VectorScanError, match="cursor"):
        store.scroll_page("source", offset=str(key))
    store, _ = store_for([([raw(str(key)), raw(key.hex)], None)])
    with pytest.raises(VectorScanError, match="duplicate_point"):
        store.scroll_page("source")
    store, _ = store_for([([raw(0)], str(key)), ([raw(1)], 2), ([raw(2)], key.hex)])
    pages = store.iter_scroll_pages("source")
    next(pages)
    next(pages)
    with pytest.raises(VectorScanError, match="cursor"):
        next(pages)


def test_native_unsigned_integer_upper_bound_is_preserved():
    identifier = 2**64 - 1
    store, client = store_for([([raw(identifier)], None)])
    page = store.scroll_page("source", offset=identifier)
    assert type(client.calls[0]["offset"]) is int
    assert page.records[0].storage_id == identifier
    assert page.records[0].logical_id == str(identifier)


def test_page_retries_keep_the_same_cursor_and_scope():
    client = Client([TimeoutError("transient"), ([raw(7)], None)])
    store = QdrantVectorStore(client=client, retry_delays=(0,))
    page = store.scroll_page("source", {"rag_namespace": "user-a"}, offset=7, page_size=1)
    assert page.records[0].storage_id == 7
    assert len(client.calls) == 2
    assert client.calls[0] == client.calls[1]


@pytest.mark.parametrize("logical_id", [None, 4, "x" * 513, "secret" + chr(127)])
def test_present_invalid_logical_id_is_not_replaced_by_physical_fallback(logical_id):
    row = raw(1)
    row.payload["_vector_store_id"] = logical_id
    store, _ = store_for([([row], None)])
    with pytest.raises(VectorScanError) as caught:
        store.scroll_page("source")
    assert caught.value.code == "logical_id"
    assert str(caught.value) == "Qdrant scan rejected: logical_id"


def test_embedded_qdrant_round_trip_without_a_service():
    from qdrant_client import QdrantClient, models

    client = QdrantClient(location=":memory:")
    try:
        client.create_collection("source", vectors_config=models.VectorParams(size=2, distance="Cosine"))
        client.upsert("source", points=[
            models.PointStruct(id=i, vector=[1.0, 0.0], payload={
                "_vector_store_id": f"doc-{i}", "rag_namespace": "user-a" if i < 5 else "user-b"
            }) for i in range(7)
        ])
        store = QdrantVectorStore(client=client, retry_delays=())
        pages = list(store.iter_scroll_pages("source", {"rag_namespace": "user-a"}, page_size=2))
        assert [record.storage_id for page in pages for record in page.records] == list(range(5))
        assert [record.logical_id for page in pages for record in page.records] == [
            f"doc-{i}" for i in range(5)
        ]
        assert all(len(page.records) <= 2 for page in pages)
        assert pages[-1].next_offset is None
        assert client.count("source").count == 7
    finally:
        client.close()
