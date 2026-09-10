from pathlib import Path
from dataclasses import replace

import pytest

from hello_agents.memory.rag.embedding_profile import EmbeddingFailure
from hello_agents.memory.rag.embedding_runtime import build_rag_embedding
from hello_agents.memory.rag.errors import RAGConfigError
from hello_agents.memory.rag.index_identity import IndexIdentity
from hello_agents.memory.rag.index_registry import (
    IndexRecord, IndexRegistry, IndexValidation,
)
from hello_agents.memory.rag.json_index_cache import JsonIndexCache
from hello_agents.memory.rag.pipeline import SimpleRAGPipeline, create_rag_pipeline
from hello_agents.memory.rag.qdrant_pipeline import QdrantRAGPipeline
from hello_agents.memory.storage.vector_store import InMemoryVectorStore


VALUES = {
    "RAG_EMBEDDING_PROVIDER": "siliconflow",
    "RAG_EMBEDDING_API_KEY": "fake-private-key",
    "RAG_EMBEDDING_MODEL": "BAAI/bge-m3",
    "RAG_EMBEDDING_DIMENSION": "1024",
    "RAG_EMBEDDING_REVISION": "siliconflow-bge-m3-v1",
}


def _activate(data_root, identity):
    registry = IndexRegistry(data_root)
    registry.save([IndexRecord(
        identity=identity,
        source_index=identity.base_collection,
        migration_id="migration-20260903",
        validation=IndexValidation(
            passed=True,
            checked_at="2026-09-03T12:00:00Z",
            chunk_count=0,
            content_digest="c" * 64,
            scope_leaks=0,
        ),
        state="active",
    )])
    return registry


def test_factory_keeps_simple_provider_on_legacy_path(tmp_path):
    pipeline = create_rag_pipeline(
        backend="json",
        cache_path=str(tmp_path / "legacy.json"),
        data_root=tmp_path,
        embedding_values={"RAG_EMBEDDING_PROVIDER": "simple"},
    )
    assert isinstance(pipeline, SimpleRAGPipeline)
    assert pipeline.index_identity is None


def test_factory_wires_managed_json_from_explicit_data_root(tmp_path):
    data_root = (tmp_path / "data").resolve()
    cache_path = (tmp_path / "rag.json").resolve()
    runtime = build_rag_embedding(VALUES, backend="json")
    identity = IndexIdentity("json", "docs", runtime.profile)
    _activate(data_root, identity)
    JsonIndexCache(cache_path, identity, "user-a").write([])

    pipeline = create_rag_pipeline(
        backend="json", collection_name="docs", rag_namespace="user-a",
        cache_path=str(cache_path), data_root=data_root,
        embedding_values=VALUES,
    )

    assert isinstance(pipeline, SimpleRAGPipeline)
    assert pipeline.index_identity == identity
    assert pipeline.index_registry.path == (
        data_root / "vector_indexes" / "rag" / "registry.json"
    )


def test_factory_wires_managed_qdrant_to_existing_physical_collection(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("QDRANT_COLLECTION", raising=False)
    data_root = (tmp_path / "data").resolve()
    runtime = build_rag_embedding(VALUES, backend="qdrant")
    identity = IndexIdentity("qdrant", "docs", runtime.profile)
    _activate(data_root, identity)
    store = InMemoryVectorStore()
    store.ensure_collection(identity.physical_collection, 1024, "Cosine")

    pipeline = create_rag_pipeline(
        backend="qdrant", collection_name="docs", rag_namespace="user-a",
        data_root=data_root, embedding_values=VALUES, vector_store=store,
    )

    assert isinstance(pipeline, QdrantRAGPipeline)
    assert pipeline.collection_name == identity.physical_collection
    assert pipeline.index_identity == identity


def test_managed_factory_requires_absolute_data_root_and_never_falls_back(tmp_path):
    with pytest.raises(RAGConfigError, match="data_root"):
        create_rag_pipeline(
            backend="json", cache_path=str((tmp_path / "rag.json").resolve()),
            data_root="relative", embedding_values=VALUES,
        )
    with pytest.raises(EmbeddingFailure, match="configuration"):
        create_rag_pipeline(
            backend="json", cache_path=str((tmp_path / "rag.json").resolve()),
            data_root=tmp_path.resolve(),
            embedding_values={"RAG_EMBEDDING_PROVIDER": "unknown"},
        )
    with pytest.raises(EmbeddingFailure, match="configuration"):
        create_rag_pipeline(
            backend="json", cache_path=str((tmp_path / "rag.json").resolve()),
            data_root=tmp_path.resolve(),
            embedding_values={"RAG_EMBEDDING_PROVIDER": "siliconflow"},
        )


@pytest.mark.parametrize("values", [{}, {"RAG_EMBEDDING_PROVIDER": "simple"}])
@pytest.mark.parametrize("state", ["active", "failed"])
def test_simple_cannot_bypass_previously_activated_remote_registry(tmp_path, values, state):
    identity = IndexIdentity("qdrant", "docs", build_rag_embedding(VALUES, backend="qdrant").profile)
    registry = _activate(tmp_path, identity)
    registry.put(replace(registry.require_active(identity), state=state))
    with pytest.raises(RAGConfigError, match="downgrade"):
        create_rag_pipeline(backend="json", cache_path=str(tmp_path / "rag.json"),
                            data_root=tmp_path, embedding_values=values)


def test_simple_cannot_hide_corrupt_registry(tmp_path):
    registry = IndexRegistry(tmp_path)
    registry.path.parent.mkdir(parents=True)
    registry.path.write_text("{broken", encoding="utf-8")
    with pytest.raises(RAGConfigError):
        create_rag_pipeline(backend="json", cache_path=str(tmp_path / "rag.json"),
                            data_root=tmp_path, embedding_values={})


@pytest.mark.parametrize("state", ["planned", "building", "validated"])
def test_simple_remains_available_before_remote_activation(tmp_path, state):
    identity = IndexIdentity("qdrant", "docs", build_rag_embedding(VALUES, backend="qdrant").profile)
    registry = _activate(tmp_path, identity)
    registry.put(replace(registry.require_active(identity), state=state))
    pipeline = create_rag_pipeline(backend="json", cache_path=str(tmp_path / "rag.json"),
                                   data_root=tmp_path, embedding_values={})
    assert pipeline.index_identity is None
