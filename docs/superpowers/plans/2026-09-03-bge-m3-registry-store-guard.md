# BGE-M3 Persistent Registry and Read-Only Store Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver E2-B1: a durable, fail-closed index registry under the explicit application data root and non-creating physical collection validation.

**Architecture:** Build a strict registry around the E2-A `IndexIdentity` DTO, with typed validation evidence and atomic replacement. Extend the vector-store boundary with `require_collection`, distinct from the existing creating `ensure_collection`. Pipelines are not switched in this substage; E2-B2 will consume both reviewed interfaces.

**Tech Stack:** Python, dataclasses, pathlib, JSON, atomic `os.replace`, existing RAG errors/identity contracts, pytest and in-memory/fake Qdrant stores. No new dependency.

## Global Constraints

- The approved `docs/superpowers/specs/2026-09-03-bge-m3-rag-embedding-design.md` remains governing; E2-B1 does not complete E2.
- Registry path is exactly `<explicit data_root>/vector_indexes/rag/registry.json`; never derive it from cwd, worktree or a user's cache directory.
- Registry stores full profile/identity, physical collection, source index, migration ID, validation evidence and lifecycle state. It never stores API keys.
- Lifecycle values are `planned|building|validated|active|failed`; `validated` and `active` require passing validation with zero scope leaks.
- Structural deserialization is not trust. App paths must compare a record to an identity derived from current trusted runtime configuration.
- Missing, malformed, incomplete, inactive or mismatched registry state fails explicitly; it is not interpreted as an empty index.
- `require_collection` must only inspect. It never creates a collection or payload index.
- No production config, registry, Qdrant collection, containers or business data change in this substage.
- Continue serial execution in `D:/python_self_agent/.worktrees/bge-m3-runtime-identity`; use stable venv and scoped commits.

---

## Scope boundary and file map

| Task | Files | Responsibility |
| --- | --- | --- |
| 1 | `hello_agents/memory/rag/index_registry.py`; `tests/memory/rag/test_index_registry.py` | Durable typed registry and active-identity gate |
| 2 | `hello_agents/memory/storage/vector_store.py`; `tests/memory/storage/test_vector_store_contract.py` | Non-creating dimension/distance validation |

E2-B2 will inject the runtime and registry into JSON/Qdrant pipelines, implement versioned
JSON envelopes and protect every read/write. E2-B3 will wire the explicit app data root,
per-user caches and error semantics. E3 remains responsible for inventory, bounded
rebuild, quality validation, cutover journal and rollback.

### Task 1: Durable typed registry

**Files:**
- Create: `hello_agents/memory/rag/index_registry.py`
- Test: `tests/memory/rag/test_index_registry.py`

**Interfaces:**
- Consumes `IndexIdentity.to_dict/from_dict/require_match`.
- Produces `IndexValidation`, `IndexRecord`, `IndexRegistry(data_root)`,
  `load() -> dict[str, IndexRecord]`, `save(Iterable[IndexRecord]) -> None`,
  `put(IndexRecord) -> None`, and `require_active(IndexIdentity) -> IndexRecord`.
- All boundary failures use safe `IndexRegistryError.code`; messages do not echo data.

- [x] **Step 1: Add the failing registry tests.**

```python
import json
from dataclasses import replace
from pathlib import Path

import pytest

import hello_agents.memory.rag.index_registry as registry_module
from hello_agents.memory.rag.embedding_runtime import build_rag_embedding
from hello_agents.memory.rag.index_identity import IndexIdentity
from hello_agents.memory.rag.index_registry import (
    IndexRecord, IndexRegistry, IndexRegistryError, IndexValidation,
)


def identity(*, revision="siliconflow-bge-m3-v1"):
    runtime = build_rag_embedding(
        {
            "RAG_EMBEDDING_PROVIDER": "siliconflow",
            "RAG_EMBEDDING_API_KEY": "fake-private-key",
            "RAG_EMBEDDING_REVISION": revision,
        },
        backend="qdrant",
    )
    return IndexIdentity("qdrant", "doc_learning_vectors", runtime.profile)


def validation(*, passed=True, scope_leaks=0):
    return IndexValidation(
        passed=passed,
        checked_at="2026-09-03T12:00:00Z",
        chunk_count=3,
        content_digest="a" * 64,
        scope_leaks=scope_leaks,
        recall_at_5=0.95,
        mrr_at_5=0.80,
    )


_DEFAULT_VALIDATION = object()


def record(*, expected=None, state="active", checked=_DEFAULT_VALIDATION):
    return IndexRecord(
        identity=expected or identity(),
        source_index="doc_learning_vectors",
        migration_id="migration-20260903",
        validation=validation() if checked is _DEFAULT_VALIDATION else checked,
        state=state,
    )


def test_registry_uses_explicit_app_data_root_and_round_trips_without_key(tmp_path):
    registry = IndexRegistry(tmp_path)
    expected = record()

    registry.save([expected])

    assert registry.path == (
        tmp_path / "vector_indexes" / "rag" / "registry.json"
    ).resolve()
    assert registry.require_active(expected.identity) == expected
    raw = registry.path.read_text(encoding="utf-8")
    assert "fake-private-key" not in raw
    assert json.loads(raw)["schema_version"] == 1


def test_registry_does_not_follow_working_directory(tmp_path, monkeypatch):
    registry = IndexRegistry(tmp_path / "data")
    expected = record()
    registry.save([expected])
    another = tmp_path / "another"
    another.mkdir()
    monkeypatch.chdir(another)

    assert registry.require_active(expected.identity) == expected
    assert not (another / "vector_indexes").exists()


@pytest.mark.parametrize("payload", [
    "{broken",
    json.dumps({"schema_version": 1, "entries": [], "api_key": "private-body"}),
    json.dumps({"schema_version": True, "entries": {}}),
])
def test_missing_or_corrupt_registry_fails_closed_without_echoing_content(
    tmp_path, payload
):
    registry = IndexRegistry(tmp_path)
    registry.registry_root.mkdir(parents=True)
    registry.path.write_text(payload, encoding="utf-8")

    with pytest.raises(IndexRegistryError) as caught:
        registry.load()
    assert "private-body" not in str(caught.value)


def test_missing_active_registry_and_same_dimension_different_model_fail(tmp_path):
    registry = IndexRegistry(tmp_path)
    with pytest.raises(IndexRegistryError, match="missing"):
        registry.require_active(identity())

    expected = record()
    registry.save([expected])
    changed = replace(
        expected.identity,
        profile=replace(expected.identity.profile, revision="different-v2"),
    )
    with pytest.raises(IndexRegistryError):
        registry.require_active(changed)


@pytest.mark.parametrize(("state", "checked"), [
    ("active", None),
    ("validated", IndexValidation(
        passed=False,
        checked_at="2026-09-03T12:00:00Z",
        chunk_count=3,
        content_digest="a" * 64,
        scope_leaks=0,
    )),
    ("active", IndexValidation(
        passed=True,
        checked_at="2026-09-03T12:00:00Z",
        chunk_count=3,
        content_digest="a" * 64,
        scope_leaks=1,
    )),
])
def test_validated_or_active_records_require_passing_leak_free_validation(
    state, checked
):
    with pytest.raises(IndexRegistryError, match="unvalidated"):
        record(state=state, checked=checked)


def test_failed_atomic_replace_preserves_previous_registry(tmp_path, monkeypatch):
    registry = IndexRegistry(tmp_path)
    original = record()
    registry.save([original])
    before = registry.path.read_bytes()

    def fail_replace(source, destination):
        raise OSError("forced failure")

    monkeypatch.setattr(registry_module.os, "replace", fail_replace)
    with pytest.raises(IndexRegistryError, match="write"):
        registry.put(record(expected=identity(revision="v2"), state="planned",
                           checked=None))

    assert registry.path.read_bytes() == before
    assert list(registry.registry_root.glob("*.tmp")) == []


def test_relative_data_root_and_unknown_fields_are_rejected(tmp_path):
    with pytest.raises(IndexRegistryError, match="data_root"):
        IndexRegistry(Path("relative-data"))

    registry = IndexRegistry(tmp_path)
    raw = record().to_dict()
    raw["api_key"] = "private-body"
    registry.registry_root.mkdir(parents=True)
    registry.path.write_text(json.dumps({
        "schema_version": 1,
        "entries": {"qdrant:doc_learning_vectors": raw},
    }), encoding="utf-8")
    with pytest.raises(IndexRegistryError) as caught:
        registry.load()
    assert "private-body" not in str(caught.value)

```

- [x] **Step 2: Run red.**

```powershell
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pytest tests/memory/rag/test_index_registry.py -q --basetemp=.pytest-tmp-bge-e2b1-registry-red
```

Expected: collection fails because `index_registry` does not exist.

- [x] **Step 3: Add the complete registry implementation.**

```python
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
from threading import RLock
from typing import Iterable
import uuid

from hello_agents.memory.rag.errors import RAGConfigError
from hello_agents.memory.rag.index_identity import (
    IndexIdentity, IndexIdentityError,
)

_RECORD_FIELDS = {
    "identity", "source_index", "migration_id", "validation", "state",
}
_VALIDATION_FIELDS = {
    "passed", "checked_at", "chunk_count", "content_digest",
    "scope_leaks", "recall_at_5", "mrr_at_5",
}
_DOCUMENT_FIELDS = {"schema_version", "entries"}
_STATES = {"planned", "building", "validated", "active", "failed"}
_MIGRATION_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")
_HEX_DIGEST = re.compile(r"[a-f0-9]{64}")
_LOCK = RLock()


class IndexRegistryError(RAGConfigError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(f"RAG index registry rejected: {code}")


def _safe_text(value: object, *, maximum: int, empty: bool = False) -> bool:
    return (
        isinstance(value, str)
        and (empty or bool(value))
        and len(value) <= maximum
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
    )


def _score(value: object) -> float | None:
    if value is None:
        return None
    if type(value) not in (int, float):
        raise IndexRegistryError("validation")
    result = float(value)
    if not math.isfinite(result) or not 0 <= result <= 1:
        raise IndexRegistryError("validation")
    return result


@dataclass(frozen=True)
class IndexValidation:
    passed: bool
    checked_at: str
    chunk_count: int
    content_digest: str
    scope_leaks: int
    recall_at_5: float | None = None
    mrr_at_5: float | None = None

    def __post_init__(self) -> None:
        if (
            type(self.passed) is not bool
            or not _safe_text(self.checked_at, maximum=64)
            or type(self.chunk_count) is not int
            or self.chunk_count < 0
            or not isinstance(self.content_digest, str)
            or not _HEX_DIGEST.fullmatch(self.content_digest)
            or type(self.scope_leaks) is not int
            or self.scope_leaks < 0
        ):
            raise IndexRegistryError("validation")
        object.__setattr__(self, "recall_at_5", _score(self.recall_at_5))
        object.__setattr__(self, "mrr_at_5", _score(self.mrr_at_5))

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "checked_at": self.checked_at,
            "chunk_count": self.chunk_count,
            "content_digest": self.content_digest,
            "scope_leaks": self.scope_leaks,
            "recall_at_5": self.recall_at_5,
            "mrr_at_5": self.mrr_at_5,
        }

    @classmethod
    def from_dict(cls, value: object) -> "IndexValidation":
        try:
            if not isinstance(value, dict) or set(value) != _VALIDATION_FIELDS:
                raise IndexRegistryError("validation")
            return cls(**value)
        except (TypeError, ValueError, KeyError):
            raise IndexRegistryError("validation") from None


@dataclass(frozen=True)
class IndexRecord:
    identity: IndexIdentity
    source_index: str | None
    migration_id: str
    validation: IndexValidation | None
    state: str

    def __post_init__(self) -> None:
        if not isinstance(self.identity, IndexIdentity):
            raise IndexRegistryError("record")
        if (
            self.source_index is not None
            and not _safe_text(self.source_index, maximum=512)
        ):
            raise IndexRegistryError("record")
        if (
            not isinstance(self.migration_id, str)
            or not _MIGRATION_ID.fullmatch(self.migration_id)
            or self.state not in _STATES
            or (
                self.validation is not None
                and not isinstance(self.validation, IndexValidation)
            )
        ):
            raise IndexRegistryError("record")
        if self.state in {"validated", "active"} and (
            self.validation is None
            or not self.validation.passed
            or self.validation.scope_leaks != 0
        ):
            raise IndexRegistryError("unvalidated")

    def to_dict(self) -> dict[str, object]:
        return {
            "identity": self.identity.to_dict(),
            "source_index": self.source_index,
            "migration_id": self.migration_id,
            "validation": (
                None if self.validation is None else self.validation.to_dict()
            ),
            "state": self.state,
        }

    @classmethod
    def from_dict(cls, value: object) -> "IndexRecord":
        try:
            if not isinstance(value, dict) or set(value) != _RECORD_FIELDS:
                raise IndexRegistryError("record")
            validation = value["validation"]
            return cls(
                identity=IndexIdentity.from_dict(value["identity"]),
                source_index=value["source_index"],
                migration_id=value["migration_id"],
                validation=(
                    None if validation is None
                    else IndexValidation.from_dict(validation)
                ),
                state=value["state"],
            )
        except (TypeError, ValueError, KeyError, IndexIdentityError):
            raise IndexRegistryError("record") from None


def registry_key(identity: IndexIdentity) -> str:
    return f"{identity.backend}:{identity.base_collection}"


class IndexRegistry:
    def __init__(self, data_root: Path | str):
        root = Path(data_root)
        if not root.is_absolute():
            raise IndexRegistryError("data_root")
        self.root = root.resolve()
        self.registry_root = self.root / "vector_indexes" / "rag"
        self.path = self.registry_root / "registry.json"

    def load(self) -> dict[str, IndexRecord]:
        with _LOCK:
            try:
                value = json.loads(self.path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                raise IndexRegistryError("missing") from None
            except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
                raise IndexRegistryError("unreadable") from None
            return self._decode(value)

    def save(self, records: Iterable[IndexRecord]) -> None:
        with _LOCK:
            entries: dict[str, IndexRecord] = {}
            try:
                for record in records:
                    if not isinstance(record, IndexRecord):
                        raise IndexRegistryError("record")
                    key = registry_key(record.identity)
                    if key in entries:
                        raise IndexRegistryError("duplicate")
                    entries[key] = record
            except TypeError:
                raise IndexRegistryError("record") from None
            self._save_unlocked(entries)

    def put(self, record: IndexRecord) -> None:
        if not isinstance(record, IndexRecord):
            raise IndexRegistryError("record")
        with _LOCK:
            entries = self.load() if self.path.exists() else {}
            entries[registry_key(record.identity)] = record
            self._save_unlocked(entries)

    def require_active(self, expected: IndexIdentity) -> IndexRecord:
        if not isinstance(expected, IndexIdentity):
            raise IndexRegistryError("identity")
        record = self.load().get(registry_key(expected))
        if record is None:
            raise IndexRegistryError("entry_missing")
        if record.state != "active":
            raise IndexRegistryError("inactive")
        try:
            record.identity.require_match(expected)
        except IndexIdentityError:
            raise IndexRegistryError("identity") from None
        return record

    @staticmethod
    def _decode(value: object) -> dict[str, IndexRecord]:
        if (
            not isinstance(value, dict)
            or set(value) != _DOCUMENT_FIELDS
            or type(value.get("schema_version")) is not int
            or value["schema_version"] != 1
            or not isinstance(value.get("entries"), dict)
        ):
            raise IndexRegistryError("schema")
        result: dict[str, IndexRecord] = {}
        for key, raw_record in value["entries"].items():
            record = IndexRecord.from_dict(raw_record)
            if key != registry_key(record.identity) or key in result:
                raise IndexRegistryError("schema")
            result[key] = record
        return result

    def _save_unlocked(self, records: dict[str, IndexRecord]) -> None:
        document = {
            "schema_version": 1,
            "entries": {
                key: records[key].to_dict() for key in sorted(records)
            },
        }
        encoded = json.dumps(
            document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        self.registry_root.mkdir(parents=True, exist_ok=True)
        temporary = self.registry_root / (
            f".{self.path.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except (OSError, UnicodeError):
            raise IndexRegistryError("write") from None
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

```

- [x] **Step 4: Run registry plus E2-A identity/runtime tests.**

```powershell
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pytest tests/memory/rag/test_index_registry.py tests/memory/rag/test_index_identity.py tests/memory/rag/test_embedding_runtime.py -q --basetemp=.pytest-tmp-bge-e2b1-registry-green
```

Expected: all pass; cwd independence, exact matching, corruption rejection, secret
exclusion and failed-replace preservation are demonstrated.

- [x] **Step 5: Commit the first interface.**

```powershell
git diff --check
git add -- hello_agents/memory/rag/index_registry.py tests/memory/rag/test_index_registry.py docs/superpowers/plans/2026-09-03-bge-m3-registry-store-guard.md
git commit -m "feat(embedding): add durable RAG index registry"
```

### Task 2: Non-creating physical collection validation

**Files:**
- Modify: `hello_agents/memory/storage/vector_store.py`
- Modify/Test: `tests/memory/storage/test_vector_store_contract.py`

**Interfaces:**
- Extends `VectorStore` with `require_collection(collection_name: str,
  dimension: int, distance: str = "Cosine") -> None`.
- `InMemoryVectorStore` records distance as well as dimension.
- `QdrantVectorStore.require_collection` calls only `collection_exists` and,
  when true, `get_collection`; it reuses `_validate_collection`.
- Existing `ensure_collection` keeps its explicit create-if-missing behavior for
  tests and the future admin initializer. E2-B2 must use `require_collection`
  for managed active startup.

- [x] **Step 1: Extend the existing contract test imports and append these failing tests.**

```python
from types import SimpleNamespace

from hello_agents.memory.storage.vector_store import QdrantVectorStore


class ProbeClient:
    def __init__(self, *, exists, dimension=2, distance="Cosine"):
        self.exists = exists
        self.dimension = dimension
        self.distance = distance
        self.create_calls = []

    def collection_exists(self, name):
        return self.exists

    def get_collection(self, name):
        vectors = SimpleNamespace(
            size=self.dimension,
            distance=SimpleNamespace(value=self.distance),
        )
        return SimpleNamespace(
            config=SimpleNamespace(params=SimpleNamespace(vectors=vectors))
        )

    def create_collection(self, **kwargs):
        self.create_calls.append(kwargs)


def test_in_memory_require_collection_never_creates_and_checks_distance(store):
    with pytest.raises(RAGCollectionError, match="not found"):
        store.require_collection("documents", 2, "Cosine")
    assert "documents" not in store._collections

    store.ensure_collection("documents", 2, "Cosine")
    store.require_collection("documents", 2, "Cosine")
    with pytest.raises(RAGCollectionError, match="expected 2/Dot"):
        store.require_collection("documents", 2, "Dot")


def test_qdrant_require_collection_never_creates_missing_collection():
    client = ProbeClient(exists=False)
    store = QdrantVectorStore(client=client, retry_delays=())

    with pytest.raises(RAGCollectionError, match="not found"):
        store.require_collection("documents", 2, "Cosine")

    assert client.create_calls == []


@pytest.mark.parametrize(("dimension", "distance"), [
    (3, "Cosine"), (2, "Dot"),
])
def test_qdrant_require_collection_rejects_incompatible_existing_collection(
    dimension, distance
):
    client = ProbeClient(exists=True, dimension=dimension, distance=distance)
    store = QdrantVectorStore(client=client, retry_delays=())

    with pytest.raises(RAGCollectionError, match="incompatible"):
        store.require_collection("documents", 2, "Cosine")
    assert client.create_calls == []


def test_qdrant_require_collection_accepts_exact_existing_collection():
    client = ProbeClient(exists=True)
    store = QdrantVectorStore(client=client, retry_delays=())

    store.require_collection("documents", 2, "Cosine")

    assert client.create_calls == []

```

- [x] **Step 2: Run red.**

```powershell
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pytest tests/memory/storage/test_vector_store_contract.py -q --basetemp=.pytest-tmp-bge-e2b1-store-red
```

Expected: missing `require_collection` failures.

- [x] **Step 3: Extend the protocol.**

```python
@runtime_checkable
class VectorStore(Protocol):
    def ensure_collection(
        self, collection_name: str, dimension: int, distance: str = "Cosine",
    ) -> None: ...

    def require_collection(
        self, collection_name: str, dimension: int, distance: str = "Cosine",
    ) -> None: ...
```

Keep all existing protocol methods after these declarations.

- [x] **Step 4: In InMemoryVectorStore, initialize distance state and use these methods.**

```python
self._distances: dict[str, str] = {}

def ensure_collection(
    self, collection_name: str, dimension: int, distance: str = "Cosine",
) -> None:
    with self._lock:
        existing = self._dimensions.get(collection_name)
        existing_distance = self._distances.get(collection_name)
        if existing is not None and (
            existing != dimension
            or str(existing_distance).lower() != str(distance).lower()
        ):
            raise RAGCollectionError(
                f"Collection {collection_name} has vector size {existing}, "
                f"distance {existing_distance}; expected {dimension}/{distance}"
            )
        self._dimensions[collection_name] = dimension
        self._distances[collection_name] = distance
        self._collections.setdefault(collection_name, {})

def require_collection(
    self, collection_name: str, dimension: int, distance: str = "Cosine",
) -> None:
    with self._lock:
        if collection_name not in self._dimensions:
            raise RAGCollectionError(
                f"Collection {collection_name} was not found"
            )
        existing = self._dimensions[collection_name]
        existing_distance = self._distances.get(collection_name)
        if (
            existing != dimension
            or str(existing_distance).lower() != str(distance).lower()
        ):
            raise RAGCollectionError(
                f"Collection {collection_name} has vector size {existing}, "
                f"distance {existing_distance}; expected {dimension}/{distance}"
            )
```

- [x] **Step 5: Add QdrantVectorStore.require_collection immediately before ensure_collection.**

```python
def require_collection(
    self, collection_name: str, dimension: int, distance: str = "Cosine",
) -> None:
    exists = self._call(
        "collection_exists", self.client.collection_exists, collection_name
    )
    if not exists:
        raise RAGCollectionError(
            f"Qdrant collection {collection_name} was not found"
        )
    info = self._call(
        "get_collection", self.client.get_collection, collection_name
    )
    self._validate_collection(collection_name, info, dimension, distance)
```

- [x] **Step 6: Run storage and both pipeline contract suites.**

```powershell
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pytest tests/memory/storage/test_vector_store_contract.py tests/memory/rag/test_backend_selection_and_contracts.py tests/memory/rag/test_qdrant_pipeline.py -q --basetemp=.pytest-tmp-bge-e2b1-store-green
```

Expected: all pass; missing collections are not created by read-only validation.

- [x] **Step 7: Run the E2 aggregate gate and commit.**

```powershell
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pytest tests/memory -q --basetemp=.pytest-tmp-bge-e2b1-regression
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pip check
git diff --check
git add -- hello_agents/memory/storage/vector_store.py tests/memory/storage/test_vector_store_contract.py docs/superpowers/plans/2026-09-03-bge-m3-registry-store-guard.md
git commit -m "feat(embedding): require existing vector collections without creation"
```

## Review and execution evidence

### Task 1 execution result (2026-09-03)

- Initial generated test file had a newline-escaping syntax error; it was corrected
  before red evidence. Credible red then failed only because `index_registry`
  did not exist.
- Initial registry/identity/runtime green: **66 passed**, 0.67 seconds.
- Review strengthened the initial plan: validation timestamps must be parseable UTC
  values ending in `Z`; a nonempty remote index cannot enter `active` below
  Recall@5 0.90 or MRR@5 0.75, while an explicitly empty index may omit retrieval
  scores. Reviewed green: **75 passed**, 0.73 seconds.
- Failed atomic replacement preserves the prior registry and removes temporary
  files. Keys and malformed payload content are never echoed. No production
  registry was created.

### Task 2 execution result (2026-09-03)

- Task 1 committed as `187d3b7`.
- Red: five expected failures because neither vector-store implementation exposed
  `require_collection`.
- Storage plus JSON/Qdrant contract green: **62 passed**, 3.79 seconds.
- Full `tests/memory` regression: **355 passed**, 5 pre-existing Neo4j-driver
  destructor deprecation warnings, 129.48 seconds. No Neo4j container was started.
- `compileall`, `pip check` and `git diff --check` passed. Missing-collection
  tests prove no creation call. E2-B1 is complete after this commit; managed
  pipeline and application startup adoption remain E2-B2/E2-B3 work.

- [x] Derived this substage from the approved E2 acceptance matrix after E2-A review.
- [x] Kept registry persistence separate from physical-store inspection.
- [x] Retained named E2-B2/E2-B3/E3 ownership; this plan does not activate production RAG.
- [x] Parsed all six Python blocks with the project venv; syntax passed.
  Placeholder/type scan corrected the test helper's explicit-null validation case.
- [x] Execute both red/green cycles and aggregate regression.
- [ ] Author E2-B2 against the reviewed registry and store interfaces.

At plan creation no registry file or Qdrant collection is touched. Tests use temporary
directories and fake/in-memory clients.
