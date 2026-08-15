# Document Library Vertical Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `/documents` migration placeholder with a real, responsive document library that supports durable batch upload, progress, retry, safe cancellation, listing, and deletion for the authenticated user.

**Architecture:** Extend the existing SQLite-backed `ImportTaskRepository` and worker with a deterministic cancellation gate, then expose the existing import pipeline and a new `DocumentLibraryService` through user-scoped FastAPI routers. Build one React feature slice on the approved Penpot design source, using TanStack Query for server state and the existing AuthProvider for Cookie/CSRF/401 behavior.

**Tech Stack:** Python 3.12, SQLite, FastAPI, Pydantic, existing RAG/Memory runtime, React 19, React Router 7, TanStack Query 5, TypeScript 5.9, Vitest, Playwright, axe, Penpot 2.17.1.

## Global Constraints

- Implement from clean base commit `f90883e` in `D:\python_self_agent\.worktrees\document-library-vertical-slice`; preserve the approved design spec at `docs/superpowers/specs/2026-08-15-document-library-vertical-slice-design.md`.
- Use `D:\python_self_agent\venv\Scripts\python.exe` for every Python command; create pytest `--basetemp` under this worktree's ignored `.runtime/` directory.
- Keep one `ApplicationServices` instance and one Uvicorn worker; do not add Redis, Celery, browser object-storage upload, or a second import pipeline.
- Derive `user_id` only from the HttpOnly session Cookie; every POST and DELETE uses the existing `X-CSRF-Token` dependency.
- Preserve `.pdf`, `.txt`, `.md`, `.markdown`, `.docx`; 20 files per batch, 100 MiB per file, 500 MiB per batch.
- Never expose `user_id`, absolute paths, `staged_relative_path`, Cookie/CSRF values, raw exceptions, or file contents in API responses.
- Preserve `document_id` isolation, source metadata, PDF page citations, JSON/Qdrant switching, Memory, reports, `/legacy/`, and existing Gradio callers.
- Penpot file `3be9e5e1-190f-8090-8008-6ff3f3dcd54c` remains the sole product design source; stop before a design write if the file, page, component, or expected parent identity differs.
- Use approved desktop 1440×1024, tablet 1024×768, and mobile 390×844 viewports; mobile interactive targets are at least 44×44 px.
- Do not fabricate document counts, pages, chunks, reading progress, filenames, or overview statistics in production or E2E fixtures beyond records created by the test itself.
- Use RED → GREEN for each delivery unit, run the listed focused commands, commit only owned files, and stop on a task-packet reality conflict.

---

## File Structure

### Design source and handoff

- Modify `docs/product-ui/penpot-handoff.md`: append verified document-library page, component, board, responsive, and deliberate-difference evidence.
- Create `docs/product-ui/reference/penpot/desktop-documents.png`, `tablet-documents.png`, `mobile-documents.png`, `documents-empty.png`, `documents-importing.png`, `documents-partial-failure.png`, and `mobile-import-sheet.png`: direct Penpot exports.

### Import state machine

- Modify `app/database.py`: idempotent SQLite rebuild migration for `cancelled` plus `cancel_requested_at`.
- Modify `app/import_models.py`: cancellation status, stage, count, timestamp, and typed decision.
- Modify `app/import_repository.py`: cancellation transitions, commit gate, cancelled reconciliation, and batch aggregation.
- Modify `tests/test_import_models.py` and `tests/test_import_repository.py`: model, migration, concurrency, and isolation coverage.

### Streaming and worker cancellation

- Modify `app/storage.py`: guarded `.partial` staging and exact terminal cleanup helpers.
- Modify `app/import_service.py`: path/stream normalization, actual-byte enforcement, cancel API, and safe cleanup.
- Modify `app/import_worker.py`: cooperative cancellation checks and atomic begin-commit gate.
- Modify `tests/test_import_service.py`, `tests/test_import_worker.py`, and `tests/test_import_error_sanitization.py`: stream, cancellation, cleanup, and sanitization coverage.

### Document/API boundary

- Create `app/document_library.py`: safe document projection and coordinated deletion service.
- Modify `assistants/pdf_learning_assistant.py`: public structured delete-by-ID entry; retain Gradio formatting wrapper.
- Modify `app/session.py`: clear an exact deleted document selection across active sessions for one user.
- Modify `app/bootstrap.py`: construct `DocumentLibraryService` once.
- Modify `api/dependencies.py`: resolve application services through request state.
- Create `api/schemas/documents.py`, `api/schemas/imports.py`, `api/routes/documents.py`, and `api/routes/imports.py`.
- Modify `api/app.py`: include new routers without changing mounts/fallbacks.
- Create `tests/test_document_library_service.py`, `tests/api/test_document_routes.py`, and `tests/api/test_import_routes.py`; modify `tests/test_app_bootstrap.py`.

### React document feature

- Create `web/src/features/documents/types.ts`, `api.ts`, and `queries.ts`.
- Create `web/src/components/DocumentToolbar/DocumentToolbar.tsx`, `DocumentList/DocumentList.tsx`, `ImportDialog/ImportDialog.tsx`, and `ImportBatchPanel/ImportBatchPanel.tsx`.
- Create `web/src/pages/DocumentsPage.tsx`, `web/src/pages/DocumentsPage.test.tsx`, and `web/src/styles/documents.css`.
- Modify `web/src/App.tsx`, `web/src/main.tsx`, and `docs/product-ui/penpot-component-map.json`.
- Create focused component/query tests beside each new feature module.

### Acceptance

- Create `web/e2e/documents.spec.ts` and document visual snapshots.
- Modify `web/e2e/accessibility.spec.ts` and `web/e2e/visual.spec.ts` only for the new authenticated route.
- Create `tests/deploy/test_document_library_contract.py` for tracked-design and E2E contract assertions.

---

### Task 1: Create and verify the Penpot document-library source

**Files:**
- Modify: `docs/product-ui/penpot-handoff.md`
- Create: `docs/product-ui/reference/penpot/desktop-documents.png`
- Create: `docs/product-ui/reference/penpot/tablet-documents.png`
- Create: `docs/product-ui/reference/penpot/mobile-documents.png`
- Create: `docs/product-ui/reference/penpot/documents-empty.png`
- Create: `docs/product-ui/reference/penpot/documents-importing.png`
- Create: `docs/product-ui/reference/penpot/documents-partial-failure.png`
- Create: `docs/product-ui/reference/penpot/mobile-import-sheet.png`
- Test: `tests/deploy/test_document_library_contract.py`

**Interfaces:**
- Consumes: Penpot file ID `3be9e5e1-190f-8090-8008-6ff3f3dcd54c`, the seven existing page IDs, approved Tokens, and existing AppShell/Button/IconButton/Dialog/Drawer/TextField/Badge/EmptyState/Skeleton components.
- Produces: verified board/component IDs and seven direct PNG exports; code tasks use those names, dimensions, and IDs as visual authority.

- [ ] **Step 1: Write the failing repository contract**

```python
from pathlib import Path


EXPECTED_EXPORTS = {
    "desktop-documents.png": (1440, 1024),
    "tablet-documents.png": (1024, 768),
    "mobile-documents.png": (390, 844),
    "documents-empty.png": (1440, 1024),
    "documents-importing.png": (1440, 1024),
    "documents-partial-failure.png": (1440, 1024),
    "mobile-import-sheet.png": (390, 844),
}


def test_document_library_handoff_names_all_reference_exports():
    handoff = Path("docs/product-ui/penpot-handoff.md").read_text(encoding="utf-8")
    for filename in EXPECTED_EXPORTS:
        assert filename in handoff
```

- [ ] **Step 2: Run the contract to verify RED**

Run:

```powershell
New-Item -ItemType Directory -Force .runtime | Out-Null
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/deploy/test_document_library_contract.py --basetemp=.runtime/pytest-penpot-documents-red
```

Expected: FAIL because the document-library handoff section and exports do not exist.

- [ ] **Step 3: Fresh-read and build the design source**

Use the connected Penpot MCP to assert the active file ID and page IDs before writing. Reuse existing components; if repeated DocumentRow, ImportTaskRow, or FilePicker structures are added, create library components with explicit variants and token bindings. Create these board names exactly:

```text
Desktop / Documents / Complete
Tablet / Documents / Complete
Mobile / Documents / Complete
State / Documents / Empty
State / Documents / Importing
State / Documents / Partial failure
Mobile / Documents / Import sheet
```

The complete boards use only design-sample content and label it in Handoff as non-production. The empty/importing/failure boards must use the approved Chinese copy from the design spec, not fake business statistics.

- [ ] **Step 4: Verify and export**

Fresh-read every original board ID after the write and assert:

```text
linked component copies: 0 broken
text bounds overflow: 0
actual bounds overflow: 0
mobile interactive targets: every target >= 44 x 44
viewport dimensions: 1440x1024, 1024x768, or 390x844 exactly
```

Export each board directly to its owned repository PNG path, decode it, verify dimensions/non-empty bytes, and visually inspect all seven files.

- [ ] **Step 5: Update handoff and turn GREEN**

Record file revision, page/board/component IDs, direct export paths, responsive behavior, state semantics, token bindings, and any deliberate browser difference in `penpot-handoff.md`. Then run:

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/deploy/test_document_library_contract.py --basetemp=.runtime/pytest-penpot-documents-green
git diff --check
```

Expected: contract PASS, seven images have exact expected dimensions, diff check PASS.

- [ ] **Step 6: Commit**

```powershell
git add docs/product-ui/penpot-handoff.md docs/product-ui/reference/penpot tests/deploy/test_document_library_contract.py
git commit -m "design: add Penpot document library source"
```

---

### Task 2: Add durable cancellation persistence and commit arbitration

**Files:**
- Modify: `app/database.py`
- Modify: `app/import_models.py`
- Modify: `app/import_repository.py`
- Modify: `tests/test_import_models.py`
- Modify: `tests/test_import_repository.py`

**Interfaces:**
- Consumes: existing `ImportTaskRepository.claim_next()`, `update_progress()`, terminal methods, batch aggregation, and SQLite startup before worker start.
- Produces: `cancelled` status/stage, `cancel_requested_at`, `CancelOutcome`, `request_cancel()`, `is_cancel_requested()`, `try_begin_committing()`, and `mark_cancelled()`.

- [ ] **Step 1: Write failing model and migration tests**

```python
def test_cancelled_batch_count_is_part_of_summary():
    assert "cancelled" in ImportBatchSummary.__dataclass_fields__


def test_new_import_schema_has_cancellation_contract(tmp_path):
    db_path = tmp_path / "app.db"
    initialize_database(db_path)
    with connect(db_path) as conn:
        sql = conn.execute(
            "select sql from sqlite_master where type='table' and name='import_tasks'"
        ).fetchone()["sql"]
        columns = {
            row["name"] for row in conn.execute("pragma table_info('import_tasks')")
        }
    assert "cancelled" in sql
    assert "cancel_requested_at" in columns
```

Add a second migration test that creates the exact pre-change `import_tasks` table from base commit `f90883e`, inserts one failed task and its batch/user parents, runs `initialize_database()`, and asserts the task values plus all three index names are preserved. The pre-change SQL is a literal test fixture; it must not call the new initializer to create the old table.

- [ ] **Step 2: Run migration/model RED**

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/test_import_models.py tests/test_import_repository.py -k "cancel or upgrade or commit" --basetemp=.runtime/pytest-cancel-repository-red
```

Expected: FAIL because cancellation types, column, migration, and methods are absent.

- [ ] **Step 3: Add exact model contracts**

```python
ImportStatus = Literal[
    "queued", "running", "retry_wait", "succeeded", "failed", "cancelled"
]
ImportStage = Literal[
    "queued", "staged", "parsing", "chunking", "embedding",
    "persisting", "committing", "succeeded", "failed", "cancelled",
]
CancelOutcome = Literal[
    "cancelled", "cancel_requested", "not_cancellable", "unchanged"
]


@dataclass(frozen=True)
class ImportCancelDecision:
    task: ImportTaskRecord
    outcome: CancelOutcome
```

Add `cancel_requested_at: str | None` to `ImportTaskRecord` and `cancelled: int` to `ImportBatchSummary`.

- [ ] **Step 4: Implement the idempotent SQLite rebuild**

Create `_upgrade_import_tasks_for_cancellation(conn)` and call it from `initialize_database()` before indexes are relied upon. It must inspect `sqlite_master.sql`; when `cancelled` and `cancel_requested_at` are already present it returns. Otherwise it creates `import_tasks_new`, copies every old column plus `null as cancel_requested_at`, drops/renames inside one transaction, and recreates:

```sql
create unique index if not exists uq_import_tasks_running_user
on import_tasks(user_id) where status = 'running';
create index if not exists ix_import_tasks_scheduler
on import_tasks(status, next_attempt_at, created_at);
create index if not exists ix_import_tasks_user_created
on import_tasks(user_id, created_at);
```

- [ ] **Step 5: Write cancellation/concurrency tests**

```python
def test_cancel_wins_before_committing(repository, queued_task):
    running = repository.claim_next(set())
    decision = repository.request_cancel(running.user_id, running.batch_id, running.task_id)
    assert decision.outcome == "cancel_requested"
    assert repository.try_begin_committing(running.user_id, running.task_id) is False


def test_committing_wins_before_cancel(repository, queued_task):
    running = repository.claim_next(set())
    assert repository.try_begin_committing(running.user_id, running.task_id) is True
    decision = repository.request_cancel(running.user_id, running.batch_id, running.task_id)
    assert decision.outcome == "not_cancellable"
```

Also cover immediate queued/retry_wait cancellation, terminal idempotence, cancelled-not-claimable, cancelled-not-retryable, batch/task/user mismatch, and two connections racing cancel vs commit.

- [ ] **Step 6: Implement repository transitions**

Use `begin immediate` transactions for `request_cancel()` and `try_begin_committing()`. `request_cancel()` returns current task plus typed outcome. `try_begin_committing()` performs one conditional update:

```sql
update import_tasks
set stage = 'committing', updated_at = ?
where id = ? and user_id = ? and status = 'running'
  and stage != 'committing' and cancel_requested_at is null
```

`mark_cancelled()` accepts only running tasks with a cancellation request. Batch aggregation and row mapping include all new fields. `retry_task()` rejects cancelled tasks.

- [ ] **Step 7: Run GREEN and regression**

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/test_import_models.py tests/test_import_repository.py --basetemp=.runtime/pytest-cancel-repository-green
```

Expected: all selected model/repository tests PASS, including the pre-change database upgrade.

- [ ] **Step 8: Commit**

```powershell
git add app/database.py app/import_models.py app/import_repository.py tests/test_import_models.py tests/test_import_repository.py
git commit -m "feat: add durable import cancellation state"
```

---

### Task 3: Stream FastAPI uploads and cancel worker attempts safely

**Files:**
- Modify: `app/storage.py`
- Modify: `app/import_service.py`
- Modify: `app/import_worker.py`
- Modify: `tests/test_import_service.py`
- Modify: `tests/test_import_worker.py`
- Modify: `tests/test_import_error_sanitization.py`

**Interfaces:**
- Consumes: Task 2 cancellation repository API and current Gradio `ImportTaskService.submit_batch(session_token, files, progress=None)`.
- Produces: `ImportUpload`, `submit_uploads()`, `cancel_task()`, guarded stream staging, `ImportCancelled`, and worker cleanup/reconciliation.

- [ ] **Step 1: Write streaming and cancellation RED tests**

```python
def test_submit_uploads_enforces_actual_stream_size(service, session_token):
    upload = ImportUpload("too-large.txt", io.BytesIO(b"x" * 11))
    service.limits = ImportLimits(max_files=2, max_file_bytes=10, max_batch_bytes=20)
    with pytest.raises(ImportLimitError) as exc_info:
        service.submit_uploads(session_token, [upload])
    assert exc_info.value.code == "import_file_too_large"
    assert service.list_batches(session_token) == []


def test_running_cancel_marks_cancelled_and_removes_attempt_files(runner_fixture):
    runner_fixture.request_cancel_during("embedding")
    runner_fixture.run_once()
    task = runner_fixture.reload_task()
    assert task.status == "cancelled"
    assert not runner_fixture.staged_path.exists()
    assert not runner_fixture.formal_path.exists()
```

- [ ] **Step 2: Run RED**

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/test_import_service.py tests/test_import_worker.py tests/test_import_error_sanitization.py -k "stream or cancel or partial or actual" --basetemp=.runtime/pytest-import-stream-red
```

Expected: FAIL because stream upload and worker cancellation APIs are absent.

- [ ] **Step 3: Add typed service inputs and errors**

```python
@dataclass(frozen=True)
class ImportUpload:
    original_name: str
    stream: BinaryIO


class ImportLimitError(ValueError):
    def __init__(self, code: str, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class ImportTaskNotCancellableError(RuntimeError):
    pass
```

Keep `submit_batch()` public and adapt path-backed values into `ImportUpload` instances with an `ExitStack`; add `submit_uploads(session_token, uploads, progress=None)` for FastAPI.

- [ ] **Step 4: Implement guarded streaming staging**

Write in fixed-size chunks to `<task-id><suffix>.partial`, counting actual bytes. Reject an individual stream once it exceeds 100 MiB and reject a batch once accumulated bytes exceed 500 MiB. Flush and `fsync`, then `os.replace(partial, staged)` only after that file is valid. On any exception, close streams owned by the adapter, remove the exact validated batch directory, and create no database batch.

- [ ] **Step 5: Implement service cancel behavior**

```python
def cancel_task(
    self, session_token: str, batch_id: str, task_id: str
) -> ImportBatchSummary:
    session = self._session(session_token)
    with self._runtime_lock(session):
        decision = self.repository.request_cancel(
            str(session.user_id), batch_id, task_id
        )
        if decision.outcome == "not_cancellable":
            raise ImportTaskNotCancellableError("import task is committing")
        if decision.outcome == "cancelled":
            self._cleanup_cancelled_staging(decision.task)
        summary = self.repository.get_batch(str(session.user_id), batch_id)
    self.worker_pool.notify()
    return summary
```

Cleanup failure leaves the task cancelled and emits only a sanitized server diagnostic. Extend terminal startup reconciliation to validate and delete staging files for both succeeded and cancelled tasks.

- [ ] **Step 6: Implement worker cooperation and the commit gate**

Add `ImportCancelled`. Before every non-committing progress write, query `is_cancel_requested()` and raise. When the callback receives `committing`, call `try_begin_committing()` instead of ordinary `update_progress()`; false means raise before the Assistant commits. Catch `ImportCancelled` separately, remove exact temporary/formal/staged files, mark cancelled, and never schedule retry.

- [ ] **Step 7: Run GREEN and existing import regression**

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/test_import_service.py tests/test_import_worker.py tests/test_import_error_sanitization.py tests/ui/test_import_handlers.py --basetemp=.runtime/pytest-import-stream-green
```

Expected: all selected tests PASS; existing Gradio path uploads and retry behavior remain passing.

- [ ] **Step 8: Commit**

```powershell
git add app/storage.py app/import_service.py app/import_worker.py tests/test_import_service.py tests/test_import_worker.py tests/test_import_error_sanitization.py
git commit -m "feat: stream and cancel document imports"
```

---

### Task 4: Expose user-scoped document and import APIs

**Files:**
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

**Interfaces:**
- Consumes: completed Tasks 2–3, `get_current_session`, `get_csrf_validated_session`, existing API error envelope, session Cookie name, and unified application lifecycle.
- Produces: `DocumentLibraryService`, document/import Pydantic response schemas, and `/api/v1/documents` plus `/api/v1/imports` endpoints.

- [ ] **Step 1: Write failing service and API tests**

```python
def test_list_documents_projects_only_safe_fields(document_service, session_token):
    items = document_service.list_documents(session_token)
    assert items[0].document_id == DOCUMENT_ID
    assert items[0].name == "notes.md"
    assert not hasattr(items[0], "document_path")
    assert not hasattr(items[0], "user_id")


def test_submit_import_requires_csrf(authenticated_client, upload_file):
    response = authenticated_client.post(
        "/api/v1/imports", files=[("files", upload_file)]
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "invalid_csrf_token"
```

Add API cases for 202 submission, query, retry, cancel, delete, active-import 409, unsupported type, actual size limits, cross-user 404, and no path/user fields.

- [ ] **Step 2: Run RED**

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/test_document_library_service.py tests/api/test_document_routes.py tests/api/test_import_routes.py --basetemp=.runtime/pytest-document-api-red
```

Expected: collection/import failures because the service, schemas, and routers do not exist.

- [ ] **Step 3: Implement structured document projection/deletion**

```python
@dataclass(frozen=True)
class DocumentLibraryItem:
    document_id: str
    name: str
    file_suffix: str
    size_bytes: int | None
    loaded_at: str | None
    status: Literal["ready"] = "ready"
```

Implement `DocumentLibraryService.list_documents(self, session_token: str) -> tuple[DocumentLibraryItem, ...]` and `DocumentLibraryService.delete_document(self, session_token: str, document_id: str) -> None` with concrete lock/read/project/delete logic: latest duplicate wins, paths are preflighted inside the user root, nullable metadata stays nullable, active task yields a typed conflict, and no response model carries a path.

Add a structured `delete_document(document_id)` public Assistant method and make `delete_current_document()` format that result. Add `SessionRegistry.clear_document_selection(user_id, document_id)` to clear only exact matches for that user across active sessions.

- [ ] **Step 4: Wire one service instance**

Add `document_library: DocumentLibraryService` to `ApplicationServices`, construct it after `import_service`, and expose request-state dependency getters. Extend `tests/test_app_bootstrap.py` to assert identity and zero extra worker start/stop calls.

- [ ] **Step 5: Define exact API schemas and routes**

```python
@router.get("/api/v1/documents", response_model=DocumentListResponse)
def list_documents(
    request: Request,
    _session: Annotated[UserSession, Depends(get_current_session)],
    service: Annotated[DocumentLibraryService, Depends(get_document_library_service)],
) -> DocumentListResponse:
    return DocumentListResponse(
        items=service.list_documents(get_session_token(request))
    )


@router.delete("/api/v1/documents/{document_id}", status_code=204)
def delete_document(
    document_id: UUID,
    request: Request,
    _session: Annotated[UserSession, Depends(get_csrf_validated_session)],
    service: Annotated[DocumentLibraryService, Depends(get_document_library_service)],
) -> Response:
    service.delete_document(get_session_token(request), str(document_id))
    return Response(status_code=204)


@router.post("/api/v1/imports", response_model=ImportBatchResponse, status_code=202)
def submit_imports(
    request: Request,
    files: Annotated[list[UploadFile], File()],
    _session: Annotated[UserSession, Depends(get_csrf_validated_session)],
    service: Annotated[ImportTaskService, Depends(get_import_service)],
) -> ImportBatchResponse:
    uploads = [
        ImportUpload(original_name=item.filename or "", stream=item.file)
        for item in files
    ]
    return import_batch_response(
        service.submit_uploads(get_session_token(request), uploads)
    )
```

Also implement list/get/retry/retry-failed/cancel at the exact paths in the design spec. Every mutation depends on `get_csrf_validated_session`; every service call receives the Cookie token, never a body `user_id`. Map typed domain failures to the stable codes/statuses in the spec.

- [ ] **Step 6: Include routers without changing mounts**

Include both routers in `create_api_app()` after auth. Do not move `/legacy`, `/assets`, SPA fallback, lazy service binding, or lifespan start/stop.

- [ ] **Step 7: Run GREEN and API/lifecycle regression**

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/test_document_library_service.py tests/test_app_bootstrap.py tests/api --basetemp=.runtime/pytest-document-api-green
```

Expected: all selected tests PASS; `/legacy` redirect/mount, SPA Accept behavior, auth, and one start/stop lifecycle remain passing.

- [ ] **Step 8: Commit**

```powershell
git add app/document_library.py assistants/pdf_learning_assistant.py app/session.py app/bootstrap.py api tests/test_document_library_service.py tests/test_app_bootstrap.py tests/api
git commit -m "feat: add document library APIs"
```

---

### Task 5: Build the responsive React document library

**Files:**
- Create: `web/src/features/documents/types.ts`
- Create: `web/src/features/documents/api.ts`
- Create: `web/src/features/documents/queries.ts`
- Create: `web/src/features/documents/queries.test.tsx`
- Create: `web/src/components/DocumentToolbar/DocumentToolbar.tsx`
- Create: `web/src/components/DocumentList/DocumentList.tsx`
- Create: `web/src/components/ImportDialog/ImportDialog.tsx`
- Create: `web/src/components/ImportDialog/ImportDialog.test.tsx`
- Create: `web/src/components/ImportBatchPanel/ImportBatchPanel.tsx`
- Create: `web/src/pages/DocumentsPage.tsx`
- Create: `web/src/pages/DocumentsPage.test.tsx`
- Create: `web/src/styles/documents.css`
- Modify: `web/src/App.tsx`
- Modify: `web/src/main.tsx`
- Modify: `docs/product-ui/penpot-component-map.json`

**Interfaces:**
- Consumes: Task 1 Penpot handoff IDs/geometry; Task 4 exact API schemas and AuthProvider `request<T>()`.
- Produces: real `/documents`, TanStack Query hooks, accessible import/delete overlays, and mapped document components.

- [ ] **Step 1: Write route/state/query RED tests**

```tsx
it("renders the real document library route", async () => {
  renderAuthenticatedApp("/documents");
  expect(await screen.findByRole("heading", { level: 1, name: "文档库" })).toBeVisible();
  expect(screen.queryByText("该能力正在迁移到新版界面")).not.toBeInTheDocument();
});


it("polls only while an import task is active", async () => {
  const fetchStub = installDocumentFetchStub([task({ status: "running" })]);
  const { setImports } = renderDocumentQueries();
  await advanceTimersByTimeAsync(2000);
  expect(fetchStub.importRequestCount()).toBe(2);
  setImports([task({ status: "succeeded" })]);
  await advanceTimersByTimeAsync(4000);
  expect(fetchStub.importRequestCount()).toBe(2);
});
```

Use test-local `fetch` stubs consistent with existing frontend tests; do not add MSW or a dependency.

- [ ] **Step 2: Run frontend RED**

```powershell
Set-Location web
npm ci
npx vitest run src/pages/DocumentsPage.test.tsx src/features/documents/queries.test.tsx src/components/ImportDialog/ImportDialog.test.tsx
```

Expected: FAIL because feature files and route are absent.

- [ ] **Step 3: Add exact client types and API calls**

```ts
export type ImportStatus =
  | "queued" | "running" | "retry_wait"
  | "succeeded" | "failed" | "cancelled";

export const DOCUMENTS_QUERY_KEY = ["documents"] as const;
export const IMPORTS_QUERY_KEY = ["imports", { limit: 20 }] as const;

export function hasActiveImports(batches: ImportBatch[]): boolean {
  return batches.some((batch) =>
    batch.tasks.some((task) =>
      task.status === "queued" || task.status === "running" || task.status === "retry_wait",
    ),
  );
}
```

Use `auth.request` for every call. Upload uses `FormData` without manually setting `Content-Type`; mutations rely on AuthProvider to add CSRF.

- [ ] **Step 4: Implement queries and invalidation**

Use `refetchInterval: (query) => hasActiveImports(query.state.data ?? []) ? 2000 : false`, refetch on focus, and no retry. On task success invalidate documents. Mutation success writes the returned batch into cached imports then invalidates imports; do not guess local state transitions.

- [ ] **Step 5: Implement approved components and states**

Build list-first desktop/tablet/mobile UI from Task 1. The production page renders only API data. `size_bytes`/`loaded_at` null values are omitted. Implement loading skeleton, first empty state, importing, partial failure, and complete states. Delete uses a confirmation dialog with the real filename. Import overlay enforces visible format/size help and client feedback but still displays server errors.

The import dialog/sheet must trap focus, close on Escape, restore focus and original body overflow, and keep every mobile action at least 44px. Use a deduplicated polite live region for status changes.

- [ ] **Step 6: Replace only `/documents` and bind design mapping**

In `App.tsx`, render `DocumentsPage` for `item.path === "/documents"`; leave all other navigation destinations on `MigrationPage`. Import `documents.css` from `main.tsx`. Add only components that actually exist in code and Penpot to `penpot-component-map.json`, with exact Task 1 IDs and `verified: true` after fresh-read evidence.

- [ ] **Step 7: Run GREEN and frontend gates**

```powershell
Set-Location web
npm test
npm run typecheck
npm run lint
npm run build
Set-Location ..
node --test tests/design/test_penpot_component_map.mjs
node scripts/design_tokens.mjs --check
```

Expected: unit tests, typecheck, lint, build, mapping, and 32-token freshness all PASS; `web/dist` remains ignored.

- [ ] **Step 8: Commit**

```powershell
git add web/src docs/product-ui/penpot-component-map.json
git commit -m "feat: add responsive document library"
```

---

### Task 6: Prove the real vertical slice in three viewports

**Files:**
- Create: `web/e2e/documents.spec.ts`
- Modify: `web/e2e/accessibility.spec.ts`
- Modify: `web/e2e/visual.spec.ts`
- Create: `web/e2e/visual.spec.ts-snapshots/documents-empty-desktop.png`
- Create: `web/e2e/visual.spec.ts-snapshots/documents-empty-tablet.png`
- Create: `web/e2e/visual.spec.ts-snapshots/documents-empty-mobile.png`
- Create: `web/e2e/visual.spec.ts-snapshots/documents-complete-desktop.png`
- Create: `web/e2e/visual.spec.ts-snapshots/documents-complete-tablet.png`
- Create: `web/e2e/visual.spec.ts-snapshots/documents-complete-mobile.png`
- Modify: `tests/deploy/test_document_library_contract.py`

**Interfaces:**
- Consumes: all prior tasks, current `web/e2e/fixtures.ts` real Uvicorn fixture, and direct Penpot reference exports.
- Produces: real-server functional, isolation, axe, keyboard, cleanup, and visual evidence; no test-only production endpoint.

- [ ] **Step 1: Write functional E2E RED**

```ts
test("imports, lists, and deletes a real document", async ({ page, appUrl }, testInfo) => {
  await registerUser(page, appUrl, uniqueUsername(`documents_${testInfo.project.name}`));
  await page.goto(`${appUrl}/documents`);
  await page.getByRole("button", { name: "导入文档" }).click();
  await page.getByLabel("选择文档").setInputFiles({
    name: "e2e-notes.md",
    mimeType: "text/markdown",
    buffer: Buffer.from("# E2E\nA real imported document."),
  });
  await page.getByRole("button", { name: "开始导入" }).click();
  await expect(page.getByText("e2e-notes.md")).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: "删除 e2e-notes.md" }).click();
  await page.getByRole("button", { name: "确认删除" }).click();
  await expect(page.getByText("e2e-notes.md")).not.toBeVisible();
});
```

Also test a second user cannot access the first user's batch/task/document IDs through direct API requests made in that second browser context.

- [ ] **Step 2: Build and run E2E RED**

```powershell
Set-Location web
npm run build
npx playwright test e2e/documents.spec.ts --project=desktop --workers=1
```

Expected before final E2E implementation: at least one focused assertion fails; no route interception, history injection, or production backdoor is added.

- [ ] **Step 3: Complete stable functional and cancellation coverage**

Use real small TXT/Markdown uploads for success. Exercise retry in service/API integration when no legitimate deterministic browser input can guarantee a parser failure. Exercise running-cancel in worker integration unless a bounded large fixture reliably exposes running state; do not add sleeps or test-only endpoints to production. Browser tests must still verify the cancel control contract for a cancellable task through real API state when timing is stable.

- [ ] **Step 4: Add axe, keyboard, and visual coverage**

Extend the existing accessibility test to include empty library, import overlay/sheet, populated list, and delete dialog. Assert focus trap, Shift+Tab loop, Escape, return focus, scroll restoration, one `h1`, visible focus, 44px mobile targets, and serious/critical axe count zero.

Generate exactly six document visual baselines after direct comparison with the Penpot references. Do not update unrelated snapshots. Record any approved illustrative-data difference in the handoff before accepting a mismatch.

- [ ] **Step 5: Run full feature gates**

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/test_import_models.py tests/test_import_repository.py tests/test_import_service.py tests/test_import_worker.py tests/test_document_library_service.py tests/api tests/deploy/test_document_library_contract.py --basetemp=.runtime/pytest-document-library-final
Set-Location web
npm test
npm run typecheck
npm run lint
npm run build
npx playwright test --workers=1
Set-Location ..
node --test tests/design/test_penpot_component_map.mjs
node scripts/design_tokens.mjs --check
git diff --check
```

Expected: all commands exit 0; Playwright reports three intentional existing skips only if they remain documented, no new failure or skip; no Python/Uvicorn process or `zhiyan-playwright-*` runtime root remains.

- [ ] **Step 6: Run safety scans**

```powershell
rg -n "(password|secret|token|api[_-]?key)\s*[:=]\s*['\"][^'\"]+" app api web/src docs/product-ui
rg -n "figma\.com|file://|C:\\Users" docs/product-ui
rg -n 'staged_relative_path|"user_id"' web/src web/e2e
git status --short
```

Expected: no credential, Figma, local-path, or response-leak finding introduced by this feature; only intended tracked files appear; `web/dist`, node_modules, `.runtime`, uploads, data, and test output remain untracked/ignored.

- [ ] **Step 7: Commit**

```powershell
git add web/e2e tests/deploy/test_document_library_contract.py
git commit -m "test: verify document library vertical slice"
```

---

## Final Integration Review Gate

After all six tasks are committed and their task packets are marked `done`, Codex must inspect the combined diff and create:

`docs/agent-workflow/task-packets/2026-08-15-document-library-vertical-slice/FINAL_INTEGRATION_REVIEW.md`

The feature is not complete until that review result is `accepted`. If it finds any Critical or Important issue, Codex creates corrective packets rather than patching implementation during review.
