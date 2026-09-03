---
id: "qa-vertical-slice-09"
title: "Harden QA concurrency and deletion replay"
status: "done"
parallel-safe: false
depends-on: ["qa-vertical-slice-08"]
base-commit: "c0dc5a8251eb31785710dc309dad2dde1324c2f0"
owner: "codex"
---

# Task Packet: Harden QA concurrency and deletion replay

## Goal

Close the whole-branch concurrency review findings without changing a public
contract: make conversation creation atomic with document deletion fences,
reconcile expired final-attempt leases in live workers, and make the destructive
document-removal stage safe to replay after an interruption.

## Non-goals

- No database schema or index migration, public API/DTO, frontend, E2E, visual
  snapshot, RAG/Memory model, authentication, or deployment redesign.
- No distributed lock/queue redesign. The existing SQLite leases and
  process-local runtime lock remain the coordination boundaries.
- No weakening of ordinary document-not-found or cross-user isolation behavior.

## Delivery context

A mandatory whole-branch review after packet 08 found three earlier defects:
conversation creation read a document fence before owning a write transaction;
workers recovered expired leases only at startup, allowing final attempts to
remain active forever; and document removal could be retried from the prior
durable stage after the first irreversible deletion. Two missing regressions also
left the default 50-message page and active-summary deletion-fence behavior
unproved. This packet closes all five findings on the packet 08 integration base.

## Relevant files and current interfaces

- `app/qa_repository.py:41` — `create_conversation()` now owns an immediate
  transaction before reading fences and commits the complete aggregate atomically.
- `app/qa_service.py:95` — the document projection and repository commit share the
  user's runtime lock so a stale ready-document candidate cannot cross deletion.
- `app/qa_job_repository.py:102` and `:552` — each claim pass recovers expired
  jobs in the same immediate transaction before selecting the next queued job.
- `app/qa_deletion.py:91`, `:287`, `:354`, and `:722` — deletion creation can
  atomically restart an exhausted row, claims recover expired rows, and the
  destructive document-removal stage opts into fenced replay.
- `app/document_library.py:113` — `perform_document_delete(...,
  replay_if_missing=False)` preserves ordinary not-found behavior while the
  durable deletion worker can safely replay an already removed target.
- `assistants/pdf_learning_assistant.py:896` — destructive deletion orders RAG,
  safe source unlink, then History removal so absent History certifies completion
  of the earlier in-scope side effects; clear-all follows the same order and
  rejects structured or legacy RAG failures before unlinking any source.
- `tests/test_qa_deletion.py:207`, `:457`, `:535`, and `:658` — real
  two-connection orderings, live final-lease recovery, explicit terminal restart,
  and real-resource/legacy-backup post-delete failure injection.
- `tests/test_document_library_service.py:612` — an injected source-unlink failure
  proves History remains available for retry; clear-all tests inject a later
  source-unlink failure and a RAG-clear failure before any source removal.
- `tests/test_qa_worker.py:266` and `:312` — live summary worker recovery without
  restart or notification, including cancel-requested expiry.
- Existing changes to preserve: packets 01–08 and all IDs, schemas, route/DTO
  shapes, frontend behavior, E2E behavior, and visual baselines.

## Prerequisites

### Packet dependencies

- `qa-vertical-slice-08` is `done`.

### Repository/base state

- Fix base: `c0dc5a8251eb31785710dc309dad2dde1324c2f0`.
- Existing QA/deletion tables, statuses, attempt limit (`3`), lease ownership,
  stored deletion payloads, and process runtime locks remain authoritative.

### External prerequisites

- Repository virtual environment at `D:\python_self_agent\venv`; no external
  service, model credential, frontend dependency, or schema migration.

## Explicit change boundary

### Allowed files

- Modify: `app/qa_repository.py`, `app/qa_service.py`
- Modify: `app/qa_job_repository.py`, `app/qa_deletion.py`
- Modify: `app/document_library.py`, `assistants/pdf_learning_assistant.py`
- Test: `tests/test_qa_repository.py`, `tests/test_qa_service.py`
- Test: `tests/test_qa_job_repository.py`, `tests/test_qa_worker.py`
- Test: `tests/test_qa_deletion.py`, `tests/test_document_library_service.py`
- Test: `tests/api/test_qa_routes.py`
- Document: this packet and `FINAL_INTEGRATION_REVIEW.md`

### Allowed behavior changes

- Serialize conversation scope validation/commit with document deletion,
  reconcile expired work on every claim pass, and permit only the durable
  deletion replay path to no-op an already absent document.
- Treat only queued, running, and retryable failed deletion rows as active fences.
- On an explicit same-owner retry of an exhausted deletion, reuse its durable ID
  and atomically rebuild its old-plus-current payload as one fresh active pass.

### Forbidden changes

- No `app/database.py`, database migration, API schema/DTO, route contract,
  frontend, browser storage, Playwright/E2E, snapshot, dependency, or lockfile edit.
- No production fake, new persistent state, cross-user lookup, broad deletion,
  weakened normal not-found response, or architecture/data-isolation change.
- Do not edit or commit controller-owned `.superpowers/sdd/progress.md`.

## Interface contract

### Consumes

- Existing `qa_deletion_fences` rows and stored deletion payload; existing
  `queued|running|failed|cancel_requested|cancelled|completed` state machines.
- Existing SQLite connection helper, `BEGIN IMMEDIATE`, lease owner/expiry fields,
  runtime `RLock`, and worker polling cadence.

### Produces

- `QaRepository.create_conversation(...)` checks fences and writes the aggregate
  inside one immediate transaction with rollback and reliable close.
- `QaJobRepository.claim_next(...)` and
  `QaDeletionRepository.claim_next_deletion(...)` recover expired rows before
  claiming within one immediate transaction.
- `DocumentLibraryService.perform_document_delete(..., *,
  replay_if_missing=False)` adds an internal opt-in used only by the deletion
  worker; default callers still receive `DocumentNotFoundError`.
- `QaDeletionRepository._create(...)` atomically requeues an exhausted failed row
  under the same owner/type/target/ID, resets stage/attempt/lease/error/finished
  state, and unions its stored payload with conversations/memories discovered now.

### Invariants

- User ownership, stable IDs, validation/error codes, returned models, public
  DTOs, lease fencing and stale-owner rejection are preserved.
- A terminal exhausted/cancelled deletion is not an active fence; a retryable
  failure remains active and hidden.
- Replay operates only from the deletion worker's stored user/target payload;
  unowned or unsafe records still fail preflight.

## Required behavior

- If deletion commits first, a concurrent creation observes the active document
  fence and fails with `QA_DOCUMENT_DELETING`. If creation commits first, deletion
  subsequently snapshots and removes that conversation.
- The service cannot validate a ready document and commit a conversation after
  physical deletion crosses the runtime-lock boundary.
- A live scheduling pass terminalizes an expired final summary attempt and its
  assistant message, cancels an expired cancel-requested attempt, and removes both
  from active discovery without restart or external notification.
- A live deletion worker terminalizes an expired final attempt, releases active
  fence visibility, and continues to reject stale-owner advance/complete/fail.
- A third real processing failure records terminal attempt/error/finished state;
  a later explicit same-owner request reuses the same ID, creates exactly one
  active row, resnapshots late conversations, and can complete. Other users see
  neither that row nor its target.
- A failure after the first physical document removal but before durable stage
  advancement can retry to `completed`; legacy scrub, selection cleanup,
  history/file/RAG removal and fence state converge without a second delete.
- Clear-all performs RAG, then all safe in-scope source unlinks, then History
  removal. A mid-unlink failure retains every path in History for retry; a RAG
  failure leaves every source and History record untouched.
- Recent message paging with no explicit limit returns exactly the newest 50 in
  chronological display order and a cursor that reaches older rows. Active-summary
  discovery under a document fence returns the existing safe API `404`.

## Implementation guidance

Acquire the database write reservation before reading a fence and keep every
conversation insert/read in that transaction. Pair it with the existing reentrant
runtime lock around service projection and commit, because database ordering alone
cannot make the document-history projection atomic with physical deletion.

For both queues, use a private in-connection recovery helper from every claim
pass. Terminalize exhausted or cancelled rows before selecting queued work, while
retaining the same conditional owner/version checks for all subsequent updates.
The existing timed worker wait supplies live lease-expiry observation.

For replay, keep normal deletion strict. Inside the assistant, delete RAG first,
then safely unlink every captured source, and only then remove the History record.
An unlink rejection/error therefore leaves the path metadata for retry; because
RAG deletion is idempotent, repeating it is safe. Only the durable worker passes
`replay_if_missing=True` after it has claimed the stored fence/payload, so absent
History now certifies that RAG/source cleanup already succeeded. Present
unowned/unsafe records still fail. Run legacy backup scrub and selection cleanup
again and checkpoint the existing stage so all effects converge without adding
persistent state.

An exhausted failed row cannot coexist with a second active row under the existing
partial unique index. On explicit retry, reserve the database with `BEGIN
IMMEDIATE`, retain the same owner/type/target/ID and created timestamp, union the
stored payload with the current snapshot, cancel newly affected active work, and
reset the row to `queued/fenced` with clean attempt/lease/error/finished fields.

## Acceptance criteria

- [x] Two real SQLite connections/threads prove both document-fence commit
  orderings with event-based synchronization and no timing sleep.
- [x] Service tests prove the runtime lock covers both the document snapshot and
  the repository commit boundary.
- [x] Repository and live-worker tests prove final-attempt and cancel-requested
  expiry, assistant/job terminal state, active discovery, deletion visibility,
  and stale-owner rejection.
- [x] Failure injection immediately after the first destructive removal retries
  to completed with one physical delete and no remaining History/source/RAG,
  legacy-backup, selection, QA, Memory, or active-fence remnants.
- [x] Source-unlink failure leaves History metadata intact for retry, and a real
  third processing failure can be explicitly restarted under the same ID with a
  refreshed payload, exactly one active row, and cross-user isolation.
- [x] Clear-all retains all History path metadata after a later source-unlink
  failure, retries idempotently, and stops before sources/History when structured
  RAG clear reports failure.
- [x] Default recent paging and API active-summary-under-fence regressions pass.
- [x] Full Python, dependency, diff/scope gates pass and forbidden areas are
  untouched.

## Test and verification commands

The brief named `tests/test_document_library.py`, which does not exist. The exact
nearest existing suite `tests/test_document_library_service.py` was substituted.

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/test_qa_repository.py tests/test_qa_job_repository.py tests/test_qa_worker.py tests/test_qa_deletion.py tests/test_document_library_service.py tests/test_user_mutation_coordination.py tests/test_qa_service.py tests/api/test_qa_routes.py --basetemp=.runtime/pytest-qa-concurrency-focused-takeover
```

Result: PASS — `102 passed in 204.67s`.

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q --basetemp=.runtime/pytest-qa-concurrency-full-takeover
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pip check
git diff --check
```

Result: PASS — `1053 passed, 7 skipped in 1284.35s`; no broken requirements;
diff check exited `0` (line-ending notices only).

TDD replay of the 11 new defect regressions used a detached worktree at the exact
base with only the test patch applied: RED — `11 failed in 17.73s`. The same 11
tests on the implementation: GREEN — `11 passed in 3.39s`. The two minor coverage
regressions separately passed `2 passed in 13.13s`.

The final clear-all audit added one structured-RAG failure regression. Before the
guard it was RED with the selected clear tests at `1 failed, 1 passed in 7.31s`;
after the guard and replay ordering, the clear/out-of-root/notes selection was
GREEN at `5 passed, 33 deselected in 5.83s`.

## Stop conditions

Stop and report `blocked` if correctness requires a schema migration, public
API/DTO change, frontend/browser state, production fake, weakened isolation, or
destructive cleanup outside the stored fence scope. None was encountered.

## Implementation handoff

- Packet: `qa-vertical-slice-09`
- Status: `done`
- Delivered:
  - atomic creation/fence ordering and runtime projection serialization;
  - live expired-lease recovery for summary and deletion workers;
  - replay-safe destructive document removal plus both missing regressions.
- Files changed:
  - `app/qa_repository.py`, `app/qa_service.py` — creation transaction/lock.
  - `app/qa_job_repository.py`, `app/qa_deletion.py` — atomic live recovery.
  - `app/document_library.py` — narrow fenced replay option.
  - `assistants/pdf_learning_assistant.py` — replay-safe single delete and
    clear-all ordering with RAG failure protection.
  - seven Python test files — deterministic repository, worker, replay, paging,
    isolation, clear-all, and API regressions.
- Interfaces added or changed:
  - internal `perform_document_delete(..., replay_if_missing=False)` keyword;
    no public API/DTO change.
- Acceptance evidence:
  - [x] exact base test-only replay: `11 failed` RED.
  - [x] defect regressions: `11 passed`; clear-all selection: `5 passed`;
    expanded focused: `102 passed`.
  - [x] full regression: `1053 passed, 7 skipped`; dependency/diff/scope clean.
- Scope confirmation:
  - changed only allowed files: yes.
  - forbidden areas untouched: yes.
- Deviations:
  - substituted the existing `tests/test_document_library_service.py` for the
    nonexistent `tests/test_document_library.py` named by the brief.
- Residual risks/follow-ups:
  - polling and single-process runtime locking remain intentional; distributed
    deployment still requires shared coordination.
  - independent whole-range re-review: PASS — `19 passed in 37.21s`, with no
    Critical or Important findings. Its sole Minor EOF whitespace finding in
    `docs/superpowers/specs/2026-08-28-qa-reconnect-pagination-design.md` was
    fixed by `31b7fca`; the correct reviewed range passes `git diff --check`.
- Commits:
  - concurrency/lease baseline: `6b0494b55761c39695ba8e2fdae0630f22e5830b`
  - deletion replay audit fixes: `40ec2b124aa02bc50396497a4b435966c7c05738`
  - clear-all replay/RAG guard: `46ae385e3c5aa406bf6cee8675e732366d72f11d`
