# Plan Review: GraphRAG Cross-Document Canonical Entities

- Source plan: `docs/superpowers/plans/2026-08-01-graphrag-cross-document-entities.md`
- Reviewed commit: `e0d11b9a775642c10a0237ad7d8e7335cb64ba71`
- Review date: `2026-08-01`
- Verdict: `accepted`

## Repository evidence

- Relevant implementation:
  - `hello_agents/memory/storage/neo4j_store.py:188` — document replacement is
    already a single transaction and all local nodes are namespace/document
    scoped.
  - `hello_agents/memory/storage/neo4j_store.py:443` — bounded lexical graph
    context establishes the read/sanitization pattern.
  - `hello_agents/memory/storage/neo4j_store.py:657` — document deletion is the
    canonical orphan-cleanup integration point.
  - `hello_agents/memory/graph/service.py:383` — `_ready_state()` is the gate
    used before graph reads.
  - `hello_agents/tools/builtin/rag_tool.py:1886` and `:2043` — compare and
    multi-summary already consume per-document graph context.
- Relevant tests:
  - `tests/memory/storage/test_neo4j_store.py` — recording Neo4j driver asserts
    Cypher markers and exact parameters.
  - `tests/memory/graph/test_service.py` — fake store and ready-state envelopes.
  - `tests/tools/test_rag_tool_multi_document.py` — compare/summary prompt,
    citation, cache, progress, cancellation, and graph-mode coverage.
  - `tests/integration/test_neo4j_live.py` — real replace/query/delete lifecycle.
- Configuration/runtime facts:
  - Local Bolt port `localhost:7687` is reachable and the prior live test
    passed with the user-supplied local credentials.
  - `.env` currently points to a remote hostname that fails DNS resolution.
  - Repository `venv` contains Neo4j driver `5.28.4`.
- Existing worktree changes to preserve:
  - Prior accepted GraphRAG changes in `neo4j_store.py`, `rag_tool.py`, their
    tests, README, specs, plans, and packet reviews.
  - Unrelated concurrent Qdrant, episodic memory, import service, and smoke-test
    changes listed by `git status`; none are in this plan's responsibility.

## Findings

### Blocking

- None.

### Required revisions

- None.

### Non-blocking notes

- Exact normalized-name matching intentionally under-merges aliases; this is a
  precision-first phase and is documented as a residual limitation.
- `.env` is local runtime state and will not be committed or echoed.

## Accepted scope

- Goal: safely reconcile same-name entities across selected documents and make
  that evidence available to compare and multi-summary answers.
- In scope: canonical schema/link lifecycle, legacy read fallback, bounded
  store/service API, RAGTool consumption, local `.env`, docs, tests, live gate.
- Out of scope: fuzzy/LLM linking, manual merge review, graph UI,
  cross-namespace sharing, vector ranking, distributed locks.
- Compatibility requirements: existing document graph APIs and per-document
  graph sources retain their shapes and defaults.
- Architecture/data-isolation constraints: `RAGTool ->
  KnowledgeGraphService -> Neo4jGraphStore`; all canonical identity is
  namespace-scoped and all evidence remains explicitly document-scoped.

## Packet graph

| Packet | Depends on | Parallel-safe | Owned files | Outcome |
|---|---|---:|---|---|
| `01-storage.md` | none | no | store + storage/live tests | canonical persistence/read |
| `02-service.md` | 01 | no | service + service tests | ready-gated API |
| `03-rag-integration.md` | 02 | no | `.env`, RAGTool/tests, README, handoffs | end-to-end consumption and acceptance |

## Packet readiness audit

| Packet | Goal/non-goals | Context/interfaces | Prerequisites | Change boundary | Acceptance/tests | Forbidden changes | Handoff format | Ready |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `01-storage.md` | yes | yes | yes | yes | yes | yes | yes | yes |
| `02-service.md` | yes | yes | yes | yes | yes | yes | yes | yes |
| `03-rag-integration.md` | yes | yes | yes | yes | yes | yes | yes | yes |

## Integration verification

- `D:\python_self_agent\venv\Scripts\python.exe -m pytest tests/memory/storage/test_neo4j_store.py tests/memory/graph/test_service.py tests/tools/test_rag_tool_graph.py tests/tools/test_rag_compare.py tests/tools/test_rag_tool_multi_document.py -q`
- real `tests/integration/test_neo4j_live.py` with local credentials
- full pytest, compileall, pip check, and diff check from the source plan

## Final integration review requirement

- Output:
  `docs/agent-workflow/task-packets/2026-08-01-graphrag-cross-document-entities/FINAL_INTEGRATION_REVIEW.md`
- Required after: every implementation packet is `done`
- Result must be: `accepted | changes-required | blocked`
- Required checks: interface agreement, requirement coverage, overlap,
  canonical lifecycle, compatibility, namespace/document isolation, failure
  behavior, live Neo4j, and combined regression.

## Open decisions

- None.
