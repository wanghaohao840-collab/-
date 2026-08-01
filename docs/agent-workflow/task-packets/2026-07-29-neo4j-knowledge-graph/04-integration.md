---
id: "neo4j-knowledge-graph-04"
title: "RAG and Tool graph integration"
status: "done"
parallel-safe: false
depends-on: ["neo4j-knowledge-graph-03"]
base-commit: "614f84e"
owner: "codex"
---

# Task Packet: RAG and Tool graph integration

## Goal

Integrate the graph lifecycle weakly into both RAG backends and `RAGTool`, preserve SemanticMemory without Neo4j configuration, add optional live verification, and document runtime behavior.

## Non-goals

- Graph answer retrieval, Gradio changes, assistant public API changes, cross-document graphs.

## Delivery context

RAG is the primary path. Graph work follows successful ingestion; RAG deletion happens before graph cleanup. Legacy string output stays usable while structured results include the graph envelope.

## Relevant files and current interfaces

- `hello_agents/tools/builtin/rag_tool.py:178` — structured `execute_result` and `_last_action_data`.
- `hello_agents/memory/rag/pipeline.py:75` — JSON chunks are available internally.
- `hello_agents/memory/rag/qdrant_pipeline.py:353` — scoped payload scrolling exists.
- `hello_agents/memory/types/semantic.py:62` — eagerly creates placeholder graph store and must be made optional.
- Existing changes to preserve: all four files and README/requirements are already dirty.

## Prerequisites

### Packet dependencies

- Packet 03 done.

### Repository/base state

- Base commit `614f84e` plus Packets 01–03.

### External prerequisites

- none by default; live test needs `NEO4J_TEST_URI`.

## Explicit change boundary

### Allowed files

- Modify: `hello_agents/memory/rag/pipeline.py`
- Modify: `hello_agents/memory/rag/qdrant_pipeline.py`
- Modify: `hello_agents/tools/builtin/rag_tool.py`
- Modify: `hello_agents/memory/types/semantic.py`
- Modify: `app/runtime.py`
- Modify: `assistants/pdf_learning_assistant.py`
- Modify: `tests/memory/test_semantic_fallback.py`
- Create: `tests/tools/test_rag_tool_graph.py`
- Create: `tests/integration/test_neo4j_live.py`
- Modify: `README.md`

### Allowed behavior changes

- Public complete-document chunk loader, optional graph service injection/configuration, graph actions, graph status in structured import/delete results, optional semantic graph writes.

### Forbidden changes

- No UI/assistant behavior, graph retrieval in `_ask`, cache format migration, Qdrant collection redesign, or unrelated cleanup.

## Interface contract

### Consumes

- `KnowledgeGraphService`; existing pipeline replace/delete operations and Tool structured result.

### Produces

- `get_document_chunks(document_id)` on both backends.
- Optional `graph_service` constructor argument and approved graph actions on `RAGTool`.

### Invariants

- Exact `(rag_namespace, document_id)` scope, weak consistency, unchanged RAG success, no required external service, and deterministic driver close.

## Required behavior

- Build after import; graph errors attached but non-fatal; RAG-first delete and cleanup pending; action routing; no extra build for ready/building; target-only retry; docs/live skip.

## Implementation guidance

Centralize graph post-processing helpers in `RAGTool`; do not duplicate format-specific graph calls. Environment construction requires URI, username, and password. State path should be namespace-specific beneath the existing knowledge-base location unless injected.

## Acceptance criteria

- [ ] Graph Tool/integration tests pass.
- [ ] Existing RAG/semantic regression tests pass.
- [ ] No-config startup works.
- [ ] Live test skips without URI.

## Test and verification commands

```powershell
pytest -q tests/tools/test_rag_tool_graph.py tests/memory/test_semantic_fallback.py tests/memory/rag tests/tools/test_rag_tool_backend_contract.py tests/tools/test_rag_tool_multi_document.py tests/integration/test_neo4j_live.py
```

Expected: all tests pass, with live test skipped when unconfigured.

```powershell
pytest -q --ignore=tests/ui/test_authenticated_handlers.py
```

Expected: all non-UI tests pass.

## Stop conditions

Use the repository reality-conflict protocol for interface drift, overlapping changes that cannot be preserved, boundary expansion, or invalid verification.

## Implementation handoff

- Packet: `neo4j-knowledge-graph-04`
- Status: `done`
- Delivered:
  - Both RAG backends expose complete document chunk loading and document listing; RAGTool builds, queries, retries, deletes, and clears graph state weakly; user runtime owns graph state and closes the driver.
- Files changed:
  - `hello_agents/memory/rag/pipeline.py`
  - `hello_agents/memory/rag/qdrant_pipeline.py`
  - `hello_agents/tools/builtin/rag_tool.py`
  - `hello_agents/memory/types/semantic.py`
  - `app/runtime.py`
  - `assistants/pdf_learning_assistant.py`
  - `tests/tools/test_rag_tool_graph.py`
  - `tests/integration/test_neo4j_live.py`
  - `README.md`
- Interfaces added or changed:
  - `get_document_chunks`, `list_document_ids`, graph Tool actions, optional graph configuration/injection, and `RAGTool.close()`.
- Acceptance evidence:
  - [x] graph failures do not change RAG success
  - [x] import/delete/clear are graph-aware
  - [x] namespace mismatch fails closed
  - [x] no-config startup and semantic memory remain available
  - [x] runtime closes graph resources
- Verification:
  - `pytest -q --basetemp=.pytest-tmp-neo4j-final-focused ...` — PASS (`121 passed, 1 skipped`)
  - `D:\Anaconda\python.exe -m pytest -q` — PASS (`421 passed, 3 skipped`)
- Scope confirmation:
  - changed only allowed files: yes, after the reviewed runtime-lifecycle boundary amendment
  - forbidden areas untouched: yes; no UI or graph answer retrieval
- Deviations:
  - Added user-runtime/assistant close hooks and namespace scoping required by current repository isolation rules.
- Residual risks/follow-ups:
  - Live Neo4j verification requires `NEO4J_TEST_URI` and credentials.
- Commit:
  - `not committed`
