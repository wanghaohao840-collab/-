---
id: "qa-vertical-slice-06"
title: "Build responsive QA workspace"
status: "ready"
parallel-safe: false
depends-on: ["qa-vertical-slice-05"]
base-commit: "6b1548972cc3819d45c89edf0939931d80c4d362"
owner: "unassigned"
---

# Task Packet: Build responsive QA workspace

## Goal

Implement the real `/qa` React data layer and accessible desktop/tablet/mobile workspace from the verified Penpot source, including fixed-scope creation, conversation/message/citation states, durable summary polling/cancel, retry and deletion.

## Non-goals

- No backend contract changes, local fake product data or browser-only message persistence.
- No SSE implementation; polling remains replaceable.
- No new visual system or unverified Penpot component identities.

## Delivery context

The current React shell protects routes and owns navigation/session/query caching. The QA page must consume packet 05 resources through the shared client and make reconnectable server state visible. Desktop is three-column inside AppShell; tablet uses side drawers; mobile uses bottom sheets with 44px targets and safe-area spacing.

## Relevant files and current interfaces

- `web/src/App.tsx:11-24` — protected route tree and placeholder integration point.
- `web/src/layout/AppShell.tsx:11` and `web/src/layout/navigation.ts:8` — shell/navigation contracts.
- `web/src/api/client.ts:10` — shared `ApiError` and fetch/auth/error handling.
- `web/src/pages/DocumentsPage.tsx:169` — document-selection handoff point.
- `web/src/features/documents/queries.ts:44-181` — query-key, invalidation and server-state conventions.
- `web/src/main.tsx:12-21` — shared `QueryClient`; do not create a second client.
- `web/src/layout/AppShell.test.tsx:155` and `web/src/pages/DocumentsPage.test.tsx:135` — regression seams.
- Existing changes to preserve: packets 01–05, especially exact exported board IDs/states and API DTOs.

## Prerequisites

### Packet dependencies

- `qa-vertical-slice-05` must be `done`.

### Repository/base state

- Base commit plus prior packet handoffs/commits.
- Packet 01 final PNGs/handoff and packet 05 API are authoritative.

### External prerequisites

- Node dependencies already installed from `web/package-lock.json`; no new package is expected.

## Explicit change boundary

### Allowed files

- Create: `web/src/features/qa/types.ts`, `api.ts`, `queries.ts`
- Create: `web/src/features/qa/api.test.ts`, `queries.test.ts`
- Create: `web/src/features/qa/components/ConversationList.tsx`, `MessageList.tsx`, `QaComposer.tsx`, `SourcePanel.tsx`, `QaDrawer.tsx`, `QaDeleteDialog.tsx`
- Create/Test: focused `*.test.tsx` files beside QA components
- Create: `web/src/pages/QaPage.tsx`, `web/src/pages/QaPage.test.tsx`, `web/src/styles/qa.css`
- Modify: `web/src/App.tsx`, `web/src/main.tsx`, `web/src/layout/navigation.ts`, `web/src/api/client.ts`
- Modify/Test: `web/src/components/DocumentList/DocumentList.tsx`, its tests, `web/src/pages/DocumentsPage.tsx`, its tests, `web/src/layout/AppShell.test.tsx`
- Modify: `docs/product-ui/penpot-component-map.json`
- Test: `tests/design/test_penpot_component_map.mjs`, `tests/design/test_penpot_handoff.mjs`

### Allowed behavior changes

- Replace `/qa` placeholder with real protected workspace and add “开始问答” handoff from selected/owned documents.

### Forbidden changes

- No backend, dependencies/lockfiles, design tokens or unrelated route/page redesign.
- No raw owner IDs, paths, prompts or errors rendered/cached.
- No localStorage message source of truth and no optimistic deletion that can resurrect server data.
- Do not mark a code wrapper as Penpot component without an actual verified component ID.

## Interface contract

### Consumes

- Packet 05 DTOs/endpoints and common `ApiError`; existing session/query client; packet 01 boards and IDs.

### Produces

- Typed QA client functions and query/mutation hooks with stable keys.
- `QaPage` and named components for conversation list, messages, composer, citations, drawers/sheets and deletion confirmation.
- Route `/qa`; document handoff via navigation state/query containing only document IDs, followed by server-side validation.

### Invariants

- Server resources are authoritative; invalidations reconcile ask/summary/delete states.
- A conversation's document scope is visibly fixed; compare needs two documents.
- Only one submission is active per conversation; `client_request_id` is stable across network retry but renewed for a new user action.
- Keyboard order, focus trap/return, Escape close, ARIA labels/live status, reduced motion and `44x44` mobile targets are preserved.

## Required behavior

- Empty/loading/error/retry, completed answer/citations, summary progress/cancel and delete confirmation are all truthful server states.
- `>=1200px`: `240px minmax(0,1fr) 320px`; `768–1199px`: chat primary with side drawers; `<768px`: one-column chat with bottom sheets above 64px nav.
- Route-disabled capability renders the migration explanation, not a broken workspace.
- Source panel exposes document name/page/snippet/reference safely and supports keyboard/screen-reader use.

## Implementation guidance

Model the data layer first and test exact URLs/bodies/errors/invalidation. Then build semantic components using existing tokens. Use CSS media/container queries already supported by the project and keep AppShell outside the QA inner grid. Implement polling through a hook boundary that can later switch to SSE.

## Acceptance criteria

- [ ] Client/hook tests prove exact endpoint, idempotency ID reuse, error trace mapping, polling/cancel and cache invalidation.
- [ ] Page/component tests cover empty, ask, summary, failure/retry, sources, delete and route-disabled states.
- [ ] Desktop/tablet/mobile layouts match verified Penpot semantics with accessible focus/targets and no overflow.
- [ ] Document handoff creates only validated fixed-scope conversations; existing shell/document tests remain passing.

## Test and verification commands

```powershell
Push-Location web
npm run typecheck
npm run lint
npm test -- --run
npm run build
Pop-Location
node --test tests/design/test_penpot_component_map.mjs tests/design/test_penpot_handoff.mjs
git diff --check
```

Expected: all commands PASS with no new moderate-or-higher advisory introduced.

## Stop conditions

Stop with a reality-conflict report if packet 05 DTOs differ, Penpot IDs/exports are missing, a package/lockfile change is required, or implementation needs files outside the boundary.

## Implementation handoff

Replace this section with packet ID/status, delivered workspace, files/interfaces, acceptance/verification evidence, scope/deviation/risk confirmation and commit.

