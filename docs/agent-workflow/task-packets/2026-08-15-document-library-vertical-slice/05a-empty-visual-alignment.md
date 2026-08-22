---
id: "document-library-vertical-slice-05a"
title: "Align the real document empty state to Penpot"
status: "done"
parallel-safe: false
depends-on: ["document-library-vertical-slice-05"]
base-commit: "60e4525"
owner: "codex"
---

# Corrective Task Packet: Align the real document empty state to Penpot

## Trigger

Task 6 real-browser visual RED exposed material, unapproved differences between the desktop empty runtime and the authoritative `docs/product-ui/reference/penpot/documents-empty.png`. Functional E2E and document accessibility already pass in all three projects; this correction changes only the affected Task 5 presentation and focused tests.

## Required corrections

- Use the approved subtitle `管理已导入文档与批量任务` in all document states.
- Do not render the filename filter in the true empty state. Filtering remains present in the complete/list state.
- Keep the exact empty copy and import actions.
- Place `每批最多 20 个文件 · 单文件 100 MiB · 每批 500 MiB` outside and below the empty card, as in Penpot.
- Replace the standalone square `文` tile above the title with the Penpot empty-state title/icon composition; do not introduce a new asset or dependency.
- Align the desktop empty card vertical position and height to the Penpot structure while preserving responsive tablet/mobile behavior and 44 px actions.
- Preserve real-data-only behavior, one h1, focus order, query/auth isolation and all Task 5 functionality.

## Allowed files

- `web/src/components/DocumentToolbar/DocumentToolbar.tsx`
- `web/src/pages/DocumentsPage.tsx`
- `web/src/pages/DocumentsPage.test.tsx`
- `web/src/styles/documents.css`
- focused component/page tests if necessary under existing Task 5-owned paths
- this packet, `.superpowers/sdd/progress.md`, `.superpowers/sdd/task-5a-report.md`

## Forbidden files and behavior

- No backend, API/query/cache, AuthProvider, AppShell/navigation, shared component internals, E2E acceptance, Playwright config/fixtures, dependencies, tokens, Penpot source/handoff/reference or snapshot changes.
- Do not hide the filter when real documents exist or fabricate data to reach a visual state.

## Test-first acceptance

- Add RED assertions for exact subtitle, filter absent when `items=[]`, filter present when documents exist, limit note outside the empty card, and no standalone empty `文` tile.
- GREEN the focused Task 5 page tests, then run the full frontend test/typecheck/lint/build gates, component map, authoritative token check and `git diff --check`.
- Compare a fresh desktop empty actual screenshot to `documents-empty.png`; structural differences above must be closed before Task 6 resumes.

## Stop conditions

Stop if matching requires a new asset/dependency, shared shell geometry change, fabricated records, or any production change beyond the allowed files.

## Handoff

Record RED/GREEN evidence, exact geometry/comparison notes, changed paths, gates and commit. Leave the worktree clean for independent review.

## Implementation handoff

- Status: done; independent visual re-review approved with no Critical, Important or Minor findings.
- Files changed:
  - `web/src/components/DocumentToolbar/DocumentToolbar.tsx`
  - `web/src/pages/DocumentsPage.tsx`
  - `web/src/pages/DocumentsPage.test.tsx`
  - `web/src/styles/documents.css`
  - this packet, `.superpowers/sdd/progress.md`, `.superpowers/sdd/task-5a-report.md`
- RED: the focused empty-page case produced five soft assertion failures: approved subtitle absent, empty filter present, limits inside the card, standalone icon class present and standalone `文` visible. The complete/list filter case remained green.
- GREEN: exact subtitle and copy render from real empty data; the filter is absent only for empty documents and remains present for the complete list; limits are outside the card; a CSS title halo replaces the standalone tile.
- Desktop comparison: authoritative card border `x=540..1099`, `y=302..601` and halo pixels `x=788..850`, `y=420..482`; fresh ignored browser actual matches both exactly, with a `560 × 300` card and a 44 px empty action. Tablet retains a `560 × 320` card, mobile retains `342 × 320`, both have 44 px actions and neither overflows horizontally.
- Verification: page `12/12`, Task 5 focused `36/36`, full frontend `101/101`, typecheck/lint/build, component map `6/6`, authoritative token and diff checks PASS.
- Scope: implementation diff from `60e4525` is limited to the four Task 5-owned production/test files plus the three handoff ledgers. The four pre-existing Task 6 E2E changes were preserved byte-for-byte and never staged.
- Deviations: none.
- Residual risk: Task 6 owns final multi-project Playwright visual acceptance; the fresh Task 5a actual is ignored and intentionally not committed.
- Commit: `67132d2` (`fix: align document empty state to Penpot`); handoff ledgers in the subsequent documentation commit.
- Independent review: Approved. The reviewer independently reproduced the card/halo pixel equality and all reported gates.
