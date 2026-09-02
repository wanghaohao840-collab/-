---
id: "notes-vertical-slice-10"
title: "对齐 Notes 移动编辑器与验收语义"
status: "done"
parallel-safe: false
depends-on: ["notes-vertical-slice-01", "notes-vertical-slice-04", "notes-vertical-slice-07", "notes-vertical-slice-09"]
base-commit: "4ee742c"
owner: "Codex /root/notes_packet_10_retry"
---

# Task Packet: 对齐 Notes 移动编辑器与验收语义

## Goal

Align the authenticated `390×844` Notes editor with the approved Penpot mobile
contract: one existing Save action is visibly positioned in the top editor
header, while source and delete actions remain visible above bottom navigation.
Also state and test the accepted bulk-clear scope accurately: desktop/tablet
provide the clear-confirm UI; mobile retains per-note delete and verifies the
same clear API/tombstone invariant without claiming a mobile bulk-clear UI.

## Non-goals

- Do not invent a mobile overflow/menu absent from the approved Penpot source.
- Do not change Notes API, persistence, mutation semantics or desktop/tablet
  layouts.
- Do not duplicate Save controls or add browser/viewport branching in React.

## Delivery context

Packet 06's independent review compared the tracked mobile editor snapshot to
`docs/product-ui/reference/penpot/mobile-notes-editor.png`. Runtime currently
places Save/source/delete after a tall form, below the first viewport. Penpot
requires top back/title/save and bottom source/delete controls. The review also
found Packet 06 overclaimed mobile clear UI coverage: Penpot has no mobile
bulk-clear entry and CSS intentionally hides it, while E2E uses the real
authenticated API to prove tombstones. The accepted design must be implemented
and documented precisely rather than silently extending it.

## Relevant files and current interfaces

- `web/src/components/NotesWorkspace/NoteEditor.tsx:10-22` — one Save button
  currently lives in the footer with source/delete; `Button` accepts className.
- `web/src/components/NotesWorkspace/notes-workspace.css:33-49,78` — mobile
  editor has no viewport bound; footer falls below the first viewport.
- `web/src/components/NotesWorkspace/NotesWorkspace.test.tsx` — editor save,
  source overlay, delete and clear surfaces have unit seams.
- `web/e2e/notes.spec.ts:213-239` — mobile clear calls authenticated API; editor
  snapshot is captured later in the same real-server suite.
- `web/e2e/notes.spec.ts-snapshots/notes-editor-mobile.png` — current runtime
  baseline requiring replacement only after original-size inspection.
- `docs/product-ui/penpot-handoff.md:259-273` — responsive/clear contract and
  Notes MCP provenance; version 2.15.4 is stale versus the actual successful
  2.17.0 bridge used against Penpot 2.17.2.
- `docs/product-ui/reference/penpot/mobile-notes-editor.png` — approved visual
  authority; must remain unchanged.
- Existing changes to preserve: controller `progress.md` and untracked
  `REVIEW.md`; Packet 06 is committed at `4ee742c`.

## Prerequisites

- Packets 01, 04, 07 and 09 are `done`; current HEAD is `4ee742c`.
- Node dependencies already installed under `web/` and project venv available.
- Real-server Playwright runtime may be used; Penpot mutation is not required.

## Explicit change boundary

### Allowed files

- Modify: `web/src/components/NotesWorkspace/NoteEditor.tsx`
- Modify: `web/src/components/NotesWorkspace/notes-workspace.css`
- Modify: `web/src/components/NotesWorkspace/NotesWorkspace.test.tsx`
- Modify: `web/e2e/notes.spec.ts`
- Modify: `web/e2e/notes.spec.ts-snapshots/notes-editor-mobile.png`
- Modify: `docs/product-ui/penpot-handoff.md`
- Modify: `docs/agent-workflow/task-packets/2026-08-30-notes-vertical-slice/06-notes-release-acceptance.md`
- Modify: this Packet 10 handoff.

### Allowed behavior changes

- Add a class to the existing Save button and reposition that same element in
  mobile CSS; bound/scroll the form region so footer actions remain visible.
- Add responsive unit/E2E assertions and replace only the mobile editor runtime
  snapshot after explicit original-size review.
- Narrow release/handoff wording for mobile bulk clear and correct MCP version
  provenance from 2.15.4 to the actually used 2.17.0.

### Forbidden changes

- No second Save button, sticky overlay that obscures content, reduced 44px
  targets, hidden source/delete, production API/backend edits, Penpot source
  mutation, broad snapshot update or desktop/tablet baseline change.
- Do not commit progress/REVIEW, runtime databases, test-results, secrets,
  uploads, reports or traces.

## Interface contract

### Consumes

- Existing `Button` className and save/delete/source callbacks.
- Existing mobile AppShell topbar/bottom-nav CSS variables.
- Existing authenticated Notes E2E server and CSRF clear request.

### Produces

- No TypeScript/API signature changes.
- Mobile editor geometry: visible top Save; visible footer source/delete above
  the 64px bottom navigation; scroll stays inside content/form when necessary.
- Handoff truth: mobile bulk clear is not a UI capability in this design;
  mobile E2E proves API/tombstone invariants and per-note deletion remains UI.

### Invariants

- One accessible `保存笔记` button only; dirty/saving/disabled behavior stays
  identical at all viewports.
- Keyboard order remains logical; focus ring and all named mobile targets are
  at least 44×44.
- Desktop/tablet clear-confirm UI remains tested and functional.

## Required behavior

- At `390×844`, the Save button bounding box is visible near the top editor
  heading; source/delete bounding boxes are visible and end above bottom nav.
- The form may scroll internally but header/footer actions must not cover its
  editable controls.
- Mobile lifecycle explicitly asserts bulk-clear action is hidden before the
  authenticated clear API/tombstone check; release docs must not call that a
  mobile clear UI flow.
- Update Notes fresh-read provenance to `@penpot/mcp` 2.17.0 against Penpot
  2.17.2 with its already-recorded patch warning; do not alter revision/IDs.

## Implementation guidance

Use one DOM Save button. A class plus mobile absolute/grid positioning is
preferred over rendering duplicates, because jsdom/accessibility queries and
keyboard order must stay unambiguous. Constrain the editor to the available
mobile viewport, make the central form/preview the scrollable flex child, and
keep footer actions in normal layout above bottom navigation. Add a focused
computed-geometry Playwright assertion before accepting the new snapshot.

First generate a temporary actual screenshot and inspect it at original size
against the Penpot export. Only then replace the single editor baseline; never
bulk-update snapshots or change the Penpot reference.

## Acceptance criteria

- [ ] One Save button preserves behavior and appears in the mobile header.
- [ ] Source/delete are visible above bottom nav at 390×844 with no overflow or
  obscured inputs; desktop/tablet regressions stay green.
- [ ] Unit tests, full frontend, typecheck, lint and build pass.
- [ ] Mobile Notes E2E and geometry assertions pass; only editor snapshot is
  deliberately replaced after original-size comparison.
- [ ] Handoff accurately scopes mobile clear and records MCP 2.17.0 provenance.

## Test and verification commands

```powershell
Set-Location web
npx vitest run src/components/NotesWorkspace/NotesWorkspace.test.tsx src/pages/NotesPage.test.tsx
npm test -- --run
npm run typecheck
npm run lint
npm run build
npx playwright test e2e/notes.spec.ts --project=mobile --workers=1
Set-Location ..
node --test tests/design/test_penpot_handoff.mjs tests/design/test_penpot_component_map.mjs
git diff --check
```

Expected: all commands pass; full frontend remains at least 157 tests, mobile
Notes E2E passes, design contract remains 10/10, and only the named mobile
editor snapshot changes.

Manual geometry evidence at `390×844`:

- exactly one visible/accessibility-tree `保存笔记`;
- Save top < first tab top and all bounds inside viewport;
- source/delete bottom ≤ bottom-nav top;
- no horizontal page overflow;
- inspected snapshot shows no clipped heading, form labels, controls or footer.

## Stop conditions

Stop as `blocked` if the approved layout requires a second DOM Save control,
source/delete cannot remain visible without obscuring editable fields, Penpot
authority differs, or any backend/extra snapshot change is required.

## Implementation handoff

- Status: done
- Files changed:
  - `web/src/components/NotesWorkspace/NoteEditor.tsx`
  - `web/src/components/NotesWorkspace/notes-workspace.css`
  - `web/src/components/NotesWorkspace/NotesWorkspace.test.tsx`
  - `web/e2e/notes.spec.ts`
  - `web/e2e/notes.spec.ts-snapshots/notes-editor-mobile.png`
  - `docs/product-ui/penpot-handoff.md`
  - `docs/agent-workflow/task-packets/2026-08-30-notes-vertical-slice/06-notes-release-acceptance.md`
  - `docs/agent-workflow/task-packets/2026-08-30-notes-vertical-slice/10-notes-mobile-editor-acceptance.md`
- Acceptance criteria:
  - [x] One `保存笔记` button remains the only accessible Save action and is rendered in the editor header; its mobile geometry is above the tablist at `x=238, y=104, w=112, h=44`.
  - [x] Source and delete remain visible above bottom navigation at `390×844`: source `x=40, y=728, w=151, h=44`, delete `x=199, y=728, w=151, h=44`, bottom nav `y=780, h=64`.
  - [x] Desktop/tablet/unit/frontend/design regressions stayed green; no mobile clear menu was introduced.
  - [x] Mobile lifecycle now explicitly asserts the hidden clear action before the authenticated clear API/tombstone invariant.
  - [x] Handoff wording narrows mobile clear scope and records `@penpot/mcp` `2.17.0` against Penpot `2.17.2`.
- Verification:
  - `Set-Location web; npx vitest run src/components/NotesWorkspace/NotesWorkspace.test.tsx src/pages/NotesPage.test.tsx` — PASS (`2` files, `18` tests).
  - `Set-Location web; npm test -- --run` — PASS (`18` files, `157` tests).
  - `Set-Location web; npm run typecheck` — PASS.
  - `Set-Location web; npm run lint` — PASS.
  - `Set-Location web; npm run build` — PASS (Vite bundle-size advisory only; no build failure).
  - `Set-Location web; npx playwright test e2e/notes.spec.ts --project=mobile --workers=1` — PASS (`4` tests).
  - `node --test tests/design/test_penpot_handoff.mjs tests/design/test_penpot_component_map.mjs` — PASS (`10` tests).
  - `git diff --check` — PASS (Git emitted only existing CRLF normalization warnings).
- Original-size comparison:
  - On 2026-09-02, the temporary runtime screenshot `test-results/.../notes-editor-actual.png` was inspected at original `390×844` size beside `docs/product-ui/reference/penpot/mobile-notes-editor.png` before replacing the checked-in baseline.
  - Runtime content intentionally differs from Penpot’s illustrative sample copy, but the approved mobile contract now matches: top back/title/save, form content within the editor viewport, and source/delete above the fixed bottom navigation.
- Snapshot hash:
  - `web/e2e/notes.spec.ts-snapshots/notes-editor-mobile.png` changed from Git blob `e3aaf48b8bbc61fb3276914f0853de9e01b80a53` to `e063a6e63106cf8ada82a35bfba4f3b3db20b44d`.
- Deviations:
  - Save moved into the shared editor header DOM instead of remaining footer-positioned on desktop/tablet; no API/state semantics changed, and the packet’s required regressions stayed green.
  - The mobile form can remain fully visible for the current acceptance fixture, so the Playwright gate verifies final geometry instead of requiring overflow to occur on that specific dataset.
- Residual risks:
  - Notes mobile still uses runtime-authenticated data rather than Penpot’s sample strings, so future visual reviews should keep comparing geometry/state meaning rather than literal copy.
- Commit:
  - not committed
