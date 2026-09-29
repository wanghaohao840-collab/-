"""Pinned public RAG methods against disposable PostgreSQL and Qdrant."""
from __future__ import annotations

import os
from uuid import uuid4

import pytest

from app.postgres_coordination import PostgresUserMutationCoordinator
from app.postgres_vector_generations import PostgresVectorGenerationAuthority, VectorScope
from app.published_rag_reads import PublishedRAGReadFactory
from app.vector_generation_service import VectorGenerationService
from hello_agents.memory.rag.embedding_runtime import build_rag_embedding
from hello_agents.memory.rag.index_identity import IndexIdentity
from hello_agents.memory.storage.generation_vector_store import (
    GenerationVectorStore, GenerationVectorStoreError,
)
from hello_agents.memory.storage.vector_store import QdrantVectorStore, VectorPoint
from tests.integration.test_postgres_auth_sessions import shared_database


@pytest.fixture
def rag_setup(shared_database):
    qdrant_url = os.environ.get("GENERATION_QDRANT_TEST_URL")
    if not qdrant_url:
        pytest.skip("GENERATION_QDRANT_TEST_URL is required")
    open_pool, _ = shared_database
    db = open_pool()
    users = (str(uuid4()), str(uuid4()))
    with db.transaction() as cursor:
        for user in users:
            cursor.execute("insert into users values (%s,%s,%s,%s,%s,%s,%s)",
                           (user, user, user, "hash", "active", "now", "now"))
    runtime = build_rag_embedding({}, backend="qdrant")
    identity = IndexIdentity("qdrant", "pinned_rag_" + uuid4().hex,
                             runtime.profile)
    raw = QdrantVectorStore(url=qdrant_url, retry_delays=())
    raw.ensure_collection(identity.physical_collection, runtime.profile.dimension)
    service = VectorGenerationService(PostgresVectorGenerationAuthority(db), raw)
    factory = PublishedRAGReadFactory(service, runtime, identity)
    document_id = str(uuid4())
    try:
        yield service, factory, db, raw, identity, runtime, users, document_id, {}
    finally:
        raw.client.delete_collection(identity.physical_collection)


def _point(runtime, document_id, marker, *, chunk_id="same-chunk-id",
           chunk_index=0, payload_id=None):
    text = f"{marker} shared search terms"
    payload = {
        "content": text,
        "document_id": document_id,
        "rag_namespace": "documents",
        "chunk_index": chunk_index,
        "metadata": {
            "embedding_fingerprint": runtime.profile.fingerprint,
            "file_name": "sample.txt",
            "page_number": 1,
            "id": "nested-user-metadata-id",
        },
    }
    if payload_id is not None:
        payload["id"] = payload_id
    return VectorPoint(chunk_id, runtime.embed_documents([text])[0], payload)


def _publish(service, db, identity, user, document_id, runtime, marker, leases,
             *, extra_marker=None, payload_id=None):
    scope = VectorScope(user, "rag", "documents", identity)
    lease = leases.get(user)
    if lease is None:
        lease = PostgresUserMutationCoordinator(db).acquire(user, "rag-test", lease_seconds=60)
        leases[user] = lease
    assert lease is not None
    points = [] if marker is None else [
        _point(runtime, document_id, marker, payload_id=payload_id),
    ]
    if extra_marker is not None:
        points.append(_point(runtime, document_id, extra_marker,
                             chunk_id="second-chunk-id", chunk_index=1))
    return service.publish_complete(scope, lease,
                                    service.authority.read_head(scope), points)


def test_each_rag_operation_keeps_one_head_across_subreads(rag_setup, monkeypatch):
    service, factory, db, _, identity, runtime, users, document_id, leases = rag_setup
    user = users[0]
    with pytest.raises(GenerationVectorStoreError, match="Missing or invalid"):
        factory.open_operation(user, "documents")
    a = _publish(service, db, identity, user, document_id, runtime, "alpha", leases)

    pinned_a = factory.open_operation(user, "documents")
    assert pinned_a.head == a.head
    original_scroll = GenerationVectorStore.scroll
    published_b = []

    def swap_after_scroll(view, *args, **kwargs):
        result = original_scroll(view, *args, **kwargs)
        if view is pinned_a.view and not published_b:
            published_b.append(_publish(service, db, identity, user, document_id,
                                        runtime, "bravo", leases,
                                        extra_marker="bravo second"))
        return result

    monkeypatch.setattr(GenerationVectorStore, "scroll", swap_after_scroll)
    assert pinned_a.stats()["chunk_count"] == 1
    assert pinned_a.stats()["document_count"] == 1
    assert published_b[0].head.revision == 2
    assert pinned_a.scroll_payloads()[0]["content"].startswith("alpha")
    monkeypatch.setattr(GenerationVectorStore, "scroll", original_scroll)

    pinned_b = factory.open_operation(user, "documents")
    assert pinned_b.stats()["chunk_count"] == 2
    original_search = GenerationVectorStore.search
    published_c = []

    def swap_after_search(view, *args, **kwargs):
        result = original_search(view, *args, **kwargs)
        if view is pinned_b.view and not published_c:
            published_c.append(_publish(service, db, identity, user, document_id,
                                        runtime, "charlie", leases))
        return result

    monkeypatch.setattr(GenerationVectorStore, "search", swap_after_search)
    hits = pinned_b.search("shared search terms", retrieval_mode="hybrid")
    assert hits and all(hit["content"].startswith("bravo") for hit in hits)
    assert {hit["id"] for hit in hits} == {"same-chunk-id", "second-chunk-id"}
    monkeypatch.setattr(GenerationVectorStore, "search", original_search)

    pinned_c = factory.open_operation(user, "documents")
    original_count = GenerationVectorStore.count
    published_d = []

    def swap_after_count(view, *args, **kwargs):
        result = original_count(view, *args, **kwargs)
        if view is pinned_c.view and not published_d:
            published_d.append(_publish(service, db, identity, user, document_id,
                                        runtime, "delta", leases))
        return result

    monkeypatch.setattr(GenerationVectorStore, "count", swap_after_count)
    context = pinned_c.get_document_summary_context(document_id)
    assert [item["content"] for item in context] == ["charlie shared search terms"]
    monkeypatch.setattr(GenerationVectorStore, "count", original_count)
    assert pinned_c.get_document_chunk(document_id, "same-chunk-id", 0)["content"].startswith("charlie")
    assert pinned_c.get_document_chunks(document_id)[0]["id"] == "same-chunk-id"
    assert pinned_c.max_chunk_index(document_id) == 0
    assert factory.open_operation(user, "documents").search("shared")[0]["content"].startswith("delta")


def test_empty_tenant_scope_and_logical_identity_fail_closed(rag_setup):
    service, factory, db, raw, identity, runtime, users, document_id, leases = rag_setup
    first, second = users
    _publish(service, db, identity, first, document_id, runtime, "first", leases,
             payload_id="conflicting-payload-id")
    _publish(service, db, identity, second, document_id, runtime, "second", leases)
    first_read = factory.open_operation(first, "documents")
    second_read = factory.open_operation(second, "documents")
    assert first_read.search("shared")[0]["content"].startswith("first")
    assert second_read.search("shared")[0]["content"].startswith("second")
    assert first_read.search("shared")[0]["id"] == "same-chunk-id"
    assert first_read.get_document_summary_context(document_id)[0]["id"] == "same-chunk-id"
    assert first_read.get_document_chunks(document_id)[0]["id"] == "same-chunk-id"
    assert first_read.scroll_payloads()[0]["id"] == "same-chunk-id"
    chunk = first_read.get_document_chunk(document_id, "same-chunk-id", 0)
    assert chunk["id"] == "same-chunk-id"
    assert chunk["metadata"]["id"] == "nested-user-metadata-id"
    assert first_read.list_document_ids() == [document_id]
    assert raw.count(identity.physical_collection) == 2
    assert all(not key.startswith("_gv_") for key in first_read.scroll_payloads()[0])
    assert all(point.id == "same-chunk-id" for point in first_read.view.scroll(identity.physical_collection))

    _publish(service, db, identity, first, document_id, runtime, None, leases)
    empty = factory.open_operation(first, "documents")
    assert empty.head.state == "empty"
    assert empty.search("shared", retrieval_mode="hybrid") == []
    assert empty.stats()["chunk_count"] == 0
    assert empty.get_document_summary_context(document_id) == []
    assert empty.get_document_chunk(document_id, "same-chunk-id", 0) is None
    assert empty.get_document_chunks(document_id) == []
    assert empty.list_document_ids() == []
    assert empty.count() == 0 and empty.scroll_payloads() == []
    assert second_read.search("shared")[0]["content"].startswith("second")
    assert raw.count(identity.physical_collection) == 2  # retired bytes stay hidden
    with pytest.raises(GenerationVectorStoreError):
        empty.view.delete_by_filter(identity.physical_collection, {"document_id": document_id})
    with pytest.raises(GenerationVectorStoreError):
        empty.view.count(identity.physical_collection, {"tenant_id": second})
    with pytest.raises(GenerationVectorStoreError, match="Missing or invalid"):
        factory.open_operation(first, "never-published")
