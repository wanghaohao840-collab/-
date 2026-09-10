# Plan Review: document-search

- Source plan: `docs/superpowers/plans/2026-09-04-document-search.md`
- Reviewed commit: `daeb24f` plus the verified dirty state that contains the completed embedding, operations, notes, overview and insights work
- Review date: 2026-09-04
- Verdict: `accepted-with-revisions`

## Repository evidence

- Relevant implementation:
  - `assistants/pdf_learning_assistant.py:490` accepts an explicit 1–10 document search scope but returns human-readable text.
  - `hello_agents/tools/builtin/rag_tool.py:1352` is the active `_search`; `execute_result` can carry `_last_action_data`, but search does not currently populate structured hits.
  - `hello_agents/memory/rag/pipeline.py:1204` and `hello_agents/memory/rag/qdrant_pipeline.py:436` provide document-scoped chunk reads; use them only behind RAGTool, not by crossing into a private pipeline from the application service.
  - `app/document_library.py:52` lists current-user ready documents and excludes active deletion fences.
  - `app/database.py:294` constrains legacy `note_sources` to QA kinds; rebuilding it would make application rollback unsafe.
  - `app/note_service.py:278` resolves QA sources server-side and `app/note_repository.py:621` scrubs them inside document/conversation deletion transactions.
  - `web/src/App.tsx:25` still maps `/search` to the migration placeholder. `web/src/pages/QaPage.tsx:18` already accepts a validated document handoff; NotesPage accepts only QA sources.
- Relevant tests:
  - RAG backend contracts and Qdrant scope tests already constrain backend parity.
  - QA, notes and deletion tests provide user/fence/idempotency seams.
  - Frontend uses Vitest plus three Playwright viewports and axe checks.
- Configuration/runtime facts:
  - Production currently runs App + Qdrant with BGE-M3; Neo4j is intentionally off.
  - `Update-Deployment.ps1` provides cold backup, image gates, rollback images and deep smoke.
- Existing worktree changes to preserve:
  - All existing dirty changes in stable and `.worktrees/bge-m3-runtime-identity`, especially real `.env`, embedding identity/cutover, operations, insights and runtime evidence. Do not copy runtime artifacts or secrets between worktrees.

## Findings

### Blocking

- None.

### Required revisions

- Packet 03 browser-isolation correction: combined acceptance exposed inherited workstation dotenv/Neo4j configuration in `web/e2e/python-runtime.ts`. Include this shared test-child environment helper and `web/tests/python-runtime.test.ts` only; strip service credentials, disable dotenv, preserve interpreter selection and caller signatures. Repeat all browser acceptance, rather than increasing timeouts. No production configuration or application code change.

- Packet 03 consumer ownership correction: the existing NotesWorkspace source panel uses a QA-only fallback and the two filter controls omit document sources. Explicitly include their component/test files for this narrow adaptation; backend and unrelated editor changes remain excluded. Packet 02's concrete API selector/error contract is now embedded in Packet 03.

- Implementation reality conflict resolved in Packet 01: bounded exact-chunk readers are allowed in both pipeline backends; structured source excerpts must not pass through the exception-text truncation/redaction routine. Full-content checksums remain separate from the 1200-character excerpt.

- Replace the original destructive `note_sources` enum migration with additive `note_document_sources`; project both tables into `NoteSource`. This preserves old-image database readability.
- Resolve a saved search hit through a public structured internal RAGTool action rather than calling `_get_pipeline` from `DocumentSearchService`.
- Make request saturation and stale post-search scope observable (`SEARCH_BUSY`, `SEARCH_SCOPE_CHANGED`) and verify semaphore/active-user cleanup in `finally`.

### Non-blocking notes

- Result count is returned-window count, not total cardinality. Search history, reranking and online literature providers remain future work.
- A content checksum detects stale locators; it is not a secrecy or authenticity primitive against a compromised server.

## Accepted scope

- Goal: complete a real `/search` local-document retrieval workflow with structured provenance, QA handoff and direct note capture.
- In scope: 1–10 explicit documents, semantic search, 5/10/20 results, page/section metadata, copying, additive document-chunk note sources, three viewports and safe publication.
- Out of scope: online literature discovery, OCR, all-library search, history, reranker, GraphRAG/Neo4j, new embedding models and async search jobs.
- Compatibility requirements: legacy Gradio search text, QA behavior, old QA note sources and old application image database reads must remain valid.
- Architecture/data-isolation constraints: session identity only; backend scope validation before and after search; structured RAG adapter; server-side source re-resolution; no offline inventory in online requests; no locks during network embedding calls.

## Packet graph

| Packet | Depends on | Parallel-safe | Owned files | Outcome |
|---|---|---:|---|---|
| `01-search-service-api.md` | none | no | RAG structured search, search service/schema/route, composition/tests | safe structured search API |
| `02-document-note-source.md` | 01 | no | additive schema, note model/repository/service/API/tests | verified document-chunk notes |
| `03-search-react-ui.md` | 02 | no | search React feature/page/styles/handoffs/E2E and shared test-environment helper/tests | responsive end-user workflow with isolated acceptance |
| `04-release-integration.md` | 03 | no | product docs, stable integration and release evidence | published accepted slice |

## Packet readiness audit

| Packet | Goal/non-goals | Context/interfaces | Prerequisites | Change boundary | Acceptance/tests | Forbidden changes | Handoff format | Ready |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 01 | yes | yes | yes | yes | yes | yes | yes | yes |
| 02 | yes | yes | yes | yes | yes | yes | yes | yes |
| 03 | yes | yes | yes | yes | yes | yes | yes | yes |
| 04 | yes | yes | yes | yes | yes | yes | yes | yes |

No packet may have `status: ready` while any readiness column is `no`.

## Integration verification

- `venv/Scripts/python.exe -m pytest -q --tb=short --basetemp=deploy-state/pytest-search-final`
- `npm --prefix web test -- --run --maxWorkers=2`
- `npm --prefix web run typecheck`; `npm --prefix web run lint`; `npm --prefix web run build:app`; `npm --prefix web audit --audit-level=low`
- With isolated JSON/simple-embedding configuration, run `npx playwright test e2e/search.spec.ts e2e/auth-shell.spec.ts e2e/visual.spec.ts` from `web`.
- Publish with `deploy/windows/Update-Deployment.ps1`; repeat `deploy/smoke_test.py --deep` and a temporary-user real-service search provenance check inside the published container with an isolated database/namespace. Packet 04 records the cleanup correction: no supported production account deletion exists, so no disposable account or ad hoc SQL cleanup in the live database. Check served HTTP assets/auth separately and retain three-viewport browser handoff evidence.

## Final integration review requirement

- Output: `docs/agent-workflow/task-packets/2026-09-04-document-search/FINAL_INTEGRATION_REVIEW.md`
- Required after: every implementation packet is `done`
- Result must be: `accepted | changes-required | blocked`
- Required checks: producer/consumer shapes, duplicate search logic, old-schema compatibility, user/fence/checksum isolation, late-response UI behavior, central wiring, regressions and real production evidence.

## Open decisions

- None. User selected imported-document search first and previously selected isolated serial execution.
