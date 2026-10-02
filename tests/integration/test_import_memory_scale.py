"""Opt-in physical 100000+1 episode preflight against disposable services."""
from __future__ import annotations

import gc
import hashlib
import json
import os
import sys
import time
from math import ceil
from uuid import uuid5

import pytest

from app.import_memory_publication import ImportMemoryPublicationError
from app.postgres_coordination import PostgresUserMutationCoordinator
from app.postgres_import_leases import PostgresImportLeaseRepository
from app.published_episode_reads import PublishedEpisodeReadFactory
from hello_agents.memory.rag.prepare import PROJECT_POINT_NAMESPACE_UUID
from hello_agents.memory.storage.generation_vector_store import _scope_fields
from hello_agents.memory.storage.vector_store import VectorPoint
from tests.integration.test_import_document_publication import publication, _point, _task
from tests.integration.test_import_memory_publication import memory_publication
from tests.integration.test_postgres_auth_sessions import shared_database
from tests.integration.test_s3_object_store import store


POINT_COUNT = 100_000
LEASE_SECONDS = 86_400
TIMESTAMP = "2026-10-02T00:00:00+00:00"
TABLES = (
    "users", "user_mutation_leases", "user_snapshots", "memory_documents",
    "vector_indexes", "vector_generations", "vector_heads",
    "history_document_witnesses", "document_objects", "import_batches",
    "import_tasks", "import_objects", "import_task_attempts",
    "import_user_schedule",
)

pytestmark = pytest.mark.skipif(
    os.environ.get("IMPORT_MEMORY_SCALE_TEST") != "1",
    reason="physical 100000+1 gate unverified; opt in with IMPORT_MEMORY_SCALE_TEST=1",
)


@pytest.fixture(scope="module", autouse=True)
def _configured_disposable_endpoints():
    for name in ("POSTGRES_TEST_URL", "S3_TEST_ENDPOINT", "S3_TEST_ACCESS_KEY",
                 "S3_TEST_SECRET_KEY", "GENERATION_QDRANT_TEST_URL"):
        assert os.environ.get(name), f"{name} is required for the physical scale gate"


def _item(user: str, index: int) -> dict:
    return {
        "id": f"scale-{index:06d}", "content": "e", "memory_type": "episodic",
        "importance": 0.0, "timestamp": TIMESTAMP,
        "metadata": {"user_id": user, "session_id": "scale"},
    }


def _payload(item: dict) -> dict:
    return dict(item["metadata"]) | {
        "memory_id": item["id"], "episode_id": item["id"],
        "timestamp": item["timestamp"], "memory_type": "episodic",
        "importance": item["importance"], "content": item["content"],
    }


def _publish_physical_baseline(service, db, scope, user):
    items = []
    points = []
    for index in range(POINT_COUNT):
        item = _item(user, index)
        items.append(item)
        points.append(VectorPoint(item["id"], [1.0, 0.0, 0.0, 0.0], _payload(item)))
    coordinator = PostgresUserMutationCoordinator(db)
    lease = coordinator.acquire(user, "physical-scale-bootstrap", lease_seconds=LEASE_SECONDS)
    assert lease is not None
    old = service.pair.episode.authority.read_head(scope)
    assert old.snapshot_version == 1

    def domain(cursor):
        snapshot = json.dumps({"user_id": user, "memories": items},
                              separators=(",", ":"), ensure_ascii=False)
        changed = cursor.execute("""update user_snapshots set version=2,
            payload=%s::jsonb,updated_at=clock_timestamp()
            where user_id=%s and kind='memory' and version=1 returning version""",
            (snapshot, user)).fetchone()
        assert changed == {"version": 2}
        # COPY uses PostgreSQL's bulk protocol; each write_row is not an INSERT.
        with cursor.copy("""copy memory_documents
            (user_id,document_id,content,metadata,created_at) from stdin""") as copy:
            for point in points:
                copy.write_row((user, point.id, "e", json.dumps(point.payload,
                    separators=(",", ":"), sort_keys=True), TIMESTAMP))

    try:
        published = service.pair.episode.publish_complete(
            scope, lease, old, points, domain_publish=domain, snapshot_version=2)
    finally:
        coordinator.release(lease)
        del items, points
        gc.collect()
    return published


def _table_fingerprint(cursor, table: str) -> tuple[int, str]:
    # These tables live in the fixture's one-off schema. Fetch bounded pages so
    # the oracle never retains a second 100000-row document copy.
    digest = hashlib.sha256()
    count = 0
    ordering = "user_id,document_id" if table == "memory_documents" else "to_jsonb(t)::text"
    with cursor.connection.cursor(name="physical_scale_oracle") as stream:
        stream.execute(f"select to_jsonb(t)::text as row from {table} t order by {ordering}")
        while batch := stream.fetchmany(512):
            for record in batch:
                encoded = record["row"].encode("utf-8")
                digest.update(len(encoded).to_bytes(8, "big"))
                digest.update(encoded)
                count += 1
    return count, digest.hexdigest()


def _authority_state(db, user, event_id):
    with db.transaction() as cursor:
        schema = cursor.execute("select current_schema() as value").fetchone()["value"]
        tables = {table: _table_fingerprint(cursor, table) for table in TABLES}
        counts = cursor.execute("""select
            (select jsonb_array_length(payload->'memories') from user_snapshots
             where user_id=%s and kind='memory') as snapshot_items,
            (select count(*) from memory_documents where user_id=%s) as raw_rows,
            (select count(*) from memory_documents where user_id=%s and document_id=%s)
                as attempted_event_rows""",
            (user, user, user, event_id)).fetchone()
        heads = cursor.execute("""select vector_kind,generation_id,last_generation_id,
            revision,snapshot_version from vector_heads where tenant_id=%s
            order by vector_kind""", (user,)).fetchall()
        receipts = cursor.execute("""select vector_kind,generation_id,state,
            expected_count,content_digest,publication_revision,
            publication_snapshot_version from vector_generations
            where tenant_id=%s order by vector_kind,generation_id""", (user,)).fetchall()
    return {"schema": schema, "tables": tables, "counts": dict(counts),
            "heads": heads, "receipts": receipts}


def _pg_storage_bytes(db):
    with db.transaction() as cursor:
        return cursor.execute("""select
            pg_total_relation_size('memory_documents') as raw_rows,
            pg_total_relation_size('user_snapshots') as snapshots""").fetchone()


def _forbid_writes(monkeypatch, store, rag, episode):
    calls = {name: 0 for name in ("s3_put", "s3_raw_put", "rag_stage",
                                   "episode_stage", "rag_upsert", "episode_upsert")}

    def guard(name):
        def fail(*args, **kwargs):
            calls[name] += 1
            pytest.fail(f"preflight crossed the {name} write boundary")
        return fail

    monkeypatch.setattr(store, "put_immutable", guard("s3_put"))
    monkeypatch.setattr(store.client, "put_object", guard("s3_raw_put"))
    monkeypatch.setattr(rag.authority, "stage", guard("rag_stage"))
    monkeypatch.setattr(episode.authority, "stage", guard("episode_stage"))
    monkeypatch.setattr(rag.raw.client, "upsert", guard("rag_upsert"))
    monkeypatch.setattr(episode.raw.client, "upsert", guard("episode_upsert"))
    return calls


def test_physical_100000_point_publication_refuses_100001_before_any_write(
        memory_publication, monkeypatch, request):
    service, db, store, rag, _, rag_scope, episode_scope, profile, user, _ = memory_publication
    episode = service.pair.episode
    ledger = {
        "schema": None, "bucket": store.bucket,
        "rag_collection": rag_scope.identity.physical_collection,
        "episode_collection": episode_scope.identity.physical_collection,
        "point_count_requested": POINT_COUNT,
        "qdrant_upsert_batches_expected": ceil(POINT_COUNT / episode.raw.UPSERT_BATCH_SIZE),
        "qdrant_scan_pages_expected_per_full_scan": ceil(POINT_COUNT / 128),
        "lease_seconds": LEASE_SECONDS,
    }
    started = time.perf_counter()
    try:
        with db.transaction() as cursor:
            ledger["schema"] = cursor.execute(
                "select current_schema() as schema").fetchone()["schema"]
        baseline_io = {"upsert_calls": 0, "scroll_pages": 0}
        original_upsert = episode.raw.client.upsert
        original_scroll = episode.raw.client.scroll

        def counted_upsert(*args, **kwargs):
            baseline_io["upsert_calls"] += 1
            return original_upsert(*args, **kwargs)

        def counted_scroll(*args, **kwargs):
            baseline_io["scroll_pages"] += 1
            return original_scroll(*args, **kwargs)

        with monkeypatch.context() as baseline_patch:
            baseline_patch.setattr(episode.raw.client, "upsert", counted_upsert)
            baseline_patch.setattr(episode.raw.client, "scroll", counted_scroll)
            published = _publish_physical_baseline(service, db, episode_scope, user)
        assert baseline_io["upsert_calls"] == ceil(POINT_COUNT / episode.raw.UPSERT_BATCH_SIZE)
        assert baseline_io["scroll_pages"] == ceil(POINT_COUNT / 128)
        ledger["baseline_qdrant_upsert_calls"] = baseline_io["upsert_calls"]
        ledger["baseline_qdrant_verify_pages"] = baseline_io["scroll_pages"]
        ledger["baseline_seconds"] = round(time.perf_counter() - started, 3)
        ledger["episode_generation_id"] = str(published.generation_id)
        ledger["episode_receipt_count"] = POINT_COUNT
        ledger["episode_receipt_digest"] = episode.authority.publication_receipt(
            episode_scope, published.generation_id)["content_digest"]
        assert published.head.generation_id == published.generation_id
        assert published.head.snapshot_version == 2
        assert episode.raw.count(episode_scope.identity.physical_collection,
                                 _scope_fields(episode_scope, published.generation_id)) == POINT_COUNT
        ledger["qdrant_raw_filtered_count"] = POINT_COUNT
        info = episode.raw.client.get_collection(episode_scope.identity.physical_collection)
        ledger["qdrant_reported_collection_points"] = info.points_count

        scan_pages = {"baseline_read": 0, "refusal": 0, "post_public": 0}
        scan_phase = {"name": "baseline_read"}

        def counted_public_scroll(*args, **kwargs):
            scan_pages[scan_phase["name"]] += 1
            return original_scroll(*args, **kwargs)

        monkeypatch.setattr(episode.raw.client, "scroll", counted_public_scroll)

        # This calls the real 782-page manifest scan, then releases its large bundle.
        capture_started = time.perf_counter()
        bundle = service.episodes._capture_bundle(user)
        assert len(bundle._points) == POINT_COUNT
        assert len(bundle._snapshot["memories"]) == POINT_COUNT
        assert len(bundle._documents) == POINT_COUNT
        assert bundle._receipt["expected_count"] == POINT_COUNT
        assert bundle._receipt["content_digest"] == ledger["episode_receipt_digest"]
        ledger["baseline_capture_seconds"] = round(time.perf_counter() - capture_started, 3)
        assert scan_pages["baseline_read"] == ceil(POINT_COUNT / 128)
        del bundle
        gc.collect()

        # The source object exists before the write fence; the claimed task has
        # a full-day lease so the large read cannot be mistaken for expiry.
        attempt = _task(db, store, user, content=b"physical-scale-100001", name="scale.txt")
        attempt = PostgresImportLeaseRepository(db).heartbeat(
            attempt, lease_seconds=LEASE_SECONDS)
        event_id = "import-" + str(uuid5(PROJECT_POINT_NAMESPACE_UUID,
                                           f"{user}:{attempt.task.task_id}"))
        before = _authority_state(db, user, event_id)
        ledger["schema"] = before["schema"]
        assert before["counts"]["snapshot_items"] == POINT_COUNT
        assert before["counts"]["raw_rows"] == POINT_COUNT
        assert before["counts"]["attempted_event_rows"] == 0
        assert len(before["heads"]) == 2
        assert any(row["generation_id"] == published.generation_id and
                   row["state"] == "published" and row["expected_count"] == POINT_COUNT and
                   row["content_digest"] == ledger["episode_receipt_digest"]
                   for row in before["receipts"])
        ledger["pg_snapshot_items"] = before["counts"]["snapshot_items"]
        ledger["pg_raw_rows"] = before["counts"]["raw_rows"]
        storage = _pg_storage_bytes(db)
        ledger["pg_raw_storage_bytes"] = storage["raw_rows"]
        ledger["pg_snapshot_storage_bytes"] = storage["snapshots"]
        ledger["pg_memory_documents_digest"] = before["tables"]["memory_documents"][1]
        ledger["pg_memory_snapshot_digest"] = before["tables"]["user_snapshots"][1]
        ledger["generation_ids"] = [str(row["generation_id"])
                                     for row in before["receipts"]]
        ledger["task_id"] = attempt.task.task_id
        ledger["batch_id"] = attempt.task.batch_id
        ledger["document_id"] = attempt.task.document_id

        original_capture = service.episodes._capture_bundle
        completed_scans = []

        def observed_capture(owner):
            result = original_capture(owner)
            assert len(result._points) == POINT_COUNT
            assert result._receipt["content_digest"] == ledger["episode_receipt_digest"]
            completed_scans.append(len(result._points))
            return result

        monkeypatch.setattr(service.episodes, "_capture_bundle", observed_capture)
        calls = _forbid_writes(monkeypatch, store, rag, episode)
        scan_phase["name"] = "refusal"
        preflight_started = time.perf_counter()
        with pytest.raises(ImportMemoryPublicationError, match="^Episode corpus exceeds bound$"):
            service.publish(rag_scope, attempt,
                [_point(rag_scope, attempt.task.document_id, "scale-new")],
                event_vector=[1.0, 0.0, 0.0, 0.0], event_profile=profile)
        ledger["refusal_seconds"] = round(time.perf_counter() - preflight_started, 3)
        assert completed_scans == [POINT_COUNT]
        assert scan_pages["refusal"] == ceil(POINT_COUNT / 128)
        assert calls == {name: 0 for name in calls}
        after = _authority_state(db, user, event_id)
        assert after == before
        assert episode.raw.count(episode_scope.identity.physical_collection,
                                 _scope_fields(episode_scope, published.generation_id)) == POINT_COUNT
        assert rag.read_view(rag_scope).count(rag_scope.identity.physical_collection) == 0
        scan_phase["name"] = "post_public"
        public_after = PublishedEpisodeReadFactory(episode, episode_scope).open_operation(user)
        public_points = public_after.scroll()
        assert len(public_points) == POINT_COUNT
        assert any(point.id == "scale-000000" for point in public_points)
        assert any(point.id == "scale-099999" for point in public_points)
        del public_points, public_after
        assert scan_pages["post_public"] == 2 * ceil(POINT_COUNT / 128)
        ledger["qdrant_manifest_pages"] = scan_pages
        ledger["post_refusal_qdrant_raw_filtered_count"] = POINT_COUNT
        ledger["result"] = "physical 100000 baseline; 100001 refused before external writes"
    finally:
        ledger["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        try:
            import psutil
            ledger["peak_process_rss_bytes"] = psutil.Process().memory_info().peak_wset
        except (ImportError, AttributeError):
            ledger["peak_process_rss_bytes"] = None
        request.node.user_properties.append(("physical_scale_ledger",
                                             json.dumps(ledger, default=str)))
        print("PHYSICAL_SCALE_LEDGER " + json.dumps(ledger, sort_keys=True, default=str),
              file=sys.stderr)
