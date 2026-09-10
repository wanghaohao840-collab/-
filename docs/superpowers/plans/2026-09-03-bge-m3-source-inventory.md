# BGE-M3 Source Inventory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the E3-A2 read-only inventory core: bounded JSON/Qdrant source adapters and a fail-closed disk-backed inventory with per-owner/document counts and digests.

**Architecture:** Reuse E3-A1 Qdrant pages. Parse both verified JSON cache envelopes incrementally with pinned ijson; preserve chunk data without vectors. A management caller must supply authoritative (namespace, document, user) ownership; never derive ownership from vectors. Consume to validated EOF before marking a new SQLite artifact complete. Separate raw-source and model-independent content digests support later rebuild verification.

**Tech Stack:** Existing project Python venv, SQLite stdlib, hashlib, dataclasses, pytest, qdrant-client 1.18.0; new fixed ijson==3.4.0.post0.

## Global Constraints

- Approved design: docs/superpowers/specs/2026-09-03-bge-m3-rag-embedding-design.md sections 7–9.
- Continue serial inline in D:/python_self_agent/.worktrees/bge-m3-runtime-identity, branch codex/bge-m3-runtime-identity, base a33b071 plus accepted, uncommitted E3-A1.
- executing-plans is unavailable; use the reviewed packet inline. No subagents; user already chose this execution mode.
- Preserve E3-A1 files/output and stable-checkout work. No commit, push, merge, real .env access, production data scan, live embedding, container/task change or Neo4j start.
- Do not reparse documents, rechunk content, transform ownership or enable a model.
- Qdrant native page interface is an existing prerequisite. JSON cache legacy fields and schema-2 identity fields were read from actual writers, not assumed.
- Streaming parser: 64 KiB input reads; 8 MiB between emitted bounded values, depth <=32, <=100,000 nodes/value, <=2 MiB canonical record. These are migration safety limits, not model token limits. Oversized/corrupt input is refused, never truncated.
- Inventory files contain private source text/provenance; they belong under managed private application storage in the eventual controller, not Git/report logs. This unit creates only synthetic fixtures in tests.
- Source adapters must finish envelope/count/source-change checks and produce a completion receipt. A partial generator cannot be accepted merely because the observed document has a contiguous prefix of chunks.
- Each authoritative listed document must have chunks with contiguous indexes from 0 and one positive version. Empty deployment is an explicit empty source with no owners, not a missing file/collection.
- Full records and ownership are stored on disk with uniqueness constraints; no collection-wide list or ID set. SQLite uses file-backed temp storage and a 2 MiB cache target.
- New inventory destinations are exclusive-create; failure remains absent/building/failed and cannot be opened as complete. Source files, collections and earlier attempts are never overwritten/deleted.
- This delivers a library core, not application ownership discovery, a CLI, maintenance locking, candidate rebuild, durable rebuild checkpoint, cutover or production acceptance. The controller must later enumerate authoritative accounts/documents safely, supply the complete list, hold the maintenance lock and rescan/compare source fingerprints immediately before resume/cutover. Qdrant equal counts alone do not prove stable content.

## Decisions and evidence

- Rejected whole-file json.loads: retains entire cache and cannot bound migration memory.
- Rejected handwritten JSON parser: unnecessary grammar/security maintenance; ijson supplies standard event parsing and explicit duplicate/depth/size guards wrap it.
- Selected SQLite instead of a growing Python set/list for global duplicate checks and sorted deterministic summaries.
- Application history records document ownership but not authoritative chunk counts (assistants/pdf_learning_assistant.py history_item); no fabricated count requirement. Complete-source counts, complete owner coverage and contiguous source indexes are checked here; snapshot coordination remains mandatory.
- Existing source writers: pipeline.py _save_cache/_serializable_chunks; json_index_cache.py schema 2; qdrant_pipeline.py _upsert_chunks.
- Baseline: existing Qdrant scan and JSON-cache tests: 67 passed in 5.15s.
- Dependency protocol verified against [ijson upstream](https://github.com/ICRAR/ijson) and [pinned package documentation](https://pypi.org/project/ijson/3.4.0.post0/). No runtime network source is introduced.

### Task 1: Implement the complete read-only inventory core

**Files:** Create the five files below; modify requirements.txt with the pinned dependency only. Record results in this plan, its REVIEW/01-source-inventory/FINAL_INTEGRATION_REVIEW under docs/agent-workflow/task-packets/2026-09-03-bge-m3-source-inventory/ and the E3 progress paragraph of the governing spec.

**Interfaces:**
- Consumes QdrantVectorStore.require_collection(collection, dimension), count(collection), iter_scroll_pages(collection, page_size, max_pages); VectorScanRecord.
- JsonChunkSource(path, collection, namespace, dimension, identity=None) and QdrantChunkSource(store, collection, dimension, identity=None, page_size=128, max_pages=10000) expose records(), contract, identity and completed_token.
- build_inventory(path, source, owners) -> InventorySummary; no arbitrary iterable source accepted. owners yields authoritative triples (namespace, document_id, user_id).
- inspect_inventory(path, expected=None) checks read-only artifact integrity and rejects changed source when an expected summary is supplied.
- iter_partitions(path, expected) streams per-document count/content bytes/source digest/content digest under one read transaction.
- SourceInventoryError carries a stable safe code without raw content, paths or parser errors.

- [x] **Step 1: Add failing tests**

Create tests/memory/rag/test_source_inventory.py:

```python
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
```

- [x] **Step 2: Run red verification**

```powershell
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pytest tests/memory/rag/test_source_inventory.py -q --tb=short
```

Expected: collection error because source_inventory does not exist.

- [x] **Step 3: Pin dependency and implement**

Add exactly this requirement after qdrant-client:

```text
ijson==3.4.0.post0
```

Install only this dependency into the existing project venv (no bulk upgrades):

```powershell
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pip install --disable-pip-version-check ijson==3.4.0.post0
```

Create hello_agents/memory/rag/source_records.py:

```python
"""Read-only migration records; callers supply authoritative scope separately."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from hello_agents.memory.rag.errors import RAGConfigError
from hello_agents.memory.rag.index_identity import IndexIdentity, require_point_identity
from hello_agents.memory.rag.prepare import contains_secret_metadata
from hello_agents.memory.storage.vector_scan import VectorScanRecord


class SourceInventoryError(RAGConfigError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(f"RAG source inventory rejected: {code}")


def require_name(value: object) -> str:
    if (not isinstance(value, str) or not value.strip() or len(value) > 512
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise SourceInventoryError("name")
    return value


def canonical(value: object) -> str:
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False)
        # Reject lone surrogates and excessively large records before persistence.
        if len(encoded.encode("utf-8")) > 2 * 1024 * 1024:
            raise SourceInventoryError("record_size")
        return encoded
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise SourceInventoryError("record") from None


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SourceChunk:
    storage_id: int | str
    chunk_id: str
    namespace: str
    document_id: str
    content: str
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "storage_id": self.storage_id, "chunk_id": self.chunk_id,
            "namespace": self.namespace, "document_id": self.document_id,
            "content": self.content, "metadata": self.metadata,
        }


def validate_chunk(chunk: SourceChunk, identity: IndexIdentity | None = None) -> SourceChunk:
    if not isinstance(chunk, SourceChunk):
        raise SourceInventoryError("record")
    require_name(chunk.chunk_id)
    require_name(chunk.namespace)
    require_name(chunk.document_id)
    if type(chunk.storage_id) is int:
        if not 0 <= chunk.storage_id < 2**64:
            raise SourceInventoryError("storage_id")
    else:
        require_name(chunk.storage_id)
    if not isinstance(chunk.content, str) or not chunk.content.strip():
        raise SourceInventoryError("content")
    metadata = chunk.metadata
    if not isinstance(metadata, dict):
        raise SourceInventoryError("metadata")
    try:
        if contains_secret_metadata(metadata):
            raise SourceInventoryError("secret_metadata")
    except RecursionError:
        raise SourceInventoryError("metadata") from None
    for key, expected in (
        ("memory_id", chunk.chunk_id), ("document_id", chunk.document_id),
        ("rag_namespace", chunk.namespace), ("content", chunk.content),
    ):
        if metadata.get(key) != expected:
            raise SourceInventoryError("metadata_conflict")
    if (type(metadata.get("chunk_index")) is not int or not 0 <= metadata["chunk_index"] < 2**63
            or type(metadata.get("document_version")) is not int
            or not 1 <= metadata["document_version"] < 2**63
            or "_vector_store_id" in metadata):
        raise SourceInventoryError("metadata")
    if identity is not None:
        try:
            require_point_identity(metadata, identity=identity,
                                   rag_namespace=chunk.namespace, document_id=chunk.document_id)
        except RAGConfigError:
            raise SourceInventoryError("identity") from None
    elif "embedding_fingerprint" in metadata:
        # An unidentified source cannot silently claim a managed model.
        raise SourceInventoryError("identity")
    canonical(chunk.to_dict())
    return chunk


def json_chunk(raw: object, namespace: str, identity: IndexIdentity | None) -> SourceChunk:
    if not isinstance(raw, dict) or set(raw) != {"id", "document_id", "content", "vector", "metadata"}:
        raise SourceInventoryError("chunk_schema")
    if not isinstance(raw["vector"], list):
        raise SourceInventoryError("vector")
    return validate_chunk(SourceChunk(raw["id"], raw["id"], namespace,
                                      raw["document_id"], raw["content"], raw["metadata"]), identity)


def qdrant_chunk(record: VectorScanRecord, identity: IndexIdentity | None) -> SourceChunk:
    payload = record.payload
    if not isinstance(payload.get("metadata"), dict):
        raise SourceInventoryError("metadata")
    metadata = dict(payload["metadata"])
    # Preserve every source field, but never let flattening hide a conflict.
    for key, value in payload.items():
        if key in {"metadata", "_vector_store_id"}:
            continue
        if key in metadata and metadata[key] != value:
            raise SourceInventoryError("metadata_conflict")
        metadata[key] = value
    return validate_chunk(SourceChunk(
        record.storage_id, record.logical_id, payload.get("rag_namespace"),
        payload.get("document_id"), payload.get("content"), metadata,
    ), identity)
```

Create hello_agents/memory/rag/source_json.py:

```python
"""Bounded streaming reader for legacy and managed JSON RAG caches."""
from __future__ import annotations

from datetime import datetime
import hashlib
import math
import os
from pathlib import Path
import stat

import ijson

from hello_agents.memory.rag.index_identity import IndexIdentity, IndexIdentityError
from hello_agents.memory.rag.source_records import (
    SourceInventoryError, canonical, json_chunk, require_name,
)

_BUFFER = 64 * 1024
_MAX_VALUE_BYTES = 8 * 1024 * 1024
_LEGACY = {"collection_name", "rag_namespace", "dimension", "updated_at", "chunk_count"}
_MANAGED = {"schema_version", "identity", "rag_namespace", "updated_at", "chunk_count"}


def _signature(info):
    # Windows/Python can expose different ctime meanings via stat vs fstat.
    # Compare identity across APIs, but ctime only against the same API later.
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


class _Reader:
    def __init__(self, stream):
        self.stream = stream
        self.hash = hashlib.sha256()
        self.pending = 0

    def read(self, size):
        if size == 0:
            return b""
        data = self.stream.read(min(size, _BUFFER) if size > 0 else _BUFFER)
        self.pending += len(data)
        if self.pending > _MAX_VALUE_BYTES:
            raise SourceInventoryError("value_size")
        self.hash.update(data)
        return data


def _value(events, first, depth=0, budget=None):
    budget = [100_000] if budget is None else budget
    budget[0] -= 1
    if depth > 32 or budget[0] < 0:
        raise SourceInventoryError("value_size")
    event, value = first
    if event == "start_map":
        result = {}
        while True:
            event, key = next(events)
            if event == "end_map":
                return result
            if event != "map_key" or key in result:
                raise SourceInventoryError("duplicate_key")
            result[key] = _value(events, next(events), depth + 1, budget)
    if event == "start_array":
        result = []
        while True:
            item = next(events)
            if item[0] == "end_array":
                return result
            result.append(_value(events, item, depth + 1, budget))
    if event in {"null", "boolean", "number", "string", "integer", "double"}:
        return value
    raise SourceInventoryError("json")


class JsonChunkSource:
    """Exhaust records() to validate the envelope and obtain completed_token.

    A token is an observed file digest, not a maintenance lock or snapshot.
    The caller must close the generator when abandoning a scan.
    """
    def __init__(self, path: Path | str, *, collection: str, namespace: str,
                 dimension: int, identity: IndexIdentity | None = None):
        self.path = Path(path)
        if not self.path.is_absolute():
            raise SourceInventoryError("path")
        self.collection = require_name(collection)
        self.namespace = require_name(namespace)
        if type(dimension) is not int or not 1 <= dimension <= 65536:
            raise SourceInventoryError("dimension")
        if identity is not None and (
            identity.backend != "json" or identity.physical_collection != collection
            or identity.profile.dimension != dimension
        ):
            raise SourceInventoryError("identity")
        self.dimension, self.identity = dimension, identity
        self.completed_token = None
        self._running = False

    @property
    def contract(self):
        return {"backend": "json", "collection": self.collection, "namespace": self.namespace,
                "dimension": self.dimension,
                "source_identity": self.identity.to_dict() if self.identity else None}

    def _header(self, header, count):
        expected = _MANAGED if self.identity else _LEGACY
        if (set(header) != expected or header.get("rag_namespace") != self.namespace
                or type(header.get("chunk_count")) is not int
                or header["chunk_count"] != count):
            raise SourceInventoryError("header")
        try:
            value = header["updated_at"]
            if not isinstance(value, str):
                raise ValueError()
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if self.identity:
                if (type(header["schema_version"]) is not int or header["schema_version"] != 2
                        or not value.endswith("Z") or parsed.utcoffset().total_seconds() != 0):
                    raise ValueError()
                IndexIdentity.from_dict(header["identity"]).require_match(self.identity)
            elif (header["collection_name"] != self.collection
                  or type(header["dimension"]) is not int
                  or header["dimension"] != self.dimension):
                raise ValueError()
        except (ValueError, TypeError, AttributeError, IndexIdentityError):
            raise SourceInventoryError("header") from None

    def records(self):
        if self._running:
            raise SourceInventoryError("scan_busy")
        self._running = True
        self.completed_token = None
        try:
            info = self.path.lstat()
            if (not stat.S_ISREG(info.st_mode)
                    or getattr(info, "st_file_attributes", 0) & 0x400):
                raise SourceInventoryError("path")
            with self.path.open("rb") as stream:
                before = os.fstat(stream.fileno())
                if _signature(before) != _signature(info):
                    raise SourceInventoryError("source_changed")
                reader = _Reader(stream)
                events = iter(ijson.basic_parse(reader, use_float=True, buf_size=_BUFFER))
                if next(events)[0] != "start_map":
                    raise SourceInventoryError("header")
                header, seen = {}, set()
                count = 0
                while True:
                    event, key = next(events)
                    if event == "end_map":
                        break
                    if event != "map_key" or key in seen:
                        raise SourceInventoryError("duplicate_key")
                    seen.add(key)
                    if key not in _LEGACY | _MANAGED | {"chunks"}:
                        raise SourceInventoryError("header")
                    if key == "chunks":
                        if next(events)[0] != "start_array":
                            raise SourceInventoryError("chunks")
                        while True:
                            first = next(events)
                            if first[0] == "end_array":
                                break
                            raw = _value(events, first)
                            canonical(raw)
                            if (not isinstance(raw, dict) or not isinstance(raw.get("vector"), list)
                                    or len(raw["vector"]) != self.dimension
                                    or any(type(v) not in (int, float) or not math.isfinite(v) for v in raw["vector"])
                                    or not any(raw["vector"])):
                                raise SourceInventoryError("vector")
                            chunk = json_chunk(raw, self.namespace, self.identity)
                            count += 1
                            reader.pending = 0
                            yield chunk
                    else:
                        header[key] = _value(events, next(events))
                        canonical(header[key])
                    reader.pending = 0
                if "chunks" not in seen or next(events, None) is not None:
                    raise SourceInventoryError("header")
                self._header(header, count)
                after = os.fstat(stream.fileno())
                path_after = self.path.lstat()
                if (_signature(before) != _signature(after)
                        or before.st_ctime_ns != after.st_ctime_ns
                        or _signature(info) != _signature(path_after)
                        or info.st_ctime_ns != path_after.st_ctime_ns):
                    raise SourceInventoryError("source_changed")
                self.completed_token = reader.hash.hexdigest()
        except SourceInventoryError:
            raise
        except (OSError, UnicodeError, ValueError, OverflowError, RecursionError,
                StopIteration, ijson.JSONError):
            raise SourceInventoryError("json") from None
        finally:
            self._running = False

```

Create hello_agents/memory/rag/source_qdrant.py:

```python
"""Complete bounded Qdrant scans; counts alone are not a content snapshot."""
from hello_agents.memory.rag.source_records import SourceInventoryError, digest, qdrant_chunk, require_name
from hello_agents.memory.storage.vector_scan import native_point_id


class QdrantChunkSource:
    def __init__(self, store, *, collection, dimension, identity=None, page_size=128, max_pages=10_000):
        self.store = store
        self.collection = require_name(collection)
        if type(dimension) is not int or not 1 <= dimension <= 65536:
            raise SourceInventoryError("dimension")
        if identity and (identity.backend != "qdrant" or identity.physical_collection != collection
                         or identity.profile.dimension != dimension):
            raise SourceInventoryError("identity")
        self.dimension, self.identity = dimension, identity
        self.page_size, self.max_pages = page_size, max_pages
        self.completed_token = None
        self._running = False

    @property
    def contract(self):
        return {"backend": "qdrant", "collection": self.collection, "namespace": None,
                "dimension": self.dimension,
                "source_identity": self.identity.to_dict() if self.identity else None}

    def records(self):
        if self._running:
            raise SourceInventoryError("scan_busy")
        self._running = True
        self.completed_token = None
        try:
            self.store.require_collection(self.collection, self.dimension)
            before = self.store.count(self.collection)
            if type(before) is not int or before < 0:
                raise SourceInventoryError("count")
            count = 0
            pages = self.store.iter_scroll_pages(self.collection, page_size=self.page_size,
                                                max_pages=self.max_pages)
            try:
                for page in pages:
                    for record in page.records:
                        native_point_id(record.storage_id)
                        count += 1
                        yield qdrant_chunk(record, self.identity)
            finally:
                pages.close()
            after = self.store.count(self.collection)
            if type(after) is not int or before != count or after != count:
                raise SourceInventoryError("source_changed")
            self.completed_token = digest(["qdrant-count-v1", count])
        finally:
            self._running = False
```

Create hello_agents/memory/rag/source_inventory.py:

```python
"""Disk-backed source inventory. No source mutation or production activation."""
from __future__ import annotations

from contextlib import closing
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import uuid

from hello_agents.memory.rag.source_records import (
    SourceChunk, SourceInventoryError, canonical, digest, require_name, validate_chunk,
)

from hello_agents.memory.rag.source_json import JsonChunkSource
from hello_agents.memory.rag.source_qdrant import QdrantChunkSource
from hello_agents.memory.rag.index_identity import IndexIdentity
from hello_agents.memory.storage.vector_scan import native_point_id


@dataclass(frozen=True)
class PartitionSummary:
    namespace: str
    document_id: str
    user_id: str
    count: int
    content_bytes: int
    source_digest: str
    content_digest: str


@dataclass(frozen=True)
class InventorySummary:
    count: int
    partitions: int
    fingerprint: str


_SCHEMA = """
CREATE TABLE state (id INTEGER PRIMARY KEY CHECK(id=1), status TEXT NOT NULL,
                    contract TEXT NOT NULL, receipt TEXT, fingerprint TEXT);
CREATE TABLE owners (namespace TEXT NOT NULL, document_id TEXT NOT NULL,
                     user_id TEXT NOT NULL, PRIMARY KEY(namespace, document_id));
CREATE TABLE chunks (namespace TEXT NOT NULL, document_id TEXT NOT NULL,
                     chunk_id TEXT NOT NULL, chunk_index INTEGER NOT NULL,
                     version INTEGER NOT NULL, storage_key TEXT NOT NULL UNIQUE,
                     record TEXT NOT NULL, digest TEXT NOT NULL,
                     PRIMARY KEY(namespace, chunk_id),
                     UNIQUE(namespace, document_id, chunk_index),
                     FOREIGN KEY(namespace, document_id) REFERENCES owners);
CREATE INDEX ordered_chunks ON chunks(namespace, document_id, chunk_id);
"""


def _partitions(db, identity):
    for namespace, document_id, user_id in db.execute(
        "SELECT namespace,document_id,user_id FROM owners ORDER BY namespace,document_id"
    ):
        source_hash, content_hash = hashlib.sha256(), hashlib.sha256()
        count, content_bytes = 0, 0
        for row in db.execute(
            """SELECT chunk_id,chunk_index,version,record,digest FROM chunks
            WHERE namespace=? AND document_id=? ORDER BY chunk_id""", (namespace, document_id)
        ):
            data = json.loads(row[3])
            chunk = validate_chunk(SourceChunk(**data), identity)
            if (chunk.namespace != namespace or chunk.document_id != document_id
                    or chunk.chunk_id != row[0] or chunk.metadata["chunk_index"] != row[1]
                    or chunk.metadata["document_version"] != row[2] or digest(data) != row[4]):
                raise SourceInventoryError("corrupt_inventory")
            source_hash.update(row[4].encode("ascii"))
            comparable = dict(data)
            comparable.pop("storage_id")
            comparable["metadata"] = {k: v for k, v in chunk.metadata.items()
                                      if k != "embedding_fingerprint"}
            content_hash.update(digest(comparable).encode("ascii"))
            count += 1
            content_bytes += len(chunk.content.encode("utf-8"))
        yield PartitionSummary(namespace, document_id, user_id, count, content_bytes,
                               source_hash.hexdigest(), content_hash.hexdigest())


def _hash_inventory(db):
    contract, receipt = db.execute("SELECT contract,receipt FROM state WHERE id=1").fetchone()
    descriptor = json.loads(contract)
    identity = IndexIdentity.from_dict(descriptor["source_identity"]) if descriptor["source_identity"] else None
    hasher = hashlib.sha256(canonical(["source-inventory-v1", descriptor, receipt]).encode())
    count, partitions = 0, 0
    for part in _partitions(db, identity):
        hasher.update(digest(asdict(part)).encode("ascii"))
        count += part.count
        partitions += 1
    return InventorySummary(count, partitions, hasher.hexdigest())


def build_inventory(path: Path | str, *, source, owners) -> InventorySummary:
    """Create a new inventory from a *complete* source scan.

    owners yields (namespace, document_id, user_id), supplied by the management
    layer from authoritative records, never inferred from the source payload.
    Each listed document must have chunks. Empty deployment is owners=[] and
    an explicitly successful empty source, not a missing source.
    Consume under the maintenance controller; rescan and compare before reuse.
    """
    if not isinstance(source, (JsonChunkSource, QdrantChunkSource)):
        raise SourceInventoryError("source")
    contract = source.contract
    identity = source.identity
    path = Path(path)
    if not path.is_absolute():
        raise SourceInventoryError("path")
    # Serialize configuration before creating an artifact. The approved identity
    # includes its non-secret endpoint, never credentials or filesystem paths.
    if (not isinstance(contract, dict)
            or set(contract) != {"backend", "collection", "namespace", "dimension", "source_identity"}
            or contract["backend"] not in {"json", "qdrant"}):
        raise SourceInventoryError("contract")
    require_name(contract["collection"])
    if contract["namespace"] is not None:
        require_name(contract["namespace"])
    expected_identity = identity.to_dict() if identity else None
    if contract["source_identity"] != expected_identity:
        raise SourceInventoryError("identity")
    if identity and (identity.backend != contract["backend"]
                     or identity.physical_collection != contract["collection"]):
        raise SourceInventoryError("identity")
    encoded_contract = canonical(contract)
    iterator = source.records()
    try:
        # Never overwrite an inventory, source file or previous partial attempt.
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    except OSError:
        raise SourceInventoryError("destination") from None
    db = None
    try:
        db = sqlite3.connect(path)
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=DELETE")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA temp_store=FILE")
        db.execute("PRAGMA cache_size=-2048")
        db.executescript(_SCHEMA)
        db.execute("INSERT INTO state VALUES(1,'building',?,NULL,NULL)", (encoded_contract,))
        db.commit()
        for namespace, document_id, user_id in owners:
            for value in (namespace, document_id, user_id):
                require_name(value)
            if contract["namespace"] is not None and namespace != contract["namespace"]:
                raise SourceInventoryError("ownership")
            # Enforce one namespace -> one owner, but never derive it from names.
            prior = db.execute("SELECT user_id FROM owners WHERE namespace=? LIMIT 1", (namespace,)).fetchone()
            if prior and prior[0] != user_id:
                raise SourceInventoryError("ownership")
            db.execute("INSERT INTO owners VALUES(?,?,?)", (namespace, document_id, user_id))
        for chunk in iterator:
            chunk = validate_chunk(chunk, identity)
            if contract["namespace"] is not None and chunk.namespace != contract["namespace"]:
                raise SourceInventoryError("ownership")
            if not db.execute("SELECT 1 FROM owners WHERE namespace=? AND document_id=?",
                              (chunk.namespace, chunk.document_id)).fetchone():
                raise SourceInventoryError("ownership")
            metadata = chunk.metadata
            version = metadata["document_version"]
            previous = db.execute("SELECT version FROM chunks WHERE namespace=? AND document_id=? LIMIT 1",
                                  (chunk.namespace, chunk.document_id)).fetchone()
            if previous and previous[0] != version:
                raise SourceInventoryError("mixed_version")
            record = chunk.to_dict()
            # Physical JSON IDs are only unique within each namespace/file.
            storage_id = chunk.storage_id
            if contract["backend"] == "qdrant":
                storage_id = native_point_id(storage_id)
                if isinstance(storage_id, str):
                    storage_id = str(uuid.UUID(storage_id))
            storage_key = canonical([contract["backend"], chunk.namespace if contract["backend"] == "json" else None,
                                     type(storage_id).__name__, storage_id])
            db.execute("INSERT INTO chunks VALUES(?,?,?,?,?,?,?,?)", (
                chunk.namespace, chunk.document_id, chunk.chunk_id, metadata["chunk_index"],
                version, storage_key, canonical(record), digest(record),
            ))
        if not isinstance(source.completed_token, str) or len(source.completed_token) != 64:
            raise SourceInventoryError("incomplete_source")
        db.execute("UPDATE state SET receipt=? WHERE id=1", (source.completed_token,))
        # Reject missing partitions and index holes; no partial read acceptance.
        if db.execute("""SELECT 1 FROM owners o LEFT JOIN chunks c
            ON o.namespace=c.namespace AND o.document_id=c.document_id
            GROUP BY o.namespace,o.document_id
            HAVING count(c.chunk_id)=0 OR min(c.chunk_index)!=0
                OR max(c.chunk_index)!=count(c.chunk_id)-1 LIMIT 1""").fetchone():
            raise SourceInventoryError("incomplete_partition")
        summary = _hash_inventory(db)
        db.execute("UPDATE state SET status='complete',fingerprint=? WHERE id=1", (summary.fingerprint,))
        db.commit()
        return summary
    except BaseException as exc:
        if db is not None:
            try:
                db.rollback()
                db.execute("UPDATE state SET status='failed' WHERE id=1")
                db.commit()
            except sqlite3.Error:
                pass  # An absent/building state also refuses acceptance.
        if isinstance(exc, sqlite3.IntegrityError):
            raise SourceInventoryError("duplicate") from None
        if isinstance(exc, (sqlite3.Error, OSError, OverflowError)):
            raise SourceInventoryError("storage") from None
        raise
    finally:
        if db is not None:
            db.close()
        close = getattr(iterator, "close", None)
        if close:
            close()


def _require_complete(db, expected):
    state = db.execute("SELECT status,fingerprint FROM state WHERE id=1").fetchone()
    if not state or state[0] != "complete":
        raise SourceInventoryError("incomplete_inventory")
    actual = _hash_inventory(db)
    if actual.fingerprint != state[1]:
        raise SourceInventoryError("corrupt_inventory")
    if expected is not None and actual != expected:
        raise SourceInventoryError("source_changed")
    return actual


def inspect_inventory(path: Path | str, *, expected: InventorySummary | None = None) -> InventorySummary:
    """Read-only integrity/source comparison before a later migration stage."""
    path = Path(path)
    if not path.is_absolute():
        raise SourceInventoryError("path")
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("BEGIN")
            return _require_complete(db, expected)
    except (sqlite3.Error, OSError, ValueError, TypeError, KeyError):
        raise SourceInventoryError("inventory") from None


def iter_partitions(path: Path | str, *, expected: InventorySummary):
    """Yield verified counts, bytes and source/content digests; no content log."""
    path = Path(path)
    if not path.is_absolute():
        raise SourceInventoryError("path")
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("BEGIN")
            _require_complete(db, expected)
            descriptor = json.loads(db.execute("SELECT contract FROM state WHERE id=1").fetchone()[0])
            identity = IndexIdentity.from_dict(descriptor["source_identity"]) if descriptor["source_identity"] else None
            yield from _partitions(db, identity)
    except (sqlite3.Error, OSError, ValueError, TypeError, KeyError):
        raise SourceInventoryError("inventory") from None
```

- [x] **Step 4: Verify core and regressions**

```powershell
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pytest tests/memory/rag/test_source_inventory.py tests/memory/storage/test_qdrant_scan.py tests/memory/rag/test_json_index_cache.py -q --tb=short --junitxml=output/e3-a2-focused.xml
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pytest tests/memory -q --tb=short --junitxml=output/e3-a2-memory.xml
& 'D:/python_self_agent/venv/Scripts/python.exe' -m compileall -q hello_agents/memory/rag tests/memory/rag
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pip check
git diff --check
```

Expected: all applicable offline tests pass, no real service dependency, compile/pip/diff checks exit 0. Verify laziness, source change, EOF proof, duplicate/metadata/identity guards, unknown ownership, restart inspection and source immutability.

- [x] **Step 5: Complete handoff and final integration review**

Inspect all new source plus combined diff, record exact counts and residual controller requirements in the packet and final review. Update only E3-A2 core progress in the governing spec. Leave changes uncommitted.

## Implementation evidence

Accepted final evidence: 114 focused tests passed in 11.78s and 503 Memory/RAG tests passed in 15.25s (overlap); compile, dependency and diff checks pass. Final integration review accepts only this core. All changes remain in the isolated worktree, uncommitted/unpushed; no production source scan, activation or stable integration occurred.

Windows validation exposed that lstat and fstat can report different ctime meanings on Python 3.12. Cross-API identity checks now compare device/inode/size/mtime; each API's ctime is independently compared before/after. A deterministic regression simulates differing ctime values. Source change detection was retained, not disabled.
Additional tests cover actual embedded Qdrant, interruption/disk failure, UUID aliases across pages, multi-user same logical IDs, read-only inspection and model-independent content digests.

## Plan self-review

E3-A1 page prerequisite exists and is accepted. This unit preserves IDs, source metadata, ownership claims and exact content, produces complete/disk-backed per-partition evidence, rejects source changes/partial scans, and does not touch active state. Design sections 8–9 also require management ownership discovery, backup/lock, candidate resume, paired cutover recovery, quality and deep smoke; these remain separate sequential units, not lost acceptance criteria. The code plan and packet must be reviewed before implementation.
