---
id: "document-library-vertical-slice-02"
title: "Add durable import cancellation and commit arbitration"
status: "done"
parallel-safe: true
depends-on: []
base-commit: "f90883e71d2fa73a7cb981b11478b68519d8ce80"
owner: "task2-implementer"
---

# Task Packet: Add durable import cancellation and commit arbitration

## Goal

Make cancellation a durable SQLite state and provide one atomic repository gate that deterministically decides whether cancel or committing wins.

## Non-goals

- Do not stream uploads, modify worker execution, expose HTTP routes, or build UI.
- Do not change retry delays, worker count, user serialization, task IDs or existing status meanings.
- Do not alter Gradio, RAG, Memory, auth or deployment code.

## Delivery context

The current table CHECK excludes `cancelled`; the repository owns every transition. Running cancellation must never race into a half commit. This packet adds persistence and repository semantics only; Packet 03 consumes them to stop work and clean files.

## Relevant files and current interfaces

- `app/database.py:36-69` — table CHECK and three indexes; `initialize_database():96` runs before worker start.
- `app/import_models.py:7-66` — status/stage/task/batch dataclasses without cancellation fields.
- `app/import_repository.py:31` — repository; `claim_next():111`, `update_progress():168`, `mark_succeeded():204`, `retry_task():258`.
- `tests/test_import_models.py`, `tests/test_import_repository.py` — current transition, recovery, index and user-scope coverage; 54 import baseline tests passed at base.
- Existing changes to preserve: uncommitted feature plan/review/packets; no production overlap.

## Prerequisites

### Packet dependencies

- none.

### Repository/base state

- Base commit: `f90883e71d2fa73a7cb981b11478b68519d8ce80`.
- Current import schema and repository signatures must match the lines above.

### External prerequisites

- `D:\python_self_agent\venv\Scripts\python.exe`; no service or network.

## Explicit change boundary

### Allowed files

- Modify: `app/database.py`
- Modify: `app/import_models.py`
- Modify: `app/import_repository.py`
- Modify: `tests/test_import_models.py`
- Modify: `tests/test_import_repository.py`
- Modify: this packet for handoff.

### Allowed behavior changes

- Add `cancelled`, `cancel_requested_at`, cancellation counts/decision types and repository transitions.
- Upgrade existing databases idempotently before worker startup.

### Forbidden changes

- Do not edit service/worker/storage/API/frontend/design/dependencies.
- Do not drop or rewrite users, batches or task data outside the exact migration.
- Do not loosen UUID/user/batch scoping, foreign keys or the one-running-task-per-user index.
- Do not stage unrelated artifacts.

## Interface contract

### Consumes

- `ImportTaskRepository` current constructor and task/batch row mapping.
- Existing active statuses `queued`, `running`, `retry_wait` and terminal statuses `succeeded`, `failed`.

### Produces

- `ImportStatus` and `ImportStage` include `cancelled`.
- `ImportTaskRecord.cancel_requested_at: str | None`.
- `ImportBatchSummary.cancelled: int`.
- `ImportCancelDecision(task: ImportTaskRecord, outcome: Literal['cancelled','cancel_requested','not_cancellable','unchanged'])`.
- `request_cancel(user_id, batch_id, task_id, now=None) -> ImportCancelDecision`.
- `is_cancel_requested(user_id, task_id) -> bool`.
- `try_begin_committing(user_id, task_id, now=None) -> bool`.
- `mark_cancelled(user_id, task_id, now=None) -> ImportTaskRecord`.

### Invariants

- Cancel and begin-commit use write transactions; exactly one wins.
- Cancelled tasks are never claimed or retried and survive restart as cancelled.
- Existing data, counters, timestamps, indexes and foreign keys survive migration.
- Other-user resources are indistinguishable from missing.

## Required behavior

- queued/retry_wait cancellation immediately becomes cancelled; running sets `cancel_requested_at`; committing returns not-cancellable; all terminal states return unchanged.
- `try_begin_committing()` conditionally sets stage only when running and no cancel request exists.
- `mark_cancelled()` only accepts running with a cancellation request; invalid transitions fail without mutation.
- Batch aggregation includes cancelled count and row mapping includes timestamp.
- Migration rebuild is idempotent and all-or-nothing; a pre-change failed task remains byte-for-byte equivalent in represented fields.

## Implementation guidance

Follow Task 2 in the plan. Use `BEGIN IMMEDIATE`/the repository transaction pattern; do not implement check-then-update in separate connections. Rebuild `import_tasks` because SQLite cannot alter the CHECK. Create the new table, copy explicit columns, swap and recreate indexes in one transaction before services start. Tests must include two real connections racing cancel vs commit.

## Acceptance criteria

- [ ] New database accepts cancelled and exposes cancel_requested_at.
- [ ] Pre-change database migrates without task or index loss; second initialize is a no-op.
- [ ] Every legal/illegal transition and terminal idempotence is tested.
- [ ] Concurrent cancel vs commit yields one legal winner and no half state.
- [ ] Cancelled tasks are not claimable/retryable and remain user-scoped.
- [ ] Full model/repository tests and diff check pass.

## Test and verification commands

```powershell
New-Item -ItemType Directory -Force .runtime | Out-Null
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/test_import_models.py tests/test_import_repository.py --basetemp=.runtime/pytest-cancel-repository
git diff --check
```

Expected: all selected tests PASS, no warnings introduced, diff check PASS.

## Stop conditions

Stop and report `blocked` on any repository reality conflict, migration requiring files outside this packet, failing base transition tests, missing Task 2 interfaces, or overlapping production edits.

## Implementation handoff

- Status: done
- Files changed:
  - `app/database.py`
  - `app/import_models.py`
  - `app/import_repository.py`
  - `tests/test_import_models.py`
  - `tests/test_import_repository.py`
- Acceptance criteria:
  - [x] New databases accept `cancelled` and expose `cancel_requested_at`; model and row-mapping contracts include cancellation fields and decisions.
  - [x] A literal `f90883e` pre-cancellation table fixture upgrades twice without changing represented task values, losing the three indexes, or breaking its foreign key.
  - [x] Queued/retry-wait cancellation, running cancellation requests, commit arbitration, terminal idempotence, invalid transitions, retry/claim exclusion, and user/batch/task mismatches are covered.
  - [x] Two repository instances using real SQLite connections race cancel against begin-commit and produce exactly one legal winner with no half state.
  - [x] Scope remained limited to the five owned implementation/test files plus this packet; no worker, service, storage, API, frontend, design, or dependency code changed.
- Verification:
  - `& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/test_import_models.py tests/test_import_repository.py -k "cancel or upgrade or commit" --basetemp=.runtime/pytest-cancel-repository-red` — expected RED (`11 failed, 18 deselected`).
  - `& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/test_import_models.py tests/test_import_repository.py --basetemp=.runtime/pytest-cancel-repository-green` — PASS (`29 passed`).
  - `& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/ui/test_import_handlers.py -k "task_table" --basetemp=.runtime/pytest-cancel-model-compat-focused` — PASS (`2 passed, 28 deselected`).
  - `git diff --check` — PASS.
- Deviations:
  - `ImportTaskRecord.cancel_requested_at` and `ImportBatchSummary.cancelled` have backward-compatible defaults so existing named test/UI constructors remain valid; their produced repository values are always explicit.
- Residual risks:
  - None within this persistence/repository packet. Worker cooperation and file cleanup remain intentionally assigned to Packet 03.
- Commit:
  - `dfaa55d`
