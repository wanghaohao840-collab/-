# Research Workspace Implementation Plan

> Execute inline in this session as explicitly requested by the user. No delegation or production deployment.

**Goal:** Implement the approved evidence workspace while preserving search and note source contracts.
**Architecture:** SearchPage retains session identity and orchestration; presentational components live in components/ResearchWorkspace. Existing features/search and features/notes API layers remain authoritative.
**Tech Stack:** React 19, TypeScript, TanStack Query, existing CSS, Vitest and Playwright.

## Constraints

Follow ../specs/2026-09-12-research-workspace-design.md. No backend changes, new dependencies, fabricated modes, citation identities, progress or Memory data. Preserve locator, session isolation, request generation checks and note idempotency. Do not include unrelated deployment changes.

## 1. Extract source and presentation components

- [x] Move sourceLocation/highlightedExcerpt to components/ResearchWorkspace/presentation.tsx and ResultDetail to EvidenceDetail.tsx, adjusting imports; add initialEditing boolean for direct note entry.
- [x] Keep source payload exactly `{ kind: "document_chunk", locator: result.locator }` and existing client_request_id behavior.
- [x] Retain SearchPage test coverage for source removal, stale saves, delayed user changes, clipboard, focus and literal highlight escaping.

## 2. Build workspace and connect state

- [x] Add ResearchPageHeader.tsx, ResearchScopePanel.tsx, ResearchCommandBar.tsx, ResearchStates.tsx and EvidenceList.tsx with controlled props using existing Document/SearchResult types.
- [x] Replace SearchWorkspace markup with components. Keep fingerprint and activeDetail checks. Scope uses native checkbox semantics styled as selectable document rows; narrow layouts expose a toggle with aria-expanded.
- [x] Add suggestions that only set query. Show true selected and returned counts. Add numbered evidence, direct note entry and real QA/copy actions.
- [x] Replace styles/search.css with scoped responsive layout, focus states, low-motion transitions and readable surfaces.

## 3. Validate and deliver

- [x] Update SearchPage.test.tsx for approved labels and closed more menu; add suggestion-without-request and direct note entry assertions.
- [x] Run `npm test` and `npm run lint` in web, then `npm run build`; expect zero failures.
- [x] Update e2e/search.spec.ts for labels and direct note entry. Execute `npx playwright test e2e/search.spec.ts` against isolated fixture services, expecting desktop/tablet/mobile passes.
- [x] Capture idle and results at 1920×1080, 1440×900, 1280×800 and 390×844; inspect screenshots and overflow. Preserve actual backend search→source→note→QA tests and axe checks.
- [x] Review diff, update this checklist, commit only feature files and attempt push. Report evidence and any deployment/push limitations.
