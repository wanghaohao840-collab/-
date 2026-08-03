---
id: "episodic-memory-qdrant-backend-01"
title: "Unify EpisodicMemory Qdrant lifecycle"
status: "done"
parallel-safe: false
depends-on: []
base-commit: "8cf505bfe69c39c4439557bdaab862c6ef172c6c"
owner: "Codex"
---

# Task Packet: Unify EpisodicMemory Qdrant lifecycle

## Goal

EpisodicMemory uses configured Qdrant storage through `VectorStore`, applies
indexed user/session filters, and cannot clear unrelated shared-collection
points.

## Non-goals

- SQLite cleanup or transaction changes.
- Importance/timestamp range filters.
- Connection-manager, VectorStore, SemanticMemory, or RAG changes.
- Migration, double-write, failover, or collection-layout redesign.

## Delivery context

The class currently advertises SQLite+Qdrant behavior but silently constructs
an in-memory vector store when no backend is injected. It also calls legacy
convenience methods outside the public protocol and performs unfiltered vector
clear. The existing Qdrant boundary already supports the required operations.

## Relevant files and current interfaces

- `hello_agents/memory/types/episodic.py:24` — target class and all vector
  lifecycle behavior.
- `hello_agents/memory/storage/vector_store.py:41` — existing protocol consumed
  by this task; not editable.
- `hello_agents/memory/storage/qdrant_store.py:20` — existing configured
  Qdrant/in-memory selector; not editable.
- `tests/memory/test_episodic_vector_cleanup.py:6` — existing cleanup baseline.
- `tests/integration/test_qdrant_document_scope.py:111` — live memory schema
  pattern.
- Existing changes to preserve: unrelated dirty GraphRAG, RAG Tool, pytest,
  import-task, and review artifacts.

## Prerequisites

### Packet dependencies

- none

### Repository/base state

- Base commit: `8cf505bfe69c39c4439557bdaab862c6ef172c6c`.
- SemanticMemory index phase is accepted and reverified.
- Existing `VectorStore.ensure_payload_indexes` is available.

### External prerequisites

- Repository venv and the local Qdrant 1.18.2 live-test runner.

## Explicit change boundary

### Allowed files

- Modify: `hello_agents/memory/types/episodic.py`
- Modify: `tests/memory/test_episodic_vector_cleanup.py`
- Create: `tests/memory/test_episodic_vector_store_protocol.py`
- Modify: `tests/integration/test_qdrant_document_scope.py`
- Modify: this task packet for its handoff

### Allowed behavior changes

- Select configured Qdrant storage.
- Use public VectorStore CRUD.
- Add exact keyword index declarations and session equality filtering.
- Restrict forget/clear deletion to owned IDs.

### Forbidden changes

- Do not edit any other implementation, configuration, dependency, UI, RAG,
  graph, or test files.
- Do not change public method signatures, payload keys, snapshot shape, SQLite
  behavior, vector dimensions, or collection naming.
- Do not delete a collection or issue an unfiltered point deletion.
- Preserve the concurrent duplicate-ID/session maintenance block in
  `EpisodicMemory.add()`; it is outside this packet's delivered behavior.

## Interface contract

### Consumes

- `QdrantConnectionManager.get_instance(**kwargs) -> VectorStore`.
- `VectorStore.ensure_collection`, `ensure_payload_indexes`, `upsert`,
  `search`, and `delete_by_filter`.

### Produces

- Exact index declaration:
  `{"memory_type": "keyword", "user_id": "keyword", "session_id": "keyword"}`.
- Exact equality filters for fields available to a query.
- Existing dictionary-shaped internal hits after adapting `VectorHit`.

### Invariants

- Initialization prepares the collection before indexes.
- No public/persisted data shape changes.
- Other users, sessions, and memory types cannot be deleted by episodic clear.
- Explicit Qdrant configuration does not silently select an in-memory store.

## Required behavior

- A missing Qdrant URL retains in-memory behavior.
- An injected store is honored.
- Session filtering happens remotely and remains locally rechecked.
- Empty cleanup ID sets issue no remote deletion.
- Live test collections are unique and deleted in `finally`.

## Implementation guidance

Follow the existing SemanticMemory initialization pattern without extracting a
new helper. Convert returned `VectorHit` values at the EpisodicMemory boundary
so ranking code remains unchanged. Capture IDs before clearing dictionaries.
Do not touch SQLite cleanup in this packet.

## Acceptance criteria

- [ ] Recording store observes the exact collection and index declaration.
- [ ] Search receives memory type, user, and session equality filters.
- [ ] Episodic clear preserves a semantic point in the same collection.
- [ ] Live Qdrant proves schema, retrieval, and cleanup isolation.
- [ ] Focused, affected, live, and full regression suites pass.

## Test and verification commands

Run from repository root:

```powershell
.\venv\Scripts\python.exe -m pytest tests/memory/test_episodic_vector_store_protocol.py tests/memory/test_episodic_vector_cleanup.py tests/memory/storage/test_qdrant_vector_store.py -q --basetemp=.runtime/pytest-episodic-qdrant
```

Expected: all focused tests pass.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_qdrant_integration.ps1
```

Expected: five live tests pass.

```powershell
.\venv\Scripts\python.exe -m pytest tests/memory tests/tools/test_rag_tool_backend_contract.py tests/integration/test_qdrant_document_scope.py -q --basetemp=.runtime/pytest-episodic-final
```

Expected: affected suite passes with only guarded external-service skips.

```powershell
.\venv\Scripts\python.exe -m pytest -q --basetemp=.runtime/pytest-episodic-full
```

Expected: full suite passes with only guarded external-service skips.

## Stop conditions

Stop and report `blocked` if repository interfaces differ, an owned file gains
overlapping concurrent changes, the behavior already exists, or any required
change exceeds the allowed boundary. Append the repository reality-conflict
report instead of improvising.

## Implementation handoff

- Packet: `episodic-memory-qdrant-backend-01`
- Status: `done`
- Delivered:
  - EpisodicMemory now selects configured Qdrant through the unified
    VectorStore boundary, declares indexed equality scopes, and deletes only
    instance-owned episode IDs.
- Files changed:
  - `hello_agents/memory/types/episodic.py` — unified initialization/CRUD,
    keyword indexes, session filter, and ID-scoped cleanup.
  - `tests/memory/test_episodic_vector_store_protocol.py` — exact protocol,
    index, and filter assertions.
  - `tests/memory/test_episodic_vector_cleanup.py` — shared-collection cleanup
    isolation.
  - `tests/integration/test_qdrant_document_scope.py` — real Qdrant episodic
    schema, retrieval, and cleanup lifecycle.
- Interfaces added or changed:
  - no public interface changes; EpisodicMemory now consumes existing
    `VectorStore` protocol methods.
- Acceptance evidence:
  - [x] Exact index declaration observed by recording store.
  - [x] Search receives memory type, user, and session filters.
  - [x] In-memory and live cleanup preserve semantic points.
  - [x] Real Qdrant schema/retrieval lifecycle passed.
  - [x] Focused, affected, and full regression suites passed.
- Verification:
  - `.\venv\Scripts\python.exe -m pytest tests/memory/test_episodic_vector_store_protocol.py tests/memory/test_episodic_vector_cleanup.py tests/memory/storage/test_qdrant_vector_store.py -q --basetemp=.runtime/pytest-episodic-qdrant` — PASS (6 passed)
  - `.\venv\Scripts\python.exe -m pytest tests/memory/test_episodic_vector_store_protocol.py tests/memory/test_episodic_vector_cleanup.py tests/assistants/test_import_idempotency.py -q --basetemp=.runtime/pytest-episodic-concurrent-compat` — PASS (4 passed)
  - `powershell -ExecutionPolicy Bypass -File scripts/run_qdrant_integration.ps1` — PASS (5 passed)
  - `.\venv\Scripts\python.exe -m pytest tests/memory tests/tools/test_rag_tool_backend_contract.py tests/integration/test_qdrant_document_scope.py -q --basetemp=.runtime/pytest-episodic-final` — PASS (126 passed, 5 skipped)
  - `.\venv\Scripts\python.exe -m pytest -q --basetemp=.runtime/pytest-episodic-full` — PASS (523 passed, 6 skipped)
- Scope confirmation:
  - changed only allowed files: yes
  - forbidden areas untouched: yes
- Deviations:
  - A concurrent task modified `EpisodicMemory.add()` for duplicate-ID session
    maintenance. That block was preserved, excluded from this delivery, and
    verified together with this work.
- Residual risks/follow-ups:
  - SQLite delete consistency and numeric/timestamp range pushdown remain
    outside this phase.
- Commit:
  - not committed
