# Final Integration Review: document search

- Source review: `REVIEW.md`
- Reviewed commit/worktree: stable `daeb24f9bead3a7894b3c0a0fe6f1ddcf2fc01a1` plus the 42 uncommitted paths in COPY_MANIFEST.json; isolated worktree `codex/bge-m3-runtime-identity`. Existing unrelated dirty files retained.
- Review date: 2026-09-05
- Result: `accepted`

## Delivered packet inventory

| Packet | Status | Commit | Owned files | Verification |
|---|---|---|---|---|
| 01 | done | uncommitted | search service/API, RAG structured adapters | PASS |
| 02 | done | uncommitted | additive note source schema/service/API | PASS |
| 03 | done | uncommitted | React search, source consumers, isolated E2E | PASS |
| 04 | done | uncommitted | stable copy, release and acceptance records | PASS |

## Combined diff reviewed

- Added: app/document_search.py, search API/schema/tests, web search feature/page/styles/E2E; exact inventory in COPY_MANIFEST.json.
- Modified: RAGTool and both exact-chunk readers; note schema/models/repository/service and DTOs; application composition, route wiring and React consumers. Reviewed incremental pipeline/tool diffs against `.runtime/search-preintegration-20260904` to distinguish pre-existing embedding work.
- Pre-existing changes excluded: embedding migration/cutover, Windows operations, overview/insights, GraphRAG records and runtime artifacts. No bulk overwrite, commit or push.
- Rechecked all 42 stable and worktree SHA-256 values against copy manifest: zero mismatches.

## Cross-packet interface audit

| Producer | Consumer | Contract checked | Result | Evidence |
|---|---|---|---|---|
| RAGTool structured result | DocumentSearchService | full-content hash, bounded excerpt, exact identity | pass | hello_agents/tools/builtin/rag_tool.py:1428; app/document_search.py:139 |
| Exact JSON/Qdrant reader | source resolver | scope, index identity, ambiguous/missing rejection | pass | hello_agents/memory/rag/qdrant_pipeline.py:435; pipeline.py:1204 |
| SearchChunkLocator | note create DTO/service | locator-only request, server re-resolution | pass | api/schemas/notes.py:19; app/note_service.py:303 |
| Document source repository | deletion/notes UI | atomic redaction, retained body, source kind | pass | app/note_repository.py:672; web/src/components/NotesWorkspace/NoteSourcePanel.tsx |
| Search response | React search and QA handoff | limits, stale/session guards, range confirmation | pass | web/src/features/search/queries.ts; web/src/pages/SearchPage.tsx |

## Requirement coverage

| Accepted requirement | Implementing packets | Evidence | Result |
|---|---|---|---|
| Explicit 1–10 documents, 5/10/20 structured hits | 01,03 | search service/API/frontend tests | pass |
| Provenance/copy/editable notes/QA confirmation | 01–03 | note tests and three-viewport E2E | pass |
| Cross-user/fence/stale-source privacy | 01–03 | focused tests and real-service acceptance | pass |
| Legacy compatibility and additive schema | 01,02 | backend contract, upgrade and deletion tests | pass |
| Safe stable release and real providers | 04 | RELEASE_ACCEPTANCE.md; successful updater report | pass |

## Overlap and duplication audit

- No conflicting edits or overwritten packet work found in the manifest set.
- Search reuses existing retrieval ranking; no second retrieval engine or source authority.
- Bootstrap constructs one search service and injects it into notes; API and React routes are registered.
- RAG action fields are ContextVar-backed, not ordinary shared result fields (`rag_tool.py:130–160`). A deterministic interleaving probe retained the search envelope; the suspected overwrite was not reproduced and no corrective change was justified.

## Architecture and invariant audit

- Dependency direction: UI → API/application service → public structured RAGTool → backend; no offline inventory in request handling.
- Backward compatibility: existing QA selectors/digests and Gradio text remain; additive table leaves legacy source DDL intact.
- Persistence/migration: old-table readability does not permit old-image-only writable rollback. Safe rollback pairs old image with consistent cold backup; first failed update exercised this path successfully.
- Data isolation: session-derived user, scope validation before/after retrieval, source checksum verification and transaction-time deletion guard. Queries/excerpts are not placed in browser URLs or persistent storage.
- Failure/concurrency: per-user/global admission released in finally; network calls outside user mutation lock; no-store also applies to search errors. React generation/session guards reject late responses. Admission remains process-local, not a distributed lock.

## Combined verification

- Additional final frontend rerun `D:/NODEJS_fastapi/npm.cmd --prefix web test -- --run --maxWorkers=2`: 188 passed / 24 files, 42.99 s, exit 0.

- Stable `venv/Scripts/python.exe -m pytest -q --tb=short --basetemp=D:/python_self_agent/deploy-state/pytest-search-stable-20260904`: 1756 passed / 8 conditional skips / one existing local-Qdrant warning (release-stage run; manifest unchanged).
- Fresh final `venv/Scripts/python.exe -m pytest -q tests/test_document_search.py tests/api/test_search_routes.py tests/test_note_models.py tests/test_note_repository.py tests/test_note_service.py tests/test_note_source_deletion.py tests/api/test_note_routes.py tests/tools/test_rag_tool_backend_contract.py --basetemp=deploy-state/pytest-search-review-20260905`: 106 passed / one existing local-Qdrant warning, 19.58 s.
- Release-stage frontend: 188 passed; typecheck, lint and build passed. `npm exec -- playwright test e2e/search.spec.ts e2e/auth-shell.spec.ts e2e/visual.spec.ts --output=test-results` with APP_COOKIE_SECURE=false: 31 passed / 2 expected viewport skips. Three viewports, zero serious/critical axe findings or checked overflow; no new snapshot acceptance.
- Design contract checks: 18 passed. Fresh release npm audit at low threshold: zero vulnerabilities. These are recorded release-stage checks, not claims of fresh remote Penpot readback.
- Safe updater `update-20260905T004248Z.json`: succeeded; backup `assistant-20260905T004522Z.tar.gz`; both configured image gates passed.
- Post-recovery `venv/Scripts/python.exe deploy/smoke_test.py --env-file deploy/.env --deep`: PASS including real LLM answer. Isolated in-container BGE-M3 search/source workflow: all 11 checks passed and cleanup completed. See RELEASE_ACCEPTANCE.md for command and failed-fixture cleanup evidence.
- Published /search 200, unauthenticated search POST 401, current hashed assets served. App healthy on 127.0.0.1:7860; Qdrant healthy, no host exposure; Neo4j off. `git diff --check` passed with line-ending notices only.

## Findings

### Blocking

- None for this local-document search slice.

### Changes required

- None.

### Residual risks

- External provider and local proxy availability; persisted exact-host bypass repaired observed TLS failures, not a guarantee of future uptime.
- Existing main bundle size warning and local-Qdrant payload-index warning. Conditional external tests are not counted as executed.
- Previously stuck Windows health-task instance recovered; original cause unproven. Bounded command deadlines remain operational hardening work.
- Real-service fixture used an isolated database inside the published container, not a live-account browser session. Browser handoff evidence comes from isolated three-viewport E2E.
- No online scholarly discovery, reranking/history, new Penpot export, multi-process deployment or Neo4j acceptance is included.

## Decision

Accepted for the agreed local-document /search vertical slice. Source, contracts, regression, safe publication and real-service gates have evidence; production fixtures were cleaned. This does not mark the broader learning-center/distributed roadmap complete. No corrective packets required. Not committed or pushed.
