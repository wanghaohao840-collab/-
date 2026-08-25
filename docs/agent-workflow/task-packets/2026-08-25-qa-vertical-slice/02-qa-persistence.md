---
id: "qa-vertical-slice-02"
title: "Add isolated QA persistence"
status: "ready"
parallel-safe: false
depends-on: ["qa-vertical-slice-01"]
base-commit: "6b1548972cc3819d45c89edf0939931d80c4d362"
owner: "unassigned"
---

# Task Packet: Add isolated QA persistence

## Goal

Add idempotent user-scoped SQLite records and repositories for conversations, fixed document snapshots, messages, immutable sources and durable jobs, including atomic idempotency, cursor pagination, conditional transitions and lease arbitration.

## Non-goals

- No model/RAG calls, workers, HTTP routes, legacy migration or UI.
- No generic ORM or replacement of existing SQLite helpers.
- No cross-user administration API.

## Delivery context

This is the durable foundation for synchronous answers, summaries, Memory sync and deletion. Each conversation captures an ordered immutable document snapshot. Full messages persist; rolling summaries are derived state. User isolation belongs in every query and foreign key, not only callers.

## Relevant files and current interfaces

- `app/database.py:97` — `initialize_database(db_path)` owns additive idempotent schema initialization.
- `app/database.py` — existing `connect()`/transaction helpers and `users(id)` ownership conventions are authoritative.
- `app/import_repository.py:241` — current `claim_next()` shows SQLite lease/arbitration conventions.
- `tests/test_p0_data_integrity.py` — data-isolation and failure-atomicity regression boundary.
- Existing changes to preserve: completed packet 01 artifacts only.

## Prerequisites

### Packet dependencies

- `qa-vertical-slice-01` must be `done`.

### Repository/base state

- Base commit plus packet 01 handoff/commit.
- UUIDs are persisted as strings and all domain timestamps are UTC-compatible.

### External prerequisites

- none.

## Explicit change boundary

### Allowed files

- Modify: `app/database.py`
- Create: `app/qa_models.py`
- Create: `app/qa_repository.py`
- Create: `app/qa_job_repository.py`
- Create/Test: `tests/test_qa_models.py`
- Create/Test: `tests/test_qa_repository.py`
- Create/Test: `tests/test_qa_job_repository.py`
- Test: `tests/test_p0_data_integrity.py`

### Allowed behavior changes

- Add QA schema and repository/model APIs only.

### Forbidden changes

- No current table rewrites or destructive migrations.
- No application/service/API/UI/Memory/RAG edits.
- No unscoped `get/update/delete` by ID; every operation requires `user_id` except worker claims that return owner-scoped records.
- No plaintext paths, secrets, prompts or raw errors in jobs/deletion metadata.

## Interface contract

### Consumes

- `initialize_database`, `connect`, transaction helpers and `users(id)`.

### Produces

- Immutable records/enums/cursors: `QaConversation`, `QaConversationDocument`, `QaMessage`, `QaSource`, `QaConversationAggregate`, pages, `PendingTurn`, `QaJob`, enqueue/claim results and typed domain errors.
- `QaRepository`: create/list/get conversations and messages; `create_pending_turn`; conditional complete/fail/cancel/retry/recovery; source persistence; hard delete.
- `QaJobRepository`: atomic summary turn+job enqueue, conditional lease claim/heartbeat/terminal transitions, cancellation and user-scoped get.

### Invariants

- `unique(user_id,id)` parent keys and composite FKs preserve ownership.
- Only user messages carry nullable `client_request_id`; a partial unique index on `(user_id, conversation_id, client_request_id)` makes duplicate asks return the same turn/job. Assistant linkage uses `turn_id`.
- One pending synchronous assistant and one active job per conversation are enforced by partial indexes.
- Sources are copied immutable snapshots in deterministic order.
- Only the current version/lease owner may win transitions; stale owners return false.

## Required behavior

- Conversation creation validates 1–10 owned, ready, distinct documents and preserves request order.
- Compare mode requires at least two documents; fixed scope never silently changes when the library changes.
- Cursor pagination is stable under equal timestamps using an ID tiebreaker.
- `recover_interrupted_questions()` fails orphaned sync pending rows but excludes assistant messages with queued/running jobs.
- Job leases reclaim expired work once and never mutate terminal state.

## Implementation guidance

Use additive `create table/index if not exists`. Keep write transactions short with `begin immediate` for deduplication/claims. Create user+assistant rows in one transaction. Store no rendered prompt. Return immutable copies rather than live row objects. Prove initializer reruns preserve data.

## Acceptance criteria

- [ ] QA tables/indexes initialize twice without error or data loss.
- [ ] Cross-user list/get/update/delete and FK substitution cannot expose or mutate data.
- [ ] Duplicate client request creates exactly one user/assistant pair and, for summary, one job.
- [ ] Complete/fail/cancel/version and lease races have one winner; stale writers are rejected.
- [ ] Startup recovery preserves active summary messages and fails only orphaned synchronous pending work.

## Test and verification commands

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/test_qa_models.py tests/test_qa_repository.py tests/test_qa_job_repository.py tests/test_p0_data_integrity.py --basetemp=.runtime/pytest-qa-persistence
git diff --check
```

Expected: all selected tests and diff check PASS.

## Stop conditions

Stop and append a reality-conflict report if packet 01 is not done, existing schema helpers differ, a required interface needs files outside the boundary, or a migration would rewrite/drop current data.

## Implementation handoff

Replace this section with the required packet ID/status, delivered result, files/interfaces, acceptance evidence, exact command outcomes, scope confirmation, deviations, residual risks and commit hash.
