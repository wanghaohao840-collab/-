---
id: "document-library-vertical-slice-05"
title: "Build the responsive React document library"
status: "done"
parallel-safe: false
depends-on: ["document-library-vertical-slice-01", "document-library-vertical-slice-04"]
base-commit: "1ac419a3be25b167db16cf6d218f26e9f9f5bc4e"
owner: "completed"
---

# Task Packet: Build the responsive React document library

## Goal

Replace only the protected `/documents` migration page with the approved, real-data React experience for listing, filtering, importing, progress, retry, cancellation and deletion across all three viewports.

## Non-goals

- Do not modify backend/import/auth/session/Gradio/Penpot nodes, add dependencies, or migrate other product routes.
- Do not add pagination, preview, tags, folders, rename, batch delete, persisted filters or fabricated stats.
- Do not write E2E snapshots; Packet 06 owns them.

## Delivery context

Packet 01 provides exact Penpot boards/IDs. Packet 04 provides authenticated JSON. The existing AppShell/navigation/AuthProvider/TanStack Query infrastructure must be reused. The page must distinguish loading/error/empty from real data and poll only while tasks are active.

## Relevant files and current interfaces

- `web/src/App.tsx:10-30` — navigation map currently renders MigrationPage for every protected route.
- `web/src/auth/AuthProvider.tsx:24-31` and `:74` — `request<T>()`, in-memory CSRF and stale-401 handling; feature code must use it.
- `web/src/main.tsx` — one QueryClient with no automatic retries and current style imports.
- `web/src/layout/AppShell.tsx`, `web/src/styles/app-shell.css` — approved shell; must not be duplicated.
- `web/src/components/Button/Button.tsx`, `TextField/TextField.tsx` and current overlay patterns in MoreDrawer — component/focus conventions.
- `docs/product-ui/penpot-component-map.json` — five current code mappings; schema test verifies paths/exports/variants.
- Packet 01 handoff/PNGs and Packet 04 Pydantic response fields are authoritative inputs.
- Existing changes to preserve: completed prerequisite commits and planning artifacts.

## Prerequisites

### Packet dependencies

- `document-library-vertical-slice-01` and `document-library-vertical-slice-04` must be `done`.

### Repository/base state

- Base ancestor `f90883e...` plus prerequisite commits.
- Exact API and Penpot IDs must match their handoffs; no inferred schema/geometry.

### External prerequisites

- `npm ci` from `web`; installed dependencies already cover all work.

## Explicit change boundary

### Allowed files

- Create: `web/src/features/documents/types.ts`
- Create: `web/src/features/documents/api.ts`
- Create: `web/src/features/documents/queries.ts`
- Create: `web/src/features/documents/queries.test.tsx`
- Create: `web/src/components/DocumentToolbar/DocumentToolbar.tsx`
- Create: `web/src/components/DocumentList/DocumentList.tsx`
- Create: `web/src/components/ImportDialog/ImportDialog.tsx`
- Create: `web/src/components/ImportDialog/ImportDialog.test.tsx`
- Create: `web/src/components/ImportBatchPanel/ImportBatchPanel.tsx`
- Create: focused tests beside DocumentToolbar/DocumentList/ImportBatchPanel when behavior requires them.
- Create: `web/src/pages/DocumentsPage.tsx`
- Create: `web/src/pages/DocumentsPage.test.tsx`
- Create: `web/src/styles/documents.css`
- Modify: `web/src/App.tsx`
- Modify: `web/src/main.tsx`
- Modify: `docs/product-ui/penpot-component-map.json`
- Modify: this packet for handoff.

### Allowed behavior changes

- Real `/documents` route, document feature API/hooks/components/styles and verified component mappings.

### Forbidden changes

- Do not edit package/lock/config, existing shared component internals, AuthProvider, AppShell/navigation, backend, E2E, Penpot handoff/PNGs or unrelated route copy.
- Do not write auth/task state to browser storage or bypass `auth.request`.
- Do not hardcode raw brand colors; use generated token variables.

## Interface contract

### Consumes

- Packet 04 document/import shapes and routes exactly.
- AuthContext `request<T>(input, options)`; existing `ApiError` fields.
- Packet 01 board/component IDs and responsive geometry.

### Produces

- `DOCUMENTS_QUERY_KEY = ['documents']` and `IMPORTS_QUERY_KEY = ['imports',{limit:20}]`.
- `hasActiveImports()` true only for queued/running/retry_wait.
- Query/mutation hooks with 2000ms active-only polling and server-summary cache updates.
- `DocumentsPage` with one h1 and approved empty/importing/partial-failure/complete states.
- Accessible import dialog/mobile sheet, delete confirmation, list/actions and deduplicated polite live region.
- Component-map entries only for code components that also exist/verified in Penpot.

### Invariants

- Other protected routes remain MigrationPage; AppShell/navigation/auth behavior unchanged.
- Null size/date is omitted, not replaced by fake values.
- No optimistic task state machine; returned server batch is authoritative.
- Mobile actions >=44px; overlay focus trap/Escape/return/scroll restore and reduced-motion are preserved.

## Required behavior

- Loading uses Skeleton, error preserves prior data/refetch, empty is not shown during load/error.
- Name filter is client-only and case-insensitive; default documents sort is server order.
- Active batch sits above list; failed item retry and batch retry-all; cancellable item cancel; terminal panel can collapse.
- Successful transition invalidates documents; all terminal batches stop timer; window focus refetches once.
- FormData leaves Content-Type to browser; client validation is feedback only and server envelope controls final errors.
- Delete dialog contains actual name, busy state, non-optimistic success/refetch, and recoverable error focus.

## Implementation guidance

Follow Task 5 in the plan. Start with fetch-stub unit tests using existing Vitest patterns; do not add MSW. Keep feature modules small and typed. Reuse Button/TextField; implement overlay mechanics locally without changing shared drawer. Match Penpot structure/tokens first, then responsive CSS at existing 768/1200 breakpoints.

## Acceptance criteria

- [ ] `/documents` is real and every other navigation route remains migration state.
- [ ] All API operations use AuthProvider and exact Packet 04 data/errors.
- [ ] Polling/invalidation/state rendering and no-unhandled-rejection behavior are unit-tested.
- [ ] Dialog/sheet focus/keyboard/scroll/reduced-motion/mobile targets are tested.
- [ ] Three-viewport layout matches Packet 01 references without fake data.
- [ ] Unit, typecheck, lint, build, mapping and token checks pass; dist/node_modules remain ignored.

## Test and verification commands

```powershell
Set-Location web
npm ci
npm test
npm run typecheck
npm run lint
npm run build
Set-Location ..
node --test tests/design/test_penpot_component_map.mjs
node scripts/design_tokens.mjs --check design/tokens/zhiyan.tokens.json web/src/styles/tokens.css
git diff --check
```

Expected: every command exits 0; component-map exact IDs/exports/variants verified.

## Stop conditions

Stop on any standard reality conflict, incomplete prerequisite, API/Penpot mismatch, needed shared-component/backend/E2E edit, missing design node ID, or overlapping frontend file.

## Implementation handoff

- Status: done; ready for independent review.
- Files changed:
  - `web/src/features/documents/types.ts`
  - `web/src/features/documents/api.ts`
  - `web/src/features/documents/queries.ts`
  - `web/src/features/documents/queries.test.tsx`
  - `web/src/components/DocumentToolbar/DocumentToolbar.tsx`
  - `web/src/components/DocumentList/DocumentList.tsx`
  - `web/src/components/ImportDialog/ImportDialog.tsx`
  - `web/src/components/ImportDialog/ImportDialog.test.tsx`
  - `web/src/components/ImportBatchPanel/ImportBatchPanel.tsx`
  - `web/src/pages/DocumentsPage.tsx`
  - `web/src/pages/DocumentsPage.test.tsx`
  - `web/src/styles/documents.css`
  - `web/src/App.tsx`
  - `web/src/main.tsx`
  - `docs/product-ui/penpot-component-map.json`
  - this packet
- Produced behavior:
  - `/documents` now uses authenticated Packet 04 data for documents and the latest 20 import batches; all other five protected navigation routes retain `MigrationPage`.
  - Typed feature APIs use `useAuth().request<T>()`, exact encoded mutation routes and browser-owned multipart headers.
  - Imports poll every 2000 ms only while visible data is queued/running/retry-waiting; focus refetch, active-to-success document invalidation, authoritative server-batch caching and delete success/failure invalidation are implemented without optimistic business state.
  - Loading, initial error, stale-data error, exact empty, filtered complete, active, partial-failure and collapsed terminal states render exclusively from API data.
  - Import modal/mobile sheet and delete confirmation provide focus entry/trap/Escape/return, scroll restoration, qualified action names, safe errors, one deduplicated polite live region and 44 px action targets.
- Focused test coverage (25 tests total; categories overlap):
  - Route: 1 case proves real `/documents` and every one of the other five protected navigation routes remains migration-only.
  - State/rendering: 5 cases cover loading versus empty, initial error, stale-data preservation, complete/filter/duplicate/null metadata, and active/partial-failure/terminal imports.
  - Query/API: 7 cases cover exact keys/routes/FormData, active-only polling, single focus refetch, success invalidation, server-summary authority and delete success/failure refresh.
  - Accessibility/overlay: 5 focused cases cover import focus trap plus Shift+Tab/Escape/return, overflow restoration, mobile/44 px CSS contracts, recoverable delete error focus/return and deduplicated live status.
  - Mutation/validation: remaining focused assertions cover retry/retry-all/cancel/delete routes, safe `ApiError` messages, drag/invalid state, stable duplicate-file removal and all three upload limits.
- Acceptance criteria:
  - [x] `/documents` is real and all other protected navigation remains migration state.
  - [x] All document/import operations use AuthProvider and exact Packet 04 public DTOs/routes; backend-internal fields are absent from TypeScript.
  - [x] Polling, focus refresh, transition invalidation, server-authoritative cache writes, delete refresh and promise handling are unit-tested.
  - [x] Exact UI states, real-data identity/filter/null behavior and retry/cancel/delete/import flows are unit-tested without production fake data.
  - [x] Dialog/sheet focus, keyboard, body-scroll restoration, polite announcements, reduced motion and mobile target contracts are implemented and tested.
  - [x] 342/856/1096 px content widths, 342/360/420 px filter sizing and 342/160/180 px import actions follow the approved mobile/tablet/desktop handoff.
- Design mapping evidence:
  - `DocumentRow`: container `879161d4-ba5f-800d-8008-852644bff09e`; ready `f35db4ee-075c-8075-8008-7c1eea45d28f`; deleting `879161d4-ba5f-800d-8008-852644a6ce86`.
  - `ImportTaskRow`: container `879161d4-ba5f-800d-8008-8526d12f81ea`; running `f35db4ee-075c-8075-8008-7c1eefe300f4`; queued `879161d4-ba5f-800d-8008-85267e7cc503`; failed `879161d4-ba5f-800d-8008-8526c1b6c14b`; cancelled `879161d4-ba5f-800d-8008-8526c2549491`.
  - `FilePicker`: container `879161d4-ba5f-800d-8008-8526e7a901fd`; idle `f35db4ee-075c-8075-8008-7c1ef1831cf3`; drag-active `879161d4-ba5f-800d-8008-8526e71f0357`; invalid `879161d4-ba5f-800d-8008-8526e797edf6`.
  - `node --test tests/design/test_penpot_component_map.mjs` validates the map schema, paths, exports, state names and fresh verification: `6/6 passed`.
- Verification:
  - Baseline `npm ci` — PASS, 274 packages installed; unchanged lock audit reports one high-severity advisory.
  - Baseline frontend gates before Task 5 — PASS, `65/65` tests plus typecheck/lint/build; component map `6/6`, authoritative token check and diff check also passed.
  - Focused RED — expected failure, three suites failed: the page's seven new expectations saw the migration placeholder and the query/dialog modules did not exist.
  - Focused GREEN — PASS, `3 files / 25 tests`.
  - Final fresh `npm ci` — PASS, 274 packages installed/audited.
  - `npm test` — PASS, `9 files / 90 tests`.
  - `npm run typecheck` — PASS.
  - `npm run lint` — PASS.
  - `npm run build` — PASS, Vite transformed 114 modules.
  - `node --test tests/design/test_penpot_component_map.mjs` — PASS, `6/6`.
  - `node scripts/design_tokens.mjs --check design/tokens/zhiyan.tokens.json web/src/styles/tokens.css` — PASS.
  - `git diff --check` — PASS; Git emitted line-ending notices only.
- Scope confirmation:
  - Implementation commit `9571ee9` changes only the fifteen frontend/component-map files allowed by the revised Task 5 brief.
  - Shared component internals, AuthProvider, AppShell/navigation, backend, E2E/snapshots, Penpot source/handoff/PNGs, packages/lock/config, dependencies and generated tokens are unchanged.
  - `web/dist` and `web/node_modules` remain ignored.
- Deviations: None.
- Residual risks:
  - The locked dependency audit continues to report one pre-existing high-severity advisory; dependency changes are explicitly outside Packet 05.
  - One unchanged `ProtectedRoute` assertion transiently raced DOM commit while all gates ran concurrently; its isolated `4/4` rerun and the required serial full suite `90/90` both passed. No Auth file was changed.
  - Independent browser/E2E viewport acceptance remains Packet 06; mandatory independent code review is still pending.
- Commits: `9571ee9` (`feat: add responsive document library`); handoff metadata in the subsequent documentation commit.

## Reality-conflict resolution

- Baseline conflict: the abbreviated `node scripts/design_tokens.mjs --check` command exits 1 because the script requires explicit input and output paths.
- Authoritative command: `node scripts/design_tokens.mjs --check design/tokens/zhiyan.tokens.json web/src/styles/tokens.css`, as documented in the repository and product UI README files.
- Decision: verification command corrected; no acceptance, design, ownership or implementation scope change.
- Status: remains `ready`; worker may resume from the clean baseline.
