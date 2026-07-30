---
id: "qdrant-stabilization-03"
title: "Create RAG payload indexes"
status: "done"
parallel-safe: false
depends-on: ["qdrant-stabilization-01", "qdrant-stabilization-02"]
base-commit: "614f84e9d01179ce1272281f77e15550c1dcd764"
owner: "Codex"
---

# Task Packet: Create RAG payload indexes

## Goal

Every Qdrant-backed RAG collection idempotently has keyword indexes on
`rag_namespace` and `document_id` and an integer index on `chunk_index`,
verified against both fake and official Qdrant.

## Non-goals

- SemanticMemory indexes.
- A second metadata source of truth.
- Stats redesign, distributed locking, or collection migration.

## Delivery context

The three fields are used by every scoped search, count, scroll, replacement,
and delete. Qdrant filters are correct without indexes but degrade as
collections grow. Index creation belongs behind the vector-store boundary;
the RAG layer chooses which business fields require indexes.

## Relevant files and current interfaces

- `hello_agents/memory/storage/vector_store.py:38` — `VectorStore`.
- `hello_agents/memory/storage/vector_store.py:272` — `QdrantVectorStore`.
- `hello_agents/memory/rag/qdrant_pipeline.py:63` — vector store construction
  and collection assurance.
- `tests/memory/rag/test_qdrant_pipeline.py:15` — fake Qdrant client.
- `tests/memory/storage/test_vector_store_contract.py:20` — generic lifecycle.
- `tests/integration/test_qdrant_document_scope.py` — live service tests from
  packet 02.
- Existing changes from packets 01 and 02 must be preserved.

## Prerequisites

### Packet dependencies

- `qdrant-stabilization-01` and `qdrant-stabilization-02` must be `done`.

### Repository/base state

- Base commit plus the dirty worktree and completed packets 01/02.

### External prerequisites

- The packet 02 live runner and downloaded official Qdrant service.
- `qdrant-client==1.18.0`.

## Explicit change boundary

### Allowed files

- Modify: `hello_agents/memory/storage/vector_store.py`
- Modify: `hello_agents/memory/rag/qdrant_pipeline.py`
- Modify/Test: `tests/memory/storage/test_vector_store_contract.py`
- Modify/Test: `tests/memory/rag/test_qdrant_pipeline.py`
- Modify/Test: `tests/integration/test_qdrant_document_scope.py`
- Update handoff only: this packet file

### Allowed behavior changes

- Add a vector-store payload-index assurance interface.
- Request the three exact RAG indexes at pipeline startup.
- Assert live payload schema.

### Forbidden changes

- No SemanticMemory index request.
- No payload shape or filtering changes.
- No collection deletion/recreation.
- No edits to runner, README, requirements, Neo4j, UI, app, or assistants.

## Interface contract

### Consumes

- Existing `VectorStore` lifecycle methods.
- Qdrant `create_payload_index`.
- Qdrant schema types `KEYWORD` and `INTEGER`.

### Produces

```python
def ensure_payload_indexes(
    self,
    collection_name: str,
    indexes: Mapping[str, str],
) -> None: ...
```

`InMemoryVectorStore` implements this as a validated no-op.
`QdrantVectorStore` creates requested indexes with `wait=True`.

### Invariants

- Existing collections and data are retained.
- Repeated pipeline construction is safe.
- Unknown schema names fail before a remote request.
- Existing retries/redaction apply to remote index creation.

## Required behavior

- Exactly the three accepted RAG fields are requested.
- Keyword/integer schema mapping matches Qdrant v1.18.2.
- Live collection payload schema confirms all fields.
- JSON backend remains untouched.

## Implementation guidance

Add the protocol method and both implementations before changing the RAG
caller. Update the fake client with a call-capturing
`create_payload_index(collection_name, field_name, field_schema, wait=True)`.
Normalize schema strings to lowercase. Reject unsupported values with a
message that identifies the field and schema but contains no credentials.

## Acceptance criteria

- [ ] Protocol test accepts index assurance on the in-memory store.
- [ ] Fake client observes the exact three field/schema requests.
- [ ] Repeated assurance does not fail.
- [ ] Unknown schema fails before remote invocation.
- [ ] Official Qdrant reports the expected payload schema.
- [ ] Focused and affected regression suites pass.

## Test and verification commands

```powershell
.\venv\Scripts\python.exe -m pytest tests/memory/storage/test_qdrant_vector_store.py tests/memory/storage/test_vector_store_contract.py tests/memory/rag/test_qdrant_pipeline.py -q --basetemp=.runtime/pytest-qdrant-indexes
powershell -ExecutionPolicy Bypass -File scripts/run_qdrant_integration.ps1
```

Expected: all fake and live index assertions pass.

```powershell
.\venv\Scripts\python.exe -m compileall -q hello_agents
.\venv\Scripts\python.exe -m pytest tests/memory tests/tools/test_rag_tool_backend_contract.py tests/tools/test_rag_tool_multi_document.py tests/integration/test_qdrant_document_scope.py -q --basetemp=.runtime/pytest-qdrant-final
```

Expected: compilation succeeds; non-live suite passes with live file skipped
outside the runner; runner separately executes it with no skips.

## Stop conditions

Stop and report `blocked` if Qdrant 1.18.2 rejects the documented schema API,
if current fake interfaces differ, or if acceptance requires editing a
forbidden file.

## Implementation handoff

- Packet: `qdrant-stabilization-03`
- Status: `done`
- Delivered:
  - idempotent keyword indexes for `rag_namespace` and `document_id` and an
    integer index for `chunk_index`, behind the `VectorStore` boundary.
- Files changed:
  - `hello_agents/memory/storage/vector_store.py` — protocol and Qdrant/in-memory index assurance.
  - `hello_agents/memory/rag/qdrant_pipeline.py` — exact RAG index request.
  - `tests/memory/storage/test_vector_store_contract.py` — protocol coverage.
  - `tests/memory/storage/test_qdrant_vector_store.py` — unsupported schema validation.
  - `tests/memory/rag/test_qdrant_pipeline.py` — exact fake-client calls.
  - `tests/integration/test_qdrant_document_scope.py` — live payload schema.
- Interfaces added or changed:
  - `VectorStore.ensure_payload_indexes(collection_name, indexes) -> None`.
- Acceptance evidence:
  - [x] in-memory protocol accepts valid index assurance.
  - [x] fake client receives exact three field/schema requests.
  - [x] two live pipelines safely repeat index assurance.
  - [x] unknown schema fails before remote invocation.
  - [x] live server reports keyword/keyword/integer.
  - [x] affected regressions pass.
- Verification:
  - RED command — expected FAIL, 3 failed and 37 passed.
  - focused fake command — PASS, 40 passed.
  - live runner — PASS, 3 passed; PID 26896 stopped.
  - compile and combined affected suite — PASS, 129 passed and 3 expected skips.
- Scope confirmation:
  - changed only allowed files: yes
  - forbidden areas untouched: yes
- Deviations:
  - none
- Residual risks/follow-ups:
  - SemanticMemory-specific payload indexes remain out of scope.
- Commit:
  - `not committed`
