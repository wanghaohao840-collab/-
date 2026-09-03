---
id: "qa-vertical-slice-06h"
title: "Align existing E2E contracts with QA handoff"
status: "done"
parallel-safe: false
depends-on: ["qa-vertical-slice-06g"]
base-commit: "2930425"
owner: "codex"
---

# Task Packet: Align existing E2E contracts with QA handoff

## Goal

Update pre-QA browser contracts for the approved document-to-QA handoff, asynchronous fenced document deletion, and the remaining migration-route legacy CTA without changing production behavior.

## Non-goals

- No product code, API, schema, deletion worker, focus logic or layout changes.
- No screenshot threshold relaxation or unrelated baseline regeneration.

## Delivery context

Packet 07's complete 60-test Playwright gate produced four identical failures per viewport. All are stale acceptance expectations: keyboard traversal omitted the new “开始问答” action, `/qa` was still treated as a migration page, deletion expected obsolete synchronous `204`, and document-complete baselines predate the approved handoff button. Visual diff inspection confirms only the handoff action changed.

## Explicit change boundary

### Allowed files

- Modify: `web/e2e/accessibility.spec.ts`, `web/e2e/auth-shell.spec.ts`, `web/e2e/documents.spec.ts`, `web/e2e/visual.spec.ts`
- Update: exactly three `documents-complete-*.png` baselines
- Modify: this packet and Packet 07 dependency metadata

### Forbidden changes

- No production source, dependency, global threshold or other snapshot changes.
- No test skips or weakened accessibility/isolation assertions.

## Acceptance criteria

- [x] Keyboard order asserts filter → “开始问答” → delete, then proves delete-dialog focus trap and return.
- [x] Exact legacy redirect/CTA remains proven through an unimplemented product route.
- [x] Document deletion asserts `202`, an opaque deletion ID, and eventual row removal after the fence.
- [x] Exactly three reviewed document-complete baselines include “开始问答”.
- [x] Focused three-viewport E2E passes without snapshot update, then the full suite passes.

## Verification commands

```powershell
Push-Location web
npm run build
npx playwright test e2e/accessibility.spec.ts e2e/auth-shell.spec.ts e2e/documents.spec.ts e2e/visual.spec.ts
npm run test:e2e
Pop-Location
git diff --check
```

## Stop conditions

Stop if any correction requires product edits, removing a scenario, masking dynamic regions, broadening screenshot tolerance or updating more than three existing baselines.

## Implementation handoff

- Packet: `qa-vertical-slice-06h`
- Status: `done`
- Delivered:
  - Existing browser contracts now include the document-to-QA action, asynchronous deletion fence and the remaining migration-page legacy CTA.
- Files changed:
  - `web/e2e/accessibility.spec.ts` — proves filter → ask → delete order plus the existing dialog trap/return.
  - `web/e2e/auth-shell.spec.ts` — verifies the migration CTA on `/notes` while retaining exact `/legacy/` redirect coverage.
  - `web/e2e/documents.spec.ts` — verifies `202`, opaque deletion identity, target type and eventual removal.
  - Three `documents-complete-*.png` baselines — add only the approved “开始问答” action.
- Interfaces added or changed:
  - no production interfaces or behavior changed.
- Acceptance evidence:
  - [x] Visual expected/actual/diff were inspected at desktop and mobile; tablet had the identical approved delta.
  - [x] Focused affected matrix: 12 passed across three viewports without snapshot updates.
  - [x] Complete Playwright suite: 58 passed, 2 project-conditional skips, 0 failed; QA 12/12 with 0 skipped.
- Verification:
  - `npm run build` — PASS.
  - focused 12-scenario Playwright command — PASS, 12 tests.
  - `npm run test:e2e` — PASS, 58 passed / 2 project-conditional skipped / 0 failed.
  - `git diff --check` — PASS (line-ending notices only).
- Scope confirmation:
  - production files untouched: yes
  - exactly three existing global baselines updated: yes
- Deviations:
  - none.
- Residual risks/follow-ups:
  - none within this correction.
- Commit:
  - `4fc6122`
