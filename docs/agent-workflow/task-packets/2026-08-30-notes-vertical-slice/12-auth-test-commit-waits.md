---
id: "notes-vertical-slice-12"
title: "稳定认证路由的 React 提交时序测试"
status: "done"
parallel-safe: false
depends-on: ["notes-vertical-slice-10"]
base-commit: "33b57fa"
owner: "Codex /root"
---

# Task Packet: 稳定认证路由的 React 提交时序测试

## Goal

Make the existing authentication route/focus assertions wait for their actual
React DOM/focus commits so the full 157-test frontend suite remains reliable
under parallel load without weakening any behavior assertion.

## Non-goals

- No production authentication, navigation, focus or timing change.
- No Vitest worker/timeout reduction, retry, skip or assertion removal.
- No Notes implementation change.

## Delivery context

After Packet 11 reached Notes E2E `12/12`, two fresh full frontend runs each
failed two authentication assertions while the same authentication modules
passed `28/28` in isolation. `ProtectedRoute.test.tsx` waits only for the
imperative router state and then synchronously queries the React DOM; under
full-suite load, router state can update before `RouterProvider` commits the
login element. `AuthProvider.test.tsx` similarly queries the dialog and then
synchronously assumes the focus effect has committed. Tests must await the
observable UI state they assert.

## Relevant files and current interfaces

- `web/src/auth/ProtectedRoute.test.tsx:106-111,150-156` — waits for router
  location, then uses synchronous `getByTestId("login-state")`.
- `web/src/auth/AuthProvider.test.tsx:199-201` — awaits dialog presence, then
  synchronously asserts focus moved to `重新登录`.
- `web/src/test/setup.ts` — already performs Testing Library cleanup globally.

## Prerequisites

- Base commit `33b57fa`; production authentication tests are otherwise green.
- The exact two auth modules pass `28/28` when run alone.

## Explicit change boundary

### Allowed files

- Modify: `web/src/auth/ProtectedRoute.test.tsx`
- Modify: `web/src/auth/AuthProvider.test.tsx`
- Modify: this Packet 12 handoff.
- Modify: `06-notes-release-acceptance.md` only to record dependency/resolution.

### Allowed behavior changes

- Replace synchronous post-navigation/post-dialog DOM or focus assertions with
  Testing Library async queries/waits for the same expected state.

### Forbidden changes

- No production code, test retries/skips, global timeout/worker configuration,
  weakened route state assertions, or unrelated test edits.

## Interface contract

### Consumes

- Testing Library `findBy*` and existing `waitFor` semantics.

### Produces

- No product interface; deterministic observation of the same user-visible
  route and focus outcomes.

### Invariants

- Route remains `REPLACE` with the exact remembered target/session-expired
  state; auth status remains `anonymous`; focus still lands on `重新登录`.

## Required behavior

- Both protected-route cases await the rendered `login-state` before asserting
  its text.
- Session-expiry focus assertion waits for the focus effect, retaining the
  exact target and Escape/focus-return checks.

## Implementation guidance

Use `await screen.findByTestId("login-state")` for post-navigation rendering
and `await waitFor(() => expect(button).toHaveFocus())` for the effect-driven
focus transfer. Do not add sleeps or increase timeouts.

## Acceptance criteria

- [x] Exact authentication modules pass `28/28`.
- [x] Full frontend suite passed twice at `18` files / `157` tests before the
  Packet 11 DOM-order test raised the current total to `158`.
- [x] No production file or test assertion meaning changes.

## Test and verification commands

```powershell
Set-Location web
npx vitest run src/auth/AuthProvider.test.tsx src/auth/ProtectedRoute.test.tsx
npm test -- --run
npm test -- --run
Set-Location ..
git diff --check
```

Expected: all three test commands pass; both full runs report 157/157.

## Stop conditions

Stop as `blocked` if failures persist after awaiting React commits, or if a
production change/global test policy change is required.

## Implementation handoff

- Status: done
- Delivered:
  - Authentication tests await the same committed DOM and focus outcomes they
    already asserted; no retry, sleep, timeout or production change was added.
- Files changed:
  - `web/src/auth/ProtectedRoute.test.tsx`
  - `web/src/auth/AuthProvider.test.tsx`
  - this packet and Packet 06/REVIEW records
- Acceptance evidence:
  - [x] Focused auth modules pass `28/28`.
  - [x] Consecutive full runs passed `157/157`; final combined frontend run
    after Packet 11's extra test passed `158/158`.
- Verification:
  - `npx vitest run src/auth/AuthProvider.test.tsx src/auth/ProtectedRoute.test.tsx` — PASS (`28` tests).
  - `npm test -- --run` twice — PASS (`157/157` each).
  - Final combined `npm test -- --run` — PASS (`158/158`).
- Scope confirmation:
  - changed only allowed files: yes
  - forbidden areas untouched: yes
- Deviations:
  - None.
- Residual risks/follow-ups:
  - None.
- Commit:
  - To be reported by the controller after commit creation.
