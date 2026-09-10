from contextlib import closing
from copy import deepcopy
import hashlib
import json
import os
import sqlite3

import pytest

from hello_agents.memory.rag.embedding_runtime import build_rag_embedding
from hello_agents.memory.rag.index_identity import IndexIdentity
from hello_agents.memory.rag.source_inventory import build_inventory, inspect_inventory, iter_partitions
from hello_agents.memory.rag.source_json import JsonChunkSource
import hello_agents.memory.rag.source_json as stream_module
from hello_agents.memory.rag.source_qdrant import QdrantChunkSource
from hello_agents.memory.rag.source_records import SourceInventoryError
from hello_agents.memory.storage.vector_scan import VectorScanPage, VectorScanRecord


OWNERS = [("pdf_user-a", "doc-a", "user-a")]


def chunk(i=0, *, namespace="pdf_user-a", document="doc-a", fingerprint=None, dimension=2):
    content = f"公开学习资料 {i}"
    metadata = {
        "memory_id": f"{document}_{i}", "document_id": document, "content": content,
        "rag_namespace": namespace, "chunk_index": i, "document_version": 2,
        "file_name": "public.pdf", "page_number": i + 1, "created_at": "2026-09-03T00:00:00Z",
        "updated_at": "2026-09-03T00:00:00Z",
    }
    if fingerprint:
        metadata["embedding_fingerprint"] = fingerprint
    return {"id": f"{document}_{i}", "document_id": document, "content": content,
            "vector": [1.0] + [0.0] * (dimension - 1), "metadata": metadata}


def document(rows):
    return {"collection_name": "source", "rag_namespace": "pdf_user-a", "dimension": 2,
            "updated_at": "2026-09-03T00:00:00", "chunk_count": len(rows), "chunks": rows}


def source_file(tmp_path, rows=None, *, raw=None):
    path = tmp_path / "source.json"
    text = raw if raw is not None else json.dumps(document(rows if rows is not None else [chunk()]), ensure_ascii=False)
    path.write_text(text, encoding="utf-8")
    return JsonChunkSource(path, collection="source", namespace="pdf_user-a", dimension=2)


def test_json_roundtrip_inventory_retains_provenance_and_survives_reopen(tmp_path):
    source = source_file(tmp_path, [chunk(0), chunk(1)])
    original = source.path.read_bytes()
    target = tmp_path / "inventory.sqlite"
    summary = build_inventory(target, source=source, owners=OWNERS)
    assert summary.count == 2 and summary.partitions == 1
    assert source.completed_token == hashlib.sha256(original).hexdigest()
    assert inspect_inventory(target, expected=summary) == summary
    part, = list(iter_partitions(target, expected=summary))
    assert part.count == 2 and part.user_id == "user-a"
    assert part.content_bytes == sum(len(chunk(i)["content"].encode()) for i in range(2))
    with sqlite3.connect(target) as db:
        saved = json.loads(db.execute("SELECT record FROM chunks ORDER BY chunk_index").fetchone()[0])
        assert saved["metadata"] == chunk()["metadata"]
        assert "vector" not in saved
    assert source.path.read_bytes() == original


def test_sorted_managed_cache_with_chunks_first_is_supported(tmp_path):
    profile = build_rag_embedding({"RAG_EMBEDDING_PROVIDER": "siliconflow",
                                  "RAG_EMBEDDING_API_KEY": "fake"}, backend="json").profile
    identity = IndexIdentity("json", "source", profile)
    rows = [chunk(fingerprint=profile.fingerprint, dimension=profile.dimension)]
    data = {"schema_version": 2, "identity": identity.to_dict(), "rag_namespace": "pdf_user-a",
            "updated_at": "2026-09-03T00:00:00Z", "chunk_count": 1, "chunks": rows}
    path = tmp_path / "managed.json"
    path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
    source = JsonChunkSource(path, collection=identity.physical_collection, namespace="pdf_user-a",
                             dimension=profile.dimension, identity=identity)
    assert build_inventory(tmp_path / "index.sqlite", source=source, owners=OWNERS).count == 1
    data["identity"]["fingerprint"] = "0" * 64
    path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
    with pytest.raises(SourceInventoryError):
        build_inventory(tmp_path / "bad.sqlite", source=source, owners=OWNERS)
    with pytest.raises(SourceInventoryError, match="incomplete_inventory"):
        inspect_inventory(tmp_path / "bad.sqlite")


@pytest.mark.parametrize("mutation", [
    "duplicate", "cross_namespace", "cross_document", "content_conflict", "id_conflict",
    "index_duplicate", "index_gap", "mixed_version", "unknown_identity", "secret",
    "empty_content", "bad_vector", "zero_vector", "bool_version", "bad_metadata",
])
def test_invalid_records_never_become_accepted_inventory(tmp_path, mutation):
    rows = [chunk(0), chunk(1)]
    if mutation == "duplicate":
        rows[1] = deepcopy(rows[0])
    elif mutation == "cross_namespace":
        rows[1]["metadata"]["rag_namespace"] = "pdf_user-b"
    elif mutation == "cross_document":
        rows[1] = chunk(1, document="doc-b")
    elif mutation == "content_conflict":
        rows[1]["metadata"]["content"] = "forged"
    elif mutation == "id_conflict":
        rows[1]["metadata"]["memory_id"] = "forged"
    elif mutation == "index_duplicate":
        rows[1]["metadata"]["chunk_index"] = 0
    elif mutation == "index_gap":
        rows[1]["metadata"]["chunk_index"] = 2
    elif mutation == "mixed_version":
        rows[1]["metadata"]["document_version"] = 3
    elif mutation == "unknown_identity":
        rows[1]["metadata"]["embedding_fingerprint"] = "0" * 64
    elif mutation == "secret":
        rows[1]["metadata"]["nested"] = {"api_key": "private-key"}
    elif mutation == "empty_content":
        rows[1]["content"] = rows[1]["metadata"]["content"] = ""
    elif mutation == "bad_vector":
        rows[1]["vector"] = ["secret", 0]
    elif mutation == "zero_vector":
        rows[1]["vector"] = [0, 0]
    elif mutation == "bool_version":
        rows[1]["metadata"]["document_version"] = True
    elif mutation == "bad_metadata":
        rows[1]["metadata"] = None
    source = source_file(tmp_path, rows)
    with pytest.raises(SourceInventoryError) as caught:
        build_inventory(tmp_path / "bad.sqlite", source=source, owners=OWNERS)
    assert "private-key" not in str(caught.value)
    with pytest.raises(SourceInventoryError):
        inspect_inventory(tmp_path / "bad.sqlite")


@pytest.mark.parametrize("mutation", ["missing", "count", "namespace", "dimension", "date", "trailing",
                                      "duplicate_top", "duplicate_nested", "root_array", "truncated", "nan"])
def test_corrupt_envelopes_fail_closed_even_after_rows_were_yielded(tmp_path, mutation):
    data = document([chunk()])
    if mutation == "missing":
        data.pop("chunks")
    elif mutation == "count":
        data["chunk_count"] = 0
    elif mutation == "namespace":
        data["rag_namespace"] = "other"
    elif mutation == "dimension":
        data["dimension"] = True
    elif mutation == "date":
        data["updated_at"] = "invalid"
    raw = json.dumps(data)
    if mutation == "trailing":
        raw += " {}"
    elif mutation == "duplicate_top":
        raw = raw[:-1] + ', "chunks":[]}'
    elif mutation == "duplicate_nested":
        raw = raw.replace('"page_number": 1', '"page_number": 1, "page_number": 2')
    elif mutation == "root_array":
        raw = "[]"
    elif mutation == "truncated":
        raw = raw[:-2]
    elif mutation == "nan":
        raw = raw.replace('"vector": [1.0, 0.0]', '"vector": [NaN, 0.0]')
    source = source_file(tmp_path, raw=raw)
    with pytest.raises(SourceInventoryError):
        build_inventory(tmp_path / "bad.sqlite", source=source, owners=OWNERS)
    assert source.completed_token is None


def test_empty_is_explicit_missing_source_and_missing_owner_partition_are_errors(tmp_path):
    source = source_file(tmp_path, [])
    assert build_inventory(tmp_path / "empty.sqlite", source=source, owners=[]).count == 0
    with pytest.raises(SourceInventoryError, match="incomplete_partition"):
        build_inventory(tmp_path / "missing-part.sqlite", source=source, owners=OWNERS)
    source.path.unlink()
    with pytest.raises(SourceInventoryError):
        build_inventory(tmp_path / "missing-file.sqlite", source=source, owners=[])


def test_destination_is_never_overwritten(tmp_path):
    source = source_file(tmp_path)
    before = source.path.read_bytes()
    with pytest.raises(SourceInventoryError, match="destination"):
        build_inventory(source.path, source=source, owners=OWNERS)
    assert source.path.read_bytes() == before


def test_partial_scan_has_no_completion_receipt_and_closes_file(tmp_path):
    source = source_file(tmp_path, [chunk(0), chunk(1)])
    records = source.records()
    next(records)
    with pytest.raises(SourceInventoryError, match="scan_busy"):
        next(source.records())
    records.close()
    assert source.completed_token is None
    # A partial iterator cannot accidentally be accepted by the builder.
    source.records = lambda: iter([chunk_record()])
    with pytest.raises(SourceInventoryError, match="incomplete_source"):
        build_inventory(tmp_path / "partial.sqlite", source=source, owners=OWNERS)


def chunk_record():
    from hello_agents.memory.rag.source_records import json_chunk
    return json_chunk(chunk(), "pdf_user-a", None)


def test_file_change_during_scan_and_changed_rescan_refuse_reuse(tmp_path):
    source = source_file(tmp_path, [chunk(0), chunk(1)])
    records = source.records()
    next(records)
    info = source.path.stat()
    os.utime(source.path, ns=(info.st_atime_ns, info.st_mtime_ns + 2_000_000_000))
    with pytest.raises(SourceInventoryError, match="source_changed"):
        list(records)
    original = build_inventory(tmp_path / "first.sqlite", source=source, owners=OWNERS)
    rows = [chunk(0), chunk(1)]
    rows[0]["metadata"]["page_number"] = 99
    source = source_file(tmp_path, rows)
    build_inventory(tmp_path / "second.sqlite", source=source, owners=OWNERS)
    with pytest.raises(SourceInventoryError, match="source_changed"):
        inspect_inventory(tmp_path / "second.sqlite", expected=original)


def test_stat_and_fstat_ctime_are_compared_within_their_own_api(tmp_path, monkeypatch):
    from types import SimpleNamespace
    source = source_file(tmp_path)
    original = stream_module.os.fstat
    def different_ctime(fd):
        info = original(fd)
        return SimpleNamespace(
            st_dev=info.st_dev, st_ino=info.st_ino, st_size=info.st_size,
            st_mtime_ns=info.st_mtime_ns, st_ctime_ns=info.st_ctime_ns + 123,
        )
    monkeypatch.setattr(stream_module.os, "fstat", different_ctime)
    assert len(list(source.records())) == 1
    assert source.completed_token is not None


def test_streaming_reads_are_bounded_and_early_close_does_not_read_file(tmp_path, monkeypatch):
    source = source_file(tmp_path, [chunk(i) for i in range(2000)])
    sizes = []
    read = stream_module._Reader.read
    def observed(self, size):
        data = read(self, size)
        sizes.append(len(data))
        return data
    monkeypatch.setattr(stream_module._Reader, "read", observed)
    records = source.records()
    next(records)
    records.close()
    assert max(sizes) <= 65536
    assert sum(sizes) < source.path.stat().st_size
    assert source.completed_token is None


def test_stream_rejects_deep_values_and_oversized_tokens(tmp_path, monkeypatch):
    row = chunk()
    nested = {}
    row["metadata"]["nested"] = nested
    for _ in range(40):
        nested["next"] = {}
        nested = nested["next"]
    with pytest.raises(SourceInventoryError, match="value_size"):
        list(source_file(tmp_path, [row]).records())
    monkeypatch.setattr(stream_module, "_MAX_VALUE_BYTES", 1024)
    row = chunk()
    row["metadata"]["huge"] = "x" * 100_000
    with pytest.raises(SourceInventoryError, match="value_size"):
        list(source_file(tmp_path, [row]).records())


class PageStore:
    def __init__(self, rows, *, after=None):
        self.rows = rows
        self.calls = 0
        self.after = len(rows) if after is None else after

    def require_collection(self, collection, dimension):
        assert collection == "source" and dimension == 2

    def count(self, collection):
        self.calls += 1
        return len(self.rows) if self.calls == 1 else self.after

    def iter_scroll_pages(self, collection, **kwargs):
        for i, row in enumerate(self.rows):
            yield VectorScanPage((row,), None if i == len(self.rows) - 1 else i + 1)


def qrow(i=0, *, storage_id=None, namespace="pdf_user-a", document="doc-a"):
    row = chunk(i, namespace=namespace, document=document)
    metadata = dict(row["metadata"])
    payload = {k: metadata.pop(k) for k in ("document_id", "rag_namespace", "content", "chunk_index",
                                           "document_version", "created_at", "updated_at")}
    payload["metadata"] = metadata
    return VectorScanRecord(i if storage_id is None else storage_id, row["id"], payload)


def test_qdrant_source_is_order_independent_and_preserves_native_ids(tmp_path):
    rows = [qrow(0), qrow(1)]
    def run(name, records):
        source = QdrantChunkSource(PageStore(records), collection="source", dimension=2)
        return build_inventory(tmp_path / name, source=source, owners=OWNERS)
    first = run("a.sqlite", rows)
    assert run("b.sqlite", list(reversed(rows))) == first
    part, = iter_partitions(tmp_path / "a.sqlite", expected=first)
    assert part.count == 2 and part.content_bytes > 0


@pytest.mark.parametrize("case", ["count", "unknown_owner", "duplicate_physical", "conflict", "missing_owner"])
def test_qdrant_ownership_duplicates_and_count_change_fail(tmp_path, case):
    rows = [qrow(0), qrow(1)]
    owners = OWNERS
    if case == "unknown_owner":
        rows[1] = qrow(1, namespace="pdf_other")
    elif case == "duplicate_physical":
        rows[1] = qrow(1, storage_id=0)
    elif case == "conflict":
        rows[1].payload["metadata"]["document_id"] = "forged"
    elif case == "missing_owner":
        owners = OWNERS + [("pdf_user-a", "missing", "user-a")]
    store = PageStore(rows, after=3 if case == "count" else None)
    source = QdrantChunkSource(store, collection="source", dimension=2)
    with pytest.raises(SourceInventoryError):
        build_inventory(tmp_path / "bad.sqlite", source=source, owners=owners)


def test_corrupted_inventory_and_source_contract_change_are_detected(tmp_path):
    source = source_file(tmp_path)
    target = tmp_path / "inventory.sqlite"
    result = build_inventory(target, source=source, owners=OWNERS)
    with sqlite3.connect(target) as db:
        db.execute("UPDATE chunks SET record='{}'")
    with pytest.raises(SourceInventoryError):
        inspect_inventory(target, expected=result)


def test_content_digest_survives_model_identity_change(tmp_path):
    legacy = source_file(tmp_path)
    old_path = tmp_path / "old.sqlite"
    old = build_inventory(old_path, source=legacy, owners=OWNERS)
    profile = build_rag_embedding({"RAG_EMBEDDING_PROVIDER": "siliconflow",
                                  "RAG_EMBEDDING_API_KEY": "fake"}, backend="json").profile
    identity = IndexIdentity("json", "source", profile)
    raw = {"schema_version": 2, "identity": identity.to_dict(), "rag_namespace": "pdf_user-a",
           "updated_at": "2026-09-03T00:00:00Z", "chunk_count": 1,
           "chunks": [chunk(fingerprint=profile.fingerprint, dimension=profile.dimension)]}
    path = tmp_path / "new.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    source = JsonChunkSource(path, collection=identity.physical_collection, namespace="pdf_user-a",
                             dimension=profile.dimension, identity=identity)
    new_path = tmp_path / "new.sqlite"
    new = build_inventory(new_path, source=source, owners=OWNERS)
    old_part, = iter_partitions(old_path, expected=old)
    new_part, = iter_partitions(new_path, expected=new)
    assert old_part.source_digest != new_part.source_digest
    assert old_part.content_digest == new_part.content_digest
    assert old.fingerprint != new.fingerprint


def test_ownership_fingerprint_and_multi_user_same_logical_ids(tmp_path):
    rows = [qrow(0), qrow(0, storage_id=9, namespace="pdf_user-b")]
    owners = OWNERS + [("pdf_user-b", "doc-a", "user-b")]
    def run(name, authority):
        source = QdrantChunkSource(PageStore(rows), collection="source", dimension=2)
        return build_inventory(tmp_path / name, source=source, owners=authority)
    first = run("a.sqlite", owners)
    assert first.count == 2 and first.partitions == 2
    parts = list(iter_partitions(tmp_path / "a.sqlite", expected=first))
    assert {p.user_id for p in parts} == {"user-a", "user-b"}
    changed = run("b.sqlite", OWNERS + [("pdf_user-b", "doc-a", "different-owner")])
    assert changed.fingerprint != first.fingerprint
    with pytest.raises(SourceInventoryError, match="ownership"):
        run("c.sqlite", OWNERS + [("pdf_user-a", "doc-b", "other")])


def test_uuid_spelling_aliases_do_not_bypass_cross_page_uniqueness(tmp_path):
    import uuid
    identifier = uuid.uuid4()
    rows = [qrow(0, storage_id=str(identifier)), qrow(1, storage_id=identifier.hex.upper())]
    source = QdrantChunkSource(PageStore(rows), collection="source", dimension=2)
    with pytest.raises(SourceInventoryError, match="duplicate"):
        build_inventory(tmp_path / "bad.sqlite", source=source, owners=OWNERS)


def test_interrupt_and_disk_error_cannot_leave_complete_inventory(tmp_path, monkeypatch):
    import hello_agents.memory.rag.source_inventory as inventory_module
    source = source_file(tmp_path)
    original = source.path.read_bytes()
    original_records = source.records

    def interrupted():
        with closing(original_records()) as rows:
            yield next(rows)
            raise KeyboardInterrupt()
    monkeypatch.setattr(source, "records", interrupted)
    with pytest.raises(KeyboardInterrupt):
        build_inventory(tmp_path / "interrupted.sqlite", source=source, owners=OWNERS)
    with pytest.raises(SourceInventoryError, match="incomplete_inventory"):
        inspect_inventory(tmp_path / "interrupted.sqlite")
    assert not source._running
    monkeypatch.setattr(source, "records", original_records)

    def disk_full(_):
        raise OSError("private-source-path: disk full")
    monkeypatch.setattr(inventory_module, "_hash_inventory", disk_full)
    with pytest.raises(SourceInventoryError) as caught:
        build_inventory(tmp_path / "full.sqlite", source=source, owners=OWNERS)
    assert str(caught.value) == "RAG source inventory rejected: storage"
    with sqlite3.connect(tmp_path / "full.sqlite") as db:
        assert db.execute("SELECT status FROM state").fetchone()[0] == "failed"
    assert source.path.read_bytes() == original


def test_embedded_qdrant_inventory_has_no_source_writes(tmp_path):
    from qdrant_client import QdrantClient, models
    from hello_agents.memory.storage.vector_store import QdrantVectorStore
    client = QdrantClient(location=":memory:")
    try:
        client.create_collection("source", vectors_config=models.VectorParams(size=2, distance="Cosine"))
        rows = [qrow(0), qrow(1)]
        client.upsert("source", points=[
            models.PointStruct(id=row.storage_id, vector=[1.0, 0.0],
                               payload={**row.payload, "_vector_store_id": row.logical_id})
            for row in rows
        ])
        before = client.scroll("source", with_vectors=True)[0]
        source = QdrantChunkSource(QdrantVectorStore(client=client), collection="source", dimension=2, page_size=1)
        path = tmp_path / "real-adapter.sqlite"
        summary = build_inventory(path, source=source, owners=OWNERS)
        assert summary.count == 2
        data = path.read_bytes()
        assert inspect_inventory(path, expected=summary) == summary
        assert len(list(iter_partitions(path, expected=summary))) == 1
        assert path.read_bytes() == data
        assert client.scroll("source", with_vectors=True)[0] == before
    finally:
        client.close()
