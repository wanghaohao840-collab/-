---
id: "document-library-vertical-slice-06"
title: "Verify the real vertical slice in three viewports"
status: "ready"
parallel-safe: false
depends-on: ["document-library-vertical-slice-05"]
base-commit: "f90883e71d2fa73a7cb981b11478b68519d8ce80"
owner: "unassigned"
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
- Create: six `documents-empty|complete-{desktop|tablet|mobile}.png` files under `web/e2e/visual.spec.ts-snapshots/`
- Modify: `tests/deploy/test_document_library_contract.py`
- Modify: this packet for handoff.

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

- [ ] Real functional and cross-user scenarios pass in desktop/tablet/mobile.
- [ ] Document axe/focus/mobile-target checks pass with no serious/critical violation.
- [ ] Exactly six new snapshots pass no-update and human Penpot comparison.
- [ ] Design contract remains strict and all feature/full gates pass.
- [ ] Final process/runtime/generated/secret/path scans are clean.

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

Replace with template handoff, including exact totals per command/project, six snapshot names/dimensions, human comparison notes, process/runtime cleanup evidence, scope confirmation, deviations/risks and commit.
