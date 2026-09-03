---
id: "document-library-vertical-slice-06"
title: "Verify the real vertical slice in three viewports"
status: "done"
parallel-safe: false
depends-on: ["document-library-vertical-slice-05b"]
base-commit: "f90883e71d2fa73a7cb981b11478b68519d8ce80"
owner: "Codex"
---

# Task Packet: Verify the real vertical slice in three viewports

## Goal

Produce repeatable real-unified-server evidence that the completed document library works, is isolated and accessible, matches approved visuals, and leaves no runtime residue.

## Non-goals

- Do not change product/backend/frontend behavior to make tests pass.
- Do not add test-only routes, environment backdoors, route fulfillment, history injection, sleeps or mocked authentication.
- Do not update unrelated visual snapshots or Penpot source/reference exports.

## Delivery context

All product behavior exists after Packet 05. Current Playwright fixture launches the real single-worker `server:app` with an isolated data root and provides start/stop/restart. This packet adds acceptance only. Timing-sensitive failure/cancel cases stay in service/worker integration unless legitimate bounded inputs expose them reliably.

## Relevant files and current interfaces

- `web/e2e/fixtures.ts` — real Uvicorn lifecycle, disposable `.runtime/zhiyan-playwright-*`, registration helper and cleanup.
- `web/playwright.config.ts` — one worker, Chromium and exact three viewport projects.
- `web/e2e/accessibility.spec.ts` — axe and focus conventions.
- `web/e2e/visual.spec.ts` — real validation/outage/session flows and no-update snapshot pattern.
- `tests/deploy/test_document_library_contract.py` — Packet 01 design/export contract; may be extended but not weakened.
- Packet 05 production DOM/accessibility labels and all previous handoff evidence are authoritative.
- Existing changes to preserve: completed prerequisite commits and planning artifacts.

## Prerequisites

### Packet dependencies

- `document-library-vertical-slice-05` must be `done` (transitively all earlier packets).

### Repository/base state

- Base ancestor `f90883e...` plus Packets 01–05.
- `web/dist/index.html` must be freshly built before Playwright.

### External prerequisites

- Mandated project venv imports uvicorn/FastAPI/Gradio and passes `pip check`.
- Installed Chromium revision required by Playwright 1.62.1.

## Explicit change boundary

### Allowed files

- Create: `web/e2e/documents.spec.ts`
- Modify: `web/e2e/accessibility.spec.ts`
- Modify: `web/e2e/visual.spec.ts`
- Modify: `web/e2e/auth-shell.spec.ts`
- Modify: `web/tests/visual-acceptance-contract.test.ts`
- Create: six `documents-empty|complete-{desktop|tablet|mobile}.png` files under `web/e2e/visual.spec.ts-snapshots/`
- Modify: `tests/deploy/test_document_library_contract.py`
- Modify: this packet for handoff.
- Modify: `.superpowers/sdd/progress.md` for packet status only.

### Allowed behavior changes

- Acceptance tests/contracts/snapshots only.

### Forbidden changes

- Do not edit any production code, API/service, fixture lifecycle, config, dependency, Penpot handoff/reference or unrelated snapshot.
- Do not relax assertions/skips/timeouts globally or use snapshot update beyond six exact new files.
- Do not leave Python/Uvicorn/browser processes, `.runtime` data roots, uploads or generated output tracked.

## Interface contract

### Consumes

- Real registration/login/session/CSRF from existing fixture and AuthProvider.
- Real `/api/v1/documents` and `/api/v1/imports` from Packet 04; DOM/labels from Packet 05.
- Direct Penpot references from Packet 01.

### Produces

- Functional test: register → upload real in-memory Markdown/TXT → terminal success → document list → delete → absent after restore.
- Cross-user API isolation for batch/task/document IDs.
- axe serious/critical zero and keyboard/focus/44px evidence for document states/overlays.
- Six no-update visual baselines across empty and complete states.
- Contract asserts exact design and browser snapshot sets/dimensions.

### Invariants

- No request interception/fulfillment, history/session injection or production test hook.
- Each worker uses one disposable data root and one Uvicorn process; cleanup succeeds even after failure.
- Only test-created records appear; no fabricated production content.

## Required behavior

- All three viewports complete real upload/list/delete and preserve session/auth behavior.
- Second user receives 404-equivalent behavior for first user's IDs and cannot mutate them.
- Empty, import overlay/sheet, populated list and delete dialog have one h1, visible focus, focus trap/return, scroll restore and axe serious/critical zero.
- Six screenshots are generated only after human comparison to Penpot; structural differences require product correction/reality conflict, not masking.
- Running cancel/retry remains covered by prior deterministic tests if browser timing cannot prove it without flaky waits.

## Implementation guidance

Follow Task 6 in the plan. Create legitimate in-memory files with Playwright `setInputFiles`. Poll through visible UI or response state with bounded assertions, never fixed sleeps. Use a second browser context for isolation. Run focused desktop RED/GREEN before the full three-project suite. After any failure verify and clean only the fixture-owned exact data root.

## Acceptance criteria

- [x] Real functional and cross-user scenarios pass in desktop/tablet/mobile.
- [x] Document axe/focus/mobile-target checks pass with no serious/critical violation.
- [x] Exactly six new snapshots pass no-update and human Penpot comparison.
- [x] Design contract remains strict and all feature/full gates pass.
- [x] Final process/runtime/generated/secret/path scans are clean.

## Test and verification commands

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pip check
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/test_import_models.py tests/test_import_repository.py tests/test_import_service.py tests/test_import_worker.py tests/test_document_library_service.py tests/api tests/deploy/test_document_library_contract.py --basetemp=.runtime/pytest-document-library-final
Set-Location web
npm test
npm run typecheck
npm run lint
npm run build
npx playwright test --workers=1
Set-Location ..
node --test tests/design/test_penpot_component_map.mjs
node scripts/design_tokens.mjs --check design/tokens/zhiyan.tokens.json web/src/styles/tokens.css
git diff --check
```

Expected: all exit 0; Playwright has no new skip/failure; Python/Uvicorn process count and `zhiyan-playwright-*` root count are zero after completion.

## Stop conditions

Stop on any standard reality conflict, incomplete Packet 05, need for production/fixture/config changes, unstable timing requiring sleeps/hooks, unexplained Penpot mismatch, unrelated snapshot update or cleanup outside exact owned roots.

## Implementation handoff

- Status: done.
- Implementation commit: `af469ba`.
- Functional evidence: real register/upload/list/delete/restore and cross-user batch/task/document isolation pass in desktop, tablet and mobile without request interception, session/history injection or test-only routes.
- Accessibility evidence: empty/import and populated/delete states pass axe serious/critical zero, visible 2 px focus, focus trap/return, scroll restoration and mobile 44 px target assertions.
- Visual evidence: focused no-update run passes `6/6`; full E2E passes `46`, with the two existing non-mobile More-drawer conditional skips.
- Accepted snapshots:
  - `documents-empty-desktop.png` and `documents-complete-desktop.png` — `1440 × 1024`.
  - `documents-empty-tablet.png` and `documents-complete-tablet.png` — `1024 × 768`.
  - `documents-empty-mobile.png` and `documents-complete-mobile.png` — `390 × 844`.
- Human comparison: the Empty card/copy/action/limit-note structure matches the authoritative Empty board in all breakpoints; Complete is list-first with no terminal-success panel and matches toolbar/filter/panel/row geometry. The browser shows only its one real imported record instead of Penpot's illustrative samples, as required; documented browser font rasterization remains the only rendering distinction. The volatile real-server completion minute is normalized only inside the visual test after the real import succeeds so future no-update pixels stay deterministic.
- Gates: `pip check` clean; Python `227 passed`; frontend `102 passed`; typecheck, lint and production build pass; document design/browser PNG contract `6 passed`; component map `6 passed`; token and diff checks pass.
- Cleanup: `zhiyan-playwright-*` runtime roots `0`, owned runtime files `0`, owned Python/Uvicorn processes `0`; no generated runtime output is tracked.
- Scope: only Task 6 acceptance files, the six exact PNGs, the reconciled exact-baseline contract and packet/progress ledgers changed. No production, fixture, configuration, dependency, Penpot source/reference or unrelated snapshot file changed.
- Deviations/risks: none. The packet is ready for final integration review.

## Reality-conflict resolution

- Baseline conflict: `web/e2e/auth-shell.spec.ts` still asserted that `/documents` exposes the migration CTA, but Packet 05 intentionally replaced that route with the real document library.
- Decision: Task 6 owns the acceptance-only update that moves the migration CTA assertion to `/qa`, which remains a `MigrationPage`. This preserves both the legacy redirect/CTA contract and the new `/documents` behavior.
- Scope impact: `web/e2e/auth-shell.spec.ts` is added to the allowed files; production, fixtures, configuration and acceptance criteria are unchanged.
- Visual conflict: the first desktop empty actual exposed unapproved structural differences from `documents-empty.png`. Corrective Packet `document-library-vertical-slice-05a` owns the production alignment; Task 6 resumes only after that packet is independently approved.
- Complete-state conflict: focused visual generation exposed an extra terminal-success summary above the document list in all three viewports. Corrective Packet `document-library-vertical-slice-05b` removes that unapproved visual/semantic block while preserving active and failed imports.
- Snapshot-contract conflict: the existing visual acceptance unit test fixes the pre-slice baseline set at sixteen files, so the six required and reviewed document-library snapshots make the full frontend suite fail by design. Task 6 owns the acceptance-only update from the exact sixteen-file set to the exact twenty-two-file set; network/history prohibitions and every existing baseline remain unchanged.
- Empty-state accessibility conflict: corrective Packet 05a intentionally removes the filename filter when the document list is truly empty, but Task 6's mobile 44 px assertion still queried it in that state. The assertion moves to the populated-document scenario, where the filter exists; empty import targets, populated filter/delete targets and all dialog targets remain covered.
