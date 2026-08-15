---
id: "document-library-vertical-slice-05"
title: "Build the responsive React document library"
status: "ready"
parallel-safe: false
depends-on: ["document-library-vertical-slice-01", "document-library-vertical-slice-04"]
base-commit: "f90883e71d2fa73a7cb981b11478b68519d8ce80"
owner: "unassigned"
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
node scripts/design_tokens.mjs --check
git diff --check
```

Expected: every command exits 0; component-map exact IDs/exports/variants verified.

## Stop conditions

Stop on any standard reality conflict, incomplete prerequisite, API/Penpot mismatch, needed shared-component/backend/E2E edit, missing design node ID, or overlapping frontend file.

## Implementation handoff

Replace with template handoff, including route/state/query/accessibility test counts, design mapping evidence, build gates, scope confirmation, deviations/risks and commit.
