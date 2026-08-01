---
id: "neo4j-knowledge-graph-03"
title: "Knowledge graph lifecycle service"
status: "done"
parallel-safe: false
depends-on: ["neo4j-knowledge-graph-01", "neo4j-knowledge-graph-02"]
base-commit: "614f84e"
owner: "codex"
---

# Task Packet: Knowledge graph lifecycle service

## Goal

Expose the approved graph lifecycle/query API with durable transitions, exact-build recovery, retry rules, safe envelopes, and single-process document locking.

## Non-goals

- RAG/Tool wiring, UI, background work, distributed locking.

## Delivery context

The service is the only business layer allowed to combine extraction, state, and Neo4j. Public mutation methods acquire a per-document lock once.

## Relevant files and current interfaces

- Packet 01 store contract.
- Packet 02 extractor/state contracts.
- Existing changes to preserve: none in `service.py`.

## Prerequisites

### Packet dependencies

- Packets 01 and 02 done.

### Repository/base state

- Base commit `614f84e` plus dependency outputs.

### External prerequisites

- none.

## Explicit change boundary

### Allowed files

- Create: `hello_agents/memory/graph/service.py`
- Modify: `hello_agents/memory/graph/__init__.py`
- Create: `tests/memory/graph/test_service.py`

### Allowed behavior changes

- New graph service/export only.

### Forbidden changes

- No RAG pipeline, Tool, semantic memory, assistant, UI, or storage implementation edits.

## Interface contract

### Consumes

- Store, extractor, state repository, and `chunk_loader(document_id) -> list[dict]`.

### Produces

- All service methods and response envelope exactly listed in the plan.

### Invariants

- Query requires `ready`; mutations serialized per document; build status becomes ready only after transaction success; lock references are reclaimed.

## Required behavior

- Build/recovery/retry/delete transitions and counters; limit validation; cleanup retry does not extract; safe errors; pagination passthrough.

## Implementation guidance

Inject UUID/time functions for tests. Keep lock registry private and expose only a diagnostic count property. Recovery runs once in initialization before public work.

## Acceptance criteria

- [ ] Lifecycle tests pass.
- [ ] Matching committed build recovers ready; mismatch/failure recovers failed.
- [ ] Invalid retry states call neither extractor nor store.
- [ ] Lock registry returns to zero after success and failure.

## Test and verification commands

```powershell
pytest -q tests/memory/graph/test_service.py
```

Expected: all tests pass.

## Stop conditions

Use the repository reality-conflict protocol for stale dependency contracts, boundary expansion, or invalid tests.

## Implementation handoff

- Packet: `neo4j-knowledge-graph-03`
- Status: `done`
- Delivered:
  - Graph lifecycle/query service with build states, exact-build recovery, retry admission, cleanup retry, safe envelopes, pagination/tree shaping, and ref-counted document locks.
- Files changed:
  - `hello_agents/memory/graph/service.py`
  - `hello_agents/memory/graph/__init__.py`
  - `tests/memory/graph/test_service.py`
- Interfaces added or changed:
  - `KnowledgeGraphService` build/status/query/retry/delete APIs and `close()`.
- Acceptance evidence:
  - [x] ready is persisted only after store commit
  - [x] interrupted builds recover by exact build ID
  - [x] invalid retry states invoke neither LLM nor store
  - [x] same-document work serializes, different documents run independently, lock entries are reclaimed
- Verification:
  - `pytest -q tests/memory/graph/test_service.py` — PASS (`9 passed`)
- Scope confirmation:
  - changed only allowed files: yes
  - forbidden areas untouched: yes
- Deviations:
  - Service forwards its bound `rag_namespace` to extractor and store.
- Residual risks/follow-ups:
  - Multi-process locking remains explicitly out of scope.
- Commit:
  - `not committed`
