# Zhiyan QA Vertical Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `/qa` migration placeholder with a persistent, responsive and safely recoverable QA product slice that supports fixed-document conversations, ordinary/joint/compare questions, durable multi-document summaries, immutable citations, legacy migration and cascading deletion.

**Architecture:** Add a versioned SQLite QA domain behind `QaService`, adapt the existing user runtime and RAG tool through a request-local `QaAnswerEngine`, and run summaries/deletions through lease-based durable workers owned by the single `ApplicationServices` lifecycle. Expose stable REST status resources now, then build a React/TanStack Query page from the approved Penpot desktop/tablet/mobile source without adding a second RAG stack or speculative distributed infrastructure.

**Tech Stack:** Python 3.12, SQLite, FastAPI, Pydantic, existing HelloAgents RAG/Memory runtime, React 19, React Router 7, TanStack Query 5, TypeScript 5.9, Vitest, Playwright, axe, Docker Compose, Penpot 2.17.1.

## Global Constraints

- Implement from clean base commit `7841e90` in `D:\python_self_agent\.worktrees\document-library-vertical-slice`; preserve the approved design at `docs/superpowers/specs/2026-08-25-qa-vertical-slice-design.md`.
- Use `D:\python_self_agent\venv\Scripts\python.exe` for Python commands and keep pytest `--basetemp` under this worktree's ignored `.runtime/` directory.
- Keep one `ApplicationServices`, one `SessionRegistry`, one user runtime/RAG namespace and one Uvicorn worker; do not add PostgreSQL, Redis, Celery, Kafka, SSE, WebSocket or a second QA/RAG pipeline in this slice.
- Ordinary, joint and compare questions are synchronous HTTP operations; multi-document summaries are durable background jobs with polling and cancellation.
- A conversation owns a fixed ordered scope of 1–10 ready documents; `compare` requires at least two; changing scope creates a new conversation.
- Persist every user message, assistant placeholder, terminal status, citation snapshot, rolling-summary boundary and idempotency key before exposing it as product state.
- Keep original messages permanently until an explicit cascade deletion; rolling-summary failure falls back to recent full turns and never fails the current question.
- Derive `user_id` only from the HttpOnly session Cookie; every mutation uses the existing `X-CSRF-Token` dependency and every resource lookup scopes by `user_id`, returning 404 across users.
- Stop product writes to `history.json.questions`; React and legacy Gradio must call the same `QaService` and reports/statistics must read the QA repository.
- Conversation deletion hard-deletes its messages, citations, summaries, jobs and linked QA Memory. Document deletion first establishes a deletion fence and then cascades all affected QA content, Memory, vectors and source files without late-result resurrection.
- Log only correlation IDs, state transitions, durations and safe error codes. Never log or return questions, answers, citation excerpts, prompts, credentials, absolute paths, Cookie/CSRF values or raw provider exceptions.
- Preserve existing document IDs, PDF page citations, JSON/Qdrant switching, GraphRAG behavior, Memory, reports, `/legacy/`, auth, import and document-library contracts except where this plan explicitly migrates QA history/deletion.
- Penpot file `3be9e5e1-190f-8090-8008-6ff3f3dcd54c` remains the product design source. Stop before any write if file/page/parent/component identity differs from the verified handoff.
- Use exact viewports desktop 1440×1024, tablet 1024×768 and mobile 390×844; mobile controls are at least 44×44 px and drawers trap/restore focus and close on Escape.
- Production and E2E code must not fabricate business counts, citations, filenames or answers. E2E fixtures may assert only data created by that test.
- Use RED → GREEN for every task, run the focused commands, commit only owned files, and stop on a plan/task-packet reality conflict.

---

## File Structure

### Penpot and design evidence

- Modify `docs/product-ui/penpot-handoff.md`: append verified QA boards, components, states, responsive behavior and deliberate differences.
- Modify `docs/product-ui/penpot-component-map.json`: map only implemented and freshly verified QA components.
- Create `docs/product-ui/reference/penpot/desktop-qa.png`, `desktop-qa-summary.png`, `desktop-qa-delete.png`, `tablet-qa.png`, `tablet-qa-sources.png`, `mobile-qa.png`, `mobile-qa-sources.png` and `mobile-qa-failure.png`.
- Create `tests/deploy/test_qa_product_contract.py`: tracked design/E2E/API documentation contract.

### QA domain and persistence

- Modify `app/database.py`: idempotent QA schema and indexes.
- Create `app/qa_models.py`: immutable records, enums, cursors and domain errors.
- Create `app/qa_repository.py`: user-scoped conversation/message/source persistence and conditional transitions.
- Create `app/qa_job_repository.py`: summary jobs, leases, cancellation and deletion fences.
- Create `tests/test_qa_models.py`, `tests/test_qa_repository.py` and `tests/test_qa_job_repository.py`.

### Answer generation and context

- Modify `hello_agents/tools/builtin/rag_tool.py`: request-local action result/error state for concurrent `execute_result()` calls.
- Modify `assistants/pdf_learning_assistant.py`: side-effect-free generation boundary; stop question-history writes.
- Create `app/qa_answer_engine.py`: typed adapter from user runtime to existing RAG.
- Create `app/qa_context.py`: token-budgeted rolling-summary plus recent-turn context.
- Create `app/qa_observability.py`: content-free structured events and replaceable metric counters.
- Create `tests/test_qa_answer_engine.py`, `tests/test_qa_context.py`, `tests/test_qa_observability.py`; modify focused RAG/Assistant tests.

### Services, workers, migration and deletion

- Create `app/qa_service.py`: conversation management, synchronous asks, retries, Memory linking and summary enqueue.
- Create `app/qa_worker.py`: durable summary worker and best-effort rolling-summary refresh.
- Create `app/qa_deletion.py`: deletion request service and durable deletion worker.
- Create `app/qa_memory.py`: deterministic, retryable QA Memory linking and unlinking.
- Create `app/qa_migration.py`: per-user idempotent `history.json.questions` migration.
- Modify `hello_agents/memory/manager.py` and `hello_agents/memory/types/episodic.py`: exact QA Memory removal by deterministic ID.
- Modify `app/document_library.py`, `app/bootstrap.py`, `app/runtime.py`, `ui/gradio_app.py`, `app/reports.py` and focused tests.

### FastAPI boundary

- Create `api/schemas/qa.py` and `api/routes/qa.py`.
- Modify `api/dependencies.py`, `api/routes/documents.py`, `api/schemas/documents.py`, `api/app.py`.
- Modify `api/errors.py`: optional safe trace ID in the common error envelope.
- Create `tests/api/test_qa_routes.py`; modify document/lifecycle/mount tests.

### React QA slice

- Create `web/src/features/qa/types.ts`, `api.ts`, `queries.ts` and focused query tests.
- Create `web/src/features/qa/components/ConversationList.tsx`, `MessageList.tsx`, `QaComposer.tsx`, `SourcePanel.tsx`, `QaDrawer.tsx`, `QaDeleteDialog.tsx` and focused component tests.
- Create `web/src/pages/QaPage.tsx`, `web/src/pages/QaPage.test.tsx` and `web/src/styles/qa.css`.
- Modify `web/src/App.tsx`, `web/src/main.tsx`, `web/src/components/DocumentList/DocumentList.tsx`, `web/src/pages/DocumentsPage.tsx` and focused document tests.

### Acceptance and operations

- Create `web/e2e/qa.spec.ts` and eight QA visual snapshots.
- Modify `web/e2e/accessibility.spec.ts`, `web/e2e/visual.spec.ts` and E2E fixtures only where the real QA slice needs reusable helpers.
- Modify `docs/product-ui/README.md`, `docs/product-ui/penpot-handoff.md`, `README.md` and deployment/contract tests.

---

### Task 1: Create and verify the Penpot QA source

**Files:**
- Modify: `docs/product-ui/penpot-handoff.md`
- Create: `docs/product-ui/reference/penpot/desktop-qa.png`
- Create: `docs/product-ui/reference/penpot/desktop-qa-summary.png`
- Create: `docs/product-ui/reference/penpot/desktop-qa-delete.png`
- Create: `docs/product-ui/reference/penpot/tablet-qa.png`
- Create: `docs/product-ui/reference/penpot/tablet-qa-sources.png`
- Create: `docs/product-ui/reference/penpot/mobile-qa.png`
- Create: `docs/product-ui/reference/penpot/mobile-qa-sources.png`
- Create: `docs/product-ui/reference/penpot/mobile-qa-failure.png`
- Create: `tests/deploy/test_qa_product_contract.py`

**Interfaces:**
- Consumes: approved `/qa` responsive wireframe, existing Tokens/AppShell/Button/Dialog/Drawer/TextField/Badge/Skeleton components and Penpot file ID `3be9e5e1-190f-8090-8008-6ff3f3dcd54c`.
- Produces: freshly verified QA board/component IDs and eight direct PNG exports used as the visual authority for Tasks 9–11.

- [ ] **Step 1: Write the failing tracked-design contract**

```python
from pathlib import Path
from PIL import Image


EXPECTED_QA_EXPORTS = {
    "desktop-qa.png": (1440, 1024),
    "desktop-qa-summary.png": (1440, 1024),
    "desktop-qa-delete.png": (1440, 1024),
    "tablet-qa.png": (1024, 768),
    "tablet-qa-sources.png": (1024, 768),
    "mobile-qa.png": (390, 844),
    "mobile-qa-sources.png": (390, 844),
    "mobile-qa-failure.png": (390, 844),
}


def test_qa_handoff_names_verified_reference_exports():
    handoff = Path("docs/product-ui/penpot-handoff.md").read_text(encoding="utf-8")
    reference_dir = Path("docs/product-ui/reference/penpot")
    for filename, size in EXPECTED_QA_EXPORTS.items():
        assert filename in handoff
        with Image.open(reference_dir / filename) as image:
            assert image.size == size
```

- [ ] **Step 2: Run the contract to verify RED**

```powershell
New-Item -ItemType Directory -Force .runtime | Out-Null
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/deploy/test_qa_product_contract.py --basetemp=.runtime/pytest-qa-penpot-red
```

Expected: FAIL because the QA handoff section and exports do not exist.

- [ ] **Step 3: Fresh-read and create the exact boards**

Use the connected Penpot MCP to verify the active file and the existing Desktop/Tablet/Mobile pages before writing. Reuse linked components and existing semantic tokens. Create board names exactly:

```text
Desktop / QA / Default
Desktop / QA / Summary running
Desktop / QA / Delete confirm
Tablet / QA / Default
Tablet / QA / Sources drawer
Mobile / QA / Default
Mobile / QA / Sources sheet
Mobile / QA / Failure retry
```

Desktop uses application navigation + recent conversations + chat + sources. Tablet keeps chat primary and moves conversations/sources to side drawers. Mobile uses one-column chat plus bottom sheets. Every board labels its content as design sample data.

- [ ] **Step 4: Verify geometry, linkage and exports**

Fresh-read every created board and assert:

```text
broken linked components: 0
text overflow: 0
actual bounds overflow: 0
mobile interactive targets: all >= 44 x 44
desktop/tablet/mobile dimensions: exact approved viewport
```

Export each board directly to its owned PNG, verify non-empty bytes and exact dimensions, and visually inspect all eight exports.

- [ ] **Step 5: Record handoff evidence and turn GREEN**

Append file revision, page/board/component IDs, state semantics, responsive transitions, token bindings, export paths and deliberate differences to `penpot-handoff.md`, then run:

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/deploy/test_qa_product_contract.py --basetemp=.runtime/pytest-qa-penpot-green
git diff --check
```

Expected: contract and diff check PASS.

- [ ] **Step 6: Commit**

```powershell
git add docs/product-ui/penpot-handoff.md docs/product-ui/reference/penpot tests/deploy/test_qa_product_contract.py
git commit -m "design: add Penpot QA source"
```

---

### Task 2: Add the QA schema, records and conversation repository

**Files:**
- Modify: `app/database.py`
- Create: `app/qa_models.py`
- Create: `app/qa_repository.py`
- Create: `tests/test_qa_models.py`
- Create: `tests/test_qa_repository.py`

**Interfaces:**
- Consumes: existing `connect()`/`transaction()` helpers, `users(id)` ownership and UUID string conventions.
- Produces: immutable `QaConversation`, `QaConversationDocument`, `QaMessage`, `QaSource`, `QaConversationAggregate`, `QaConversationPage`, `PendingTurn`; and `QaRepository` conversation/message/source APIs used by every later task.

- [ ] **Step 1: Write failing schema and model tests**

```python
def test_qa_schema_has_user_scoped_conversation_and_message_indexes(tmp_path):
    db_path = tmp_path / "app.db"
    initialize_database(db_path)
    with connect(db_path) as conn:
        tables = {
            row["name"]
            for row in conn.execute("select name from sqlite_master where type='table'")
        }
        indexes = {
            row["name"]
            for row in conn.execute("select name from sqlite_master where type='index'")
        }
    assert {"qa_conversations", "qa_conversation_documents", "qa_messages", "qa_message_sources"} <= tables
    assert {"ix_qa_conversations_user_recent", "ix_qa_messages_conversation_created"} <= indexes


def test_compare_requires_two_documents():
    with pytest.raises(QaValidationError) as exc_info:
        validate_mode("compare", (document("one"),))
    assert exc_info.value.code == "QA_COMPARE_REQUIRES_MULTIPLE_DOCUMENTS"
```

- [ ] **Step 2: Run RED**

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/test_qa_models.py tests/test_qa_repository.py --basetemp=.runtime/pytest-qa-repository-red
```

Expected: FAIL because QA tables, types and repository do not exist.

- [ ] **Step 3: Define exact public records and validation**

```python
QaMode = Literal["auto", "joint", "compare", "summary"]
QaMessageStatus = Literal["pending", "completed", "failed", "cancelled"]
QaSourceState = Literal["available", "none", "legacy_unavailable"]


@dataclass(frozen=True)
class QaDocumentScopeItem:
    document_id: str
    document_name: str
    position: int


@dataclass(frozen=True)
class PendingTurn:
    user_message: QaMessage
    assistant_message: QaMessage
    duplicate: bool


class QaValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
```

Also define immutable records for conversations, aggregate documents, messages, sources and cursor pages. Normalize IDs and mode in one place. Enforce 1–10 distinct documents, stable order, non-empty questions, 40-grapheme deterministic title and compare cardinality without importing FastAPI.

- [ ] **Step 4: Add idempotent SQLite tables and indexes**

Add `create table if not exists` statements for `qa_conversations`, `qa_conversation_documents`, `qa_messages` and `qa_message_sources`. Required constraints include:

```sql
unique(user_id, id);
check(role in ('user','assistant'));
check(status in ('pending','completed','failed','cancelled'));
check(mode is null or mode in ('auto','joint','compare','summary'));
foreign key(conversation_id, user_id)
  references qa_conversations(id, user_id) on delete cascade;
```

`qa_messages` includes `turn_id`, nullable `client_request_id`, `retry_of_message_id`, `source_state`, `memory_id`, `memory_sync_status` (`pending/running/completed/failed/not_required`), `memory_sync_attempt_count`, Memory-sync lease owner/expiry, `safe_error_code`, `trace_id`, `version`, and timestamps. Only the user message carries `client_request_id`; its paired assistant message uses the shared `turn_id`. Add deterministic recent-conversation, message-page, pending-memory-sync and source-order indexes. Running `initialize_database()` twice must be a no-op and preserve existing rows.

Enforce the active synchronous slot in SQLite as well as service code:

```sql
create unique index if not exists uq_qa_messages_pending_conversation
on qa_messages(user_id, conversation_id)
where role = 'assistant' and status = 'pending';

create unique index if not exists uq_qa_messages_client_request
on qa_messages(user_id, conversation_id, client_request_id)
where role = 'user' and client_request_id is not null;
```

- [ ] **Step 5: Write repository isolation, idempotency and transition tests**

```python
def test_duplicate_client_request_returns_original_pending_turn(repository, owner):
    conversation = repository.create_conversation(owner, scope("a.pdf"))
    first = repository.create_pending_turn(
        owner, conversation.id, "问题", "auto", "client-1"
    )
    duplicate = repository.create_pending_turn(
        owner, conversation.id, "问题", "auto", "client-1"
    )
    assert duplicate.duplicate is True
    assert duplicate.assistant_message.id == first.assistant_message.id


def test_late_completion_cannot_resurrect_deleted_conversation(repository, owner):
    conversation = repository.create_conversation(owner, scope("a.pdf"))
    pending = repository.create_pending_turn(owner, conversation.id, "问题", "auto", "c1")
    repository.hard_delete_conversation(owner, conversation.id)
    assert repository.complete_turn(
        owner, pending.assistant_message.id, pending.assistant_message.version,
        "answer", (), "none", None,
    ) is False
```

Cover user-scoped 404 behavior, immutable document scope, conversation pagination, message pagination, source ordering, one pending assistant per conversation, retry linkage, version mismatch, fail/cancel transitions and hard cascade deletion.

- [ ] **Step 6: Implement `QaRepository` with short conditional transactions**

Expose exact methods:

```python
create_conversation(user_id, documents, *, origin="product") -> QaConversationAggregate
list_conversations(user_id, *, cursor=None, limit=20) -> QaConversationPage
get_conversation(user_id, conversation_id) -> QaConversationAggregate | None
list_messages(user_id, conversation_id, *, cursor=None, limit=50) -> QaMessagePage
create_pending_turn(user_id, conversation_id, question, mode, client_request_id, *, retry_of_message_id=None) -> PendingTurn
complete_turn(user_id, assistant_message_id, expected_version, answer, sources, source_state, memory_id) -> bool
fail_turn(user_id, assistant_message_id, expected_version, error_code, trace_id) -> bool
recover_interrupted_questions(error_code="QA_REQUEST_INTERRUPTED") -> int
hard_delete_conversation(user_id, conversation_id) -> bool
```

Every update includes `user_id`, current status and expected version. `create_pending_turn()` uses `begin immediate`, enforces the one-active-generation rule, derives the title from the first valid question while holding the same transaction, and returns an existing idempotent request without another call.

- [ ] **Step 7: Run GREEN and database regression**

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/test_qa_models.py tests/test_qa_repository.py tests/test_p0_data_integrity.py --basetemp=.runtime/pytest-qa-repository-green
```

Expected: selected tests PASS and repeated database initialization preserves auth/import/report tables.

- [ ] **Step 8: Commit**

```powershell
git add app/database.py app/qa_models.py app/qa_repository.py tests/test_qa_models.py tests/test_qa_repository.py
git commit -m "feat: add persistent QA conversations"
```

---

### Task 3: Make RAG results request-local and add the QA answer adapter

**Files:**
- Modify: `hello_agents/tools/builtin/rag_tool.py`
- Modify: `assistants/pdf_learning_assistant.py`
- Create: `app/qa_answer_engine.py`
- Create: `tests/test_qa_answer_engine.py`
- Modify: `tests/tools/test_rag_tool_multi_document.py`
- Modify: `tests/assistants/test_pdf_learning_assistant_multi_document.py`
- Modify: `tests/test_history_repository.py`

**Interfaces:**
- Consumes: `RAGTool.execute_result()` for the `ask` action with query, document-scope and mode keyword arguments; existing document-scope/mode validation and Task 2 `QaMode`/`QaSource` projection types.
- Produces: `QaAnswerRequest`, `QaAnswerResult`, `QaAnswerEngine` protocol and `RagQaAnswerEngine.answer(runtime, request, progress_callback=None, cancel_event=None)` with no history or Memory side effects.

- [ ] **Step 1: Write concurrent-result and no-dual-write RED tests**

```python
def test_execute_result_keeps_sources_request_local_across_threads(rag_tool):
    first, second = run_two_barriered_asks(rag_tool, "one", "two")
    assert {item["document_id"] for item in first.data["sources"]} == {"doc-one"}
    assert {item["document_id"] for item in second.data["sources"]} == {"doc-two"}


def test_assistant_ask_no_longer_appends_history_questions(assistant):
    before = assistant.history_repository.load()["questions"]
    assistant.ask("question", selected_documents=[DOCUMENT_ID])
    after = assistant.history_repository.load()["questions"]
    assert after == before
```

Add adapter tests for normal, compare structured output, RAG failure classification, citation projection, progress/cancel forwarding and graph source projection.

- [ ] **Step 2: Run RED**

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/test_qa_answer_engine.py tests/tools/test_rag_tool_multi_document.py tests/assistants/test_pdf_learning_assistant_multi_document.py -k "request_local or dual_write or answer_engine or sources" --basetemp=.runtime/pytest-qa-engine-red
```

Expected: concurrent data crosses requests or the new adapter is missing, and Assistant still appends `history.json.questions`.

- [ ] **Step 3: Isolate action data and error per execution context**

Use `ContextVar`-backed accessors for `_last_action_data` and `_last_action_error` so the existing internal assignments remain compatible but two threads do not share values:

```python
self._action_data_context = ContextVar(
    f"rag_action_data_{id(self)}", default={}
)
self._action_error_context = ContextVar(
    f"rag_action_error_{id(self)}", default=None
)

@property
def _last_action_data(self) -> dict[str, Any]:
    return self._action_data_context.get()

@_last_action_data.setter
def _last_action_data(self, value: dict[str, Any]) -> None:
    self._action_data_context.set(dict(value or {}))
```

Mirror the pattern for `_last_action_error`. `execute_result()` continues returning `RAGActionResult`; legacy `execute()` output remains unchanged. Do not introduce one global or per-user generation lock as the final concurrency mechanism.

- [ ] **Step 4: Define and implement the typed adapter**

```python
@dataclass(frozen=True)
class QaAnswerRequest:
    question: str
    conversation_context: str
    document_ids: tuple[str, ...]
    mode: Literal["auto", "joint", "compare", "summary"]
    limit: int = 5
    structured_output: bool = False


@dataclass(frozen=True)
class QaSourceDraft:
    citation_id: str
    document_id: str
    document_name: str
    page_number: int | None
    section: str | None
    excerpt: str
    reference: str
    truncated: bool
    source_type: Literal["rag", "graph"]


@dataclass(frozen=True)
class QaAnswerResult:
    answer: str
    sources: tuple[QaSourceDraft, ...]
    mode: QaMode
    retryable: bool = False
    error_code: str | None = None


class QaEngineError(RuntimeError):
    def __init__(self, code: str, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


def source_draft(
    raw: Mapping[str, Any],
    source_type: Literal["rag", "graph"],
) -> QaSourceDraft:
    page = raw.get("page_number")
    return QaSourceDraft(
        citation_id=str(raw.get("citation_id") or ""),
        document_id=str(raw.get("document_id") or ""),
        document_name=str(raw.get("file_name") or raw.get("document_name") or ""),
        page_number=int(page) if isinstance(page, int) else None,
        section=str(raw.get("section") or "") or None,
        excerpt=str(raw.get("excerpt") or ""),
        reference=str(raw.get("reference") or ""),
        truncated=bool(raw.get("truncated")),
        source_type=source_type,
    )


class RagQaAnswerEngine:
    def answer(self, runtime, request, *, progress_callback=None, cancel_event=None):
        result = runtime.rag_tool.execute_result(
            "ask",
            query=request.question,
            conversation_context=request.conversation_context,
            document_ids=list(request.document_ids),
            mode=request.mode,
            limit=request.limit,
            min_score=0.12,
            structured_output=request.structured_output,
            progress_callback=progress_callback,
            cancel_event=cancel_event,
        )
        if not result.success:
            raise QaEngineError(
                result.error_code or "QA_ENGINE_UNAVAILABLE",
                bool(result.retryable),
            )
        sources = tuple(
            [source_draft(item, "rag") for item in result.data.get("sources", ())]
            + [source_draft(item, "graph") for item in result.data.get("graph_sources", ())]
        )
        return QaAnswerResult(
            answer=result.message,
            sources=sources,
            mode=request.mode,
        )
```

Only include optional kwargs when non-null if the existing RAG path distinguishes absence. Extend the RAG ask/prompt builders so `query` alone drives retrieval while `conversation_context` is added only to the final answer prompt under the existing token budget. Compare/joint prompt builders receive the same optional context; document-summary jobs pass an empty context. Map `data.sources` and `data.graph_sources` to safe drafts and reject a failed result with typed `QaEngineError(code, retryable)`. Add tests proving the retriever sees only the current question while the LLM prompt receives the persisted conversation context.

- [ ] **Step 5: Remove question-history persistence from generation**

Refactor `PDFLearningAssistant.ask()` to use the same request/result projection and return the legacy answer string, but remove `history_item` construction and the mutation that appends to `history["questions"]`. Keep document history, notes, imports and non-QA behavior unchanged. Product entry points will persist QA through `QaService`; no compatibility path may append new questions.

- [ ] **Step 6: Run GREEN and RAG/Assistant regressions**

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/test_qa_answer_engine.py tests/tools/test_rag_tool_multi_document.py tests/tools/test_rag_tool_graph.py tests/assistants/test_pdf_learning_assistant_multi_document.py tests/test_history_repository.py --basetemp=.runtime/pytest-qa-engine-green
```

Expected: all selected tests PASS, concurrent sources remain isolated and no new question record is written.

- [ ] **Step 7: Commit**

```powershell
git add hello_agents/tools/builtin/rag_tool.py assistants/pdf_learning_assistant.py app/qa_answer_engine.py tests/test_qa_answer_engine.py tests/tools/test_rag_tool_multi_document.py tests/assistants/test_pdf_learning_assistant_multi_document.py tests/test_history_repository.py
git commit -m "refactor: isolate QA answer generation"
```

---

### Task 4: Implement multi-turn context and synchronous `QaService`

**Files:**
- Create: `app/qa_context.py`
- Create: `app/qa_observability.py`
- Create: `app/qa_service.py`
- Create: `tests/test_qa_context.py`
- Create: `tests/test_qa_observability.py`
- Create: `tests/test_qa_service.py`

**Interfaces:**
- Consumes: Task 2 `QaRepository`, Task 3 `QaAnswerEngine`, current `SessionRegistry`, existing token/context-budget helpers and `DocumentLibraryService.list_documents()`.
- Produces: `QaContextBuilder`, `QaConversationSummarizer` protocol and `QaService` conversation/list/message/ask/retry interfaces. Later tasks add summary jobs, deletion and legacy migration without changing these public method names.

- [ ] **Step 1: Write context and service RED tests**

```python
def test_context_uses_summary_then_only_messages_after_boundary(builder):
    context = builder.build(
        rolling_summary="早期结论",
        summary_through_message_id="a2",
        messages=turns("u1", "a1", "u2", "a2", "u3", "a3"),
        current_question="继续比较",
    )
    assert "早期结论" in context
    assert "u3" in context and "a3" in context
    assert "u1" not in context and "a1" not in context
    assert context.endswith("继续比较")


def test_duplicate_request_does_not_call_engine_twice(qa_service, engine):
    conversation = qa_service.create_conversation(TOKEN, (DOC_ID,))
    first = qa_service.ask(TOKEN, conversation.id, "问题", "auto", "client-1")
    second = qa_service.ask(TOKEN, conversation.id, "问题", "auto", "client-1")
    assert second.id == first.id
    assert engine.call_count == 1
```

Cover fixed-scope validation, compare cardinality, cross-user missing behavior, one active generation, failed safe code/trace ID, retry creating a new assistant message, disconnect recovery through repository state and deterministic title.

- [ ] **Step 2: Run RED**

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/test_qa_context.py tests/test_qa_service.py --basetemp=.runtime/pytest-qa-service-red
```

Expected: FAIL because context and service modules are absent.

- [ ] **Step 3: Implement the token-budgeted context builder**

```python
@dataclass(frozen=True)
class QaContextWindow:
    rendered: str
    included_message_ids: tuple[str, ...]
    used_summary: bool
    truncated: bool


class QaContextBuilder:
    def __init__(self, max_input_tokens: int) -> None:
        self.max_input_tokens = max(1, int(max_input_tokens))

    def build(
        self,
        *,
        rolling_summary: str | None,
        summary_through_message_id: str | None,
        messages: Sequence[QaMessage],
        current_question: str,
    ) -> QaContextWindow:
        after_boundary = summary_through_message_id is None
        grouped: dict[str, list[QaMessage]] = {}
        for message in messages:
            if message.id == summary_through_message_id:
                after_boundary = True
                continue
            if after_boundary and message.status == "completed":
                grouped.setdefault(message.turn_id, []).append(message)
        complete_turns = [
            sorted(items, key=lambda item: (item.role != "user", item.created_at))
            for items in grouped.values()
            if {item.role for item in items} == {"user", "assistant"}
        ]

        def render(items: Sequence[QaMessage], summary: str | None) -> str:
            parts = [f"历史摘要：{summary}"] if summary else []
            parts.extend(
                f"{'用户' if item.role == 'user' else '助手'}：{item.content}"
                for item in items
            )
            parts.append(f"当前问题：{current_question}")
            return "\n\n".join(parts)

        recent = [item for turn in complete_turns for item in turn]
        rendered = render(recent, rolling_summary)
        truncated = False
        while complete_turns and estimate_tokens(rendered) > self.max_input_tokens:
            complete_turns.pop(0)
            recent = [item for turn in complete_turns for item in turn]
            truncated = True
            rendered = render(recent, rolling_summary)
        if estimate_tokens(rendered) > self.max_input_tokens and rolling_summary:
            keep = max(0, len(rolling_summary) // 2)
            while keep and estimate_tokens(rendered) > self.max_input_tokens:
                keep //= 2
                rendered = render(recent, rolling_summary[-keep:] if keep else None)
            truncated = True
        return QaContextWindow(
            rendered=rendered,
            included_message_ids=tuple(item.id for item in recent),
            used_summary=bool(rolling_summary),
            truncated=truncated,
        )
```

Use the existing `estimate_tokens()` and model context/output reserve/safety margin. Always retain the full current question, then recent complete turns from newest to oldest, then the persisted summary. Render explicit `用户:`/`助手:` boundaries. Failed, cancelled and pending assistant messages are not context. If the summary alone exceeds budget, truncate it without deleting stored data.

- [ ] **Step 4: Define service errors and document-scope projection**

```python
class QaNotFoundError(LookupError):
    pass


class QaBusyError(RuntimeError):
    code = "QA_CONVERSATION_BUSY"


class QaEngineUnavailableError(RuntimeError):
    code = "QA_ENGINE_UNAVAILABLE"

    def __init__(self, trace_id: str) -> None:
        super().__init__(self.code)
        self.trace_id = trace_id
```

`QaService.create_conversation()` obtains the authenticated session, projects the current user's `DocumentLibraryItem`s by ID, preserves caller order, rejects missing/not-ready IDs as not found, and calls the repository. Do not accept `user_id`, document names or paths from a body.

Add a replaceable `QaTelemetry` port and default in-process/logging implementation:

```python
class QaTelemetry(Protocol):
    def record(
        self,
        event: str,
        *,
        duration_ms: float | None = None,
        error_code: str | None = None,
        conversation_id: str | None = None,
        message_id: str | None = None,
        job_id: str | None = None,
    ) -> None:
        pass

    def snapshot(self) -> Mapping[str, int]:
        pass
```

The concrete implementation uses a lock-protected counter and structured logger fields. It rejects unexpected event names and has no parameter for question, answer, source excerpt, prompt or path. Service/worker/deletion tasks record latency, success/failure, idempotent hits, busy conflicts, retries, cancels, summary fallback and deletion retry through this port.

- [ ] **Step 5: Implement the synchronous ask transaction boundaries**

```python
def ask(self, session_token, conversation_id, question, mode, client_request_id):
    session = self.session_registry.get_session(session_token)
    user_id = str(session.user_id)
    conversation = self._require_conversation(user_id, conversation_id)
    validate_mode(mode, conversation.documents)
    pending = self.repository.create_pending_turn(
        user_id, conversation_id, question, mode, client_request_id
    )
    if pending.duplicate:
        return self.repository.get_message(user_id, pending.assistant_message.id)
    context = self.context_builder.build(
        rolling_summary=conversation.rolling_summary,
        summary_through_message_id=conversation.summary_through_message_id,
        messages=self.repository.list_context_messages(user_id, conversation_id),
        current_question=question,
    )
    try:
        result = self.answer_engine.answer(
            session.runtime,
            QaAnswerRequest(
                question=question,
                conversation_context=context.rendered,
                document_ids=tuple(item.document_id for item in conversation.documents),
                mode=mode,
                structured_output=mode == "compare",
            ),
        )
    except QaEngineError as error:
        trace_id = secrets.token_urlsafe(12)
        self.repository.fail_turn(
            user_id,
            pending.assistant_message.id,
            pending.assistant_message.version,
            error.code or "QA_ENGINE_UNAVAILABLE",
            trace_id,
        )
        raise QaEngineUnavailableError(trace_id) from error
    committed = self.repository.complete_turn(
        user_id,
        pending.assistant_message.id,
        pending.assistant_message.version,
        result.answer,
        result.sources,
        "available" if result.sources else "none",
        None,
    )
    if not committed:
        raise QaNotFoundError(conversation_id)
    return self.repository.get_message(user_id, pending.assistant_message.id)
```

Model/RAG work is outside transactions. A false conditional completion means cancellation/deletion won and the answer is discarded. Unknown exceptions follow the same persisted `QA_ENGINE_UNAVAILABLE` path and log only trace/type, never content.

- [ ] **Step 6: Implement retry and recovery**

`retry()` accepts only the current user's failed assistant message with a retryable safe code, reuses its user question, creates a new assistant message linked through `retry_of_message_id`, and executes once. `ApplicationServices.start()` will call `recover_interrupted_questions()` in Task 8; implement this as one conditional update that turns only pre-start synchronous `pending` messages with no joined `qa_jobs` row in `queued/running` into `failed/QA_REQUEST_INTERRUPTED`. Pending `mode=summary` messages backed by active jobs remain owned by job recovery. Add a restart test containing one sync pending turn and one queued summary turn: only the sync turn fails.

- [ ] **Step 7: Run GREEN**

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/test_qa_context.py tests/test_qa_observability.py tests/test_qa_service.py tests/test_assistant_user_isolation.py --basetemp=.runtime/pytest-qa-service-green
```

Expected: selected tests PASS; fake engine proves LLM work occurs outside repository transactions and duplicate requests call it once.

- [ ] **Step 8: Commit**

```powershell
git add app/qa_context.py app/qa_observability.py app/qa_service.py tests/test_qa_context.py tests/test_qa_observability.py tests/test_qa_service.py
git commit -m "feat: add synchronous QA service"
```

---

### Task 5: Add durable summary jobs and rolling-context refresh

**Files:**
- Modify: `app/database.py`
- Modify: `app/qa_models.py`
- Create: `app/qa_job_repository.py`
- Create: `app/qa_worker.py`
- Modify: `app/qa_service.py`
- Modify: `app/qa_context.py`
- Create: `tests/test_qa_job_repository.py`
- Create: `tests/test_qa_worker.py`
- Modify: `tests/test_qa_service.py`
- Modify: `tests/test_qa_context.py`

**Interfaces:**
- Consumes: Task 4 `QaService`, `QaContextBuilder`, Task 3 answer engine, `UserRuntimeRegistry.acquire_background()` and import-worker lease/recovery conventions.
- Produces: `QaJobRepository`, `QaWorkerPool`, `QaService.start_summary()`, `get_job()`, `cancel_job()` and a deduplicated best-effort rolling-summary refresh queue.

- [ ] **Step 1: Write job state, lease and restart RED tests**

```python
def test_expired_running_job_is_reclaimed_once(job_repository, clock):
    enqueue = job_repository.create_summary_turn_and_job(
        OWNER, CONVERSATION_ID, "总结文档", "client-summary-1"
    )
    job = enqueue.job
    first = job_repository.claim_next("worker-a", lease_seconds=30, now=clock.now())
    clock.advance(seconds=31)
    second = job_repository.claim_next("worker-b", lease_seconds=30, now=clock.now())
    assert first.id == second.id == job.id
    assert second.lease_owner == "worker-b"
    assert second.attempt_count == 2


def test_cancel_after_model_return_wins_before_commit(worker_fixture):
    worker_fixture.pause_after_answer()
    worker_fixture.start_one_summary()
    worker_fixture.repository.request_cancel(OWNER, JOB_ID)
    worker_fixture.release_answer()
    assert worker_fixture.job().status == "cancelled"
    assert worker_fixture.assistant_message().status == "cancelled"
```

Cover queued/running cancellation, heartbeat ownership, max attempts, one active generation per conversation, cross-user lookup, progress bounds, conditional completion and process-start recovery.

- [ ] **Step 2: Run RED**

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/test_qa_job_repository.py tests/test_qa_worker.py tests/test_qa_service.py -k "summary or lease or cancel or rolling" --basetemp=.runtime/pytest-qa-worker-red
```

Expected: FAIL because jobs and worker do not exist.

- [ ] **Step 3: Add exact job schema and records**

Create `qa_jobs` with status check `queued/running/completed/failed/cancelled`, stage, progress 0–100, cancellation timestamp, attempt/max-attempt counts, lease owner/expiry, message IDs, safe error/trace and timestamps. Add:

```sql
create unique index if not exists uq_qa_jobs_active_conversation
on qa_jobs(user_id, conversation_id)
where status in ('queued','running');

create index if not exists ix_qa_jobs_scheduler
on qa_jobs(status, lease_expires_at, created_at);
```

Define immutable `QaJob` plus the atomic enqueue result without duplicating conversation/message records:

```python
@dataclass(frozen=True)
class SummaryEnqueueResult:
    pending: PendingTurn
    job: QaJob
    duplicate: bool
```

- [ ] **Step 4: Implement lease-based job repository**

Expose:

```python
create_summary_turn_and_job(user_id, conversation_id, question, client_request_id) -> SummaryEnqueueResult
claim_next(worker_id, *, lease_seconds, now=None) -> QaJob | None
heartbeat(job_id, worker_id, *, progress, stage, now=None) -> bool
request_cancel(user_id, job_id, *, now=None) -> QaJob | None
complete(job_id, worker_id, *, now=None) -> bool
fail_or_retry(job_id, worker_id, error_code, trace_id, *, now=None) -> QaJob
recover_expired(*, now=None) -> int
get(user_id, job_id) -> QaJob | None
```

`create_summary_turn_and_job()` creates/reuses the user message, pending assistant message and queued job inside one `begin immediate` transaction; a job insert failure cannot leave an orphan pending turn. Use `begin immediate` for claiming and all arbitration. A terminal job never transitions again. Progress callbacks from a stale lease return false and cannot mutate state.

- [ ] **Step 5: Implement the worker lifecycle**

`QaWorkerPool` follows `ImportWorkerPool`: bounded threads, wake event, explicit start/stop, unique worker IDs and `UserRuntimeRegistry.acquire_background/release_background`. For a summary job it loads the fixed scope, calls the answer engine with `mode="summary"`, forwards sanitized progress heartbeats, checks cancel/fence after the model returns, conditionally completes the assistant message, then marks the job completed. Retry only safe transient engine failures and cap attempts at 3.

- [ ] **Step 6: Add service summary methods**

```python
def start_summary(self, session_token, conversation_id, instruction, client_request_id):
    session = self.session_registry.get_session(session_token)
    enqueue = self.job_repository.create_summary_turn_and_job(
        str(session.user_id), conversation_id,
        instruction or "总结这些文档的核心内容、共识、分歧与证据。",
        client_request_id,
    )
    self.worker_pool.notify()
    return enqueue.job
```

Duplicate `client_request_id` returns the existing message/job. `get_job()` and `cancel_job()` scope by user; cancellation also conditionally cancels the pending assistant message.

- [ ] **Step 7: Implement rolling-summary refresh without blocking answers**

Create `QaConversationSummarizer.summarize(runtime, previous_summary, completed_turns) -> str`. The worker pool owns an in-memory deduplicated refresh queue keyed by `(user_id, conversation_id)` because losing this optimization is safe: the next completed answer schedules it again. Generate outside transactions, then call:

```python
repository.update_rolling_summary(
    user_id,
    conversation_id,
    expected_conversation_version,
    summary,
    through_message_id,
)
```

If generation or conditional update fails, retain the old summary, clear the dedupe key and increment/log only a safe failure metric. Context building already falls back to recent full turns.

- [ ] **Step 8: Run GREEN and worker shutdown regression**

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/test_qa_job_repository.py tests/test_qa_worker.py tests/test_qa_service.py tests/test_qa_context.py tests/api/test_app_lifecycle.py --basetemp=.runtime/pytest-qa-worker-green
```

Expected: all selected tests PASS; stop cancels/wakes owned threads without closing shared runtimes still leased elsewhere.

- [ ] **Step 9: Commit**

```powershell
git add app/database.py app/qa_models.py app/qa_job_repository.py app/qa_worker.py app/qa_service.py app/qa_context.py tests/test_qa_job_repository.py tests/test_qa_worker.py tests/test_qa_service.py tests/test_qa_context.py
git commit -m "feat: add durable QA summaries"
```

---

### Task 6: Implement deletion fences, QA Memory cleanup and document cascade

**Files:**
- Modify: `app/database.py`
- Create: `app/qa_deletion.py`
- Create: `app/qa_memory.py`
- Modify: `app/qa_repository.py`
- Modify: `app/qa_job_repository.py`
- Modify: `app/qa_worker.py`
- Modify: `app/qa_service.py`
- Modify: `app/document_library.py`
- Modify: `hello_agents/memory/manager.py`
- Modify: `hello_agents/memory/types/episodic.py`
- Create: `tests/test_qa_deletion.py`
- Create: `tests/test_qa_memory.py`
- Modify: `tests/test_qa_repository.py`
- Modify: `tests/test_document_library_service.py`
- Modify: `tests/memory/test_episodic_vector_cleanup.py`

**Interfaces:**
- Consumes: deterministic `qa_messages.memory_id`, Task 5 worker lifecycle patterns and existing coordinated document deletion.
- Produces: `QaDeletionService`, `QaDeletionWorker`, `QaService.delete_conversation()`, document deletion fence/count/status and exact episodic Memory removal.

- [ ] **Step 1: Write deletion-race and Memory RED tests**

```python
def test_document_fence_hides_document_and_blocks_late_answer(deletion_fixture):
    deletion_fixture.pause_answer_before_commit()
    deletion = deletion_fixture.request_document_delete(DOC_ID)
    assert DOC_ID not in {item.document_id for item in deletion_fixture.list_documents()}
    deletion_fixture.release_answer()
    assert deletion_fixture.message_exists() is False
    assert deletion_fixture.answer_was_committed() is False
    assert deletion.status == "queued"


def test_remove_memory_deletes_episodic_sqlite_vector_and_snapshot(memory_manager):
    memory_id = memory_manager.add_memory(
        "answer", "episodic", metadata={"qa_message_id": "m1"}, memory_id="qa-m1"
    )
    assert memory_manager.remove_memory(memory_id, memory_type="episodic") is True
    assert memory_manager.remove_memory(memory_id, memory_type="episodic") is False
```

Cover conversation and document cascade, cross-user 404, in-flight summary cancellation, durable Memory-link retry, retry after Memory/vector/file failure, opaque fence contents, and completed deletion idempotence.

- [ ] **Step 2: Run RED**

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/test_qa_deletion.py tests/test_qa_memory.py tests/test_document_library_service.py tests/memory/test_episodic_vector_cleanup.py --basetemp=.runtime/pytest-qa-delete-red
```

Expected: FAIL because deletion fences and exact Memory removal are absent.

- [ ] **Step 3: Add deletion-fence schema and repository methods**

Create `qa_deletion_fences` with `id`, `user_id`, `target_type`, opaque `target_id`, `status`, `stage`, opaque `memory_ids_json`, attempts, lease fields, safe error/trace and timestamps. Never store question/answer/source/path content. Add indexes for target fencing and scheduler claiming.

```python
@dataclass(frozen=True)
class QaDeletion:
    id: str
    user_id: str
    target_type: Literal["conversation", "document"]
    target_id: str
    status: Literal["queued", "running", "completed", "failed"]
    stage: str
    affected_conversation_count: int
    attempt_count: int
    safe_error_code: str | None
    trace_id: str | None
    created_at: str
    updated_at: str
```

Expose:

```python
create_conversation_deletion(user_id, conversation_id) -> QaDeletion
create_document_deletion(user_id, document_id, affected_conversation_ids) -> QaDeletion
has_active_fence(user_id, target_type, target_id) -> bool
claim_next_deletion(worker_id, lease_seconds, now=None) -> QaDeletion | None
advance_deletion(deletion_id, worker_id, expected_stage, next_stage) -> bool
complete_deletion(deletion_id, worker_id) -> bool
fail_or_retry_deletion(deletion_id, worker_id, error_code, trace_id, *, now=None) -> QaDeletion
get_deletion(user_id, deletion_id) -> QaDeletion | None
```

Conversation/message/job creation and completion queries must reject or no-op when a conversation or any scoped document has an active fence.

- [ ] **Step 4: Add exact episodic removal**

Implement `EpisodicMemory.remove(memory_id: str) -> bool` by calling its existing failure-atomic `_delete_episode_ids([memory_id])`, removing `_episodes`/session membership, and preserving SQLite/vector consistency. Implement:

```python
def remove_memory(self, memory_id: str, *, memory_type: str) -> bool:
    module = self.memory_types.get(memory_type)
    if module is None or not hasattr(module, "remove"):
        return False
    removed = bool(module.remove(memory_id))
    if removed:
        self._save_snapshot()
    return removed
```

QA stores only deterministic episodic Memory IDs. Do not add a broad metadata delete or clear unrelated memories.

- [ ] **Step 5: Add durable deterministic QA Memory linking**

`QaMemoryLinker` computes `qa-<uuid5(user_id:assistant_message_id)>`, writes exactly one episodic memory with `conversation_id`, `qa_message_id`, fixed document IDs and `event_type="pdf_qa"`, then conditionally attaches the ID and marks `memory_sync_status=completed`. Completed answers enter `memory_sync_status=pending`; `QaWorkerPool` atomically claims `pending`, retryable `failed`, or lease-expired `running` records by setting `running`, lease owner/expiry and incremented attempts inside `begin immediate`. Only the current lease owner may complete or fail a claim. A failure increments attempts, marks `failed`, clears the lease, emits a content-free metric and leaves the completed answer intact. Before and after Memory write, check the deletion fence; if attach loses a deletion race, remove the deterministic memory immediately.

Expose repository methods:

```python
claim_next_memory_sync(worker_id, *, lease_seconds, now=None) -> QaMessage | None
heartbeat_memory_sync(message_id, worker_id, *, lease_seconds, now=None) -> bool
complete_memory_sync(user_id, message_id, worker_id, memory_id, expected_version) -> bool
fail_memory_sync(user_id, message_id, worker_id, expected_version, safe_error_code) -> bool
```

Startup/wake scans make failures compensatable without a new generic event bus. Tests must prove two workers cannot own the same live claim, an expired lease can be reclaimed once, and a stale owner cannot attach or fail Memory after lease loss.

- [ ] **Step 6: Implement deletion service and worker stages**

`QaDeletionService.request_conversation()` and `request_document()` validate the current owner, create the fence in a short transaction, request cancellation, notify the worker and return the deletion record plus affected-conversation count. The worker stages are exact and idempotent:

```text
fenced -> qa_rows_removed -> memory_removed -> document_removed -> completed
```

Conversation deletion skips `document_removed`. Before removing QA rows, snapshot only opaque linked Memory IDs into the fence. On retry, each stage can be repeated. Result callbacks check fences and cannot recreate rows.

- [ ] **Step 7: Refactor document deletion behind a background-safe method**

Split `DocumentLibraryService.delete_document()` into authenticated request/fence creation and `perform_document_delete(user_id, runtime, document_id)` containing the existing safe preflight/coordinated delete. `list_documents()` filters active document fences immediately. The deletion worker obtains the runtime via `acquire_background()`, calls `perform_document_delete`, clears exact active selections, then releases the background lease.

- [ ] **Step 8: Run GREEN and destructive-operation regressions**

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/test_qa_deletion.py tests/test_qa_memory.py tests/test_qa_repository.py tests/test_document_library_service.py tests/test_user_mutation_coordination.py tests/memory/test_episodic_vector_cleanup.py --basetemp=.runtime/pytest-qa-delete-green
```

Expected: selected tests PASS; deletion failures leave a retryable fence, no unrelated document/conversation/Memory changes, and no late result resurrection.

- [ ] **Step 9: Commit**

```powershell
git add app/database.py app/qa_deletion.py app/qa_memory.py app/qa_repository.py app/qa_job_repository.py app/qa_worker.py app/qa_service.py app/document_library.py hello_agents/memory/manager.py hello_agents/memory/types/episodic.py tests/test_qa_deletion.py tests/test_qa_memory.py tests/test_qa_repository.py tests/test_document_library_service.py tests/memory/test_episodic_vector_cleanup.py
git commit -m "feat: add safe QA cascade deletion"
```

---

### Task 7: Migrate legacy questions and route Gradio/reporting through `QaService`

**Files:**
- Modify: `app/database.py`
- Create: `app/qa_migration.py`
- Modify: `app/qa_repository.py`
- Modify: `app/qa_service.py`
- Modify: `ui/gradio_app.py`
- Modify: `assistants/pdf_learning_assistant.py`
- Modify: `app/reports.py`
- Create: `tests/test_qa_migration.py`
- Modify: `tests/ui/test_authenticated_handlers.py`
- Modify: `tests/ui/test_summary_polling.py`
- Modify: `tests/test_report_service.py`
- Modify: `tests/test_legacy_migration.py`

**Interfaces:**
- Consumes: per-user `HistoryRepository`, Task 4/5 `QaService`, current Gradio `_HandlerBindings` and existing report/statistics handlers.
- Produces: `QaLegacyMigrationService.ensure_user_migrated()`, repository report projections and Gradio compatibility handlers backed only by QA persistence.

- [ ] **Step 1: Write idempotent/truthful migration RED tests**

```python
def test_each_flat_legacy_question_becomes_one_single_turn_conversation(migrator, history):
    history.save({
        **EMPTY_HISTORY,
        "questions": [legacy_question("Q1", "A1"), legacy_question("Q2", "A2")],
    })
    first = migrator.ensure_user_migrated(OWNER, history)
    second = migrator.ensure_user_migrated(OWNER, history)
    assert first.imported_count == 2
    assert second.imported_count == 2
    assert len(migrator.repository.list_conversations(OWNER).items) == 2
    assert all(len(migrator.repository.list_messages(OWNER, item.id).items) == 2
               for item in migrator.repository.list_conversations(OWNER).items)
```

Add tests for source state `legacy_unavailable`, fixed normalized document order, invalid records counted/skipped without fake IDs, digest/version rerun, Gradio creating `origin=legacy_gradio`, and report/statistics parity.

- [ ] **Step 2: Run RED**

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/test_qa_migration.py tests/ui/test_authenticated_handlers.py tests/ui/test_summary_polling.py tests/test_report_service.py --basetemp=.runtime/pytest-qa-migration-red
```

Expected: FAIL because migration and shared Gradio service path are absent.

- [ ] **Step 3: Add migration ledger and deterministic import**

Create `qa_legacy_imports(user_id primary key, migration_version, source_digest, imported_count, skipped_count, completed_at)` with a user FK. Hash a canonical JSON projection of `questions[]`, not paths or secrets. `ensure_user_migrated()` uses `begin immediate`; each valid record becomes one `origin=legacy_json` conversation with one completed user/assistant turn and `source_state=legacy_unavailable`. It never groups unrelated records or injects them into rolling context.

After a successful migration, keep the original history file as a non-authoritative rollback source for one release cycle. Product/Gradio/report paths never append new questions. Privacy operations are the exception: conversation/document deletion must remove the affected legacy question/document entries and any owned rollback copy so deleted content is not retained merely for rollback.

- [ ] **Step 4: Add repository report projections**

Define and expose a safe projection:

```python
@dataclass(frozen=True)
class QaReportTurn:
    question: str
    answer: str
    document_ids: tuple[str, ...]
    document_names: tuple[str, ...]
    mode: QaMode
    asked_at: str
```

Expose `list_completed_turns_for_report(user_id: str) -> tuple[QaReportTurn, ...]`. The repository implementation returns only completed pairs with document snapshots, mode and timestamps. Update reporting/statistics entry points to consume this projection. Do not expose repository rows or absolute paths to Gradio.

- [ ] **Step 5: Route Gradio question/summary handlers through services**

Replace `_require_assistant(session_token).ask()` in `ask_pdf()` with:

```python
services = _get_bindings().services
conversation = services.qa_service.create_legacy_single_turn_conversation(
    session_token, selected_pdf or []
)
message = services.qa_service.ask(
    session_token,
    conversation.id,
    question,
    qa_mode or "auto",
    str(uuid.uuid4()),
)
return message.content
```

`ask_pdf_with_sources()` formats `message.sources` rather than `_last_action_data`. Summary start/poll/cancel call `QaService` job methods. Preserve existing Gradio return shapes and safe Chinese messages. If no conversation ID exists in legacy state, one submission remains one truthful single-turn conversation.

After the Gradio switch, production and Gradio callers no longer use `PDFLearningAssistant.start_summary_task()`, `get_summary_task()` or `cancel_summary_task()`. Keep those methods and `app/summary_tasks.py` for one release as explicitly deprecated direct-Python compatibility, with tests proving the product path never reaches them. Document their removal as a later breaking cleanup; do not maintain or extend the in-memory manager in this slice.

- [ ] **Step 6: Switch report/statistics QA input and prove no dual write**

Pass `QaReportTurn` projections into the Assistant/report formatter rather than reading `history["questions"]`. Add a test that asks through both React-equivalent service and Gradio handler, then asserts `history.json.questions` is unchanged and both appear exactly once in repository reports.

- [ ] **Step 7: Run GREEN and legacy regression**

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/test_qa_migration.py tests/ui/test_authenticated_handlers.py tests/ui/test_summary_polling.py tests/test_report_service.py tests/test_legacy_migration.py tests/test_legacy_migration_recovery.py --basetemp=.runtime/pytest-qa-migration-green
```

Expected: selected tests PASS; legacy handlers retain their public output tuple/string contracts and no product path writes `questions[]`.

- [ ] **Step 8: Commit**

```powershell
git add app/database.py app/qa_migration.py app/qa_repository.py app/qa_service.py ui/gradio_app.py assistants/pdf_learning_assistant.py app/reports.py tests/test_qa_migration.py tests/ui/test_authenticated_handlers.py tests/ui/test_summary_polling.py tests/test_report_service.py tests/test_legacy_migration.py
git commit -m "feat: migrate legacy QA history"
```

---

### Task 8: Wire one application lifecycle and expose the QA REST API

**Files:**
- Modify: `app/bootstrap.py`
- Modify: `app/runtime.py`
- Modify: `api/dependencies.py`
- Modify: `api/config.py`
- Modify: `api/errors.py`
- Create: `api/schemas/qa.py`
- Create: `api/routes/qa.py`
- Modify: `api/routes/documents.py`
- Modify: `api/schemas/documents.py`
- Modify: `api/app.py`
- Modify: `tests/test_app_bootstrap.py`
- Create: `tests/api/test_qa_routes.py`
- Modify: `tests/api/test_document_routes.py`
- Modify: `tests/api/test_auth_routes.py`
- Modify: `tests/api/test_app_lifecycle.py`
- Modify: `tests/api/test_mounts.py`

**Interfaces:**
- Consumes: Tasks 4–7 services/workers, existing request-state dependency pattern, Cookie/CSRF dependencies and error envelope.
- Produces: one lifecycle-owned QA service graph and `/api/v1/qa/*`; changes document deletion to return an accepted deletion resource rather than an unsafe immediate 204.

- [ ] **Step 1: Write API and lifecycle RED tests**

```python
def test_create_conversation_uses_cookie_owner_and_csrf(client, services):
    owner(client)
    response = client.post(
        "/api/v1/qa/conversations",
        headers={"X-CSRF-Token": "owner-csrf"},
        json={"document_ids": [DOCUMENT_ID]},
    )
    assert response.status_code == 201
    assert response.json()["documents"][0]["document_id"] == DOCUMENT_ID
    assert "user_id" not in response.text
    assert services.qa_service.calls == [
        ("create_conversation", "owner-token", (DOCUMENT_ID,))
    ]


def test_application_starts_and_stops_each_worker_once(services):
    with TestClient(create_api_app(services)):
        assert services.import_worker_pool.start_calls == 1
        assert services.qa_worker_pool.start_calls == 1
        assert services.qa_deletion_worker.start_calls == 1
    assert services.qa_deletion_worker.stop_calls == 1
    assert services.qa_worker_pool.stop_calls == 1
    assert services.import_worker_pool.stop_calls == 1
```

Add every endpoint's auth/CSRF, cross-user 404, validation, idempotent 200/202, busy 409, safe engine 503, retry, cancel, deletion and response-field projection cases.

Add a config test proving `QA_ROUTE_ENABLED` defaults to true, accepts only explicit `false` to disable the product route, and does not alter authentication or worker recovery.

- [ ] **Step 2: Run RED**

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/api/test_qa_routes.py tests/api/test_document_routes.py tests/api/test_app_lifecycle.py tests/test_app_bootstrap.py --basetemp=.runtime/pytest-qa-api-red
```

Expected: FAIL because services, dependencies, schemas and router are not wired.

- [ ] **Step 3: Construct one service graph with explicit test injection**

Extend `ApplicationServices.create()` with a keyword-only `qa_answer_engine: QaAnswerEngine | None = None`. Default to `RagQaAnswerEngine`; this is a normal dependency seam, not an environment backdoor. Construct repositories, worker pools, migration, deletion and `QaService` exactly once, then inject the already-created services into the document library/runtime where needed.

Add fields:

```python
qa_repository: QaRepository
qa_job_repository: QaJobRepository
qa_service: QaService
qa_worker_pool: QaWorkerPool
qa_deletion_service: QaDeletionService
qa_deletion_worker: QaDeletionWorker
qa_legacy_migration: QaLegacyMigrationService
```

`start()` first requeues expired summary/deletion/Memory-sync leases, then calls `recover_interrupted_questions()` with the active-job exclusion described in Task 4, and finally starts import, QA and deletion workers. This ordering preserves queued/running summary turns while failing only orphaned synchronous work. `stop()` stops deletion, QA and import workers in reverse order. Repeated start/stop remains idempotent.

- [ ] **Step 4: Define exact Pydantic request/response schemas**

```python
class CreateConversationRequest(BaseModel):
    document_ids: list[UUID] = Field(min_length=1, max_length=10)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=20_000)
    mode: Literal["auto", "joint", "compare"] = "auto"
    client_request_id: UUID


class SummaryRequest(BaseModel):
    instruction: str | None = Field(default=None, max_length=20_000)
    client_request_id: UUID


class RetryRequest(BaseModel):
    client_request_id: UUID


class DeletionResponse(BaseModel):
    deletion_id: str
    status: Literal["queued", "running", "completed", "failed"]
    affected_conversation_count: int


class QaCapabilitiesResponse(BaseModel):
    enabled: bool
```

Define safe conversation, document-snapshot, message, citation, job and cursor-page DTOs. No DTO includes user IDs, paths, prompts, raw errors, lease owners or memory IDs.

Extend the common error envelope with optional `trace_id`. QA engine/worker/deletion failures pass their persisted safe trace ID; ordinary auth/validation errors use `null`. Update exact-envelope tests so clients can display a support reference without seeing raw exceptions.

- [ ] **Step 5: Implement exact QA routes**

```text
GET    /api/v1/qa/capabilities
POST   /api/v1/qa/conversations
GET    /api/v1/qa/conversations
GET    /api/v1/qa/conversations/{conversation_id}
GET    /api/v1/qa/conversations/{conversation_id}/messages
DELETE /api/v1/qa/conversations/{conversation_id}
POST   /api/v1/qa/conversations/{conversation_id}/messages
GET    /api/v1/qa/messages/{message_id}
POST   /api/v1/qa/messages/{message_id}/retry
POST   /api/v1/qa/conversations/{conversation_id}/summary-jobs
GET    /api/v1/qa/jobs/{job_id}
POST   /api/v1/qa/jobs/{job_id}/cancel
GET    /api/v1/qa/deletions/{deletion_id}
```

All POST/DELETE endpoints depend on `get_csrf_validated_session`; GET endpoints depend on `get_current_session`. Pass only the Cookie token to services. Map `QaNotFoundError` to 404, validation to 422 stable QA code, busy to 409, resource deleting to 409, engine unavailable to 503 retryable and accepted work to 202. A duplicate completed message returns 200 and a duplicate pending message returns 202.

`GET /capabilities` returns the server-side `QA_ROUTE_ENABLED` value. When disabled, other QA routes return safe `503 QA_ROUTE_DISABLED`; workers still recover durable state and no path resumes `history.json` dual writes. Task 10 renders the existing migration explanation instead of the workspace, providing a restart-only rollback switch without data rollback.

- [ ] **Step 6: Change document deletion to accepted durable work**

`DELETE /api/v1/documents/{document_id}` calls `QaDeletionService.request_document()` through `DocumentLibraryService`, returns `DeletionResponse` with 202, and reports the real affected conversation count. Preserve UUID validation, CSRF, owner isolation and active-import conflict. Update document route tests; do not return 204 before cross-store cleanup is durable.

- [ ] **Step 7: Include the router without changing mounts/fallbacks**

Add `qa_router` to `create_api_app()` after auth and before the SPA fallback. Do not move `/legacy`, `/assets`, lazy service binding or Accept negotiation. Extend fake service dataclasses only with required QA dependencies.

- [ ] **Step 8: Run GREEN and API regression**

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/test_app_bootstrap.py tests/api --basetemp=.runtime/pytest-qa-api-green
```

Expected: all API/lifecycle/mount tests PASS; every mutation rejects missing/forged CSRF and every cross-user identifier is indistinguishable from missing.

- [ ] **Step 9: Commit**

```powershell
git add app/bootstrap.py app/runtime.py api tests/test_app_bootstrap.py tests/api
git commit -m "feat: expose QA product APIs"
```

---

### Task 9: Build the typed React QA data layer

**Files:**
- Modify: `web/src/api/client.ts`
- Modify: `web/src/api/client.test.ts`
- Create: `web/src/features/qa/types.ts`
- Create: `web/src/features/qa/api.ts`
- Create: `web/src/features/qa/queries.ts`
- Create: `web/src/features/qa/api.test.ts`
- Create: `web/src/features/qa/queries.test.tsx`

**Interfaces:**
- Consumes: Task 8 safe DTOs and existing `AuthProvider.request<T>()`/TanStack Query conventions.
- Produces: exact client types, API functions, query keys, pagination/status polling and mutations consumed by `QaPage` and components.

- [ ] **Step 1: Write API and polling RED tests**

```tsx
it("adds UUID idempotency to an ask and reuses the server message", async () => {
  const request = vi.fn().mockResolvedValue(message({ status: "completed" }));
  const id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
  await askQuestion(request, "conversation", "问题", "auto", id);
  expect(request).toHaveBeenCalledWith(
    "/api/v1/qa/conversations/conversation/messages",
    expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ question: "问题", mode: "auto", client_request_id: id }),
    }),
  );
});


it("polls only pending messages and active summary jobs", async () => {
  const harness = renderQaQueries({ messageStatus: "pending", jobStatus: "running" });
  await vi.advanceTimersByTimeAsync(2_000);
  expect(harness.messageRequests()).toBe(2);
  expect(harness.jobRequests()).toBe(2);
  harness.completeAll();
  await vi.advanceTimersByTimeAsync(4_000);
  expect(harness.messageRequests()).toBe(2);
  expect(harness.jobRequests()).toBe(2);
});
```

- [ ] **Step 2: Run RED**

```powershell
Set-Location web
npx vitest run src/features/qa/api.test.ts src/features/qa/queries.test.tsx
Set-Location ..
```

Expected: FAIL because QA frontend modules do not exist.

- [ ] **Step 3: Define client types matching API DTOs exactly**

```ts
export type QaMode = "auto" | "joint" | "compare" | "summary";
export type QaMessageStatus = "pending" | "completed" | "failed" | "cancelled";
export type QaJobStatus = "queued" | "running" | "completed" | "failed" | "cancelled";

export type QaCitation = {
  citation_id: string;
  document_id: string;
  document_name: string;
  page_number: number | null;
  section: string | null;
  excerpt: string;
  reference: string;
  truncated: boolean;
  source_type: "rag" | "graph";
};
```

Define conversation/document/message/job/deletion and cursor-page types with no client-only server fields. Extend `ApiErrorBody.error` with `trace_id: string | null` and expose it as `ApiError.traceId`; retain compatibility with a missing field during rolling deployment by defaulting to `null`.

- [ ] **Step 4: Implement API functions**

Use `encodeURIComponent` for path segments and `JSON.stringify` with `Content-Type: application/json`. Export exact functions for all Task 8 endpoints. `deleteConversation()` and the existing document delete client return `DeletionResponse`, not `void`. UUID generation happens in the mutation caller with `crypto.randomUUID()` and remains stable across a retry of the same submitted action.

- [ ] **Step 5: Implement queries, polling and cache transitions**

```ts
export const QA_CONVERSATIONS_KEY = ["qa", "conversations"] as const;
export const qaConversationKey = (id: string) => ["qa", "conversation", id] as const;
export const qaMessagesKey = (id: string) => ["qa", "messages", id] as const;
export const qaMessageKey = (id: string) => ["qa", "message", id] as const;
export const qaJobKey = (id: string) => ["qa", "job", id] as const;
```

Conversations/messages use cursor pagination. Poll a message every 2 seconds only while pending and a job every 2 seconds only while queued/running. Mutations accept server truth into cache, invalidate conversation/message lists, and never invent completed answers, sources, progress or deletion completion. On accepted deletion, remove the resource from visible cache immediately and poll only the deletion notification if the page needs feedback.

- [ ] **Step 6: Run GREEN and frontend client regression**

```powershell
Set-Location web
npx vitest run src/features/qa/api.test.ts src/features/qa/queries.test.tsx src/api/client.test.ts
npm run typecheck
Set-Location ..
```

Expected: tests and typecheck PASS; auth request still supplies Cookie credentials and CSRF for mutations.

- [ ] **Step 7: Commit**

```powershell
git add web/src/api/client.ts web/src/api/client.test.ts web/src/features/qa
git commit -m "feat: add QA frontend data layer"
```

---

### Task 10: Build the responsive React `/qa` product page

**Files:**
- Create: `web/src/features/qa/components/ConversationList.tsx`
- Create: `web/src/features/qa/components/MessageList.tsx`
- Create: `web/src/features/qa/components/QaComposer.tsx`
- Create: `web/src/features/qa/components/SourcePanel.tsx`
- Create: `web/src/features/qa/components/QaDrawer.tsx`
- Create: `web/src/features/qa/components/QaDeleteDialog.tsx`
- Create: `web/src/features/qa/components/QaComposer.test.tsx`
- Create: `web/src/features/qa/components/QaDrawer.test.tsx`
- Create: `web/src/pages/QaPage.tsx`
- Create: `web/src/pages/QaPage.test.tsx`
- Create: `web/src/styles/qa.css`
- Modify: `web/src/App.tsx`
- Modify: `web/src/main.tsx`
- Modify: `web/src/components/DocumentList/DocumentList.tsx`
- Modify: `web/src/pages/DocumentsPage.tsx`
- Modify: `web/src/pages/DocumentsPage.test.tsx`
- Modify: `docs/product-ui/penpot-component-map.json`

**Interfaces:**
- Consumes: Task 1 Penpot authority, Task 9 hooks/types, existing AppShell/Button/focus/overlay patterns and document query.
- Produces: real `/qa`, desktop four-region UI, tablet side drawers, mobile bottom sheets, document-to-QA entry, all required loading/error/pending/retry/delete states.

- [ ] **Step 1: Write route, state and accessibility RED tests**

```tsx
it("renders the real QA route with conversation, chat and sources regions", async () => {
  renderAuthenticatedApp("/qa?conversation=conversation-1");
  expect(await screen.findByRole("heading", { level: 1, name: "智能问答" })).toBeVisible();
  expect(screen.getByRole("navigation", { name: "问答会话" })).toBeVisible();
  expect(screen.getByRole("log", { name: "问答消息" })).toBeVisible();
  expect(screen.getByRole("complementary", { name: "引用依据" })).toBeVisible();
  expect(screen.queryByText("该能力正在迁移到新版界面")).not.toBeInTheDocument();
});


it("restores focus after the mobile sources sheet closes", async () => {
  const user = userEvent.setup();
  renderQaAtWidth(390);
  const trigger = await screen.findByRole("button", { name: "查看 2 条引用依据" });
  await user.click(trigger);
  await user.keyboard("{Escape}");
  expect(trigger).toHaveFocus();
});
```

Cover new conversation document picker, fixed-scope display, create/switch/delete, ordinary/compare send, summary progress/cancel, pending refresh recovery, failed retry, copy citation feedback, empty/legacy-no-source states and 44px controls.

- [ ] **Step 2: Run RED**

```powershell
Set-Location web
npx vitest run src/pages/QaPage.test.tsx src/features/qa/components/QaComposer.test.tsx src/features/qa/components/QaDrawer.test.tsx src/pages/DocumentsPage.test.tsx
Set-Location ..
```

Expected: FAIL because the page/components and route are absent.

- [ ] **Step 3: Implement focused presentational components**

- `ConversationList`: new action, recent cursor list, selected state, auto title, delete trigger.
- `MessageList`: `role="log"`, stable user/assistant order, pending/failed/cancelled cards and citation buttons.
- `QaComposer`: controlled question, mode `auto/joint/compare`, compare disabled for one document, busy state and submit on Ctrl/Cmd+Enter without breaking IME.
- `SourcePanel`: current assistant message's immutable sources, document/page/section/excerpt and clipboard feedback.
- `QaDrawer`: shared accessible side/bottom overlay, focus trap, Escape, backdrop close, scroll lock and focus return.
- `QaDeleteDialog`: real title and affected-data warning; pending accepted deletion cannot be double-submitted.

Components receive data/callback props and do not call APIs directly.

- [ ] **Step 4: Compose `QaPage` state from queries**

`QaPage` first reads `/api/v1/qa/capabilities`; when disabled it renders the existing migration explanation and performs no conversation query. When enabled it owns selected conversation ID from `?conversation=`, drawer state, currently selected assistant message for sources and new-conversation picker. It loads document choices through the existing document query, creates a fixed-scope conversation, updates the URL with `replace`, and renders only server-backed content. On first question it displays the returned automatic title after cache invalidation.

Summary action uses Task 9 durable jobs; the composer is disabled while the current conversation has a pending answer or active job, but other conversations remain selectable.

- [ ] **Step 5: Implement the approved responsive CSS**

At `>=1200px`, `.qa-page__workspace` is `240px minmax(0, 1fr) 320px`; AppShell navigation remains outside this grid. At `768–1199px`, conversation and sources become side drawers and chat fills content. Below 768px, chat is one column; conversations, fixed documents and sources are bottom sheets above the existing 64px mobile nav. Use only existing semantic tokens, safe-area insets and reduced-motion behavior.

- [ ] **Step 6: Add document-library entry and route**

Add an accessible “开始问答” action per ready document. `DocumentsPage` navigates to `/qa?documents=<encoded-id>`; `QaPage` opens the new-conversation picker with that real document preselected. Multi-document selection remains available in the picker. In `App.tsx`, render `QaPage` only for `/qa`, keep all other unfinished destinations on `MigrationPage`, and import `qa.css` from `main.tsx`.

- [ ] **Step 7: Bind only verified Penpot components**

Add QA page/components to `penpot-component-map.json` using Task 1 exact IDs after fresh-read verification. Do not mark wrappers or states as reusable components unless both code and Penpot have a real component identity.

- [ ] **Step 8: Run GREEN and frontend gates**

```powershell
Set-Location web
npm test
npm run typecheck
npm run lint
npm run build
Set-Location ..
node --test tests/design/test_penpot_component_map.mjs tests/design/test_penpot_handoff.mjs
node scripts/design_tokens.mjs --check design/tokens/zhiyan.tokens.json web/src/styles/tokens.css
```

Expected: unit, typecheck, lint, build, design map/handoff and token freshness all PASS; unrelated routes retain existing behavior.

- [ ] **Step 9: Commit**

```powershell
git add web/src docs/product-ui/penpot-component-map.json
git commit -m "feat: add responsive QA workspace"
```

---

### Task 11: Prove migration, recovery, security and three-viewport acceptance

**Files:**
- Create: `web/e2e/qa.spec.ts`
- Create: `web/e2e/qa-runtime.py`
- Modify: `web/e2e/python-runtime.ts`
- Modify: `web/e2e/fixtures.ts`
- Modify: `web/e2e/accessibility.spec.ts`
- Modify: `web/e2e/visual.spec.ts`
- Create: eight QA visual snapshots under `web/e2e/visual.spec.ts-snapshots/`
- Modify: `tests/deploy/test_qa_product_contract.py`
- Modify: `docs/product-ui/README.md`
- Modify: `docs/product-ui/penpot-handoff.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: all prior tasks, the real unified FastAPI/React application and Task 1 Penpot exports.
- Produces: deterministic real-server E2E through a test-only injected answer adapter, failure/restart/isolation evidence, visual baselines, operations documentation and complete release-gate evidence.

- [ ] **Step 1: Write real-server functional E2E RED**

```ts
test("creates a fixed-scope conversation, asks, refreshes and opens sources", async ({ page, qaAppUrl }, testInfo) => {
  await registerUser(page, qaAppUrl, uniqueUsername(`qa_${testInfo.project.name}`));
  await importDocument(page, qaAppUrl, {
    name: "qa-evidence.md",
    content: "# Evidence\nRetrieval quality requires grounded citations.",
  });
  await page.getByRole("button", { name: "开始问答 qa-evidence.md" }).click();
  await page.getByRole("button", { name: "创建会话" }).click();
  await page.getByLabel("问题").fill("如何验证检索质量？");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByText("基于 qa-evidence.md 的测试回答")).toBeVisible();
  await page.reload();
  await expect(page.getByText("基于 qa-evidence.md 的测试回答")).toBeVisible();
  await page.getByRole("button", { name: /查看 1 条引用依据/ }).click();
  await expect(page.getByText("Retrieval quality requires grounded citations.")).toBeVisible();
});
```

Also cover compare, summary polling/cancel, retry after injected failure, conversation delete, document cascade, refresh during pending state and second-user direct-ID isolation.

- [ ] **Step 2: Add the test-only answer runtime without a production backdoor**

`web/e2e/qa-runtime.py` imports `ApplicationServices.create(data_root, qa_answer_engine=DeterministicQaAnswerEngine())` and `create_application(services)`, then runs Uvicorn. The fake adapter uses only the test-created document IDs/names/content supplied by fixtures, returns deterministic safe citations, supports barrier/failure controls entirely inside the test process, and is never selectable by production environment variables or API endpoints.

Extend TypeScript runtime/fixtures with `qaAppUrl` that launches this test-only module. Existing E2E projects keep using the normal runtime.

- [ ] **Step 3: Run functional E2E RED then complete fixtures**

```powershell
Set-Location web
npm run build
npx playwright test e2e/qa.spec.ts --project=desktop --workers=1
Set-Location ..
```

Expected before completion: focused E2E fails at the first missing fixture/state assertion. Complete only test-owned helpers; do not add production seeding, bypass, sleep-only synchronization or route interception.

- [ ] **Step 4: Add failure-injection integration tests**

Extend Python service/worker tests, not browser-only timing, for:

```text
model failure after pending write
process restart with pending sync question
summary lease loss and reclaim
cancel vs completion race
database conditional-update conflict
document delete vs in-flight answer
Memory cleanup failure and retry
vector/source deletion failure and retry
rolling-summary failure fallback
same-user concurrent conversations with isolated citations
```

Each test asserts final database, Memory/vector/file state and safe logs; never assert only an HTTP message.

- [ ] **Step 5: Add axe, keyboard and visual acceptance**

Extend accessibility coverage for empty QA, populated chat, conversation drawer, sources drawer/sheet, delete dialog, pending, failure and summary progress. Assert one `h1`, logical tab order, visible focus, Escape/focus return, scroll restoration, `aria-live`, 44px mobile targets and zero serious/critical axe violations.

Create visual baselines exactly:

```text
qa-default-desktop.png
qa-summary-desktop.png
qa-delete-desktop.png
qa-default-tablet.png
qa-sources-tablet.png
qa-default-mobile.png
qa-sources-mobile.png
qa-failure-mobile.png
```

Compare each with the corresponding Task 1 Penpot export and record any deliberate browser difference before accepting it. Do not update unrelated snapshots.

- [ ] **Step 6: Update product and operations documentation**

Document `/qa` as a real route, REST recovery semantics, fixed scope, summary polling/cancellation, legacy migration, deletion fences, safe error/trace behavior, backup implications and worker recovery. Update the product route matrix without marking notes/insights/search/learning center complete.

- [ ] **Step 7: Run the complete release gates**

```powershell
New-Item -ItemType Directory -Force .runtime | Out-Null
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q --basetemp=.runtime/pytest-qa-final
Set-Location web
npm test
npm run typecheck
npm run lint
npm run build
npm audit --audit-level=moderate
npx playwright test --workers=1
Set-Location ..
node --test tests/design/test_design_tokens.mjs tests/design/test_penpot_component_map.mjs tests/design/test_penpot_handoff.mjs
git diff --check
```

Expected: all commands exit 0; no new skip, advisory, leaked process or runtime directory. Existing conditional external-service skips must remain documented and unrelated.

- [ ] **Step 8: Run isolated Docker Linux validation**

Create `.runtime/qa-docker/deploy.env` from the documented example with an unused host port and a worktree-local `DEPLOY_DATA_ROOT`; never overwrite `deploy/.env`. Then run:

```powershell
docker compose --env-file .runtime/qa-docker/deploy.env -p zhiyan-qa-20260825 build app qdrant
docker compose --env-file .runtime/qa-docker/deploy.env -p zhiyan-qa-20260825 up -d app qdrant
& 'D:\python_self_agent\venv\Scripts\python.exe' deploy/smoke_test.py --env-file .runtime/qa-docker/deploy.env --deep
docker compose --env-file .runtime/qa-docker/deploy.env -p zhiyan-qa-20260825 down --remove-orphans
```

Expected: Linux images build, app/Qdrant are healthy, deep smoke passes and only this owned Compose project/network are removed. Do not use `--volumes`.

- [ ] **Step 9: Run security and repository scans**

```powershell
rg -n "(password|secret|token|api[_-]?key)\s*[:=]\s*['\"][^'\"]+" app api web/src docs/product-ui
rg -n "figma\.com|file://|C:\\Users" docs/product-ui
rg -n "history\[.?questions.?\].*append|questions.*append" app assistants ui
rg -n "user_id|memory_id|lease_owner|absolute_path" api/schemas/qa.py web/src/features/qa
git status --short
```

Expected: no introduced credential, Figma, local-path, legacy question-write or unsafe DTO finding; only intended tracked files appear.

- [ ] **Step 10: Commit acceptance evidence**

```powershell
git add web/e2e tests/deploy/test_qa_product_contract.py docs/product-ui README.md
git commit -m "test: accept QA vertical slice"
```

---

## Final Integration Review Gate

After all eleven tasks are committed and their task packets are marked `done`, Codex must inspect the combined diff and create:

`docs/agent-workflow/task-packets/2026-08-25-qa-vertical-slice/FINAL_INTEGRATION_REVIEW.md`

The feature is not complete until that review result is `accepted`. If it finds any Critical or Important issue, Codex creates corrective task packets and reruns the affected focused/full gates instead of silently accepting the deviation.
