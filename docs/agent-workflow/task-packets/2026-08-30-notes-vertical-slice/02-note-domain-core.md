---
id: "notes-vertical-slice-02"
title: "交付持久化 Note 领域核心"
status: "done"
parallel-safe: true
depends-on: []
base-commit: "8ac2775dc2cb0563095f0fad5b4a83abcbf52fb9"
owner: "codex-notes-packet-02"
---

# Task Packet: 交付持久化 Note 领域核心

## Goal

交付以 SQLite/FTS5 为唯一事实源的用户隔离 Note 聚合，包括验证、CRUD/搜索、版本/墓碑、幂等旧数据迁移、持久化 Memory 投影任务和统一 `NoteService`，不接 API 或 UI。

## Non-goals

- 不修改 FastAPI、ApplicationServices、QA 删除流程、React、Gradio 或 Penpot。
- 不实现文件夹、可见版本历史、语义搜索 API、多进程调度或分布式锁。
- 不删除或改写旧 `history.json.notes`。

## Delivery context

当前学习笔记由 `PDFLearningAssistant` 同时写随机 Memory 与 JSON，缺少稳定 ID、并发版本、恢复和单一事实源。本包先建立可独立验证的领域层；后续包只消费这些稳定接口。

## Relevant files and current interfaces

- `app/database.py:9,280,289` — central schema/connect/initialize seam.
- `app/runtime.py:42,63` — `UserRuntimeRegistry` resolves UUID-scoped Memory runtimes used by projection.
- `app/qa_repository.py:255,1279` — server-owned QA message/source lookup for trusted source snapshots.
- `assistants/pdf_learning_assistant.py:527-738` — legacy data shapes to migrate; this packet reads them but must not edit the assistant.
- `hello_agents/tools/builtin/memory_tool.py` and `hello_agents/memory/manager.py` — stable-ID add/remove capability; do not broaden Memory APIs.
- `docs/superpowers/specs/2026-08-30-notes-vertical-slice-design.md:73-239` — normative data, migration and projection contract.
- Existing changes to preserve: review/packet artifacts; any concurrently completed packet 01 design changes.

## Prerequisites

### Packet dependencies

- none.

### Repository/base state

- Base commit: `8ac2775dc2cb0563095f0fad5b4a83abcbf52fb9`.
- Project Python: `D:\python_self_agent\venv\Scripts\python.exe`.

### External prerequisites

- none; tests use temporary SQLite/user roots and fake Memory projections.

## Explicit change boundary

### Allowed files

- Modify: `docs/agent-workflow/task-packets/2026-08-30-notes-vertical-slice/02-note-domain-core.md` (status and handoff only)
- Modify: `app/database.py`
- Create: `app/note_models.py`
- Create: `app/note_repository.py`
- Create: `app/note_migration.py`
- Create: `app/note_projection.py`
- Create: `app/note_service.py`
- Create: `tests/test_note_models.py`
- Create: `tests/test_note_repository.py`
- Create: `tests/test_note_migration.py`
- Create: `tests/test_note_projection.py`
- Create: `tests/test_note_service.py`

### Allowed behavior changes

- Add idempotent Note tables/indexes/FTS, immutable domain DTOs/exceptions and the domain services above.
- Read existing user history/Memory under runtime locks for migration/projection only.

### Forbidden changes

- No edits to `app/bootstrap.py`, `app/runtime.py`, `app/qa_deletion.py`, `api/`, `assistants/`, `ui/`, `web/` or design artifacts.
- No public `user_id` acceptance, full-table search fallback, broad Memory delete, JSON Note mutation, random projection IDs or background thread startup at import time.
- Do not alter existing QA/document/import schemas or semantics.

## Interface contract

### Consumes

- `app.database.connect(db_path)` and `initialize_database(db_path)`.
- `UserStorage`, `UserRuntimeRegistry`, `QaRepository.get_message(user_id, message_id)`.
- `MemoryManager.add_memory` with explicit `memory_id`; exact `remove_memory`.

### Produces

- Immutable `Note`, `NoteSource`, `NotePage`, `NoteProjectionTask`, `NoteMigrationResult`, `NoteFilters`, `NoteSourceSelector` and typed Note exceptions.
- `NoteRepository`: create/get/list_page/update/soft_delete/clear_all/retry_failed_projections plus transaction-safe source scrub primitive.
- `NoteMigrationService.ensure_user_migrated(user_id)` and `migrate_known_users()`.
- `NoteProjectionRepository` lease/heartbeat/retry/recovery methods and `NoteProjectionWorker.start/stop/notify/run_once`.
- `NoteService` authenticated and trusted-internal create/list/get/update/delete/clear/search/count/recent/retry entry points.

### Invariants

- Composite user ownership on every row/read/write; safe not-found for deleted/cross-user records.
- Body 1–20,000, concept ≤120, ≤10 normalized tags of ≤32, ≤10 sources.
- Create idempotency detects payload mismatch; updates/deletes require exact current version; tombstones cannot revive.
- FTS5 is required and maintained transactionally; no unbounded fallback scan.
- Stable projection ID `note:{user_id}:{note_id}`; task claims/heartbeats/completions require owner and lease predicates.
- Stable upsert succeeds before exact matched legacy memory cleanup; projection failure never rolls back saved Note data.

## Required behavior

- Schema contains Notes, tags, sources, FTS, projection tasks and legacy ledger with foreign keys/indexes and upgrade idempotency.
- Pagination uses opaque `(updated_at,note_id)` cursor, default 20/max 50, deterministic descending order and tag intersection.
- Duplicate identical legacy JSON Notes are distinguished by canonical payload plus occurrence index and deterministic UUIDv5.
- Migration ledger records exact legacy Memory ID and cleanup time; unmatched memory produces only a safe count warning.
- Worker recovers expired leases, rejects stale owners, bounds retries, reloads latest Note before projection and no-ops stale/tombstoned upserts.
- `NoteService` resolves QA sources server-side and notifies worker only after commit; Memory is not part of save success.

## Implementation guidance

Follow Tasks 2–4 of the source plan in RED→GREEN order. Keep SQL transactions short; use `BEGIN IMMEDIATE` only where claim/write serialization is required. Use parameterized queries and public DTO conversion helpers. Test two users and two connections, not only repository mocks.

## Acceptance criteria

- [x] Fresh and upgraded databases contain valid FTS5 Note schema and constraints.
- [x] CRUD/search/paging/idempotency/version/tombstone/user-isolation tests pass.
- [x] Duplicate legacy rows migrate exactly once without JSON mutation or premature Memory cleanup.
- [x] Projection recovers/retries with stable IDs and stale-owner protection.
- [x] `NoteService` resolves trusted QA sources and remains usable when projection fails.
- [x] Only allowed files changed.

## Test and verification commands

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/test_note_models.py tests/test_note_repository.py tests/test_note_migration.py tests/test_note_projection.py tests/test_note_service.py tests/test_user_runtime.py tests/test_assistant_user_isolation.py --basetemp=.runtime/pytest-notes-domain
git diff --check
```

Expected: all selected tests PASS; diff check silent.

## Stop conditions

Use the standard reality-conflict report and stop if an existing schema/source shape differs, stable Memory IDs are unsupported, a prerequisite requires editing a forbidden file, FTS5 is unavailable, or concurrent packet changes overlap this boundary.

## Implementation handoff

- Status: done
- Files changed:
  - `app/database.py`
  - `app/note_models.py`
  - `app/note_repository.py`
  - `app/note_migration.py`
  - `app/note_projection.py`
  - `app/note_service.py`
  - `tests/test_note_models.py`
  - `tests/test_note_repository.py`
  - `tests/test_note_migration.py`
  - `tests/test_note_projection.py`
  - `tests/test_note_service.py`
  - `docs/agent-workflow/task-packets/2026-08-30-notes-vertical-slice/02-note-domain-core.md`
- Interfaces delivered:
  - Immutable Note DTOs, filters, selectors, cursor helpers and typed errors.
  - `NoteRepository` CRUD/FTS/paging/outbox/retry plus transaction-safe source scrubbing.
  - `NoteMigrationService.ensure_user_migrated()` and `migrate_known_users()`.
  - Lease-owned `NoteProjectionRepository` and lifecycle-safe `NoteProjectionWorker` with stable Memory IDs and exact legacy cleanup.
  - Authenticated and trusted-internal `NoteService` create/list/get/update/delete/clear/search/count/recent/retry entry points.
- Acceptance criteria:
  - [x] Schema/FTS initialization is idempotent and fails explicitly when FTS5 is unavailable.
  - [x] Real SQLite tests cover two users, two connections, optimistic versions, tombstones, idempotency, filters and opaque cursors.
  - [x] Duplicate legacy rows use canonical payload plus occurrence index and deterministic UUIDv5 without mutating JSON.
  - [x] Projection tests cover expiry recovery, stale owners, bounded failure, stable IDs, no resurrection and heartbeat ownership loss.
  - [x] QA source snapshots are resolved from user-scoped server rows; projection notification failure does not fail Note persistence.
  - [x] Staged task diff contains only Packet 02 allowed files; pre-existing progress/review/packets 03–06 remain unstaged and unchanged by this task.
- Verification:
  - `& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/test_note_models.py tests/test_note_repository.py tests/test_note_migration.py tests/test_note_projection.py tests/test_note_service.py tests/test_user_runtime.py tests/test_assistant_user_isolation.py --basetemp=.runtime/pytest-notes-domain` — PASS (`33 passed in 47.70s`)
  - `& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/test_qa_repository.py tests/test_qa_deletion.py --basetemp=.runtime/pytest-notes-qa-regression` — PASS (`20 passed in 15.46s`)
  - `git diff --check` — PASS (exit 0; only Git line-ending notices, no whitespace errors)
- Review:
  - Independent task re-review — Spec PASS; Quality APPROVED; no Critical or Important findings. Its non-blocking heartbeat-path coverage note was subsequently closed by a focused regression test included in the 33-pass command.
- Deviations:
  - None.
- Residual risks:
  - The source-scrub primitive is intentionally not wired into `app/qa_deletion.py` in this packet; Packet 03 owns that integration and the two-connection fence race test.
- Commit:
  - `not committed` (handoff recorded immediately before creating the single Packet 02 task commit; final hash is reported to the controller).
