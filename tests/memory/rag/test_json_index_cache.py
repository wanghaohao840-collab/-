from copy import deepcopy
import json
from pathlib import Path

import pytest

import hello_agents.memory.rag.json_index_cache as cache_module
from hello_agents.memory.rag.embedding_runtime import build_rag_embedding
from hello_agents.memory.rag.index_identity import IndexIdentity
from hello_agents.memory.rag.json_index_cache import (
    JsonIndexCache, JsonIndexCacheError, versioned_cache_path,
)
from hello_agents.memory.storage.vector_store import VectorPoint


def identity(*, revision="siliconflow-bge-m3-v1"):
    runtime = build_rag_embedding(
        {
            "RAG_EMBEDDING_PROVIDER": "siliconflow",
            "RAG_EMBEDDING_API_KEY": "fake-private-key",
            "RAG_EMBEDDING_REVISION": revision,
        },
        backend="json",
    )
    return IndexIdentity("json", "pdf_learning_collection", runtime.profile)


def point(*, fingerprint=None, namespace="pdf_user-a", document_id="doc-a"):
    expected = identity()
    fingerprint = fingerprint or expected.profile.fingerprint
    metadata = {
        "memory_id": "doc-a_0",
        "document_id": document_id,
        "chunk_index": 0,
        "content": "public synthetic content",
        "memory_type": "rag_chunk",
        "is_rag_data": True,
        "data_source": "rag_pipeline",
        "rag_namespace": namespace,
        "created_at": "2026-09-03T12:00:00Z",
        "updated_at": "2026-09-03T12:00:00Z",
        "document_version": 1,
        "embedding_fingerprint": fingerprint,
        "file_name": "public.txt",
    }
    return VectorPoint(
        "doc-a_0",
        [1.0] + [0.0] * 1023,
        metadata,
    )


def test_versioned_path_binds_namespace_and_identity_and_preserves_legacy(tmp_path):
    legacy = tmp_path / "rag_cache.json"
    legacy.write_text("legacy", encoding="utf-8")
    first = versioned_cache_path(legacy, identity(), "pdf_user-a")
    rotated_key = versioned_cache_path(legacy, identity(), "pdf_user-a")
    another_user = versioned_cache_path(legacy, identity(), "pdf_user-b")
    another_revision = versioned_cache_path(
        legacy, identity(revision="v2"), "pdf_user-a"
    )

    assert first == rotated_key
    assert first != legacy
    assert first != another_user
    assert first != another_revision
    assert legacy.read_text(encoding="utf-8") == "legacy"


def test_round_trip_is_strict_and_contains_no_key(tmp_path):
    cache = JsonIndexCache(
        tmp_path / "rag_cache.json", identity(), "pdf_user-a"
    )
    expected = point()

    cache.write([expected])

    loaded = cache.load()
    assert loaded == [expected]
    raw = cache.path.read_text(encoding="utf-8")
    assert "fake-private-key" not in raw
    document = json.loads(raw)
    assert document["schema_version"] == 2
    assert document["chunk_count"] == 1


def test_explicit_empty_index_round_trips_as_empty(tmp_path):
    cache = JsonIndexCache(
        tmp_path / "rag_cache.json", identity(), "pdf_user-a"
    )
    cache.write([])
    assert cache.load() == []


@pytest.mark.parametrize("mutation", [
    "broken_json", "wrong_count", "wrong_identity", "wrong_namespace",
    "wrong_dimension", "missing_vector", "zero_vector", "nan_vector",
    "forged_document", "forged_namespace", "forged_fingerprint",
    "duplicate_id", "reserved_id",
    "secret_metadata",
])
def test_corruption_never_becomes_an_empty_or_reembedded_index(tmp_path, mutation):
    cache = JsonIndexCache(
        tmp_path / "rag_cache.json", identity(), "pdf_user-a"
    )
    cache.write([point()])
    if mutation == "broken_json":
        cache.path.write_text("{broken", encoding="utf-8")
    else:
        document = json.loads(cache.path.read_text(encoding="utf-8"))
        if mutation == "wrong_count":
            document["chunk_count"] = 0
        elif mutation == "wrong_identity":
            document["identity"] = identity(revision="v2").to_dict()
        elif mutation == "wrong_namespace":
            document["rag_namespace"] = "pdf_user-b"
        elif mutation == "wrong_dimension":
            document["chunks"][0]["vector"] = [1.0]
        elif mutation == "missing_vector":
            document["chunks"][0]["vector"] = []
        elif mutation == "zero_vector":
            document["chunks"][0]["vector"] = [0.0] * 1024
        elif mutation == "nan_vector":
            document["chunks"][0]["vector"][0] = float("nan")
        elif mutation == "forged_document":
            document["chunks"][0]["metadata"]["document_id"] = "doc-b"
        elif mutation == "forged_namespace":
            document["chunks"][0]["metadata"]["rag_namespace"] = "pdf_user-b"
        elif mutation == "forged_fingerprint":
            document["chunks"][0]["metadata"]["embedding_fingerprint"] = "0" * 64
        elif mutation == "reserved_id":
            document["chunks"][0]["metadata"]["_vector_store_id"] = "forged"
        elif mutation == "secret_metadata":
            document["chunks"][0]["metadata"]["x-api-key"] = "private"
        elif mutation == "duplicate_id":
            document["chunks"].append(deepcopy(document["chunks"][0]))
            document["chunk_count"] = 2
        cache.path.write_text(
            json.dumps(document, allow_nan=True), encoding="utf-8"
        )

    with pytest.raises(JsonIndexCacheError):
        cache.load()


def test_missing_cache_fails_instead_of_becoming_empty(tmp_path):
    cache = JsonIndexCache(
        tmp_path / "rag_cache.json", identity(), "pdf_user-a"
    )
    with pytest.raises(JsonIndexCacheError, match="missing"):
        cache.load()


def test_failed_replace_preserves_previous_version(tmp_path, monkeypatch):
    cache = JsonIndexCache(
        tmp_path / "rag_cache.json", identity(), "pdf_user-a"
    )
    cache.write([point()])
    before = cache.path.read_bytes()

    monkeypatch.setattr(
        cache_module.os, "replace",
        lambda source, destination: (_ for _ in ()).throw(OSError("forced")),
    )
    with pytest.raises(JsonIndexCacheError, match="write"):
        cache.write([])

    assert cache.path.read_bytes() == before
    assert list(cache.path.parent.glob("*.tmp")) == []


def test_full_fingerprint_collision_cannot_overwrite_existing_cache(tmp_path):
    original = JsonIndexCache(
        tmp_path / "rag_cache.json", identity(), "pdf_user-a"
    )
    original.write([point()])
    before = original.path.read_bytes()
    changed = JsonIndexCache(
        tmp_path / "rag_cache.json", identity(revision="v2"), "pdf_user-a"
    )
    changed.path = original.path

    with pytest.raises(JsonIndexCacheError, match="identity"):
        changed.write([])

    assert original.path.read_bytes() == before


@pytest.mark.parametrize(("path", "namespace"), [
    (Path("relative.json"), "pdf_user-a"),
    (Path("relative.json"), ""),
])
def test_cache_requires_explicit_absolute_path_and_namespace(path, namespace):
    with pytest.raises(JsonIndexCacheError):
        JsonIndexCache(path, identity(), namespace)
