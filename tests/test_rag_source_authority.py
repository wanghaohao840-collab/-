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
