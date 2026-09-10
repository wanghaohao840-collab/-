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


def iter_chunks(path: Path | str, *, expected: InventorySummary,
                require_authority: bool = False):
    """Yield verified source chunks in deterministic rebuild order."""
    path = Path(path)
    if not path.is_absolute() or type(require_authority) is not bool:
        raise SourceInventoryError("path")
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("BEGIN")
            _require_complete(db, expected)
            contract, authority = db.execute(
                "SELECT contract,authority_receipt FROM state WHERE id=1"
            ).fetchone()
            if require_authority and (
                not isinstance(authority, str)
                or not re.fullmatch(r"[0-9a-f]{64}", authority)
            ):
                raise SourceInventoryError("authority_receipt")
            descriptor = json.loads(contract)
            identity = IndexIdentity.from_dict(descriptor["source_identity"]) if descriptor["source_identity"] else None
            for record, saved_digest in db.execute(
                """SELECT record,digest FROM chunks
                ORDER BY namespace,document_id,chunk_index,chunk_id"""
            ):
                data = json.loads(record)
                if digest(data) != saved_digest:
                    raise SourceInventoryError("corrupt_inventory")
                yield validate_chunk(SourceChunk(**data), identity)
    except SourceInventoryError:
        raise
    except (sqlite3.Error, OSError, ValueError, TypeError, KeyError):
        raise SourceInventoryError("inventory") from None
