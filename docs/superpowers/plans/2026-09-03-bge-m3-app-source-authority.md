# BGE-M3 App Source Authority Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bind complete migration inventories to authoritative application account/history ownership without changing any source.

**Architecture:** Application-layer streaming discovery feeds the existing disk-backed RAG inventory. A second authority scan and pre-commit receipt bind acceptance to verified ownership; bounded JSON parsing is shared with the existing source reader.

**Tech Stack:** Python 3, SQLite, pathlib, pinned ijson==3.4.0.post0, pytest; existing stable venv.

## Global Constraints

- Preserve existing chunks, IDs, document versions and provenance; no original-file reparsing.
- Source ownership must be authoritative; unknown owners/corruption fail closed.
- No real data scan, credentials, remote embedding/LLM calls, running database services, source writes, production configuration/activation, Windows tasks or Neo4j changes.
- Existing isolated worktree D:/python_self_agent/.worktrees/bge-m3-runtime-identity, branch codex/bge-m3-runtime-identity, a33b071 plus accepted uncommitted E3-A1/A2.
- User selected serial inline execution. executing-plans skill is unavailable; use this plan's explicit test/review gates inline, without agent dispatch or another execution-choice prompt.
- No commit/push/merge. Preserve the prior dirty work and output/. Test data must be synthetic and temporary.
- Runtime APIs that create user directories, databases or default histories must not be called by discovery.

---
## Verified behavior and decisions

- app/auth.py stores UUID user IDs and status in app.db users. Read only id/status, never password_hash, usernames, QA tables or sessions.
- app/runtime.py derives RAG namespace pdf_{user_id}; app/storage.py locates users/{id}/history.json and rag/rag_cache.json. Calling get_or_create or ensure_user_dirs would mutate sources.
- History stores authoritative document IDs, not trustworthy chunk counts. Legacy migration documents may use 16-character IDs; do not require document UUIDs. Original document_path is provenance, not an ownership source or a file to reopen, and may name another container/host.
- All statuses participate (including disabled/future statuses); only canonical account UUIDs and nonblank status accepted. Account without any user directory is an explicit never-initialized account; existing user directory without history is an error, not guessed empty.
- Unknown users directories, missing/corrupt database, symlink/reparse paths/ancestors, hot journals/WAL/SHM, malformed histories and contradictory explicit user_id reject scanning.
- Stream every history section one item at a time, using the accepted 64 KiB/8 MiB input-budget, depth 32/node 100,000 and 2 MiB canonical value limits. No whole history or all-user owner list in RAM. Only ownership triples are emitted; private note/question content is discarded after validation and contributes only to the raw file hash.
- Strict duplicate document rejection for selected scope is delegated to the inventory's existing SQLite primary key; do not silently pick a duplicate history winner.
- Share the existing bounded parser by moving it to source_stream.py; keep aliases in source_json.py so tests/callers remain compatible, with no semantic parser changes.
- SQLite source opens mode=ro&immutable=1 only after rejecting ALL journal sidecars; current database connection uses the default rollback journal. This deliberately requires offline/checkpointed data. Check account database/path signatures before/after; complete authority rescan runs before inventory commit.
- Account/history/namespace receipt is bound into the inventory fingerprint, including users with zero documents and account statuses. History changes unrelated to RAG conservatively invalidate receipts.
- build_inventory gains optional verify_owners callback, run after source EOF and partition checks but before complete/commit. Require a lowercase SHA-256 receipt; callback failure leaves failed/noncomplete inventory. Close both source and ownership generators on failure.
- Add authority_receipt column and fingerprint domain source-inventory-v2 to the PRE-RELEASE E3 artifact, not application databases/caches. Existing E3-A2 temporary inventory artifacts need regeneration; do not transparently accept old unbound artifacts.
- build_app_inventory requires an already provisioned data_root/vector_indexes/rag/inventories directory and a NEW direct-child .sqlite artifact. No directory creation, overwrite or output in source cache. JSON source path must equal runtime legacy/versioned cache path for its account. Qdrant covers the entire explicit collection.
- This is a management library, not a production CLI: the subsequent controller must provision/check private ACLs, hold operations.lock, stop writers, select actual backend/profile/collection, enumerate all JSON caches, rescan source contents and perform backup/rebuild gates. Path checks and double scans do not replace maintenance locking or prevent malicious concurrent directory swaps.

## Interfaces

Consumes App data filesystem layout, SQLite users(id,status), JSON document_id, JsonChunkSource/QdrantChunkSource, versioned_cache_path and the accepted inventory core. No runtime service or auth API is needed.

Produces:
- AppOwnershipSource(data_root, *, namespace=None).records() yields (namespace, document_id, user_id); completed_token exists only after full successful enumeration.
- checked_path(value, *, directory=False, missing=False) returns guarded absolute Path or safe SourceInventoryError.
- build_app_inventory(data_root, path, *, source) -> InventorySummary.
- build_inventory(path, *, source, owners, verify_owners: Callable[[], str] | None = None); existing owners callers still work, authority-aware wrapper uses full revalidation before acceptance.
- Shared BoundedJSONReader/read_json_value retain existing parser limits and exceptions.

## Task 1: Authoritative ownership-bound source inventory

Files: create app/rag_authority.py, app/rag_inventory.py, hello_agents/memory/rag/source_stream.py, tests/test_rag_source_authority.py; update only source_json.py parser relocation and source_inventory.py finalization/receipt/generator closure. Records in this plan and same-name task-packets directory; governing embedding spec progress only.

- [x] Step 1: Add the exact test module below.
- [x] Step 2: Run missing-module red from isolated root:
```powershell
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pytest tests/test_rag_source_authority.py -q --tb=short
```
Expected collection failure: app.rag_authority missing.
- [x] Step 3: Apply the full module listings below, preserving prior E3 behavior outside described seams.
- [x] Step 4: Run focused and combined offline regression:
```powershell
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pytest tests/test_rag_source_authority.py tests/memory/rag/test_source_inventory.py tests/memory/storage/test_qdrant_scan.py tests/test_auth_service.py tests/test_history_repository.py tests/test_user_storage.py -q --tb=short --junitxml=output/e3-a3-focused.xml
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pytest tests/memory tests/test_auth_service.py tests/test_history_repository.py tests/test_user_storage.py tests/test_document_library_service.py tests/test_legacy_migration.py tests/test_legacy_migration_recovery.py tests/test_rag_source_authority.py -q --tb=short --junitxml=output/e3-a3-regression.xml
& 'D:/python_self_agent/venv/Scripts/python.exe' -m compileall -q app/rag_authority.py app/rag_inventory.py hello_agents/memory/rag/source_stream.py hello_agents/memory/rag/source_json.py hello_agents/memory/rag/source_inventory.py tests/test_rag_source_authority.py
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pip check
git diff --check
```
Expected all tests pass; compile/pip/diff checks exit 0. Actual counts are recorded only after execution.
- [x] Step 5: Record handoff done, inspect actual combined code and interfaces, create FINAL_INTEGRATION_REVIEW.md. If issues appear during final review, create corrective packet before implementation. No Git publication is authorized.

## Test module: tests/test_rag_source_authority.py

```python
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import uuid

import pytest

from app.rag_authority import AppOwnershipSource, checked_path
from app.rag_inventory import build_app_inventory
from hello_agents.memory.rag.source_inventory import build_inventory, inspect_inventory, iter_partitions
from hello_agents.memory.rag.source_json import JsonChunkSource
from hello_agents.memory.rag.source_qdrant import QdrantChunkSource
from hello_agents.memory.rag.source_records import SourceInventoryError
from hello_agents.memory.storage.vector_scan import VectorScanPage, VectorScanRecord


USER = "00000000-0000-0000-0000-000000000001"
OTHER = "00000000-0000-0000-0000-000000000002"
PRIVATE = "synthetic-private-password-or-note"
DOC = "51eacdef02991234"  # Old migration IDs are not UUIDs.


def write_history(root, user=USER, docs=None):
    path = root / "users" / user / "history.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"documents": docs if docs is not None else [
        {"document_id": DOC, "document_path": "/app/data/former-host/public.pdf"}],
        "questions": [{"answer": PRIVATE}], "notes": [{"content": PRIVATE}], "sessions": []}
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def fixture_root(tmp_path, *, initialized=True):
    root = tmp_path / "data"
    root.mkdir()
    with closing(sqlite3.connect(root / "app.db")) as db:
        db.execute("CREATE TABLE users(id TEXT PRIMARY KEY,status TEXT,password_hash TEXT)")
        db.execute("INSERT INTO users VALUES(?, 'active', ?)", (USER, PRIVATE))
        db.commit()
    (root / "vector_indexes" / "rag" / "inventories").mkdir(parents=True)
    if initialized:
        write_history(root)
    return root


def target(root, name="inventory"):
    return root / "vector_indexes" / "rag" / "inventories" / (name + ".sqlite")


def chunk(user=USER, doc=DOC):
    content = "A public document about learning."
    metadata = {"memory_id": f"{doc}_0", "document_id": doc, "content": content,
                "rag_namespace": f"pdf_{user}", "chunk_index": 0, "document_version": 1,
                "file_name": "public.pdf", "document_path": "/app/data/former-host/public.pdf"}
    return {"id": f"{doc}_0", "document_id": doc, "content": content,
            "vector": [1.0, 0.0], "metadata": metadata}


def cache(root, user=USER, rows=None):
    path = root / "users" / user / "rag" / "rag_cache.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [chunk(user)] if rows is None else rows
    data = {"collection_name": "source", "rag_namespace": f"pdf_{user}", "dimension": 2,
            "updated_at": "2026-09-03T00:00:00", "chunk_count": len(rows), "chunks": rows}
    path.write_text(json.dumps(data), encoding="utf-8")
    return JsonChunkSource(path, collection="source", namespace=f"pdf_{user}", dimension=2)


def account(root, user=OTHER, status="disabled"):
    with closing(sqlite3.connect(root / "app.db")) as db:
        db.execute("INSERT INTO users VALUES(?,?,?)", (user, status, PRIVATE))
        db.commit()


def assert_failed(path):
    with pytest.raises(SourceInventoryError):
        inspect_inventory(path)


def test_authority_preserves_legacy_ids_inactive_accounts_and_source_files(tmp_path):
    root = fixture_root(tmp_path)
    account(root)
    write_history(root, OTHER)
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    source = AppOwnershipSource(root)
    assert list(source.records()) == [(f"pdf_{USER}", DOC, USER), (f"pdf_{OTHER}", DOC, OTHER)]
    assert len(source.completed_token) == 64
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before
    assert not any(root.glob("app.db-*"))


def test_json_entry_binds_receipt_without_copying_password_notes_or_paths(tmp_path):
    root = fixture_root(tmp_path)
    source = cache(root)
    summary = build_app_inventory(root, target(root), source=source)
    assert summary.count == summary.partitions == 1
    assert inspect_inventory(target(root), expected=summary) == summary
    part, = iter_partitions(target(root), expected=summary)
    assert (part.user_id, part.document_id) == (USER, DOC)
    owners = AppOwnershipSource(root, namespace=f"pdf_{USER}")
    list(owners.records())
    with closing(sqlite3.connect(target(root))) as db:
        receipt, = db.execute("SELECT authority_receipt FROM state").fetchone()
        record, = db.execute("SELECT record FROM chunks").fetchone()
    assert receipt == owners.completed_token
    assert json.loads(record)["metadata"] == chunk()["metadata"]
    assert PRIVATE.encode() not in target(root).read_bytes()


def test_receipt_changes_when_account_status_changes_even_with_same_chunks(tmp_path):
    root = fixture_root(tmp_path)
    source = cache(root)
    first = build_app_inventory(root, target(root, "first"), source=source)
    with closing(sqlite3.connect(root / "app.db")) as db:
        db.execute("UPDATE users SET status='disabled'")
        db.commit()
    second = build_app_inventory(root, target(root, "second"), source=source)
    assert first.fingerprint != second.fingerprint
    assert list(iter_partitions(target(root, "first"), expected=first)) == list(
        iter_partitions(target(root, "second"), expected=second))


def test_registered_never_initialized_account_is_explicitly_empty(tmp_path):
    root = fixture_root(tmp_path, initialized=False)
    source = AppOwnershipSource(root)
    assert list(source.records()) == []
    assert source.completed_token
    assert not (root / "users").exists()


@pytest.mark.parametrize("fault", ["missing_db", "corrupt_db", "missing_history", "truncated_history",
    "missing_documents", "bad_root", "duplicate_key", "bad_document", "missing_id", "wrong_owner",
    "orphan_user", "non_uuid_user", "bad_status", "unknown_section", "wal", "shm", "journal"])
def test_authority_failures_are_closed_and_redacted(tmp_path, fault):
    root = fixture_root(tmp_path)
    history = root / "users" / USER / "history.json"
    if fault == "missing_db":
        (root / "app.db").unlink()
    elif fault == "corrupt_db":
        (root / "app.db").write_bytes(PRIVATE.encode())
    elif fault == "missing_history":
        history.unlink()
    elif fault == "truncated_history":
        history.write_text('{"documents":[', encoding="utf-8")
    elif fault == "missing_documents":
        history.write_text('{"notes":[]}', encoding="utf-8")
    elif fault == "bad_root":
        history.write_text("[]", encoding="utf-8")
    elif fault == "duplicate_key":
        history.write_text('{"documents":[],"documents":[]}', encoding="utf-8")
    elif fault == "bad_document":
        write_history(root, docs=[PRIVATE])
    elif fault == "missing_id":
        write_history(root, docs=[{}])
    elif fault == "wrong_owner":
        write_history(root, docs=[{"document_id": DOC, "user_id": OTHER}])
    elif fault == "orphan_user":
        write_history(root, OTHER)
    elif fault == "non_uuid_user":
        account(root, "../escape")
    elif fault == "bad_status":
        with closing(sqlite3.connect(root / "app.db")) as db:
            db.execute("UPDATE users SET status=NULL")
            db.commit()
    elif fault == "unknown_section":
        history.write_text('{"documents":[],"mystery":[]}', encoding="utf-8")
    else:
        (root / f"app.db-{fault}").write_text(PRIVATE, encoding="utf-8")
    source = AppOwnershipSource(root)
    with pytest.raises(SourceInventoryError) as caught:
        list(source.records())
    assert source.completed_token is None
    assert PRIVATE not in str(caught.value)
    assert str(root) not in str(caught.value)
    if fault == "missing_db":
        assert not (root / "app.db").exists()


@pytest.mark.parametrize("change", ["history", "account", "new_user"])
def test_authority_changes_during_scan_are_rejected(tmp_path, change):
    root = fixture_root(tmp_path)
    source = AppOwnershipSource(root)
    records = source.records()
    assert next(records)[1] == DOC
    if change == "history":
        write_history(root, docs=[])
    elif change == "account":
        account(root)
    else:
        write_history(root, OTHER)
    with pytest.raises(SourceInventoryError):
        list(records)
    assert source.completed_token is None
    assert not source._running


def test_closed_or_overlapping_scan_has_no_false_receipt(tmp_path):
    source = AppOwnershipSource(fixture_root(tmp_path))
    records = source.records()
    next(records)
    with pytest.raises(SourceInventoryError, match="authority_busy"):
        next(source.records())
    records.close()
    assert source.completed_token is None and not source._running
    assert len(list(source.records())) == 1
    assert source.completed_token


def test_other_namespace_history_is_checked_but_not_emitted(tmp_path):
    root = fixture_root(tmp_path)
    account(root)
    history = write_history(root, OTHER)
    source = cache(root)
    summary = build_app_inventory(root, target(root), source=source)
    assert summary.partitions == 1
    history.write_text("{broken", encoding="utf-8")
    with pytest.raises(SourceInventoryError):
        build_app_inventory(root, target(root, "bad"), source=source)
    assert_failed(target(root, "bad"))


@pytest.mark.parametrize("mutation", ["history", "account", "new_user"])
def test_change_after_owner_scan_prevents_inventory_completion(tmp_path, monkeypatch, mutation):
    root = fixture_root(tmp_path)
    source = cache(root)
    original = source.records
    def mutate_after_chunks():
        yield from original()
        if mutation == "history":
            write_history(root, docs=[])
        elif mutation == "account":
            account(root)
        else:
            write_history(root, OTHER)
    monkeypatch.setattr(source, "records", mutate_after_chunks)
    with pytest.raises(SourceInventoryError):
        build_app_inventory(root, target(root), source=source)
    assert_failed(target(root))


@pytest.mark.parametrize("fault", ["duplicate", "unknown_document", "missing_document"])
def test_inventory_reconciles_history_on_disk(tmp_path, fault):
    root = fixture_root(tmp_path)
    if fault == "duplicate":
        write_history(root, docs=[{"document_id": DOC}, {"document_id": DOC}])
    elif fault == "unknown_document":
        write_history(root, docs=[])
    else:
        write_history(root, docs=[{"document_id": DOC}, {"document_id": "another"}])
    source = cache(root)
    with pytest.raises(SourceInventoryError):
        build_app_inventory(root, target(root), source=source)
    assert_failed(target(root))


def test_destination_and_cache_path_are_confined_and_never_overwritten(tmp_path):
    root = fixture_root(tmp_path)
    source = cache(root)
    outside = tmp_path / "outside.sqlite"
    with pytest.raises(SourceInventoryError):
        build_app_inventory(root, outside, source=source)
    assert not outside.exists()
    target(root).write_bytes(b"keep")
    with pytest.raises(SourceInventoryError):
        build_app_inventory(root, target(root), source=source)
    assert target(root).read_bytes() == b"keep"
    copy = tmp_path / "copy.json"
    copy.write_bytes(source.path.read_bytes())
    source.path = copy
    with pytest.raises(SourceInventoryError, match="authority_source_path"):
        build_app_inventory(root, target(root, "copy"), source=source)
    assert not target(root, "copy").exists()


@pytest.mark.parametrize("component", ["data", "users", USER, "history.json"])
def test_reparse_ancestors_are_rejected_without_windows_symlink_privileges(tmp_path, monkeypatch, component):
    root = fixture_root(tmp_path)
    original = Path.lstat
    def fake(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        if path.name == component:
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
        return info
    monkeypatch.setattr(Path, "lstat", fake)
    with pytest.raises(SourceInventoryError):
        list(AppOwnershipSource(root).records())


def test_namespace_cannot_claim_unknown_account_even_for_empty_cache(tmp_path):
    root = fixture_root(tmp_path, initialized=False)
    source = cache(root, OTHER, rows=[])
    with pytest.raises(SourceInventoryError):
        build_app_inventory(root, target(root), source=source)
    assert_failed(target(root))


def test_qdrant_whole_collection_uses_all_account_owners(tmp_path):
    root = fixture_root(tmp_path)
    account(root)
    write_history(root, OTHER)
    rows = [chunk(USER), chunk(OTHER)]
    records = tuple(VectorScanRecord(str(uuid.UUID(int=i + 20)), row["id"], {
        "content": row["content"], "document_id": row["document_id"],
        "rag_namespace": row["metadata"]["rag_namespace"], "metadata": row["metadata"],
    }) for i, row in enumerate(rows))
    class Store:
        def require_collection(self, *args):
            pass
        def count(self, *args):
            return 2
        def iter_scroll_pages(self, *args, **kwargs):
            yield VectorScanPage(records, None)
    source = QdrantChunkSource(Store(), collection="source", dimension=2)
    result = build_app_inventory(root, target(root), source=source)
    assert result.count == result.partitions == 2
    assert {p.user_id for p in iter_partitions(target(root), expected=result)} == {USER, OTHER}


@pytest.mark.parametrize("receipt", [None, "bad", "A" * 64, 42])
def test_invalid_finalizer_receipt_never_marks_inventory_complete(tmp_path, receipt):
    root = fixture_root(tmp_path)
    source = cache(root)
    with pytest.raises(SourceInventoryError, match="authority_receipt"):
        build_inventory(target(root), source=source, owners=[(f"pdf_{USER}", DOC, USER)],
                        verify_owners=lambda: receipt)
    assert_failed(target(root))


def test_owner_generator_closes_when_storage_rejects_duplicate(tmp_path):
    root = fixture_root(tmp_path)
    source = cache(root)
    closed = []
    def owners():
        try:
            yield f"pdf_{USER}", DOC, USER
            yield f"pdf_{USER}", DOC, USER
        finally:
            closed.append(True)
    with pytest.raises(SourceInventoryError):
        build_inventory(target(root), source=source, owners=owners())
    assert closed == [True]


def test_managed_cache_uses_runtime_versioned_path(tmp_path):
    from hello_agents.memory.rag.embedding_runtime import build_rag_embedding
    from hello_agents.memory.rag.index_identity import IndexIdentity
    from hello_agents.memory.rag.json_index_cache import versioned_cache_path
    root = fixture_root(tmp_path)
    legacy = cache(root).path
    profile = build_rag_embedding({"RAG_EMBEDDING_PROVIDER": "simple"}, backend="json").profile
    identity = IndexIdentity("json", "source", profile)
    path = versioned_cache_path(legacy, identity, f"pdf_{USER}")
    row = chunk()
    row["vector"] = [1.0] + [0.0] * (profile.dimension - 1)
    row["metadata"]["embedding_fingerprint"] = profile.fingerprint
    path.write_text(json.dumps({"schema_version": 2, "identity": identity.to_dict(),
        "rag_namespace": f"pdf_{USER}", "updated_at": "2026-09-03T00:00:00Z",
        "chunk_count": 1, "chunks": [row]}), encoding="utf-8")
    source = JsonChunkSource(path, collection=identity.physical_collection,
        namespace=f"pdf_{USER}", dimension=profile.dimension, identity=identity)
    assert build_app_inventory(root, target(root), source=source).count == 1
    assert legacy.exists()


def test_large_history_is_streamed_with_bounded_reads(tmp_path, monkeypatch):
    from hello_agents.memory.rag.source_stream import BoundedJSONReader
    root = fixture_root(tmp_path)
    history = root / "users" / USER / "history.json"
    history.write_text(json.dumps({"documents": [{"document_id": DOC}],
        "notes": [{"content": "x" * 4096} for _ in range(600)]}), encoding="utf-8")
    sizes = []
    original = BoundedJSONReader.read
    def read(self, size):
        data = original(self, size)
        sizes.append(len(data))
        return data
    monkeypatch.setattr(BoundedJSONReader, "read", read)
    owners = AppOwnershipSource(root)
    assert len(list(owners.records())) == 1
    assert owners.completed_token and max(sizes) <= 65536 and len(sizes) > 30


def test_receipt_tampering_is_detected_by_readonly_inspection(tmp_path):
    root = fixture_root(tmp_path)
    result = build_app_inventory(root, target(root), source=cache(root))
    with closing(sqlite3.connect(target(root))) as db:
        db.execute("UPDATE state SET authority_receipt=?", ("0" * 64,))
        db.commit()
    with pytest.raises(SourceInventoryError, match="corrupt_inventory"):
        inspect_inventory(target(root), expected=result)


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows path alias semantics")
@pytest.mark.parametrize("name", ["other:inventory.sqlite", "trailing. ", "NUL.sqlite", "CON", "COM1"])
def test_windows_alias_and_device_paths_are_not_safe_missing_targets(tmp_path, name):
    with pytest.raises(SourceInventoryError, match="authority_path"):
        checked_path(tmp_path / name, missing=True)
```

## Implementation: app/rag_authority.py

```python
"""Read-only account/history authority for offline RAG migration management.

Never instantiate UserRuntimeRegistry, AuthService or HistoryRepository here:
their runtime entry points can initialize directories or default missing data.
The maintenance controller must stop writers; stat checks/rescans are additional
guards, not a cross-file transaction or a substitute for that controller.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import os
from pathlib import Path, PureWindowsPath
import sqlite3
import stat
import uuid

import ijson

from hello_agents.memory.rag.source_records import (
    SourceInventoryError, canonical, digest, require_name,
)
from hello_agents.memory.rag.source_stream import BoundedJSONReader, read_json_value


def checked_path(value: Path | str, *, directory=False, missing=False) -> Path:
    try:
        return _checked_path(value, directory=directory, missing=missing)
    except (OSError, ValueError, TypeError):
        raise SourceInventoryError("authority_path") from None


def _checked_path(value: Path | str, *, directory=False, missing=False) -> Path:
    """Require an absolute, normalized path with no link/reparse ancestors.

    Missing leaves are allowed only explicitly. This confines paths; it does
    not provision Windows ACLs or defeat malicious concurrent directory swaps.
    """
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise SourceInventoryError("authority_path")
    if os.name == "nt":
        reserved = getattr(os.path, "isreserved", lambda part: PureWindowsPath(part).is_reserved())
        if any(":" in part or part.rstrip(" .") != part or reserved(part) for part in path.parts[1:]):
            raise SourceInventoryError("authority_path")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            if missing:
                return path
            raise SourceInventoryError("authority_missing") from None
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise SourceInventoryError("authority_path")
        if current != path and not stat.S_ISDIR(info.st_mode):
            raise SourceInventoryError("authority_path")
    info = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(info.st_mode):
        raise SourceInventoryError("authority_path")
    return path


def _stamp(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _user_id(value):
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value:
            raise ValueError()
        return value
    except (ValueError, AttributeError):
        raise SourceInventoryError("authority_user") from None


def namespace_user(namespace):
    if not isinstance(namespace, str) or not namespace.startswith("pdf_"):
        raise SourceInventoryError("authority_namespace")
    return _user_id(namespace[4:])


class AppOwnershipSource:
    """Stream (namespace, document_id, user_id); receipt only at full EOF.

    All persisted user statuses participate, including inactive accounts.
    namespace limits emitted owners, NOT the account/history validation scan.
    Repeated historical document IDs are passed through; build_inventory rejects
    duplicates on disk for the selected scope rather than guessing which wins.
    Only document_id and optional user_id are ownership facts. Original document
    paths may refer to a former host/container; they are not reopened or used to
    infer ownership. Source chunks retain provenance independently.
    """

    def __init__(self, data_root: Path | str, *, namespace: str | None = None):
        self.root = Path(data_root)
        if not self.root.is_absolute():
            raise SourceInventoryError("authority_path")
        self.selected_user = namespace_user(namespace) if namespace is not None else None
        self.namespace = namespace
        self.completed_token = None
        self._running = False

    def _history(self, path, user_id, hasher):
        checked_path(path)
        before = _stamp(path)
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            # stat/fstat ctime have different meanings on some Windows/Python.
            if before[:4] != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns):
                raise SourceInventoryError("authority_changed")
            reader = BoundedJSONReader(stream)
            events = iter(ijson.basic_parse(reader, use_float=True, buf_size=65536))
            if next(events)[0] != "start_map":
                raise SourceInventoryError("authority_history")
            seen = set()
            while True:
                event, key = next(events)
                if event == "end_map":
                    break
                if (event != "map_key" or key in seen
                        or key not in {"documents", "questions", "notes", "sessions"}):
                    raise SourceInventoryError("authority_history")
                seen.add(key)
                if next(events)[0] != "start_array":
                    raise SourceInventoryError("authority_history")
                while True:
                    first = next(events)
                    if first[0] == "end_array":
                        break
                    item = read_json_value(events, first)
                    canonical(item)
                    reader.pending = 0
                    if key == "documents":
                        if not isinstance(item, dict):
                            raise SourceInventoryError("authority_document")
                        document_id = require_name(item.get("document_id"))
                        if "user_id" in item and item["user_id"] != user_id:
                            raise SourceInventoryError("authority_owner")
                        yield document_id
                    # Other history fields are validated one value at a time
                    # but never copied to the inventory or sent to providers.
                reader.pending = 0
            if "documents" not in seen or next(events, None) is not None:
                raise SourceInventoryError("authority_history")
            after = os.fstat(stream.fileno())
            if (_stamp(checked_path(path)) != before
                    or (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns)
                    != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
                raise SourceInventoryError("authority_changed")
            hasher.update(reader.hash.hexdigest().encode("ascii"))

    def _no_journal(self, path):
        # immutable=1 avoids SQLite creating/touching WAL shared-memory files.
        # It is safe here only for a quiescent, checkpointed source. Never
        # silently ignore a WAL or hot rollback journal.
        for suffix in ("-wal", "-shm", "-journal"):
            if _stamp(path.with_name(path.name + suffix)) is not None:
                raise SourceInventoryError("authority_database_busy")

    def _audit_users(self, db, users):
        checked_path(users, directory=True, missing=True)
        if not users.exists():
            return
        with os.scandir(users) as entries:
            for entry in entries:
                user_id = _user_id(entry.name)
                checked_path(Path(entry.path), directory=True)
                if not db.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
                    raise SourceInventoryError("authority_orphan")

    def records(self):
        if self._running:
            raise SourceInventoryError("authority_busy")
        self._running = True
        self.completed_token = None
        try:
            checked_path(self.root, directory=True)
            path = checked_path(self.root / "app.db")
            users = checked_path(self.root / "users", directory=True, missing=True)
            self._no_journal(path)
            before = _stamp(path)
            users_before = _stamp(users)
            hasher = hashlib.sha256(b"app-rag-ownership-v1")
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
                db.execute("PRAGMA query_only=ON")
                db.execute("PRAGMA temp_store=FILE")
                db.execute("PRAGMA cache_size=-2048")
                db.execute("BEGIN")
                if db.execute("SELECT type FROM sqlite_master WHERE name='users'").fetchone() != ("table",):
                    raise SourceInventoryError("authority_database")
                if self.selected_user is not None and not db.execute(
                    "SELECT 1 FROM users WHERE id=?", (self.selected_user,)
                ).fetchone():
                    raise SourceInventoryError("authority_owner")
                self._audit_users(db, users)
                for user_id, status in db.execute("SELECT id,status FROM users ORDER BY id"):
                    _user_id(user_id)
                    require_name(status)
                    user_root = checked_path(users / user_id, directory=True, missing=True)
                    exists = user_root.exists()
                    hasher.update(digest([user_id, status, exists]).encode("ascii"))
                    if not exists:
                        continue  # Registered account with no initialized data.
                    with closing(self._history(user_root / "history.json", user_id, hasher)) as history:
                        for document_id in history:
                            if self.selected_user is None or user_id == self.selected_user:
                                yield f"pdf_{user_id}", document_id, user_id
                self._audit_users(db, users)
                self._no_journal(path)
                if _stamp(checked_path(path)) != before or _stamp(users) != users_before:
                    raise SourceInventoryError("authority_changed")
            self.completed_token = hasher.hexdigest()
        except SourceInventoryError:
            raise
        except (OSError, sqlite3.Error, UnicodeError, ValueError, TypeError,
                OverflowError, RecursionError, StopIteration, ijson.JSONError):
            raise SourceInventoryError("authority_unreadable") from None
        finally:
            self._running = False
```

## Implementation: app/rag_inventory.py

```python
"""Application-authorized source inventory; no CLI or production activation."""
from pathlib import Path

from app.rag_authority import AppOwnershipSource, checked_path, namespace_user
from hello_agents.memory.rag.json_index_cache import versioned_cache_path
from hello_agents.memory.rag.source_inventory import InventorySummary, build_inventory
from hello_agents.memory.rag.source_json import JsonChunkSource
from hello_agents.memory.rag.source_qdrant import QdrantChunkSource
from hello_agents.memory.rag.source_records import SourceInventoryError


def build_app_inventory(data_root: Path | str, path: Path | str, *, source) -> InventorySummary:
    """Bind a complete source inventory to two matching authority scans.

    Caller must hold the deployment maintenance lock, stop writers and prepare
    a private inventories directory/ACL. This library confines paths but does
    not authorize a live scan or provision deployment permissions.
    JSON covers one explicit user cache; Qdrant covers the whole source
    collection. The later controller must enumerate ALL JSON user caches.
    """
    root = checked_path(data_root, directory=True)
    path = Path(path)
    parent = checked_path(root / "vector_indexes" / "rag" / "inventories", directory=True)
    if not path.is_absolute() or path.parent != parent or path.suffix != ".sqlite":
        raise SourceInventoryError("inventory_destination")
    checked_path(path, missing=True)
    if path.exists():
        raise SourceInventoryError("destination")
    if not isinstance(source, (JsonChunkSource, QdrantChunkSource)):
        raise SourceInventoryError("source")
    namespace = source.namespace if isinstance(source, JsonChunkSource) else None

    def check_source_path():
        if isinstance(source, JsonChunkSource):
            user_id = namespace_user(namespace)
            legacy = root / "users" / user_id / "rag" / "rag_cache.json"
            checked_path(legacy.parent, directory=True)
            expected = versioned_cache_path(legacy, source.identity, namespace) if source.identity else legacy
            if checked_path(source.path) != expected:
                raise SourceInventoryError("authority_source_path")

    check_source_path()
    owners = AppOwnershipSource(root, namespace=namespace)

    def verify_owners():
        if owners.completed_token is None:
            raise SourceInventoryError("incomplete_authority")
        check_source_path()
        checked_path(parent, directory=True)
        probe = AppOwnershipSource(root, namespace=namespace)
        for _ in probe.records():
            pass
        if probe.completed_token != owners.completed_token:
            raise SourceInventoryError("authority_changed")
        return owners.completed_token

    return build_inventory(path, source=source, owners=owners.records(), verify_owners=verify_owners)
```

## Implementation: hello_agents/memory/rag/source_stream.py

```python
"""Bounded JSON event values shared by migration source readers."""
from __future__ import annotations

import hashlib

from hello_agents.memory.rag.source_records import SourceInventoryError

_BUFFER = 64 * 1024
_MAX_VALUE_BYTES = 8 * 1024 * 1024


class BoundedJSONReader:
    def __init__(self, stream, *, max_value_bytes=_MAX_VALUE_BYTES):
        if type(max_value_bytes) is not int or not 1 <= max_value_bytes <= _MAX_VALUE_BYTES:
            raise SourceInventoryError("value_size")
        self.max_value_bytes = max_value_bytes
        self.stream = stream
        self.hash = hashlib.sha256()
        self.pending = 0

    def read(self, size):
        if size == 0:
            return b""
        data = self.stream.read(min(size, _BUFFER) if size > 0 else _BUFFER)
        self.pending += len(data)
        if self.pending > self.max_value_bytes:
            raise SourceInventoryError("value_size")
        self.hash.update(data)
        return data


def read_json_value(events, first, depth=0, budget=None):
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
            result[key] = read_json_value(events, next(events), depth + 1, budget)
    if event == "start_array":
        result = []
        while True:
            item = next(events)
            if item[0] == "end_array":
                return result
            result.append(read_json_value(events, item, depth + 1, budget))
    if event in {"null", "boolean", "number", "string", "integer", "double"}:
        return value
    raise SourceInventoryError("json")

```

## Implementation: hello_agents/memory/rag/source_json.py

```python
"""Bounded streaming reader for legacy and managed JSON RAG caches."""
from __future__ import annotations

from datetime import datetime
import math
import os
from pathlib import Path
import stat

import ijson

from hello_agents.memory.rag.index_identity import IndexIdentity, IndexIdentityError
from hello_agents.memory.rag.source_records import (
    SourceInventoryError, canonical, json_chunk, require_name,
)

from hello_agents.memory.rag.source_stream import (
    BoundedJSONReader as _Reader, read_json_value as _value,
)

_BUFFER = 64 * 1024
_MAX_VALUE_BYTES = 8 * 1024 * 1024
_LEGACY = {"collection_name", "rag_namespace", "dimension", "updated_at", "chunk_count"}
_MANAGED = {"schema_version", "identity", "rag_namespace", "updated_at", "chunk_count"}


def _signature(info):
    # Windows/Python can expose different ctime meanings via stat vs fstat.
    # Compare identity across APIs, but ctime only against the same API later.
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


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
                reader = _Reader(stream, max_value_bytes=_MAX_VALUE_BYTES)
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

## Implementation: hello_agents/memory/rag/source_inventory.py

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
import re
from typing import Callable
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
                    contract TEXT NOT NULL, receipt TEXT, fingerprint TEXT, authority_receipt TEXT);
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
    contract, receipt, authority = db.execute("SELECT contract,receipt,authority_receipt FROM state WHERE id=1").fetchone()
    if authority is not None and (not isinstance(authority, str) or not re.fullmatch(r"[0-9a-f]{64}", authority)):
        raise SourceInventoryError("authority_receipt")
    descriptor = json.loads(contract)
    identity = IndexIdentity.from_dict(descriptor["source_identity"]) if descriptor["source_identity"] else None
    hasher = hashlib.sha256(canonical(["source-inventory-v2", descriptor, receipt, authority]).encode())
    count, partitions = 0, 0
    for part in _partitions(db, identity):
        hasher.update(digest(asdict(part)).encode("ascii"))
        count += part.count
        partitions += 1
    return InventorySummary(count, partitions, hasher.hexdigest())


def build_inventory(path: Path | str, *, source, owners,
                    verify_owners: Callable[[], str] | None = None) -> InventorySummary:
    """Create a new inventory from a *complete* source scan.

    owners yields (namespace, document_id, user_id), supplied by the management
    layer from authoritative records, never inferred from the source payload.
    Each listed document must have chunks. Empty deployment is owners=[] and
    an explicitly successful empty source, not a missing source.
    Consume under the maintenance controller; rescan and compare before reuse.
    Optional verify_owners runs before completion; its SHA-256 receipt binds the
    authority scan into the fingerprint. It must recheck authority, not infer it.
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
    owner_iterator = iter(owners)
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
        db.execute("INSERT INTO state VALUES(1,'building',?,NULL,NULL,NULL)", (encoded_contract,))
        db.commit()
        for namespace, document_id, user_id in owner_iterator:
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
        if verify_owners is not None:
            authority = verify_owners()
            if not isinstance(authority, str) or not re.fullmatch(r"[0-9a-f]{64}", authority):
                raise SourceInventoryError("authority_receipt")
            db.execute("UPDATE state SET authority_receipt=? WHERE id=1", (authority,))
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
        for resource in (iterator, owner_iterator):
            close = getattr(resource, "close", None)
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

## Plan self-review

Checked current schemas, ID history, runtime cache naming, immutable SQLite sidecar precondition, private-data minimization, exact callback/iterator contracts and one-owned-packet scope. Detailed implementation/test listings contain no placeholders. Baseline: 71 tests passed in 14.85s (auth/history/storage/inventory). Approved E3 design remains governing; rebuild/checkpoint/maintenance/cutover/quality/live deep-smoke and stable integration are not claimed by this increment.

## Reality-conflict report and bounded resolution

- Packet: bge-m3-app-source-authority-01; temporarily blocked during first regression.
- Expected: parser relocation retains existing private test seams with no behavior change.
- Observed: tests/memory/rag/test_source_inventory.py:260 patches source_json._MAX_VALUE_BYTES; moved parser no longer exposes that module constant. First run: 162 passed, 1 failed.
- Impact: old parser-limit regression cannot exercise the preserved source entry.
- Work completed: planned library/code/tests applied; new authority tests pass.
- Resolution: keep source_json._MAX_VALUE_BYTES and pass it explicitly to BoundedJSONReader's bounded optional max_value_bytes argument. Default remains 8 MiB, adjustable only downward; alias remains the same shared class. No old tests or unrelated files changed.
- Decision: Codex plan review accepts this within the existing owned-file compatibility requirement. Resume inline after revising plan/review; no new product choice/authority required.

## Windows path acceptance refinement

Five added red tests prove missing-leaf lstat checks alone do not reject ADS, trailing-dot/space aliases and NUL/CON/COM1 device paths. Require lexical Windows normalization before ancestor inspection, using native os.path.isreserved when available and PureWindowsPath compatibility fallback. This implements the original normalized-path requirement within app/rag_authority.py and its test module. Linux names/semantics remain unchanged. No device file/ADS was opened by tests.

## Execution and final integration result

Completed serially in the existing isolated worktree. Packet bge-m3-app-source-authority-01 is done; FINAL_INTEGRATION_REVIEW.md accepted actual combined changes. Added 52 tests. Final focused 168 passed (16.28s); combined regression 647 passed (44.60s), groups overlap, no failures/errors/skips. Compile/pip/diff checks pass. No real source scans, live service calls, production activation or Git publication. Mandatory release/controller/candidate gates remain listed in the final review and governing spec.

## Corrective packet 02

Final import audit found source_json.py retained hashlib after its reader implementation moved into source_stream.py. Reviewed corrective packet 02 removes exactly that unused import and updates the listing above; all interfaces and parser behavior remain unchanged. Acceptance requires the same final regression commands.

Packet 02 completed and re-reviewed: final focused 168 passed in 16.36s, combined 647 passed in 46.57s, no failures/errors/skips; compile/pip/diff checks pass. Both packets are done and final integration result is accepted. No production/Git publication occurred.
