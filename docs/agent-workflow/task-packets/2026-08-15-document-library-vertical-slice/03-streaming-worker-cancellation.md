---
id: "document-library-vertical-slice-03"
title: "Stream uploads and cancel worker attempts safely"
status: "ready"
parallel-safe: false
depends-on: ["document-library-vertical-slice-02"]
base-commit: "f90883e71d2fa73a7cb981b11478b68519d8ce80"
owner: "unassigned"
---

# Task Packet: Stream uploads and cancel worker attempts safely

## Goal

Allow path-backed Gradio uploads and FastAPI file streams to share one actual-byte-enforced staging path, and make workers cooperatively end approved cancellation requests without retry or residue.

## Non-goals

- Do not add API routers, document listing/deletion, React UI, Penpot work or dependencies.
- Do not change supported suffixes, limits, retry delays, pool size or user serialization.
- Do not cancel after the Task 02 committing gate wins.

## Delivery context

`ImportTaskService.submit_batch()` currently copies server paths; `UploadFile` is a stream. Task 02 makes cancellation durable but does not stop work or clean files. This packet provides the single staging implementation and runner cooperation while preserving Gradio callers.

## Relevant files and current interfaces

- `app/storage.py:25` — per-user UUID path construction, suffix validation, staged-path/reparse-point guards.
- `app/import_service.py:34` — authenticated service; `submit_batch():51`, list/get/retry and Runtime lock behavior.
- `app/import_worker.py:97` — runner formal/temp file flow and progress callback; `ImportWorkerPool:267` startup recovery/reconciliation.
- `tests/test_import_service.py`, `tests/test_import_worker.py`, `tests/test_import_error_sanitization.py`, `tests/ui/test_import_handlers.py` — existing behavior and Gradio compatibility seams.
- Existing changes to preserve: completed Packet 02 commit plus uncommitted planning artifacts.

## Prerequisites

### Packet dependencies

- `document-library-vertical-slice-02` must be `done`; its repository methods and model fields must match the contract below.

### Repository/base state

- Base ancestor: `f90883e71d2fa73a7cb981b11478b68519d8ce80` plus the Packet 02 commit.
- Required symbols: `request_cancel`, `is_cancel_requested`, `try_begin_committing`, `mark_cancelled`, `ImportCancelDecision`.

### External prerequisites

- Mandated project venv only; no network/service.

## Explicit change boundary

### Allowed files

- Modify: `app/storage.py`
- Modify: `app/import_service.py`
- Modify: `app/import_worker.py`
- Modify: `tests/test_import_service.py`
- Modify: `tests/test_import_worker.py`
- Modify: `tests/test_import_error_sanitization.py`
- Modify: this packet for handoff.

### Allowed behavior changes

- Add typed stream inputs, actual-byte limits, `.partial` staging, cancellation service call, worker checks and cancelled staging reconciliation.

### Forbidden changes

- Do not edit Packet 02 files, Assistant/session/bootstrap/API/frontend/design/dependencies.
- Do not trust client sizes/names for paths, return raw errors, or broaden cleanup beyond the exact validated task/batch path.
- Do not break `submit_batch(session_token, files, progress=None)` or existing Gradio retry semantics.

## Interface contract

### Consumes

- Packet 02 repository API exactly as documented in `02-cancellation-persistence.md`.
- Existing `ImportLimits(20, 100 MiB, 500 MiB)` and `UserStorage` UUID/suffix guards.

### Produces

- `ImportUpload(original_name: str, stream: BinaryIO)`.
- `ImportLimitError(code, message, status_code)` with safe stable code.
- `ImportTaskNotCancellableError`.
- `ImportTaskService.submit_uploads(session_token, uploads, progress=None) -> ImportBatchSummary`.
- `ImportTaskService.cancel_task(session_token, batch_id, task_id) -> ImportBatchSummary`.
- `ImportCancelled` internal runner exception; it never escapes as raw API text.

### Invariants

- Database batch is created only after every file is durably staged; failure leaves no batch/partial file.
- Gradio path input delegates to the same core and still works.
- Cancellation before commit leaves no temporary/formal/staged file and is never retried.
- Cancelled cleanup failure leaves durable status cancelled and is retried only by exact startup reconciliation.

## Required behavior

- Count actual stream bytes in fixed chunks; enforce 20/100MiB/500MiB even when metadata lies.
- Generate all path components server-side; basename only supplies safe display name/suffix.
- queued/retry_wait cancel cleans staging after durable transition; running cancel wakes pool and worker checks at every stage/progress plus commit boundary.
- When `try_begin_committing()` returns false, raise `ImportCancelled` before Assistant commit. When true, later cancellation is rejected and success/failure completes normally.
- Redact path/credential/error details in persisted and returned summaries.

## Implementation guidance

Follow Task 3 in the plan. Use `.partial`, flush/fsync and `os.replace`. Use `ExitStack` only for service-owned path streams; never close FastAPI-owned streams unexpectedly. Extend existing terminal reconciliation rather than adding a second sweeper. Catch cancellation before generic failure classification, otherwise it could enter retry_wait.

## Acceptance criteria

- [ ] Path and stream inputs produce identical durable tasks and limits.
- [ ] Actual byte overages, read errors and DB failures leave no partial batch.
- [ ] queued/retry_wait/running cancellation follows the exact state/file rules.
- [ ] All non-committing stages, race boundary, cleanup failure and startup cleanup are tested.
- [ ] Existing retries, Gradio handlers, user serialization and sanitization remain passing.

## Test and verification commands

```powershell
New-Item -ItemType Directory -Force .runtime | Out-Null
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/test_import_service.py tests/test_import_worker.py tests/test_import_error_sanitization.py tests/ui/test_import_handlers.py --basetemp=.runtime/pytest-import-stream-cancel
git diff --check
```

Expected: all selected tests PASS and no unhandled worker thread/error output.

## Stop conditions

Stop on any standard reality conflict, incomplete Packet 02, a progress contract that cannot establish a pre-commit gate within owned files, cleanup requiring unsafe path operations, or overlap with another worker.

## Implementation handoff

Replace with the template handoff, including exact stream-limit/cancel/cleanup test counts, Gradio regression, changed interfaces, scope confirmation, deviations/risks and commit.
