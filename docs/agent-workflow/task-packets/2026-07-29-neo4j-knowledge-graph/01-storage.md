---
id: "neo4j-knowledge-graph-01"
title: "Real Neo4j storage adapter"
status: "done"
parallel-safe: false
depends-on: []
base-commit: "614f84e"
owner: "codex"
---

# Task Packet: Real Neo4j storage adapter

## Goal

Replace the memory dictionary placeholder with a credential-safe, injectable Neo4j driver adapter supporting idempotent schema initialization, atomic document replacement, scoped query, build inspection, and scoped deletion.

## Non-goals

- LLM extraction, status manifests, Tool integration, live-service requirement.

## Delivery context

The store is the lowest graph layer and must contain no Tool/UI imports or business retries. Tests use a fake driver, so default verification is offline.

## Relevant files and current interfaces

- `hello_agents/memory/storage/neo4j_store.py:4` — placeholder stores `entities` and `relations` in process memory.
- `requirements.txt` — currently lacks `neo4j`.
- Existing changes to preserve: staged edits in `requirements.txt` and `hello_agents/memory/storage/__init__.py`.

## Prerequisites

### Packet dependencies

- none.

### Repository/base state

- Base commit: `614f84e`; preserve all dirty changes.

### External prerequisites

- none for tests; runtime uses Neo4j 5.x.

## Explicit change boundary

### Allowed files

- Modify: `requirements.txt`
- Modify: `hello_agents/memory/storage/neo4j_store.py`
- Modify: `hello_agents/memory/storage/__init__.py`
- Create: `tests/memory/storage/test_neo4j_store.py`

### Allowed behavior changes

- Replace placeholder with the interface named in the plan.

### Forbidden changes

- No Tool, UI, RAG pipeline, semantic-memory, status, or extraction edits.
- No dynamic user/model text in Cypher syntax.

## Interface contract

### Consumes

- Neo4j-compatible driver/session/transaction protocol.

### Produces

- `Neo4jGraphStore` with `initialize_schema`, `replace_document_graph`, `get_document_build`, `get_document_graph`, typed relation queries, chapter query, and `delete_document`.

### Invariants

- Non-empty `rag_namespace` and `document_id`; parameterized values; transaction rollback; credentials absent from public state.

## Required behavior

- Static whitelisted templates, independent stable cursors, default chunk-content exclusion, targeted deletes, relationship endpoint summaries.

## Implementation guidance

Construct an official driver only when no driver is injected. Keep only `_driver`, `database`, and schema-init state. Use transaction functions so delete and complete write share a commit boundary.

## Acceptance criteria

- [ ] Fake-driver contract tests pass.
- [ ] Transaction failure rolls back.
- [ ] Every query/delete carries `document_id` as a parameter.
- [ ] `repr`, `str`, and serializable attributes contain no credentials.

## Test and verification commands

```powershell
pytest -q tests/memory/storage/test_neo4j_store.py
```

Expected: all tests pass.

## Stop conditions

Use the repository reality-conflict protocol for missing interfaces, boundary expansion, invalid verification, or overlapping unpreservable edits.

## Implementation handoff

- Packet: `neo4j-knowledge-graph-01`
- Status: `done`
- Delivered:
  - Real injectable Neo4j adapter with constraints/indexes, parameterized Cypher, single-transaction replacement, scoped reads/deletes, pagination, and credential-safe state.
- Files changed:
  - `requirements.txt`
  - `hello_agents/memory/storage/neo4j_store.py`
  - `hello_agents/memory/storage/__init__.py`
  - `tests/memory/storage/test_neo4j_store.py`
- Interfaces added or changed:
  - `Neo4jGraphStore` lifecycle, replacement, build lookup, graph/chapter/typed queries, and deletion methods; all accept `rag_namespace`.
- Acceptance evidence:
  - [x] values are parameters and relationship/label syntax is whitelisted
  - [x] document replacement uses one write transaction
  - [x] credentials are absent from public state and representations
  - [x] all operations scope namespace plus document
- Verification:
  - `pytest -q tests/memory/storage/test_neo4j_store.py` — PASS (`9 passed`)
- Scope confirmation:
  - changed only allowed files: yes
  - forbidden areas untouched: yes
- Deviations:
  - Added `rag_namespace` to the accepted scope after current multi-user code made document-only scope unsafe.
- Residual risks/follow-ups:
  - Live database behavior is covered by an optional integration test in Packet 04.
- Commit:
  - `not committed`
