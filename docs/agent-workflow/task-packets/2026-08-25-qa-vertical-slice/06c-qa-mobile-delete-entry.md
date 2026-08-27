---
id: "qa-vertical-slice-06c"
title: "Expose safe conversation deletion on mobile"
status: "done"
parallel-safe: false
depends-on: ["qa-vertical-slice-06b"]
base-commit: "f97a584"
owner: "codex"
---

# Task Packet: Expose safe conversation deletion on mobile

## Goal

Make the existing durable conversation deletion flow reachable on mobile through the existing conversation sheet, without crowding the approved compact QA header.

## Non-goals

- No deletion API, worker, dialog, header, navigation or Penpot board redesign.
- No bulk deletion, undo, soft delete or additional mobile menu.

## Delivery context

The QA specification requires deletion confirmation/processing/failure among responsive states. Mobile intentionally hides the ghost delete button in the compact header, but Packet 07 found no alternate entry. The existing conversation bottom sheet is the smallest coherent place for a selected-conversation destructive action and can reuse the already verified confirmation and durable deletion flow.

## Relevant files and current interfaces

- `web/src/components/QaWorkspace/QaWorkspace.tsx` — `ConversationList` renders selection/new actions and accepts no optional selected-conversation action.
- `web/src/pages/QaPage.tsx` — mobile/tablet conversation drawer already owns the selected ID and delete-dialog state.
- `web/src/pages/QaPage.test.tsx` — verifies the shared durable delete flow but not drawer reachability.
- Existing changes to preserve: uncommitted Packet 07 E2E files and baselines.

## Prerequisites

- `qa-vertical-slice-06b` is `done` at `f97a584`.

## Explicit change boundary

### Allowed files

- Modify: `web/src/components/QaWorkspace/QaWorkspace.tsx`
- Modify: `web/src/pages/QaPage.tsx`
- Test: `web/src/pages/QaPage.test.tsx`
- Modify: this packet and Packet 07 dependency metadata

### Allowed behavior changes

- `ConversationList` may receive an optional selected-conversation deletion callback and render one danger action when supplied.
- The responsive conversation drawer may close itself and open the existing delete confirmation.

### Forbidden changes

- No API/query/state-machine/CSS/global navigation/baseline/dependency changes.
- Do not duplicate deletion mutation or confirmation logic.

## Interface contract

### Consumes

- Existing `selectedId`, `setConversationOpen(false)` and `setDeleteOpen(true)` state.

### Produces

- Optional `onDeleteSelected?: () => void` on `ConversationList`; absent in the desktop fixed list, supplied in the responsive drawer only when a conversation is selected.

### Invariants

- All deletion still requires the existing explicit confirmation and uses the same durable endpoint/polling.
- Closing drawer/dialog retains existing focus trapping and safe destructive copy.

## Required behavior

- Opening “对话” on a selected conversation exposes “删除当前对话”.
- Activating it closes the drawer and opens “永久删除对话”; confirming calls the existing delete mutation exactly once.
- Desktop header deletion remains unchanged.

## Implementation guidance

Keep the callback optional and render the existing `Button hierarchy="danger"` after the list. In `QaPage`, pass it only to the drawer and compose the two existing state transitions; do not introduce a second deletion component.

## Acceptance criteria

- [ ] Unit test proves drawer-to-confirmation reachability and the existing DELETE call.
- [ ] Existing QA frontend tests, typecheck and lint pass.
- [ ] Packet 07 can run the full lifecycle at the mobile viewport without a hidden-control workaround.

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

Stop if implementation requires a new API, duplicated dialog/mutation state, header crowding or global navigation changes.

## Implementation handoff

- Packet: `qa-vertical-slice-06c`
- Status: `done`
- Delivered:
  - Responsive conversation drawers expose the selected conversation's existing safe deletion flow through “删除当前对话”.
- Files changed:
  - `web/src/components/QaWorkspace/QaWorkspace.tsx` — optional drawer-only destructive action.
  - `web/src/pages/QaPage.tsx` — close the conversation drawer and open the shared confirmation.
  - `web/src/pages/QaPage.test.tsx` — drawer-to-delete regression.
  - `docs/agent-workflow/task-packets/2026-08-25-qa-vertical-slice/07-qa-integration-acceptance.md` — acceptance dependency metadata.
- Interfaces added or changed:
  - `ConversationList` accepts optional `onDeleteSelected?: () => void`.
- Acceptance evidence:
  - [x] Unit test opens the conversation drawer, enters the existing confirmation and confirms the same durable DELETE request.
  - [x] Desktop fixed conversation list receives no extra action; desktop header deletion is unchanged.
  - [x] No new API, query, mutation or deletion state was introduced.
- Verification:
  - `npm test -- --run src/pages/QaPage.test.tsx` — PASS, 14 files / 115 tests.
  - `npm run typecheck` — PASS.
  - `npm run lint` — PASS.
  - `git diff --check` — PASS (line-ending notices only).
- Scope confirmation:
  - changed only allowed files: yes
  - forbidden areas untouched: yes
- Deviations:
  - none
- Residual risks/follow-ups:
  - Packet 07 must exercise this entry through real browsers at tablet/mobile widths.
- Commit:
  - `pending`
