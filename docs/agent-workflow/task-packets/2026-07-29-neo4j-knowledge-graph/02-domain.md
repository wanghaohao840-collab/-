---
id: "neo4j-knowledge-graph-02"
title: "Validated graph extraction and durable state"
status: "done"
parallel-safe: false
depends-on: ["neo4j-knowledge-graph-01"]
base-commit: "614f84e"
owner: "codex"
---

# Task Packet: Validated graph extraction and durable state

## Goal

Produce a completely validated, stable-ID document graph in memory and persist only sanitized lifecycle state atomically.

## Non-goals

- Neo4j writes, RAG/Tool integration, graph queries.

## Delivery context

No partial batch may reach Neo4j. The state manifest is authoritative for lifecycle status but must never store prompts, chunk bodies, credentials, or full LLM responses.

## Relevant files and current interfaces

- `hello_agents/core/llm.py:161` — `chat(...) -> str`, including prefixed failure strings.
- `hello_agents/memory/rag/contracts.py:7` — existing document chunk shapes.
- Existing changes to preserve: none in the new package.

## Prerequisites

### Packet dependencies

- `neo4j-knowledge-graph-01` must be done.

### Repository/base state

- Base commit `614f84e` plus Packet 01 output.

### External prerequisites

- none; fixed fake LLM.

## Explicit change boundary

### Allowed files

- Create: `hello_agents/memory/graph/__init__.py`
- Create: `hello_agents/memory/graph/contracts.py`
- Create: `hello_agents/memory/graph/extractor.py`
- Create: `hello_agents/memory/graph/state.py`
- Create: `tests/memory/graph/__init__.py`
- Create: `tests/memory/graph/test_extractor.py`
- Create: `tests/memory/graph/test_state.py`

### Allowed behavior changes

- New isolated graph-domain package only.

### Forbidden changes

- No storage, RAG, Tool, assistant, or UI edits.

## Interface contract

### Consumes

- `llm.chat(prompt, system_prompt=..., temperature=..., max_tokens=...) -> str`.

### Produces

- `GraphExtractor.extract(document_id, chunks, metadata)`.
- `GraphStateRepository.get`, `upsert`, `list_by_status`.
- JSON-safe `ExtractedGraph.to_store_payload()`.

### Invariants

- Stable IDs include document/type/name; model scope is ignored; full-document validation precedes return.

## Required behavior

- Max five chunks and 4,000 conservative tokens per batch; long chunks are windowed without changing source IDs; three-attempt retry; whitelist/dangling validation; dedupe; confidence clamping; atomic manifest updates; safe 500-character errors.

## Implementation guidance

Use dataclasses and standard library JSON only. Inject sleep/random functions for deterministic retry tests. Derive chapter order exclusively from chunk metadata.

## Acceptance criteria

- [ ] Extraction/state tests pass offline.
- [ ] Unknown relations and dangling endpoints reject the whole graph.
- [ ] Counts include every actual LLM call.
- [ ] State JSON contains no chunk body or configured secret.

## Test and verification commands

```powershell
pytest -q tests/memory/graph/test_extractor.py tests/memory/graph/test_state.py
```

Expected: all tests pass.

## Stop conditions

Use the repository reality-conflict protocol for missing prerequisites, boundary expansion, or unverifiable acceptance.

## Implementation handoff

- Packet: `neo4j-knowledge-graph-02`
- Status: `done`
- Delivered:
  - Namespace-aware stable IDs, controlled LLM batching/windowing/retry, whole-document validation and dedupe, chapter derivation, atomic fail-closed state manifest, and sanitized errors.
- Files changed:
  - `hello_agents/memory/graph/__init__.py`
  - `hello_agents/memory/graph/contracts.py`
  - `hello_agents/memory/graph/extractor.py`
  - `hello_agents/memory/graph/state.py`
  - `tests/memory/graph/test_extractor.py`
  - `tests/memory/graph/test_state.py`
- Interfaces added or changed:
  - `GraphExtractor.extract`, `ExtractedGraph.to_store_payload`, `GraphStateRepository`, response envelope and stable-ID helpers.
- Acceptance evidence:
  - [x] five-chunk/4,000-token limits and long-chunk windows
  - [x] retry classification, cumulative attempt counts, jitter and capped Retry-After
  - [x] relation whitelist, dangling checks, confidence normalization and dedupe
  - [x] atomic state, corruption failure, sanitization and 500-character limit
- Verification:
  - `pytest -q tests/memory/graph/test_extractor.py tests/memory/graph/test_state.py` — PASS (`13 passed`)
- Scope confirmation:
  - changed only allowed files: yes
  - forbidden areas untouched: yes
- Deviations:
  - Stable IDs include `rag_namespace` to preserve current multi-user isolation.
- Residual risks/follow-ups:
  - Extraction quality depends on the configured LLM response quality; schema and evidence validation fail closed.
- Commit:
  - `not committed`
