---
id: "semantic-memory-qdrant-indexes-01"
title: "Declare SemanticMemory Qdrant filter indexes"
status: "done"
parallel-safe: false
depends-on: []
base-commit: "22dd4ece9a173327cfcd74227976e92d003736e6"
owner: "Codex"
---

# Task Packet: Declare SemanticMemory Qdrant filter indexes

## Goal

Constructing `SemanticMemory` prepares keyword payload indexes for
`memory_type` and `user_id`, and both an injected-store test and live Qdrant
schema test prove the behavior.

## Non-goals

- Do not change the `VectorStore` protocol or Qdrant connection manager.
- Do not add indexes to EpisodicMemory or alter existing RAG indexes.
- Do not refactor adjacent semantic-memory or graph behavior.

## Delivery context

SemanticMemory already filters Qdrant operations by `memory_type` and optional
`user_id`, but it only ensures the collection. Qdrant payload indexes should
be declared by the component that owns these business fields. The existing
store protocol already provides the required idempotent operation.

## Relevant files and current interfaces

- `hello_agents/memory/types/semantic.py:43` — selects the collection and
  injected `VectorStore`; initialization currently ends collection preparation
  after `ensure_collection`.
- `hello_agents/memory/storage/vector_store.py:48` —
  `ensure_payload_indexes(collection_name, indexes) -> None`; implementation
  already exists and is not owned by this packet.
- `tests/memory/test_semantic_vector_store_protocol.py:6` — constructs
  SemanticMemory with an `InMemoryVectorStore`.
- `tests/integration/test_qdrant_document_scope.py:92` — contains the existing
  live Qdrant payload-schema assertion pattern.
- Existing changes to preserve:
  `hello_agents/memory/types/semantic.py` has pre-existing uncommitted
  VectorStore and fallback edits; append only the index declaration.

## Prerequisites

### Packet dependencies

- none

### Repository/base state

- Base commit: `22dd4ece9a173327cfcd74227976e92d003736e6`.
- The working tree includes the existing `VectorStore.ensure_payload_indexes`
  protocol and Qdrant implementation.

### External prerequisites

- Focused tests use the repository `venv`.
- Live verification uses the repository runner and local Qdrant 1.18.2 binary.

## Explicit change boundary

### Allowed files

- Modify: `hello_agents/memory/types/semantic.py`
- Test: `tests/memory/test_semantic_vector_store_protocol.py`
- Test: `tests/integration/test_qdrant_document_scope.py`
- Modify: this packet for implementation handoff

### Allowed behavior changes

- SemanticMemory initialization may request exactly two keyword payload
  indexes after its collection is ensured.

### Forbidden changes

- Do not edit `vector_store.py`, `qdrant_store.py`, EpisodicMemory, RAG code,
  dependency manifests, deployment configuration, or unrelated dirty files.
- Do not change public interfaces, payload keys, persisted values, collection
  naming, vector dimensions, or user-isolation behavior.
- If implementation requires anything outside the allowed boundary, stop
  instead of broadening the packet.

## Interface contract

### Consumes

- `VectorStore.ensure_payload_indexes(str, Mapping[str, str]) -> None`.
- `MemoryConfig.qdrant_collection` and `.qdrant_vector_size`.

### Produces

- `SemanticMemory.__init__` calls:
  `ensure_payload_indexes(self.vector_collection,
  {"memory_type": "keyword", "user_id": "keyword"})`.

### Invariants

- Collection creation precedes index creation.
- Store errors are not swallowed.
- User filtering and all existing payloads remain unchanged.

## Required behavior

- The declaration is executed for both injected in-memory and real Qdrant
  stores.
- Both schemas are keyword indexes.
- Live tests use a unique collection and always clean it up.

## Implementation guidance

First add the recording-store unit assertion and observe it fail. Add the live
test using the current schema-normalization helper pattern. Then add the single
declaration directly after `ensure_collection`; do not introduce a constant or
helper for this one call.

## Acceptance criteria

- [ ] The recording store observes exactly the configured collection and two
  required keyword indexes.
- [ ] Live Qdrant reports both payload schema entries as `keyword`.
- [ ] Existing semantic fallback and vector-store tests pass.
- [ ] Combined memory/tool/integration regression passes.

## Test and verification commands

Run from repository root:

```powershell
.\venv\Scripts\python.exe -m pytest tests/memory/test_semantic_vector_store_protocol.py tests/memory/test_semantic_fallback.py tests/memory/storage/test_qdrant_vector_store.py -q --basetemp=.runtime/pytest-semantic-indexes
```

Expected: all focused tests pass.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_qdrant_integration.ps1
```

Expected: four live tests pass.

```powershell
.\venv\Scripts\python.exe -m pytest tests/memory tests/tools/test_rag_tool_backend_contract.py tests/integration/test_qdrant_document_scope.py -q --basetemp=.runtime/pytest-semantic-final
```

Expected: all collected tests pass, with only explicitly guarded live tests
skipped if the URL variable is absent.

## Stop conditions

Stop and report `blocked` if:

- a verified repository fact above is no longer true;
- a referenced interface, caller, fixture, or test differs from this packet;
- requested behavior already exists or conflicts with the current code;
- implementation requires a file outside **Allowed files**;
- a dependency packet is not `done`;
- acceptance criteria conflict with current behavior or another packet;
- another packet or pre-existing worktree change overlaps this responsibility;
- the verification commands are invalid or cannot prove acceptance.

Do not improvise around these conflicts. Append the **Reality-conflict report**
from `docs/agent-workflow/README.md` and wait for packet revision.

## Implementation handoff

- Packet: `semantic-memory-qdrant-indexes-01`
- Status: `done`
- Delivered:
  - SemanticMemory now declares keyword payload indexes for its active
    `memory_type` and `user_id` Qdrant filters.
- Files changed:
  - `hello_agents/memory/types/semantic.py` — declares the two indexes after
    collection preparation.
  - `tests/memory/test_semantic_vector_store_protocol.py` — records and asserts
    the exact protocol call.
  - `tests/integration/test_qdrant_document_scope.py` — verifies the real
    Qdrant payload schema and cleans up the unique collection.
- Interfaces added or changed:
  - no public interface changes; `SemanticMemory.__init__` now consumes the
    existing `VectorStore.ensure_payload_indexes` method.
- Acceptance evidence:
  - [x] Exact in-memory declaration — focused protocol test passed.
  - [x] Live payload schema — both fields reported `keyword`.
  - [x] Existing semantic and vector-store behavior — focused suite passed.
  - [x] Combined regression — memory/tool/integration and full suites passed.
- Verification:
  - `.\venv\Scripts\python.exe -m pytest tests/memory/test_semantic_vector_store_protocol.py tests/memory/test_semantic_fallback.py tests/memory/storage/test_qdrant_vector_store.py -q --basetemp=.runtime/pytest-semantic-indexes` — PASS (9 passed)
  - `powershell -ExecutionPolicy Bypass -File scripts\run_qdrant_integration.ps1` — PASS (4 passed)
  - `.\venv\Scripts\python.exe -m pytest tests/memory tests/tools/test_rag_tool_backend_contract.py tests/integration/test_qdrant_document_scope.py -q --basetemp=.runtime/pytest-semantic-final` — PASS (124 passed, 4 skipped)
  - `.\venv\Scripts\python.exe -m pytest -q --basetemp=.runtime/pytest-semantic-full` — PASS (494 passed, 5 skipped)
- Scope confirmation:
  - changed only allowed files: yes
  - forbidden areas untouched: yes
- Deviations:
  - The first live-suite run reached all business assertions and passed the new
    test, but Qdrant returned a Windows file-lock error while deleting the
    existing RAG lifecycle collection. An unchanged rerun passed 4/4.
- Residual risks/follow-ups:
  - Qdrant collection deletion can be transiently flaky on Windows when a
    storage directory is still locked; no product behavior failed.
- Commit:
  - `dadfb1f` (the implementation was included by a concurrent broad checkpoint
    commit, not a dedicated scoped commit from this task)
