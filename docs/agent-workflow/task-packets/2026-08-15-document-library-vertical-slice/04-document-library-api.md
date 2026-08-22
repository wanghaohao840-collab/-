---
id: "document-library-vertical-slice-04"
title: "Expose authenticated document-library APIs"
status: "done"
parallel-safe: false
depends-on: ["document-library-vertical-slice-03"]
base-commit: "6d9ed6f19e1f97082a9e85d5b5da448cfa7f6f29"
owner: "completed"
---

# Task Packet: Expose authenticated document-library APIs

## Goal

Add one user-scoped document service and FastAPI document/import routers that expose listing, upload, progress, retry, cancellation and coordinated deletion through the existing Cookie/CSRF/error/lifecycle architecture.

## Non-goals

- Do not build React/Penpot/E2E UI, change import internals, add dependencies or redesign Assistant/RAG behavior.
- Do not add pagination, preview, rename, tags, folders, bulk delete or clear-all API.
- Do not modify `/legacy`, SPA fallback, Accept negotiation or deployment topology.

## Delivery context

Imports already exist behind ApplicationServices after Packets 02–03. Documents are recorded in each user's latest history and formal file root. The API needs safe projections and structured deletion, not string parsing or direct filesystem/RAG calls from route functions.

## Relevant files and current interfaces

- `app/history.py:105-153` — document upsert/delete and latest persisted history shape.
- `assistants/pdf_learning_assistant.py:793-930` — list/current delete and internal coordinated deletion.
- `app/session.py:27` — per-session Assistant map and current-document state; session registry owns active sessions.
- `app/bootstrap.py:23-81` — single service construction/start/stop.
- `api/dependencies.py:11-32` — session registry, Cookie token, current session and CSRF dependencies.
- `api/errors.py` — stable JSON envelope and exception handlers.
- `api/app.py:46-81` — lazy services and auth router; `create_application():127` must retain mounts/fallback.
- `tests/api/*` and `tests/test_app_bootstrap.py` — current factory/lifecycle/auth/mount test patterns.
- Existing changes to preserve: completed Packet 03 chain and planning artifacts.

## Prerequisites

### Packet dependencies

- `document-library-vertical-slice-03` must be `done`.

### Repository/base state

- Base ancestor: `f90883e...` plus Packet 02 and 03 commits.
- `ImportTaskService.submit_uploads()` and `cancel_task()` must match Packet 03.

### External prerequisites

- Mandated project venv; no network/service.

## Explicit change boundary

### Allowed files

- Create: `app/document_library.py`
- Modify: `assistants/pdf_learning_assistant.py`
- Modify: `app/session.py`
- Modify: `app/bootstrap.py`
- Modify: `api/dependencies.py`
- Create: `api/schemas/documents.py`
- Create: `api/schemas/imports.py`
- Create: `api/routes/documents.py`
- Create: `api/routes/imports.py`
- Modify: `api/app.py`
- Create: `tests/test_document_library_service.py`
- Modify: `tests/test_app_bootstrap.py`
- Create: `tests/api/test_document_routes.py`
- Create: `tests/api/test_import_routes.py`
- Modify: this packet for handoff.

### Allowed behavior changes

- Add document projection/deletion service and exact `/api/v1/documents`/`imports` contracts.
- Add one `document_library` field to ApplicationServices and request-state dependency accessors.

### Forbidden changes

- Do not edit import repository/service/worker/storage/schema, frontend/design/E2E/dependencies.
- Do not expose paths, user IDs or raw errors; do not accept caller user_id.
- Do not let route functions manipulate Runtime/history/RAG/files directly.
- Do not create another service lifecycle or worker.

## Interface contract

### Consumes

- Cookie token from `get_session_token(request)`; auth/CSRF dependencies from `api.dependencies`.
- Packet 03 `ImportUpload`, `submit_uploads`, list/get/retry/retry-failed/cancel service methods.
- Existing user Runtime lock, latest HistoryRepository and coordinated deletion.

### Produces

- `DocumentLibraryItem(document_id, name, file_suffix, size_bytes: int|None, loaded_at: str|None, status='ready')`.
- `DocumentLibraryService.list_documents(token) -> tuple[DocumentLibraryItem, ...]` and `delete_document(token, document_id) -> None`.
- Public structured Assistant delete-by-ID; `SessionRegistry.clear_document_selection(user_id, document_id) -> int`.
- GET/DELETE `/api/v1/documents`; POST/GET and nested retry/retry-failed/cancel `/api/v1/imports` as specified.
- API schemas strip `user_id` and `staged_relative_path` and add cancelled/count/timestamp fields.

### Invariants

- All writes require CSRF; GET is read-only.
- Other-user IDs produce the same 404 as missing.
- Latest duplicate document ID wins; invalid history records are skipped and safely logged.
- Delete preflights user-root paths, blocks active exact-document import, removes RAG/history/questions/file, and clears exact selection across that user's sessions only.
- Auth router, mounts, lazy services and one start/stop remain unchanged.

## Required behavior

- List returns sorted safe ready documents with nullable authoritative metadata only.
- Multipart upload returns 202 complete batch; list/get limit 1–50; every nested task validates batch membership.
- Typed failures map to the exact stable codes/statuses in the design spec, using existing envelope.
- Delete returns 204 only after successful coordinated cleanup; failure refetch semantics are represented by non-2xx safe errors.
- Unknown API still returns JSON 404 and never SPA HTML.

## Implementation guidance

Follow Task 4 in the plan. Keep HTTP parsing in routers and domain logic in services. Reuse current dependencies and error handlers. Use Pydantic response models rather than returning import dataclasses directly. Do not parse Assistant display strings; introduce a structured result and keep Gradio formatting in `delete_current_document()`.

## Acceptance criteria

- [ ] Service projection/deletion and same-user multi-session invalidation are tested.
- [ ] Every endpoint has authenticated success, missing auth, missing/forged CSRF where applicable, validation, cross-user and sanitization coverage.
- [ ] Upload is actual multipart and returns 202; all response fields match the accepted schema.
- [ ] ApplicationServices identity/start/stop and `/legacy`/SPA/auth tests remain passing.
- [ ] No allowed API response contains user/path/secret fields.

## Test and verification commands

```powershell
New-Item -ItemType Directory -Force .runtime | Out-Null
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/test_document_library_service.py tests/test_app_bootstrap.py tests/api --basetemp=.runtime/pytest-document-api
git diff --check
```

Expected: all selected tests PASS; no lifecycle/mount/auth regression.

## Stop conditions

Stop on any standard reality conflict, incomplete Packet 03, need to parse user identity from request data, need to bypass coordinated mutation, need to modify import internals or frontend, or overlapping files.

## Implementation handoff

- Status: done; ready for independent re-review after correcting the producer timestamp finding in addition to the prior two Important and one Minor findings
- Files changed:
  - `app/document_library.py`
  - `assistants/pdf_learning_assistant.py`
  - `app/session.py`
  - `app/bootstrap.py`
  - `api/dependencies.py`
  - `api/schemas/documents.py`
  - `api/schemas/imports.py`
  - `api/routes/documents.py`
  - `api/routes/imports.py`
  - `api/app.py`
  - `tests/test_document_library_service.py`
  - `tests/test_app_bootstrap.py`
  - `tests/api/test_document_routes.py`
  - `tests/api/test_import_routes.py`
- Changed interfaces:
  - Added immutable `DocumentLibraryItem`, safe typed document errors and authenticated `DocumentLibraryService.list_documents()` / `delete_document()`.
  - Added immutable structured `DocumentDeleteResult` and public `PDFLearningAssistant.delete_document(document_id)`; the existing Gradio wrapper delegates and retains display formatting.
  - Added `SessionRegistry.clear_document_selection(user_id, document_id) -> int`.
  - Added exactly one `ApplicationServices.document_library` instance plus request-state getters for document/import services.
  - Added safe document/import Pydantic projections and both API routers.
- Endpoint matrix:

  | Endpoint | Success | Authentication / mutation guard | Stable domain failures |
  |---|---:|---|---|
  | `GET /api/v1/documents` | 200 | Cookie | invalid session 401 |
  | `DELETE /api/v1/documents/{document_id}` | 204 | Cookie + CSRF | not found 404; active import 409; delete failure 500 retryable |
  | `POST /api/v1/imports` | 202 | Cookie + CSRF | unsupported/empty/count 422; byte limits 413; stage failure 500 retryable |
  | `GET /api/v1/imports?limit=20` | 200 | Cookie; limit 1–50 | validation 422 |
  | `GET /api/v1/imports/{batch_id}` | 200 | Cookie | batch not found 404 |
  | `POST /api/v1/imports/{batch_id}/tasks/{task_id}/retry` | 200 | Cookie + CSRF | task/membership not found 404; not retryable 409 |
  | `POST /api/v1/imports/{batch_id}/retry-failed` | 200 | Cookie + CSRF | batch not found 404 |
  | `POST /api/v1/imports/{batch_id}/tasks/{task_id}/cancel` | 200 | Cookie + CSRF | task/membership not found 404; not cancellable 409 |
- Acceptance criteria:
  - [x] Session-derived user scope, shared Runtime lock and fresh persisted history are asserted.
  - [x] Latest duplicate wins; nullable metadata, UTC-aware ISO-8601 ordering and malformed/escaping/Windows-junction records are covered.
  - [x] Exact coordinated RAG/history/question/formal-file deletion, all matching duplicate-source preflight, structured-result postconditions, exact active-import conflict, safe failure and same-user multi-session invalidation are covered.
  - [x] Service identity is stable and worker start/stop counts remain one each.
  - [x] Every endpoint has success/authentication coverage; every mutation has missing/forged CSRF coverage; UUID, query and multipart validation are covered.
  - [x] Actual repeated `files` multipart returns 202; real streamed byte limits return stable 413 errors.
  - [x] List/get/retry/retry-failed/cancel enforce user scope and nested membership with indistinguishable 404 behavior.
  - [x] Document and import response-field scans allow only the accepted DTO keys and exclude user/path/session/CSRF/internal fields.
  - [x] Existing auth, lifecycle, `/legacy`, assets, SPA Accept/fallback and unknown `/api/*` tests pass.
- Verification:
  - Baseline `D:\python_self_agent\venv\Scripts\python.exe -m pytest -q tests/test_app_bootstrap.py tests/api --basetemp=.runtime/pytest-document-api-baseline` — PASS, `45 passed in 45.86s`.
  - Collection RED with the three new focused files — expected failure, `2 errors` because `app.document_library` did not exist.
  - Behavior RED after public type skeleton — expected failure, `65 failed, 1 skipped in 7.22s`; services were unimplemented and routes absent.
  - Focused GREEN after implementation — `65 passed, 1 skipped in 5.00s`; the host-disabled symlink case was then replaced by a real Windows junction assertion.
  - Reparse-focused GREEN — `10 passed in 2.06s`.
  - Final required regression `D:\python_self_agent\venv\Scripts\python.exe -m pytest -q tests/test_document_library_service.py tests/test_app_bootstrap.py tests/api --basetemp=.runtime/pytest-document-api-final` — PASS, `114 passed in 43.33s`.
  - Existing Assistant/worker delete compatibility — `8 passed, 54 deselected in 8.36s`.
  - Existing real multi-user delete/clear integration — `2 passed, 10 deselected in 15.93s`.
  - Independent-review focused RED — `6 failed, 57 deselected in 4.09s`; failures proved incomplete duplicate preflight/result validation, swallowed `InvalidSessionError` and string-based timestamp ordering.
  - Independent-review focused GREEN — `6 passed, 57 deselected in 2.03s`.
  - Corrected final required regression — `120 passed in 35.39s`.
  - Corrected delete compatibility — `8 passed, 54 deselected in 9.52s`.
  - Corrected real multi-user delete/clear integration — `2 passed, 10 deselected in 15.51s`.
  - Producer timestamp re-review RED — `1 failed, 17 deselected in 3.95s`; real `load_document()` wrote a naive timestamp rejected by the service.
  - Producer/service focused GREEN — `1 passed, 17 deselected in 2.00s`.
  - Producer-corrected final required regression — `121 passed in 45.69s`.
  - Producer-corrected import compatibility — `14 passed in 7.75s`.
  - Producer-corrected delete compatibility — `8 passed, 54 deselected in 9.35s`.
  - `git diff --check` — PASS; line-ending notices only.
- Response-field scan:
  - Documents contain only `document_id`, `name`, `file_suffix`, `size_bytes`, `loaded_at`, `status`.
  - Import batches contain only `batch_id`, timestamps, accepted counts and accepted task DTOs; `user_id`, `staged_relative_path`, absolute/formal/temp paths, Cookie/CSRF values, raw exceptions and contents are not projected.
- Scope confirmation:
  - Implementation commit changes only the fourteen production/test files allowed by Packet 04. Packet 03/import internals, dependencies, frontend/design/E2E, RAG/Memory internals, deployment, `/legacy`, assets and SPA fallback are unchanged.
- Deviations: None.
- Independent-review corrections:
  - Every fresh-history record matching the requested document ID now receives the same documents-root containment/reparse preflight before Assistant mutation.
  - The structured Assistant result must report the exact requested ID, at least one removed document and zero skipped source files before session selection invalidation.
  - Upload-session expiry is re-raised to the existing `invalid_session` 401 handler instead of entering staging-error mapping.
  - Zoned ISO-8601 `loaded_at` values sort as UTC-aware datetimes; malformed values are diagnosed statically, projected as `null` and sorted in the deterministic null tail.
  - Real `PDFLearningAssistant.load_document()` now persists timezone-aware UTC `loaded_at`; legacy naive history is deliberately not reinterpreted.
- Residual risks: None known within Packet 04; independent re-review and mandatory final cross-packet integration review remain pending.
- Commits: `dcd64cd` (`feat: add document library APIs`), `0d06346` (`fix: close document API review gaps`), `a5355c8` (`fix: persist zoned document load times`).
