---
id: "qa-vertical-slice-06b"
title: "Keep the complete QA workspace visible on tablet"
status: "done"
parallel-safe: false
depends-on: ["qa-vertical-slice-06a"]
base-commit: "62d62f4"
owner: "codex"
---

# Task Packet: Keep the complete QA workspace visible on tablet

## Goal

At the approved 1024 × 768 tablet viewport, keep the QA page heading and the complete composer visible together at the top scroll position so focus does not force the visual state away from its controls.

## Non-goals

- No component, route, API, desktop/mobile geometry or global AppShell redesign.
- No E2E baseline ownership; Packet 07 will regenerate and approve the eight screenshots.

## Delivery context

Packet 07 visual inspection proved that the shared 680 px workspace minimum, plus the tablet top bar, page padding and heading, exceeds the 768 px viewport. Focusing the composer scrolls the heading out of view. Penpot's tablet QA board keeps both regions visible, so the tablet workspace needs a viewport-aware minimum while preserving a usable lower bound.

## Relevant files and current interfaces

- `web/src/styles/qa.css:7` — `.qa-workspace` uses a desktop-oriented 680 px minimum at all non-mobile widths.
- `web/src/styles/app-shell.css:574` — tablet uses a 64 px top bar and 32 px main padding.
- `web/e2e/qa.spec.ts` — Packet 07 now proves full composer visibility at scroll position zero.
- Existing changes to preserve: uncommitted Packet 07 E2E files and baselines.

## Prerequisites

- `qa-vertical-slice-06a` is `done` at `62d62f4`.
- Chromium and the repository Node dependencies are installed.

## Explicit change boundary

### Allowed files

- Modify: `web/src/styles/qa.css`
- Modify: this packet and Packet 07 dependency metadata

### Allowed behavior changes

- Override tablet QA workspace minimum height with a viewport-aware calculation and a 520 px usability floor.

### Forbidden changes

- No JavaScript scroll forcing, global shell CSS, desktop/mobile layout, tests, baselines, dependencies or product behavior changes.

## Interface contract

### Consumes

- Existing `--topbar-height` and `--space-8` tokens and the 768–1199 px tablet breakpoint.

### Produces

- Tablet `.qa-workspace` minimum height of `max(520px, viewport minus top bar, vertical page padding and heading allowance)`.

### Invariants

- Desktop remains 680 px minimum; mobile retains its dedicated dynamic minimum and bottom-navigation allowance.
- No horizontal overflow or target-size changes.

## Required behavior

- At 1024 × 768 and scroll position zero, the page heading and entire question textarea are in the viewport.
- Existing tablet source drawer and mobile bottom sheet geometry remain unchanged.

## Implementation guidance

Add one tablet-only rule inside the existing `max-width: 1199px` block. Express the available height with existing tokens and a small explicit heading allowance; use `max(...)` so shorter landscape tablets retain a usable workspace rather than collapsing.

## Acceptance criteria

- [ ] 1024 × 768 visual acceptance sees the heading and full composer at scroll zero.
- [ ] Desktop and mobile computed rules remain unchanged.
- [ ] Typecheck, lint and Packet 07 tablet visual scenario pass.

## Test and verification commands

```powershell
Push-Location web
npm run typecheck
npm run lint
npm run build
npx playwright test e2e/qa.spec.ts --project=tablet --grep "approved visual states" --update-snapshots
Pop-Location
git diff --check
```

Expected: all commands pass; the tablet test proves a viewport ratio of 1 for the composer.

## Stop conditions

Stop if the fix requires JavaScript scroll mutation, global shell changes or a desktop/mobile layout change.

## Implementation handoff

- Packet: `qa-vertical-slice-06b`
- Status: `done`
- Delivered:
  - Tablet QA workspace height now tracks available viewport height with a 520 px usability floor, keeping the heading and full composer visible at 1024 × 768.
- Files changed:
  - `web/src/styles/qa.css` — tablet-only viewport-aware minimum height.
  - `docs/agent-workflow/task-packets/2026-08-25-qa-vertical-slice/07-qa-integration-acceptance.md` — acceptance dependency metadata.
- Interfaces added or changed:
  - none
- Acceptance evidence:
  - [x] Packet 07 assertion reports composer viewport ratio 1 at scroll zero.
  - [x] Updated tablet default and source-drawer screenshots show the heading, actions and complete composer without clipping.
  - [x] Rule is confined to `max-width: 1199px`; later mobile rule and desktop base remain authoritative at their viewports.
- Verification:
  - `npm run typecheck` — PASS.
  - `npm run lint` — PASS.
  - `npm run build` — PASS.
  - `npx playwright test e2e/qa.spec.ts --project=tablet --grep "approved visual states" --update-snapshots` — PASS, 1 passed.
  - visual inspection of `qa-default-tablet.png` and `qa-sources-tablet.png` — PASS.
- Scope confirmation:
  - changed only allowed files: yes
  - forbidden areas untouched: yes
- Deviations:
  - none
- Residual risks/follow-ups:
  - Packet 07 owns the final three-viewport run and baselines.
- Commit:
  - `pending`
