---
id: "document-library-vertical-slice-04"
title: "Expose authenticated document-library APIs"
status: "ready"
parallel-safe: false
depends-on: ["document-library-vertical-slice-03"]
base-commit: "f90883e71d2fa73a7cb981b11478b68519d8ce80"
owner: "unassigned"
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

Replace with the template handoff, including endpoint matrix/status evidence, service/lifecycle tests, response-field scans, scope confirmation, deviations/risks and commit.
