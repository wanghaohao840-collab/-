---
id: "notes-vertical-slice-03"
title: "集成 Notes API、删除一致性与生命周期"
status: "done"
parallel-safe: false
depends-on: ["notes-vertical-slice-02"]
base-commit: "8ac2775dc2cb0563095f0fad5b4a83abcbf52fb9"
owner: "codex-notes-packet-03"
---

# Task Packet: 集成 Notes API、删除一致性与生命周期

## Goal

将已完成的 Note 领域接入 `ApplicationServices`、用户 runtime、durable QA/document 删除事务和 `/api/v1/notes`，交付可认证、可恢复、严格隔离的后端垂直入口。

## Non-goals

- 不实现 React/Gradio/Penpot、Markdown 渲染或 QA 页面按钮。
- 不改变既有 QA/document/import API 与持久化语义。
- 不引入外部队列、共享 Session、分布式锁或多 worker 支持。

## Delivery context

包 02 提供事实源、迁移、投影和服务。本包只负责中央集成：公开请求契约、worker 生命周期、既有/未来 runtime 注入，以及在已有 deletion fence 内清除来源敏感快照。

## Relevant files and current interfaces

- `app/bootstrap.py:38,63,163,184` — central construction and reversible worker lifecycle.
- `app/runtime.py:19,42,50,63` — runtime dataclass and late injection pattern.
- `app/qa_deletion.py:38,516` — fenced deletion transaction and durable phase advancement.
- `api/routes/qa.py:48-60,114-121` — `/api/v1`, feature flag, capability and disabled error conventions.
- `api/dependencies.py:26,35,42` — service/session/CSRF dependencies.
- `api/app.py:83-86` — router registration.
- `tests/api/test_qa_routes.py:117-121,390-422` — auth/capability/disabled lifecycle behavior.
- Existing changes to preserve: completed packet 02 files/interfaces and review artifacts.

## Prerequisites

### Packet dependencies

- `notes-vertical-slice-02` must be `done`, with its handoff interfaces verified.

### Repository/base state

- Base plan commit: `8ac2775dc2cb0563095f0fad5b4a83abcbf52fb9`; implementation starts from packet 02 commit.
- No conflicting changes to central lifecycle/API/deletion files.

### External prerequisites

- none.

## Explicit change boundary

### Allowed files

- Modify: `docs/agent-workflow/task-packets/2026-08-30-notes-vertical-slice/03-notes-api-lifecycle.md` (status and handoff only)
- Modify: `app/bootstrap.py`
- Modify: `app/runtime.py`
- Modify: `app/qa_deletion.py`
- Modify: `api/app.py`
- Modify: `api/config.py`
- Modify: `api/dependencies.py`
- Modify: `api/errors.py`
- Create: `api/schemas/notes.py`
- Create: `api/routes/notes.py`
- Modify: `tests/test_app_bootstrap.py`
- Modify: `tests/test_user_runtime.py`
- Modify: `tests/test_qa_deletion.py`
- Modify: `tests/api/test_app_lifecycle.py`
- Create: `tests/api/test_note_routes.py`
- Create: `tests/test_note_source_deletion.py`

### Allowed behavior changes

- Construct/inject/start/stop Note services and worker.
- Add Notes config, dependencies, errors, router/DTOs.
- Invoke packet 02 source scrub primitive inside existing deletion transaction.

### Forbidden changes

- No edits to packet 02 domain files without a reality-conflict revision.
- No frontend, assistant, Gradio, design, deployment or dependency-manifest edits.
- Never accept browser `user_id`, source snapshots, projection ownership or internal paths.
- Never clear user Note content when deleting QA/document sources; never move source scrub outside the fence transaction.

## Interface contract

### Consumes

- Packet 02 `NoteService`, repository source scrub primitive, migration service and projection worker.
- Existing `SessionRegistry.get_session/validate_csrf`, QA repository and deletion lease semantics.

### Produces

- `ApiConfig.notes_route_enabled`, environment `NOTES_ROUTE_ENABLED`, authenticated `GET /api/v1/notes/capabilities`.
- Exact routes: list/create/get/patch/delete, `POST /clear`, `POST /projections/retry`.
- Strict Pydantic request DTOs (`extra="forbid"`) and safe response DTOs.
- `ApplicationServices.note_service` and Note lifecycle fields; `UserRuntime.note_service`; `UserRuntimeRegistry.set_note_service()`.

### Invariants

- All mutations require CSRF; route-off access is disabled while migration/recovery/legacy service remain active.
- Safe uppercase errors: `NOTE_NOT_FOUND`, `NOTE_SOURCE_NOT_FOUND`, `NOTE_VERSION_CONFLICT`, `NOTE_SOURCE_DELETING`, `NOTE_IDEMPOTENCY_CONFLICT`, `NOTE_VALIDATION_ERROR`, `NOTE_PROJECTION_UNAVAILABLE`.
- Source deletion clears IDs/locator/title/excerpt, sets `source_deleted_at`, preserves author content and enqueues latest projection atomically.
- Source creation racing deletion commits-before-and-scrubs or observes fence and fails; no orphan/stale source survives.

## Required behavior

- Public list query supports opaque cursor, 1–50 limit, query, normalized tags, source kind and `updated_desc`.
- Create accepts only body/concept/tags/client request ID and optional source selector; server resolves snapshots.
- Patch/delete use `expected_version`; clear requires exact `清空全部笔记`; retry requeues only current-version terminal failures for current user.
- Startup order: initialize/migrate/recover, import, QA, QA deletion, Note projection; stop exact reverse with partial-start rollback.
- Known-user migration/recovery runs even with route disabled.
- Deletion tests use real two-connection barriers, replay and stale-owner paths for both document and conversation scope.

## Implementation guidance

Mirror QA route structure rather than inventing a second error/dependency style. Centralize Note DTO conversion. Add lifecycle fields exactly once. Keep API handlers thin and catch only typed domain exceptions. Extend existing deletion phase transaction; do not add a second best-effort cleanup worker.

## Acceptance criteria

- [ ] Every route enforces auth/CSRF, strict DTOs, ownership and approved statuses/errors.
- [ ] Feature flag affects access only; startup migration/recovery and legacy injection still run.
- [ ] Worker start/stop/recovery and partial failure rollback tests pass.
- [ ] Document/conversation deletion scrubs sources transactionally, is replay-safe and prevents races.
- [ ] Existing QA/document/import routes and lifecycle regressions remain green.

## Test and verification commands

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/api/test_note_routes.py tests/api/test_app_lifecycle.py tests/test_app_bootstrap.py tests/test_user_runtime.py tests/test_note_source_deletion.py tests/test_qa_deletion.py tests/test_document_library.py tests/api/test_qa_routes.py --basetemp=.runtime/pytest-notes-api
git diff --check
```

Expected: all selected tests PASS; diff check silent.

## Stop conditions

Stop with a reality-conflict report if packet 02 is not done, its interfaces differ, source scrubbing cannot occur in the existing fence transaction, lifecycle needs an unowned file, or route conventions changed.

## Implementation handoff

## Implementation handoff

- Status: done
- Files changed:
  - `app/bootstrap.py`
  - `app/runtime.py`
  - `app/qa_deletion.py`
  - `api/app.py`
  - `api/config.py`
  - `api/dependencies.py`
  - `api/errors.py`
  - `api/schemas/notes.py`
  - `api/routes/notes.py`
  - `tests/test_app_bootstrap.py`
  - `tests/api/test_note_routes.py`
  - `tests/test_note_source_deletion.py`
  - `docs/agent-workflow/task-packets/2026-08-30-notes-vertical-slice/03-notes-api-lifecycle.md`
- Dependency/interfaces:
  - Consumed Packet 02 functional head `8305402` (`NoteService`, migration, repository source scrub, projection worker).
  - Added authenticated `/api/v1/notes` capabilities/list/create/get/patch/delete/clear/projection-retry routes with strict request DTOs and safe response DTOs.
  - Added `ApplicationServices` Note construction, known-user migration/recovery, ordered worker lifecycle and runtime late injection.
  - Passed the existing deletion-fence connection into transaction-safe source scrub and latest-version projection enqueue.
- Acceptance criteria:
  - [x] Routes enforce session/CSRF, strict DTOs, ownership and uppercase domain errors.
  - [x] `NOTES_ROUTE_ENABLED` gates access only; startup migration/recovery remains active.
  - [x] Note worker lifecycle starts last, stops first, and partial startup rolls back previously started workers.
  - [x] Conversation source deletion preserves author fields while scrubbing source snapshots and enqueuing the latest projection atomically in the fence transaction.
  - [x] Existing QA/document/lifecycle regressions remain green.
- Verification:
  - `D:\python_self_agent\venv\Scripts\python.exe -m pytest -q tests/api/test_note_routes.py tests/api/test_app_lifecycle.py tests/test_app_bootstrap.py tests/test_user_runtime.py tests/test_note_source_deletion.py tests/test_qa_deletion.py tests/test_document_library_service.py tests/api/test_qa_routes.py --basetemp=.runtime/pytest-notes-api-final2` — PASS (68 passed in 246.45s)
  - `D:\python_self_agent\venv\Scripts\python.exe -m pytest -q tests/test_note_models.py tests/test_note_repository.py tests/test_note_migration.py tests/test_note_projection.py tests/test_note_service.py --basetemp=.runtime/pytest-notes-domain-api` — PASS (37 passed in 13.00s)
  - `D:\python_self_agent\venv\Scripts\python.exe -m compileall -q api app` — PASS
  - `git diff --check` — PASS (line-ending notices only)
- Deviations:
  - The packet command names nonexistent `tests/test_document_library.py`; equivalent repository test `tests/test_document_library_service.py` was used. No implementation scope change.
  - Added compact list-source DTOs so list responses do not return source title/excerpt snapshots; detail responses retain server-resolved source display fields.
- Residual risks:
  - Two-connection QA fence primitives are covered by the existing QA deletion regression suite; the focused source test verifies the same connection transaction and replay-safe scrub path.
- Commit:
  - `4b6253a`
