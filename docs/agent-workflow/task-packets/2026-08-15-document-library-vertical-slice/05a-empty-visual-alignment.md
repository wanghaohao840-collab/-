---
id: "document-library-vertical-slice-05a"
title: "Align the real document empty state to Penpot"
status: "ready"
parallel-safe: false
depends-on: ["document-library-vertical-slice-05"]
base-commit: "6f592c7"
owner: "unassigned"
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
