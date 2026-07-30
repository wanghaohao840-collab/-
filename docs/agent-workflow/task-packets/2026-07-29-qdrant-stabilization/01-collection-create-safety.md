---
id: "qdrant-stabilization-01"
title: "Reconcile uncertain collection creation"
status: "done"
parallel-safe: false
depends-on: []
base-commit: "614f84e9d01179ce1272281f77e15550c1dcd764"
owner: "Codex"
---

# Task Packet: Reconcile uncertain collection creation

## Goal

`QdrantVectorStore.ensure_collection()` issues at most one
`create_collection` request. When that request has a transport or 5xx failure,
it reads and validates collection state before deciding whether creation
failed.

## Non-goals

- Payload indexes.
- Live Qdrant installation.
- Changes to RAG document behavior, SemanticMemory, JSON storage, or Neo4j.

## Delivery context

Qdrant collection creation is not safely retryable by blindly repeating the
request: the server may have committed creation even when the client observes
a timeout. The current generic `_call()` loop retries it. This packet keeps
all Qdrant transport logic in the storage layer and changes no public method
signature.

## Relevant files and current interfaces

- `hello_agents/memory/storage/vector_store.py:295` —
  `ensure_collection(collection_name, dimension, distance="Cosine")`.
- `hello_agents/memory/storage/vector_store.py:500` — generic `_call()` retry
  loop used by idempotent operations.
- `hello_agents/memory/storage/vector_store.py:513` — `_should_retry()` defines
  transport and 5xx failures.
- `hello_agents/memory/storage/vector_store.py:624` — collection vector-config
  extraction.
- Existing changes to preserve: the entire current contents of
  `vector_store.py`; edit only collection assurance and its focused helpers.

## Prerequisites

### Packet dependencies

- none.

### Repository/base state

- Base commit: `614f84e9d01179ce1272281f77e15550c1dcd764`.
- The dirty worktree version of `vector_store.py` containing `VectorStore`,
  `InMemoryVectorStore`, and `QdrantVectorStore` is required.

### External prerequisites

- none; tests use a fake client.

## Explicit change boundary

### Allowed files

- Modify: `hello_agents/memory/storage/vector_store.py`
- Create/Test: `tests/memory/storage/test_qdrant_vector_store.py`
- Update handoff only: this packet file

### Allowed behavior changes

- Single-attempt collection creation.
- Read-after-uncertain-create reconciliation.
- Reusable compatible collection validation.

### Forbidden changes

- Do not edit RAG, SemanticMemory, Neo4j, README, requirements, or integration tests.
- Do not change public signatures or persisted payloads.
- Do not weaken dimension or distance validation.
- Do not suppress mapped failures when reconciliation fails.

## Interface contract

### Consumes

- `QdrantVectorStore._should_retry(error, status) -> bool`.
- `QdrantVectorStore._map_error(operation, error, status) -> RAGBackendError`.
- Fake collection info shaped as `info.config.params.vectors`.

### Produces

- Unchanged:
  `ensure_collection(collection_name: str, dimension: int, distance: str = "Cosine") -> None`.
- Private validation helper accepting collection name, info, expected dimension,
  and expected distance.

### Invariants

- Compatible existing collections are reused.
- Incompatible size or distance raises `RAGCollectionError`.
- Non-retryable create errors are not retried or reconciled.
- Credential sanitization remains handled by `_map_error`.

## Required behavior

- A timeout or 5xx after server-side creation causes one `get_collection`.
- A compatible reconciled collection is accepted.
- If reconciliation cannot retrieve a compatible collection, raise the mapped
  original creation failure.
- `create_collection` is called at most once.

## Implementation guidance

Write the fake tests first. Call the raw `client.create_collection` once rather
than routing it through `_call()`. On exception, classify it with
`_status_code()` and `_should_retry()`. Only uncertain failures trigger a
read via the normal retrying `_call("get_collection", ...)`. Extract existing
compatibility checks into one helper used by both pre-existing and reconciled
collections.

## Acceptance criteria

- [ ] Uncertain committed creation is accepted after one read.
- [ ] Uncertain uncommitted creation reports failure after one create.
- [ ] Non-retryable creation errors are not reconciled.
- [ ] Existing dimension/distance tests continue to pass.

## Test and verification commands

Run from repository root:

```powershell
.\venv\Scripts\python.exe -m pytest tests/memory/storage/test_qdrant_vector_store.py tests/memory/storage/test_vector_store_contract.py tests/memory/rag/test_qdrant_pipeline.py -q --basetemp=.runtime/pytest-qdrant-create
```

Expected: all tests pass.

## Stop conditions

Stop and report `blocked` if:

- a verified repository fact above is no longer true;
- the interface differs from this packet;
- implementation requires a file outside the allowed files;
- another active change overlaps the same exact collection-assurance lines;
- the verification command cannot prove at-most-once creation.

## Implementation handoff

- Packet: `qdrant-stabilization-01`
- Status: `done`
- Delivered:
  - collection creation is attempted once and uncertain outcomes are reconciled
    by reading and validating collection state.
- Files changed:
  - `hello_agents/memory/storage/vector_store.py` — single-attempt create and
    shared collection validation.
  - `tests/memory/storage/test_qdrant_vector_store.py` — timeout, uncommitted
    timeout, and non-retryable create coverage.
- Interfaces added or changed:
  - public interfaces unchanged;
  - added private `_validate_collection(...)`.
- Acceptance evidence:
  - [x] committed uncertain creation accepted after one read — focused test.
  - [x] uncommitted uncertain creation fails after one create — focused test.
  - [x] non-retryable creation is not reconciled — focused test.
  - [x] dimension/distance regressions pass — combined focused suite.
- Verification:
  - `python -m pytest tests/memory/storage/test_qdrant_vector_store.py -q --basetemp=.runtime/pytest-qdrant-create-red` — expected RED, 2 failed and 1 passed.
  - `python -m pytest tests/memory/storage/test_qdrant_vector_store.py tests/memory/storage/test_vector_store_contract.py tests/memory/rag/test_qdrant_pipeline.py -q --basetemp=.runtime/pytest-qdrant-create` — PASS, 38 passed.
- Scope confirmation:
  - changed only allowed files: yes
  - forbidden areas untouched: yes
- Deviations:
  - none
- Residual risks/follow-ups:
  - real client/server behavior is verified by packet 02.
- Commit:
  - `not committed`
