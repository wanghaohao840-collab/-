# Task 4 Implementation Report

## Status

- Result: ready for independent re-review
- Implementation base: `6d9ed6f19e1f97082a9e85d5b5da448cfa7f6f29`
- Implementation commit: `dcd64cd` (`feat: add document library APIs`)
- Independent-review correction: `0d06346` (`fix: close document API review gaps`)
- Packet: `document-library-vertical-slice-04`
- Network/external services: not used

## Delivered behavior

- `DocumentLibraryService` derives identity only from `SessionRegistry`, takes the shared user Runtime lock, reloads persisted History and returns immutable safe document views. Latest duplicate IDs win; invalid, escaping and reparse/junction records are skipped with static diagnostics. Zoned ISO-8601 timestamps sort as UTC-aware datetimes, while malformed/missing metadata stays nullable in a deterministic null tail.
- Deletion fresh-reads and preflights every source record matching the exact document ID, rejects an active exact-document import, invokes the Assistant's public structured delete, validates exact ID/non-zero removal/zero skipped-file postconditions, and only then clears exact selections across that user's active sessions. Missing and cross-user IDs are indistinguishable; failures become safe typed errors.
- `PDFLearningAssistant.delete_document(document_id)` returns immutable structured counts while reusing the existing RAG/history/question/source-file coordination. `delete_current_document()` remains the active-import-aware Gradio formatting wrapper.
- `ApplicationServices` constructs one document library from the existing registry/storage/import service. Request dependencies return that object and the existing import service from app state; no service or worker lifecycle was added.
- Document and import routers use Cookie-derived session identity, the existing CSRF dependency on every mutation, UUID/query/multipart parsing, accepted DTO projections and the common JSON error envelope.

## Endpoint and error evidence

| Endpoint | Success | Stable errors covered |
|---|---:|---|
| `GET /api/v1/documents` | 200 | session 401 |
| `DELETE /api/v1/documents/{document_id}` | 204 | CSRF 403; validation 422; document 404; active 409; delete 500 retryable |
| `POST /api/v1/imports` | 202 | CSRF 403; unsupported/empty/count 422; actual byte limits 413; stage 500 retryable |
| `GET /api/v1/imports?limit=20` | 200 | session 401; validation 422 |
| `GET /api/v1/imports/{batch_id}` | 200 | validation 422; batch 404 |
| retry / retry-failed / cancel mutations | 200 | CSRF 403; batch/task/membership 404; retry/cancel conflict 409 |

Other-user document, batch and task IDs use the same 404 codes as missing IDs. Nested task routes pass the URL batch to the existing membership-aware service contracts.

## RED evidence

1. Initial focused collection RED:

   `D:\python_self_agent\venv\Scripts\python.exe -m pytest -q tests/test_document_library_service.py tests/api/test_document_routes.py tests/api/test_import_routes.py --basetemp=.runtime/pytest-document-api-red`

   Expected result: `2 errors`, both proving the absent `app.document_library` boundary.

2. Behavior RED after adding only the public type skeleton:

   Same test set with `--basetemp=.runtime/pytest-document-api-red-behavior` — expected `65 failed, 1 skipped in 7.22s`. Failures proved unimplemented service methods, missing structured Assistant/session APIs and absent document/import routes.

3. Independent-review correction RED:

   `D:\python_self_agent\venv\Scripts\python.exe -m pytest -q tests/test_document_library_service.py tests/api/test_import_routes.py -k "sorts_valid_offsets or preflights_every_duplicate or structured_partial or mismatched_or_zero or propagates_session_expiry" --basetemp=.runtime/pytest-task4-review-red`

   Expected result: `6 failed, 57 deselected in 4.09s`. The failures directly proved string-based timestamp misordering, missing older-duplicate path preflight, ignored partial/mismatched/zero-removal structured results and `InvalidSessionError` misclassification as a staging 500.

## GREEN evidence

1. Final required regression:

   `D:\python_self_agent\venv\Scripts\python.exe -m pytest -q tests/test_document_library_service.py tests/test_app_bootstrap.py tests/api --basetemp=.runtime/pytest-document-api-final`

   Result: `114 passed in 43.33s`.

2. Compatibility checks:

   - Existing Assistant/worker delete-clear guard selection: `8 passed, 54 deselected in 8.36s`.
   - Real multi-user Assistant delete/clear integration: `2 passed, 10 deselected in 15.93s`.
   - Windows junction/reparse service coverage: `10 passed in 2.06s` before two later service safety cases brought the final service count to 12.

3. `git diff --check` — PASS; only expected CRLF conversion notices.

4. Independent-review correction GREEN:

   - Focused command above with `--basetemp=.runtime/pytest-task4-review-green` — `6 passed, 57 deselected in 2.03s`.
   - Corrected required regression with `--basetemp=.runtime/pytest-document-api-review-final` — `120 passed in 35.39s`.
   - Corrected Assistant/worker delete compatibility — `8 passed, 54 deselected in 9.52s`.
   - Corrected real multi-user delete/clear integration — `2 passed, 10 deselected in 15.51s`.

## Scope and security review

- Production and tests changed only in Packet 04's exact allowlist. Packet 03 import internals and all forbidden frontend/design/E2E/dependency/RAG/Memory/deployment/mount files are untouched.
- All service calls use the HttpOnly Cookie token. No request body/query/header `user_id` is consumed.
- Runtime locking, fresh history, exact active-import lookup and same-user exact selection invalidation have direct assertions.
- Selection invalidation is withheld when any duplicate source preflight fails or when the structured deletion result reports a mismatched ID, zero removals or skipped files.
- A session that expires between CSRF validation and upload staging reuses the existing 401 `invalid_session` envelope; the broad staging `ValueError` mapping no longer consumes it.
- Document DTOs expose six accepted business fields. Import DTOs explicitly project only accepted batch/count/task fields; persisted user and staging fields never enter schema construction.
- Static error messages and diagnostics do not echo paths, credentials, raw exceptions, Cookie/CSRF values or file contents.
- Existing application lifespan, worker identity/count, auth, `/legacy`, assets, SPA Accept handling and reserved `/api/*` JSON 404 behavior all remain passing.

## Deviations and residual risks

- Deviations: None.
- Residual risks: None known within Packet 04. Independent re-review and the mandatory final integration review remain pending.
