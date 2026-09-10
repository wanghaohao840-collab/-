import json
import httpx
import pytest

from hello_agents.memory.rag.contracts import DocumentSegment
from hello_agents.memory.rag.embedding_profile import EmbeddingFailure
from hello_agents.memory.rag.embedding_runtime import build_rag_embedding
from hello_agents.memory.rag.prepare import prepare_document_chunks, qdrant_point_id


def runtime(handler):
    return build_rag_embedding(
        {"RAG_EMBEDDING_PROVIDER": "siliconflow", "RAG_EMBEDDING_API_KEY": "fake-secret"},
        backend="qdrant", transport=httpx.MockTransport(handler),
    )


def response(count):
    return {"model": "BAAI/bge-m3", "data": [
        {"index": i, "embedding": [1.0] + [0.0] * 1023} for i in range(count)
    ]}


def test_legacy_metadata_cannot_replace_system_identity():
    prepared = prepare_document_chunks(
        "doc-a", [DocumentSegment("source", {
            "document_id": "doc-b", "rag_namespace": "user-b", "content": "forged",
            "memory_id": "forged", "chunk_index": 900, "document_version": 99,
            "embedding_fingerprint": "forged", "_vector_store_id": "forged",
            "page_number": 7, "file_name": "paper.pdf",
        })], "user-a", lambda text: [text], lambda text: [1.0, 0.0],
    )
    item = prepared[0]
    assert item.id == qdrant_point_id("user-a", "doc-a", 0)
    assert item.metadata["memory_id"] == item.id
    assert item.metadata["document_id"] == "doc-a"
    assert item.metadata["rag_namespace"] == "user-a"
    assert item.metadata["content"] == "source"
    assert item.metadata["chunk_index"] == 0
    assert item.metadata["document_version"] == 1
    assert "embedding_fingerprint" not in item.metadata
    assert "_vector_store_id" not in item.metadata
    assert item.metadata["page_number"] == 7
    assert item.metadata["file_name"] == "paper.pdf"


def test_remote_preparation_batches_and_reports_monotonic_progress():
    calls, progress = [], []
    def handler(request):
        body = json.loads(request.content)
        calls.append(body["input"])
        return httpx.Response(200, json=response(len(body["input"])))
    embedded = runtime(handler)
    prepared = prepare_document_chunks(
        "doc-a", [DocumentSegment(" ".join(str(i) for i in range(10)), {"page_number": 3})],
        "user-a", str.split, None, embedding_runtime=embedded,
        progress_callback=lambda *event: progress.append(event),
    )
    assert [len(batch) for batch in calls] == [8, 2]
    assert [event[1] for event in progress] == [8, 10]
    assert all(event[0] == "embedding" and event[2] == 10 for event in progress)
    assert len(prepared) == 10
    assert all(c.metadata["embedding_fingerprint"] == embedded.profile.fingerprint
               and c.metadata["page_number"] == 3 and len(c.vector) == 1024
               for c in prepared)


def test_later_bad_input_prevents_even_first_remote_batch():
    calls = []
    def forbidden(request):
        calls.append(request)
        pytest.fail("invalid later chunk caused a request")
    with pytest.raises(EmbeddingFailure, match="input"):
        prepare_document_chunks(
            "doc-a", [DocumentSegment("source", {})], "user-a",
            lambda text: ["valid"] * 8 + ["文" * 2001], None,
            embedding_runtime=runtime(forbidden),
        )
    assert calls == []


def test_second_remote_batch_failure_returns_no_prepared_result():
    calls = []
    def handler(request):
        calls.append(request)
        return (httpx.Response(200, json=response(8)) if len(calls) == 1
                else httpx.Response(401))
    with pytest.raises(EmbeddingFailure) as caught:
        prepare_document_chunks(
            "doc-a", [DocumentSegment("source", {})], "user-a",
            lambda text: ["chunk"] * 10, None, embedding_runtime=runtime(handler),
        )
    assert caught.value.status_code == 401
    assert len(calls) == 2


def test_preparation_rejects_ambiguous_embedding_sources():
    with pytest.raises(EmbeddingFailure, match="configuration"):
        prepare_document_chunks(
            "doc", [], "ns", str.split, lambda text: [1.0],
            embedding_runtime=runtime(lambda request: httpx.Response(401)),
        )
