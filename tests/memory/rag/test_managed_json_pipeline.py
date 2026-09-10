import json

import httpx
import pytest

from hello_agents.memory.rag.contracts import DocumentSegment
from hello_agents.memory.rag.embedding_profile import EmbeddingFailure
from hello_agents.memory.rag.embedding_runtime import build_rag_embedding
from hello_agents.memory.rag.index_identity import IndexIdentity
from hello_agents.memory.rag.index_registry import (
    IndexRecord, IndexRegistry, IndexRegistryError, IndexValidation,
)
from hello_agents.memory.rag.json_index_cache import JsonIndexCache
from hello_agents.memory.rag.pipeline import SimpleRAGPipeline


def _response(count):
    return {
        "model": "BAAI/bge-m3",
        "data": [
            {"index": index, "embedding": [1.0] + [0.0] * 1023}
            for index in range(count)
        ],
    }


def _runtime(handler, *, revision="siliconflow-bge-m3-v1", backend="json"):
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
        source_index="legacy-cache.json",
        migration_id="migration-20260903",
        validation=IndexValidation(
            passed=True,
            checked_at="2026-09-03T12:00:00Z",
            chunk_count=0,
            content_digest="a" * 64,
            scope_leaks=0,
        ),
        state="active",
    )


def _managed(tmp_path, handler=None):
    calls = []

    def default_handler(request):
        body = json.loads(request.content)
        calls.append((request.url.path, body["input"]))
        return httpx.Response(200, json=_response(len(body["input"])))

    runtime = _runtime(handler or default_handler)
    identity = IndexIdentity("json", "docs", runtime.profile)
    registry = IndexRegistry(tmp_path / "data")
    registry.save([_active(identity)])
    cache = JsonIndexCache(tmp_path / "legacy.json", identity, "user-a")
    cache.write([])
    pipeline = SimpleRAGPipeline(
        collection_name="docs",
        rag_namespace="user-a",
        cache_path=str(tmp_path / "legacy.json"),
        embedding_runtime=runtime,
        index_registry=registry,
    )
    return pipeline, registry, cache, calls


def test_managed_configuration_and_startup_fail_closed(tmp_path):
    runtime = _runtime(lambda request: httpx.Response(500))
    registry = IndexRegistry(tmp_path / "data")

    with pytest.raises(Exception, match="configuration"):
        SimpleRAGPipeline(
            cache_path=str(tmp_path / "legacy.json"),
            embedding_runtime=runtime,
        )
    with pytest.raises(Exception, match="backend"):
        SimpleRAGPipeline(
            cache_path=str(tmp_path / "legacy.json"),
            embedding_runtime=_runtime(lambda request: httpx.Response(500), backend="qdrant"),
            index_registry=registry,
        )
    with pytest.raises(IndexRegistryError, match="missing"):
        SimpleRAGPipeline(
            cache_path=str(tmp_path / "legacy.json"),
            embedding_runtime=runtime,
            index_registry=registry,
        )

    identity = IndexIdentity("json", "rag_knowledge_base", runtime.profile)
    registry.save([IndexRecord(
        identity=identity,
        source_index="legacy-cache.json",
        migration_id="migration-20260903",
        validation=None,
        state="planned",
    )])
    with pytest.raises(IndexRegistryError, match="inactive"):
        SimpleRAGPipeline(
            cache_path=str(tmp_path / "legacy.json"),
            embedding_runtime=runtime,
            index_registry=registry,
        )

    changed = _runtime(
        lambda request: httpx.Response(500), revision="siliconflow-bge-m3-v2"
    )
    registry.save([_active(IndexIdentity(
        "json", "rag_knowledge_base", changed.profile
    ))])
    with pytest.raises(IndexRegistryError, match="identity"):
        SimpleRAGPipeline(
            cache_path=str(tmp_path / "legacy.json"),
            embedding_runtime=runtime,
            index_registry=registry,
        )

    registry.save([_active(identity)])
    with pytest.raises(Exception, match="missing"):
        SimpleRAGPipeline(
            cache_path=str(tmp_path / "legacy.json"),
            embedding_runtime=runtime,
            index_registry=registry,
        )

    cache = JsonIndexCache(
        tmp_path / "legacy.json", identity, "default"
    )
    cache.write([])
    cache.path.write_text("{broken", encoding="utf-8")
    with pytest.raises(Exception, match="unreadable"):
        SimpleRAGPipeline(
            cache_path=str(tmp_path / "legacy.json"),
            embedding_runtime=runtime,
            index_registry=registry,
        )


def test_registry_switch_during_query_embedding_blocks_search(tmp_path):
    pipeline, registry, _, _ = _managed(tmp_path)
    pipeline.replace_document("doc-a", [DocumentSegment("public", {})])
    changed = IndexIdentity("json", "docs", _runtime(
        lambda request: httpx.Response(500), revision="next-v2").profile)

    def handler(request):
        registry.put(_active(changed))
        return httpx.Response(200, json=_response(1))

    pipeline.embedding_runtime = _runtime(handler)
    with pytest.raises(IndexRegistryError):
        pipeline.search("question")


def test_managed_document_and_query_embeddings_share_runtime(tmp_path):
    pipeline, _, cache, calls = _managed(tmp_path)
    pipeline._split_text = lambda text: text.split("\n\n")
    result = pipeline.replace_document(
        "doc-a",
        [DocumentSegment("first\n\nsecond", {"file_name": "public.txt"})],
    )
    hits = pipeline.search("question", limit=1)

    assert result["success"] is True
    assert [kind for kind, _ in calls] == ["/v1/embeddings", "/v1/embeddings"]
    assert len(calls[0][1]) == 2
    assert calls[1][1] == ["question"]
    assert hits[0]["metadata"]["embedding_fingerprint"] == pipeline.index_identity.profile.fingerprint
    assert cache.path == pipeline.cache_path


def test_later_embedding_failure_preserves_live_and_durable_index(tmp_path):
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body["input"])
        if len(requests) == 3:
            return httpx.Response(500)
        return httpx.Response(200, json=_response(len(body["input"])))

    pipeline, _, cache, _ = _managed(tmp_path, handler)
    pipeline.replace_document("doc-a", [DocumentSegment("old", {})])
    before_chunks = pipeline.chunks
    before_bytes = cache.path.read_bytes()
    pipeline._split_text = lambda text: [f"chunk-{index}" for index in range(10)]

    with pytest.raises(EmbeddingFailure):
        pipeline.replace_document("doc-a", [DocumentSegment("new", {})])

    assert pipeline.chunks == before_chunks
    assert cache.path.read_bytes() == before_bytes


def test_cache_failure_preserves_live_and_durable_index(tmp_path, monkeypatch):
    pipeline, _, cache, _ = _managed(tmp_path)
    pipeline.replace_document("doc-a", [DocumentSegment("old", {})])
    before_chunks = pipeline.chunks
    before_bytes = cache.path.read_bytes()

    def fail_write(points):
        raise OSError("forced cache failure")

    monkeypatch.setattr(pipeline._managed_cache, "write", fail_write)
    with pytest.raises(OSError, match="forced"):
        pipeline.replace_document("doc-a", [DocumentSegment("new", {})])

    assert pipeline.chunks == before_chunks
    assert cache.path.read_bytes() == before_bytes


def test_managed_mutations_are_durable_and_metadata_is_protected(tmp_path):
    pipeline, registry, cache, _ = _managed(tmp_path)
    pipeline.add_text("public", document_id="doc-a", metadata={
        "document_id": "forged",
        "content": "forged",
        "rag_namespace": "user-b",
        "chunk_index": 99,
        "document_version": 99,
        "embedding_fingerprint": "forged",
        "_vector_store_id": "forged",
        "api_key": "metadata-private-key",
        "authorization": "Bearer metadata-private-token",
        "nested": {"x-api-key": "nested-private-key", "safe": "kept"},
    })
    point = pipeline.chunks[0]
    assert point["document_id"] == "doc-a"
    assert point["content"] == "public"
    assert point["metadata"]["rag_namespace"] == "user-a"
    assert point["metadata"]["chunk_index"] == 0
    assert point["metadata"]["document_version"] == 1
    assert point["metadata"]["embedding_fingerprint"] == pipeline.index_identity.profile.fingerprint
    assert "_vector_store_id" not in point["metadata"]
    raw_cache = cache.path.read_text(encoding="utf-8")
    assert "fake-private-key" not in raw_cache
    assert "metadata-private-key" not in raw_cache
    assert "metadata-private-token" not in raw_cache
    assert "nested-private-key" not in raw_cache
    assert point["metadata"]["nested"] == {"safe": "kept"}

    pipeline.add_text(
        "second", document_id="doc-a", replace_existing=False
    )
    assert [
        chunk["metadata"]["chunk_index"] for chunk in pipeline.chunks
    ] == [0, 1]

    restarted = SimpleRAGPipeline(
        collection_name="docs", rag_namespace="user-a",
        cache_path=str(tmp_path / "legacy.json"),
        embedding_runtime=pipeline.embedding_runtime,
        index_registry=registry,
    )
    assert restarted.list_document_ids() == ["doc-a"]
    assert restarted.delete_document("doc-a")["chunks_removed"] == 2
    assert restarted.chunks == []
    restarted.add_text("again", document_id="doc-b")
    assert restarted.clear()["success"] is True
    assert JsonIndexCache(
        tmp_path / "legacy.json", pipeline.index_identity, "user-a"
    ).load() == []


def test_registry_switch_blocks_existing_pipeline_and_bypass_hooks(tmp_path):
    pipeline, registry, _, _ = _managed(tmp_path)
    changed_runtime = _runtime(
        lambda request: httpx.Response(500), revision="siliconflow-bge-m3-v2"
    )
    registry.save([_active(IndexIdentity("json", "docs", changed_runtime.profile))])

    with pytest.raises(IndexRegistryError):
        pipeline.stats()
    with pytest.raises(IndexRegistryError):
        pipeline.add_text("blocked", document_id="doc-a")
    with pytest.raises(Exception):
        pipeline.chunks = []
    with pytest.raises(Exception):
        pipeline._save_cache()
    with pytest.raises(Exception):
        pipeline._remove_document_chunks("doc-a")


def test_managed_mode_rejects_nondurable_writes_but_legacy_keeps_them(tmp_path):
    pipeline, _, _, _ = _managed(tmp_path)
    with pytest.raises(Exception, match="save_cache"):
        pipeline.add_text("managed", document_id="doc-a", save_cache=False)
    with pytest.raises(Exception, match="save_cache"):
        pipeline.replace_document(
            "doc-a", [DocumentSegment("managed", {})], save_cache=False
        )

    legacy = SimpleRAGPipeline(cache_path=str(tmp_path / "legacy-only.json"))
    assert legacy.add_text(
        "legacy", document_id="doc-a", save_cache=False
    )["success"] is True
