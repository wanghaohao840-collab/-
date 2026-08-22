---
id: "document-library-vertical-slice-05b"
title: "Align completed document imports to the Penpot complete state"
status: "ready"
parallel-safe: false
depends-on: ["document-library-vertical-slice-05a"]
base-commit: "d1cb6d5"
owner: "unassigned"
---

# Corrective Task Packet: Align completed imports to the complete state

## Trigger

Task 6 generated all six focused visual candidates successfully, then human comparison found an unapproved terminal summary block (`最近导入结果 · 完成 1 · 取消 0`) above the list in desktop, tablet and mobile complete states. All three authoritative complete boards are list-first after the toolbar/filter and contain no terminal import panel.

## Required correction

- `ImportBatchPanel` must render panels only for batches that are active or contain failed tasks.
- Batches containing only succeeded/cancelled terminal tasks must not occupy visual or semantic space in the Complete page.
- Active, retry-wait, cancel-requested, mixed active+failed and terminal failed behavior must remain unchanged.
- Server cache/history remains authoritative; this is presentation filtering only, not deletion or mutation of terminal batches.
- Remove now-unused terminal block CSS if applicable.

## Allowed files

- `web/src/components/ImportBatchPanel/ImportBatchPanel.tsx`
- `web/src/components/ImportBatchPanel/ImportBatchPanel.test.tsx`
- `web/src/styles/documents.css` only for removal of now-unused terminal styles
- this packet, `.superpowers/sdd/progress.md`, `.superpowers/sdd/task-5b-report.md`

## Forbidden files

- No query/cache/API/backend, page/toolbar, AuthProvider/AppShell, E2E, fixtures/config, dependencies, tokens, Penpot source/reference or snapshot changes.
- Preserve the four existing Task 6 dirty E2E files byte-for-byte and never stage them.

## Test-first acceptance

- RED: a succeeded-only and a cancelled-only batch currently render a terminal summary; assert both are absent from DOM.
- GREEN: active and failed/mixed focused tests still pass; Task 5 focused and full frontend gates pass.
- Fresh complete desktop/tablet/mobile actuals show toolbar/filter followed directly by the list panel, matching Penpot structure.

## Handoff

Record RED/GREEN, changed paths, visual comparison, full gates and commit; leave only the pre-existing Task 6 E2E changes dirty.
