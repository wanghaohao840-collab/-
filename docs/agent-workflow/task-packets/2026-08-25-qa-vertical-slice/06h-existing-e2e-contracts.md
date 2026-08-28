---
id: "qa-vertical-slice-06h"
title: "Align existing E2E contracts with QA handoff"
status: "in_progress"
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

- [ ] Keyboard order asserts filter → “开始问答” → delete, then proves delete-dialog focus trap and return.
- [ ] Exact legacy redirect/CTA remains proven through an unimplemented product route.
- [ ] Document deletion asserts `202`, an opaque deletion ID, and eventual row removal after the fence.
- [ ] Exactly three reviewed document-complete baselines include “开始问答”.
- [ ] Focused three-viewport E2E passes without snapshot update, then the full suite passes.

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

Pending implementation and verification.
