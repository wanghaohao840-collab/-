# Plan Review: document-library-vertical-slice

- Source plan: `docs/superpowers/plans/2026-08-15-document-library-vertical-slice.md`
- Reviewed commit: `f90883e71d2fa73a7cb981b11478b68519d8ce80`
- Review date: `2026-08-15`
- Verdict: `accepted`

## Repository evidence

- Relevant implementation:
  - `app/import_models.py:7` — current durable task status has queued/running/retry_wait/succeeded/failed only; cancellation is not already implemented.
  - `app/import_repository.py:31` — one SQLite repository owns task creation, claim, progress, retry, terminal transitions, recovery, and user-scoped reads.
  - `app/import_service.py:34` — authenticated import service derives the user from `SessionRegistry`; `submit_batch()` currently consumes path-backed upload values.
  - `app/import_worker.py:97` — `ImportTaskRunner` owns formal-file attempts and progress callbacks; `ImportWorkerPool` starts after database initialization and is the correct cancellation cooperation point.
  - `app/database.py:36` — `import_tasks.status` has a SQLite CHECK that requires a rebuild to add `cancelled`; `initialize_database()` runs before services start.
  - `app/runtime.py:14` and `app/session.py:27` — one user Runtime is shared while per-session Assistant selection remains separate.
  - `assistants/pdf_learning_assistant.py:793` and `:857` — latest history is already the document source, while coordinated deletion is currently exposed only through current-document display behavior.
  - `app/bootstrap.py:23` — `ApplicationServices` constructs exactly one import repository/service/pool and owns start/stop.
  - `api/app.py:46` and `:127` — auth router, lazy services, `/legacy`, assets and SPA fallback are unified in one ASGI app.
  - `web/src/App.tsx:10` — every protected navigation route currently renders `MigrationPage`; `/documents` is available in `web/src/layout/navigation.ts:16`.
  - `web/src/auth/AuthProvider.tsx:74` — authenticated requests already centralize Cookie credentials, in-memory CSRF and stale-401 generation handling.
  - `docs/product-ui/penpot-handoff.md` — Penpot file/page/component identities and responsive AppShell contracts are current; no document-library boards are recorded.
- Relevant tests:
  - `tests/test_import_models.py`, `tests/test_import_repository.py`, `tests/test_import_service.py`, `tests/test_import_worker.py` — 54/54 passed on the reviewed base using the mandated project venv.
  - `tests/api/test_auth_routes.py`, `tests/api/test_mounts.py`, `tests/api/test_app_lifecycle.py` — established Cookie/CSRF/error/mount/lifespan test patterns.
  - `web/src/layout/AppShell.test.tsx`, `web/src/auth/AuthProvider.test.tsx` — established protected-route and responsive-shell fixtures.
  - `web/e2e/fixtures.ts` — real single-worker Uvicorn with a disposable per-run data root; no route mocking is required.
- Configuration/runtime facts:
  - `requirements.txt:11` already includes `python-multipart>=0.0.20,<1`; no Python dependency change is needed for `UploadFile`.
  - `web/package.json` already contains TanStack Query 5, Vitest, Playwright and axe; no frontend dependency or lockfile edit is needed.
  - `web/playwright.config.ts` fixes one Chromium worker and the approved 1440×1024, 1024×768 and 390×844 viewports.
  - The repository uses `scripts/design_tokens.mjs --check` and `node --test tests/design/test_penpot_component_map.mjs`; the plan uses these verified commands.
- Existing worktree changes to preserve:
  - `docs/superpowers/plans/2026-08-15-document-library-vertical-slice.md` is the only pre-packet uncommitted file. Workers must not overwrite or stage unrelated planning artifacts.

## Findings

### Blocking

- None.

### Required revisions

- None. During inline plan self-review, nonexistent token/mapping script names and an MSW-style example were corrected to current repository commands and test-local fetch stubs before acceptance.

### Non-blocking notes

- A browser-visible running-cancel test may be timing-sensitive. Packet 06 requires deterministic worker/API integration coverage and permits browser cancellation only when a real bounded fixture exposes running state without sleeps or test-only endpoints.
- Penpot writes require the user's connected plugin. Packet 01 must remain blocked rather than invent IDs if the connector or active-file identity is unavailable.

## Accepted scope

- Goal: deliver a real `/documents` vertical slice for authenticated listing, durable batch upload, progress, retry, safe cancellation and coordinated deletion.
- In scope: Penpot source boards, cancelled persistence and arbitration, streaming staging, worker cooperation, document/import APIs, React three-viewport UI, accessibility and real-server acceptance.
- Out of scope: preview, rename, tags, folders, pagination, batch delete, QA/search/notes/insights pages, external queues, multi-worker deployment, object storage, and changes to RAG/Memory/report semantics.
- Compatibility requirements: existing Gradio path upload and `/legacy/` remain operational; old import databases migrate without task loss; existing statuses and retries keep their meaning; nullable old metadata stays nullable.
- Architecture/data-isolation constraints: session-derived user identity, CSRF on mutations, UUID server paths, per-user Runtime lock, exact `document_id` scope, single ApplicationServices lifecycle, no secret/path fields in API or browser storage.

## Packet graph

| Packet | Depends on | Parallel-safe | Owned files | Outcome |
|---|---|---:|---|---|
| `01-penpot-design-source.md` | none | yes | Penpot file; handoff; seven reference PNGs; design contract test | verified design authority |
| `02-cancellation-persistence.md` | none | yes | database, import models/repository and their tests | durable cancellation and commit arbitration |
| `03-streaming-worker-cancellation.md` | 02 | no | storage, import service/worker and their tests | actual-byte staging and cooperative cancellation |
| `04-document-library-api.md` | 03 | no | document service, Assistant/session/bootstrap/API and API tests | authenticated JSON contracts |
| `05-react-document-library.md` | 01, 04 | no | React feature/components/styles/App mapping and unit tests | responsive product route |
| `06-e2e-acceptance.md` | 05 | no | document E2E, document snapshots, accessibility/visual additions, contract extension | real-server acceptance evidence |

## Packet readiness audit

| Packet | Goal/non-goals | Context/interfaces | Prerequisites | Change boundary | Acceptance/tests | Forbidden changes | Handoff format | Ready |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `01-penpot-design-source.md` | yes | yes | yes | yes | yes | yes | yes | yes |
| `02-cancellation-persistence.md` | yes | yes | yes | yes | yes | yes | yes | yes |
| `03-streaming-worker-cancellation.md` | yes | yes | yes | yes | yes | yes | yes | yes |
| `04-document-library-api.md` | yes | yes | yes | yes | yes | yes | yes | yes |
| `05-react-document-library.md` | yes | yes | yes | yes | yes | yes | yes | yes |
| `06-e2e-acceptance.md` | yes | yes | yes | yes | yes | yes | yes | yes |

No packet may have `status: ready` while any readiness column is `no`.

## Integration verification

- `D:\python_self_agent\venv\Scripts\python.exe -m pytest -q tests/test_import_models.py tests/test_import_repository.py tests/test_import_service.py tests/test_import_worker.py tests/test_document_library_service.py tests/api tests/deploy/test_document_library_contract.py --basetemp=.runtime/pytest-document-library-final`
- `cd web; npm test; npm run typecheck; npm run lint; npm run build; npx playwright test --workers=1`
- `node --test tests/design/test_penpot_component_map.mjs`
- `node scripts/design_tokens.mjs --check`
- `git diff --check`

## Final integration review requirement

- Output:
  `docs/agent-workflow/task-packets/2026-08-15-document-library-vertical-slice/FINAL_INTEGRATION_REVIEW.md`
- Required after: every implementation packet is `done`
- Result must be: `accepted | changes-required | blocked`
- Required checks:
  - cross-packet interfaces
  - missing requirements
  - duplicate or overlapping implementation
  - central integration points
  - architecture, compatibility, persistence, and isolation
  - combined regression verification

## Open decisions

- None.
