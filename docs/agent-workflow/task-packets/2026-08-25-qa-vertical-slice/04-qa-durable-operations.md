---
id: "qa-vertical-slice-04"
title: "Add durable QA operations"
status: "ready"
parallel-safe: false
depends-on: ["qa-vertical-slice-03"]
base-commit: "6b1548972cc3819d45c89edf0939931d80c4d362"
owner: "unassigned"
---

# Task Packet: Add durable QA operations

## Goal

Add restart-safe summary, rolling-context, Memory-sync and deletion operations; migrate legacy flat questions once; and move Gradio/report consumers to the same QA domain while preserving one-release direct-Python compatibility.

## Non-goals

- No REST route or React UI.
- No generic event bus/distributed queue and no deletion of deprecated summary APIs.
- No broad Memory metadata purge or unrelated history cleanup.

## Delivery context

Long-running summaries and destructive cleanup cannot be process-local. Durable rows, leases, deterministic Memory IDs and deletion fences make work retryable without duplicating answers or resurrecting deleted data. Legacy history is imported per user as truthful single-turn conversations but is no longer authoritative.

## Relevant files and current interfaces

- `app/import_worker.py:337,364,501` — bounded worker lifecycle/wake/stop precedent.
- `app/runtime.py:42,129-135` — background runtime lease contract.
- `app/document_library.py:36,68` and `app/coordination.py:106` — current synchronous coordinated document delete.
- `hello_agents/memory/types/episodic.py:165-184` — failure-atomic exact episode/vector cleanup internal seam.
- `ui/gradio_app.py:714,754-758` — authenticated QA handlers currently read assistant/RAG shared state.
- `assistants/pdf_learning_assistant.py:473-507` and `app/summary_tasks.py:26` — compatibility surface that remains for one release.
- `ui/gradio_app.py:938` and `assistants/pdf_learning_assistant.py:701` — report callers to switch to repository projections.
- Existing changes to preserve: packets 01–03.

## Prerequisites

### Packet dependencies

- `qa-vertical-slice-03` must be `done`.

### Repository/base state

- Base commit plus prior packet handoffs/commits.
- Packet 02 repositories and packet 03 `QaService`/answer/context contracts exist.

### External prerequisites

- none; workers use existing local runtime and SQLite.

## Explicit change boundary

### Allowed files

- Modify: `app/database.py`, `app/qa_repository.py`, `app/qa_job_repository.py`, `app/qa_service.py`, `app/qa_context.py`
- Create: `app/qa_worker.py`, `app/qa_memory.py`, `app/qa_deletion.py`, `app/qa_migration.py`
- Modify: `app/document_library.py`, `app/runtime.py`, `app/reports.py`, `ui/gradio_app.py`, `assistants/pdf_learning_assistant.py`
- Modify: `hello_agents/memory/manager.py`, `hello_agents/memory/types/episodic.py`
- Create/Test: `tests/test_qa_worker.py`, `tests/test_qa_memory.py`, `tests/test_qa_deletion.py`, `tests/test_qa_migration.py`
- Test: `tests/test_qa_job_repository.py`, `tests/test_qa_repository.py`, `tests/test_qa_service.py`, `tests/test_qa_context.py`, `tests/test_document_library_service.py`, `tests/test_user_mutation_coordination.py`, `tests/memory/test_episodic_vector_cleanup.py`, `tests/ui/test_authenticated_handlers.py`, `tests/ui/test_summary_polling.py`, `tests/test_report_service.py`, `tests/test_legacy_migration.py`, `tests/test_legacy_migration_recovery.py`

### Allowed behavior changes

- Product summary/deletion becomes durable; list APIs hide fenced documents/conversations immediately; Gradio/report reads QA from SQLite.

### Forbidden changes

- Do not delete/extend `app/summary_tasks.py` or remove public assistant summary methods.
- Do not clear unrelated memories, notes, users or documents; never select deletion targets without user scope.
- Do not retain deleted content in legacy rollback copies.
- Do not add distributed infrastructure or environment-selectable fake engines.

## Interface contract

### Consumes

- Packet 02 repository/job contracts; packet 03 answer/context/service contracts; runtime background leases; current coordinated document-delete implementation.

### Produces

- `QaWorkerPool` with summary and Memory-sync wake/start/stop/reclaim.
- `QaService.start_summary/get_job/cancel_job` and nonblocking rolling-summary refresh.
- Public exact QA Memory removal by deterministic ID; `QaMemoryLinker` with leased claims.
- `QaDeletionService`/worker with user-scoped conversation/document fences and idempotent stages.
- `QaLegacyMigrationService.ensure_user_migrated()` and repository report projections.
- Gradio handlers using `QaService` only for product QA/summary paths.

### Invariants

- Active job uniqueness and lease ownership arbitrate all terminal writes; expired work is reclaimable, stale owners cannot mutate.
- Memory ID is `qa-<uuid5(user_id:assistant_message_id)>`; claim status is pending/running/completed/failed/not_required with owner/expiry.
- Deletion fence is visible before model/Memory/RAG/file cleanup; late answer/attach writes lose and are removed.
- Document deletion removes affected QA rows, exact QA Memory, RAG/vector/graph/file/history/legacy entries, but not unrelated data.
- Legacy migration is per-user, digest/version idempotent, one source record → one single-turn conversation, no grouping or context injection.

## Required behavior

- Summary enqueue creates turn+job atomically; progress, cancel, retry cap and restart reclaim are observable.
- Rolling summary is best-effort derived state and never blocks an ordinary answer.
- Two Memory workers cannot own one live claim; expired claim is reclaimed once; stale owner cannot attach/fail.
- Conversation/document deletion is staged and idempotent across restarts; lists hide active fences immediately.
- New Gradio/report activity causes no JSON question dual writes. Privacy deletion scrubs affected legacy source/rollback copies.

## Implementation guidance

Follow `ImportWorkerPool` lifecycle. Keep model/RAG/Memory/file operations outside DB transactions; use short conditional transitions. On document deletion, capture only opaque deterministic cleanup IDs in the fence, acquire background runtime, run existing safe preflight/delete, and release in `finally`. Keep deprecated in-memory methods unchanged and add a test that product paths never call them.

## Acceptance criteria

- [ ] Summary jobs survive restart, reclaim expired leases, cancel safely and do not duplicate messages.
- [ ] Memory sync is deterministic, leased, retryable and safe against deletion/lease races.
- [ ] Scoped deletion removes exactly related QA/Memory/RAG/file/history/legacy state and never resurrects late work.
- [ ] Migration reruns are idempotent; Gradio/report use SQLite and produce no new flat question writes.
- [ ] Deprecated direct-Python summary compatibility remains passing and explicitly unused by product handlers.

## Test and verification commands

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/test_qa_job_repository.py tests/test_qa_worker.py tests/test_qa_memory.py tests/test_qa_deletion.py tests/test_qa_migration.py tests/test_qa_repository.py tests/test_qa_service.py tests/test_qa_context.py tests/test_document_library_service.py tests/test_user_mutation_coordination.py tests/memory/test_episodic_vector_cleanup.py tests/ui/test_authenticated_handlers.py tests/ui/test_summary_polling.py tests/test_report_service.py tests/test_legacy_migration.py tests/test_legacy_migration_recovery.py --basetemp=.runtime/pytest-qa-operations
git diff --check
```

Expected: all selected tests and diff check PASS.

## Stop conditions

Stop with a reality-conflict report if a dependency is incomplete, exact deletion cannot be achieved without broad clearing, current callers require an unapproved compatibility break, or any edit outside allowed files is needed.

## Implementation handoff

Replace this section with the workflow-required packet ID/status, delivered operations, changed files/interfaces, acceptance/verification evidence, scope confirmation, deviations, residual risks and commit.
