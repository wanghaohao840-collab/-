# BGE-M3 RAG Runtime and Index Identity Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver E2-A: an isolated RAG embedding runtime, exact serializable index identity, and safe batch chunk preparation, without activating a production index.

**Architecture:** E1 supplies the immutable provider profile, bounded SiliconFlow client, and strict vector validation. E2-A adds backend-specific runtime identity and a pure identity contract, then extends the existing storage-independent preparation function. E2-B owns persistence and pipeline adoption; E3 owns migration and activation.

**Tech Stack:** Python 3.12-compatible code; the project's existing venv, dataclasses, pytest, HTTPX 0.28.1 mock transports, existing RAG contracts and SimpleEmbedding. No new dependency or model service.

## Global Constraints

- Governing approved specification: `docs/superpowers/specs/2026-09-03-bge-m3-rag-embedding-design.md`. Its complete acceptance criteria remain binding.
- Exact remote endpoint `https://api.siliconflow.cn/v1`, model `BAAI/bge-m3`, dimension `1024`; no `Pro/`, no request `dimensions`, no provider fallback.
- Reuse E1 bounds: batch size at most 8, each nonempty input at most 6000 UTF-8 bytes, two process-wide concurrent requests, 20 seconds per attempt, at most two retries, 75-second batch budget including slot wait, Retry-After waiting at most 10 seconds.
- Documents and queries share one runtime profile. L2 normalization rejects wrong dimensions, non-finite values and zero vectors; never pad, truncate, or replace failed vectors.
- RAG uses explicit settings and its own engine. Personal Memory remains local and its global singleton is neither replaced nor reconfigured.
- Full SHA-256 identity includes provider, endpoint, model, revision, dimension, normalization, document/query preprocessing, chunking, and distance; API keys never enter identity.
- Collection naming: `<base>__<fingerprint first 16 hex characters>`, maximum 255 characters. Full identity, not the short name or dimension alone, determines compatibility.
- Retain user namespace, document ID, content, chunk identity, version, filename and page provenance. External metadata cannot override system identity.
- Do not change either backend's existing chunking algorithm. Record their actual different algorithms in the profile.
- All document chunks must be embedded and validated before storage mutation. E2-A supplies preparation; E2-B must move every existing premature deletion behind this boundary.
- Keep `RAG_EMBEDDING_PROVIDER=simple` in the running deployment. No production `.env` edit, business-text API call, index write, container rebuild/restart or migration in this batch.
- Tests use synthetic data, fake credentials and mocked HTTP transports. No secret is copied into a worktree or command line.
- Preserve current stable-directory changes and unrelated branches; no broad staging or publication of unrelated ancestor commits.

---

## Execution context and scope boundary

Baseline: stable branch `codex/batch-import-async-tasks`, commit `5cad8bd`.
E1 has already passed live public-text probing, but that does not verify an indexed RAG path.
The E1 probe's generic `existing-rag-v1` fingerprint is not an active index identity;
the runtime deliberately derives a more precise backend-specific fingerprint.

The previously selected execution mode is serial work in this session with an isolated
worktree. At execution time read `using-git-worktrees`, create a new
`codex/bge-m3-runtime-identity` branch, and reuse the stable venv. The installed skill
catalog does not contain `executing-plans`; state that limitation and use the same
inline checkpoint fallback already accepted for E1. Do not silently substitute
subagents or install a new skill. A user-requested change to subagent execution
requires reading the corresponding skill first.

This plan delivers three independently testable foundation changes. It intentionally
does not connect a remote runtime to an unguarded live pipeline. E2 is not complete
until the E2-B acceptance matrix below is implemented and verified. Author its detailed
code-level plan against the reviewed E2-A interfaces before starting E2-B.

### Current-code evidence influencing this split

- JSON `pipeline.py::_split_text` is paragraph-first, while Qdrant
  `qdrant_pipeline.py::_split_text` is a fixed window; both use defaults 800/120.
  A shared vague chunking tag would misrepresent compatibility.
- JSON `add_text` removes old chunks before vector generation. JSON loading can
  pad/truncate, re-embed, or swallow malformed caches. These require E2-B integration
  tests, not just a provider factory.
- `prepare_document_chunks` places external metadata after system fields, allowing
  document/namespace/content/version overrides.
- `document.py` is an incomplete legacy helper with missing imports and unsafe vector
  conversion, and has no discovered production caller. E2-B must fail closed or route
  its supported entry through validated preparation; it must not claim this fragment
  is an already-working production path.
- Application persistence is rooted in `ApplicationServices.create` /
  `UserStorage.data_root`. Future registry paths must come from that explicit root,
  not cwd or the worktree.

## File and responsibility map

| Task | Files | Responsibility |
| --- | --- | --- |
| 1 | `hello_agents/memory/rag/embedding_runtime.py`; `tests/memory/rag/test_embedding_runtime.py` | Isolated engine, precise backend profile, query/document validation |
| 2 | `hello_agents/memory/rag/index_identity.py`; `tests/memory/rag/test_index_identity.py` | Pure identity serialization, comparison, safe naming and point-scope checks |
| 3 | `hello_agents/memory/rag/prepare.py`; `tests/memory/rag/test_prepare_embedding_runtime.py` | Optional runtime batches, input prevalidation and protected metadata |

No production pipeline constructor is changed in E2-A. `index_identity.py` performs
no filesystem or Qdrant operation. Its serialized DTO is evidence to compare against
trusted runtime configuration, not permission to instantiate an arbitrary provider or
trust an unverified collection. E2-B must not derive trusted active configuration from
an untrusted manifest.

## Baseline gate

- [x] Inspect worktree status and verify its imports resolve to that worktree while using the stable venv.
- [x] Run the existing memory baseline before edits:


```powershell
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pytest tests/memory -q --basetemp=.pytest-tmp-bge-e2a-baseline
```

Expected: existing tests pass, with only explicitly explained environment skips.
If failures occur, record their exact cause before advancing; do not change stable
deployment settings to make worktree tests pass.

### Task 1: Isolated runtime with exact backend identity

**Files:**

- Create: `hello_agents/memory/rag/embedding_runtime.py`
- Test: `tests/memory/rag/test_embedding_runtime.py`

**Interfaces:**

- Consumes E1: `EmbeddingSettings.from_env(values, *, candidate=False)`,
  `SiliconFlowEmbedding(settings, transport=None)`, and
  `normalize_vector(value: object, dimension: int) -> list[float]`.
- Consumes local `SimpleEmbedding(dimension=384)`, whose `encode` returns a vector
  for a string and a matrix for a list; do not use `get_text_embedder`.
- Produces `build_rag_embedding(values: Mapping[str, str | None], *, backend: str,
  transport=None) -> RAGEmbeddingRuntime`.
- Produces immutable `RAGEmbeddingRuntime.profile`, `.batch_size`,
  `.embed_documents(texts: list[str]) -> list[list[float]]` and
  `.embed_query(text: str) -> list[float]`.
- Produces `validate_texts(texts: object) -> None` and
  `validate_matrix(value: object, count: int, dimension: int) -> list[list[float]]`.
  Contract failures raise safe `EmbeddingFailure` codes. Whole-list validation
  precedes any request; E1 retains its network budget and process-wide slots.

- [x] **Step 1: Create the failing runtime tests.**

```python
from dataclasses import replace
import json

import httpx
import pytest

from hello_agents.memory import embedding as legacy
from hello_agents.memory.rag.embedding_profile import EmbeddingFailure
from hello_agents.memory.rag.embedding_runtime import build_rag_embedding


def remote_values(key="fake-embedding-key"):
    return {"RAG_EMBEDDING_PROVIDER": "siliconflow", "RAG_EMBEDDING_API_KEY": key}


def response(count):
    return {"model": "BAAI/bge-m3", "data": [
        {"index": i, "embedding": [1.0] + [0.0] * 1023} for i in range(count)
    ]}


def test_explicit_values_do_not_inherit_llm_or_process_credentials(monkeypatch):
    monkeypatch.setenv("RAG_EMBEDDING_API_KEY", "process-secret")
    with pytest.raises(EmbeddingFailure):
        build_rag_embedding({"RAG_EMBEDDING_PROVIDER": "siliconflow",
                             "LLM_API_KEY": "llm-secret"}, backend="json")


def test_local_runtime_does_not_change_memory_singleton(monkeypatch):
    singleton = legacy.SimpleEmbedding(17)
    monkeypatch.setattr(legacy, "_embedder", singleton)
    runtime = build_rag_embedding({}, backend="json")
    assert len(runtime.embed_query("public")) == 384
    assert legacy.get_text_embedder() is singleton
    assert len(singleton.encode("public")) == 17


def test_backend_identity_and_key_rotation_are_precise():
    first = build_rag_embedding(remote_values("one"), backend="json")
    rotated = build_rag_embedding(remote_values("two"), backend="json")
    qdrant = build_rag_embedding(remote_values("one"), backend="qdrant")
    assert first.profile.fingerprint == rotated.profile.fingerprint
    assert first.profile.fingerprint != qdrant.profile.fingerprint
    assert first.profile.chunking == "json-paragraph-char800-overlap120-v1"
    assert qdrant.profile.chunking == "qdrant-window-char800-overlap120-v1"
    assert "fake-embedding-key" not in repr(build_rag_embedding(
        remote_values(), backend="json"))


def test_remote_documents_and_query_use_one_client_profile():
    calls = []
    def handler(request):
        body = json.loads(request.content)
        calls.append(body["input"])
        return httpx.Response(200, json=response(len(body["input"])))
    runtime = build_rag_embedding(remote_values(), backend="qdrant",
                                  transport=httpx.MockTransport(handler))
    assert len(runtime.embed_documents(["one", "two"])) == 2
    assert len(runtime.embed_query("question")) == 1024
    assert calls == [["one", "two"], ["question"]]


@pytest.mark.parametrize("texts", [[""], ["x" * 6001], ["文" * 2001], ["\ud800"], "text"])
def test_validation_precedes_network(texts):
    def forbidden(request):
        pytest.fail("invalid input reached transport")
    runtime = build_rag_embedding(remote_values(), backend="json",
                                  transport=httpx.MockTransport(forbidden))
    with pytest.raises(EmbeddingFailure, match="input"):
        runtime.embed_documents(texts)
    assert runtime.embed_documents([]) == []


def test_mismatched_runtime_engine_is_rejected():
    runtime = build_rag_embedding(remote_values(), backend="json")
    with pytest.raises(EmbeddingFailure, match="configuration"):
        replace(runtime, profile=replace(runtime.profile, revision="another"))
    with pytest.raises(EmbeddingFailure, match="configuration"):
        replace(runtime, batch_size=9)


def test_remote_error_never_falls_back():
    runtime = build_rag_embedding(remote_values(), backend="json",
        transport=httpx.MockTransport(lambda request: httpx.Response(401)))
    with pytest.raises(EmbeddingFailure) as caught:
        runtime.embed_query("public")
    assert caught.value.status_code == 401


def test_unknown_backend_is_rejected():
    with pytest.raises(EmbeddingFailure, match="configuration"):
        build_rag_embedding({}, backend="unrecognized")


@pytest.mark.parametrize("bad", [
    [], [0.0] * 384, [1.0] * 383, [float("nan")] * 384,
    [float("inf")] * 384, [True] * 384,
])
def test_runtime_rejects_invalid_local_vector_without_repair(monkeypatch, bad):
    runtime = build_rag_embedding({}, backend="json")
    monkeypatch.setattr(runtime._engine, "encode",
                        lambda texts: [bad] if isinstance(texts, list) else bad)
    with pytest.raises(EmbeddingFailure, match="response_vector"):
        runtime.embed_query("public")
    with pytest.raises(EmbeddingFailure, match="response_vector"):
        runtime.embed_documents(["public"])


def test_runtime_rejects_wrong_document_vector_count(monkeypatch):
    runtime = build_rag_embedding({}, backend="json")
    monkeypatch.setattr(runtime._engine, "encode", lambda texts: [])
    with pytest.raises(EmbeddingFailure, match="response_schema"):
        runtime.embed_documents(["public"])
```

- [x] **Step 2: Run the tests and confirm the missing runtime module fails collection.**

```powershell
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pytest tests/memory/rag/test_embedding_runtime.py -q --basetemp=.pytest-tmp-bge-e2a-runtime-red
```

Expected: `ModuleNotFoundError` for `embedding_runtime`, not a network request.

- [x] **Step 3: Create the runtime module.**

```python
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Mapping

from hello_agents.memory.embedding import SimpleEmbedding
from hello_agents.memory.rag.embedding_client import SiliconFlowEmbedding
from hello_agents.memory.rag.embedding_profile import (
    EmbeddingFailure, EmbeddingProfile, EmbeddingSettings, normalize_vector,
)

CHUNKING_BY_BACKEND = {
    "json": "json-paragraph-char800-overlap120-v1",
    "qdrant": "qdrant-window-char800-overlap120-v1",
}


def profile_for_backend(profile: EmbeddingProfile, backend: str) -> EmbeddingProfile:
    if not isinstance(backend, str) or backend not in CHUNKING_BY_BACKEND:
        raise EmbeddingFailure("configuration")
    return replace(
        profile, chunking=CHUNKING_BY_BACKEND[backend],
        document_preprocessing="identity-v1", query_preprocessing="identity-v1",
        normalization="l2-v1", distance="Cosine",
    )


def validate_texts(texts: object) -> None:
    if not isinstance(texts, list):
        raise EmbeddingFailure("input")
    for text in texts:
        try:
            valid = (isinstance(text, str) and bool(text.strip())
                     and len(text.encode("utf-8")) <= 6000)
        except UnicodeEncodeError:
            valid = False
        if not valid:
            raise EmbeddingFailure("input")


def validate_matrix(value: object, count: int, dimension: int) -> list[list[float]]:
    if not isinstance(value, list) or len(value) != count:
        raise EmbeddingFailure("response_schema")
    return [normalize_vector(vector, dimension) for vector in value]


@dataclass(frozen=True)
class RAGEmbeddingRuntime:
    profile: EmbeddingProfile
    _engine: Any = field(repr=False, compare=False)
    batch_size: int = 8

    def __post_init__(self) -> None:
        if type(self.batch_size) is not int or not 1 <= self.batch_size <= 8:
            raise EmbeddingFailure("configuration")
        if self.profile.provider not in {"simple", "siliconflow"}:
            raise EmbeddingFailure("configuration")
        if self.profile.provider == "simple":
            if (not isinstance(self._engine, SimpleEmbedding)
                    or self._engine.dimension != self.profile.dimension):
                raise EmbeddingFailure("configuration")
        elif (not isinstance(self._engine, SiliconFlowEmbedding)
              or self._engine.profile != self.profile
              or self._engine.settings.batch_size != self.batch_size):
            raise EmbeddingFailure("configuration")

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        validate_texts(texts)
        if not texts:
            return []
        if self.profile.provider == "simple":
            raw = self._engine.encode(texts)
        else:
            raw = self._engine.embed_documents(texts)
        return validate_matrix(raw, len(texts), self.profile.dimension)

    def embed_query(self, text: str) -> list[float]:
        validate_texts([text])
        if self.profile.provider == "simple":
            raw = self._engine.encode(text)
        else:
            raw = self._engine.embed_query(text)
        return normalize_vector(raw, self.profile.dimension)


def build_rag_embedding(
    values: Mapping[str, str | None], *, backend: str, transport=None,
) -> RAGEmbeddingRuntime:
    settings = EmbeddingSettings.from_env(values)
    profile = profile_for_backend(settings.profile, backend)
    settings = replace(settings, profile=profile)
    if profile.provider == "simple":
        engine = SimpleEmbedding(dimension=profile.dimension)
    else:
        engine = SiliconFlowEmbedding(settings, transport=transport)
    return RAGEmbeddingRuntime(profile, engine, settings.batch_size)
```

- [x] **Step 4: Run the new tests together with E1 provider contracts.**

```powershell
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pytest tests/memory/rag/test_embedding_runtime.py tests/memory/rag/test_embedding_profile.py tests/memory/rag/test_embedding_client.py -q --basetemp=.pytest-tmp-bge-e2a-runtime-green
```

Expected: all selected tests pass; key rotation preserves identity, backend chunking
changes identity, Memory singleton remains unchanged, invalid vectors are rejected.

- [x] **Step 5: Review, record exact results below, and commit only this task's files and plan.**

```powershell
git diff --check
git add -- hello_agents/memory/rag/embedding_runtime.py tests/memory/rag/test_embedding_runtime.py docs/superpowers/plans/2026-09-03-bge-m3-runtime-identity-foundation.md
git commit -m "feat(embedding): isolate RAG runtime and backend profiles"
```


### Task 2: Exact index identity contract

**Files:**

- Create: `hello_agents/memory/rag/index_identity.py`
- Test: `tests/memory/rag/test_index_identity.py`

**Interfaces:**

- Consumes E1 frozen `EmbeddingProfile` and `.fingerprint`.
- Test setup consumes Task 1 `build_rag_embedding(values, *, backend, transport=None)`.
- Produces frozen `IndexIdentity(backend: str, base_collection: str,
  profile: EmbeddingProfile)`, `.physical_collection: str`,
  `.to_dict() -> dict[str, Any]`, `.from_dict(data: object) -> IndexIdentity`,
  and `.require_match(expected: IndexIdentity) -> None`.
- Produces `physical_collection_name(base: str, fingerprint: str) -> str` and
  `require_point_identity(metadata: object, *, identity: IndexIdentity,
  rag_namespace: str, document_id: str | None = None) -> None`.
- Failure is `IndexIdentityError(RAGConfigError)`, with a safe `.code`.
  Structural deserialization cannot establish provenance; compare against a trusted
  runtime identity and verify the physical store separately in E2-B.

- [x] **Step 1: Create the failing identity tests.**

```python
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from hello_agents.memory.rag.embedding_runtime import build_rag_embedding
from hello_agents.memory.rag.index_identity import (
    IndexIdentity, IndexIdentityError, physical_collection_name, require_point_identity,
)


def identity():
    runtime = build_rag_embedding(
        {"RAG_EMBEDDING_PROVIDER": "siliconflow", "RAG_EMBEDDING_API_KEY": "fake-secret"},
        backend="qdrant",
    )
    return IndexIdentity("qdrant", "doc_learning_vectors", runtime.profile)


def test_round_trip_contains_no_key_or_runtime_configuration():
    expected = identity()
    data = expected.to_dict()
    assert IndexIdentity.from_dict(json.loads(json.dumps(data))) == expected
    assert "fake-secret" not in json.dumps(data)
    assert data["physical_collection"].endswith(expected.profile.fingerprint[:16])


@pytest.mark.parametrize("kind", ["schema", "extra_key", "profile_key", "fingerprint",
                                  "name", "dimension", "backend"])
def test_corrupt_identity_is_rejected_safely(kind):
    data = deepcopy(identity().to_dict())
    if kind == "schema": data["schema_version"] = True
    elif kind == "extra_key": data["api_key"] = "secret-body"
    elif kind == "profile_key": data["profile"]["api_key"] = "secret-body"
    elif kind == "fingerprint": data["fingerprint"] = "0" * 64
    elif kind == "name": data["physical_collection"] = "other"
    elif kind == "dimension": data["profile"]["dimension"] = True
    elif kind == "backend": data["backend"] = []
    with pytest.raises(IndexIdentityError) as caught:
        IndexIdentity.from_dict(data)
    assert "secret-body" not in str(caught.value)


def test_same_dimension_does_not_mean_same_model_identity():
    original = identity()
    changed = replace(original, profile=replace(original.profile, revision="v2"))
    assert original.profile.dimension == changed.profile.dimension
    with pytest.raises(IndexIdentityError, match="mismatch"):
        original.require_match(changed)


def test_shortened_name_is_not_the_identity(monkeypatch):
    original = identity()
    changed = replace(original, profile=replace(original.profile, revision="v2"))
    monkeypatch.setattr(IndexIdentity, "physical_collection", property(lambda self: "collision"))
    assert original.physical_collection == changed.physical_collection
    with pytest.raises(IndexIdentityError, match="mismatch"):
        original.require_match(changed)


@pytest.mark.parametrize("base", ["", "../escape", "bad.name", "x" * 238])
def test_collection_name_rejects_unsafe_or_oversized_base(base):
    with pytest.raises(IndexIdentityError):
        physical_collection_name(base, "a" * 64)
    assert len(physical_collection_name("x" * 237, "a" * 64)) == 255


@pytest.mark.parametrize("changes", [
    {"embedding_fingerprint": "wrong"}, {"rag_namespace": "another-user"},
    {"document_id": "another-document"}, {"document_id": None},
])
def test_point_requires_fingerprint_and_scope(changes):
    expected = identity()
    metadata = {"embedding_fingerprint": expected.profile.fingerprint,
                "rag_namespace": "user-a", "document_id": "doc-a"}
    require_point_identity(metadata, identity=expected,
                           rag_namespace="user-a", document_id="doc-a")
    metadata.update(changes)
    with pytest.raises(IndexIdentityError, match="point"):
        require_point_identity(metadata, identity=expected,
                               rag_namespace="user-a", document_id="doc-a")
```

- [x] **Step 2: Confirm the new module is missing.**

```powershell
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pytest tests/memory/rag/test_index_identity.py -q --basetemp=.pytest-tmp-bge-e2a-identity-red
```

Expected: `ModuleNotFoundError` for `index_identity`.

- [x] **Step 3: Create the pure identity module.**

```python
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import re
from typing import Any

from hello_agents.memory.rag.embedding_profile import EmbeddingProfile
from hello_agents.memory.rag.errors import RAGConfigError

_NAME = re.compile(r"[A-Za-z0-9_-]+")
_PROFILE_FIELDS = {field.name for field in fields(EmbeddingProfile)}
_IDENTITY_FIELDS = {"schema_version", "backend", "base_collection",
                    "physical_collection", "fingerprint", "profile"}


class IndexIdentityError(RAGConfigError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(f"RAG index identity rejected: {code}")


def validate_profile(profile: EmbeddingProfile) -> None:
    if type(profile.dimension) is not int or not 1 <= profile.dimension <= 65536:
        raise IndexIdentityError("profile")
    for name, value in asdict(profile).items():
        if name == "dimension":
            continue
        if (not isinstance(value, str) or len(value) > 256
                or (name != "endpoint" and not value)
                or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)):
            raise IndexIdentityError("profile")
    if profile.distance != "Cosine":
        raise IndexIdentityError("profile")


def physical_collection_name(base: str, fingerprint: str) -> str:
    if (not isinstance(base, str) or not _NAME.fullmatch(base)
            or not isinstance(fingerprint, str)
            or not re.fullmatch(r"[a-f0-9]{64}", fingerprint)):
        raise IndexIdentityError("name")
    name = base + "__" + fingerprint[:16]
    if len(name) > 255:
        raise IndexIdentityError("name")
    return name


@dataclass(frozen=True)
class IndexIdentity:
    backend: str
    base_collection: str
    profile: EmbeddingProfile

    def __post_init__(self) -> None:
        if not isinstance(self.backend, str) or self.backend not in {"json", "qdrant"}:
            raise IndexIdentityError("backend")
        validate_profile(self.profile)
        physical_collection_name(self.base_collection, self.profile.fingerprint)

    @property
    def physical_collection(self) -> str:
        return physical_collection_name(self.base_collection, self.profile.fingerprint)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "backend": self.backend,
            "base_collection": self.base_collection,
            "physical_collection": self.physical_collection,
            "fingerprint": self.profile.fingerprint,
            "profile": asdict(self.profile),
        }

    @classmethod
    def from_dict(cls, data: object) -> IndexIdentity:
        try:
            if (not isinstance(data, dict) or set(data) != _IDENTITY_FIELDS
                    or type(data["schema_version"]) is not int
                    or data["schema_version"] != 1
                    or not isinstance(data["profile"], dict)
                    or set(data["profile"]) != _PROFILE_FIELDS):
                raise IndexIdentityError("schema")
            result = cls(data["backend"], data["base_collection"],
                         EmbeddingProfile(**data["profile"]))
            if (data["fingerprint"] != result.profile.fingerprint
                    or data["physical_collection"] != result.physical_collection):
                raise IndexIdentityError("fingerprint")
            return result
        except (TypeError, ValueError, KeyError, AttributeError):
            raise IndexIdentityError("schema") from None

    def require_match(self, expected: IndexIdentity) -> None:
        # Compare full profiles, not merely dimensions or shortened names.
        if self != expected:
            raise IndexIdentityError("mismatch")


def require_point_identity(
    metadata: object, *, identity: IndexIdentity, rag_namespace: str,
    document_id: str | None = None,
) -> None:
    if (not isinstance(rag_namespace, str) or not rag_namespace
            or not isinstance(metadata, dict)
            or metadata.get("embedding_fingerprint") != identity.profile.fingerprint
            or metadata.get("rag_namespace") != rag_namespace
            or not isinstance(metadata.get("document_id"), str)
            or not metadata["document_id"]
            or (document_id is not None and metadata["document_id"] != document_id)):
        raise IndexIdentityError("point")
```

- [x] **Step 4: Run identity and runtime tests.**

```powershell
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pytest tests/memory/rag/test_index_identity.py tests/memory/rag/test_embedding_runtime.py -q --basetemp=.pytest-tmp-bge-e2a-identity-green
```

Expected: strict round-trip, schema rejection, same-dimension mismatch, shortened-name
collision protection and scope rejection all pass. No collection is created.

- [x] **Step 5: Review, record exact results below, and commit this deliverable.**

```powershell
git diff --check
git add -- hello_agents/memory/rag/index_identity.py tests/memory/rag/test_index_identity.py docs/superpowers/plans/2026-09-03-bge-m3-runtime-identity-foundation.md
git commit -m "feat(embedding): define exact RAG index identity contract"
```


### Task 3: Safe batch preparation and system metadata protection

**Files:**

- Modify: `hello_agents/memory/rag/prepare.py`, its imports and
  `prepare_document_chunks` only; preserve existing helper functions.
- Test: `tests/memory/rag/test_prepare_embedding_runtime.py`

**Interfaces:**

- Consumes existing `DocumentSegment(content, metadata)`,
  `PreparedChunk(id, document_id, content, vector, metadata)`,
  `json_safe_dict`, `utc_now_iso`, `qdrant_point_id` and `report_progress`.
- Consumes Task 1 `RAGEmbeddingRuntime`, `.batch_size`, `.profile.fingerprint`,
  `.embed_documents(list[str])`, and `validate_texts(object)`.
- Extends `prepare_document_chunks(document_id, segments, rag_namespace, split_text,
  embed_text, id_for_chunk=None, progress_callback=None, *, embedding_runtime=None)`.
  Exactly one vector source is allowed. Existing positional callable users keep
  per-chunk progress; runtime users pass `embed_text=None`.
- Runtime path validates every input before the first batch, prepares all vectors
  before returning chunks, and reports completed-vector progress at each batch.
  It is storage-independent; later-batch failure returns no partial result.
- Preserve custom `id_for_chunk` callbacks and original source fields. System fields
  win, `_vector_store_id` is removed, and only the runtime supplies a fingerprint.
  Document replacement/version assignment remains the caller's responsibility.

- [x] **Step 1: Create failing preparation tests.**

```python
import json
import httpx
import pytest

from hello_agents.memory.rag.contracts import DocumentSegment
from hello_agents.memory.rag.embedding_profile import EmbeddingFailure
from hello_agents.memory.rag.embedding_runtime import build_rag_embedding
from hello_agents.memory.rag.prepare import prepare_document_chunks, qdrant_point_id


def runtime(handler):
    return build_rag_embedding(
        {"RAG_EMBEDDING_PROVIDER": "siliconflow", "RAG_EMBEDDING_API_KEY": "fake-secret"},
        backend="qdrant", transport=httpx.MockTransport(handler),
    )


def response(count):
    return {"model": "BAAI/bge-m3", "data": [
        {"index": i, "embedding": [1.0] + [0.0] * 1023} for i in range(count)
    ]}


def test_legacy_metadata_cannot_replace_system_identity():
    prepared = prepare_document_chunks(
        "doc-a", [DocumentSegment("source", {
            "document_id": "doc-b", "rag_namespace": "user-b", "content": "forged",
            "memory_id": "forged", "chunk_index": 900, "document_version": 99,
            "embedding_fingerprint": "forged", "_vector_store_id": "forged",
            "page_number": 7, "file_name": "paper.pdf",
        })], "user-a", lambda text: [text], lambda text: [1.0, 0.0],
    )
    item = prepared[0]
    assert item.id == qdrant_point_id("user-a", "doc-a", 0)
    assert item.metadata["memory_id"] == item.id
    assert item.metadata["document_id"] == "doc-a"
    assert item.metadata["rag_namespace"] == "user-a"
    assert item.metadata["content"] == "source"
    assert item.metadata["chunk_index"] == 0
    assert item.metadata["document_version"] == 1
    assert "embedding_fingerprint" not in item.metadata
    assert "_vector_store_id" not in item.metadata
    assert item.metadata["page_number"] == 7
    assert item.metadata["file_name"] == "paper.pdf"


def test_remote_preparation_batches_and_reports_monotonic_progress():
    calls, progress = [], []
    def handler(request):
        body = json.loads(request.content)
        calls.append(body["input"])
        return httpx.Response(200, json=response(len(body["input"])))
    embedded = runtime(handler)
    prepared = prepare_document_chunks(
        "doc-a", [DocumentSegment(" ".join(str(i) for i in range(10)), {"page_number": 3})],
        "user-a", str.split, None, embedding_runtime=embedded,
        progress_callback=lambda *event: progress.append(event),
    )
    assert [len(batch) for batch in calls] == [8, 2]
    assert [event[1] for event in progress] == [8, 10]
    assert all(event[0] == "embedding" and event[2] == 10 for event in progress)
    assert len(prepared) == 10
    assert all(c.metadata["embedding_fingerprint"] == embedded.profile.fingerprint
               and c.metadata["page_number"] == 3 and len(c.vector) == 1024
               for c in prepared)


def test_later_bad_input_prevents_even_first_remote_batch():
    calls = []
    def forbidden(request):
        calls.append(request)
        pytest.fail("invalid later chunk caused a request")
    with pytest.raises(EmbeddingFailure, match="input"):
        prepare_document_chunks(
            "doc-a", [DocumentSegment("source", {})], "user-a",
            lambda text: ["valid"] * 8 + ["文" * 2001], None,
            embedding_runtime=runtime(forbidden),
        )
    assert calls == []


def test_second_remote_batch_failure_returns_no_prepared_result():
    calls = []
    def handler(request):
        calls.append(request)
        return (httpx.Response(200, json=response(8)) if len(calls) == 1
                else httpx.Response(401))
    with pytest.raises(EmbeddingFailure) as caught:
        prepare_document_chunks(
            "doc-a", [DocumentSegment("source", {})], "user-a",
            lambda text: ["chunk"] * 10, None, embedding_runtime=runtime(handler),
        )
    assert caught.value.status_code == 401
    assert len(calls) == 2


def test_preparation_rejects_ambiguous_embedding_sources():
    with pytest.raises(EmbeddingFailure, match="configuration"):
        prepare_document_chunks(
            "doc", [], "ns", str.split, lambda text: [1.0],
            embedding_runtime=runtime(lambda request: httpx.Response(401)),
        )
```

- [x] **Step 2: Run tests against the current function.**

```powershell
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pytest tests/memory/rag/test_prepare_embedding_runtime.py -q --basetemp=.pytest-tmp-bge-e2a-prepare-red
```

Expected: legacy metadata protection fails and the new `embedding_runtime` keyword
is unsupported. These failures demonstrate missing behavior, not provider downtime.

- [x] **Step 3: Add these imports to prepare.py after its existing contracts import.**

```python
from hello_agents.memory.rag.embedding_profile import EmbeddingFailure
from hello_agents.memory.rag.embedding_runtime import RAGEmbeddingRuntime, validate_texts
```

- [x] **Step 4: Replace only prepare_document_chunks with this complete function.**

```python
def prepare_document_chunks(
    document_id: str,
    segments: Sequence[DocumentSegment],
    rag_namespace: str,
    split_text: Callable[[str], list[str]],
    embed_text: Callable[[str], list[float]] | None,
    id_for_chunk: Callable[[str, str, int], str] | None = None,
    progress_callback: ProgressCallback | None = None,
    *,
    embedding_runtime: RAGEmbeddingRuntime | None = None,
) -> list[PreparedChunk]:
    if not document_id:
        raise ValueError("document_id is required")
    if not isinstance(rag_namespace, str) or not rag_namespace.strip():
        raise ValueError("rag_namespace is required")
    if ((embedding_runtime is None and not callable(embed_text))
            or (embedding_runtime is not None and embed_text is not None)):
        raise EmbeddingFailure("configuration")

    id_for_chunk = id_for_chunk or qdrant_point_id
    pending: list[tuple[dict[str, Any], str]] = []
    for segment in segments:
        if not segment.content or not segment.content.strip():
            continue
        segment_metadata = json_safe_dict(segment.metadata or {})
        for chunk_text in split_text(segment.content):
            chunk_text = str(chunk_text).strip()
            if chunk_text:
                pending.append((segment_metadata, chunk_text))

    total_chunks = len(pending)
    vectors: list[list[float]] = []
    if embedding_runtime is not None:
        texts = [text for _, text in pending]
        validate_texts(texts)
        for start in range(0, total_chunks, embedding_runtime.batch_size):
            batch = texts[start:start + embedding_runtime.batch_size]
            vectors.extend(embedding_runtime.embed_documents(batch))
            report_progress(progress_callback, "embedding", len(vectors),
                            total_chunks, "embedding")

    prepared: list[PreparedChunk] = []
    for chunk_index, (segment_metadata, chunk_text) in enumerate(pending):
        chunk_id = id_for_chunk(rag_namespace, document_id, chunk_index)
        now = utc_now_iso()
        metadata = {
            **segment_metadata,
            "memory_id": chunk_id,
            "document_id": document_id,
            "chunk_index": chunk_index,
            "content": chunk_text,
            "memory_type": "rag_chunk",
            "is_rag_data": True,
            "data_source": "rag_pipeline",
            "rag_namespace": rag_namespace,
            "created_at": now,
            "updated_at": now,
            "document_version": 1,
        }
        metadata.pop("_vector_store_id", None)
        metadata.pop("embedding_fingerprint", None)
        if embedding_runtime is not None:
            metadata["embedding_fingerprint"] = embedding_runtime.profile.fingerprint
            vector = vectors[chunk_index]
        else:
            vector = normalize_vector(embed_text(chunk_text))
        prepared.append(PreparedChunk(
            id=chunk_id, document_id=document_id, content=chunk_text,
            vector=vector, metadata=json_safe_dict(metadata),
        ))
        if embedding_runtime is None:
            report_progress(progress_callback, "embedding", chunk_index + 1,
                            total_chunks, "embedding")
    return prepared
```

- [x] **Step 5: Verify the new behavior plus existing import progress and pipeline contracts.**

```powershell
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pytest tests/memory/rag/test_prepare_embedding_runtime.py tests/memory/rag/test_import_progress.py tests/memory/rag/test_qdrant_pipeline.py tests/memory/rag/test_pipeline_multi_document.py tests/assistants/test_import_idempotency.py -q --basetemp=.pytest-tmp-bge-e2a-prepare-green
```

Expected: all selected tests pass. Legacy one-at-a-time progress remains unchanged;
remote 10-chunk preparation sends 8 then 2, and system metadata cannot be forged.

- [x] **Step 6: Run the full in-scope regression gate and dependency consistency check.**

```powershell
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pytest tests/memory tests/assistants/test_import_idempotency.py tests/test_import_worker.py tests/test_import_service.py tests/test_runtime_import_leases.py tests/test_import_error_sanitization.py tests/test_document_library_service.py tests/test_qa_answer_engine.py tests/test_note_service.py -q --basetemp=.pytest-tmp-bge-e2a-regression
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pip check
git diff --check
```

Expected: tests and dependency consistency pass. Investigate failures against baseline;
do not claim production migration or real indexed retrieval was tested.

- [x] **Step 7: Record review and regression evidence; commit the reviewed preparation change.**

```powershell
git add -- hello_agents/memory/rag/prepare.py tests/memory/rag/test_prepare_embedding_runtime.py docs/superpowers/plans/2026-09-03-bge-m3-runtime-identity-foundation.md
git commit -m "fix(embedding): prepare batches before commit and protect chunk identity"
```


## Remaining E2-B acceptance ownership (not delivered by E2-A)

This is the required coverage map for the next bounded integration plan, not a list of
optional enhancements. E2 must remain incomplete until each row has code-level tasks,
failure tests, implementation and verified integration.

| Requirement | Owning files / boundary | Required acceptance evidence |
| --- | --- | --- |
| Persistent active/source profile, state, migration ID and validation registry under actual app data root | New focused registry module in `hello_agents/memory/rag/`; `app/bootstrap.py`, `app/runtime.py`, `hello_agents/tools/builtin/rag_tool.py` | Restart restores exact identity; cwd/worktree change cannot redirect registry; malformed/missing active state fails closed; no key persisted |
| Read-only physical-store identity/dimension check, separate from explicit initialization | `hello_agents/memory/storage/vector_store.py`; registry service | Missing active collection does not invoke `ensure_collection`; same dimensions with a different full profile rejected; verified-empty initialization is admin-only |
| Qdrant adoption and every read/write guard | `hello_agents/memory/rag/qdrant_pipeline.py` | Query and document use one runtime; startup, search, summary, list/count, replacement, append, deletion and clear reject mismatches; fingerprint filter alone cannot silently hide inconsistent legacy points |
| JSON versioned envelope and durable replacement | `hello_agents/memory/rag/pipeline.py`; focused cache module if needed | New versioned path preserves old file; corrupt, empty, wrong-size, missing-vector and wrong-profile cases tested; no padding/re-embedding during load; failed save cannot report success or leave memory/file divergence |
| Safe replacement ordering on both backends | `pipeline.py::add_text/replace_document`, `qdrant_pipeline.py::add_text/replace_document` | Failure during a later embedding batch leaves existing document chunks unchanged; all vectors validated before first storage mutation |
| Protected scope and provenance through outer payloads and append offsets | `prepare.py`, both pipelines, `vector_store.py` | Namespace, document ID, chunk ID/index, version, source and page survive; no external fingerprint or nested system-field override; deletion remains scoped |
| Factory and per-user RAG wiring, explicit simple compatibility | `pipeline.py::create_rag_pipeline`, `app/runtime.py`, `rag_tool.py` | No namespace can reuse another namespace's cache; absent config cannot downgrade an already-active real profile; constructors and helper APIs cannot bypass identity; actual chunking profile is checked |
| Legacy helper entry points | `hello_agents/memory/rag/document.py`, `pipeline.py::index_chunks` | Supported helper path validates vectors and scope; unsupported incomplete helper fails explicitly without network/storage mutation; no silent padding, partial storage, or duplicate re-chunking |
| Retry semantics and health isolation | `rag_tool.py::_classify_action_failure`, affected app/import error tests | Preserve `EmbeddingFailure.retryable`/safe status rather than flattening all embedding errors; base health stays independent of remote API availability |
| Cross-component regression | Existing memory, import, QA, Notes, deployment suites plus new registry tests | Both backends restart correctly; personal Memory does not contact SiliconFlow; no source/document/user leakage; no changes to LLM/Notes data |

Storage-update crash consistency and concurrent write coordination must be evaluated
against the actual backend transaction/lease behavior in E2-B; E2-A's all-vector
preparation is not described as a database transaction.

## E3 and later phase boundary

E3 retains the approved admin workflow: bounded source pagination, ownership/content
inventory, source-digest checkpoints and resume checks, cold backup and operations lock,
App drain/maintenance, build into a new target, partition/content/identity verification,
fixed 20-query bilingual quality gate, paired env/registry cutover journal, no-write
fast rollback versus current-authoritative-chunk rebuild rollback, and isolated
`deploy/smoke_test.py --deep`. No automatic deletion of source collections, caches,
Notes or backups. Success requires Recall@5 >= 0.90, MRR@5 >= 0.75, no regression
against the same old-model baseline, and zero scope leaks.

Resource limits, log rotation, disk alerts/retention and localhost-default binding
remain the next authorized product hardening stages after embedding acceptance.
Their implementation is not claimed by any E2-A test.

## Review and execution evidence

### Operator execution and remaining roadmap (2026-09-03)

- Operator explicitly selected serial execution in an isolated branch.
- Worktree: `D:/python_self_agent/.worktrees/bge-m3-runtime-identity`;
  branch `codex/bge-m3-runtime-identity`, starting commit `daeb24f`.
  Imports resolve inside this worktree; the stable venv is reused and
  `pip check` reports no broken requirements.
- Finish embedding E2-A, E2-B and E3 before remaining resource/log/disk/retention
  and localhost-default network hardening; then proceed to overview and learning
  insights through its specification, plan, implementation and acceptance.
- Previously integrated Windows login recovery, five-minute health checks,
  daily consistent backup, monthly isolated restore drill, safe update/rollback,
  Qdrant POSIX storage and real LLM configuration require evidence-based rechecks,
  not blind reinstallation or another data migration.
- Preserve the explicit decision to leave Neo4j stopped until GraphRAG enters
  the product roadmap, when it needs a real lifecycle test as a third container.
- New operator requirement: routine operations must run in the background without
  repeatedly opening PowerShell windows; alerts must remain available as background
  records. Diagnose task-launch and notification paths and include a tested quiet
  launcher/notification policy in the operations hardening stage. Do not disable
  health checks or conceal failures to suppress the windows.
- Read-only current checks found two healthy containers (App and Qdrant) and all
  four expected scheduled tasks in Ready state. App is still bound to
  `0.0.0.0:7860`; no Neo4j container was listed. No deployment change made.

- [x] Re-read approved spec, E1 progress, repository context and current code.
- [x] Separate JSON paragraph and Qdrant window chunking identities without changing algorithms.
- [x] Supply full implementation/test code for all three E2-A deliverables.
- [x] Preserve named E2-B/E3 ownership for every remaining governing-spec requirement.
- [x] Parsed all seven Python code blocks with the project venv in UTF-8 mode; syntax passed. Placeholder scan found no unfinished-code markers; source review corrected the Qdrant append ownership to its actual `add_text` method. Runtime tests have not run.
- [x] Execute baseline and all three red/green cycles in the isolated worktree.
- [x] Complete regression, source review and scoped commits; record exact results here.
- [ ] Author and review the E2-B implementation plan against the resulting interfaces.

At plan creation: no E2-A unit test has run, no E2 implementation is active, and no
live data or deployment configuration has changed. Syntax checks are not runtime tests.

### Task 1 execution result (2026-09-03)

- Baseline: `tests/memory -q --basetemp=.pytest-tmp-bge-e2a-baseline`:
  **270 passed**, 145.36 seconds; project imports confirmed from the isolated worktree.
- Initial red: missing `embedding_runtime` module. Initial runtime/profile/client
  green: **105 passed**, 0.89 seconds.
- Review identified an identity claim gap in the initial plan: direct runtime
  construction could label SimpleEmbedding as another model or an unimplemented
  normalization/preprocessing algorithm. Added ten reproducing failure cases,
  then required actual supported transforms and the exact local profile.
- Hardened runtime/profile/client green:
  `--basetemp=.pytest-tmp-bge-e2a-runtime-contract-green`: **115 passed**, 1.05 seconds.
  `git diff --check` passed. The reviewed implementation supersedes the initial
  code sketch above where that hardening differs.
- New remote tests used only HTTPX mocks. No production activation, data writes
  or container changes. Next gate is Task 2's identity contract.

### Task 2 execution result (2026-09-03)

- Task 1 committed as `9a33272`.
- Red: missing `index_identity` module. Initial identity/runtime green:
  **47 passed**, 0.40 seconds.
- Review added explicit fingerprint/mismatch coverage for provider, endpoint, model,
  dimension, normalization, document/query preprocessing and chunking changes.
  Identity/runtime reviewed green: **55 passed**, 0.49 seconds,
  `--basetemp=.pytest-tmp-bge-e2a-identity-reviewed`.
- `git diff --check` passed. This module is a pure identity DTO; its docstring
  explicitly requires comparison with trusted runtime and a separate physical-store
  verification. It does not write a registry, create collections or activate RAG.
- Next gate: safe batch preparation plus unchanged legacy import progress.

### Task 3 execution result (2026-09-03)

- Task 2 committed as `a337d00`.
- Red: five intended failures—external metadata could forge system identity and
  the runtime batch interface did not exist.
- Focused green covering preparation, import progress, both pipeline contracts and
  import idempotency: **96 passed**, 151.32 seconds.
- Full in-scope regression covering Memory, Assistant import, import workers/leases,
  document library, QA answer engine and Notes: **466 passed**, 229.73 seconds,
  `--basetemp=.pytest-tmp-bge-e2a-regression`.
- `compileall`, `pip check` and `git diff --check` passed. Tests used mock
  embedding transport only. Existing App/Qdrant and production provider were not
  changed. E2-A is complete after this commit; E2-B registry/pipeline integration
  remains required before any production activation.
