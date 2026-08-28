---
id: "qa-vertical-slice-06e"
title: "Show current-answer sources by default"
status: "done"
parallel-safe: false
depends-on: ["qa-vertical-slice-06d"]
base-commit: "afe0c20"
owner: "codex"
---

# Task Packet: Show current-answer sources by default

## Goal

Keep the source state synchronized to the latest completed assistant answer so desktop shows current-answer evidence immediately while tablet/mobile continue presenting that evidence through their existing drawer/sheet.

## Non-goals

- No source schema, citation ordering, copy behavior, query cache, drawer or layout change.
- No automatic opening of tablet/mobile overlays.

## Delivery context

The approved desktop contract exposes chat and current-answer sources simultaneously. Current runtime starts the source panel empty and only populates it after a citation-chip click. The message resource already contains immutable sources, so the page can derive current selection without another API or duplicated state model.

## Relevant files and current interfaces

- `web/src/pages/QaPage.tsx` — owns `sources` display state and receives durable message resources.
- `web/src/pages/QaPage.test.tsx` — mocks message list resources and page behavior.
- Existing changes to preserve: uncommitted Packet 07 E2E files, docs and baselines.

## Prerequisites

- `qa-vertical-slice-06d` is `done` at `afe0c20`.

## Explicit change boundary

### Allowed files

- Modify: `web/src/pages/QaPage.tsx`
- Test: `web/src/pages/QaPage.test.tsx`
- Modify: this packet and Packet 07 dependency metadata

### Allowed behavior changes

- Derive the selected source list from the newest completed assistant message whenever the current message resource changes.

### Forbidden changes

- No API/query/schema/CSS/overlay/baseline/dependency changes.

## Interface contract

### Consumes

- Ordered `QaMessage[]`, including assistant `status` and immutable `sources`.

### Produces

- Latest completed assistant sources in `SourcePanel`; an answer with no sources yields the existing explicit empty state.

### Invariants

- Clicking an older citation still selects that answer until the server message resource changes.
- Conversation switching clears stale sources before the new resource arrives.
- Tablet/mobile overlays remain closed until user activation.

## Required behavior

- A loaded/reloaded completed answer with sources fills desktop SourcePanel without clicking.
- New completed answers replace the current source selection; no-source answers clear it.
- User/conversation isolation remains inherited from the message query.

## Implementation guidance

Use one effect keyed to the current conversation/message resource. Find the last completed assistant message in server order and assign its sources; do not make another request or inspect DOM width.

## Acceptance criteria

- [x] Page test proves server-loaded latest sources appear without citation activation.
- [x] Existing QA page tests, typecheck and lint pass.
- [x] Packet 07 desktop default baseline contains the current evidence card.

## Test and verification commands

```powershell
Push-Location web
npm test -- --run src/pages/QaPage.test.tsx
npm run typecheck
npm run lint
Pop-Location
git diff --check
```

Expected: all commands pass.

## Stop conditions

Stop if implementation requires another API call, viewport branching, automatic overlay opening or source persistence outside existing messages.

## Implementation handoff

- Packet: `qa-vertical-slice-06e`
- Status: `done`
- Delivered:
  - The latest completed assistant answer now hydrates the current source panel from durable message data on load, reload and resource refresh.
- Files changed:
  - `web/src/pages/QaPage.tsx` — synchronizes current sources from the newest completed assistant message.
  - `web/src/pages/QaPage.test.tsx` — proves server-loaded evidence appears without a citation click.
  - `docs/agent-workflow/task-packets/2026-08-25-qa-vertical-slice/07-qa-integration-acceptance.md` — adds this correction as an acceptance prerequisite.
- Interfaces added or changed:
  - no API, schema or public component interface changes.
- Acceptance evidence:
  - [x] Focused page suite: 5 tests passed.
  - [x] Typecheck and lint passed.
  - [x] Real-server Playwright matrix: 12 tests passed across desktop, tablet and mobile.
  - [x] All 8 visual baselines were inspected; the desktop default and summary states contain the current evidence card.
- Verification:
  - `npm exec vitest run src/pages/QaPage.test.tsx` — PASS, 1 file / 5 tests.
  - `npm run typecheck` — PASS.
  - `npm run lint` — PASS.
  - `npm run build` — PASS.
  - `npx playwright test e2e/qa.spec.ts --update-snapshots` — PASS, 12 tests / 3 viewports / 0 skipped.
  - `git diff --check` — PASS (line-ending notices only).
- Scope confirmation:
  - changed only allowed files: yes
  - forbidden areas untouched: yes
- Deviations:
  - Packet 07's citation locator was narrowed to an exact accessible-name match because the completed copy control intentionally shares the phrase “引用 1”. This is test-only and remains in Packet 07.
- Residual risks/follow-ups:
  - none within this correction; Packet 07 owns full release gates and Docker evidence.
- Commit:
  - pending
