---
id: "document-search-01"
title: "Structured user-scoped document search API"
status: "done"
parallel-safe: false
depends-on: []
base-commit: "daeb24f"
owner: "Codex-inline"
---

# Task Packet: Structured user-scoped document search API

## Goal

An authenticated caller can POST an explicit 1–10 document scope and receive bounded structured search hits without parsing legacy display text, crossing user/fence boundaries or holding mutation locks during embeddings.

## Non-goals

- Notes/UI, persistence of queries, online sources, reranking, model changes, GraphRAG/Neo4j.

## Delivery context

Old search already reaches the active JSON/Qdrant backend but only returns text. This packet establishes the stable producer contract consumed by later packets while retaining Gradio behavior.

## Relevant files and current interfaces

- `hello_agents/tools/builtin/rag_tool.py:410,451,558,1352` — dispatch, structured envelope sanitization and active text-only search.
- `hello_agents/memory/rag/pipeline.py:1204`, `hello_agents/memory/rag/qdrant_pipeline.py:436` — document-scoped ordered chunks.
- `app/document_library.py:52` — session-user document/fence authority.
- `app/bootstrap.py:38`, `api/dependencies.py:14`, `api/app.py:86` — single composition root and router seams.
- Preserve all dirty embedding/runtime changes in these files.

## Prerequisites

- Packet dependencies: none.
- Repository/base state: base plus current verified dirty embedding/overview state; RAGTool `execute_result` exists.
- External prerequisites: none for tests; Qdrant parity test may skip only under its existing environment condition.

## Explicit change boundary

- Create: `app/document_search.py`, `api/schemas/search.py`, `api/routes/search.py`, `tests/test_document_search.py`, `tests/api/test_search_routes.py`.
- Modify/test: RAGTool active search/dispatch only, `app/bootstrap.py`, `api/dependencies.py`, `api/app.py`, RAG contract and scoped integration tests.
- Allowed: structured `results`, internal exact-chunk action, typed errors, user/global admission limits and route wiring.
- Forbidden: modifying score/retrieval algorithms, `_ask`, persisted index identity, migrations, notes/UI, secrets, deployment data, Neo4j, or parsing message strings.

## Interface contract

- Consumes: `list_documents(token)`, `runtime.rag_tool.execute_result`, bounded public backend `get_document_chunk(document_id, chunk_id, chunk_index)`.
- Produces: frozen search request/hit/result/locator models; `search(token, request)`; `resolve_chunk(token, locator)`; POST `/api/v1/search` with safe error codes and no-store.
- Invariants: full requested scope is all-or-nothing; names come from the document library; network work occurs outside runtime mutation lock; scope is rechecked before publishing.

## Required behavior

- Query 1–1000 after trimming, unique 1–10 UUID-like document IDs, limit 5/10/20.
- Structured hits carry document/chunk identity, chunk index, SHA-256, bounded excerpt, rank, score and real optional page/section.
- Exact resolver rejects missing/ambiguous/stale chunks. One request/user and four/process; busy/unavailable are retryable.
- Empty is successful. No internal path, namespace, collection, model configuration or cross-user existence signal appears.

## Implementation guidance

### Resolved reality conflict (2026-09-04)

Existing `get_document_chunks` reads an entire document and the generic error sanitizer truncates normal content to 500 characters. Expand the allowed boundary to public exact-chunk readers in `hello_agents/memory/rag/pipeline.py` and `hello_agents/memory/rag/qdrant_pipeline.py`, plus their contract tests. Qdrant must use one existing `scroll_page(page_size=2)` filtered by namespace/document/index; reject ambiguity or continuation. JSON uses exact logical ID in its resident store. Preserve managed-index checks and do not change retrieval ranking. Return a maximum 1200-character source excerpt with SHA-256 computed from the original full chunk; never hash the excerpt as if it were the original. These changes resolve the bounded-read stop condition before implementation.

Follow Plan Task 1. Populate `_last_action_data` before legacy formatting. Add exact-chunk dispatch behind RAGTool rather than exposing its private pipeline. Acquire/release admission state in `try/finally`; perform pre/post library set equality around backend work.

## Acceptance criteria

- [x] JSON/Qdrant structured parity and legacy text regression pass (Qdrant local client; external-service tests remain conditional).
- [x] Authentication/CSRF/input/no-store/safe errors pass, including no-store on dependency and validation failures.
- [x] Cross-user, deletion race, timeout, saturation and cleanup pass.
- [x] Exact source resolution requires identity and checksum, with no offline inventory calls.

## Test and verification commands

`venv/Scripts/python.exe -m pytest -q tests/tools/test_rag_tool_backend_contract.py tests/test_document_search.py tests/api/test_search_routes.py tests/integration/test_qdrant_document_scope.py --basetemp=deploy-state/pytest-search-service`

Expected: zero failures; existing conditional skips only.

## Stop conditions

Stop with the repository reality-conflict format if an interface differs, another change owns active `_search`, backend chunk reads cannot be bounded/scoped, or any allowed-file expansion is needed.

## Implementation handoff

Report delivered behavior, exact files/interfaces, four acceptance checks, test counts, scope confirmation, deviations/risks and `not committed`.

### 2026-09-04 handoff

- Worktree: `D:/python_self_agent/.worktrees/bge-m3-runtime-identity`; branch `codex/bge-m3-runtime-identity`. Production and stable implementation files unchanged.
- Delivered: singleton admitted search service; POST `/api/v1/search`; typed request/result/locator; safe 404/409/422/429/503 errors; private-response middleware; exact source resolver; allowlisted bounded content with original SHA-256; public exact readers for both existing backends.
- Files: `app/document_search.py`, `app/bootstrap.py`, `api/dependencies.py`, `api/app.py`, `api/routes/search.py`, `api/schemas/search.py`, the two RAG pipelines, `hello_agents/tools/builtin/rag_tool.py`, and three search/contract test files.
- Focused search + conditional integration: **38 passed, 5 skipped**. API + search regression: **162 passed**. Final search + existing note baseline: **82 passed**. Expected warning: local Qdrant does not implement payload indexes; production indexing was not tested or altered here.
- Full Python regression: **1732 passed, 8 skipped, 1 warning**, 315.06 seconds, exit 0 (`pytest-search-regression`). This run started before the final no-store/DTO-validation refinements; the final current-code targeted rerun completed with **37 passed** (`pytest-search-final`). No real production backend or frontend acceptance is claimed by these tests.
- Approved internal correction: exact bounded backend readers and separate source-data/error-text handling, detailed above. No ranking, model, index identity, source database, user data or deployment mutation.
- Remaining slice work: Packet 02 document-note provenance; Packet 03 React workflow; Packet 04 final review and safe publication. This is not a completed `/search` product release.
- Git: **not committed, not pushed**.
