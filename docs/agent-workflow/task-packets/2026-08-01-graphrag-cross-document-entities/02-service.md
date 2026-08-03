---
id: "graphrag-cross-document-entities-02"
title: "Ready-gated cross-document graph service"
status: "done"
parallel-safe: false
depends-on: ["graphrag-cross-document-entities-01"]
base-commit: "e0d11b9a775642c10a0237ad7d8e7335cb64ba71"
owner: "Codex"
---

# Task Packet: Ready-gated cross-document graph service

## Goal

Expose shared canonical entities through the graph service only when every
selected document graph is ready, using the existing result envelopes.

## Non-goals

- Storage Cypher, RAGTool consumption, configuration, UI, or alias inference.

## Delivery context

RAGTool must not call storage directly. This packet provides the orchestration
and failure boundary between multi-document answering and Neo4j.

## Relevant files and current interfaces

- `hello_agents/memory/graph/service.py:383` — `_ready_state()`.
- `hello_agents/memory/graph/service.py:416` — lexical term normalization and
  result-envelope precedent.
- `hello_agents/memory/graph/service.py:653` — sanitized `_error()` helper.
- `tests/memory/graph/test_service.py:35` — `FakeStore` seam.
- Existing changes to preserve: per-document graph context and retry locking.

## Prerequisites

### Packet dependencies

- `graphrag-cross-document-entities-01` must be done.

### Repository/base state

- Task 1 public store method exists with the reviewed signature.

### External prerequisites

- None.

## Explicit change boundary

### Allowed files

- Modify: `hello_agents/memory/graph/service.py`
- Test: `tests/memory/graph/test_service.py`

### Allowed behavior changes

- Add one ready-gated public service method and fake-store coverage.

### Forbidden changes

- Do not edit storage, extractor, RAGTool, UI, state persistence, or unrelated
  tests. Do not perform writes from this read API.

## Interface contract

### Consumes

- Task 1 store method and current success/error envelopes.

### Produces

- `get_cross_document_entities(document_ids, query, *, entity_limit=12,
  evidence_limit=40) -> dict[str, Any]`.

### Invariants

- Every document passes ready state before one storage call.
- Order-preserving ID deduplication; 2-10 IDs.
- Errors expose exception type, not credentials or raw connection details.

## Required behavior

- Normalize query terms consistently with per-document graph context.
- Return not-ready immediately without storage access.
- Empty successful entities are a valid success.
- Pass the service `rag_namespace` on every storage call.

## Implementation guidance

Extract a private query-term helper only if it removes literal duplication
without changing current `get_graph_context()` behavior. Validate document
cardinality before ready-state access.

## Acceptance criteria

- [ ] Successful delegation uses normalized terms, limits, IDs, and namespace.
- [ ] Any unavailable document prevents storage access.
- [ ] Invalid cardinality and storage failures return sanitized envelopes.
- [ ] Existing service tests remain green.

## Test and verification commands

```powershell
.\venv\Scripts\python.exe -m pytest tests/memory/graph/test_service.py -q --basetemp=.runtime/pytest-cross-entity-service
```

Expected: all tests pass.

## Stop conditions

Stop on missing Task 1 contract, stale envelope helpers, overlap outside prior
GraphRAG work, or need for files outside this boundary.

## Implementation handoff

- Packet: `graphrag-cross-document-entities-02`
- Status: `done`
- Delivered:
  - Added ready-gated `KnowledgeGraphService.get_cross_document_entities()`.
  - Added order-preserving ID deduplication, 2-10 validation, normalized query
    terms, namespace delegation, and sanitized errors.
- Files changed:
  - `hello_agents/memory/graph/service.py`
  - `tests/memory/graph/test_service.py`
- Interfaces added or changed:
  - `KnowledgeGraphService.get_cross_document_entities(...) -> dict[str, Any]`
- Acceptance evidence:
  - [x] Every selected document must be ready before storage access.
  - [x] Successful delegation and failure envelope behavior are covered.
- Verification:
  - `pytest tests/memory/graph/test_service.py -q` — PASS (`14 passed`)
- Scope confirmation:
  - changed only allowed files: yes
  - forbidden areas untouched: yes
- Deviations:
  - none
- Residual risks/follow-ups:
  - none beyond the exact-match limitation documented by packet 01.
- Commit:
  - `not committed`
