# SemanticMemory Qdrant Payload Indexes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `SemanticMemory` declare the Qdrant keyword indexes required by its `memory_type` and `user_id` filters.

**Architecture:** Keep index ownership in `SemanticMemory`, which owns the payload fields and filter behavior. Reuse the existing `VectorStore.ensure_payload_indexes(collection_name, indexes)` protocol so in-memory tests validate the declaration and Qdrant persists it.

**Tech Stack:** Python 3, pytest 8.4.1, qdrant-client 1.18.0, Qdrant 1.18.2

## Global Constraints

- Run Python and pytest through `.\venv\Scripts\python.exe`.
- Preserve the existing `VectorStore` public protocol and payload shape.
- Preserve `memory_type` and `user_id` data-isolation filters.
- Do not change `QdrantConnectionManager`, EpisodicMemory, or RAG index ownership.
- Preserve unrelated dirty-worktree changes.

---

### Task 1: Declare and verify SemanticMemory payload indexes

**Files:**
- Modify: `hello_agents/memory/types/semantic.py:55`
- Test: `tests/memory/test_semantic_vector_store_protocol.py`
- Test: `tests/integration/test_qdrant_document_scope.py`

**Interfaces:**
- Consumes: `VectorStore.ensure_payload_indexes(collection_name: str, indexes: Mapping[str, str]) -> None`
- Produces: `SemanticMemory.__init__` requests `{"memory_type": "keyword", "user_id": "keyword"}` for `self.vector_collection`

- [ ] **Step 1: Write the failing protocol test**

Add a recording `InMemoryVectorStore` subclass and assert that constructing
`SemanticMemory` records this exact call:

```python
("semantic", {"memory_type": "keyword", "user_id": "keyword"})
```

- [ ] **Step 2: Write the live Qdrant schema test**

Inject a real `QdrantVectorStore` into `SemanticMemory`, then assert Qdrant's
collection payload schema reports both `memory_type` and `user_id` as
`keyword`. Delete the unique test collection in `finally`.

- [ ] **Step 3: Run tests to verify the missing behavior**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests/memory/test_semantic_vector_store_protocol.py -q --basetemp=.runtime/pytest-semantic-index-red
```

Expected: FAIL because `SemanticMemory` has not requested payload indexes.

- [ ] **Step 4: Implement the minimal declaration**

Immediately after `ensure_collection`, add:

```python
self.vector_store.ensure_payload_indexes(
    self.vector_collection,
    {"memory_type": "keyword", "user_id": "keyword"},
)
```

- [ ] **Step 5: Run focused and live verification**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests/memory/test_semantic_vector_store_protocol.py tests/memory/test_semantic_fallback.py tests/memory/storage/test_qdrant_vector_store.py -q --basetemp=.runtime/pytest-semantic-indexes
powershell -ExecutionPolicy Bypass -File scripts\run_qdrant_integration.ps1
```

Expected: all focused tests pass and four live Qdrant integration tests pass.

- [ ] **Step 6: Run combined regression verification**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests/memory tests/tools/test_rag_tool_backend_contract.py tests/integration/test_qdrant_document_scope.py -q --basetemp=.runtime/pytest-semantic-final
```

Expected: all collected tests pass, with only the live Qdrant tests skipped
when `QDRANT_TEST_URL` is not set.

- [ ] **Step 7: Commit**

Commit only the five files owned by this plan, and only when the user has
explicitly authorized a commit:

```powershell
git add -- hello_agents/memory/types/semantic.py tests/memory/test_semantic_vector_store_protocol.py tests/integration/test_qdrant_document_scope.py docs/superpowers/specs/2026-08-01-semantic-memory-qdrant-indexes-design.md docs/superpowers/plans/2026-08-01-semantic-memory-qdrant-indexes.md
git commit -m "perf: index semantic memory qdrant filters"
```
