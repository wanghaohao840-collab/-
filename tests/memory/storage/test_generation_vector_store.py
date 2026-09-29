import os
from uuid import uuid4
from types import SimpleNamespace

import pytest

from app.postgres_vector_generations import VectorHead, VectorScope
from hello_agents.memory.rag.embedding_profile import EmbeddingProfile
from hello_agents.memory.rag.index_identity import IndexIdentity
from hello_agents.memory.storage.generation_vector_store import (
    CandidateAbortRequired,
    CandidateGenerationWriter,
    GenerationVectorStore,
    GenerationVectorStoreError,
    cleanup_abandoned_generation,
)
from hello_agents.memory.storage.vector_store import QdrantVectorStore, VectorPoint


@pytest.fixture
def corpus():
    url = os.environ.get("GENERATION_QDRANT_TEST_URL")
    if not url:
        pytest.skip("Set GENERATION_QDRANT_TEST_URL for disposable real-Qdrant tests")
    raw = QdrantVectorStore(url=url, retry_delays=())
    identity = IndexIdentity(
        "qdrant", f"gen_slice_{uuid4().hex}",
        EmbeddingProfile("simple", "", "deterministic", "v1", 4),
    )
    raw.ensure_collection(identity.physical_collection, 4)
    try:
        yield raw, identity
    finally:
        raw.client.delete_collection(identity.physical_collection)


def scope(identity, tenant="user-a"):
    return VectorScope(tenant, "rag", "documents", identity)


def point(logical_id, marker):
    return VectorPoint(logical_id, [1.0, 0.0, 0.0, 0.0], {"marker": marker})


def test_generation_isolates_tenants_and_pinned_heads(corpus):
    raw, identity = corpus
    a = scope(identity)
    b = scope(identity, "user-b")
    generation_a1, generation_a2, generation_b = uuid4(), uuid4(), uuid4()
    for current_scope, generation, marker in (
        (a, generation_a1, "old"),
        (a, generation_a2, "new"),
        (b, generation_b, "other"),
    ):
        writer = CandidateGenerationWriter(raw, current_scope, generation,
                                           lambda _s, _g: "staging")
        writer.upload([point("same-logical-id", marker)])
        assert writer.verify()[0] == 1

    old = GenerationVectorStore(raw, a, VectorHead("published", 1, generation_a1, 1, None))
    new = GenerationVectorStore(raw, a, VectorHead("published", 2, generation_a2, 1, None))
    other = GenerationVectorStore(raw, b, VectorHead("published", 1, generation_b, 1, None))
    collection = identity.physical_collection
    assert [hit.payload["marker"] for hit in old.search(collection, [1, 0, 0, 0])] == ["old"]
    assert [hit.payload["marker"] for hit in new.search(collection, [1, 0, 0, 0])] == ["new"]
    assert [hit.payload["marker"] for hit in other.search(collection, [1, 0, 0, 0])] == ["other"]
    assert new.count(collection, {"_id": "same-logical-id"}) == 1
    assert new.count(collection, {"_id": "missing"}) == 0
    assert new.scroll(collection, payload_fields=["marker"])[0] == VectorPoint(
        "same-logical-id", [], {"marker": "new"})
    page = new.scroll_page(collection, page_size=1)
    assert page.records[0].logical_id == "same-logical-id"
    assert page.records[0].storage_id != "same-logical-id"
    assert page.records[0].payload == {"marker": "new"}
    assert [record.logical_id for p in new.iter_scroll_pages(collection, page_size=1)
            for record in p.records] == ["same-logical-id"]


def test_pinned_view_rejects_rebinding_between_subreads(corpus):
    raw, identity = corpus
    current_scope = scope(identity)
    old_id, new_id = uuid4(), uuid4()
    for generation, marker in ((old_id, "old"), (new_id, "new")):
        CandidateGenerationWriter(raw, current_scope, generation,
                                  lambda _s, _g: "staging").upload([point("same", marker)])
    old = GenerationVectorStore(raw, current_scope,
                                VectorHead("published", 1, old_id, 1, None))
    new = GenerationVectorStore(raw, current_scope,
                                VectorHead("published", 2, new_id, 1, None))
    collection = identity.physical_collection
    assert old.count(collection) == 1
    for name, replacement in (
        ("head", new.head), ("scope", scope(identity, "user-b")),
        ("raw", QdrantVectorStore(url=os.environ["GENERATION_QDRANT_TEST_URL"])),
        ("collection_name", "other_collection"),
    ):
        with pytest.raises(AttributeError):
            setattr(old, name, replacement)
    assert old.count(collection) == 1
    assert [item.payload["marker"] for item in old.scroll(collection)] == ["old"]


def test_page_iterator_freezes_nested_document_and_id_filters(corpus):
    raw, identity = corpus
    current_scope = scope(identity)
    generation = uuid4()
    writer = CandidateGenerationWriter(raw, current_scope, generation,
                                       lambda _s, _g: "staging")
    writer.upload([
        VectorPoint("one", [1.0, 0.0, 0.0, 0.0], {"document_id": "doc-a"}),
        VectorPoint("two", [1.0, 0.0, 0.0, 0.0], {"document_id": "doc-a"}),
        VectorPoint("three", [1.0, 0.0, 0.0, 0.0], {"document_id": "doc-b"}),
    ])
    view = GenerationVectorStore(raw, current_scope,
                                 VectorHead("published", 1, generation, 1, None))
    collection = identity.physical_collection
    documents = ["doc-a"]
    ids = ["one", "two"]
    pages = view.iter_scroll_pages(collection,
                                   {"document_id": documents, "_id": ids}, page_size=1)
    first = next(pages)
    assert len(first.records) == 1
    documents[:] = ["doc-b"]
    ids[:] = ["three"]
    remaining = [record.logical_id for page in pages for record in page.records]
    assert {first.records[0].logical_id, *remaining} == {"one", "two"}


def test_bounded_pages_resume_at_native_cursor(corpus):
    raw, identity = corpus
    current_scope = scope(identity)
    generation = uuid4()
    writer = CandidateGenerationWriter(raw, current_scope, generation,
                                       lambda _s, _g: "staging")
    writer.upload([point("one", "first"), point("two", "second")])
    assert writer.verify()[0] == 2
    view = GenerationVectorStore(raw, current_scope,
                                 VectorHead("published", 1, generation, 1, None))
    collection = identity.physical_collection
    first = view.scroll_page(collection, page_size=1)
    assert len(first.records) == 1
    assert first.next_offset is not None
    second = view.scroll_page(collection, offset=first.next_offset, page_size=1)
    assert len(second.records) == 1
    assert second.next_offset is None
    assert {first.records[0].logical_id, second.records[0].logical_id} == {"one", "two"}
    assert {record.logical_id for page in view.iter_scroll_pages(collection, page_size=1)
            for record in page.records} == {"one", "two"}


def test_empty_head_hides_stale_points_and_reader_is_immutable(corpus):
    raw, identity = corpus
    current_scope = scope(identity)
    generation = uuid4()
    writer = CandidateGenerationWriter(raw, current_scope, generation,
                                       lambda _s, _g: "staging")
    writer.upload([point("stale", "stale")])
    collection = identity.physical_collection
    empty = GenerationVectorStore(raw, current_scope, VectorHead("empty", 2, None, 1, None))
    assert empty.search(collection, [1, 0, 0, 0]) == []
    assert empty.count(collection) == 0
    assert empty.scroll(collection) == []
    assert empty.scroll_page(collection).records == ()
    with pytest.raises(GenerationVectorStoreError):
        empty.upsert(collection, [point("new", "new")])
    with pytest.raises(GenerationVectorStoreError):
        GenerationVectorStore(raw, current_scope, VectorHead("missing", None, None, None, None))


def test_cleanup_requires_abandoned_and_exact_generation(corpus):
    raw, identity = corpus
    current_scope = scope(identity)
    old_id, live_id = uuid4(), uuid4()
    for generation in (old_id, live_id):
        CandidateGenerationWriter(raw, current_scope, generation,
                                  lambda _s, _g: "staging").upload([point("same", str(generation))])
    other_scope = scope(identity, "user-b")
    CandidateGenerationWriter(raw, other_scope, uuid4(),
                              lambda _s, _g: "staging").upload([point("same", "other")])
    collection = identity.physical_collection
    with pytest.raises(GenerationVectorStoreError):
        cleanup_abandoned_generation(raw, current_scope, old_id,
                                     lambda _s, _g: "published")
    assert cleanup_abandoned_generation(raw, current_scope, old_id,
                                        lambda _s, _g: "abandoned") == 1
    live = GenerationVectorStore(raw, current_scope, VectorHead("published", 2, live_id, 1, None))
    assert live.count(collection) == 1
    assert raw.count(collection) == 2


def test_candidate_poisoned_after_unknown_write(corpus, monkeypatch):
    raw, identity = corpus
    writer = CandidateGenerationWriter(raw, scope(identity), uuid4(),
                                       lambda _s, _g: "staging")
    def timeout(*args, **kwargs):
        raise TimeoutError("unknown outcome")
    monkeypatch.setattr(raw.client, "upsert", timeout)
    with pytest.raises(CandidateAbortRequired):
        writer.upload([point("a", "a")])
    with pytest.raises(GenerationVectorStoreError):
        writer.upload([point("b", "b")])
    with pytest.raises(GenerationVectorStoreError):
        writer.verify()


def test_scope_conflicts_duplicate_ids_and_bad_vectors_preflight(corpus):
    raw, identity = corpus
    current_scope = scope(identity)
    generation = uuid4()
    writer = CandidateGenerationWriter(raw, current_scope, generation,
                                       lambda _s, _g: "staging")
    collection = identity.physical_collection
    with pytest.raises(GenerationVectorStoreError):
        writer.upload([point("dup", "a"), point("dup", "b")])
    with pytest.raises(GenerationVectorStoreError):
        writer.upload([VectorPoint("bad", [float("nan"), 0, 0, 0], {})])
    with pytest.raises(GenerationVectorStoreError):
        writer.upload([VectorPoint("bad", [float("inf"), 0, 0, 0], {})])
    with pytest.raises(GenerationVectorStoreError):
        writer.upload([VectorPoint("bad", [1, 0, 0, 0], {"user_id": "user-b"})])
    assert raw.count(collection) == 0
    writer.upload([point("valid", "valid")])
    view = GenerationVectorStore(raw, current_scope,
                                 VectorHead("published", 1, generation, 1, None))
    with pytest.raises(GenerationVectorStoreError):
        view.search(collection, [1, 0, 0, 0], {"user_id": "user-b"})
    with pytest.raises(GenerationVectorStoreError):
        view.count(collection, {"_gv_generation": str(generation)})
    assert view.count(collection, {"_id": ["missing", "valid"]}) == 1


def test_candidate_rejects_sealed_state_before_upload_or_verify(corpus):
    raw, identity = corpus
    state = {"value": "staging"}
    writer = CandidateGenerationWriter(raw, scope(identity), uuid4(),
                                       lambda _s, _g: state["value"])
    state["value"] = "sealed"
    with pytest.raises(GenerationVectorStoreError):
        writer.upload([point("a", "a")])
    state["value"] = "staging"
    writer.upload([point("a", "a")])
    state["value"] = "sealed"
    with pytest.raises(GenerationVectorStoreError):
        writer.verify()


def test_zero_point_candidate_has_explicit_empty_manifest(corpus):
    raw, identity = corpus
    writer = CandidateGenerationWriter(raw, scope(identity), uuid4(),
                                       lambda _s, _g: "staging")
    writer.upload([])
    count, digest = writer.verify()
    assert count == 0
    assert len(digest) == 64
    assert raw.count(identity.physical_collection) == 0


def test_corrupt_native_point_or_payload_fails_closed(corpus, monkeypatch):
    raw, identity = corpus
    current_scope = scope(identity)
    generation = uuid4()
    CandidateGenerationWriter(raw, current_scope, generation,
                              lambda _s, _g: "staging").upload([point("ok", "ok")])
    view = GenerationVectorStore(raw, current_scope,
                                 VectorHead("published", 1, generation, 1, None))
    collection = identity.physical_collection
    real_query = raw.client.query_points
    response = real_query(collection_name=collection, query=[1, 0, 0, 0],
                          query_filter=raw._filter(view._filters(None)),
                          limit=5, with_payload=True)
    payload = dict(response.points[0].payload)
    monkeypatch.setattr(raw.client, "query_points", lambda **_: SimpleNamespace(
        points=[SimpleNamespace(id=str(uuid4()), payload=payload, score=1.0)]))
    with pytest.raises(GenerationVectorStoreError):
        view.search(collection, [1, 0, 0, 0])
    monkeypatch.setattr(raw.client, "query_points", real_query)
    payload["_gv_tenant"] = "user-b"
    native = response.points[0].id
    monkeypatch.setattr(raw.client, "scroll", lambda **_: (
        [SimpleNamespace(id=native, payload=payload, vector=None)], None))
    with pytest.raises(GenerationVectorStoreError):
        view.scroll(collection, payload_fields=["marker"])
