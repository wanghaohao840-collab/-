---
id: "qa-vertical-slice-01"
title: "Verify Penpot QA source"
status: "done"
parallel-safe: false
depends-on: []
base-commit: "6b1548972cc3819d45c89edf0939931d80c4d362"
owner: "unassigned"
---

# Task Packet: Verify Penpot QA source

## Goal

Create and fresh-read eight QA boards in the approved Penpot file, export exact desktop/tablet/mobile reference PNGs, and record identifiers and responsive/state semantics in the tracked handoff so code has one verifiable visual authority.

## Non-goals

- Do not implement React or backend behavior.
- Do not rename/rebuild existing foundations, AppShell or shared components.
- Do not treat a screenshot or mockup as proof of a linked Penpot component.

## Delivery context

The approved layout uses three responsive modes: desktop has conversations/chat/sources columns; tablet keeps chat primary with side drawers; mobile uses one-column chat and bottom sheets. Boards cover default, summary-running, delete-confirm, sources and retry failure states. Content is labeled sample data and uses the existing Zhiyan semantic tokens.

## Relevant files and current interfaces

- `docs/product-ui/penpot-handoff.md` — existing file/page/component IDs and deliberate-difference record; append QA evidence.
- `docs/product-ui/penpot-component-map.json` — existing verified code/design identity map; packet 06 updates QA entries after implementation, so do not edit it here.
- `tests/design/test_penpot_handoff.mjs` — existing handoff format constraints.
- `tests/design/test_penpot_component_map.mjs:137` — AppShell mapping proves shared design/code identity is tracked.
- Existing changes to preserve: none.

## Prerequisites

### Packet dependencies

- none.

### Repository/base state

- Base commit: `6b1548972cc3819d45c89edf0939931d80c4d362`.
- Approved spec: `docs/superpowers/specs/2026-08-25-qa-vertical-slice-design.md`.

### External prerequisites

- Signed-in Penpot file ID `3be9e5e1-190f-8090-8008-6ff3f3dcd54c` with the MCP plugin connected and kept open.

## Explicit change boundary

### Allowed files

- Modify: `docs/product-ui/penpot-handoff.md`
- Create: `docs/product-ui/reference/penpot/desktop-qa.png`
- Create: `docs/product-ui/reference/penpot/desktop-qa-summary.png`
- Create: `docs/product-ui/reference/penpot/desktop-qa-delete.png`
- Create: `docs/product-ui/reference/penpot/tablet-qa.png`
- Create: `docs/product-ui/reference/penpot/tablet-qa-sources.png`
- Create: `docs/product-ui/reference/penpot/mobile-qa.png`
- Create: `docs/product-ui/reference/penpot/mobile-qa-sources.png`
- Create: `docs/product-ui/reference/penpot/mobile-qa-failure.png`
- Create/Test: `tests/deploy/test_qa_product_contract.py`
- External: only the eight named boards in the approved Penpot file.

### Allowed behavior changes

- Add the QA design source and tracked export contract only.

### Forbidden changes

- No application code, dependency, shared token/component or unrelated Penpot edits.
- No Figma URLs, local absolute paths or unverified component IDs in tracked handoff.
- If Penpot fresh-read/write/export is unavailable, stop; do not manufacture exports.

## Interface contract

### Consumes

- Existing Penpot Desktop/Tablet/Mobile pages, semantic tokens, AppShell, Button, Dialog, Drawer, TextField, Badge and Skeleton components.

### Produces

- Boards named exactly: `Desktop / QA / Default`, `Desktop / QA / Summary running`, `Desktop / QA / Delete confirm`, `Tablet / QA / Default`, `Tablet / QA / Sources drawer`, `Mobile / QA / Default`, `Mobile / QA / Sources sheet`, `Mobile / QA / Failure retry`.
- PNG dimensions: desktop `1440x1024`, tablet `1024x768`, mobile `390x844`.
- Handoff with file revision, page/board/component IDs, states, breakpoints, token bindings and deliberate differences.

### Invariants

- Broken linked components, text overflow and actual bounds overflow are zero.
- Every mobile interactive target is at least `44x44`.
- Existing boards/components remain unchanged.

## Required behavior

- Desktop exposes fixed-scope context and citations without hiding the chat composer.
- Tablet uses drawers; mobile uses bottom sheets above the existing 64px navigation.
- Running, destructive confirmation and retryable failure are visibly distinct and accessible without color-only meaning.

## Implementation guidance

Fresh-read the active file/pages first. Reuse actual linked components and semantic tokens, create only the named boards, inspect internal structure and bounds after writes, export directly, then visually inspect every PNG. Record IDs only from the final fresh read.

## Acceptance criteria

- [ ] All eight final boards exist with exact names, sizes and state semantics.
- [ ] Fresh-read geometry/linkage checks report zero broken links/overflow and compliant mobile targets.
- [ ] Eight non-empty tracked PNGs have exact expected dimensions.
- [ ] Handoff records verifiable IDs and responsive behavior; the product contract passes.

## Test and verification commands

Run from repository root:

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/deploy/test_qa_product_contract.py --basetemp=.runtime/pytest-qa-penpot
node --test tests/design/test_penpot_handoff.mjs
git diff --check
```

Expected: all tests and diff check PASS; manual fresh-read evidence satisfies geometry/linkage criteria.

## Stop conditions

Stop and append the repository reality-conflict report if a prerequisite/interface differs, the plugin cannot fresh-read/write/export, a change outside allowed files is required, or existing Penpot content would be overwritten.

## Implementation handoff

- Packet: `qa-vertical-slice-01`
- Status: `done`
- Delivered:
  - Restored the explicit local Penpot MCP bridge, fresh-read the approved file, and created only the eight exact QA boards on the existing Desktop, Tablet and Mobile pages.
  - Reused linked AppShell, Button, IconButton, Badge, Dialog, Drawer and Skeleton sources plus the existing Zhiyan semantic tokens.
  - Direct-exported and visually inspected all eight original-size PNG references after final overlay, close-control and destructive-dialog cleanup.
  - Added the focused repository contract for exact board/export names and decoded dimensions, and recorded the full responsive/state/accessibility handoff at final Penpot revision `136`.
- Files changed:
  - `docs/product-ui/penpot-handoff.md`
  - `docs/product-ui/reference/penpot/desktop-qa.png`
  - `docs/product-ui/reference/penpot/desktop-qa-summary.png`
  - `docs/product-ui/reference/penpot/desktop-qa-delete.png`
  - `docs/product-ui/reference/penpot/tablet-qa.png`
  - `docs/product-ui/reference/penpot/tablet-qa-sources.png`
  - `docs/product-ui/reference/penpot/mobile-qa.png`
  - `docs/product-ui/reference/penpot/mobile-qa-sources.png`
  - `docs/product-ui/reference/penpot/mobile-qa-failure.png`
  - `tests/deploy/test_qa_product_contract.py`
  - `docs/agent-workflow/task-packets/2026-08-25-qa-vertical-slice/01-penpot-qa-source.md`
- Interfaces added or changed:
  - Penpot boards and IDs: all eight exact names and final IDs are recorded in `docs/product-ui/penpot-handoff.md`.
  - Responsive authority: desktop 1440 × 1024, tablet 1024 × 768, mobile 390 × 844, with fixed-scope, summary-running, delete-confirm, source-drawer/sheet and retry-failure semantics.
  - Repository contract: `tests/deploy/test_qa_product_contract.py` owns the exact eight export names and dimensions.
- Acceptance criteria:
  - [x] Exact names/pages/dimensions — one final board exists for every required name at its specified viewport size.
  - [x] Links and geometry — final readback found 8/10/11 linked roots on desktop, 7/10 on tablet and 6/9/6 on mobile, with zero broken component roots, zero text-bounds overflow and zero actual-bounds overflow.
  - [x] Mobile targets — all named mobile actions are linked and at least 44 × 44; undersized count is zero.
  - [x] Exports — eight non-empty PNGs decode at exact dimensions and passed visual inspection for clipping, hierarchy, glyphs and state clarity.
  - [x] Handoff — revision, page/board/component IDs, token bindings, transitions, breakpoints, accessibility behavior, sample-data boundary and tooling provenance are recorded.
- Verification:
  - Penpot MCP final fresh read at revision `136` — PASS (8 unique boards, exact sizes, zero broken links/overflow/undersized mobile actions).
  - Direct Penpot export plus visual inspection — PASS (8/8 references; final cleanup re-exported and rechecked).
  - `& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/deploy/test_qa_product_contract.py --basetemp=.runtime/pytest-qa-penpot` before design write — expected RED (`2 failed`).
  - `& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/deploy/test_qa_product_contract.py --basetemp=.runtime/pytest-qa-penpot` — PASS (`2 passed`).
  - `node --test tests/design/test_penpot_handoff.mjs` — PASS (`3 passed`).
  - `git diff --check` — PASS.
- Scope confirmation:
  - changed only allowed product-UI, export, contract and mandatory packet-handoff files: yes
  - existing Penpot boards, shared component masters, application code and dependency manifests untouched: yes
- Deviations:
  - The local MCP plugin was version `2.17.0` while the Penpot web application was `2.17.2`; the patch-level warning is recorded in the handoff. Actual read, write, link, bounds and export operations all passed final verification.
- Residual risks:
  - The package registry did not yet expose a matching 2.17.2 plugin build. Replace the local compatibility shim when an official matching package is published; no design-source migration is expected.
- Commit:
  - `not committed`
