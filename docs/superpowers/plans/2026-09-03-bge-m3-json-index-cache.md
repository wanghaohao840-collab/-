# BGE-M3 Versioned JSON Index Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver E2-B2a: a strict, versioned and atomically replaced JSON index cache that cannot hide incompatible or corrupted vectors as an empty index.

**Architecture:** A focused cache codec consumes trusted E2-A identity and validates a complete flat JSON point envelope. Its path binds both namespace and profile fingerprint while leaving the legacy file untouched. Pipeline adoption follows in E2-B2b after this storage boundary is reviewed.

**Tech Stack:** Python, pathlib, JSON, SHA-256 path derivation, atomic `os.replace`, existing `IndexIdentity`, strict vector normalizer and `VectorPoint`, pytest. No new dependency.

## Global Constraints

- Governing BGE-M3 design and all E1/E2 identity, isolation and no-fallback rules remain binding.
- New cache schema is version 2; profile, namespace, count and every point are checked.
- Versioned path is derived from an explicit absolute legacy path plus namespace hash and fingerprint prefix; full identity remains in the envelope.
- Missing and corrupt files are errors. Only a successfully written schema-v2 envelope with zero chunks represents an empty managed index.
- Reject missing/wrong-size/non-finite/zero vectors; never pad, truncate or re-embed while loading.
- Point metadata must match outer ID, document, content, namespace and full fingerprint; reserved store IDs and duplicates are rejected.
- Old cache is never overwritten or removed. Failed replacement preserves the last complete version.
- No production file, index, config or container is touched. E2 is still incomplete.

---

### Task 1: Strict versioned JSON cache

**Files:**
- Create: `hello_agents/memory/rag/json_index_cache.py`
- Test: `tests/memory/rag/test_json_index_cache.py`

**Interfaces:**
- Produces `versioned_cache_path(legacy_path, identity, rag_namespace) -> Path`.
- Produces `JsonIndexCache(legacy_path, identity, rag_namespace)` with
  `.legacy_path`, `.path`, `.load() -> list[VectorPoint]`,
  and `.write(points: list[VectorPoint]) -> None`.
- Raises only safe `JsonIndexCacheError(code)` at persistence boundaries.
- Does not own an in-memory vector store and performs no RAG query.

- [x] **Step 1: Add the failing test file.**

```python
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

```

- [x] **Step 2: Run red.**

```powershell
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pytest tests/memory/rag/test_json_index_cache.py -q --basetemp=.pytest-tmp-bge-e2b2a-cache-red
```

Expected: missing `json_index_cache` module.

- [x] **Step 3: Add the complete cache implementation.**

```python
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from threading import RLock
import uuid

from hello_agents.memory.rag.embedding_profile import normalize_vector
from hello_agents.memory.rag.embedding_runtime import validate_texts
from hello_agents.memory.rag.errors import RAGConfigError, RAGEmbeddingError
from hello_agents.memory.rag.index_identity import (
    IndexIdentity, IndexIdentityError, require_point_identity,
)
from hello_agents.memory.storage.vector_store import VectorPoint

_DOCUMENT_FIELDS = {
    "schema_version", "identity", "rag_namespace",
    "updated_at", "chunk_count", "chunks",
}
_CHUNK_FIELDS = {"id", "document_id", "content", "vector", "metadata"}
_LOCK = RLock()


class JsonIndexCacheError(RAGConfigError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(f"RAG JSON index cache rejected: {code}")


def _safe_text(value: object, *, maximum: int) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and len(value) <= maximum
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
    )


def _require_timestamp(value: object) -> str:
    try:
        result = str(value)
        parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
        if (
            not isinstance(value, str)
            or not result.endswith("Z")
            or parsed.tzinfo is None
            or parsed.utcoffset() != timezone.utc.utcoffset(parsed)
        ):
            raise ValueError()
        return result
    except (TypeError, ValueError, OverflowError):
        raise JsonIndexCacheError("schema") from None


def versioned_cache_path(
    legacy_path: Path | str,
    identity: IndexIdentity,
    rag_namespace: str,
) -> Path:
    path = Path(legacy_path)
    if (
        not path.is_absolute()
        or not isinstance(identity, IndexIdentity)
        or identity.backend != "json"
    ):
        raise JsonIndexCacheError("path")
    if not _safe_text(rag_namespace, maximum=512):
        raise JsonIndexCacheError("namespace")
    path = path.resolve()
    namespace_hash = hashlib.sha256(rag_namespace.encode("utf-8")).hexdigest()[:12]
    suffix = path.suffix or ".json"
    stem = path.stem if path.suffix else path.name
    name = (
        f"{stem}__{namespace_hash}__"
        f"{identity.profile.fingerprint[:16]}{suffix}"
    )
    return path.with_name(name)


class JsonIndexCache:
    def __init__(
        self,
        legacy_path: Path | str,
        identity: IndexIdentity,
        rag_namespace: str,
    ):
        if identity.backend != "json":
            raise JsonIndexCacheError("backend")
        self.identity = identity
        self.rag_namespace = rag_namespace
        self.legacy_path = Path(legacy_path).resolve()
        self.path = versioned_cache_path(
            legacy_path, identity, rag_namespace
        )

    def load(self) -> list[VectorPoint]:
        with _LOCK:
            try:
                document = json.loads(self.path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                raise JsonIndexCacheError("missing") from None
            except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
                raise JsonIndexCacheError("unreadable") from None
            return self._decode(document)

    def write(self, points: list[VectorPoint]) -> None:
        with _LOCK:
            if self.path.exists():
                # Refuse to overwrite corruption or a full-fingerprint collision.
                self.load()
            chunks = self._encode_points(points)
            document = {
                "schema_version": 2,
                "identity": self.identity.to_dict(),
                "rag_namespace": self.rag_namespace,
                "updated_at": (
                    datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                ),
                "chunk_count": len(chunks),
                "chunks": chunks,
            }
            try:
                encoded = json.dumps(
                    document,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
            except (TypeError, ValueError, OverflowError, RecursionError):
                raise JsonIndexCacheError("point") from None
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.parent / (
                f".{self.path.name}.{uuid.uuid4().hex}.tmp"
            )
            try:
                with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
            except (OSError, UnicodeError):
                raise JsonIndexCacheError("write") from None
            finally:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def _decode(self, document: object) -> list[VectorPoint]:
        if (
            not isinstance(document, dict)
            or set(document) != _DOCUMENT_FIELDS
            or type(document.get("schema_version")) is not int
            or document["schema_version"] != 2
            or not isinstance(document.get("chunks"), list)
            or type(document.get("chunk_count")) is not int
            or document["chunk_count"] != len(document["chunks"])
            or document.get("rag_namespace") != self.rag_namespace
        ):
            raise JsonIndexCacheError("schema")
        _require_timestamp(document.get("updated_at"))
        try:
            stored_identity = IndexIdentity.from_dict(document["identity"])
            stored_identity.require_match(self.identity)
        except (IndexIdentityError, KeyError):
            raise JsonIndexCacheError("identity") from None
        points = []
        seen_ids: set[str] = set()
        seen_indexes: set[tuple[str, int]] = set()
        for chunk in document["chunks"]:
            point = self._decode_chunk(chunk)
            chunk_index = point.payload["chunk_index"]
            key = (point.payload["document_id"], chunk_index)
            if point.id in seen_ids or key in seen_indexes:
                raise JsonIndexCacheError("duplicate")
            seen_ids.add(point.id)
            seen_indexes.add(key)
            points.append(point)
        return points

    def _decode_chunk(self, chunk: object) -> VectorPoint:
        if not isinstance(chunk, dict) or set(chunk) != _CHUNK_FIELDS:
            raise JsonIndexCacheError("point")
        point_id = chunk.get("id")
        document_id = chunk.get("document_id")
        content = chunk.get("content")
        metadata = chunk.get("metadata")
        if (
            not _safe_text(point_id, maximum=512)
            or not _safe_text(document_id, maximum=512)
            or not isinstance(content, str)
            or not content.strip()
            or not isinstance(metadata, dict)
            or metadata.get("memory_id") != point_id
            or metadata.get("content") != content
            or "_vector_store_id" in metadata
            or type(metadata.get("chunk_index")) is not int
            or metadata["chunk_index"] < 0
            or type(metadata.get("document_version")) is not int
            or metadata["document_version"] < 1
        ):
            raise JsonIndexCacheError("point")
        try:
            validate_texts([content])
            require_point_identity(
                metadata,
                identity=self.identity,
                rag_namespace=self.rag_namespace,
                document_id=document_id,
            )
            vector = normalize_vector(
                chunk.get("vector"), self.identity.profile.dimension
            )
        except (IndexIdentityError, RAGConfigError, RAGEmbeddingError):
            raise JsonIndexCacheError("point") from None
        return VectorPoint(point_id, vector, dict(metadata))

    def _encode_points(self, points: object) -> list[dict[str, object]]:
        if not isinstance(points, list):
            raise JsonIndexCacheError("point")
        result = []
        seen_ids: set[str] = set()
        seen_indexes: set[tuple[str, int]] = set()
        for point in points:
            if not isinstance(point, VectorPoint):
                raise JsonIndexCacheError("point")
            decoded = self._decode_chunk({
                "id": point.id,
                "document_id": point.payload.get("document_id"),
                "content": point.payload.get("content"),
                "vector": point.vector,
                "metadata": point.payload,
            })
            key = (
                decoded.payload["document_id"],
                decoded.payload["chunk_index"],
            )
            if decoded.id in seen_ids or key in seen_indexes:
                raise JsonIndexCacheError("duplicate")
            seen_ids.add(decoded.id)
            seen_indexes.add(key)
            result.append({
                "id": decoded.id,
                "document_id": decoded.payload["document_id"],
                "content": decoded.payload["content"],
                "vector": decoded.vector,
                "metadata": decoded.payload,
            })
        return result

```

- [x] **Step 4: Run cache, registry and identity tests.**

```powershell
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pytest tests/memory/rag/test_json_index_cache.py tests/memory/rag/test_index_registry.py tests/memory/rag/test_index_identity.py -q --basetemp=.pytest-tmp-bge-e2b2a-cache-green
```

Expected: all tests pass; only explicit schema-v2 empty loads as empty.

- [x] **Step 5: Run storage/RAG regressions and commit.**

```powershell
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pytest tests/memory/storage tests/memory/rag -q --basetemp=.pytest-tmp-bge-e2b2a-regression
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pip check
git diff --check
git add -- hello_agents/memory/rag/json_index_cache.py tests/memory/rag/test_json_index_cache.py docs/superpowers/plans/2026-09-03-bge-m3-json-index-cache.md
git commit -m "feat(embedding): add strict versioned JSON index cache"
```

## Follow-on ownership

E2-B2b changes `SimpleRAGPipeline` managed mode to require an active registry record,
load/save only through `JsonIndexCache`, use `RAGEmbeddingRuntime` for both documents
and queries, and roll back memory if durable writes fail. It must preserve explicit
legacy simple behavior. E2-B2c performs equivalent guarded Qdrant adoption. E2-B3 wires
the explicit app data root and prevents namespace/cache reuse. E3 alone initializes,
rebuilds, validates and activates production state.

## Review and execution evidence

### Execution result (2026-09-03)

- Credible red: module missing. The first generated implementation contained two
  literal newline escapes; corrected before accepting any green result.
- Reviewed cache/registry/identity green: **67 passed**, 1.55 seconds.
- Storage and RAG regression: **329 passed**, 4 pre-existing Neo4j-driver
  destructor deprecation warnings, 76.58 seconds. No Neo4j container was started.
- Review ensures a cache refuses to overwrite corruption or a different full
  fingerprint sharing the shortened path, and validates the 6000-byte chunk limit.
- `compileall`, `pip check` and `git diff --check` passed. No production
  cache or legacy file was read, written or removed.

- [x] Scope split from the approved E2 matrix after E2-B1 passed.
- [x] Parsed both Python blocks with the project venv; syntax passed.
  Review added whole-text validation and a full-fingerprint collision guard.
- [x] Execute red/green and regressions in the existing isolated branch.
- [ ] Write E2-B2b against this reviewed codec.

At plan creation no cache file has been written outside pytest temporary directories.
