---
id: "document-search-03"
title: "Responsive document search workflow"
status: "done"
parallel-safe: false
depends-on: ["document-search-02"]
base-commit: "daeb24f"
owner: "Codex-inline"
---

# Task Packet: Responsive document search workflow

## Goal

`/search` provides a real desktop/tablet/mobile workflow from explicit scope through results, detail/copy, QA handoff and editable sourced note.

## Non-goals

- Penpot source mutation without fresh evidence, online search, history, reranking, new component library or unrelated page redesign.

## Delivery context

The protected route exists only as a migration placeholder. Existing documents, QA and notes features supply the integration patterns; search privacy requires mutation state rather than query URLs or durable browser storage.

## Relevant files and current interfaces

- `web/src/App.tsx:25` placeholder routing.
- `web/src/pages/QaPage.tsx:18` document handoff.
- `web/src/pages/NotesPage.tsx:8-40` allow-listed source/query state and server-backed editor.
- `web/src/features/documents/queries.ts` user document list.
- Packets 01–02 API DTOs and document source request.

## Prerequisites

- Packets 01 and 02 done and their focused tests passing.
- Existing Node dependencies installed; no new package expected.

## Explicit change boundary

- Create the search feature API/query/tests, SearchPage/tests, search CSS and search E2E listed in Plan Task 3.
- Modify only App/main wiring, validated QA/Notes handoffs and placeholder test list.
- Reviewed test-isolation correction: `web/e2e/python-runtime.ts` and `web/tests/python-runtime.test.ts` may be changed only to strip inherited service/Python overrides and force `PYTHON_DOTENV_DISABLED=1` in child test processes. Preserve interpreter resolution and callers. Do not change real `.env`, production startup or application behavior.
- Narrow reviewed ownership correction: also modify `web/src/features/notes/types.ts`, `web/src/components/NotesWorkspace/NoteSourcePanel.tsx`, `web/src/components/NotesWorkspace/NotesWorkspace.tsx` and `NotesWorkspace.test.tsx` in that same component directory, only for the new source type, correct document-source labels/filter options, editable-prefill seam if needed, and their tests. No unrelated editor redesign.
- Forbidden: localStorage/query-string search text or excerpt, optimistic fake results, trusting client source snapshots, changing auth/cookie semantics, backend files, bulk screenshot acceptance, unrelated visual baselines.

## Interface contract

### Packet 02 concrete producer contract

POST `/api/v1/notes` accepts the existing editable note fields and this source object:

```json
{"kind":"document_chunk","locator":{"document_id":"00000000-0000-0000-0000-000000000001","chunk_id":"chunk-0","chunk_index":0,"content_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}}
```

The server re-resolves the source; do not submit title/excerpt/page snapshots as source authority. New notes return 201; an idempotent replay returns 200. Changed/foreign/missing sources return `NOTE_SOURCE_NOT_FOUND` (404), deletion races `NOTE_SOURCE_DELETING` (409), temporary resolver failure `NOTE_SOURCE_UNAVAILABLE` (503, retryable). Reusing a request ID with changed content returns the existing idempotency conflict. Responses and `source_kind` filtering now support `document_chunk`; deleted sources return null identity/locator/title/excerpt. Old QA selector shapes and digests are unchanged. The exact source resolver is internal, not a preview HTTP endpoint. Check the existing editor prefill seam before implementing cross-page handoff; never invent a preview endpoint or place source text in a URL.

- Consumes: POST search DTO, documents query, note create selector, QA document handoff.
- Produces: SearchPage and typed search mutation; origin-user/request-safe state; source actions.
- Invariants: logout unmounts private results; changed query/scope disables old actions; excerpts are rendered as text, not HTML.

## Required behavior

- Explicit 1–10 picker, 1–1000 query, 5/10/20 limit, distinct idle/loading/success-empty/error/stale-source states.
- Literal-only safe highlighting; retrieval score is not confidence. Copy handles denied clipboard.
- QA requires existing range confirmation; note is editable before save and opens after success.
- 1440×1024, 1024×768, 390×844; ≥44px mobile targets; focus-managed details; no serious/critical axe issue or horizontal overflow.

## Implementation guidance

Editable-note seam correction: NotesPage has no source-preview endpoint or transient prefill channel. Keep the editable draft in a focus-managed search detail dialog, then navigate to `/notes?note=<saved-id>` only after successful server creation. This satisfies edit-before-save without storing excerpts in URL/history state or inventing a preview API. Source authority remains locator-only. An unchanged failed save reuses its request ID; editing the draft creates a new request identity.

Implementation details: the documents API helper is reused through a session-instance-scoped query under the existing `documents` invalidation prefix. This avoids a freshly authenticated search page observing another session's pending list query. Query text/results use a nonpersistent mutation with generation guards. The detail/editor uses the browser's native modal focus containment on all viewports (desktop/tablet bounded panel, mobile scrollable single column), rather than a modeless desktop split pane. No Penpot source update is claimed. Preserve the existing `/legacy/` secondary entry in the page header; the shell compatibility test still verifies it.

Reality correction (2026-09-04, before UI implementation): `NoteSourcePanel.tsx:15` labels every non-QA-citation source as a QA answer; `NotesWorkspace.tsx:67,78` offers only two QA source filters. The original ownership list omitted these consumers. The expanded narrow boundary above is necessary for the already-approved document-source workflow. Update these labels/options with tests, rather than relying on the old fallback. Backend code remains forbidden in this packet.

Follow Plan Task 3. Use authenticated request helper and a mutation. Capture username/request fingerprint in mutation context; ignore late mismatched success. Use semantic buttons/labels/live regions. Reuse tokens and overlay patterns.

## Acceptance criteria

- [x] Browser-test child environments cannot load workstation dotenv or inherit LLM/Neo4j/Qdrant/RAG credentials; all existing fixture callers share the tested helper.

- [x] All UI states and stale/late/user-change safety pass unit tests.
- [x] Copy, QA and sourced-note workflows pass.
- [x] Three viewport E2E and accessibility/overflow pass.
- [x] Typecheck, lint, build and existing shell visual regressions pass.

## Test and verification commands

From `web`: `npm test -- --run --maxWorkers=2`; `npm run typecheck`; `npm run lint`; `npm run build:app`; with isolated env run `npx playwright test e2e/search.spec.ts e2e/auth-shell.spec.ts e2e/visual.spec.ts`.

Expected: zero failures; only predeclared viewport skips.

## Stop conditions

Stop if prerequisite DTOs differ, handoff requires a hidden server mutation, Penpot change is required but unavailable, or an unlisted file is necessary.

## Implementation handoff

Report states/actions/viewports, exact files/interfaces, test counts, snapshot facts, accessibility, scope confirmation, deviations/risks and `not committed`.

## Reality-conflict report — 2026-09-04 browser isolation

- Packet: `document-search-03`
- Status at pause: blocked
- Expected by packet: isolated JSON/simple-embedding browser acceptance independent of production services.
- Observed: `web/e2e/python-runtime.ts:84` removes only E2E_PYTHON/PYTHONPATH. `hello_agents/core/llm.py` loads dotenv at import; the worktree's parent can therefore contribute service configuration. The combined browser run failed restarting the tablet server with repeated `Unable to retrieve routing information` and a 45-second startup timeout. `tests/conftest.py:12` already addresses this for pytest, but not Playwright children.
- Impact: unreliable verification and unintended external connectivity; do not treat the partial browser run as a clean isolated acceptance.
- Work before pause: frontend workflow plus 187 frontend tests, successful initial three-viewport search flow, 18 token/component-contract tests, successful build/lint/typecheck and npm audit (0 vulnerabilities). Combined browser run stopped. No application/production configuration change.
- Recommended resolution: revise packet to include the existing shared E2E environment helper and its tests.
- Decision required: Codex adjudication of that narrow test boundary.
- Resolution: accepted. All three E2E fixtures already call the same helper; strip inherited LLM_/OPENAI_/DEEPSEEK_/NEO4J_/QDRANT_/RAG_ variables and Python overrides, disable dotenv in the child environment, retain PATH/system variables and existing interpreter checks. Add case-insensitive stripping and input-immutability regression tests. Repeat the entire browser combination against the final build. No fixture timing extension, mocked production fallback, new provider or production mutation. Packet returns to ready with dependencies unchanged.

### 2026-09-04 implementation handoff

- Status: **done** in the isolated `codex/bge-m3-runtime-identity` worktree; not copied into the stable application or published.
- Added files: `web/src/features/search/{types.ts,api.ts,api.test.ts,queries.ts,queries.test.tsx}`, `web/src/pages/{SearchPage.tsx,SearchPage.test.tsx}`, `web/src/styles/search.css`, `web/e2e/search.spec.ts`.
- Modified files: App/main wiring; NotesPage source filter; notes types; NotesWorkspace source labels, filter options and tests; DocumentsPage placeholder-route test; shared E2E Python environment helper/tests. QA page/API, application backend and existing visual snapshots were not changed by this packet.
- Behavior: explicit 1–10 documents, trimmed 1–1000 query, 5/10/20 window; structured safe-text results and literal highlights; returned-window count (not total cardinality); real optional page/section; non-confidence score label; idle/loading/empty/error/stale states; scope/session/request-generation invalidation; clipboard success/failure; native modal focus/escape/return; guarded editable note save with locator-only source and unchanged-payload request-ID reuse; existing QA range confirmation; preserved `/legacy/` entry.
- Saved source behavior: direct document source labels and filters coexist with unchanged QA source selectors. Real E2E verifies source deletion nulls locator/excerpt while retaining the user's edited note body. A user change while saving removes the old scope/draft and suppresses late navigation/cache publication.
- Current-code verification:
  - `npm test -- --maxWorkers=2`: **188 passed / 24 files**, exit 0 (72.79 s).
  - Final search API/hook/page focused tests: **18 passed**; new E2E environment helper tests: **5 passed**, including two pre-fix RED failures.
  - `npm run typecheck`; `npm run lint`; `npm run build:app`: passed. Bundle warning remains nonblocking: main JS 534.10 kB (164.09 kB gzip); no new package added.
  - With `APP_COOKIE_SECURE=false`, `npm exec -- playwright test e2e/search.spec.ts e2e/auth-shell.spec.ts e2e/visual.spec.ts --output=test-results-search-isolated-final`: **31 passed, 2 expected desktop/tablet mobile-drawer skips**, exit 0 (4.9 min). Final run uses the corrected isolated child environment and final application build. No snapshot acceptance/update was used.
  - All three real search flows cover import, search, detail, clipboard, edited note creation/open, QA confirmation, document deletion and source redaction. Serious/critical axe findings and horizontal overflow: **zero** in checked states. Desktop/tablet/mobile screenshots inspected; scrollable mobile note form/save remains reachable.
  - `node --test tests/design/test_design_tokens.mjs tests/design/test_penpot_component_map.mjs tests/design/test_penpot_handoff.mjs`: **18 passed**; token generator `--check`: passed. These are repository contract checks, not fresh remote Penpot readback.
  - `D:/python_self_agent/venv/Scripts/python.exe -m pytest -q --tb=short --basetemp=D:/python_self_agent/deploy-state/pytest-search-ui-final-20260904`: **1756 passed, 8 skipped, 1 existing local-Qdrant payload-index warning**, exit 0 (542.14 s). This run includes the final Packet 02 session handling.
  - `git diff --check` passed for modified tracked packet files (only CRLF notices).
- Evidence retained under `.runtime/search-ui-evidence-20260904/test-results-search-isolated-final/` in this worktree; prior single legacy-regression rerun retained alongside. Final build index SHA-256: `AC8A340325EAF0F288C1F9935E0050154DD867F41A23B19A1E784A46DD288188`; lockfile SHA-256: `F16D23D3AF0E9916967A88B1C2E83A99F712035DBF5FE09CC046D24A78084D0D`.
- Prior failures are not hidden: an initial combined run caught the missing legacy entry, then a browser restart timed out because the old fixture loaded workstation dotenv/Neo4j settings. The run was stopped, the header entry restored, the helper boundary reviewed/fixed, and the full combination rerun successfully. Production configuration was not edited; no Neo4j container was started.
- Audit: `npm audit --audit-level=moderate` completed with **0 vulnerabilities** for the unchanged lockfile. Later `--audit-level=low` reruns (20 s/no retries and 60 s/one retry) timed out at the public npm bulk advisory endpoint; a bounded independent connectivity probe also timed out. Do not present those reruns as a successful fresh release security gate. Packet 04 must obtain a successful fresh audit before publication.
- Deviations: editable note stays in an in-memory search detail form until persisted (no invented preview endpoint or history state); native modal detail is shared across viewports instead of a modeless desktop split; test environment isolation correction is documented above. No Penpot source change is claimed.
- Scope: only packet-owned code/tests and work records changed. Existing dirty/staged/untracked work, production `.env`, containers, model/index identity and user data preserved. Test-only artifacts were retained in the ignored `.runtime` evidence directory.
- Next gate: Packet 04 documentation/stable integration/safe publication, fresh security audit, real BGE-M3/LLM acceptance and final integration review. **Not committed, not pushed.**
