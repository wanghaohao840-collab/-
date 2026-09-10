# BGE-M3 Bounded Source Reader Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver E3-A1: a bounded, resumable, read-only Qdrant page boundary for source inventory and rebuilding, without changing live retrieval or activating a model.

**Architecture:** Keep the existing accumulating `VectorStore.scroll()` contract intact. Add migration-specific page methods only to `QdrantVectorStore`, using a small storage-local module for typed pages, native-ID validation, response checks and finite traversal. Inventory ownership/digests and durable checkpoints consume this interface in subsequent E3 units; a page or partial scan is never an accepted inventory.

**Tech Stack:** Existing Python venv, dataclasses, UUID, pytest and qdrant-client 1.18.0. No new dependency or external service.

## Global Constraints

- Approved specification: `docs/superpowers/specs/2026-09-03-bge-m3-rag-embedding-design.md`, especially sections 7–9.
- Reuse isolated `codex/bge-m3-runtime-identity` at `D:\python_self_agent\.worktrees\bge-m3-runtime-identity`, base `a33b071`. Preserve untracked `output/` reports and all stable-checkout work.
- User chose serial inline implementation; no subagents. The named executing-plans skill is unavailable; execute the reviewed packet inline with explicit test/review gates.
- Do not alter the embedding model, chunking, Memory, existing public scroll behavior, schemas, active registry, real environment, Windows tasks, containers or production data.
- No network embeddings, live Neo4j/Qdrant, collection creation/deletion or content logging in this unit. The test-only embedded Qdrant client stores synthetic records in memory.
- One successful page performs exactly one scroll request; existing bounded transport retries remain. Payload-only pages default to 128, accept 1–256, and carry physical IDs separately from logical `_vector_store_id`.
- Native offsets preserve integer zero and UUID values; no string coercion of integer cursors. Traversal is lazy with default 10,000 pages, explicit range 1–1,000,000 and cycle detection.
- A page-size/count bound is not a byte cap on a hostile SDK response, a snapshot or a source-ownership audit. E3-A2 must add record validation, disk-backed identity/digest inventory and source stability checks under the E3 maintenance controller.

## E3 delivery sequence and unchanged release gate

1. **E3-A1 (this plan):** bounded Qdrant page reading, cancellation-by-stopping-consumption, native cursor handoff and malformed-response protection.
2. **E3-A2:** streaming JSON adapter and disk-backed source inventory, authoritative namespace/document ownership, original chunk/source/version retention, canonical per-partition digests, duplicate detection and source-change refusal. No production source is assumed empty.
3. **E3-B:** separate candidate manifest, idempotent bounded re-embedding and checkpoints, resumed-source digest verification, target identity/count/provenance audit. Active registry remains unchanged on staging failure.
4. **E3-C:** shared Windows maintenance lock including health/login recovery, consistent cold backup, crash-persistent maintenance state, paired environment/registry cutover journal and recovery, new-user/empty index initialization, safe rollback refusing stale-index revival after writes.
5. **E3-D:** at least 20 fixed public/synthetic retrieval cases (10 Chinese/10 English), Recall@5 >= 0.90, MRR@5 >= 0.75 and no worse than simple, zero scope leakage; per-run isolated managed deep smoke; final stable integration and real verification. Do not label embedding production-ready before this gate.

Subsequent units receive detailed code plans when their interfaces exist; this plan claims completion only for E3-A1, not the whole E3 sequence.

---

### Task 1: Implement and verify the bounded source reader

**Files:**
- Create: `hello_agents/memory/storage/vector_scan.py` — typed page and strict traversal helpers.
- Modify: `hello_agents/memory/storage/vector_store.py` — imports and two additive Qdrant methods only.
- Create: `tests/memory/storage/test_qdrant_scan.py` — fake transport and embedded-client acceptance.
- Record: this plan, its review/packet/final review under `docs/agent-workflow/task-packets/2026-09-03-bge-m3-bounded-source-reader/`, and the E3 progress paragraph in the governing spec.

**Interfaces:**
- Consumes: `QdrantVectorStore._call(operation, func, *args, **kwargs)` for retries/error mapping; `_filter(filters)` for scope predicates; `client.scroll(...)` returning `(records, next_offset)`.
- Produces: `VectorScanRecord(storage_id, logical_id, payload)`; `VectorScanPage(records, next_offset)`; `VectorScanError(code)`; `scroll_page(collection_name, filters=None, *, offset=None, page_size=128)`; `iter_scroll_pages(collection_name, filters=None, *, offset=None, page_size=128, max_pages=10000)`.
- Scope filters are copied at traversal start. Returned payloads are independent copies, not aliases to SDK records. Ownership validation across returned documents remains the inventory layer's responsibility.

- [x] **Step 1: Add the failing tests**

Create `tests/memory/storage/test_qdrant_scan.py` with:

```python
from types import SimpleNamespace
import uuid

import pytest

from hello_agents.memory.rag.errors import RAGAuthenticationError, RAGConnectionError
from hello_agents.memory.storage.vector_scan import VectorScanError
from hello_agents.memory.storage.vector_store import QdrantVectorStore


def raw(identifier, logical=None):
    payload = {"rag_namespace": "user-a", "metadata": {"nested": ["kept"]}}
    if logical is not None:
        payload["_vector_store_id"] = logical
    return SimpleNamespace(id=identifier, payload=payload, vector=[999.0])


class Client:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def scroll(self, **kwargs):
        self.calls.append(kwargs)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def store_for(responses, **kwargs):
    client = Client(responses)
    return QdrantVectorStore(client=client, retry_delays=(), **kwargs), client


def test_single_page_is_bounded_and_preserves_both_ids_and_native_zero_cursor():
    row = raw(0, "logical-zero")
    store, client = store_for([([row], 7)])
    page = store.scroll_page("source", {"rag_namespace": "user-a"}, offset=0, page_size=1)
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["offset"] == 0 and type(call["offset"]) is int
    assert call["limit"] == 1
    assert call["with_vectors"] is False and call["with_payload"] is True
    assert call["scroll_filter"].must[0].match.value == "user-a"
    assert page.next_offset == 7
    assert page.records[0].storage_id == 0
    assert page.records[0].logical_id == "logical-zero"
    page.records[0].payload["metadata"]["nested"].append("changed")
    assert row.payload["metadata"]["nested"] == ["kept"]
    assert not hasattr(page.records[0], "vector")


def test_iteration_is_lazy_and_can_resume_from_checkpoint():
    key = str(uuid.uuid4())
    store, client = store_for([([raw(0)], key), ([raw(key)], None)])
    pages = store.iter_scroll_pages("source", page_size=1)
    assert client.calls == []
    first = next(pages)
    assert len(client.calls) == 1 and first.next_offset == key
    pages.close()
    assert len(client.calls) == 1
    resumed = list(store.iter_scroll_pages("source", offset=first.next_offset, page_size=1))
    assert len(resumed) == 1 and resumed[0].records[0].storage_id == key
    assert client.calls[1]["offset"] == key


def test_iteration_freezes_scope_after_first_page():
    store, client = store_for([([raw(0)], 1), ([raw(1)], None)])
    filters = {"rag_namespace": ["user-a"]}
    pages = store.iter_scroll_pages("source", filters)
    next(pages)
    filters["rag_namespace"].append("user-b")
    next(pages)
    assert client.calls[1]["scroll_filter"].must[0].match.any == ["user-a"]


@pytest.mark.parametrize("size", [0, -1, 257, True, 1.0, "1"])
def test_invalid_page_size_makes_no_remote_call(size):
    store, client = store_for([])
    with pytest.raises(VectorScanError, match="page_size"):
        store.scroll_page("source", page_size=size)
    assert client.calls == []


@pytest.mark.parametrize("offset", [True, -1, 2**64, 1.5, "private-invalid", {}, []])
def test_invalid_cursor_is_rejected_without_exposing_value(offset):
    store, client = store_for([])
    with pytest.raises(VectorScanError) as caught:
        store.scroll_page("source", offset=offset)
    assert "private-invalid" not in str(caught.value)
    assert client.calls == []


@pytest.mark.parametrize("response,code", [
    (None, "response"),
    (([],), "response"),
    (([raw(1), raw(2)], None), "page_size"),
    (([], 5), "cursor"),
    (([raw(1)], True), "point_id"),
    (([SimpleNamespace(id=1, payload=None)], None), "payload"),
    (([raw(True)], None), "point_id"),
    (([raw(1, "")], None), "logical_id"),
    (([raw(1, "private\nvalue")], None), "logical_id"),
])
def test_malformed_response_fails_closed(response, code):
    store, _ = store_for([response])
    with pytest.raises(VectorScanError, match=code):
        store.scroll_page("source", page_size=1)


def test_duplicate_physical_ids_are_rejected_even_with_different_logical_ids():
    store, _ = store_for([([raw(1, "a"), raw(1, "b")], None)])
    with pytest.raises(VectorScanError, match="duplicate_point"):
        store.scroll_page("source")


def test_uuid_objects_and_strings_remain_distinct_from_integer_ids():
    key = uuid.uuid4()
    store, _ = store_for([([raw(key)], key), ([raw(0)], None)])
    first = store.scroll_page("source")
    assert first.records[0].storage_id == str(key)
    assert first.next_offset == str(key)
    assert store.scroll_page("source", offset=first.next_offset).records[0].storage_id == 0


def test_repeated_cursor_and_multi_page_cycle_are_errors():
    store, _ = store_for([([raw(0)], 0)])
    with pytest.raises(VectorScanError, match="cursor"):
        store.scroll_page("source", offset=0)
    store, client = store_for([([raw(0)], 1), ([raw(1)], 2), ([raw(2)], 1)])
    pages = store.iter_scroll_pages("source")
    next(pages)
    next(pages)
    with pytest.raises(VectorScanError, match="cursor"):
        next(pages)
    assert len(client.calls) == 3


@pytest.mark.parametrize("maximum", [0, -1, True, 1.0, 1_000_001])
def test_invalid_page_budget_makes_no_remote_call(maximum):
    store, client = store_for([])
    with pytest.raises(VectorScanError, match="max_pages"):
        list(store.iter_scroll_pages("source", max_pages=maximum))
    assert client.calls == []


def test_page_budget_does_not_silently_validate_a_truncated_inventory():
    store, client = store_for([([raw(0)], 1)])
    pages = store.iter_scroll_pages("source", max_pages=1)
    assert next(pages).next_offset == 1
    with pytest.raises(VectorScanError, match="page_limit"):
        next(pages)
    assert len(client.calls) == 1


def test_terminal_empty_page_is_valid_even_at_budget_boundary():
    store, _ = store_for([([], None)])
    assert list(store.iter_scroll_pages("source", max_pages=1))[0].records == ()


def test_transport_failure_after_a_page_is_not_hidden_as_end_of_scan():
    store, client = store_for([([raw(0)], 1), TimeoutError("timed out")])
    pages = store.iter_scroll_pages("source")
    next(pages)
    with pytest.raises(RAGConnectionError):
        next(pages)
    assert len(client.calls) == 2


def test_authentication_error_uses_existing_sanitization():
    error = RuntimeError("api_key=private-key")
    error.status_code = 401
    store, _ = store_for([error], api_key="private-key")
    with pytest.raises(RAGAuthenticationError) as caught:
        store.scroll_page("source")
    assert "private-key" not in str(caught.value)


def test_uuid_spelling_variants_cannot_hide_cursor_cycles_or_duplicate_records():
    key = uuid.uuid4()
    store, _ = store_for([([raw(0)], key.hex.upper())])
    with pytest.raises(VectorScanError, match="cursor"):
        store.scroll_page("source", offset=str(key))
    store, _ = store_for([([raw(str(key)), raw(key.hex)], None)])
    with pytest.raises(VectorScanError, match="duplicate_point"):
        store.scroll_page("source")
    store, _ = store_for([([raw(0)], str(key)), ([raw(1)], 2), ([raw(2)], key.hex)])
    pages = store.iter_scroll_pages("source")
    next(pages)
    next(pages)
    with pytest.raises(VectorScanError, match="cursor"):
        next(pages)


def test_native_unsigned_integer_upper_bound_is_preserved():
    identifier = 2**64 - 1
    store, client = store_for([([raw(identifier)], None)])
    page = store.scroll_page("source", offset=identifier)
    assert type(client.calls[0]["offset"]) is int
    assert page.records[0].storage_id == identifier
    assert page.records[0].logical_id == str(identifier)


def test_page_retries_keep_the_same_cursor_and_scope():
    client = Client([TimeoutError("transient"), ([raw(7)], None)])
    store = QdrantVectorStore(client=client, retry_delays=(0,))
    page = store.scroll_page("source", {"rag_namespace": "user-a"}, offset=7, page_size=1)
    assert page.records[0].storage_id == 7
    assert len(client.calls) == 2
    assert client.calls[0] == client.calls[1]


@pytest.mark.parametrize("logical_id", [None, 4, "x" * 513, "secret" + chr(127)])
def test_present_invalid_logical_id_is_not_replaced_by_physical_fallback(logical_id):
    row = raw(1)
    row.payload["_vector_store_id"] = logical_id
    store, _ = store_for([([row], None)])
    with pytest.raises(VectorScanError) as caught:
        store.scroll_page("source")
    assert caught.value.code == "logical_id"
    assert str(caught.value) == "Qdrant scan rejected: logical_id"


def test_embedded_qdrant_round_trip_without_a_service():
    from qdrant_client import QdrantClient, models

    client = QdrantClient(location=":memory:")
    try:
        client.create_collection("source", vectors_config=models.VectorParams(size=2, distance="Cosine"))
        client.upsert("source", points=[
            models.PointStruct(id=i, vector=[1.0, 0.0], payload={
                "_vector_store_id": f"doc-{i}", "rag_namespace": "user-a" if i < 5 else "user-b"
            }) for i in range(7)
        ])
        store = QdrantVectorStore(client=client, retry_delays=())
        pages = list(store.iter_scroll_pages("source", {"rag_namespace": "user-a"}, page_size=2))
        assert [record.storage_id for page in pages for record in page.records] == list(range(5))
        assert [record.logical_id for page in pages for record in page.records] == [
            f"doc-{i}" for i in range(5)
        ]
        assert all(len(page.records) <= 2 for page in pages)
        assert pages[-1].next_offset is None
        assert client.count("source").count == 7
    finally:
        client.close()

```

- [x] **Step 2: Confirm the red state**

Run from the isolated worktree:

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest tests/memory/storage/test_qdrant_scan.py -q --tb=short
```

Expected: collection fails because `vector_scan` does not exist.

- [x] **Step 3: Add the storage-local page implementation**

Create `hello_agents/memory/storage/vector_scan.py` with:

```python
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Callable, Iterator
import uuid

from hello_agents.memory.rag.errors import RAGOperationError


NativePointId = int | str


class VectorScanError(RAGOperationError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(
            f"Qdrant scan rejected: {code}", operation="scroll", retryable=False
        )


@dataclass(frozen=True)
class VectorScanRecord:
    # Keep transport identity separate from the application's logical ID.
    storage_id: NativePointId
    logical_id: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class VectorScanPage:
    records: tuple[VectorScanRecord, ...]
    next_offset: NativePointId | None


def native_point_id(value: object) -> NativePointId:
    if type(value) is int and 0 <= value <= 2**64 - 1:
        return value
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, str):
        try:
            uuid.UUID(value)
        except (ValueError, AttributeError):
            pass
        else:
            return value
    raise VectorScanError("point_id")


def cursor_key(value: NativePointId) -> tuple[str, int]:
    # UUID textual variants describe the same physical position.
    return ("int", value) if type(value) is int else ("uuid", uuid.UUID(value).int)


def read_qdrant_page(
    fetch: Callable[..., Any],
    *,
    collection_name: str,
    scroll_filter: Any,
    offset: NativePointId | None,
    page_size: int,
) -> VectorScanPage:
    if type(page_size) is not int or not 1 <= page_size <= 256:
        raise VectorScanError("page_size")
    if offset is not None:
        offset = native_point_id(offset)
    response = fetch(
        collection_name=collection_name,
        scroll_filter=scroll_filter,
        offset=offset,
        limit=page_size,
        with_payload=True,
        with_vectors=False,
    )
    if not isinstance(response, (tuple, list)) or len(response) != 2:
        raise VectorScanError("response")
    batch, next_offset = response
    if not isinstance(batch, (tuple, list)) or len(batch) > page_size:
        raise VectorScanError("page_size")
    if next_offset is not None:
        next_offset = native_point_id(next_offset)
        if not batch or (
            offset is not None and cursor_key(next_offset) == cursor_key(offset)
        ):
            raise VectorScanError("cursor")
    records = []
    seen = set()
    for raw in batch:
        storage_id = native_point_id(getattr(raw, "id", None))
        key = cursor_key(storage_id)
        if key in seen:
            raise VectorScanError("duplicate_point")
        seen.add(key)
        payload = getattr(raw, "payload", None)
        if not isinstance(payload, dict):
            raise VectorScanError("payload")
        logical_id = payload.get("_vector_store_id", str(storage_id))
        if (
            not isinstance(logical_id, str)
            or not logical_id
            or len(logical_id) > 512
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in logical_id)
        ):
            raise VectorScanError("logical_id")
        try:
            payload = copy.deepcopy(payload)
        except (TypeError, ValueError, RecursionError):
            raise VectorScanError("payload") from None
        records.append(VectorScanRecord(storage_id, logical_id, payload))
    return VectorScanPage(tuple(records), next_offset)


def iter_qdrant_pages(
    read: Callable[[NativePointId | None], VectorScanPage],
    *,
    offset: NativePointId | None,
    max_pages: int,
) -> Iterator[VectorScanPage]:
    if type(max_pages) is not int or not 1 <= max_pages <= 1_000_000:
        raise VectorScanError("max_pages")
    if offset is not None:
        offset = native_point_id(offset)
    seen = set() if offset is None else {cursor_key(offset)}
    for _ in range(max_pages):
        page = read(offset)
        next_offset = page.next_offset
        if next_offset is not None:
            key = cursor_key(next_offset)
            if key in seen:
                raise VectorScanError("cursor")
            seen.add(key)
        yield page
        if next_offset is None:
            return
        offset = next_offset
    # Reaching a bound with a continuation is failure, never a complete scan.
    raise VectorScanError("page_limit")

```

In `vector_store.py`, add `import copy`, add `Iterator` to the typing import, and add:

```python
from hello_agents.memory.storage.vector_scan import (
    NativePointId, VectorScanPage, iter_qdrant_pages, read_qdrant_page,
)
```

Insert the following methods in `QdrantVectorStore` before its existing `scroll` method. Do not insert them into `InMemoryVectorStore` or change the protocol:

```python
    def scroll_page(
        self,
        collection_name: str,
        filters: Optional[VectorFilter] = None,
        *,
        offset: NativePointId | None = None,
        page_size: int = 128,
    ) -> VectorScanPage:
        """Read one bounded payload page without creating or mutating a collection."""
        return read_qdrant_page(
            lambda **kwargs: self._call("scroll", self.client.scroll, **kwargs),
            collection_name=collection_name,
            scroll_filter=self._filter(filters),
            offset=offset,
            page_size=page_size,
        )

    def iter_scroll_pages(
        self,
        collection_name: str,
        filters: Optional[VectorFilter] = None,
        *,
        offset: NativePointId | None = None,
        page_size: int = 128,
        max_pages: int = 10_000,
    ) -> Iterator[VectorScanPage]:
        """Stream pages; no snapshot is implied and partial scans are not valid inventories."""
        # Freeze predicates when iteration begins; callers cannot change scope mid-scan.
        filters = copy.deepcopy(filters)
        yield from iter_qdrant_pages(
            lambda current: self.scroll_page(
                collection_name, filters, offset=current, page_size=page_size
            ),
            offset=offset,
            max_pages=max_pages,
        )


```

- [x] **Step 4: Verify green and compatibility**

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest tests/memory/storage/test_qdrant_scan.py tests/memory/storage/test_qdrant_vector_store.py tests/memory/storage/test_vector_store_contract.py tests/memory/rag/test_managed_qdrant_pipeline.py -q --tb=short --junitxml=output/e3-a1-focused.xml
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest tests/memory -q --tb=short --junitxml=output/e3-a1-memory.xml
& 'D:\python_self_agent\venv\Scripts\python.exe' -m compileall -q hello_agents/memory/storage tests/memory/storage
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pip check
git diff --check
```

Expected: all tests pass, no live service use, no compiled/dependency/diff errors. Verify the diff leaves `scroll()`, search, upsert, collection creation and the protocol unchanged.

- [x] **Step 5: Review combined delivery and record the outcome**

Use repository review and handoff templates. Inspect the actual producer/consumer signatures, strict cursor semantics, finite traversal, safe errors, scope forwarding and legacy compatibility. Record exact test evidence and residual migration work; final integration acceptance is limited to this reader unit. Follow the user's Git instructions; no push or stable merge is part of this task.

## Plan self-review

Delivery verified on 2026-09-03: 74 focused tests passed in 2.17s; 456 Memory/RAG tests passed in 9.61s (overlapping groups). Compile, dependency and diff checks pass. The final integration review accepts E3-A1 only. No production data/service/configuration changes, model activation, stable merge or Git commit were performed. Next: E3-A2 source inventory and streaming JSON.

Coverage: this unit supplies the bounded Qdrant read prerequisite in spec section 8 without claiming the rest of E3. JSON streaming, identity/digest inventory, re-embedding, Windows coordination, recovery, quality and production acceptance are explicitly assigned to the next sequential units above. Existing `scroll` callers retain their interface. No future interface is consumed by this packet. The baseline Qdrant adapter/managed-pipeline group passed 20 tests before edits.
