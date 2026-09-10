import json
from dataclasses import replace

import httpx
import pytest

from hello_agents.memory.rag.contracts import DocumentSegment
from hello_agents.memory.rag.embedding_runtime import build_rag_embedding
from hello_agents.memory.rag.errors import RAGConfigError
from hello_agents.memory.rag.index_identity import IndexIdentity
from hello_agents.memory.rag.index_registry import (
    IndexRecord, IndexRegistry, IndexRegistryError, IndexValidation,
)
from hello_agents.memory.rag.qdrant_pipeline import QdrantRAGPipeline
from hello_agents.memory.storage.vector_store import (
    InMemoryVectorStore, VectorPoint,
)


def _response(count):
    return {
        "model": "BAAI/bge-m3",
        "data": [
            {"index": index, "embedding": [1.0] + [0.0] * 1023}
            for index in range(count)
        ],
    }


def _runtime(handler, *, revision="siliconflow-bge-m3-v1", backend="qdrant"):
    return build_rag_embedding(
        {
            "RAG_EMBEDDING_PROVIDER": "siliconflow",
            "RAG_EMBEDDING_API_KEY": "fake-private-key",
            "RAG_EMBEDDING_REVISION": revision,
        },
        backend=backend,
        transport=httpx.MockTransport(handler),
    )


def _active(identity):
    return IndexRecord(
        identity=identity,
        source_index="rag_knowledge_base",
        migration_id="migration-20260903",
        validation=IndexValidation(
            passed=True,
            checked_at="2026-09-03T12:00:00Z",
            chunk_count=0,
            content_digest="b" * 64,
            scope_leaks=0,
        ),
        state="active",
    )


def _managed(tmp_path, handler=None, store=None):
    calls = []

    def default_handler(request):
        body = json.loads(request.content)
        calls.append(body["input"])
        return httpx.Response(200, json=_response(len(body["input"])))

    runtime = _runtime(handler or default_handler)
    identity = IndexIdentity("qdrant", "docs", runtime.profile)
    registry = IndexRegistry(tmp_path / "data")
    registry.save([_active(identity)])
    store = store or InMemoryVectorStore()
    store.ensure_collection(identity.physical_collection, 1024, "Cosine")
    pipeline = QdrantRAGPipeline(
        collection_name="docs",
        rag_namespace="user-a",
        vector_store=store,
        embedding_runtime=runtime,
        index_registry=registry,
    )
    return pipeline, registry, store, calls


def test_managed_startup_requires_complete_config_registry_and_physical_store(tmp_path):
    runtime = _runtime(lambda request: httpx.Response(500))
    registry = IndexRegistry(tmp_path / "data")
    with pytest.raises(RAGConfigError, match="backend"):
        QdrantRAGPipeline(
            collection_name="docs", vector_store=InMemoryVectorStore(),
            embedding_runtime=_runtime(lambda request: httpx.Response(500), backend="json"),
            index_registry=registry,
        )
    with pytest.raises(RAGConfigError, match="configuration"):
        QdrantRAGPipeline(
            collection_name="docs", vector_store=InMemoryVectorStore(),
            embedding_runtime=runtime,
        )
    with pytest.raises(IndexRegistryError, match="missing"):
        QdrantRAGPipeline(
            collection_name="docs", vector_store=InMemoryVectorStore(),
            embedding_runtime=runtime, index_registry=registry,
        )

    identity = IndexIdentity("qdrant", "docs", runtime.profile)
    registry.save([replace(_active(identity), state="validated")])
    with pytest.raises(IndexRegistryError, match="inactive"):
        QdrantRAGPipeline(
            collection_name="docs", vector_store=InMemoryVectorStore(),
            embedding_runtime=runtime, index_registry=registry,
        )

    registry.save([_active(identity)])
    empty_store = InMemoryVectorStore()
    with pytest.raises(Exception, match="not found"):
        QdrantRAGPipeline(
            collection_name="docs", vector_store=empty_store,
            embedding_runtime=runtime, index_registry=registry,
        )
    assert identity.physical_collection not in empty_store._collections

    incompatible = InMemoryVectorStore()
    incompatible.ensure_collection(
        identity.physical_collection, 384, "Cosine"
    )
    with pytest.raises(Exception, match="expected 1024"):
        QdrantRAGPipeline(
            collection_name="docs", vector_store=incompatible,
            embedding_runtime=runtime, index_registry=registry,
        )


def test_managed_pipeline_uses_physical_collection_and_shared_runtime(tmp_path):
    pipeline, _, store, calls = _managed(tmp_path)
    pipeline._split_text = lambda text: text.split("|")
    result = pipeline.replace_document(
        "doc-a", [DocumentSegment("first|second", {"file_name": "public.txt"})]
    )
    hits = pipeline.search("question", document_id="doc-a")

    assert result["chunks_added"] == 2
    assert [len(call) for call in calls] == [2, 1]
    assert pipeline.collection_name == pipeline.index_identity.physical_collection
    assert set(store._collections) == {pipeline.collection_name}
    assert all(
        hit["metadata"]["embedding_fingerprint"]
        == pipeline.index_identity.profile.fingerprint
        for hit in hits
    )


def test_managed_metadata_is_protected_before_remote_write(tmp_path):
    pipeline, _, store, _ = _managed(tmp_path)
    pipeline.replace_document("doc-a", [DocumentSegment("public", {
        "document_id": "forged",
        "rag_namespace": "user-b",
        "embedding_fingerprint": "forged",
        "api_key": "metadata-private-key",
        "nested": {"authorization": "private-token", "safe": "kept"},
    })])
    point = store.scroll(pipeline.collection_name)[0]
    metadata = point.payload["metadata"]
    assert point.payload["document_id"] == "doc-a"
    assert point.payload["rag_namespace"] == "user-a"
    assert metadata["embedding_fingerprint"] == pipeline.index_identity.profile.fingerprint
    assert "api_key" not in metadata
    assert metadata["nested"] == {"safe": "kept"}
    assert "fake-private-key" not in repr(point.payload)


def test_managed_pipeline_rejects_forged_remote_results(tmp_path):
    pipeline, _, store, _ = _managed(tmp_path)
    store.upsert(pipeline.collection_name, [VectorPoint(
        "forged",
        [1.0] + [0.0] * 1023,
        {
            "content": "forged",
            "document_id": "doc-a",
            "rag_namespace": "user-a",
            "chunk_index": 0,
            "metadata": {"embedding_fingerprint": "0" * 64},
        },
    )])
    with pytest.raises(Exception, match="point"):
        pipeline.search("question")
    with pytest.raises(Exception, match="point"):
        pipeline.list_document_ids()


def test_registry_switch_blocks_existing_qdrant_pipeline(tmp_path):
    pipeline, registry, _, _ = _managed(tmp_path)
    changed = _runtime(
        lambda request: httpx.Response(500), revision="siliconflow-bge-m3-v2"
    )
    registry.save([_active(IndexIdentity("qdrant", "docs", changed.profile))])
    with pytest.raises(IndexRegistryError):
        pipeline.stats()
    with pytest.raises(IndexRegistryError):
        pipeline.replace_document("doc-a", [DocumentSegment("blocked", {})])


class _AmbiguousFailureStore(InMemoryVectorStore):
    fail_writes = False

    def upsert(self, collection_name, points):
        points = list(points)
        super().upsert(collection_name, points)
        if self.fail_writes:
            raise RuntimeError("ambiguous remote failure")


def test_ambiguous_managed_mutation_fails_registry_closed(tmp_path):
    store = _AmbiguousFailureStore()
    pipeline, registry, _, _ = _managed(tmp_path, store=store)
    store.fail_writes = True

    with pytest.raises(RuntimeError, match="ambiguous"):
        pipeline.replace_document("doc-a", [DocumentSegment("content", {})])

    with pytest.raises(IndexRegistryError, match="inactive"):
        registry.require_active(pipeline.index_identity)
    with pytest.raises(RAGConfigError, match="failed"):
        pipeline.search("question")


def test_legacy_qdrant_mode_still_creates_collection(tmp_path):
    store = InMemoryVectorStore()
    pipeline = QdrantRAGPipeline(
        collection_name="legacy", rag_namespace="user-a", vector_store=store
    )
    assert "legacy" in store._collections
    assert pipeline.index_identity is None


def test_managed_nonempty_stats_and_scope_survive_restart(tmp_path):
    pipeline, registry, store, _ = _managed(tmp_path)
    pipeline.replace_document("doc-a", [DocumentSegment("public", {})])
    restarted = QdrantRAGPipeline(collection_name="docs", rag_namespace="user-a",
        vector_store=store, embedding_runtime=pipeline.embedding_runtime, index_registry=registry)
    assert restarted.stats()["chunk_count"] == 1
    assert restarted.stats()["document_count"] == 1


def test_managed_query_rejects_out_of_document_scope_result(tmp_path, monkeypatch):
    pipeline, _, store, _ = _managed(tmp_path)
    pipeline.replace_document("doc-b", [DocumentSegment("public", {})])
    original = store.search
    monkeypatch.setattr(store, "search", lambda *args, **kwargs: original(
        *args, **{**kwargs, "filters": None}))
    with pytest.raises(RAGConfigError, match="point"):
        pipeline.search("question", document_id="doc-a")


def test_registry_switch_during_embedding_prevents_write(tmp_path):
    pipeline, registry, store, _ = _managed(tmp_path)
    changed = _runtime(lambda request: httpx.Response(500), revision="next-v2")
    def handler(request):
        registry.put(_active(IndexIdentity("qdrant", "docs", changed.profile)))
        return httpx.Response(200, json=_response(len(json.loads(request.content)["input"])))
    pipeline.embedding_runtime = _runtime(handler)
    with pytest.raises(IndexRegistryError):
        pipeline.replace_document("doc-a", [DocumentSegment("public", {})])
    assert store.count(pipeline.collection_name) == 0


def test_failed_old_writer_cannot_overwrite_new_registry_identity(tmp_path, monkeypatch):
    pipeline, registry, store, _ = _managed(tmp_path)
    changed = IndexIdentity("qdrant", "docs", _runtime(
        lambda request: httpx.Response(500), revision="next-v2").profile)
    def write_failure(*args):
        registry.put(_active(changed))
        raise RuntimeError("ambiguous")
    monkeypatch.setattr(store, "upsert", write_failure)
    with pytest.raises(RuntimeError, match="ambiguous"):
        pipeline.replace_document("doc-a", [DocumentSegment("public", {})])
    assert registry.require_active(changed).identity == changed


def test_registry_switch_during_query_embedding_blocks_search(tmp_path, monkeypatch):
    pipeline, registry, store, _ = _managed(tmp_path)
    pipeline.replace_document("doc-a", [DocumentSegment("public", {})])
    changed = IndexIdentity("qdrant", "docs", _runtime(
        lambda request: httpx.Response(500), revision="next-v2").profile)

    def handler(request):
        registry.put(_active(changed))
        return httpx.Response(200, json=_response(1))

    def unexpected_search(*args, **kwargs):
        pytest.fail("stale query must not reach the vector store")

    pipeline.embedding_runtime = _runtime(handler)
    monkeypatch.setattr(store, "search", unexpected_search)
    with pytest.raises(IndexRegistryError):
        pipeline.search("question")
