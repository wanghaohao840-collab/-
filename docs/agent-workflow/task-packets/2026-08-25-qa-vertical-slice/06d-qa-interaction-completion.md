---
id: "qa-vertical-slice-06d"
title: "Complete QA citation and composer interactions"
status: "done"
parallel-safe: false
depends-on: ["qa-vertical-slice-06c"]
base-commit: "b4fc76a"
owner: "codex"
---

# Task Packet: Complete QA citation and composer interactions

## Goal

Close the remaining approved interaction gaps: citations are copyable with non-blocking feedback, the composer preserves ordinary/newline and IME input while submitting on Ctrl/Cmd+Enter, and active summary progress includes the approved visual skeleton cue.

## Non-goals

- No clipboard history, rich-text editor, streaming, API, source schema, summary worker or design-token change.

## Delivery context

The approved QA specification explicitly requires copyable citations and Ctrl/Cmd+Enter without breaking IME. Current `SourcePanel` only renders a reference string, while `QaComposer` submits every Enter keydown. The Penpot summary-running state also includes a linked Skeleton cue; the runtime currently shows only text/progress. These are presentational interactions within Packet 06's existing component boundary.

## Relevant files and current interfaces

- `web/src/components/QaWorkspace/QaWorkspace.tsx` — owns `QaComposer`, `SourcePanel` and `SummaryStatus`.
- `web/src/components/QaWorkspace/QaWorkspace.test.tsx` — existing failure/source, keyboard and overlay tests.
- `web/src/styles/qa.css` — QA-only component styles and reduced-motion handling.
- Existing changes to preserve: uncommitted Packet 07 E2E files and baselines.

## Prerequisites

- `qa-vertical-slice-06c` is `done` at `b4fc76a`.

## Explicit change boundary

### Allowed files

- Modify: `web/src/components/QaWorkspace/QaWorkspace.tsx`
- Test: `web/src/components/QaWorkspace/QaWorkspace.test.tsx`
- Modify: `web/src/styles/qa.css`
- Modify: this packet and Packet 07 dependency metadata

### Allowed behavior changes

- Write only `QaSource.reference` to the browser clipboard on explicit activation and announce success/failure in a polite live region.
- Submit keyboard input only for Ctrl/Cmd+Enter when not composing; ordinary Enter remains available for multiline input.
- Render an aria-hidden, reduced-motion-safe skeleton cue while a summary is queued/running.

### Forbidden changes

- No automatic clipboard access, prompt/excerpt copying, persistent feedback, global shortcuts, API/state changes or global CSS.

## Interface contract

### Consumes

- Existing `QaSource.reference`, `onSubmit(question, mode)` and `QaJob` active statuses.

### Produces

- Accessible “复制引用 N” buttons and `aria-live="polite"` result text.
- Multiline-safe composer keyboard semantics.
- Decorative `.qa-summary-status__skeleton` only for active jobs.

### Invariants

- Mouse/touch Send behavior is unchanged.
- Clipboard content contains only the already user-visible immutable reference string.
- Failure feedback does not expose browser exceptions.

## Required behavior

- Successful copy writes exactly the selected source reference and announces “引用 N 已复制”.
- Clipboard failure announces a safe actionable message without throwing.
- Plain Enter and composing Enter do not submit; Ctrl/Cmd+Enter submits once.
- Active summary shows a decorative skeleton; terminal states do not.

## Implementation guidance

Keep feedback local to `SourcePanel`. Guard absent Clipboard API and catch failures without serializing exceptions. Check both the React and native composing flags before handling the shortcut. Reuse QA semantic tokens for a compact static skeleton and let the existing reduced-motion rule suppress animation.

## Acceptance criteria

- [ ] Component tests prove exact clipboard payload/feedback and safe failure behavior.
- [ ] Component tests prove multiline/IME-safe shortcut semantics.
- [ ] Summary active/terminal rendering is distinguishable without relying on color.
- [ ] Frontend tests, typecheck, lint and build pass.

## Test and verification commands

```powershell
Push-Location web
npm test -- --run src/components/QaWorkspace/QaWorkspace.test.tsx
npm run typecheck
npm run lint
npm run build
Pop-Location
git diff --check
```

Expected: all commands pass.

## Stop conditions

Stop if implementation requires clipboard permissions outside explicit user activation, an editor dependency, API/state changes or global shortcut/CSS changes.

## Implementation handoff

- Packet: `qa-vertical-slice-06d`
- Status: `done`
- Delivered:
  - Exact citation copying with safe polite feedback, IME-safe Ctrl/Cmd+Enter composer semantics, and an active-summary skeleton cue.
- Files changed:
  - `web/src/components/QaWorkspace/QaWorkspace.tsx` — clipboard interaction, keyboard guard and active summary cue.
  - `web/src/components/QaWorkspace/QaWorkspace.test.tsx` — success/failure, IME/shortcut and active/terminal state regressions.
  - `web/src/styles/qa.css` — QA-scoped copy target, feedback and skeleton styling.
  - `docs/agent-workflow/task-packets/2026-08-25-qa-vertical-slice/07-qa-integration-acceptance.md` — acceptance dependency metadata.
- Interfaces added or changed:
  - no public component prop or API changes.
- Acceptance evidence:
  - [x] Copy writes exactly `QaSource.reference`; success and sanitized failure feedback are asserted.
  - [x] Plain/composing Enter does not submit; Ctrl+Enter submits once.
  - [x] Active summary alone renders the decorative cue; status text remains authoritative.
  - [x] Copy control has a 44 px minimum target and uses existing semantic tokens.
- Verification:
  - `npm test -- --run src/components/QaWorkspace/QaWorkspace.test.tsx` — PASS, 14 files / 116 tests.
  - `npm run typecheck` — PASS.
  - `npm run lint` — PASS.
  - `npm run build` — PASS.
  - `git diff --check` — PASS (line-ending notices only).
- Scope confirmation:
  - changed only allowed files: yes
  - forbidden areas untouched: yes
- Deviations:
  - none
- Residual risks/follow-ups:
  - Packet 07 must exercise real-browser clipboard feedback and refresh summary visual baselines.
- Commit:
  - `pending`
