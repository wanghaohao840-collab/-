# Plan Review: episodic-memory-qdrant-backend

- Source plan: `docs/superpowers/plans/2026-08-01-episodic-memory-qdrant-backend.md`
- Reviewed commit: `8cf505bfe69c39c4439557bdaab862c6ef172c6c`
- Review date: `2026-08-01`
- Verdict: `accepted`

## Repository evidence

- Relevant implementation:
  - `hello_agents/memory/types/episodic.py:27` always selects
    `InMemoryVectorStore` when no backend is injected.
  - `hello_agents/memory/types/episodic.py:135` calls unfiltered
    `vector_store.clear()`.
  - `hello_agents/memory/types/episodic.py:235` sends only `memory_type` and
    optional `user_id` to vector search; `session_id` is post-filtered.
  - `hello_agents/memory/storage/vector_store.py:41` already exposes every
    required collection, index, CRUD, and filtering method.
  - `hello_agents/memory/storage/qdrant_store.py:20` already selects Qdrant
    when a URL is configured and otherwise selects the in-memory store.
- Relevant tests:
  - `tests/memory/test_episodic_vector_cleanup.py` covers only a collection
    containing episodic points.
  - `tests/integration/test_qdrant_document_scope.py` is the established real
    Qdrant schema/lifecycle seam.
- Configuration/runtime facts:
  - `MemoryConfig` provides Qdrant URL, API key, collection, and vector size.
  - Repository venv contains qdrant-client 1.18.0; the live runner starts
    Qdrant 1.18.2.
  - Previous-stage reapproval passed 9 focused and 4 live tests.
- Existing worktree changes to preserve:
  - Dirty GraphRAG, RAG Tool, pytest/import-task files and both prior review
    documents are unrelated.
  - None of the four implementation/test files owned by this packet is dirty
    at review time.

### Reality-conflict resolution

- During implementation, a concurrent batch-import task added duplicate-ID
  session maintenance inside `EpisodicMemory.add()`.
- The concurrent block does not overlap this packet's initialization,
  VectorStore CRUD, filtering, or cleanup logic. It is excluded from this
  packet's delivered diff and must be preserved.
- Combined and full regression verification include both changes. No accepted
  interface or test expectation conflicts, so the packet remains deliverable.

## Findings

### Blocking

- None.

### Required revisions

- None.

### Non-blocking notes

- SQLite rows are not currently removed by episodic forget/clear; that existing
  persistence behavior is outside this Qdrant delivery unit.
- Numeric importance and timestamp ranges remain local post-filters because
  the current VectorFilter protocol supports equality and match-any only.
- Concurrent duplicate-ID/session maintenance in `EpisodicMemory.add()` is
  preserved but not attributed to this plan.

## Accepted scope

- Goal: make configured EpisodicMemory use Qdrant safely.
- In scope: unified initialization/CRUD, three keyword indexes, session remote
  filtering, exact-ID cleanup, unit and live verification.
- Out of scope: SQLite redesign, range filters, connection-manager changes,
  migrations, failover, and other memory types.
- Compatibility requirements: public methods, payload shape, local fallback,
  and snapshot structures remain unchanged.
- Architecture/data-isolation constraints: business filters stay in the memory
  layer; Qdrant transport stays in storage; shared collection content survives
  episodic cleanup unless its point ID belongs to the instance.

## Packet graph

| Packet | Depends on | Parallel-safe | Owned files | Outcome |
|---|---|---:|---|---|
| `01-episodic-vector-store.md` | none | no | EpisodicMemory and three tests | configured Qdrant lifecycle is indexed and isolated |

## Packet readiness audit

| Packet | Goal/non-goals | Context/interfaces | Prerequisites | Change boundary | Acceptance/tests | Forbidden changes | Handoff format | Ready |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `01-episodic-vector-store.md` | yes | yes | yes | yes | yes | yes | yes | yes |

No packet may have `status: ready` while any readiness column is `no`.

## Integration verification

- `.\venv\Scripts\python.exe -m pytest tests/memory/test_episodic_vector_store_protocol.py tests/memory/test_episodic_vector_cleanup.py tests/memory/storage/test_qdrant_vector_store.py -q --basetemp=.runtime/pytest-episodic-qdrant`
- `powershell -ExecutionPolicy Bypass -File scripts/run_qdrant_integration.ps1`
- `.\venv\Scripts\python.exe -m pytest tests/memory tests/tools/test_rag_tool_backend_contract.py tests/integration/test_qdrant_document_scope.py -q --basetemp=.runtime/pytest-episodic-final`
- `.\venv\Scripts\python.exe -m pytest -q --basetemp=.runtime/pytest-episodic-full`

## Final integration review requirement

- Output:
  `docs/agent-workflow/task-packets/2026-08-01-episodic-memory-qdrant-backend/FINAL_INTEGRATION_REVIEW.md`
- Required after: packet 01 is `done`
- Result must be: `accepted | changes-required | blocked`
- Required checks:
  - cross-packet interfaces
  - missing requirements
  - duplicate or overlapping implementation
  - architecture, compatibility, persistence, and isolation
  - combined regression verification

## Open decisions

- None.
