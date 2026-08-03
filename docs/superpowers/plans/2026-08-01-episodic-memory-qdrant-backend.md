# EpisodicMemory Qdrant Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect EpisodicMemory to the unified Qdrant-capable VectorStore with indexed user/session filters and scoped cleanup.

**Architecture:** Reuse the same collection-selection and connection-manager path as SemanticMemory, while keeping episode business fields and filter declarations inside EpisodicMemory. Replace legacy vector convenience calls with the public protocol and delete only instance-owned episode IDs.

**Tech Stack:** Python 3.12, pytest 8.4.1, qdrant-client 1.18.0, Qdrant 1.18.2, SQLite

## Global Constraints

- Use `.\venv\Scripts\python.exe` for all Python verification.
- Preserve the JSON/in-memory fallback when `qdrant_url` is absent.
- Preserve public methods, episode payloads, and SQLite persistence behavior.
- Preserve `memory_type`, `user_id`, and `session_id` data isolation.
- Never issue unfiltered deletion against a shared vector collection.
- Do not edit `QdrantConnectionManager`, vector-store implementations, RAG,
  SemanticMemory, or unrelated dirty files.

---

### Task 1: Unify EpisodicMemory vector persistence and isolation

**Files:**
- Modify: `hello_agents/memory/types/episodic.py`
- Modify: `tests/memory/test_episodic_vector_cleanup.py`
- Create: `tests/memory/test_episodic_vector_store_protocol.py`
- Modify: `tests/integration/test_qdrant_document_scope.py`

**Interfaces:**
- Consumes:
  - `QdrantConnectionManager.get_instance(**kwargs) -> VectorStore`
  - `VectorStore.ensure_collection(str, int) -> None`
  - `VectorStore.ensure_payload_indexes(str, Mapping[str, str]) -> None`
  - `VectorStore.upsert/search/delete_by_filter`
- Produces:
  - Episodic initialization requests keyword indexes on `memory_type`,
    `user_id`, and `session_id`
  - session-scoped vector queries
  - ID-scoped forget/clear operations

- [ ] **Step 1: Write failing protocol and isolation tests**

Create a recording in-memory store and assert initialization requests:

```python
(
    "episodic",
    {
        "memory_type": "keyword",
        "user_id": "keyword",
        "session_id": "keyword",
    },
)
```

Record `search()` filters and require:

```python
{
    "memory_type": "episodic",
    "user_id": "user-1",
    "session_id": "session-1",
}
```

Extend the cleanup test with a semantic point in the same collection and assert
it remains after `EpisodicMemory.clear()`.

- [ ] **Step 2: Run the new tests and verify the current behavior fails**

```powershell
.\venv\Scripts\python.exe -m pytest tests/memory/test_episodic_vector_store_protocol.py tests/memory/test_episodic_vector_cleanup.py -q --basetemp=.runtime/pytest-episodic-qdrant-red
```

Expected: failures show missing index assurance, missing session vector filter,
and unscoped clear behavior.

- [ ] **Step 3: Implement unified initialization**

Set `self.vector_collection`, obtain an injected store or
`QdrantConnectionManager` store, ensure the collection, and request:

```python
{
    "memory_type": "keyword",
    "user_id": "keyword",
    "session_id": "keyword",
}
```

- [ ] **Step 4: Use the VectorStore protocol**

Write `VectorPoint` through `upsert`, convert `VectorHit` results to the
existing dictionary shape, and replace legacy deletion calls with
`delete_by_filter`.

- [ ] **Step 5: Enforce scoped search and cleanup**

Pass optional `session_id` to `_vector_search` and include it in the equality
filter. Capture episode IDs before clearing local dictionaries and delete only
those IDs; when the set is empty, issue no remote deletion.

- [ ] **Step 6: Add live Qdrant coverage**

With a unique collection and deterministic two-dimensional embedder:

1. initialize EpisodicMemory through an injected `QdrantVectorStore`;
2. assert all three payload schema entries are keyword;
3. add and retrieve an episode with matching user/session scopes;
4. insert a semantic point into the same collection;
5. clear episodic memory and assert the semantic point remains; and
6. delete the collection in `finally`.

- [ ] **Step 7: Run focused and live verification**

```powershell
.\venv\Scripts\python.exe -m pytest tests/memory/test_episodic_vector_store_protocol.py tests/memory/test_episodic_vector_cleanup.py tests/memory/storage/test_qdrant_vector_store.py -q --basetemp=.runtime/pytest-episodic-qdrant
powershell -ExecutionPolicy Bypass -File scripts\run_qdrant_integration.ps1
```

Expected: all focused tests and five live tests pass.

- [ ] **Step 8: Run affected and full regression**

```powershell
.\venv\Scripts\python.exe -m pytest tests/memory tests/tools/test_rag_tool_backend_contract.py tests/integration/test_qdrant_document_scope.py -q --basetemp=.runtime/pytest-episodic-final
.\venv\Scripts\python.exe -m pytest -q --basetemp=.runtime/pytest-episodic-full
```

Expected: all tests pass, apart from explicitly guarded external-service
skips.

- [ ] **Step 9: Commit only if separately authorized**

Do not commit or push without explicit user authorization. Record exact
verification evidence in the task packet and final integration review.
