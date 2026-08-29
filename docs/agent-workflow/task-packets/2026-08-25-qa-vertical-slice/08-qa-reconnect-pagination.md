---
id: "qa-vertical-slice-08"
title: "Restore reconnectable long-history QA state"
status: "done"
parallel-safe: false
depends-on: ["qa-vertical-slice-07"]
base-commit: "7f3b91fbf72931cb98cf0dd7fad04a3b143780a6"
owner: "codex"
---

# Task Packet: Restore reconnectable long-history QA state

## Goal

Make the React QA workspace truthful for long-lived conversations and browser reloads: load the newest messages first with cursor-backed access to older history, paginate conversations, rediscover an active durable summary by conversation, and prevent conflicting actions while that summary is active.

## Non-goals

- No SSE/WebSocket, distributed queue, database schema, model/RAG, deletion-state or visual-system redesign.
- No unbounded eager loading of all history and no browser-local job/history source of truth.
- No change to existing internal oldest-first repository paging used by context/report workers.

## Delivery context

The combined review found that the frontend discards `next_cursor` and requests only the first 200 oldest messages, so sufficiently long conversations hide their latest answer and pending state. It also stores the current summary job ID only in React memory; reload cannot rediscover durable progress/cancel state and the composer does not treat an active summary as busy. Stable REST resources already exist, so this correction adds narrow discovery/recent-page reads without changing persisted IDs or state machines.

## Relevant files and current interfaces

- `app/qa_repository.py:152` — `list_messages()` is oldest-first and is used internally to build complete context; preserve it and add a separate recent-page read.
- `app/qa_job_repository.py:341` — `get(user_id, job_id)` is user-scoped; add a user+conversation-scoped active-job lookup without exposing leases.
- `app/qa_service.py:158` and `:306` — authenticated message/job facades are the API boundary.
- `api/routes/qa.py:186` and `:309` — message paging and summary-job routes; add only read-only active-job discovery and recent-page selection.
- `web/src/features/qa/api.ts:16` and `:20` — currently discard both cursors.
- `web/src/features/qa/queries.ts:22` and `:30` — convert conversation/message reads to bounded infinite queries while preserving stable root keys.
- `web/src/pages/QaPage.tsx:34` and `:64` — local-only summary ID and incomplete busy derivation.
- `web/e2e/qa.spec.ts` — real-server recovery proof; add active-summary reload without route interception.
- Existing changes to preserve: all completed packets 01–07 and the initial final-review artifact.

## Prerequisites

### Packet dependencies

- `qa-vertical-slice-07` must be `done` and the final review must remain `changes-required` for the findings above.

### Repository/base state

- Base commit: `7f3b91fbf72931cb98cf0dd7fad04a3b143780a6`.
- Existing QA IDs, DTOs, job/message states, route flag, query keys and E2E deterministic engine remain authoritative.

### External prerequisites

- Repository venv, installed Node/Playwright dependencies and Chromium; no external service or model credential.

## Explicit change boundary

### Allowed files

- Modify: `app/qa_repository.py`, `app/qa_job_repository.py`, `app/qa_service.py`
- Modify: `api/schemas/qa.py`, `api/routes/qa.py`
- Test: `tests/test_qa_repository.py`, `tests/test_qa_job_repository.py`, `tests/test_qa_service.py`, `tests/api/test_qa_routes.py`
- Modify: `web/src/features/qa/types.ts`, `api.ts`, `queries.ts`, `api.test.ts`, `queries.test.tsx`
- Modify: `web/src/components/QaWorkspace/QaWorkspace.tsx`, `QaWorkspace.test.tsx`
- Modify: `web/src/pages/QaPage.tsx`, `QaPage.test.tsx`
- Modify: `web/src/styles/qa.css` only if the load-older/load-more controls require QA-scoped spacing
- Modify/Test: `web/e2e/qa-runtime.py`, `web/e2e/qa.spec.ts` only for deterministic active-summary reload synchronization; do not update visual snapshots unless an approved state visibly changes
- Modify: `docs/product-ui/README.md`, `README.md`, this packet and `FINAL_INTEGRATION_REVIEW.md`

### Allowed behavior changes

- Add recent-first message cursor reads, active-summary discovery, bounded infinite-query consumption and truthful action disabling.

### Forbidden changes

- No database table/index migration, dependency/lockfile, auth/session, worker transition, deletion, RAG/Memory, Penpot source or unrelated E2E edits.
- No localStorage/sessionStorage job or history persistence, fake production flag, route interception, sleep-only browser synchronization or eager unbounded history fetch.
- Existing `QaRepository.list_messages()` semantics and DTO fields remain compatible; new API responses may only be additive.

## Interface contract

### Consumes

- `QaMessagePage(items, next_cursor)`, `QaJob`, active statuses `queued|running`, authenticated session token, root query keys `QA_CONVERSATIONS_KEY` and `qaMessagesKey(id)`.

### Produces

- `QaRepository.list_recent_messages(user_id, conversation_id, *, cursor=None, limit=50) -> QaMessagePage`; each page is chronological for rendering, initial page contains newest rows, and `next_cursor` requests older rows.
- `QaJobRepository.get_active_for_conversation(user_id, conversation_id) -> QaJob | None` and authenticated `QaService.get_active_job(session_token, conversation_id)`.
- `GET /api/v1/qa/conversations/{conversation_id}/summary-jobs/active` returning `{ "job": QaJobResponse | null }`; missing/foreign/deleting conversations remain safe `404`.
- Cursor-aware frontend functions and infinite hooks exposing flattened `items`, `hasNextPage`, `fetchNextPage` and polling over every loaded message page.
- QA page state that adopts discovered/just-created job identity, restores progress/cancel after reload, and treats summary mutation or active queued/running job as busy.

### Invariants

- Every repository/API read is user-scoped; no lease owner, Memory ID, path, prompt or raw error is exposed.
- Existing internal context paging, persisted rows, idempotency keys and job state transitions are unchanged.
- Loaded message order is globally chronological with no duplicate at page boundaries; conversation order remains newest-first.
- New user actions get new client request IDs; TanStack retry keeps the existing request ID.
- Polling remains 1500 ms and can later be replaced by SSE without changing resource identities.

## Required behavior

- A conversation with more than 200 messages initially renders its newest answer/pending message and can explicitly load older pages without duplication or reordering.
- More than 100 conversations remain reachable through a load-more action.
- Reloading or switching to a conversation with a queued/running summary restores stage/progress and cancellation from server discovery.
- Composer and summary action are disabled while an active summary exists; terminal job reconciliation refreshes messages/conversation and re-enables actions.
- No active job returns `{job:null}` rather than a fabricated resource; cross-user conversation IDs return the existing safe not-found envelope.

## Implementation guidance

Keep `list_messages()` unchanged. Implement recent paging with `created_at desc, id desc`, an opaque cursor using the existing encoder, and reverse each returned page before projection so render order stays chronological. The cursor must be derived from the oldest row in that page. Use `useInfiniteQuery` with bounded page sizes (`20` conversations, `50` messages); flatten conversation pages in fetch order and message pages from oldest loaded page to newest. Render explicit “加载更多对话” and “加载更早消息” controls rather than automatically fetching every page.

Use a dedicated active-job query keyed by conversation. Seed/update it when summary starts, invalidate it when a job becomes terminal, and clear transient local job identity on conversation change. The server discovery response, not component memory, is authoritative after reload. Extend the deterministic E2E adapter with synchronization only if needed to keep a summary active until the test observes and reloads it; control must remain test-process-only and must not add a production path.

## Acceptance criteria

- [x] Repository/API tests prove newest-first initial messages, opaque older cursor, stable equal-timestamp tiebreaker, active-job isolation and `{job:null}`.
- [x] Frontend tests prove both cursors are sent/flattened correctly, latest pending state drives polling, and no duplicates/order regressions occur.
- [x] Page tests prove reload discovery restores summary progress/cancel and disables ask/summary until terminal reconciliation.
- [x] Real-server Playwright reloads an active summary and observes restored progress/cancel without interception or browser storage.
- [x] Focused Python/frontend/E2E gates and the full combined regression are green. Dependencies and production paths are unchanged; the user explicitly approved updating only `qa-summary-desktop.png` after truthful busy-state controls made the prior enabled-controls baseline stale.

## Test and verification commands

Run from repository root:

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/test_qa_repository.py tests/test_qa_job_repository.py tests/test_qa_service.py tests/api/test_qa_routes.py --basetemp=.runtime/pytest-qa-08
Push-Location web
npm exec vitest run src/features/qa/api.test.ts src/features/qa/queries.test.tsx src/components/QaWorkspace/QaWorkspace.test.tsx src/pages/QaPage.test.tsx
npm run typecheck
npm run lint
npm run build
npx playwright test e2e/qa.spec.ts --workers=1
Pop-Location
git diff --check
```

Expected: all commands exit 0, no QA test is skipped, and no visual snapshot changes.

Broader regression before final acceptance:

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q --basetemp=.runtime/pytest-qa-08-full
Push-Location web
npm test -- --run
npm run test:e2e
npm audit --audit-level=moderate
Pop-Location
node --test tests/design/test_design_tokens.mjs tests/design/test_penpot_component_map.mjs tests/design/test_penpot_handoff.mjs
```

Expected: full Python, frontend, browser, dependency and design gates pass with only the already documented optional/project-conditional skips.

## Stop conditions

Stop and report `blocked` if implementation requires schema migration, changes existing internal oldest-first repository semantics, exposes worker internals, adds browser persistence, changes visual baselines, or needs files outside the boundary.

## Implementation handoff

- Packet/status: `qa-vertical-slice-08` / `done`
- Delivered interfaces: recent message cursor paging, active summary discovery, bounded infinite queries, explicit history controls, reload recovery and active-task mutual exclusion.
- Verification:
  - Focused Python: `42 passed`.
  - Focused frontend: `4` files and `25` tests passed; typecheck and lint exited `0`; production build transformed `121` modules.
  - Focused real-server QA: `12 passed`, `0 skipped` across desktop, tablet and mobile. The desktop reload assertion also passed twice consecutively with the original adapter before deterministic visual synchronization was finalized.
  - Full Python: `1036 passed, 7 skipped` (`7` documented optional/project-conditional skips).
  - Full frontend: `14` files and `128` tests passed.
  - Full Playwright: `58 passed, 2 skipped`; QA was `12/12` with `0` QA skips.
  - Design: `17/17`; npm audit: `0` vulnerabilities; pip check: no broken requirements; all required commands and `git diff --check` exited `0`.
- Scope: no schema, dependency, browser-storage, production-fake, auth, deletion, RAG/Memory or internal oldest-first traversal change. The E2E-only runtime holds the existing `starting · 0%` state with cancellable progress callbacks; it adds no browser fixed sleep or route interception.
- Deviations/residual risks: the user explicitly approved one visual baseline update, `web/e2e/qa.spec.ts-snapshots/qa-summary-desktop.png`, because the accepted active-summary mutual exclusion now truthfully disables conflicting controls. No other snapshot changed. Polling remains the intentional transport boundary pending SSE/WebSocket; distributed deployment still requires shared coordination and storage.
- Commits:
  - Task 1: `af2409b`
  - Task 2: `94eb558`, `e2ab51b`
  - Task 3: `17e23ad`, `8ab4da8`
  - Task 4: `ce98906`, `628992e`
  - Task 5: `81eb092`
