"""Real disposable PostgreSQL/Qdrant acceptance for attested episode reads."""
from __future__ import annotations

import json
import os
import threading
import hashlib
from copy import deepcopy
from contextlib import contextmanager
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from psycopg import sql

from app.postgres_coordination import PostgresUserMutationCoordinator
from app.postgres_snapshots import PostgresSnapshotRepository
from app.postgres_vector_generations import PostgresVectorGenerationAuthority, VectorScope
from app.vector_generation_service import VectorGenerationService
from app.published_episode_reads import PublishedEpisodeReadFactory, EpisodeReadError
from hello_agents.memory.rag.embedding_profile import EmbeddingProfile
from hello_agents.memory.rag.index_identity import IndexIdentity
from hello_agents.memory.storage.vector_store import QdrantVectorStore, VectorHit, VectorPoint
from hello_agents.memory.storage.generation_vector_store import (
    GenerationVectorStore, _canonical, _physical_id, _scope_fields,
)
from tests.integration.test_postgres_auth_sessions import shared_database


@pytest.fixture
def episode_setup(shared_database):
    url = os.environ.get("GENERATION_QDRANT_TEST_URL")
    if not url:
        pytest.skip("GENERATION_QDRANT_TEST_URL is required")
    open_pool, _ = shared_database
    db = open_pool()
    users = (str(uuid4()), str(uuid4()))
    with db.transaction() as cur:
        for user in users:
            cur.execute("insert into users values (%s,%s,%s,%s,%s,%s,%s)",
                        (user, user, user, "hash", "active", "now", "now"))
    profile = EmbeddingProfile("test-explicit", "", "episode-test", "v1", 4)
    identity = IndexIdentity("qdrant", "episode_test_" + uuid4().hex, profile)
    raw = QdrantVectorStore(url=url, retry_delays=())
    raw.ensure_collection(identity.physical_collection, profile.dimension)
    service = VectorGenerationService(PostgresVectorGenerationAuthority(db), raw)
    scopes = [VectorScope(user, "episode", "episodes", identity) for user in users]
    try:
        yield db, raw, service, scopes, profile
    finally:
        raw.client.delete_collection(identity.physical_collection)


def _item(user, label, *, logical_id="shared-episode", timestamp="2026-10-01T08:30:00+08:00"):
    return {"id": logical_id, "content": label, "memory_type": "episodic",
            "importance": 0.75, "timestamp": timestamp,
            "metadata": {"user_id": user, "session_id": "session-a",
                         "task_id": "task-a", "nested": {"flag": True}}}


def _payload(item):
    return {**item["metadata"], "memory_id": item["id"], "episode_id": item["id"],
            "session_id": item["metadata"]["session_id"], "timestamp": item["timestamp"],
            "memory_type": "episodic", "importance": item["importance"],
            "content": item["content"]}


def _publish(db, service, scope, items, *, version=None):
    lease = PostgresUserMutationCoordinator(db).acquire(scope.tenant_id, "episode-test", lease_seconds=90)
    assert lease is not None
    old = PostgresSnapshotRepository(db).read(scope.tenant_id, "memory")
    next_version = (old.version if old else 0) + 1
    assert version is None or version == next_version
    points = [VectorPoint(item["id"], [1.0, 0.0, 0.0, 0.0], _payload(item)) for item in items]

    def domain(cur):
        current = PostgresSnapshotRepository._read(cur, scope.tenant_id, "memory")
        assert (current.version if current else 0) == next_version - 1
        cur.execute("""insert into user_snapshots(user_id,kind,version,payload,updated_at)
            values (%s,'memory',%s,%s::jsonb,clock_timestamp())
            on conflict(user_id,kind) do update set version=excluded.version,
            payload=excluded.payload,updated_at=excluded.updated_at""",
            (scope.tenant_id, next_version, json.dumps({"user_id": scope.tenant_id, "memories": items})))
        cur.execute("delete from memory_documents where user_id=%s", (scope.tenant_id,))
        for item in items:
            cur.execute("""insert into memory_documents(user_id,document_id,content,metadata)
                values (%s,%s,%s,%s)""",
                (scope.tenant_id, item["id"], item["content"], json.dumps(_payload(item))))

    try:
        return service.publish_complete(scope, lease, service.authority.read_head(scope), points,
                                        domain_publish=domain, snapshot_version=next_version)
    finally:
        PostgresUserMutationCoordinator(db).release(lease)


def test_missing_head_and_attested_nonempty_bundle(episode_setup):
    db, raw, service, scopes, profile = episode_setup
    scope = scopes[0]
    factory = PublishedEpisodeReadFactory(service, scope)
    with pytest.raises(EpisodeReadError):
        factory.open_operation(scope.tenant_id)
    with pytest.raises(EpisodeReadError):
        factory.open_operation(scopes[1].tenant_id)
    published = _publish(db, service, scope, [_item(scope.tenant_id, "alpha")])
    operation = factory.open_operation(scope.tenant_id)
    assert operation.head == published.head
    assert operation.receipt["expected_count"] == 1
    assert operation.count() == 1
    assert [p.id for p in operation.scroll()] == ["shared-episode"]
    hits = operation.search_vector([1, 0, 0, 0], query_profile=profile)
    assert [hit.id for hit in hits] == ["shared-episode"]
    assert operation.count(start_time="2026-10-01T08:30:00+08:00",
                           end_time="2026-10-01T00:30:00Z") == 1
    assert operation.count(end_time="2026-10-01T00:29:59Z") == 0
    with pytest.raises(EpisodeReadError):
        operation.search_vector([1, 0, 0, 0], query_profile=EmbeddingProfile(
            "test-explicit", "", "other-model", "v1", 4))


def test_two_users_and_head_swap_keep_frozen_membership(episode_setup):
    db, raw, service, scopes, profile = episode_setup
    first, second = scopes
    _publish(db, service, first, [_item(first.tenant_id, "first")])
    _publish(db, service, second, [_item(second.tenant_id, "second")])
    a = PublishedEpisodeReadFactory(service, first).open_operation(first.tenant_id)
    other = PublishedEpisodeReadFactory(service, second).open_operation(second.tenant_id)
    assert a.scroll()[0].payload["content"] == "first"
    assert other.scroll()[0].payload["content"] == "second"
    _publish(db, service, first, [_item(first.tenant_id, "replacement")])
    b = PublishedEpisodeReadFactory(service, first).open_operation(first.tenant_id)
    assert a.search_vector([1, 0, 0, 0], query_profile=profile)[0].payload["content"] == "first"
    assert b.search_vector([1, 0, 0, 0], query_profile=profile)[0].payload["content"] == "replacement"
    assert a.count() == b.count() == other.count() == 1
    assert raw.count(first.identity.physical_collection) == 3


def test_empty_and_snapshot_drift_refuse_or_hide(episode_setup):
    db, raw, service, scopes, profile = episode_setup
    scope = scopes[0]
    _publish(db, service, scope, [_item(scope.tenant_id, "old")])
    empty_pub = _publish(db, service, scope, [])
    empty = PublishedEpisodeReadFactory(service, scope).open_operation(scope.tenant_id)
    assert empty.head == empty_pub.head
    assert empty.count() == 0 and empty.scroll() == []
    assert empty.search_vector([1, 0, 0, 0], query_profile=profile) == []
    assert raw.count(scope.identity.physical_collection) == 1
    PostgresSnapshotRepository(db).compare_and_swap(
        scope.tenant_id, "memory", {"user_id": scope.tenant_id,
                                    "memories": [{"memory_type": "semantic",
                                                  "metadata": {"user_id": scope.tenant_id}}]},
        expected_version=2)
    with pytest.raises(EpisodeReadError):
        PublishedEpisodeReadFactory(service, scope).open_operation(scope.tenant_id)


def test_publication_between_pg_capture_and_manifest_keeps_captured_bundle(
        episode_setup, monkeypatch):
    db, raw, service, scopes, profile = episode_setup
    scope = scopes[0]
    _publish(db, service, scope, [_item(scope.tenant_id, "before")])
    captured = threading.Event()
    released = threading.Event()
    original = GenerationVectorStore.scroll

    def gated(view, *args, **kwargs):
        if kwargs.get("expected_manifest") is not None and not captured.is_set():
            captured.set()
            assert released.wait(15)
        return original(view, *args, **kwargs)

    monkeypatch.setattr(GenerationVectorStore, "scroll", gated)
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(PublishedEpisodeReadFactory(service, scope).open_operation,
                             scope.tenant_id)
        assert captured.wait(15)
        replacement = _publish(db, service, scope, [_item(scope.tenant_id, "after")])
        released.set()
        old = future.result(timeout=15)
    monkeypatch.setattr(GenerationVectorStore, "scroll", original)
    new = PublishedEpisodeReadFactory(service, scope).open_operation(scope.tenant_id)
    assert (old.head.revision, new.head.revision) == (1, 2)
    assert new.head == replacement.head
    assert old.scroll()[0].payload["content"] == "before"
    assert new.scroll()[0].payload["content"] == "after"


def test_wrong_metadata_and_mutation_of_outputs_fail_closed(episode_setup):
    db, raw, service, scopes, profile = episode_setup
    scope = scopes[0]
    item = _item(scope.tenant_id, "unmodified")
    _publish(db, service, scope, [item])
    factory = PublishedEpisodeReadFactory(service, scope)
    operation = factory.open_operation(scope.tenant_id)
    output = operation.scroll()[0]
    output.payload["nested"]["flag"] = False
    assert operation.get_item(item["id"]).payload["nested"]["flag"] is True
    query = [1, 0, 0, 0]
    operation.search_vector(query, query_profile=profile)[0].payload["nested"]["flag"] = False
    query[0] = 0
    assert operation.scroll()[0].payload["nested"]["flag"] is True

    original = json.dumps(_payload(item))
    for altered in (
            original.replace('"flag": true', '"flag": 1'),
            original.replace('"task_id": "task-a"', '"task_id": "different"'),
            original.replace('"session_id": "session-a"', '"session_id": "different"'),
            original.replace('"importance": 0.75', '"importance": 0.5'),
            original[:-1] + ', "content": "duplicate"}',
            original.replace('"flag": true', '"flag": NaN'),
    ):
        with db.transaction() as cur:
            cur.execute("update memory_documents set metadata=%s where user_id=%s",
                        (altered, scope.tenant_id))
        with pytest.raises(EpisodeReadError):
            factory.open_operation(scope.tenant_id)
        with pytest.raises(EpisodeReadError):
            factory._capture_bundle(scope.tenant_id)
    with db.transaction() as cur:
        cur.execute("update memory_documents set metadata=%s where user_id=%s",
                    (original, scope.tenant_id))
    assert factory.open_operation(scope.tenant_id).count() == 1
    for invalid in ([True, 0, 0, 0], [float("nan"), 0, 0, 0], [1, 0], [1e308] * 4):
        with pytest.raises(EpisodeReadError):
            operation.search_vector(invalid, query_profile=profile)
    with pytest.raises(EpisodeReadError):
        operation.count(start_time="2026-10-01T08:30:00", end_time="2026-10-01T08:30:00Z")
    with pytest.raises(EpisodeReadError):
        operation.count(start_time="2026-10-02T00:00:00Z", end_time="2026-10-01T00:00:00Z")
    for invalid_time in ("2026-10-01T08:30:00+01:99",
                         "2026-10-01T08:30:00.1234567+08:00"):
        with pytest.raises(EpisodeReadError):
            operation.count(start_time=invalid_time)
    for invalid_limit in (None, True, 0, 1001):
        with pytest.raises(EpisodeReadError):
            operation.search_vector([1, 0, 0, 0], query_profile=profile,
                                    limit=invalid_limit)
    for bad_dimension in (4.0, True):
        with pytest.raises(EpisodeReadError):
            operation.search_vector([1, 0, 0, 0],
                                    query_profile=replace(profile, dimension=bad_dimension))


def test_forged_registry_identity_and_receipt_are_refused(episode_setup):
    db, raw, service, scopes, profile = episode_setup
    scope = scopes[0]
    publication = _publish(db, service, scope, [_item(scope.tenant_id, "trusted")])
    factory = PublishedEpisodeReadFactory(service, scope)
    original = scope.identity.to_dict()
    for forged in (dict(original, schema_version=True),
                   dict(original, profile=dict(original["profile"], dimension=4.0))):
        with db.transaction() as cur:
            cur.execute("alter table vector_indexes disable trigger vector_index_guard")
            cur.execute("update vector_indexes set identity=%s::jsonb where tenant_id=%s",
                        (json.dumps(forged), scope.tenant_id))
            cur.execute("alter table vector_indexes enable trigger vector_index_guard")
        with pytest.raises(EpisodeReadError):
            factory.open_operation(scope.tenant_id)
    with db.transaction() as cur:
        cur.execute("alter table vector_indexes disable trigger vector_index_guard")
        cur.execute("update vector_indexes set identity=%s::jsonb where tenant_id=%s",
                    (json.dumps(original), scope.tenant_id))
        cur.execute("alter table vector_indexes enable trigger vector_index_guard")
    assert factory.open_operation(scope.tenant_id).count() == 1
    with db.transaction() as cur:
        cur.execute("alter table vector_generations disable trigger vector_generation_guard")
    with db.transaction() as cur:
        cur.execute("update vector_generations set publication_snapshot_version=999 where generation_id=%s",
                    (publication.generation_id,))
    with db.transaction() as cur:
        cur.execute("alter table vector_generations enable trigger vector_generation_guard")
    with pytest.raises(EpisodeReadError):
        factory.open_operation(scope.tenant_id)


def test_snapshot_corruption_and_duplicate_ids_fail_closed(episode_setup):
    db, raw, service, scopes, profile = episode_setup
    scope = scopes[0]
    item = _item(scope.tenant_id, "trusted")
    _publish(db, service, scope, [item])
    factory = PublishedEpisodeReadFactory(service, scope)
    baseline = {"user_id": scope.tenant_id, "memories": [item]}
    variants = []
    duplicate = deepcopy(baseline)
    duplicate["memories"].append(deepcopy(item))
    variants.append(duplicate)
    for field, value in (("content", "wrong"), ("timestamp", "2026-10-01T08:30:00"),
                         ("memory_type", "unknown"), ("id", "wrong-id")):
        changed = deepcopy(baseline)
        changed["memories"][0][field] = value
        variants.append(changed)
    for key, value in (("user_id", scopes[1].tenant_id), ("session_id", "wrong"),
                       ("memory_id", "wrong-alias"), ("episode_id", "wrong-alias")):
        changed = deepcopy(baseline)
        changed["memories"][0]["metadata"][key] = value
        variants.append(changed)
    for changed in variants:
        with db.transaction() as cur:
            cur.execute("update user_snapshots set payload=%s::jsonb where user_id=%s and kind='memory'",
                        (json.dumps(changed), scope.tenant_id))
        with pytest.raises(EpisodeReadError):
            factory.open_operation(scope.tenant_id)
    with db.transaction() as cur:
        cur.execute("update user_snapshots set payload=%s::jsonb where user_id=%s and kind='memory'",
                    (json.dumps(baseline), scope.tenant_id))
    assert factory.open_operation(scope.tenant_id).count() == 1


def test_negative_importance_item_lookup_still_reads_pinned_vector(episode_setup):
    db, raw, service, scopes, profile = episode_setup
    scope = scopes[0]
    item = _item(scope.tenant_id, "below default threshold")
    item["importance"] = -0.25
    _publish(db, service, scope, [item])
    factory = PublishedEpisodeReadFactory(service, scope)
    bundle = factory._capture_bundle(scope.tenant_id)
    assert bundle.points[0].id == "shared-episode"
    operation = factory.open_operation(scope.tenant_id)
    assert operation.count() == 0
    assert operation.get_item(item["id"]).payload["content"] == "below default threshold"


def test_private_bundle_preserves_whole_memory_rows_and_negative_vector(episode_setup):
    db, raw, service, scopes, profile = episode_setup
    scope = scopes[0]
    item = _item(scope.tenant_id, "negative", logical_id="negative-episode")
    item["importance"] = -0.25
    item["extra"] = {"keep": [True, {"number": 2}]}
    published = _publish(db, service, scope, [item])
    semantic = {"id": "semantic-1", "content": "fact", "memory_type": "semantic",
                "metadata": {"user_id": scope.tenant_id, "source": {"nested": [1, False]}}}
    snapshot = {"user_id": scope.tenant_id, "memories": [item, semantic],
                "unrelated": {"retain": ["as", "JSON"]}}
    raw_metadata = ' { "user_id": "' + scope.tenant_id + '", "memory_type": "semantic", "note": [true, 3] } '
    with db.transaction() as cur:
        cur.execute("update user_snapshots set payload=%s::jsonb where user_id=%s and kind='memory'",
                    (json.dumps(snapshot), scope.tenant_id))
        cur.execute("""insert into memory_documents(user_id,document_id,content,metadata)
            values (%s,%s,%s,%s)""", (scope.tenant_id, "semantic-1", "fact", raw_metadata))
    factory = PublishedEpisodeReadFactory(service, scope)
    bundle = factory._capture_bundle(scope.tenant_id)
    assert bundle.snapshot == snapshot
    assert bundle.head == published.head
    assert bundle.scope == scope
    assert bundle.publication_receipt["generation_id"] == str(published.generation_id)
    assert bundle.publication_receipt["index_revision"] == published.head.index_revision
    assert len(bundle.documents) == 2
    assert next(row for row in bundle.documents if row["document_id"] == "semantic-1")["metadata"] == raw_metadata
    assert [(point.id, point.vector, point.payload["importance"])
            for point in bundle.points] == [("negative-episode", [1.0, 0.0, 0.0, 0.0], -0.25)]
    bundle.snapshot["memories"][0]["extra"]["keep"].clear()
    bundle.documents[0]["metadata"] = "changed"
    bundle.points[0].vector[0] = 7
    bundle.points[0].payload["content"] = "changed"
    bundle.publication_receipt["expected_count"] = 999
    assert bundle.snapshot == snapshot
    assert {row["document_id"] for row in bundle.documents} == {"negative-episode", "semantic-1"}
    assert bundle.points[0].vector == [1.0, 0.0, 0.0, 0.0]
    assert bundle.points[0].payload["content"] == "negative"
    assert bundle.publication_receipt["expected_count"] == 1
    assert factory.open_operation(scope.tenant_id).count() == 0


def test_private_bundle_keeps_pg_capture_across_publication(episode_setup, monkeypatch):
    db, raw, service, scopes, profile = episode_setup
    scope = scopes[0]
    first = _item(scope.tenant_id, "before")
    _publish(db, service, scope, [first])
    captured = threading.Event()
    released = threading.Event()
    original = GenerationVectorStore.scroll

    def gated(view, *args, **kwargs):
        if kwargs.get("expected_manifest") is not None and not captured.is_set():
            captured.set()
            assert released.wait(15)
        return original(view, *args, **kwargs)

    monkeypatch.setattr(GenerationVectorStore, "scroll", gated)
    factory = PublishedEpisodeReadFactory(service, scope)
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(factory._capture_bundle, scope.tenant_id)
        assert captured.wait(15)
        _publish(db, service, scope, [_item(scope.tenant_id, "after")])
        released.set()
        old = future.result(timeout=15)
    assert old.head.revision == 1
    assert old.snapshot["memories"] == [first]
    assert old.documents[0]["content"] == "before"
    assert old.points[0].payload["content"] == "before"
    assert factory._capture_bundle(scope.tenant_id).head.revision == 2


def test_manifest_and_returned_material_corruption_fail_closed(episode_setup, monkeypatch):
    db, raw, service, scopes, profile = episode_setup
    scope = scopes[0]
    item = _item(scope.tenant_id, "trusted")
    published = _publish(db, service, scope, [item])
    factory = PublishedEpisodeReadFactory(service, scope)
    operation = factory.open_operation(scope.tenant_id)
    collection = scope.identity.physical_collection
    point_id = _physical_id(scope, published.generation_id, item["id"])
    raw.client.set_payload(collection_name=collection, points=[point_id],
                           payload={"content": "corrupt"}, wait=True)
    with pytest.raises(Exception, match="manifest|payload|differs"):
        factory.open_operation(scope.tenant_id)
    raw.client.set_payload(collection_name=collection, points=[point_id],
                           payload={"content": "trusted"}, wait=True)
    assert factory.open_operation(scope.tenant_id).count() == 1
    raw.client.delete(collection_name=collection,
                      points_selector=[point_id], wait=True)
    with pytest.raises(Exception, match="manifest|membership|differs"):
        factory.open_operation(scope.tenant_id)

    original_search = GenerationVectorStore.search
    original_scroll = GenerationVectorStore.scroll
    def corrupted_search(view, *args, **kwargs):
        result = original_search(view, *args, **kwargs)
        return [VectorHit(result[0].id, result[0].score,
                          dict(result[0].payload, content="corrupt"))]
    def corrupted_scroll(view, *args, **kwargs):
        result = original_scroll(view, *args, **kwargs)
        return [VectorPoint(result[0].id, result[0].vector,
                            dict(result[0].payload, content="corrupt"))]
    # Restore only this disposable point to exercise validation of returned material.
    payload = _payload(item) | _scope_fields(scope, published.generation_id) | {
        "_gv_logical_id": item["id"],
        "_gv_vector_digest": hashlib.sha256(_canonical([1.0, 0.0, 0.0, 0.0]).encode()).hexdigest()}
    raw.client.upsert(collection_name=collection,
                      points=[raw._point_struct(point_id, [1.0, 0.0, 0.0, 0.0], payload)], wait=True)
    monkeypatch.setattr(GenerationVectorStore, "search", corrupted_search)
    with pytest.raises(EpisodeReadError, match="payload differs"):
        operation.search_vector([1, 0, 0, 0], query_profile=profile)
    monkeypatch.setattr(GenerationVectorStore, "scroll", corrupted_scroll)
    with pytest.raises(EpisodeReadError, match="payload differs"):
        operation.scroll()
    with pytest.raises(EpisodeReadError, match="payload differs"):
        operation.count()


def test_missing_receipt_row_refused_in_disposable_schema(episode_setup):
    db, raw, service, scopes, profile = episode_setup
    scope = scopes[0]
    _publish(db, service, scope, [_item(scope.tenant_id, "trusted")])
    factory = PublishedEpisodeReadFactory(service, scope)
    assert factory.open_operation(scope.tenant_id).count() == 1
    # The production FK prevents this state. Remove only that FK in this disposable
    # schema to prove that the reader also refuses a broken joined receipt.
    with db.transaction() as cur:
        constraint = cur.execute("""select conname from pg_constraint
            where conrelid='vector_heads'::regclass and contype='f'
              and pg_get_constraintdef(oid) like 'FOREIGN KEY (last_generation_id,%'""").fetchone()
        assert constraint is not None
        cur.execute(sql.SQL("alter table vector_heads drop constraint {}").format(
            sql.Identifier(constraint["conname"])))
        cur.execute("alter table vector_heads disable trigger user")
        cur.execute("update vector_heads set last_generation_id=%s where tenant_id=%s",
                    (uuid4(), scope.tenant_id))
        cur.execute("alter table vector_heads enable trigger user")
    with pytest.raises(EpisodeReadError):
        factory.open_operation(scope.tenant_id)


def test_read_paths_do_not_write_or_fallback(episode_setup, monkeypatch):
    db, raw, service, scopes, profile = episode_setup
    scope = scopes[0]
    _publish(db, service, scope, [_item(scope.tenant_id, "stable")])
    def prohibited(*args, **kwargs):
        raise AssertionError("read attempted a write")
    original_transaction = db.transaction

    @contextmanager
    def select_only():
        with original_transaction() as cursor:
            class ReadCursor:
                def execute(self, statement, parameters=None):
                    assert statement.lstrip().lower().startswith("select"), statement
                    return cursor.execute(statement, parameters)
            yield ReadCursor()

    monkeypatch.setattr(db, "transaction", select_only)
    monkeypatch.setattr(raw, "ensure_collection", prohibited)
    monkeypatch.setattr(raw, "ensure_payload_indexes", prohibited)
    monkeypatch.setattr(raw, "upsert", prohibited)
    monkeypatch.setattr(raw, "delete_by_filter", prohibited)
    monkeypatch.setattr(raw, "clear", prohibited)
    original_client_methods = {}
    for method in ("upsert", "delete", "create_collection", "delete_collection",
                   "create_payload_index", "set_payload", "overwrite_payload",
                   "delete_payload", "update_vectors", "delete_vectors",
                   "update_collection", "delete_payload_index"):
        if hasattr(raw.client, method):
            original_client_methods[method] = getattr(raw.client, method)
            monkeypatch.setattr(raw.client, method, prohibited)
    monkeypatch.setattr(PostgresUserMutationCoordinator, "acquire", prohibited)
    factory = PublishedEpisodeReadFactory(service, scope)
    assert factory._capture_bundle(scope.tenant_id).points[0].payload["content"] == "stable"
    operation = factory.open_operation(scope.tenant_id)
    assert operation.count(start_time="2026-10-01T00:30:00Z") == 1
    assert operation.search_vector([1, 0, 0, 0], query_profile=profile,
                                   start_time="2026-10-01T00:30:00Z")
    assert operation.get_item("shared-episode") is not None
    for method, args in (("ensure_collection", (scope.identity.physical_collection, 4)),
                         ("ensure_payload_indexes", (scope.identity.physical_collection, {})),
                         ("upsert", (scope.identity.physical_collection, [])),
                         ("delete_by_filter", (scope.identity.physical_collection,)),
                         ("clear", ())):
        with pytest.raises(Exception, match="read only"):
            getattr(operation._view, method)(*args)
    def failed(*args, **kwargs):
        raise RuntimeError("disposable Qdrant failure")
    monkeypatch.setattr(GenerationVectorStore, "search", failed)
    with pytest.raises(RuntimeError, match="disposable Qdrant failure"):
        operation.search_vector([1, 0, 0, 0], query_profile=profile)
    monkeypatch.setattr(GenerationVectorStore, "scroll", failed)
    with pytest.raises(RuntimeError, match="disposable Qdrant failure"):
        operation.count()
    for method, original_method in original_client_methods.items():
        monkeypatch.setattr(raw.client, method, original_method)


def test_late_untagged_and_prior_generation_writes_do_not_enter_new_reads(episode_setup):
    db, raw, service, scopes, profile = episode_setup
    scope = scopes[0]
    first = _publish(db, service, scope, [_item(scope.tenant_id, "old")])
    old = PublishedEpisodeReadFactory(service, scope).open_operation(scope.tenant_id)
    release = threading.Event()
    written = threading.Event()
    vector = [1.0, 0.0, 0.0, 0.0]

    def late_writer():
        assert release.wait(15)
        raw.upsert(scope.identity.physical_collection,
                   [VectorPoint("legacy-untagged", vector,
                                _payload(_item(scope.tenant_id, "legacy",
                                               logical_id="legacy-untagged")))])
        logical_id = "late-old-generation"
        old_id = _physical_id(scope, first.generation_id, logical_id)
        payload = (_payload(_item(scope.tenant_id, "late old", logical_id=logical_id))
                   | _scope_fields(scope, first.generation_id)
                   | {"_gv_logical_id": logical_id,
                      "_gv_vector_digest": hashlib.sha256(_canonical(vector).encode()).hexdigest()})
        raw.client.upsert(collection_name=scope.identity.physical_collection,
                          points=[raw._point_struct(old_id, vector, payload)], wait=True)
        written.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(late_writer)
        second = _publish(db, service, scope, [_item(scope.tenant_id, "new")])
        current = PublishedEpisodeReadFactory(service, scope).open_operation(scope.tenant_id)
        assert old.scroll()[0].payload["content"] == "old"
        assert current.head == second.head
        before = raw.count(scope.identity.physical_collection)
        release.set()
        pending.result(timeout=15)
    assert written.is_set()
    assert raw.count(scope.identity.physical_collection) == before + 2
    assert current.count() == 1
    assert [point.payload["content"] for point in current.scroll()] == ["new"]
    assert [hit.payload["content"] for hit in current.search_vector(
        vector, query_profile=profile)] == ["new"]
    reopened = PublishedEpisodeReadFactory(service, scope).open_operation(scope.tenant_id)
    assert reopened.count() == 1
