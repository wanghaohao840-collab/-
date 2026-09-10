# Document Search Vertical Slice Implementation Plan

> Execution: user-selected serial implementation in the existing isolated worktree. Follow repository-reviewed packets in order; no parallel agents. Steps use checkbox (`- [ ]`) syntax for tracking.

**2026-09-04 implementation correction:** Packet 01 now explicitly owns bounded public exact-chunk readers in both pipeline backends. Qdrant resolves namespace/document/index with one two-record page, rejecting ambiguity; JSON uses the exact logical ID in its resident store. Structured document data bypasses only error-text redaction, stays allowlisted, and is capped at 1200 characters while its SHA-256 binds the original full chunk. No ranking, model or index migration changes are authorized by this correction.

**Goal:** Replace `/search` with a production document-search workflow over 1–10 user-selected imported documents, including structured sources, QA handoff, and editable notes backed by a verified document-chunk source.

**Architecture:** React calls a CSRF-protected FastAPI search route, which delegates to a user-scoped `DocumentSearchService`; its adapter consumes structured output from the active RAG backend while preserving legacy text search. Direct search-to-note provenance uses an additive `note_document_sources` table projected into the existing `NoteSource` model, keeping old databases and old application images readable.

**Tech Stack:** Python 3.11, FastAPI/Pydantic, SQLite, existing JSON/Qdrant RAG implementations and BGE-M3 runtime, React 19/TypeScript, TanStack Query, Vitest/Testing Library, Playwright/axe, Docker Compose and Windows safe updater.

## Global Constraints

- Only search already imported documents; do not add external scholarly providers, OCR, GraphRAG, Neo4j, search history or another vector index.
- The request must explicitly contain 1–10 document IDs and a trimmed 1–1000 character query. Result limit is one of 5, 10 or 20.
- User identity and namespace come only from the authenticated session. Never accept a user ID, namespace, collection, model ID or server path from the client.
- Do not parse the legacy human-readable search string. Add structured result data while preserving that string for Gradio compatibility.
- Search is a read operation but uses POST + CSRF so query text is not placed in URLs. All successful responses use `Cache-Control: no-store`.
- Network embedding calls must not hold the per-user mutation lock. Validate document scope before and after search; a deletion race rejects stale results.
- A note source submitted by the browser contains identifiers/checksum only. Server code re-resolves and verifies the current chunk before persisting the snapshot.
- Add `note_document_sources`; do not rebuild or relax `note_sources`. Old application images must continue to open the upgraded database.
- Keep the stable product at `D:\python_self_agent` running during implementation. Work serially in the approved isolated worktree and publish only after combined acceptance.
- Preserve all unrelated dirty changes, runtime data, secrets, backups and existing BGE-M3 migration evidence. Do not start Neo4j.
- Do not commit or push unless the user separately authorizes it; mark all handoffs `not committed`.

---

### Task 1: Structured RAG search and application/API boundary

**Files:**
- Create: `app/document_search.py`
- Create: `api/schemas/search.py`
- Create: `api/routes/search.py`
- Modify: `hello_agents/tools/builtin/rag_tool.py:1352-1408`
- Modify: `app/bootstrap.py:20-180`
- Modify: `api/dependencies.py:1-45`
- Modify: `api/app.py:15-95`
- Test: `tests/tools/test_rag_tool_backend_contract.py`
- Test: `tests/test_document_search.py`
- Test: `tests/api/test_search_routes.py`
- Test: `tests/integration/test_qdrant_document_scope.py`

**Interfaces:**
- Consumes: `DocumentLibraryService.list_documents(session_token)`, session `runtime.rag_tool.execute_result("search", query=..., document_ids=..., limit=..., min_score=...)`, deletion-fence-aware document lists, and existing `RAGActionResult`.
- Produces: immutable `DocumentSearchResult`/`DocumentSearchHit`, structured internal `get_document_chunk` RAG action, `DocumentSearchService.search(session_token, request)`, `DocumentSearchService.resolve_chunk(session_token, locator)`, and `POST /api/v1/search`.

- [x] **Step 1: Add failing structured RAG contract tests**

Assert both JSON and Qdrant test doubles return `result.data["results"]` with `document_id`, `chunk_id`, integer `chunk_index`, raw `content`, metadata and numeric `score`, while `result.message` retains the legacy heading. Assert empty results produce `results=[]`, not a failure string that clients must parse.

```python
result = tool.execute_result("search", query="边界", document_ids=[document_id], limit=5)
assert result.success is True
assert result.data["results"][0]["document_id"] == document_id
assert result.data["results"][0]["chunk_index"] == 0
assert "找到" in result.message
```

- [x] **Step 2: Run focused tests and confirm the missing structured field**

Run: `venv/Scripts/python.exe -m pytest -q tests/tools/test_rag_tool_backend_contract.py tests/integration/test_qdrant_document_scope.py --basetemp=deploy-state/pytest-search-rag-red`

Expected: new assertions fail because search currently only formats text.

- [x] **Step 3: Publish safe structured RAG search data**

In the active `_search`, normalize each backend row through a small private helper and set `_last_action_data` before formatting the existing message. Require a non-empty document ID, stable chunk ID/index and bounded plain content; copy only allow-listed metadata. Do not change `_ask`, score calculation, backend selection or existing output wording.

```python
self._last_action_data = {
    "results": [self._structured_search_hit(item) for item in results],
    "result_count": len(results),
}
```

- [x] **Step 4: Define the application models and service with failing tests**

Use frozen dataclasses. `SearchRequest` validates trimmed query, unique 1–10 IDs and limit. `SearchChunkLocator` contains `document_id`, `chunk_id`, `chunk_index`, `content_sha256`. Service validation compares the entire requested set with the user’s available document map, calls RAG outside the runtime lock, then repeats availability validation. It maps names from `DocumentLibraryService`, not backend file paths.

```python
@dataclass(frozen=True)
class SearchChunkLocator:
    document_id: str
    chunk_id: str
    chunk_index: int
    content_sha256: str

@dataclass(frozen=True)
class DocumentSearchRequest:
    query: str
    document_ids: tuple[str, ...]
    limit: Literal[5, 10, 20] = 10
```

Test invalid/mixed-owner sets return one safe scope error, deleted-after-search returns `SEARCH_SCOPE_CHANGED`, backend timeout becomes retryable `SEARCH_UNAVAILABLE`, no hits remains success, and no runtime mutation lock is held during the test double’s search call. The service admits at most one request per user and four process-wide through a short protected active-user set plus bounded semaphore; saturation returns retryable `SEARCH_BUSY` and cleanup runs in `finally`.

- [x] **Step 5: Implement bounded current-chunk resolution**

`resolve_chunk` validates the document in the session library, then calls structured internal `runtime.rag_tool.execute_result("get_document_chunk", ...)`. The action uses the public exact-chunk backend reader described in the correction above, never the unbounded document reader. Compute SHA-256 on original content in the tool and verify all locator fields in the application service; the transmitted content is only a bounded excerpt. Do not use `app.rag_inventory` or cross-user scans. Repeat document/fence validation before returning.

- [x] **Step 6: Add FastAPI schema and route tests**

Request schema forbids extra fields. Response exposes safe excerpt (maximum 1200 characters), rank, score, page/section, document display name and locator, but not full content, namespace, path or collection. Verify authentication, CSRF, 422 input, 409 changing scope, 503 retryable backend, cross-user indistinguishability and no-store.

```python
@router.post("/api/v1/search", response_model=DocumentSearchResponse)
def search_documents(body: DocumentSearchRequest, ...):
    response.headers["Cache-Control"] = "no-store"
    return search_response(service.search(get_session_token(request), ...))
```

- [x] **Step 7: Wire application composition and pass focused verification**

Construct one `DocumentSearchService` in `ApplicationServices`, inject it through dependencies, register the router once, and preserve all existing service lifecycles.

Run: `venv/Scripts/python.exe -m pytest -q tests/tools/test_rag_tool_backend_contract.py tests/test_document_search.py tests/api/test_search_routes.py tests/integration/test_qdrant_document_scope.py --basetemp=deploy-state/pytest-search-service`

Expected: all selected tests pass; existing legacy search and QA contract assertions remain unchanged.

### Task 2: Backward-compatible document-chunk note provenance

**Files:**
- Modify: `app/database.py:285-320`
- Modify: `app/note_models.py:1-215`
- Modify: `app/note_repository.py:1-720`
- Modify: `app/note_service.py:45-405`
- Modify: `app/qa_deletion.py` only where it already calls source scrubbing
- Modify: `api/schemas/notes.py:1-180`
- Modify: `api/routes/notes.py:1-145`
- Modify: `app/bootstrap.py` to inject DocumentSearchService into NoteService
- Test: companion-schema upgrade checks in `tests/test_note_repository.py` (there is no standalone database test file).
- Test: `tests/test_note_models.py`
- Test: `tests/test_note_repository.py`
- Test: `tests/test_note_service.py`
- Test: `tests/test_note_source_deletion.py`
- Test: `tests/api/test_note_routes.py`

**Interfaces:**
- Consumes: Task 1 `SearchChunkLocator` and `DocumentSearchService.resolve_chunk(session_token, locator)`; current idempotent `NoteService.create`, `NoteRepository.create_in_transaction`, `NewNoteSource`, source scrubbing and projection outbox.
- Produces: request source kind `document_chunk`, additive `note_document_sources`, unified response source kind `document_chunk`, and search-result note prefill/save support.

- [x] **Step 1: Add upgrade and rollback-compatibility tests**

Create a database containing the current schema and real QA note source, rerun `initialize_database`, then assert `note_document_sources` exists and the existing row is byte-for-byte unchanged. Open the upgraded DB using SQL limited to old tables to prove old-image reads remain valid. Repeat initialization and assert idempotence.

- [x] **Step 2: Add the additive table**

Append only this responsibility to `SCHEMA`; never alter the old CHECK constraint.

```sql
create table if not exists note_document_sources (
  id text primary key,
  user_id text not null,
  note_id text not null,
  document_id text,
  chunk_id text,
  chunk_index integer check(chunk_index >= 0),
  content_sha256 text,
  locator_json text,
  title_snapshot text,
  excerpt_snapshot text,
  source_deleted_at text,
  created_at text not null,
  foreign key(note_id, user_id) references notes(id, user_id) on delete cascade
);
```

Add user/note and active user/document indexes, plus a CHECK requiring identity/checksum/excerpt on live rows and allowing redaction on deleted rows. `executescript` does not itself prove whole-script transactional atomicity: verify fresh, upgraded, repeated and interrupted initialization explicitly. Keep schema creation additive and retry-safe; do not claim an automatic enclosing transaction.

- [x] **Step 3: Expand the unified source model and repository tests**

Extend source literals and locator selector, keeping QA validation rules unchanged. Insert `document_chunk` into the additive table; `_get` loads both tables and sorts the unified result deterministically by `(created_at, id)`. Idempotency digest includes kind and locator identity, not server-resolved snapshot text.

```python
NoteSourceSelector = QaSourceSelector | DocumentChunkSourceSelector
DocumentChunkSourceSelector(kind="document_chunk", locator=SearchChunkLocator(...))
```

Test direct source creation, replay after later source deletion, cross-user IDs, more than ten sources, and old QA source parity.

- [x] **Step 4: Resolve sources server-side in NoteService**

Preserve the existing authenticated-session signature of `NoteService.create` and extend its selector. For `document_chunk`, perform early idempotent lookup, resolve outside the runtime mutation lock, and create `NewNoteSource` only from server-resolved data. The write transaction must reject active fences and a completed deletion that won the resolution/insertion race. Preserve old QA request digests and early replay after later source deletion.

- [x] **Step 5: Extend deletion scrubbing atomically**

Within the existing QA/document deletion transaction, mark active rows in both source tables deleted by user/document. Preserve note body, note version and projection semantics. Test pending/failed fences, completed deletion, different-user rows and the race between resolve and insert.

- [x] **Step 6: Extend notes API without trusting snapshots**

The browser may send only `kind=document_chunk` plus locator identifiers/checksum. Pydantic discriminated validation rejects QA fields on document sources and vice versa. Return title/excerpt/locator only from persisted server snapshots; deleted sources retain the existing redacted response convention.

- [x] **Step 7: Run note provenance verification**

Run: `venv/Scripts/python.exe -m pytest -q tests/test_note_models.py tests/test_note_repository.py tests/test_note_service.py tests/test_note_source_deletion.py tests/api/test_note_routes.py --basetemp=deploy-state/pytest-search-notes`

Expected: all selected tests pass; existing QA answer/citation note sources remain compatible.

### Task 3: Responsive React search workflow

**Files:**
- Create: `web/src/features/search/types.ts`
- Create: `web/src/features/search/api.ts`
- Create: `web/src/features/search/api.test.ts`
- Create: `web/src/features/search/queries.ts`
- Create: `web/src/features/search/queries.test.tsx`
- Create: `web/src/pages/SearchPage.tsx`
- Create: `web/src/pages/SearchPage.test.tsx`
- Create: `web/src/styles/search.css`
- Modify: `web/src/App.tsx`
- Modify: `web/src/main.tsx`
- Modify: `web/src/pages/QaPage.tsx` only for validated pending document-scope handoff
- Modify: `web/src/pages/NotesPage.tsx` and `web/src/features/notes/types.ts` only for validated document-source prefill
- Modify: `web/src/components/NotesWorkspace/NoteSourcePanel.tsx`, `web/src/components/NotesWorkspace/NotesWorkspace.tsx`, `web/src/components/NotesWorkspace/NotesWorkspace.test.tsx` only for document-source labels/filter options and editable prefill, as clarified in Packet 03.
- Modify: `web/src/pages/DocumentsPage.test.tsx` placeholder-route list
- Test: `web/e2e/search.spec.ts`

**Interfaces:**
- Consumes: Task 1 search DTO, existing document query, Task 2 note source request, and existing `/qa?documents=` handoff.
- Produces: real `/search` route with range selection, search states, results/details, citation copy, QA handoff and editable note draft.

- [x] **Step 1: Add API/query isolation tests**

Verify POST body/CSRF behavior through the shared authenticated request helper; query keys include username and normalized full request. Add an incrementing client request token or AbortController guard so late A cannot replace B; switching users never shows the prior user’s hits.

- [x] **Step 2: Add page tests for every state and action**

Cover no documents, unselected scope, ready, loading, success, empty, error/retry, changed input invalidating old actions, detail open/close, copy success/failure, QA handoff, note draft edit/save/open, and source-stale save. Do not use fabricated production fallback records.

- [x] **Step 3: Implement typed API/query layer**

Use a mutation rather than a cache-persistent GET query. Capture the originating username in mutation context and clear mutation state on user/request changes. Keep query/excerpt out of `localStorage` and route parameters.

- [x] **Step 4: Implement SearchPage and handoffs**

Render document checkboxes with 1–10 enforcement, query field, 5/10/20 selector and submit. Results show safe literal highlighting, metadata and actions. QA handoff routes to `/qa?documents=<id>` and opens the existing create-conversation confirmation. Reviewed implementation seam correction: the source preview endpoint does not exist, so the search detail panel owns an in-memory editable excerpt draft. After explicit server-backed creation it opens `/notes?note=<saved-id>`; source authority remains locator-only. No excerpt is stored in URL/history state. Retain the existing secondary `/legacy/` entry.

- [x] **Step 5: Add responsive accessible styles**

Use the existing desktop/tablet range-and-results columns and mobile single column. Implementation refinement: source details and the editable draft share a native focus-contained modal panel across viewports, bounded to 720 px on desktop/tablet and viewport width minus gutters on mobile; it scrolls independently. Keep 44 px targets, live regions, focus return, Escape handling and non-color status text using existing design tokens. This is an implementation refinement, not evidence of a Penpot source update.

- [x] **Step 6: Add three-viewport E2E**

Using isolated JSON/simple embeddings, import deterministic test documents, search only the chosen scope, open details, copy, launch QA, save/open note, then delete the source document and assert redacted/invalid source behavior. Include no horizontal overflow and axe serious/critical checks.

Reviewed prerequisite correction: modify `web/e2e/python-runtime.ts` and `web/tests/python-runtime.test.ts` so the shared child-process environment strips external service variables/Python overrides and sets `PYTHON_DOTENV_DISABLED=1`. All three fixture variants already consume the helper. This brings Playwright isolation into line with `tests/conftest.py`; do not alter production `.env` or lengthen readiness timeouts to mask unintended connections.

- [x] **Step 7: Verify frontend**

Run from `web`:

```powershell
npm test -- --run --maxWorkers=2
npm run typecheck
npm run lint
npm run build:app
npx playwright test e2e/search.spec.ts e2e/auth-shell.spec.ts e2e/visual.spec.ts
```

Expected: all commands pass at desktop 1440×1024, tablet 1024×768 and mobile 390×844. Update visual snapshots only for reviewed intentional `/search` changes.

### Task 4: Documentation, production publication and final integration review

**Files:**
- Modify: `docs/product-ui/README.md`
- Modify: `docs/product-ui/penpot-handoff.md` only after source/readback evidence exists
- Modify: `PROJECT_KNOWLEDGE.md`
- Modify: this plan’s task packet handoffs
- Create: `docs/agent-workflow/task-packets/2026-09-04-document-search/FINAL_INTEGRATION_REVIEW.md`

**Interfaces:**
- Consumes: completed Tasks 1–3, stable safe updater, current production BGE-M3 identity and Windows operation locks.
- Produces: stable published slice, reproducible evidence and accepted/changes-required/blocked integration decision.

- [x] **Step 1: Update truthful product documentation**

Record `/search` as implemented, distinguish local imported-document retrieval from future online literature discovery, document source invalidation, result limits, no history, and the additive schema’s rollback compatibility. Do not claim Penpot source changes unless fresh source readback proves them.

- [x] **Step 2: Run focused and combined regressions**

```powershell
venv/Scripts/python.exe -m pytest -q --tb=short --basetemp=deploy-state/pytest-search-final
npm --prefix web test -- --run --maxWorkers=2
npm --prefix web run typecheck
npm --prefix web run lint
npm --prefix web run build:app
npm --prefix web audit --audit-level=low
```

Expected: zero failures. Audit findings must be evaluated and fixed or explicitly block publication; do not bypass the security gate.

- [x] **Step 3: Integrate task-owned files into the stable directory**

Hash-compare source and destination, copy only reviewed task-owned files, never copy `.env`, data, caches, test output or worktree metadata, then rerun focused tests in `D:\python_self_agent`.

- [x] **Step 4: Publish through the safe updater**

```powershell
deploy/windows/Update-Deployment.ps1 -RepositoryRoot D:/python_self_agent -EnvFile D:/python_self_agent/deploy/.env -SkipNotification
```

Expected: cold backup, build, both configured image scan gates, App/Qdrant health, default and deep smoke all pass. Neo4j remains stopped. If database initialization or smoke fails, preserve reports and use updater rollback rather than manual partial recovery.

- [x] **Step 5: Verify real production search without retaining fixture data**

Use the reviewed Packet 04 isolated-runtime correction: inside the published App container, create a temporary database/user namespace with real BGE-M3/Qdrant and existing validated index metadata, assert scoped structured hits and note provenance/deletion, then verify namespace cleanup and dispose only the temporary root. The live database has no account-deletion API, so do not register a disposable account there or invent SQL cleanup. Verify published `/search` 200, unauthenticated `/api/v1/search` 401/403, served assets, loopback binding, POSIX volume and background health separately. Three-viewport E2E proves the browser QA-confirmation handoff; label these evidence types distinctly.

- [x] **Step 6: Complete mandatory final integration review**

Inspect the actual combined diff and packet handoffs for contracts, duplication, central wiring, persistence compatibility, cross-user isolation, deletion races, UI stale responses and production evidence. Write the required result using `FINAL_INTEGRATION_REVIEW_TEMPLATE.md`; the feature is complete only if the result is `accepted`.

## Self-review

- Spec coverage: all confirmed functional, security, migration, responsive, testing and publication requirements map to Tasks 1–4.
- Placeholder scan: no TBD/TODO or instruction requiring hidden conversation remains.
- Type consistency: `SearchChunkLocator` is produced by Task 1, consumed by Task 2 and represented by Task 3; the server re-resolves it. `document_chunk` persists only in the additive table and projects to the existing unified source response.
- Scope: online scholarly search and ranking upgrades remain separate; this plan produces a complete local-document retrieval product slice.
- Execution: the user already selected isolated, serial execution; no additional execution-choice prompt is required. Commits described by the generic skill are intentionally omitted because this repository requires explicit user authorization.
