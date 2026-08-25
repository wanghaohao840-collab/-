---
id: "qa-vertical-slice-05"
title: "Expose QA API and lifecycle"
status: "ready"
parallel-safe: false
depends-on: ["qa-vertical-slice-04"]
base-commit: "6b1548972cc3819d45c89edf0939931d80c4d362"
owner: "unassigned"
---

# Task Packet: Expose QA API and lifecycle

## Goal

Compose QA services/workers exactly once and expose an authenticated, user-scoped REST API for conversations, messages, summaries, retries, deletion and capability rollback, with stable safe DTO/error contracts.

## Non-goals

- No React implementation, SSE/WebSocket transport or alternate process manager.
- No test engine selectable through production environment/API.
- No route flag that disables durable recovery.

## Delivery context

React and Gradio must share one service domain. REST status resources provide reconnect/reload recovery now and can feed SSE later without changing persistence. `ApplicationServices` remains the single composition/lifecycle owner, including recovery ordering.

## Relevant files and current interfaces

- `app/bootstrap.py:24,40,82-89` — `ApplicationServices.create/start/stop` is the composition root and lifecycle owner.
- `api/app.py:82-84` — current router registration point.
- `api/dependencies.py:30` — current session authentication dependency.
- `api/errors.py:174` — common exception-handler registration/envelope seam.
- `api/routes/documents.py:29,46` — current authenticated document list/delete shape; delete changes to durable `202`.
- `tests/api/test_document_routes.py:70,86-89` — service fake and lifecycle compatibility constraints.
- Existing changes to preserve: packets 01–04.

## Prerequisites

### Packet dependencies

- `qa-vertical-slice-04` must be `done`.

### Repository/base state

- Base commit plus prior packet handoffs/commits.
- Durable QA services/workers/migration/deletion interfaces from packet 04 exist.

### External prerequisites

- none.

## Explicit change boundary

### Allowed files

- Modify: `app/bootstrap.py`
- Modify: `api/app.py`, `api/dependencies.py`, `api/errors.py`
- Modify: `api/routes/documents.py`, `api/schemas/documents.py`
- Create: `api/routes/qa.py`, `api/schemas/qa.py`
- Create/Test: `tests/api/test_qa_routes.py`
- Test: `tests/api/test_document_routes.py`, `tests/api/test_app_lifecycle.py`, `tests/api/test_frontend_mount.py`, `tests/test_app_bootstrap.py`

### Allowed behavior changes

- Add `/api/v1/qa` resources; document deletion returns durable acceptance; common safe errors may include optional `trace_id`.

### Forbidden changes

- No frontend, RAG, Memory, repository schema or worker implementation edits.
- No DTO field containing `user_id`, path, prompt, raw exception, lease owner or Memory ID.
- No authentication bypass, client-provided owner ID or API-accessible fake engine.
- `QA_ROUTE_ENABLED=false` must not stop migration/recovery/workers or resume legacy writes.

## Interface contract

### Consumes

- Packet 04 service methods and `ApplicationServices` lifecycle.
- Existing bearer/session authentication dependency and API error registration.

### Produces

- `ApplicationServices.create(data_root=None, *, qa_answer_engine=None)` test seam; constructed QA repositories/services/workers/migration fields.
- Startup order: reclaim expired summary/deletion/Memory leases → fail only orphaned sync pending rows → start workers. Reverse idempotent stop.
- `/api/v1/qa/capabilities`, conversations, messages, ask, retry, summary job status/cancel and deletion endpoints.
- Safe cursor-page/document/message/citation/job/deletion DTOs and common optional `trace_id`.

### Invariants

- Every resource access derives owner from authenticated session and maps foreign IDs to safe not-found.
- `client_request_id` is required for ask/summary/retry and maps to repository idempotency.
- Sync ask returns a completed/failed message resource; summaries/deletions return `202` status resources.
- Error envelope remains backward compatible with nullable `trace_id` and never includes raw exception text.

## Required behavior

- Capability defaults enabled and only explicit case-insensitive `false` disables product QA routes with `503 QA_ROUTE_DISABLED`.
- Disabled route still initializes/reconciles durable state.
- Conversation creation captures ready document snapshots; compare validation/error codes remain safe.
- Document delete response exposes deletion ID/status and affected conversation count; polling is reconnect-safe.

## Implementation guidance

Use narrow Pydantic DTO conversion functions, dependency injection and current exception handlers. Do not leak domain records directly. Inject fake answer engines only through `ApplicationServices.create` in tests. Keep status fields transport-neutral for later SSE.

## Acceptance criteria

- [ ] All QA endpoints require authentication and enforce user isolation/not-found semantics.
- [ ] Ask/summary/retry idempotency and safe error/trace envelopes are exact.
- [ ] Lifecycle recovery ordering preserves active summaries and reclaims expired work.
- [ ] Route flag disables presentation only; document deletion API/compatibility tests reflect durable `202`.

## Test and verification commands

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/test_app_bootstrap.py tests/api --basetemp=.runtime/pytest-qa-api
git diff --check
```

Expected: selected backend/API suite and diff check PASS.

## Stop conditions

Stop with a reality-conflict report if packet 04 interfaces differ, lifecycle requires another owner, safe DTOs need forbidden fields, or files outside the boundary are required.

## Implementation handoff

Replace this section with packet ID/status, delivery, changed files/interfaces, acceptance and exact verification evidence, scope/deviation/risk confirmation and commit.

