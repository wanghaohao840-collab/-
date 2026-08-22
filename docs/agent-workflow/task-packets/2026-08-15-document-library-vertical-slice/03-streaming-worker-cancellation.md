---
id: "document-library-vertical-slice-03"
title: "Stream uploads and cancel worker attempts safely"
status: "ready"
parallel-safe: false
depends-on: ["document-library-vertical-slice-02"]
base-commit: "7d810ec41c7bc468116553a2c6c551c2ceb6830b"
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
- `assistants/pdf_learning_assistant.py:161` — forwards the worker progress callback into `RAGTool`; RAG, history and import-memory writes occur before `load_document()` returns. This file is a verified consumer and remains read-only in this packet.
- `hello_agents/memory/rag/pipeline.py:240` — active JSON `replace_document()` prepares chunks, then removes/upserts/saves without a pre-mutation lifecycle signal.
- `hello_agents/memory/rag/qdrant_pipeline.py:145` — active Qdrant `replace_document()` prepares chunks, then upserts/deletes orphans without a pre-mutation lifecycle signal.
- `hello_agents/memory/rag/prepare.py:57` — ordinary progress is best-effort and catches `Exception`; the internal cancellation control signal must cross this wrapper without changing ordinary callback behavior.
- `tests/test_import_service.py`, `tests/test_import_worker.py`, `tests/test_import_error_sanitization.py`, `tests/ui/test_import_handlers.py` — existing behavior and Gradio compatibility seams.
- `tests/memory/rag/test_import_progress.py` — shared JSON/Qdrant stage-order and callback-failure contract.
- `tests/assistants/test_import_idempotency.py` — Assistant-to-RAG progress forwarding and retry/idempotency seam.
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
- Modify: `hello_agents/memory/rag/pipeline.py`
- Modify: `hello_agents/memory/rag/qdrant_pipeline.py`
- Modify: `tests/test_import_service.py`
- Modify: `tests/test_import_worker.py`
- Modify: `tests/test_import_error_sanitization.py`
- Modify: `tests/memory/rag/test_import_progress.py`
- Modify: `tests/assistants/test_import_idempotency.py`
- Modify: this packet for handoff.

### Allowed behavior changes

- Add typed stream inputs, actual-byte limits, `.partial` staging, cancellation service call, worker checks, cancelled staging reconciliation, and one lifecycle-only pre-mutation `committing` signal in each active RAG backend.

### Forbidden changes

- Do not edit Packet 02 files, Assistant/RAGTool/prepare/session/bootstrap/API/frontend/design/dependencies.
- Do not change RAG document data, storage formats, backend selection, graph/history/Memory/report behavior, or ordinary best-effort progress exception handling.
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
- JSON and Qdrant `replace_document(..., progress_callback=...)` emit `("committing", 0, 1, "committing")` exactly once after all parsing/chunk preparation/embedding and before their first delete, upsert, orphan cleanup or cache write.

### Invariants

- Database batch is created only after every file is durably staged; failure leaves no batch/partial file.
- Gradio path input delegates to the same core and still works.
- Cancellation before commit leaves no temporary/formal/staged file and is never retried.
- A rejected commit gate causes zero RAG/history/Memory mutation; once the gate wins, later cancellation is rejected and the existing commit/compensation behavior completes normally.
- Cancelled cleanup failure leaves durable status cancelled and is retried only by exact startup reconciliation.

## Required behavior

- Count actual stream bytes in fixed chunks; enforce 20/100MiB/500MiB even when metadata lies.
- Generate all path components server-side; basename only supplies safe display name/suffix.
- queued/retry_wait cancel cleans staging after durable transition; running cancel wakes pool and worker checks at every stage/progress plus commit boundary.
- Keep ordinary progress best-effort. Define the runner-internal `ImportCancelled` as a direct `BaseException` subclass so it crosses current `report_progress()` and RAGTool `except Exception` sanitizers; catch it explicitly before the runner's generic `Exception` failure/retry path.
- At non-committing progress, check `is_cancel_requested()` before recording progress and raise `ImportCancelled` when requested.
- At the producer's `committing` signal, call `try_begin_committing()` instead of `update_progress()`. False raises `ImportCancelled` before the active backend's first mutation; true makes later cancellation non-cancellable.
- JSON and Qdrant document replacement must emit the committing signal after successful preparation and empty-document validation, but immediately before the first mutation. Do not emit it for validation/preparation failures that make no write.
- Redact path/credential/error details in persisted and returned summaries.

## Implementation guidance

Follow the revised Task 3 in the plan. Use `.partial`, flush/fsync and `os.replace`. Use `ExitStack` only for service-owned path streams; never close FastAPI-owned streams unexpectedly. Extend existing terminal reconciliation rather than adding a second sweeper. Catch `ImportCancelled` before generic failure classification, otherwise it could enter retry_wait. Preserve the current best-effort handling for ordinary progress callback exceptions; only the runner-owned `BaseException` control signal may abort. In the JSON backend, move the existing document removal behind the committing signal. In Qdrant, signal before `_upsert_chunks`; orphan deletion remains after the winning gate.

## Acceptance criteria

- [ ] Path and stream inputs produce identical durable tasks and limits.
- [ ] Actual byte overages, read errors and DB failures leave no partial batch.
- [ ] queued/retry_wait/running cancellation follows the exact state/file rules.
- [ ] All non-committing stages, race boundary, cleanup failure and startup cleanup are tested.
- [ ] JSON and Qdrant emit one pre-mutation committing signal; aborting it leaves prior document contents/cache unchanged, while ordinary `Exception` callbacks remain best-effort.
- [ ] A real Assistant/RAG forwarding test proves the runner control signal is not converted into a sanitized RAG failure.
- [ ] Existing retries, Gradio handlers, user serialization and sanitization remain passing.

## Test and verification commands

```powershell
New-Item -ItemType Directory -Force .runtime | Out-Null
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/test_import_service.py tests/test_import_worker.py tests/test_import_error_sanitization.py tests/ui/test_import_handlers.py tests/memory/rag/test_import_progress.py tests/assistants/test_import_idempotency.py --basetemp=.runtime/pytest-import-stream-cancel
git diff --check
```

Expected: all selected tests PASS and no unhandled worker thread/error output.

## Stop conditions

Stop on any standard reality conflict, incomplete Packet 02, inability to emit the pre-mutation boundary in both owned active backends, a control signal swallowed or reclassified before the runner, cleanup requiring unsafe path operations, or overlap with another worker.

## Implementation handoff

Replace with the template handoff, including exact stream-limit/cancel/cleanup test counts, Gradio regression, changed interfaces, scope confirmation, deviations/risks and commit.

## Reality-conflict report

- Packet: `document-library-vertical-slice-03`
- Status: blocked
- Expected by packet:
  - The worker progress callback can call `try_begin_committing()` at a boundary before any Assistant commit, while cancellation remains observable during parsing, chunking, embedding and persisting.
- Observed in repository:
  - `app/import_worker.py:145` calls `assistant.load_document(...)`; the current worker does not enter `committing` until that call has returned at `app/import_worker.py:154-156`.
  - `assistants/pdf_learning_assistant.py:250-313` performs RAG, history and import-memory writes inside `load_document()`.
  - The production RAG paths report parsing/chunking/embedding/persisting progress, but no `committing` progress event exists; examples are `hello_agents/memory/rag/pipeline.py:161-224`, `hello_agents/memory/rag/qdrant_pipeline.py:118-192` and `hello_agents/tools/builtin/rag_tool.py:921-936`.
- Impact:
  - Moving the gate before `load_document()` would reject cancellation throughout all Assistant stages, violating this packet's required running-stage cooperation. Keeping the gate after `load_document()` allows Assistant persistence before arbitration, violating the pre-commit guarantee. The missing producer-side boundary cannot be supplied from the packet's allowed files.
- Work completed before pause:
  - No production or test files changed. Verified HEAD `7d810ec` and a clean starting worktree.
  - Baseline: `D:\python_self_agent\venv\Scripts\python.exe -m pytest -q tests/test_import_service.py tests/test_import_worker.py tests/test_import_error_sanitization.py tests/ui/test_import_handlers.py --basetemp=.runtime/pytest-import-task3-baseline` — `68 passed in 58.56s`.
- Recommended resolution:
  - Revise the packet and add a prerequisite/owned producer change that emits a `committing` progress event immediately before the first irreversible Assistant/RAG/history/memory write, then keep Task 3's worker gate on that event. If only task terminal-state arbitration was intended, explicitly narrow the acceptance criterion instead.
- Decision required:
  - May the Task 3 boundary be expanded to the producer(s) required for a real pre-Assistant-commit event, or should the accepted cancellation guarantee be narrowed to task terminal-state commit only?

## Reality-conflict resolution

- Decision: boundary expanded; the accepted cancellation guarantee is not narrowed.
- Scope amendment: Packet 03 now owns the JSON and Qdrant `replace_document()` pre-mutation lifecycle seam plus their focused progress tests. Assistant, RAGTool and shared best-effort progress helpers remain unchanged.
- Required contract: emit `committing` after preparation and immediately before first mutation. The runner uses a direct `BaseException` control signal so cancellation and a rejected commit gate propagate through existing `except Exception` wrappers and are caught before generic retry classification.
- Safety boundary: no RAG data shape, persistence format, backend-selection, graph, history, Memory, report, or public Assistant behavior may change.
- Ready-gate evidence: Packet 02 is reviewed complete at `7d810ec`; the expanded owned-file list is exhaustive; JSON/Qdrant no-mutation-on-abort and Assistant forwarding have explicit tests; the combined verification command is runnable in the mandated venv.
- Status: `ready` for the same Task 03 worker to resume from the clean production/test base while preserving this adjudication diff.
