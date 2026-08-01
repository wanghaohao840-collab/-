# Qdrant RAG Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a configurable Qdrant RAG backend connected through `QDRANT_URL` while preserving the existing JSON backend as the default behavior.

**Architecture:** Keep the existing `SimpleRAGPipeline` as the JSON backend and add a `QdrantRAGPipeline` behind the same backend contract. Move document-level splitting, embedding, metadata preservation, deterministic chunk indexing, and result shaping into shared RAG preparation code so JSON and Qdrant behave the same. Keep the dependency direction `UI -> Assistant -> Tool -> RAG/Storage`; storage code never imports tools or assistants.

**Tech Stack:** Python 3, `qdrant-client==1.18.0`, `qdrant/qdrant:v1.18.2` for manual verification, `pytest==8.4.1`, `pypdf`, `python-docx`, local embedding helpers in `hello_agents.memory.embedding`.

## Global Constraints

- `RAG_BACKEND` accepts only `json` or `qdrant`; default is `json`.
- `RAG_BACKEND=qdrant` requires `QDRANT_URL`; invalid configuration raises `RAGConfigError`.
- `QDRANT_API_KEY` is optional and must never appear in user-visible errors or logs.
- `QDRANT_COLLECTION` is a deployment-level override; otherwise use the collection name fixed when `RAGTool` is constructed; otherwise use `rag_knowledge_base`.
- A `RAGTool` instance fixes one collection name for all namespaces; actions and namespace pipelines cannot override the collection at runtime.
- Qdrant connects to an independent service through the official Python client; do not add embedded/local Qdrant mode.
- JSON remains the default backend and must keep existing cache persistence and restart behavior.
- Do not add JSON-to-Qdrant migration, double-write, failover, or runtime hot switching.
- Both backends support `add_text`, `replace_document`, `search`, `stats`, `delete_document`, `clear`, and `get_document_summary_context`.
- `replace_document(document_id, segments)` is the public whole-document import path; `RAGTool` must not call backend private methods such as `_remove_document_chunks()` or `_save_cache()`.
- PDF import creates one ordered segment per page and preserves `page_number`; TXT/Markdown/DOCX preserve source metadata and `document_id`.
- Shared preparation assigns document-wide contiguous `chunk_index` values starting at 0 across all segments.
- Qdrant collection uses cosine distance and vector dimension equal to the current embedder dimension.
- Reuse an existing compatible Qdrant collection; reject an existing incompatible collection with `RAGCollectionError`.
- Qdrant payload includes `content`, `document_id`, `rag_namespace`, `chunk_index`, and JSON-serializable `metadata`.
- Qdrant point IDs are deterministic UUID5 values generated from `canonical_json([rag_namespace, document_id, chunk_index])`.
- `PROJECT_POINT_NAMESPACE_UUID` is exactly `c273c00a-40ac-47a9-b475-164f135ada18`.
- Qdrant batch upsert uses at most 100 points per batch and waits for server confirmation.
- Qdrant `replace_document` first upserts all new chunks, then deletes orphan chunks where `chunk_index >= new_chunk_count`.
- Retry only idempotent remote operations: deterministic-ID upsert, search/query, scroll, count, filtered delete, and collection read.
- Retry network timeout, connection interruption, and Qdrant 5xx responses up to three retries with waits `0.5s`, `1s`, and `2s`.
- Do not retry Qdrant 4xx responses.
- If collection creation has an uncertain response, reread and verify the collection before attempting another create.
- `get_document_summary_context` uses scroll page size 256, does not request vectors, sorts by `chunk_index`, and rejects more than 10,000 chunks with `RAGDocumentTooLargeError`.
- `stats` uses Qdrant count for chunk count and scroll page size 512 with only `document_id` payload for exact document count.
- `delete_document` and `clear` filter by current `rag_namespace`; `clear` must not delete the collection.
- `RAGTool.execute_result()` returns `RAGActionResult(success, message, data, error_code)`.
- Existing `RAGTool.execute()` remains a text wrapper for Agent/UI/example compatibility.
- Assistant import/delete/clear mutates current document, history, and stats only when `execute_result().success is True`.
- Automated tests use fake or mock Qdrant clients and do not require an external Qdrant service.
- Manual integration verification uses `docker run --rm --name qdrant-rag-test -p 6333:6333 qdrant/qdrant:v1.18.2`.
- Preserve existing source metadata, page numbers, `document_id` isolation, and the existing page-level search deduplication behavior.

---

### Task 1: Shared Errors, Contracts, and Preparation Layer

**Files:**
- Create: `hello_agents/memory/rag/errors.py`
- Create: `hello_agents/memory/rag/contracts.py`
- Create: `hello_agents/memory/rag/prepare.py`
- Modify: `hello_agents/memory/rag/__init__.py`
- Test: `tests/memory/rag/test_errors_and_prepare.py`

**Interfaces:**
- Produces: `class RAGBackendError(Exception)`
- Produces: `class RAGConfigError(RAGBackendError)`
- Produces: `class RAGConnectionError(RAGBackendError)`
- Produces: `class RAGCollectionError(RAGBackendError)`
- Produces: `class RAGDocumentTooLargeError(RAGBackendError)`
- Produces: `class RAGOperationError(RAGBackendError)`
- Produces: `sanitize_qdrant_url(url: str) -> str`
- Produces: `@dataclass(frozen=True) class DocumentSegment(content: str, metadata: dict[str, Any])`
- Produces: `@dataclass(frozen=True) class PreparedChunk(id: str, document_id: str, content: str, vector: list[float], metadata: dict[str, Any])`
- Produces: `PROJECT_POINT_NAMESPACE_UUID: uuid.UUID`
- Produces: `canonical_json(value: Any) -> str`
- Produces: `prepare_document_chunks(document_id: str, segments: Sequence[DocumentSegment], rag_namespace: str, split_text: Callable[[str], list[str]], embed_text: Callable[[str], list[float]], id_for_chunk: Callable[[str, str, int], str] | None = None) -> list[PreparedChunk]`

- [ ] **Step 1: Write failing tests for error hierarchy and URL sanitization**

Create `tests/memory/rag/test_errors_and_prepare.py` with:

```python
from hello_agents.memory.rag.errors import (
    RAGBackendError,
    RAGCollectionError,
    RAGConfigError,
    RAGConnectionError,
    RAGDocumentTooLargeError,
    RAGOperationError,
    sanitize_qdrant_url,
)


def test_backend_errors_share_base_class():
    for cls in [
        RAGConfigError,
        RAGConnectionError,
        RAGCollectionError,
        RAGDocumentTooLargeError,
        RAGOperationError,
    ]:
        assert issubclass(cls, RAGBackendError)


def test_sanitize_qdrant_url_removes_credentials_and_query_keys():
    raw = "https://user:secret@example.com:6333/path?api_key=abc&token=def&x=1"

    sanitized = sanitize_qdrant_url(raw)

    assert sanitized == "https://example.com:6333/path?api_key=***&token=***&x=1"
    assert "secret" not in sanitized
    assert "abc" not in sanitized
    assert "def" not in sanitized
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/memory/rag/test_errors_and_prepare.py::test_backend_errors_share_base_class tests/memory/rag/test_errors_and_prepare.py::test_sanitize_qdrant_url_removes_credentials_and_query_keys -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'hello_agents.memory.rag.errors'`.

- [ ] **Step 3: Implement backend errors and sanitizer**

Create `hello_agents/memory/rag/errors.py`:

```python
from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


class RAGBackendError(Exception):
    """Base class for stable RAG backend errors shown through RAGTool."""


class RAGConfigError(RAGBackendError):
    """Invalid backend configuration discovered at initialization."""


class RAGConnectionError(RAGBackendError):
    """Qdrant service, network, or authentication failure."""


class RAGCollectionError(RAGBackendError):
    """Qdrant collection is missing or incompatible."""


class RAGDocumentTooLargeError(RAGBackendError):
    """Document operation exceeded a bounded chunk limit."""


class RAGOperationError(RAGBackendError):
    """Backend operation failed after safe conversion."""

    def __init__(self, message: str, *, operation: str = "", document_id: str = ""):
        self.operation = operation
        self.document_id = document_id
        super().__init__(message)


_SECRET_QUERY_KEYS = {
    "api_key",
    "apikey",
    "key",
    "token",
    "access_token",
    "authorization",
    "auth",
}


def sanitize_qdrant_url(url: str) -> str:
    if not url:
        return ""

    parts = urlsplit(str(url))
    host = parts.hostname or ""
    netloc = host

    if parts.port is not None:
        netloc = f"{netloc}:{parts.port}"

    safe_pairs = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key.lower() in _SECRET_QUERY_KEYS:
            safe_pairs.append((key, "***"))
        else:
            safe_pairs.append((key, value))

    return urlunsplit((parts.scheme, netloc, parts.path, urlencode(safe_pairs), parts.fragment))
```

- [ ] **Step 4: Write failing tests for canonical JSON and chunk preparation**

Append to `tests/memory/rag/test_errors_and_prepare.py`:

```python
import uuid

from hello_agents.memory.rag.contracts import DocumentSegment
from hello_agents.memory.rag.prepare import (
    PROJECT_POINT_NAMESPACE_UUID,
    canonical_json,
    prepare_document_chunks,
)


def test_project_point_namespace_uuid_is_fixed():
    assert PROJECT_POINT_NAMESPACE_UUID == uuid.UUID("c273c00a-40ac-47a9-b475-164f135ada18")


def test_canonical_json_is_stable_and_unambiguous():
    assert canonical_json(["ns", "doc:1", 0]) == '["ns","doc:1",0]'
    assert canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'


def test_prepare_document_chunks_assigns_global_chunk_indexes_and_metadata():
    segments = [
        DocumentSegment(content="alpha beta", metadata={"page_number": 1, "file_name": "a.pdf"}),
        DocumentSegment(content="gamma", metadata={"page_number": 2, "file_name": "a.pdf"}),
    ]

    def split_text(text: str) -> list[str]:
        return text.split()

    def embed_text(text: str) -> list[float]:
        return [float(len(text)), 0.0]

    chunks = prepare_document_chunks(
        document_id="doc-1",
        segments=segments,
        rag_namespace="ns-1",
        split_text=split_text,
        embed_text=embed_text,
    )

    assert [chunk.content for chunk in chunks] == ["alpha", "beta", "gamma"]
    assert [chunk.metadata["chunk_index"] for chunk in chunks] == [0, 1, 2]
    assert [chunk.metadata["page_number"] for chunk in chunks] == [1, 1, 2]
    assert all(chunk.document_id == "doc-1" for chunk in chunks)
    assert all(chunk.metadata["rag_namespace"] == "ns-1" for chunk in chunks)
    assert all(chunk.metadata["document_id"] == "doc-1" for chunk in chunks)
    assert all(chunk.metadata["content"] == chunk.content for chunk in chunks)
```

- [ ] **Step 5: Run preparation tests to verify they fail**

Run: `python -m pytest tests/memory/rag/test_errors_and_prepare.py -v`

Expected: FAIL with missing `contracts` and `prepare` imports.

- [ ] **Step 6: Implement contracts and preparation helpers**

Create `hello_agents/memory/rag/contracts.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DocumentSegment:
    content: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class PreparedChunk:
    id: str
    document_id: str
    content: str
    vector: list[float]
    metadata: dict[str, Any]
```

Create `hello_agents/memory/rag/prepare.py`:

```python
from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

from hello_agents.memory.rag.contracts import DocumentSegment, PreparedChunk


PROJECT_POINT_NAMESPACE_UUID = uuid.UUID("c273c00a-40ac-47a9-b475-164f135ada18")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def default_chunk_id(rag_namespace: str, document_id: str, chunk_index: int) -> str:
    return f"{document_id}_{chunk_index}"


def qdrant_point_id(rag_namespace: str, document_id: str, chunk_index: int) -> str:
    point_name = canonical_json([rag_namespace, document_id, chunk_index])
    return str(uuid.uuid5(PROJECT_POINT_NAMESPACE_UUID, point_name))


def json_safe_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [json_safe_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_safe_value(item) for key, item in value.items()}
    return str(value)


def json_safe_dict(data: dict[str, Any]) -> dict[str, Any]:
    return {str(key): json_safe_value(value) for key, value in data.items()}


def normalize_vector(vector: Any) -> list[float]:
    if hasattr(vector, "tolist"):
        vector = vector.tolist()
    if isinstance(vector, list) and vector and isinstance(vector[0], list):
        vector = vector[0]
    return [float(item) for item in vector]


def prepare_document_chunks(
    document_id: str,
    segments: Sequence[DocumentSegment],
    rag_namespace: str,
    split_text: Callable[[str], list[str]],
    embed_text: Callable[[str], list[float]],
    id_for_chunk: Callable[[str, str, int], str] | None = None,
) -> list[PreparedChunk]:
    if not document_id:
        raise ValueError("document_id is required")

    id_for_chunk = id_for_chunk or default_chunk_id
    prepared: list[PreparedChunk] = []
    chunk_index = 0

    for segment in segments:
        if not segment.content or not segment.content.strip():
            continue

        segment_metadata = json_safe_dict(segment.metadata or {})
        for chunk_text in split_text(segment.content):
            chunk_text = str(chunk_text).strip()
            if not chunk_text:
                continue

            chunk_id = id_for_chunk(rag_namespace, document_id, chunk_index)
            metadata = {
                "memory_id": chunk_id,
                "document_id": document_id,
                "chunk_index": chunk_index,
                "content": chunk_text,
                "memory_type": "rag_chunk",
                "is_rag_data": True,
                "data_source": "rag_pipeline",
                "rag_namespace": rag_namespace,
                "created_at": datetime.now().isoformat(),
                **segment_metadata,
            }

            prepared.append(
                PreparedChunk(
                    id=chunk_id,
                    document_id=document_id,
                    content=chunk_text,
                    vector=normalize_vector(embed_text(chunk_text)),
                    metadata=json_safe_dict(metadata),
                )
            )
            chunk_index += 1

    return prepared
```

Modify `hello_agents/memory/rag/__init__.py`:

```python
from hello_agents.memory.rag.contracts import DocumentSegment, PreparedChunk
from hello_agents.memory.rag.errors import (
    RAGBackendError,
    RAGCollectionError,
    RAGConfigError,
    RAGConnectionError,
    RAGDocumentTooLargeError,
    RAGOperationError,
)

__all__ = [
    "DocumentSegment",
    "PreparedChunk",
    "RAGBackendError",
    "RAGCollectionError",
    "RAGConfigError",
    "RAGConnectionError",
    "RAGDocumentTooLargeError",
    "RAGOperationError",
]
```

- [ ] **Step 7: Run tests to verify Task 1 passes**

Run: `python -m pytest tests/memory/rag/test_errors_and_prepare.py -v`

Expected: PASS.

- [ ] **Step 8: Commit Task 1**

```bash
git add hello_agents/memory/rag/errors.py hello_agents/memory/rag/contracts.py hello_agents/memory/rag/prepare.py hello_agents/memory/rag/__init__.py tests/memory/rag/test_errors_and_prepare.py
git commit -m "feat: add rag backend contracts and preparation"
```

### Task 2: JSON Pipeline Contract and Whole-Document Replace

**Files:**
- Modify: `hello_agents/memory/rag/pipeline.py`
- Test: `tests/memory/rag/test_json_pipeline_contract.py`

**Interfaces:**
- Consumes: `DocumentSegment`, `PreparedChunk`, `prepare_document_chunks`, `default_chunk_id`
- Produces: `SimpleRAGPipeline.replace_document(document_id: str, segments: list[DocumentSegment], save_cache: bool = True) -> dict[str, Any]`
- Keeps: `SimpleRAGPipeline.add_text(..., replace_existing: bool = True, save_cache: bool = True) -> dict[str, Any]`

- [ ] **Step 1: Write failing tests for JSON `replace_document` metadata and indexes**

Create `tests/memory/rag/test_json_pipeline_contract.py`:

```python
from hello_agents.memory.rag.contracts import DocumentSegment
from hello_agents.memory.rag.pipeline import SimpleRAGPipeline


def test_json_replace_document_uses_global_indexes_and_preserves_page_metadata(tmp_path):
    pipeline = SimpleRAGPipeline(
        collection_name="c",
        rag_namespace="ns",
        cache_path=str(tmp_path / "rag.json"),
    )

    result = pipeline.replace_document(
        "doc-1",
        [
            DocumentSegment("alpha beta", {"page_number": 1, "file_name": "a.pdf"}),
            DocumentSegment("gamma", {"page_number": 2, "file_name": "a.pdf"}),
        ],
    )

    assert result["success"] is True
    assert result["document_id"] == "doc-1"
    assert result["chunks_added"] == 2
    assert [chunk["metadata"]["chunk_index"] for chunk in pipeline.chunks] == [0, 1]
    assert [chunk["metadata"]["page_number"] for chunk in pipeline.chunks] == [1, 2]
    assert pipeline.chunks[0]["metadata"]["rag_namespace"] == "ns"


def test_json_replace_document_removes_old_orphan_chunks(tmp_path):
    pipeline = SimpleRAGPipeline(
        collection_name="c",
        rag_namespace="ns",
        cache_path=str(tmp_path / "rag.json"),
    )
    pipeline.add_text("first\n\nsecond\n\nthird", document_id="doc-1")

    result = pipeline.replace_document("doc-1", [DocumentSegment("new only", {})])

    assert result["success"] is True
    assert result["chunks_removed"] >= 1
    assert [chunk["content"] for chunk in pipeline.chunks if chunk["document_id"] == "doc-1"] == ["new only"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/memory/rag/test_json_pipeline_contract.py -v`

Expected: FAIL with `AttributeError: 'SimpleRAGPipeline' object has no attribute 'replace_document'`.

- [ ] **Step 3: Implement `replace_document` and route JSON preparation through shared helpers**

Modify `hello_agents/memory/rag/pipeline.py` imports:

```python
from hello_agents.memory.rag.contracts import DocumentSegment
from hello_agents.memory.rag.prepare import default_chunk_id, prepare_document_chunks
```

Add this method inside `SimpleRAGPipeline`:

```python
    def replace_document(
        self,
        document_id: str,
        segments: List[DocumentSegment],
        save_cache: bool = True,
    ) -> Dict[str, Any]:
        if not document_id:
            return {"success": False, "message": "document_id cannot be empty"}

        prepared = prepare_document_chunks(
            document_id=document_id,
            segments=segments,
            rag_namespace=self.rag_namespace,
            split_text=self._split_text,
            embed_text=self._to_vector,
            id_for_chunk=default_chunk_id,
        )

        before = len(self.chunks)
        self.chunks = [
            chunk
            for chunk in self.chunks
            if chunk.get("document_id") != document_id
        ]
        removed = before - len(self.chunks)

        for chunk in prepared:
            self.chunks.append(
                {
                    "id": chunk.id,
                    "document_id": chunk.document_id,
                    "content": chunk.content,
                    "vector": chunk.vector,
                    "metadata": chunk.metadata,
                }
            )

        if save_cache:
            self._save_cache()

        return {
            "success": True,
            "document_id": document_id,
            "chunks_added": len(prepared),
            "chunks_removed": removed,
            "cache_path": str(self.cache_path),
            "message": f"Replaced document {document_id} with {len(prepared)} chunks",
        }
```

Keep `add_text` behavior compatible by converting its input to one segment when `replace_existing=True`:

```python
        if replace_existing:
            return self.replace_document(
                document_id=document_id,
                segments=[DocumentSegment(content=text, metadata=metadata)],
                save_cache=save_cache,
            )
```

For the append path, keep the existing `existing_count` index behavior and continue using `_save_cache()` only when `save_cache` is true.

- [ ] **Step 4: Add restart persistence and search isolation regression tests**

Append to `tests/memory/rag/test_json_pipeline_contract.py`:

```python
def test_json_cache_restart_restores_replaced_document(tmp_path):
    cache_path = tmp_path / "rag.json"
    first = SimpleRAGPipeline(collection_name="c", rag_namespace="ns", cache_path=str(cache_path))
    first.replace_document("doc-1", [DocumentSegment("persistent text", {"file_name": "a.txt"})])

    second = SimpleRAGPipeline(collection_name="c", rag_namespace="ns", cache_path=str(cache_path))

    assert second.stats()["document_count"] == 1
    assert second.search("persistent", document_id="doc-1")


def test_json_search_respects_document_id_filter(tmp_path):
    pipeline = SimpleRAGPipeline(collection_name="c", rag_namespace="ns", cache_path=str(tmp_path / "rag.json"))
    pipeline.replace_document("doc-a", [DocumentSegment("apple", {})])
    pipeline.replace_document("doc-b", [DocumentSegment("banana", {})])

    results = pipeline.search("banana", document_id="doc-a")

    assert all(item["metadata"]["document_id"] == "doc-a" for item in results)
```

- [ ] **Step 5: Run JSON contract tests**

Run: `python -m pytest tests/memory/rag/test_json_pipeline_contract.py -v`

Expected: PASS.

- [ ] **Step 6: Commit Task 2**

```bash
git add hello_agents/memory/rag/pipeline.py tests/memory/rag/test_json_pipeline_contract.py
git commit -m "feat: add json rag replace document contract"
```

### Task 3: Backend Configuration and Factory Selection

**Files:**
- Modify: `requirements.txt`
- Modify: `hello_agents/memory/rag/pipeline.py`
- Create: `tests/memory/rag/test_factory_config.py`

**Interfaces:**
- Consumes: `RAGConfigError`
- Produces: `create_rag_pipeline(..., backend: str | None = None, qdrant_client: Any | None = None, **kwargs) -> Any`
- Produces: `resolve_rag_backend(backend: str | None = None) -> str`
- Produces: `resolve_qdrant_collection(collection_name: str | None = None) -> str`

- [ ] **Step 1: Add dependencies**

Modify `requirements.txt` so it includes:

```text
qdrant-client==1.18.0
pytest==8.4.1
```

- [ ] **Step 2: Write failing tests for backend selection**

Create `tests/memory/rag/test_factory_config.py`:

```python
import pytest

from hello_agents.memory.rag.errors import RAGConfigError
from hello_agents.memory.rag.pipeline import SimpleRAGPipeline, create_rag_pipeline


def test_factory_defaults_to_json(monkeypatch, tmp_path):
    monkeypatch.delenv("RAG_BACKEND", raising=False)

    pipeline = create_rag_pipeline(cache_path=str(tmp_path / "rag.json"))

    assert isinstance(pipeline, SimpleRAGPipeline)


def test_factory_rejects_invalid_backend(monkeypatch):
    monkeypatch.setenv("RAG_BACKEND", "sqlite")

    with pytest.raises(RAGConfigError, match="Unsupported RAG_BACKEND"):
        create_rag_pipeline()


def test_qdrant_backend_requires_url(monkeypatch):
    monkeypatch.setenv("RAG_BACKEND", "qdrant")
    monkeypatch.delenv("QDRANT_URL", raising=False)

    with pytest.raises(RAGConfigError, match="QDRANT_URL is required"):
        create_rag_pipeline()


def test_qdrant_collection_env_overrides_constructor(monkeypatch):
    captured = {}

    class StubQdrantPipeline:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setenv("RAG_BACKEND", "qdrant")
    monkeypatch.setenv("QDRANT_URL", "http://localhost:6333")
    monkeypatch.setenv("QDRANT_COLLECTION", "from_env")
    monkeypatch.setattr("hello_agents.memory.rag.pipeline.QdrantRAGPipeline", StubQdrantPipeline)

    create_rag_pipeline(collection_name="from_constructor", rag_namespace="ns")

    assert captured["collection_name"] == "from_env"
    assert captured["rag_namespace"] == "ns"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/memory/rag/test_factory_config.py -v`

Expected: FAIL because `RAGConfigError` is not used by the factory and `QdrantRAGPipeline` import target does not exist.

- [ ] **Step 4: Implement factory selection with lazy Qdrant import**

Modify `hello_agents/memory/rag/pipeline.py`:

```python
import os

from hello_agents.memory.rag.errors import RAGConfigError


QdrantRAGPipeline = None


def resolve_rag_backend(backend: Optional[str] = None) -> str:
    value = (backend or os.getenv("RAG_BACKEND") or "json").strip().lower()
    if value not in {"json", "qdrant"}:
        raise RAGConfigError(f"Unsupported RAG_BACKEND: {value}")
    return value


def resolve_qdrant_collection(collection_name: Optional[str] = None) -> str:
    return (os.getenv("QDRANT_COLLECTION") or collection_name or "rag_knowledge_base").strip()
```

Replace `create_rag_pipeline` body:

```python
def create_rag_pipeline(
    qdrant_url: Optional[str] = None,
    qdrant_api_key: Optional[str] = None,
    collection_name: str = "rag_knowledge_base",
    rag_namespace: str = "default",
    cache_path: Optional[str] = None,
    backend: Optional[str] = None,
    qdrant_client: Any = None,
    **kwargs
) -> Any:
    selected_backend = resolve_rag_backend(backend)

    if selected_backend == "json":
        return SimpleRAGPipeline(
            collection_name=collection_name,
            rag_namespace=rag_namespace,
            qdrant_url=qdrant_url,
            qdrant_api_key=qdrant_api_key,
            cache_path=cache_path,
        )

    resolved_url = qdrant_url or os.getenv("QDRANT_URL")
    if not resolved_url:
        raise RAGConfigError("QDRANT_URL is required when RAG_BACKEND=qdrant")

    resolved_key = qdrant_api_key or os.getenv("QDRANT_API_KEY") or None
    resolved_collection = resolve_qdrant_collection(collection_name)

    global QdrantRAGPipeline
    if QdrantRAGPipeline is None:
        from hello_agents.memory.rag.qdrant_pipeline import QdrantRAGPipeline as ImportedQdrantRAGPipeline

        QdrantRAGPipeline = ImportedQdrantRAGPipeline

    return QdrantRAGPipeline(
        collection_name=resolved_collection,
        rag_namespace=rag_namespace,
        qdrant_url=resolved_url,
        qdrant_api_key=resolved_key,
        qdrant_client=qdrant_client,
        **kwargs,
    )
```

- [ ] **Step 5: Create a temporary minimal Qdrant pipeline shell for factory tests**

Create `hello_agents/memory/rag/qdrant_pipeline.py`:

```python
from __future__ import annotations


class QdrantRAGPipeline:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
```

This shell is replaced by the full implementation in Task 5. It exists so Task 3 has a runnable, independently testable factory.

- [ ] **Step 6: Run factory tests**

Run: `python -m pytest tests/memory/rag/test_factory_config.py -v`

Expected: PASS.

- [ ] **Step 7: Commit Task 3**

```bash
git add requirements.txt hello_agents/memory/rag/pipeline.py hello_agents/memory/rag/qdrant_pipeline.py tests/memory/rag/test_factory_config.py
git commit -m "feat: add rag backend factory selection"
```

## File Structure

- Create `hello_agents/memory/rag/errors.py`: stable backend exception hierarchy and safe URL sanitization.
- Create `hello_agents/memory/rag/contracts.py`: shared dataclasses for `DocumentSegment`, `PreparedChunk`, and backend protocol-style return shapes.
- Create `hello_agents/memory/rag/prepare.py`: canonical JSON, deterministic point-name helpers, metadata normalization, document splitting, embedding, and chunk preparation.
- Modify `hello_agents/memory/rag/pipeline.py`: keep `SimpleRAGPipeline`, add `replace_document`, route JSON splitting through shared preparation, and update `create_rag_pipeline` to select JSON or Qdrant from configuration.
- Create `hello_agents/memory/rag/qdrant_pipeline.py`: Qdrant implementation of the RAG backend contract.
- Replace `hello_agents/memory/storage/qdrant_store.py`: real Qdrant storage adapter with injectable client, collection management, filters, retry/backoff, and sanitized errors.
- Modify `hello_agents/memory/rag/__init__.py`: export shared contracts and errors used by tools and tests.
- Modify `hello_agents/memory/storage/__init__.py`: export the Qdrant store adapter if needed by tests or package imports.
- Create `hello_agents/tools/builtin/rag_result.py`: `RAGActionResult` dataclass and text-format helpers.
- Modify `hello_agents/tools/builtin/rag_tool.py`: add `execute_result()`, refactor document parsing into ordered segments, preserve `execute()` text compatibility, and remove private backend method calls.
- Modify `assistants/pdf_learning_assistant.py`: use `execute_result()` for import/delete/clear and update local state only on successful backend mutation.
- Modify `requirements.txt`: add `qdrant-client==1.18.0` and `pytest==8.4.1`.
- Create `tests/conftest.py`: shared temporary directories, monkeypatch helpers, and fake sleep fixture.
- Create `tests/fakes/fake_qdrant.py`: in-memory fake client that records collection, upsert, delete, scroll, count, and search calls.
- Create `tests/memory/rag/test_errors_and_prepare.py`: exception, sanitizer, canonical JSON, and shared preparation tests.
- Create `tests/memory/rag/test_json_pipeline_contract.py`: JSON backend contract and persistence regression tests.
- Create `tests/memory/rag/test_factory_config.py`: backend selection and configuration tests.
- Create `tests/memory/storage/test_qdrant_store.py`: store adapter collection, retry, filtering, sanitization, and batch-operation tests.
- Create `tests/memory/rag/test_qdrant_pipeline.py`: Qdrant pipeline behavior tests using the fake store/client.
- Create `tests/tools/test_rag_tool_results.py`: `RAGTool` structured result and document import tests.
- Create `tests/assistants/test_pdf_learning_assistant_state.py`: Assistant state safety tests for backend failures.
- Modify `README.md`: document backend selection environment variables and manual Qdrant verification commands.

## Implementation Notes

- Keep new files focused; do not move unrelated memory, UI, or assistant code.
- Existing source files contain mojibake text in comments and messages. Do not perform a broad encoding rewrite in this feature branch.
- The implementation may improve new user-facing messages for new code paths, but existing string snapshots should not be introduced unless tests assert only stable data fields.
- Use dependency injection for Qdrant clients in tests. Do not start Docker from automated tests.

---
