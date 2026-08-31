---
id: "notes-vertical-slice-04"
title: "交付响应式 Notes React 工作台"
status: "done"
parallel-safe: false
depends-on: ["notes-vertical-slice-01", "notes-vertical-slice-03"]
base-commit: "8ac2775dc2cb0563095f0fad5b4a83abcbf52fb9"
owner: "codex-notes-packet-04"
---

# Task Packet: 交付响应式 Notes React 工作台

## Goal

将 `/notes` 占位页替换为匹配 Penpot 权威源的 desktop/tablet/mobile React 工作台，使用真实 Notes API、内存草稿、显式保存、安全 GFM Markdown 和完整冲突/失败/删除状态。

## Non-goals

- 不修改后端、QA 页面、Gradio、Penpot 源或 deployment。
- 不实现离线存储、自动保存、自动合并、文件夹、协作或 AI 改写。
- 不把草稿、Session 或任务状态写入浏览器存储。

## Delivery context

包 01 提供视觉事实，包 03 提供后端契约。现有 `App.tsx` 让 `/notes` 落入 `MigrationPage`；本包交付可单独使用的 Note library + editor，不含 QA 入口联动。

## Relevant files and current interfaces

- `web/src/App.tsx:25-28` — route-to-page selection seam.
- `web/src/layout/navigation.ts:34-38` — existing Notes navigation item.
- `web/src/features/qa/api.ts` and `queries.ts` — API/query/error/CSRF conventions.
- `web/src/pages/QaPage.tsx:18-19,78-79` — authenticated capability loading/disabled pattern.
- `web/src/components/QaWorkspace/QaWorkspace.tsx:77,83` — source/drawer/status styling and accessibility precedent.
- `web/package.json` — exact dependency pinning and test/type/lint/build scripts.
- Packet 01 handoff/map/PNGs and packet 03 `/api/v1/notes` DTOs are authoritative.
- Existing changes to preserve: completed packet 01 and 03 outputs and review artifacts.

## Prerequisites

### Packet dependencies

- `notes-vertical-slice-01` and `notes-vertical-slice-03` must be `done`.

### Repository/base state

- Base plan commit: `8ac2775dc2cb0563095f0fad5b4a83abcbf52fb9`; implementation starts from dependency commits.

### External prerequisites

- npm registry access only for exact `react-markdown@10.1.0`, `remark-gfm@4.0.1`, `rehype-sanitize@6.0.0` install.

## Explicit change boundary

### Allowed files

- Modify: `docs/agent-workflow/task-packets/2026-08-30-notes-vertical-slice/04-notes-react-workspace.md` (status and handoff only)
- Modify: `web/package.json`
- Modify: `web/package-lock.json`
- Modify: `web/src/App.tsx`
- Modify: `web/src/layout/navigation.ts`
- Create: `web/src/features/notes/types.ts`
- Create: `web/src/features/notes/api.ts`
- Create: `web/src/features/notes/queries.ts`
- Create: `web/src/features/notes/api.test.ts`
- Create: `web/src/components/MarkdownPreview/MarkdownPreview.tsx`
- Create: `web/src/components/MarkdownPreview/MarkdownPreview.test.tsx`
- Create: `web/src/components/MarkdownPreview/markdown-preview.css`
- Create: `web/src/pages/NotesPage.tsx`
- Create: `web/src/pages/NotesPage.test.tsx`
- Modify: `web/src/pages/DocumentsPage.test.tsx`
- Create: `web/src/components/NotesWorkspace/NotesWorkspace.tsx`
- Create: `web/src/components/NotesWorkspace/NotesWorkspace.test.tsx`
- Create: `web/src/components/NotesWorkspace/NoteList.tsx`
- Create: `web/src/components/NotesWorkspace/NoteEditor.tsx`
- Create: `web/src/components/NotesWorkspace/NoteSourcePanel.tsx`
- Create: `web/src/components/NotesWorkspace/NoteClearDialog.tsx`
- Create: `web/src/components/NotesWorkspace/notes-workspace.css`

### Allowed behavior changes

- Add exact Markdown dependencies, Notes API/query modules and Notes route UI.
- Reuse existing primitives/styles by import, without modifying their behavior.

### Forbidden changes

- No Python/API/QA/legacy/Penpot/E2E/deployment files.
- No raw HTML renderer, `rehype-raw`, unsafe protocols, browser storage, fake production data or silent version overwrite.
- Do not change existing navigation labels/order except replacing the `/notes` target implementation.

## Interface contract

### Consumes

- Packet 03 Notes DTOs/routes/error codes/capability.
- Packet 01 exact boards/tokens/state semantics.
- Existing API client, CSRF provider, TanStack Query provider and AppShell.

### Produces

- Typed Notes records/source/projection/filter/create/update shapes and stable query keys.
- Infinite cursor query plus selective list/detail invalidation mutations.
- `MarkdownPreview` using `react-markdown`, `remark-gfm`, `rehype-sanitize` with raw HTML disabled.
- `NotesPage`/`NotesWorkspace` route that owns URL filters/selection and React-memory draft separately.

### Invariants

- No draft persistence before explicit save; network/conflict errors retain local text.
- Unknown URL params stripped; source prefill URL contains identifiers only.
- Deleted source never displays old title/excerpt; projection failure never blocks CRUD.
- Desktop ≥1200, tablet 768–1199, mobile <768; mobile targets ≥44px; overlays trap/restore focus.

## Required behavior

- List/search/tag/source filter, cursor load-more, create/read/edit/delete/clear, edit/preview, source panel/drawers and projection retry.
- `NOTE_VERSION_CONFLICT` modal keeps draft and offers reload/copy/cancel only; no auto merge or retry with new version.
- Unsaved navigation confirmation and mobile Back preserve list query/scroll and local draft until decision.
- Safe links allow only http/https/relative and add external `noopener noreferrer`; scripts/events/iframe/data/javascript never render.
- Loading, empty, error, source-deleted, pending/running/failed projection and destructive-confirm states match Penpot.

## Implementation guidance

Implement data layer/renderer tests first, then route and workspace tests. Use semantic roles and labels before test IDs. Use CSS media queries tied to approved viewports, not user-agent detection. Do not duplicate API error parsing. Run dependency audit after exact install.

## Acceptance criteria

- [x] API/query and sanitized Markdown tests pass, including hostile payloads.
- [x] `/notes` no longer renders MigrationPage when enabled and has an explicit disabled state.
- [x] All CRUD/filter/paging/draft/conflict/projection/source-deleted interactions are covered.
- [x] Desktop/tablet/mobile DOM and accessibility behaviors match packet 01.
- [x] Typecheck, lint, build and moderate npm audit pass with exact dependency pins.

## Test and verification commands

```powershell
Set-Location web
npx vitest run src/features/notes/api.test.ts src/components/MarkdownPreview/MarkdownPreview.test.tsx src/pages/NotesPage.test.tsx src/components/NotesWorkspace/NotesWorkspace.test.tsx src/pages/DocumentsPage.test.tsx
npm test -- --run
npm run typecheck
npm run lint
npm run build
npm audit --audit-level=moderate
Set-Location ..
git diff --check
```

Expected: all commands PASS; lockfile includes only reviewed dependency changes.

## Stop conditions

Stop with a reality-conflict report if dependencies are incomplete, API/design contracts differ, an existing shared component must be edited, moderate audit cannot be resolved in boundary, or implementation requires browser storage/raw HTML.

## Reality-conflict resolution: superseded `/notes` route assertion

- The repository-wide frontend suite contains an existing assertion that `/notes` renders `MigrationPage`. Packet 04 intentionally replaces that route, so the assertion is now product-contract debt rather than a protected regression.
- Resolution: extend the packet boundary narrowly to `web/src/pages/DocumentsPage.test.tsx` and update only the superseded `/notes` expectation to the reviewed Notes route/capability behavior. Do not weaken unrelated document/navigation assertions.

## Corrective review requirements

- Hydrate an existing Note only after its detail query resolves; never let an initialization placeholder become a dirty draft. Conflict reload must explicitly replace local state from a newly fetched server record.
- Treat QA source-prefill parameters separately from list filters; `qa_answer` must never be sent as an unsupported list `source_kind`.
- Guard browser Back and AppShell/SPA navigation as well as internal selection changes; preserve a copyable local draft on remote deletion/404.
- Mobile filter and clear flows must be complete, visible, keyboard accessible and use >=44px targets.
- Every local overlay must trap focus, close on Escape and restore focus. Reject credentialized absolute URLs in Markdown.
- Add tests for derived Markdown titles, all projection states, source tombstones/locator actions, empty CTA, cursor pagination and Penpot-responsive state structure.

## Implementation handoff

- Status: done
- Files changed:
  - `web/package.json`
  - `web/package-lock.json`
  - `web/src/App.tsx`
  - `web/src/features/notes/types.ts`
  - `web/src/features/notes/api.ts`
  - `web/src/features/notes/queries.ts`
  - `web/src/features/notes/api.test.ts`
  - `web/src/components/MarkdownPreview/MarkdownPreview.tsx`
  - `web/src/components/MarkdownPreview/MarkdownPreview.test.tsx`
  - `web/src/components/MarkdownPreview/markdown-preview.css`
  - `web/src/pages/NotesPage.tsx`
  - `web/src/pages/NotesPage.test.tsx`
  - `web/src/pages/DocumentsPage.test.tsx` (superseded `/notes` assertion only)
  - `web/src/components/NotesWorkspace/NotesWorkspace.tsx`
  - `web/src/components/NotesWorkspace/NotesWorkspace.test.tsx`
  - `web/src/components/NotesWorkspace/NoteList.tsx`
  - `web/src/components/NotesWorkspace/NoteEditor.tsx`
  - `web/src/components/NotesWorkspace/NoteSourcePanel.tsx`
  - `web/src/components/NotesWorkspace/NoteClearDialog.tsx`
  - `web/src/components/NotesWorkspace/notes-workspace.css`
- Acceptance criteria:
  - [x] API/query and hostile Markdown tests pass; raw HTML and unsafe protocols are not rendered and external links receive `noopener noreferrer`.
  - [x] `/notes` routes to `NotesPage`, calls the authenticated Notes capability/API, and presents a migration/disabled state when the capability is off.
  - [x] CRUD, filters, opaque cursor load-more, memory-only draft, explicit save, conflict choices, projection retry, source tombstones and clear/delete confirmation are implemented.
  - [x] Desktop/tablet/mobile layouts use media queries at 1200px and 768px; mobile filter/clear actions use 44px minimum targets; every local overlay traps focus, closes on Escape and restores focus.
  - [x] Exact dependencies are pinned to `react-markdown@10.1.0`, `remark-gfm@4.0.1`, `rehype-sanitize@6.0.0`.
- Verification:
  - `npx vitest run src/features/notes/api.test.ts src/components/MarkdownPreview/MarkdownPreview.test.tsx src/pages/NotesPage.test.tsx src/components/NotesWorkspace/NotesWorkspace.test.tsx src/pages/DocumentsPage.test.tsx` — PASS (5 files, 35 tests).
  - `npm test -- --run` — PASS (18 files, 150 tests).
  - `npm run typecheck` — PASS.
  - `npm run lint` — PASS.
  - `npm run build` — PASS (Vite production build; existing chunk-size warning only).
  - `npm audit --audit-level=moderate` — PASS (0 vulnerabilities).
  - `git diff --check` — PASS (line-ending notices only).
  - Penpot reference inspection — PASS: `docs/product-ui/reference/penpot/desktop-notes.png` was inspected at original-size preview; the implementation follows its desktop list/editor/source hierarchy and responsive collapse semantics.
- Corrective coverage:
  - Detail hydration waits for the selected record and distinguishes pristine initialization from a dirty memory draft; conflict reload explicitly replaces the draft with the refetched server record.
  - QA prefill identifiers are separate from list filters; unsupported `qa_answer` list filters are stripped and never sent.
  - Internal selection, browser Back, beforeunload and AppShell/SPA navigation are guarded; remote 404/deletion preserves a copyable local draft.
  - Mobile filter/clear surfaces, cursor load-more, empty CTA, derived first-heading titles, counts/badges/pills, projection retry/tombstones and source locator copy are covered.
  - Markdown rejects credentialized and protocol-relative absolute URLs, and external links use `noopener noreferrer`.
  - Successful create/update consumes the returned Note to clear the dirty state; source creation requires fully validated QA identifiers and normal source filters remain manual-note creation.
  - Browser Back cancellation restores the existing history entry with a calculated `history.go` delta; projection retry failures are caught and surfaced without unhandled rejections.
  - Empty state exposes both explicit “新建笔记” and “从 QA 记录” actions; list count is labeled as loaded rows rather than an unsupported total.
  - Clear removes all cached Note detail records before list invalidation; source locator copy reports both success and failure without blocking the drawer.
  - Fresh-note drafts establish an empty baseline immediately, so unsaved create navigation is guarded; popstate tests cover both cancel-and-restore and confirm-and-proceed paths.
  - A shared discard gate now owns internal selection, desktop/mobile filter commits, Back, and programmatic QA CTA navigation; canceled operations preserve URL, selection and draft, while confirmed operations intentionally clear selection or leave the route.
  - Fresh create consumes the returned Note before committing the URL selection through a discard-bypass callback; the regression confirms one create request, no prompt, saved selection, pristine draft and disabled repeat Save.
- Local browser smoke — local `/notes` navigation reached the authenticated login gate; no unauthenticated Notes screenshot is claimed. Responsive DOM/a11y tests cover the three approved layout states.
- Deviations:
  - `web/src/layout/navigation.ts` required no change because its Notes item already matched the approved label/order.
- Residual risks:
  - The workspace does not alter the shared QA components; QA-to-Notes navigation remains Packet 05 scope.
- Commit:
  - `4643ee0` — corrective implementation and tests.
  - `6971d5f` — final exact handoff metadata.
  - `dd9e27c` — second-review corrective implementation and tests.
  - `f94460f` — history restoration safety refinement.
  - `83aee11` — new-note baseline and history regression coverage.
  - `616b988` — third-review shared discard-navigation guard and regression coverage.
  - Final-review create-save ordering commit pending.
