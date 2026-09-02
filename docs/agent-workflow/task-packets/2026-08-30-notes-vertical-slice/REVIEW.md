# Plan Review: 知研学习笔记垂直切片

- Source plan: `docs/superpowers/plans/2026-08-30-notes-vertical-slice.md`
- Reviewed commit: `8ac2775dc2cb0563095f0fad5b4a83abcbf52fb9`
- Review date: `2026-08-30`
- Verdict: `accepted-with-revisions`

## Repository evidence

- Relevant implementation:
  - `app/database.py:9,280,289`: one central SQLite schema, row-factory connection helper and idempotent initializer are the correct Note persistence integration seam.
  - `app/bootstrap.py:38,63,163,184`: `ApplicationServices` owns construction plus ordered worker start/stop; Notes migration/projection must join this lifecycle once.
  - `app/runtime.py:19,42,50,63`: user runtimes are UUID-scoped and support late injection into existing and future runtimes through the `set_import_task_service()` pattern.
  - `app/qa_repository.py:37,255,1279`: `QaRepository.get_message(user_id, message_id)` and source-scope validation provide the server-owned QA source resolution seam.
  - `app/qa_deletion.py:38,516`: durable QA deletion repository already owns the transaction that removes conversations; source scrubbing belongs in that fenced transaction.
  - `assistants/pdf_learning_assistant.py:527-738`: legacy Note add/clear/recall/stats/report currently use random Memory writes, JSON history and process counters and therefore require one explicit cutover.
  - `api/routes/qa.py:48,52,114`: `/api/v1` prefix, authenticated capabilities and feature-disabled responses establish the Notes route convention.
  - `api/dependencies.py:26,35,42`: service resolution, authenticated session and CSRF dependencies are reusable.
  - `web/src/App.tsx:25` and `web/src/layout/navigation.ts:34`: `/notes` is present in navigation but still falls through to `MigrationPage`.
  - `web/src/components/QaWorkspace/QaWorkspace.tsx:77`: completed answer citations already have stable IDs and action controls where Notes navigation can be added.
  - `docs/product-ui/penpot-handoff.md:178-221`: QA handoff demonstrates the required fresh-read, direct-export, bounds and viewport evidence format.
  - `docs/product-ui/penpot-component-map.json` and `tests/design/test_penpot_component_map.mjs:129-152`: the JSON component map and freshness test are the existing design-to-code binding.
- Relevant tests:
  - `tests/test_app_bootstrap.py`: lifecycle construction/start/stop regression seam.
  - `tests/test_user_runtime.py`: user runtime sharing, injection and release seam.
  - `tests/test_qa_deletion.py`: deletion lease/fence, stale-owner and recovery coverage.
  - `tests/api/test_qa_routes.py:117-121,390-422`: capability/auth/disabled-route behavior to mirror.
  - `web/src/pages/QaPage.test.tsx` and `web/src/components/QaWorkspace/QaWorkspace.test.tsx`: QA navigation and stable source UI seams.
  - `web/e2e/qa.spec.ts` and `web/e2e/qa-runtime.py`: real-server deterministic E2E pattern without production fakes.
  - `web/playwright.config.ts`: exact desktop/tablet/mobile projects and snapshot path convention.
- Configuration/runtime facts:
  - `api/config.py:12-19` has only `qa_route_enabled`; Notes needs a separate default-on `NOTES_ROUTE_ENABLED` flag.
  - `web/package.json` pins exact package versions and has runnable `test`, `typecheck`, `lint` and `build` scripts.
  - `compose.yaml` is the Compose source; `deploy/smoke_test.py --env-file deploy/.env` is the existing Docker smoke entry point.
  - Current supported topology remains one application process/worker; no packet may claim distributed safety.
- Existing worktree changes to preserve:
  - `docs/agent-workflow/task-packets/2026-08-30-notes-vertical-slice/` artifacts created by this review are intentionally uncommitted until the user requests a commit.

## Findings

### Blocking

- None.

### Required revisions

- Applied before acceptance: use the approved `/api/v1/notes` contract with `PATCH`, `expected_version`, `POST /clear`, `POST /projections/retry` and stable uppercase errors.
- Applied before acceptance: replace nonexistent test, Playwright and deployment paths with repository-real paths and commands.
- Applied before acceptance: expand Penpot authority from eight representative boards to fifteen boards covering projection failure, empty, filter and source states required by the specification.
- Applied before acceptance: make plan review/packetization a pre-implementation gate and reserve final integration review for completed packets.

### Non-blocking notes

- The Penpot packet depends on an authenticated, connected Penpot file. It remains ready because the exact prerequisite and stop condition are explicit; the worker must block if fresh read/direct export is unavailable.
- FTS5 is required. Startup must fail safely if unavailable rather than silently degrading to unbounded scans.
- Docker verification remains a final release gate; daemon unavailability blocks acceptance rather than weakening the gate.
- Packet 05 reality check found two pre-cutover tests that required new Notes to be written to `history.json`. Their ownership moved into Packet 05 so the tests retain concurrency, snapshot and isolation coverage against the authoritative NoteService instead of forcing the forbidden legacy fallback.
- Packet 06 initially blocked because both required local services were stopped. The official Penpot MCP bridge/plugin and Docker Desktop Linux engine were restored on 2026-08-31; the packet resumed with every original release gate intact.
- Packet 06 then found a material runtime/Penpot hierarchy mismatch after real Axe and original-size comparison. The deviation was not accepted; corrective Packet 07 owns Notes-only visual alignment, and Packet 06 now depends on its completion before snapshot acceptance.
- Packet 06's final full-suite gate found eight pre-cutover tests that still observe new Notes through `history.json.notes` or instantiate runtimes without the required injected NoteService. The accepted architecture already resolves the product decision: SQLite/FTS5 remains authoritative and legacy JSON Notes are migration input only. Corrective Packet 08 therefore owns test-contract migration without production edits or weakened invariants; Packet 06 resumes only after Packet 08 passes the complete Python suite.
- Corrective Packet 08 completed through `f0a1595`. Its independent re-review found no Critical, Important or Minor issues after positive restart/backup controls and exception-safe service cleanup were added; exact eight and focused regressions pass without production edits. Packet 06 may resume with its original release criteria intact.
- Packet 06's next full run exposed one order-sensitive projection test: equal wall-clock timestamps let the scheduler's documented opaque-ID tie-break choose delete before upsert. Focused rerun passed and either production order remains deletion-safe. Corrective Packet 09 owns deterministic test timestamps only; it may not change scheduler behavior or assertions. Packet 06 resumes after Packet 09 and a fresh full suite.
- Corrective Packet 09 completed through `6742ba0`; independent review approved its deterministic two-timestamp setup, unchanged stale-upsert/delete assertions and zero production changes. Target repeated 10/10, projection module passed 12 tests and the focused Notes backend passed 65 tests. One omitted intermediate docs-only provenance commit is non-blocking and must be noted in final integration review.
- Packet 06 task review found the authenticated mobile editor does not keep the approved top Save and bottom source/delete actions visible at `390×844`, and its handoff overstates mobile bulk-clear UI coverage. Corrective Packet 10 owns Notes-only mobile editor geometry, one reviewed editor snapshot, accurate bulk-clear scope and the stale Notes MCP version provenance. It may not invent a mobile menu or change backend semantics.
- Packet 10's first fresh all-project E2E run exposed a cross-breakpoint regression: mobile passed, but moving the single Save DOM node into the heading changed the accepted desktop and tablet visuals. Corrective Packet 11 must restore their footer placement with the same DOM node while retaining the mobile header geometry; snapshot updates are forbidden.
- During Packet 11 verification, the Notes E2E reached `12/12`, but two full frontend runs exposed authentication test races that pass `28/28` in isolation: router state/dialog presence can precede the React DOM/focus commit. Corrective Packet 12 owns async observation in the two test modules only; production auth behavior, retries and global test settings are forbidden.
- Packet 11 independent review rejected CSS-only absolute relocation because mobile DOM focus would visit source, jump to top Save, then return to delete. The packet was revised to permit an SSR-safe `matchMedia` subscription that mounts the one Save control in the semantic mobile header or desktop/tablet footer. CSS `reading-flow` was rejected as the sole fix because Firefox/Safari still lack support; no simultaneous duplicate is permitted.

## Accepted scope

- Goal: replace `/notes` migration UI with a production-shaped, authenticated, responsive Note slice whose SQLite aggregate is authoritative and whose Memory representation is recoverable projection data.
- In scope: Penpot source boards; Note schema/models/repository/FTS; deterministic legacy migration; durable Memory outbox; optimistic concurrency/tombstones; source scrubbing; Note service/API; React data/UI/Markdown; QA save-to-note; Gradio cutover; E2E, design, regression and Docker gates.
- Out of scope: folders, backlinks, collaboration, visible revision history, offline editing, AI rewriting, document text selection, multi-process/distributed coordination and broader product-route implementation.
- Compatibility requirements: retain legacy route and handler names; preserve history JSON without new Note writes; retain documents/questions/reports when clearing Notes; keep existing QA/document API contracts; feature-disable only access, not migration/recovery.
- Architecture/data-isolation constraints: authenticated user ID only; composite ownership in every SQL operation; server-resolved source snapshots; fenced transactional source scrub; no stale update/tombstone resurrection; stable Memory IDs; exact legacy cleanup; no browser draft persistence; single-process topology remains explicit.

## Packet graph

| Packet | Depends on | Parallel-safe | Owned files | Outcome |
|---|---|---:|---|---|
| `01-penpot-notes-source.md` | none | yes | Penpot handoff/map/exports and design contract tests | Fifteen authoritative Notes boards with fresh-read evidence |
| `02-note-domain-core.md` | none | yes | Note schema/models/repository/migration/projection/service and focused tests | Durable isolated Note aggregate and recoverable projection |
| `03-notes-api-lifecycle.md` | `02` | no | bootstrap/runtime/deletion/API and integration tests | Authenticated Notes API, source deletion consistency and worker lifecycle |
| `04-notes-react-workspace.md` | `01`, `03` | no | package manifests and Notes-only React modules/routes/tests | Responsive safe-Markdown Notes workspace |
| `05-qa-legacy-notes-integration.md` | `03`, `04` | no | QA workspace, assistant/Gradio Note paths, and legacy integrity/isolation regression tests | QA and legacy entry points converge on NoteService without JSON fallback |
| `06-notes-release-acceptance.md` | `01`–`05`, `07`, `08`, `09`, `10`, `11`, `12` | no | Notes E2E runtime/spec/snapshots and release/handoff documentation | Combined product, visual, regression and Docker acceptance evidence |
| `07-notes-visual-alignment.md` | `04`, `05`; consumes `9f7ba2c` checkpoint | no | NotesPage and NotesWorkspace React composition/styles/tests only | Runtime hierarchy and three responsive modes align to Penpot revision 153 |
| `08-notes-legacy-regression-contract.md` | `02`, `03`, `05`, `07` | no | Three legacy regression test modules and its handoff only | Full-suite invariants observe authoritative Notes without restoring JSON dual-write |
| `09-note-projection-order-test.md` | `02`, `08` | no | One projection regression test and its handoff only | Stale-upsert test deterministically exercises its intended queue path |
| `10-notes-mobile-editor-acceptance.md` | `01`, `04`, `07`, `09` | no | Notes editor/CSS/tests, mobile editor snapshot and acceptance provenance | Mobile editor aligns to Penpot and clear/MCP evidence is accurate |
| `11-notes-save-responsive-placement.md` | `07`, `10` | no | Notes editor/CSS/tests and corrective handoff; no snapshots | One Save node occupies the approved desktop/tablet footer and mobile header positions |
| `12-auth-test-commit-waits.md` | `10` | no | Two authentication test modules and corrective handoff only | Full-suite tests await the same committed route/focus outcomes they assert |

## Packet readiness audit

| Packet | Goal/non-goals | Context/interfaces | Prerequisites | Change boundary | Acceptance/tests | Forbidden changes | Handoff format | Ready |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `01-penpot-notes-source.md` | yes | yes | yes | yes | yes | yes | yes | yes |
| `02-note-domain-core.md` | yes | yes | yes | yes | yes | yes | yes | yes |
| `03-notes-api-lifecycle.md` | yes | yes | yes | yes | yes | yes | yes | yes |
| `04-notes-react-workspace.md` | yes | yes | yes | yes | yes | yes | yes | yes |
| `05-qa-legacy-notes-integration.md` | yes | yes | yes | yes | yes | yes | yes | yes |
| `06-notes-release-acceptance.md` | yes | yes | yes | yes | yes | yes | yes | yes |
| `07-notes-visual-alignment.md` | yes | yes | yes | yes | yes | yes | yes | yes |
| `08-notes-legacy-regression-contract.md` | yes | yes | yes | yes | yes | yes | yes | yes |
| `09-note-projection-order-test.md` | yes | yes | yes | yes | yes | yes | yes | yes |
| `10-notes-mobile-editor-acceptance.md` | yes | yes | yes | yes | yes | yes | yes | yes |
| `11-notes-save-responsive-placement.md` | yes | yes | yes | yes | yes | yes | yes | yes |
| `12-auth-test-commit-waits.md` | yes | yes | yes | yes | yes | yes | yes | yes |

No packet may have `status: ready` while any readiness column is `no`.

## Integration verification

- `& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q --basetemp=.runtime/pytest-notes-full`
- `& 'D:\python_self_agent\venv\Scripts\python.exe' -m pip check`
- `Set-Location web; npm test -- --run; npm run typecheck; npm run lint; npm run build; npx playwright test e2e/notes.spec.ts --workers=1; npm audit --audit-level=moderate; Set-Location ..`
- `node --test tests/design/test_penpot_handoff.mjs tests/design/test_penpot_component_map.mjs`
- `docker compose config`
- `docker compose --env-file deploy/.env up --build -d`
- `& 'D:\python_self_agent\venv\Scripts\python.exe' deploy/smoke_test.py --env-file deploy/.env`
- `docker compose --env-file deploy/.env down`
- `git diff --check`

## Final integration review requirement

- Output:
  `docs/agent-workflow/task-packets/2026-08-30-notes-vertical-slice/FINAL_INTEGRATION_REVIEW.md`
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
