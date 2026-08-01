import uuid

import pytest

from hello_agents.memory.rag.errors import RAGConfigError, sanitize_qdrant_url
from hello_agents.memory.rag.contracts import DocumentSegment
from hello_agents.memory.rag.prepare import (
    PROJECT_POINT_NAMESPACE_UUID,
    canonical_json,
    prepare_document_chunks,
    qdrant_point_id,
)
from hello_agents.memory.rag.pipeline import (
    SimpleRAGPipeline,
    create_rag_pipeline,
    resolve_qdrant_collection,
)
from hello_agents.memory.storage.vector_store import InMemoryVectorStore


def test_backend_errors_and_url_sanitizer():
    raw = "https://user:secret@example.com:6333/path?api_key=abc&token=def&x=1"

    sanitized = sanitize_qdrant_url(raw)

    assert sanitized == "https://example.com:6333/path?api_key=***&token=***&x=1"
    assert "secret" not in sanitized
    assert "abc" not in sanitized
    assert "def" not in sanitized


def test_prepare_document_chunks_assigns_global_indexes_and_stable_ids():
    segments = [
        DocumentSegment("alpha beta", {"page_number": 1, "file_name": "a.pdf"}),
        DocumentSegment("gamma", {"page_number": 2, "file_name": "a.pdf"}),
    ]

    chunks = prepare_document_chunks(
        document_id="doc-1",
        segments=segments,
        rag_namespace="ns-1",
        split_text=lambda text: text.split(),
        embed_text=lambda text: [float(len(text)), 0.0],
    )

    assert PROJECT_POINT_NAMESPACE_UUID == uuid.UUID("c273c00a-40ac-47a9-b475-164f135ada18")
    assert canonical_json(["ns", "doc:1", 0]) == '["ns","doc:1",0]'
    assert [chunk.content for chunk in chunks] == ["alpha", "beta", "gamma"]
    assert [chunk.metadata["chunk_index"] for chunk in chunks] == [0, 1, 2]
    assert [chunk.metadata["page_number"] for chunk in chunks] == [1, 1, 2]
    assert chunks[0].id == qdrant_point_id("ns-1", "doc-1", 0)
    assert chunks[0].metadata["rag_namespace"] == "ns-1"
    assert chunks[0].metadata["created_at"].endswith("Z")
    assert chunks[0].metadata["updated_at"].endswith("Z")
    assert chunks[0].metadata["document_version"] == 1


def test_json_replace_document_preserves_default_behavior(tmp_path, monkeypatch):
    monkeypatch.delenv("RAG_BACKEND", raising=False)
    cache_path = tmp_path / "rag.json"
    pipeline = create_rag_pipeline(collection_name="c", rag_namespace="ns", cache_path=str(cache_path))

    assert isinstance(pipeline, SimpleRAGPipeline)

    result = pipeline.replace_document(
        "doc-1",
        [
            DocumentSegment("alpha beta", {"page_number": 1, "file_name": "a.pdf"}),
            DocumentSegment("gamma", {"page_number": 2, "file_name": "a.pdf"}),
        ],
    )

    assert result["success"] is True
    assert result["document_id"] == "doc-1"
    assert result["chunks_added"] == 2
    assert [chunk["metadata"]["chunk_index"] for chunk in pipeline.chunks] == [0, 1]
    assert [chunk["metadata"]["page_number"] for chunk in pipeline.chunks] == [1, 2]

    restarted = create_rag_pipeline(collection_name="c", rag_namespace="ns", cache_path=str(cache_path))
    assert restarted.stats()["document_count"] == 1
    assert restarted.search("alpha", document_id="doc-1")


def test_factory_rejects_invalid_backend(monkeypatch):
    monkeypatch.setenv("RAG_BACKEND", "sqlite")

    with pytest.raises(RAGConfigError, match="Unsupported RAG_BACKEND"):
        create_rag_pipeline()


def test_qdrant_backend_requires_url(monkeypatch):
    monkeypatch.setenv("RAG_BACKEND", "qdrant")
    monkeypatch.delenv("QDRANT_URL", raising=False)

    with pytest.raises(RAGConfigError, match="QDRANT_URL is required"):
        create_rag_pipeline()


def test_qdrant_backend_accepts_injected_client_without_url(monkeypatch):
    captured = {}

    class StubQdrantPipeline:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    client = object()
    monkeypatch.setenv("RAG_BACKEND", "qdrant")
    monkeypatch.delenv("QDRANT_URL", raising=False)
    monkeypatch.setattr("hello_agents.memory.rag.pipeline.QdrantRAGPipeline", StubQdrantPipeline)

    create_rag_pipeline(rag_namespace="ns", qdrant_client=client)

    assert captured["qdrant_client"] is client
    assert captured["qdrant_url"] is None
    assert captured["rag_namespace"] == "ns"


def test_qdrant_backend_accepts_vector_store_without_client_or_url(monkeypatch):
    monkeypatch.setenv("RAG_BACKEND", "qdrant")
    monkeypatch.delenv("QDRANT_URL", raising=False)
    store = InMemoryVectorStore()

    pipeline = create_rag_pipeline(
        rag_namespace="ns",
        vector_store=store,
    )

    assert pipeline.vector_store is store


def test_qdrant_collection_env_overrides_constructor(monkeypatch):
    captured = {}

    class StubQdrantPipeline:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setenv("RAG_BACKEND", "qdrant")
    monkeypatch.setenv("QDRANT_URL", "http://localhost:6333")
    monkeypatch.setenv("QDRANT_COLLECTION", "from_env")
    monkeypatch.setattr("hello_agents.memory.rag.pipeline.QdrantRAGPipeline", StubQdrantPipeline)

    create_rag_pipeline(collection_name="from_constructor", rag_namespace="ns")

    assert captured["collection_name"] == "from_env"
    assert captured["rag_namespace"] == "ns"


@pytest.mark.parametrize("value", [" ", "bad/name", "bad.name", "x" * 256])
def test_qdrant_collection_validation_rejects_invalid_names(monkeypatch, value):
    monkeypatch.setenv("QDRANT_COLLECTION", value)
    with pytest.raises(RAGConfigError):
        resolve_qdrant_collection()


@pytest.mark.parametrize("namespace", ["", " ", None])
def test_factory_rejects_empty_namespace(monkeypatch, namespace):
    monkeypatch.delenv("RAG_BACKEND", raising=False)
    with pytest.raises(RAGConfigError, match="rag_namespace cannot be empty"):
        create_rag_pipeline(rag_namespace=namespace)


def test_json_empty_replace_preserves_existing_document(tmp_path, monkeypatch):
    monkeypatch.delenv("RAG_BACKEND", raising=False)
    pipeline = create_rag_pipeline(rag_namespace="ns", cache_path=str(tmp_path / "rag.json"))
    pipeline.replace_document("doc-1", [DocumentSegment("alpha", {})])

    result = pipeline.replace_document("doc-1", [DocumentSegment(" ", {})])

    assert result["success"] is False
    assert pipeline.stats()["chunk_count"] == 1


def test_json_summary_samples_document_start_middle_and_end(tmp_path, monkeypatch):
    monkeypatch.delenv("RAG_BACKEND", raising=False)
    pipeline = create_rag_pipeline(
        rag_namespace="ns",
        cache_path=str(tmp_path / "rag.json"),
    )
    pipeline.replace_document(
        "doc-1",
        [
            DocumentSegment(f"chunk-{index}", {"page_number": index + 1})
            for index in range(9)
        ],
    )

    results = pipeline.get_document_summary_context("doc-1", limit=3)

    assert [result["content"] for result in results] == [
        "chunk-0",
        "chunk-4",
        "chunk-8",
    ]
