---
id: "notes-vertical-slice-11"
title: "修复 Notes 单一保存按钮的跨断点位置"
status: "done"
parallel-safe: false
depends-on: ["notes-vertical-slice-07", "notes-vertical-slice-10"]
base-commit: "33b57fa"
owner: "Codex /root"
---

# Task Packet: 修复 Notes 单一保存按钮的跨断点位置

## Goal

Keep exactly one Save button while restoring the approved desktop and tablet
footer placement and retaining Packet 10's visible mobile header placement at
`390×844`. All three Playwright visual projects must pass without changing any
desktop/tablet snapshot or accepting a new visual baseline.

## Non-goals

- Do not change Notes save semantics, API calls, persistence or data shapes.
- Do not render two responsive Save elements at the same time.
- Do not update desktop/tablet/mobile snapshots.

## Delivery context

Packet 10 moved the single Save DOM node from the footer into the heading. Its
mobile project passes and matches the approved Penpot structure, but the first
fresh all-project release run on commit `33b57fa` failed the desktop visual by
13,312 pixels (1%) and tablet by 15,459 pixels (2%). The other ten Notes E2E
tests passed. This is a cross-breakpoint placement regression, not a reason to
replace the accepted desktop/tablet baselines.

## Relevant files and current interfaces

- `web/src/components/NotesWorkspace/NoteEditor.tsx` — one `.notes-editor__save`
  button currently sits in the heading; callbacks and disabled/loading behavior
  must remain unchanged.
- `web/src/components/NotesWorkspace/notes-workspace.css` — mobile bounds the
  editor and keeps footer source/delete above the 64 px navigation.
- `web/src/components/NotesWorkspace/NotesWorkspace.test.tsx` — asserts a
  single accessible Save action and explicit save behavior.
- `web/e2e/notes.spec.ts` — asserts mobile Save/footer geometry and all-project
  visual baselines.
- Existing changes to preserve: controller-owned `.superpowers/sdd/progress.md`
  and untracked `REVIEW.md`; no test-results artifact may be committed.

## Prerequisites

### Packet dependencies

- Packets 07 and 10 are `done` at commit `33b57fa`.

### Repository/base state

- Base commit: `33b57fa`.
- The tracked mobile editor snapshot already reflects Packet 10 and must remain
  byte-identical.

### External prerequisites

- Installed `web/node_modules`; the real single-worker Notes Playwright runtime.

## Explicit change boundary

### Allowed files

- Modify: `web/src/components/NotesWorkspace/NoteEditor.tsx`
- Modify: `web/src/components/NotesWorkspace/notes-workspace.css`
- Modify: `web/src/components/NotesWorkspace/NotesWorkspace.test.tsx`
- Modify: `web/e2e/notes.spec.ts` only if a focused placement assertion is
  required; no snapshot changes.
- Modify: this Packet 11 handoff.
- Modify: `06-notes-release-acceptance.md` only to record dependency/resolution.

### Allowed behavior changes

- Reposition the existing Save element responsively with CSS/markup structure,
  preserving one mounted DOM node and the existing callbacks/state. A
  `matchMedia` subscription may choose its semantic header/footer parent because
  CSS-only visual relocation leaves keyboard order incorrect in Firefox/Safari.

### Forbidden changes

- No API/backend/Penpot changes, snapshot updates, simultaneously duplicated
  controls, unrelated refactor, or generated/runtime artifacts.

## Interface contract

### Consumes

- Existing `Button` className, `onSave`, `dirty`, `saving` and `disabled` props.
- Existing desktop/tablet accepted screenshots and Packet 10 mobile geometry.

### Produces

- No public TypeScript signature change.
- One Save control visually in the desktop/tablet footer and mobile header.

### Invariants

- Exactly one accessible `保存笔记` button at every viewport.
- Disabled/loading/save behavior, source/delete behavior and 44 px targets stay
  unchanged.
- Mobile source/delete remain entirely above bottom navigation.

## Required behavior

- Desktop `1440×1024` and tablet `1024×768` match existing baselines exactly
  within the configured visual threshold, without snapshot updates.
- Mobile `390×844` retains Save above the tablist and source/delete above nav.
- Focusable controls remain reachable in stable DOM order; no content is
  covered by the responsively positioned Save element.

## Implementation guidance

Use an SSR-safe `matchMedia('(max-width: 767px)')` subscription to mount the
same Save element in the mobile header or desktop/tablet footer. This is an
approved reality-conflict revision: CSS `reading-flow` is not supported by
Firefox/Safari, while pure absolute positioning creates a visual/keyboard order
mismatch. Preserve the mobile title width so it cannot collide with Save.

## Acceptance criteria

- [x] Exactly one mounted Save DOM node preserves save/disabled/loading behavior.
- [x] Desktop and tablet existing visual baselines pass without modification.
- [x] Mobile editor baseline and geometric assertions pass unchanged.
- [x] Focused/full frontend, typecheck, lint and build pass.
- [x] Full Notes E2E passes `12/12`; no snapshot was modified.

## Test and verification commands

```powershell
Set-Location web
npx vitest run src/components/NotesWorkspace/NotesWorkspace.test.tsx src/pages/NotesPage.test.tsx
npm test -- --run
npm run typecheck
npm run lint
npm run build
npx playwright test e2e/notes.spec.ts --workers=1
Set-Location ..
git diff --check
```

Expected: focused and full frontend gates pass; full Notes E2E is `12/12` with
all four existing snapshots unchanged.

## Stop conditions

Stop as `blocked` if exactly one mounted Save action cannot satisfy all three
approved layouts without changing snapshots, introducing simultaneous
responsive duplicates, creating unsafe hydration behavior, obscuring content
or changing a file outside the allowed boundary.

## Implementation handoff

- Status: done
- Delivered:
  - One Save action is mounted in the semantic mobile header or desktop/tablet
    footer according to an SSR-safe media-query subscription; it is never
    duplicated in the accessibility tree.
  - Mobile keyboard order is now back, Save, edit tab; desktop/tablet retain
    the accepted footer action order and visual baselines.
- Files changed:
  - `web/src/components/NotesWorkspace/NoteEditor.tsx`
  - `web/src/components/NotesWorkspace/notes-workspace.css`
  - `web/src/components/NotesWorkspace/NotesWorkspace.test.tsx`
  - `web/e2e/notes.spec.ts`
  - this packet and Packet 06/REVIEW records
- Acceptance evidence:
  - [x] Unit DOM-order seam and explicit-save behavior pass.
  - [x] Full Notes E2E passes `12/12`, including mobile real Tab navigation.
  - [x] All four tracked snapshots remain byte-unchanged.
- Verification:
  - `npx vitest run src/components/NotesWorkspace/NotesWorkspace.test.tsx src/pages/NotesPage.test.tsx` — PASS (`19` tests).
  - `npm test -- --run` — PASS (`18` files, `158` tests).
  - `npm run typecheck; npm run lint; npm run build` — PASS.
  - `npx playwright test e2e/notes.spec.ts --workers=1` — PASS (`12/12`).
  - `git diff --check` — PASS.
- Scope confirmation:
  - changed only allowed files: yes
  - forbidden areas untouched: yes
- Deviations:
  - The packet was explicitly revised after independent review: CSS-only
    absolute relocation was replaced by `matchMedia` conditional mounting
    because it produced illogical focus order and CSS `reading-flow` lacks
    Firefox/Safari support.
- Residual risks/follow-ups:
  - None for the supported responsive contract.
- Commit:
  - To be reported by the controller after commit creation.
