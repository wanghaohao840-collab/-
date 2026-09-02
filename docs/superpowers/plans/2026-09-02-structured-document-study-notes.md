# Structured Document Study Notes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add user-triggered, durable, citation-backed structured study notes for one imported document at a time in the current Gradio application.

**Architecture:** Add a SQLite-backed task/note subsystem in the application layer, a typed structured-note generator beside `RAGTool`, and a background worker that uses the existing per-user runtime without mixing AI notes into manual notes. Gradio talks only to `StructuredNoteService`; the service, assistant, and RAG layers preserve the existing `UI -> Application Service -> Assistant -> Tool -> RAG/Storage` direction.

**Tech Stack:** Python 3, SQLite, Gradio 6.19, existing `PDFLearningAssistant`, `RAGTool`, pytest, repository-local `venv`.

## Global Constraints

- The first release runs in the existing Gradio application; it does not build the planned React + FastAPI product frontend.
- Generation starts only after an authenticated user clicks “生成学习笔记”; document import never starts it automatically.
- A task processes exactly one document owned by the current user.
- AI notes are read-only and replaceable; manual notes remain in the existing `history["notes"]` path.
- Re-generation keeps the previous note readable and replaces it only after the new result passes structure and citation validation.
- PDF evidence exposes page numbers; TXT, MD, Markdown, and DOCX expose file name plus section, chunk, or excerpt location.
- Note failure, cancellation, retry, deletion, and recovery must not break import, QA, search, reports, or manual notes.
- Every read and mutation is scoped by both authenticated `user_id` and the target identifier; browser-supplied user IDs are never trusted.
- Runtime work uses `D:\python_self_agent\venv\Scripts\python.exe`; pytest commands use a repository-local `--basetemp` on Windows.
- Do not commit `.env`, credentials, user uploads, generated note data, runtime databases, caches, reports, or temporary files.
- Preserve unrelated working-tree changes and the documented `document_id` isolation rules.

---

## File Structure

### New application files

- `app/structured_note_models.py`: immutable task and note records plus status/stage literals.
- `app/structured_note_repository.py`: SQLite task transitions, current-note versioning, atomic completion, recovery, and deletion.
- `app/structured_note_service.py`: authenticated application API and document ownership checks.
- `app/structured_note_worker.py`: task runner, cancellation registry, scheduler, and non-daemon worker lifecycle.

### New RAG helper

- `hello_agents/tools/builtin/rag_structured_notes.py`: prompt schema, JSON parsing, citation validation, Markdown rendering, and typed artifact/evidence contracts.

### Existing files to modify

- `app/database.py`: create the two durable tables and their partial unique indexes.
- `app/runtime.py`: inject the structured-note service into current and future user runtimes.
- `assistants/pdf_learning_assistant.py`: prepare a document-scoped evidence snapshot, generate a note, and coordinate note cleanup during document deletion.
- `hello_agents/tools/builtin/rag_tool.py`: prepare evidence and invoke the structured-note helper through a typed public method and a tool action.
- `ui/gradio_app.py`: initialize/start/stop note workers and add the authenticated Gradio handlers and tab.
- `README.md`: document user behavior and supported lifecycle.
- `PROJECT_KNOWLEDGE.md`: record the new verified capability only after tests pass.

### New tests

- `tests/test_structured_note_repository.py`
- `tests/tools/test_rag_structured_notes.py`
- `tests/test_structured_note_assistant.py`
- `tests/test_structured_note_service.py`
- `tests/test_structured_note_worker.py`
- `tests/ui/test_structured_note_handlers.py`
- `tests/integration/test_structured_note_acceptance.py`

---

### Task 1: Durable Schema and Typed Records

**Files:**
- Create: `app/structured_note_models.py`
- Modify: `app/database.py:9-84`
- Create: `tests/test_structured_note_repository.py`

**Interfaces:**
- Produces: `StructuredNoteTaskCreate`, `StructuredNoteTaskRecord`, and `StructuredNoteRecord` dataclasses.
- Produces: SQLite tables `structured_note_tasks` and `structured_notes`.
- Produces: partial unique indexes `uq_structured_note_tasks_active_document`, `uq_structured_note_tasks_running_user`, and `uq_structured_notes_current_document`.
- Consumes: existing `initialize_database(db_path)` and user foreign keys from `users(id)`.

- [ ] **Step 1: Write failing schema and model tests**

Add these tests to `tests/test_structured_note_repository.py`:

```python
from __future__ import annotations

from dataclasses import FrozenInstanceError
import sqlite3

import pytest

from app.database import connect, initialize_database
from app.structured_note_models import (
    StructuredNoteRecord,
    StructuredNoteTaskCreate,
    StructuredNoteTaskRecord,
)


def test_structured_note_tables_and_partial_indexes_are_created(tmp_path):
    db_path = tmp_path / "app.db"
    initialize_database(db_path)

    with connect(db_path) as conn:
        tables = {
            row["name"]
            for row in conn.execute(
                "select name from sqlite_master where type = 'table'"
            ).fetchall()
        }
        indexes = {
            row["name"]
            for row in conn.execute(
                "select name from sqlite_master where type = 'index'"
            ).fetchall()
        }

    assert {"structured_note_tasks", "structured_notes"} <= tables
    assert {
        "uq_structured_note_tasks_active_document",
        "uq_structured_note_tasks_running_user",
        "uq_structured_notes_current_document",
    } <= indexes


def test_structured_note_records_are_immutable():
    task = StructuredNoteTaskCreate(
        task_id="task-1",
        user_id="user-1",
        document_id="doc-1",
        source_import_task_id="import-1",
    )

    with pytest.raises(FrozenInstanceError):
        task.document_id = "doc-2"
```

- [ ] **Step 2: Run the tests and verify the import/schema failure**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_structured_note_repository.py -q --basetemp=.pytest-tmp-structured-note-schema-red
```

Expected: FAIL during collection because `app.structured_note_models` does not exist.

- [ ] **Step 3: Add the immutable records**

Create `app/structured_note_models.py` with these exact public fields:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


StructuredNoteStatus = Literal[
    "queued", "running", "succeeded", "failed", "cancelled"
]
StructuredNoteStage = Literal[
    "queued",
    "evidence",
    "generating",
    "validating",
    "persisting",
    "completed",
    "failed",
    "cancelled",
]


@dataclass(frozen=True)
class StructuredNoteTaskCreate:
    task_id: str
    user_id: str
    document_id: str
    source_import_task_id: str


@dataclass(frozen=True)
class StructuredNoteTaskRecord:
    task_id: str
    user_id: str
    document_id: str
    source_import_task_id: str
    status: StructuredNoteStatus
    stage: StructuredNoteStage
    progress: int
    attempt_count: int
    error_code: str | None
    error_summary: str | None
    cancel_requested: bool
    created_at: str
    started_at: str | None
    finished_at: str | None
    updated_at: str


@dataclass(frozen=True)
class StructuredNoteRecord:
    note_id: str
    user_id: str
    document_id: str
    generation_task_id: str
    source_import_task_id: str
    content_markdown: str
    sources_json: str
    document_type: str
    prompt_version: str
    is_current: bool
    created_at: str
```

- [ ] **Step 4: Add the SQLite schema**

Insert the following SQL into `SCHEMA` in `app/database.py`, after the import-task indexes and before `data_migrations`:

```sql
create table if not exists structured_note_tasks (
    id text primary key,
    user_id text not null references users(id) on delete cascade,
    document_id text not null,
    source_import_task_id text not null,
    status text not null check(status in ('queued','running','succeeded','failed','cancelled')),
    stage text not null check(stage in ('queued','evidence','generating','validating','persisting','completed','failed','cancelled')),
    progress integer not null check(progress between 0 and 100),
    attempt_count integer not null default 0,
    error_code text,
    error_summary text,
    cancel_requested integer not null default 0 check(cancel_requested in (0,1)),
    created_at text not null,
    started_at text,
    finished_at text,
    updated_at text not null
);

create unique index if not exists uq_structured_note_tasks_active_document
on structured_note_tasks(user_id, document_id)
where status in ('queued','running');

create unique index if not exists uq_structured_note_tasks_running_user
on structured_note_tasks(user_id)
where status = 'running';

create index if not exists ix_structured_note_tasks_scheduler
on structured_note_tasks(status, created_at);

create table if not exists structured_notes (
    id text primary key,
    user_id text not null references users(id) on delete cascade,
    document_id text not null,
    generation_task_id text not null references structured_note_tasks(id) on delete cascade,
    source_import_task_id text not null,
    content_markdown text not null,
    sources_json text not null,
    document_type text not null,
    prompt_version text not null,
    is_current integer not null default 1 check(is_current in (0,1)),
    created_at text not null
);

create unique index if not exists uq_structured_notes_current_document
on structured_notes(user_id, document_id)
where is_current = 1;
```

- [ ] **Step 5: Run the schema tests**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_structured_note_repository.py -q --basetemp=.pytest-tmp-structured-note-schema-green
```

Expected: PASS.

- [ ] **Step 6: Commit the schema and contracts**

```powershell
git add app/database.py app/structured_note_models.py tests/test_structured_note_repository.py
git commit -m "feat: add structured note persistence schema"
```

---

### Task 2: Task State Machine and Atomic Note Versioning

**Files:**
- Create: `app/structured_note_repository.py`
- Modify: `tests/test_structured_note_repository.py`

**Interfaces:**
- Consumes: records from `app.structured_note_models` and `connect`/`transaction` from `app.database`.
- Produces: `StructuredNoteTaskRepository.create_task(user_id, document_id, source_import_task_id, task_id=None, now=None)`.
- Produces: `get_task`, `get_required_task`, `get_active_task`, `claim_next`, `update_progress`, `request_cancel`, `mark_cancelled`, `mark_failed`, `retry_task`, `recover_running`, `is_cancel_requested`, and document/user deletion methods.
- Produces: `DocumentNoteRepository.get_current`, `complete_task`, `delete_for_document`, and `delete_for_user`.

- [ ] **Step 1: Add failing repository lifecycle tests**

Append focused tests that create two users through `AuthService`, then assert:

```python
from app.auth import AuthService
from app.structured_note_repository import (
    DocumentNoteRepository,
    InvalidStructuredNoteTransition,
    StructuredNoteTaskRepository,
)


def make_note_repositories(tmp_path):
    db_path = tmp_path / "app.db"
    initialize_database(db_path)
    auth = AuthService(db_path)
    first = auth.register("first-user", "correct horse battery")
    second = auth.register("second-user", "correct horse battery")
    return (
        StructuredNoteTaskRepository(db_path),
        DocumentNoteRepository(db_path),
        first.id,
        second.id,
    )


def test_create_is_idempotent_for_one_active_document_task(tmp_path):
    tasks, _, user_id, _ = make_note_repositories(tmp_path)
    first = tasks.create_task(user_id, "doc-1", "import-1", task_id="task-1")
    second = tasks.create_task(user_id, "doc-1", "import-1", task_id="task-2")

    assert first.task_id == "task-1"
    assert second.task_id == "task-1"


def test_progress_is_monotonic_and_user_scoped(tmp_path):
    tasks, _, user_id, other_id = make_note_repositories(tmp_path)
    tasks.create_task(user_id, "doc-1", "import-1", task_id="task-1")
    claimed = tasks.claim_next(set())
    assert claimed is not None

    tasks.update_progress(user_id, claimed.task_id, "generating", 60)
    with pytest.raises(ValueError, match="must not decrease"):
        tasks.update_progress(user_id, claimed.task_id, "evidence", 30)
    with pytest.raises(KeyError):
        tasks.get_required_task(other_id, claimed.task_id)


def test_failed_regeneration_keeps_current_note(tmp_path):
    tasks, notes, user_id, _ = make_note_repositories(tmp_path)
    tasks.create_task(user_id, "doc-1", "import-1", task_id="task-1")
    tasks.claim_next(set())
    notes.complete_task(
        user_id=user_id,
        task_id="task-1",
        note_id="note-1",
        content_markdown="# First",
        sources=[{"citation_id": "S-one"}],
        document_type="technical",
        prompt_version="structured-note-v1",
    )
    tasks.create_task(user_id, "doc-1", "import-1", task_id="task-2")
    tasks.claim_next(set())
    tasks.mark_failed(user_id, "task-2", "llm_unavailable", "Try again")

    assert notes.get_current(user_id, "doc-1").content_markdown == "# First"


def test_successful_regeneration_atomically_switches_current_note(tmp_path):
    tasks, notes, user_id, _ = make_note_repositories(tmp_path)
    for task_id, note_id, markdown in (
        ("task-1", "note-1", "# First"),
        ("task-2", "note-2", "# Second"),
    ):
        tasks.create_task(user_id, "doc-1", "import-1", task_id=task_id)
        tasks.claim_next(set())
        notes.complete_task(
            user_id=user_id,
            task_id=task_id,
            note_id=note_id,
            content_markdown=markdown,
            sources=[{"citation_id": "S-one"}],
            document_type="technical",
            prompt_version="structured-note-v1",
        )

    current = notes.get_current(user_id, "doc-1")
    assert current.note_id == "note-2"
    assert current.content_markdown == "# Second"
```

Add separate tests for queued cancellation, running cancellation request, failed/cancelled retry, `recover_running()` producing `process_interrupted`, one running task per user, and deletion scoped to one user/document.

- [ ] **Step 2: Run the lifecycle tests and verify failure**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_structured_note_repository.py -q --basetemp=.pytest-tmp-structured-note-repository-red
```

Expected: FAIL because `app.structured_note_repository` does not exist.

- [ ] **Step 3: Implement task transitions**

Create `app/structured_note_repository.py`. Use `begin immediate` in `claim_next()` and condition every mutation on `id`, `user_id`, and the required source state. The critical progress guard is:

```python
def update_progress(self, user_id, task_id, stage, progress, now=None):
    if not 0 <= progress <= 100:
        raise ValueError("progress must be between 0 and 100")
    timestamp = now or _utc_now()
    with transaction(self.db_path) as conn:
        current = self._required_row(conn, user_id, task_id)
        if current["status"] != "running":
            raise InvalidStructuredNoteTransition("task is not running")
        if progress < int(current["progress"]):
            raise ValueError("progress must not decrease")
        conn.execute(
            """
            update structured_note_tasks
            set stage = ?, progress = ?, updated_at = ?
            where id = ? and user_id = ? and status = 'running'
            """,
            (stage, progress, timestamp, task_id, user_id),
        )
        return _task_from_row(self._required_row(conn, user_id, task_id))
```

`create_task()` must catch the active-document unique-index conflict and return `get_active_task(user_id, document_id)` instead of creating a duplicate. `retry_task()` accepts only `failed` or `cancelled`, clears error/cancel/timestamps, increments no counter until `claim_next()`, and returns the requeued record.

Cancellation uses a durable two-path transition:

```python
def request_cancel(
    self, user_id: str, task_id: str, now: str | None = None
) -> StructuredNoteTaskRecord:
    timestamp = now or _utc_now()
    with transaction(self.db_path) as conn:
        row = self._required_row(conn, user_id, task_id)
        if row["status"] == "queued":
            conn.execute(
                """
                update structured_note_tasks
                set status = 'cancelled', stage = 'cancelled',
                    finished_at = ?, updated_at = ?
                where id = ? and user_id = ? and status = 'queued'
                """,
                (timestamp, timestamp, task_id, user_id),
            )
        elif row["status"] == "running":
            conn.execute(
                """
                update structured_note_tasks
                set cancel_requested = 1, updated_at = ?
                where id = ? and user_id = ? and status = 'running'
                """,
                (timestamp, task_id, user_id),
            )
        else:
            raise InvalidStructuredNoteTransition(
                "only queued or running tasks can be cancelled"
            )
        return _task_from_row(
            self._required_row(conn, user_id, task_id)
        )
```

`mark_cancelled()` accepts only `running` with `cancel_requested = 1` and writes terminal `cancelled/cancelled` with the latest progress unchanged.

- [ ] **Step 4: Implement atomic completion and version switching**

Implement `DocumentNoteRepository.complete_task()` with one transaction:

```python
def complete_task(
    self,
    *,
    user_id: str,
    task_id: str,
    note_id: str,
    content_markdown: str,
    sources: list[dict[str, object]],
    document_type: str,
    prompt_version: str,
    now: str | None = None,
) -> StructuredNoteRecord:
    timestamp = now or _utc_now()
    with transaction(self.db_path) as conn:
        task = conn.execute(
            """
            select * from structured_note_tasks
            where id = ? and user_id = ? and status = 'running'
              and cancel_requested = 0
            """,
            (task_id, user_id),
        ).fetchone()
        if task is None:
            raise InvalidStructuredNoteTransition(
                "task is not eligible for completion"
            )
        conn.execute(
            """
            update structured_notes set is_current = 0
            where user_id = ? and document_id = ? and is_current = 1
            """,
            (user_id, task["document_id"]),
        )
        conn.execute(
            """
            insert into structured_notes (
                id, user_id, document_id, generation_task_id,
                source_import_task_id, content_markdown, sources_json,
                document_type, prompt_version, is_current, created_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (
                note_id,
                user_id,
                task["document_id"],
                task_id,
                task["source_import_task_id"],
                content_markdown,
                json.dumps(sources, ensure_ascii=False),
                document_type,
                prompt_version,
                timestamp,
            ),
        )
        conn.execute(
            """
            update structured_note_tasks
            set status = 'succeeded', stage = 'completed', progress = 100,
                error_code = null, error_summary = null,
                finished_at = ?, updated_at = ?
            where id = ? and user_id = ? and status = 'running'
            """,
            (timestamp, timestamp, task_id, user_id),
        )
        row = conn.execute(
            "select * from structured_notes where id = ? and user_id = ?",
            (note_id, user_id),
        ).fetchone()
        return _note_from_row(row)
```

`get_current()` must deserialize nothing; it returns the stored JSON string in `StructuredNoteRecord`. Service/UI code performs JSON decoding at its boundary.

- [ ] **Step 5: Implement recovery and deletion**

`recover_running()` changes every `running` row to `failed/failed`, sets `error_code='process_interrupted'`, a fixed safe summary, clears `cancel_requested`, and returns the number changed. `delete_for_document()` and `delete_for_user()` delete tasks and notes in one transaction after the worker cancellation registry has been signaled.

Use these transition bodies:

```python
def recover_running(self, now: str | None = None) -> int:
    timestamp = now or _utc_now()
    with transaction(self.db_path) as conn:
        updated = conn.execute(
            """
            update structured_note_tasks
            set status = 'failed', stage = 'failed',
                error_code = 'process_interrupted',
                error_summary = 'Structured note generation was interrupted; retry it.',
                cancel_requested = 0, finished_at = ?, updated_at = ?
            where status = 'running'
            """,
            (timestamp, timestamp),
        )
        return int(updated.rowcount)


def delete_for_document(self, user_id: str, document_id: str) -> int:
    with transaction(self.db_path) as conn:
        deleted = conn.execute(
            "delete from structured_note_tasks where user_id = ? and document_id = ?",
            (user_id, document_id),
        )
        return int(deleted.rowcount)


def delete_for_user(self, user_id: str) -> int:
    with transaction(self.db_path) as conn:
        deleted = conn.execute(
            "delete from structured_note_tasks where user_id = ?",
            (user_id,),
        )
        return int(deleted.rowcount)
```

The task foreign key cascades each deleted task to its note versions. `DocumentNoteRepository.delete_for_document()` and `delete_for_user()` issue explicit scoped deletes against `structured_notes` as idempotent defensive cleanup before deleting task rows.

- [ ] **Step 6: Run repository tests**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_structured_note_repository.py -q --basetemp=.pytest-tmp-structured-note-repository-green
```

Expected: PASS.

- [ ] **Step 7: Commit repository behavior**

```powershell
git add app/structured_note_repository.py tests/test_structured_note_repository.py
git commit -m "feat: persist structured note task lifecycle"
```

---

### Task 3: Structured Output Contract, Citation Validation, and Markdown Rendering

**Files:**
- Create: `hello_agents/tools/builtin/rag_structured_notes.py`
- Create: `tests/tools/test_rag_structured_notes.py`

**Interfaces:**
- Produces: `StructuredNoteEvidence`, `StructuredNoteArtifact`, `build_structured_note_prompt`, `parse_structured_note`, and `render_structured_note_markdown`.
- Consumes: stable source IDs already produced by `rag_context.prepare_results()`.
- Guarantees: every rendered knowledge section and final summary has at least one allowed citation; unknown IDs reject the entire result.

- [ ] **Step 1: Write failing parser and renderer tests**

Create `tests/tools/test_rag_structured_notes.py` with fixtures shaped like:

```python
import json

import pytest

from hello_agents.tools.builtin.rag_structured_notes import (
    StructuredNoteValidationError,
    parse_structured_note,
    render_structured_note_markdown,
)


def valid_payload():
    return {
        "document_type": "technical",
        "title": "ORM 集成",
        "overview": {
            "text": "从连接到 CRUD 的完整闭环。",
            "citations": ["S-one"],
        },
        "logic_chain": ["建立连接", "定义模型", "操作数据"],
        "sections": [
            {
                "heading": "异步引擎",
                "learning_objective": "理解连接池",
                "body_markdown": "连接池复用数据库连接。",
                "code_markdown": "```python\nengine = create_async_engine(url)\n```",
                "warnings": ["示例池大小不能直接照搬。"],
                "citations": ["S-one"],
            }
        ],
        "summary": {
            "text": "ORM 链路需要明确事务边界。",
            "citations": ["S-two"],
        },
    }


def test_valid_technical_payload_renders_fixed_skeleton():
    parsed = parse_structured_note(
        json.dumps(valid_payload(), ensure_ascii=False),
        allowed_citation_ids={"S-one", "S-two"},
    )
    markdown = render_structured_note_markdown(parsed)

    assert "# ORM 集成" in markdown
    assert "## 章前总览" in markdown
    assert "## 逻辑链条" in markdown
    assert "### 异步引擎" in markdown
    assert "[S-one]" in markdown
    assert "## 章节总结" in markdown


def test_unknown_citation_rejects_entire_payload():
    payload = valid_payload()
    payload["sections"][0]["citations"] = ["S-invented"]

    with pytest.raises(StructuredNoteValidationError, match="unknown citation"):
        parse_structured_note(
            json.dumps(payload, ensure_ascii=False),
            allowed_citation_ids={"S-one", "S-two"},
        )


def test_nontechnical_payload_may_omit_code_and_learning_objective():
    payload = valid_payload()
    payload["document_type"] = "report"
    payload["sections"][0]["learning_objective"] = ""
    payload["sections"][0]["code_markdown"] = ""

    parsed = parse_structured_note(
        json.dumps(payload, ensure_ascii=False),
        allowed_citation_ids={"S-one", "S-two"},
    )
    assert "```" not in render_structured_note_markdown(parsed)
```

Also test fenced JSON input, malformed JSON, missing overview, empty sections, empty summary, empty section citations, invalid document type, and a warning/code field omitted from a report.

- [ ] **Step 2: Run the parser tests and verify failure**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests/tools/test_rag_structured_notes.py -q --basetemp=.pytest-tmp-rag-structured-note-red
```

Expected: FAIL because the helper module does not exist.

- [ ] **Step 3: Implement typed contracts and strict parsing**

In `rag_structured_notes.py`, define frozen dataclasses for evidence and the final artifact:

```python
@dataclass(frozen=True)
class StructuredNoteEvidence:
    document_id: str
    document_name: str
    context: str
    sources: tuple[dict[str, Any], ...]
    truncated: bool


@dataclass(frozen=True)
class StructuredNoteArtifact:
    content_markdown: str
    sources: tuple[dict[str, Any], ...]
    document_type: str
    prompt_version: str
    truncated: bool
```

Set `STRUCTURED_NOTE_PROMPT_VERSION = "structured-note-v1"`. Strip one outer Markdown JSON fence, call `json.loads`, require the exact common fields from the test fixture, normalize citations to strings, and raise `StructuredNoteValidationError` instead of returning partial output.

- [ ] **Step 4: Implement prompt construction and rendering**

The prompt must explicitly include the allowed IDs and JSON schema. Render citations with `" ".join(f"[{value}]" for value in citations)`. Omit optional learning objective, code, and warnings when empty. Never add a technical-only heading just to satisfy a template.

The common renderer order is:

```python
parts = [
    f"# {value['title']}",
    "## 章前总览",
    _with_citations(value["overview"]),
]
if value["logic_chain"]:
    parts.extend(("## 逻辑链条", " → ".join(value["logic_chain"])))
for section in value["sections"]:
    parts.extend(_render_section(section))
parts.extend(("## 章节总结", _with_citations(value["summary"])))
return "\n\n".join(part for part in parts if part).strip()
```

- [ ] **Step 5: Run helper tests**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests/tools/test_rag_structured_notes.py -q --basetemp=.pytest-tmp-rag-structured-note-green
```

Expected: PASS.

- [ ] **Step 6: Commit the pure generation contract**

```powershell
git add hello_agents/tools/builtin/rag_structured_notes.py tests/tools/test_rag_structured_notes.py
git commit -m "feat: validate structured study note output"
```

---

### Task 4: RAG Evidence Snapshot and Assistant Generation Use Case

**Files:**
- Modify: `hello_agents/tools/builtin/rag_tool.py:243-403,1888-2000`
- Modify: `assistants/pdf_learning_assistant.py:340-505,763-817`
- Create: `tests/test_structured_note_assistant.py`
- Modify: `tests/tools/test_rag_tool_multi_document.py`

**Interfaces:**
- Consumes: `StructuredNoteEvidence`, `StructuredNoteArtifact`, parser, renderer, and existing `pipeline.get_document_summary_context(document_id, limit)`.
- Produces: `RAGTool.prepare_structured_note_evidence(document_id, limit=24)`.
- Produces: `RAGTool.generate_structured_note(evidence, progress_callback=None, cancel_event=None)`.
- Produces: `PDFLearningAssistant.generate_structured_note(document_id, progress_callback=None, cancel_event=None)`.
- Guarantees: RAG reads occur under the short user lock; the LLM call occurs after the lock is released.

- [ ] **Step 1: Write failing assistant isolation and lock-duration tests**

Create a lightweight assistant with fake history, RAG tool, runtime lock, and no real LLM. Test:

```python
def test_prepare_happens_under_lock_but_generation_runs_after_release():
    lock = TrackingLock()
    rag = FakeStructuredNoteRAG(lock)
    assistant = make_assistant(lock=lock, rag_tool=rag)

    _, artifact = assistant.generate_structured_note("doc-1")

    assert artifact.content_markdown == "# Note"
    assert rag.prepare_saw_lock is True
    assert rag.generate_saw_lock is False


def test_assistant_rejects_document_outside_user_history():
    assistant = make_assistant(documents=[{"document_id": "doc-1"}])

    with pytest.raises(ValueError, match="document was not found"):
        assistant.generate_structured_note("doc-other")


def test_assistant_returns_source_import_version_with_artifact():
    assistant = make_assistant(
        documents=[
            {
                "document_id": "doc-1",
                "document_name": "notes.pdf",
                "import_task_id": "import-9",
            }
        ]
    )

    source_version, artifact = assistant.generate_structured_note("doc-1")
    assert source_version == "import-9"
    assert artifact.prompt_version == "structured-note-v1"
```

- [ ] **Step 2: Write failing RAG evidence/citation tests**

In `tests/tools/test_rag_tool_multi_document.py`, add a fake pipeline returning start/middle/end chunks with page metadata and a fake LLM returning the valid JSON contract. Assert the evidence source payloads contain stable IDs, file names, page numbers, excerpts, and references, and that an invented citation raises `StructuredNoteValidationError`.

- [ ] **Step 3: Run the focused tests and verify failure**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_structured_note_assistant.py tests/tools/test_rag_tool_multi_document.py -q --basetemp=.pytest-tmp-structured-note-rag-red
```

Expected: FAIL because the three public methods do not exist.

- [ ] **Step 4: Implement the typed RAG methods**

`prepare_structured_note_evidence()` must:

1. call `get_document_summary_context(document_id, limit=24)`;
2. compute a context budget with a structured-note output reserve;
3. call the existing `_build_context(results, token_budget=budget, return_details=True)`;
4. convert fitted results to source dictionaries using the same fields as `_format_answer()`;
5. return `StructuredNoteEvidence` without mutating `_last_action_data`.

Add `prepare_results` to the existing imports from `rag_context`, and import the structured-note contracts/functions explicitly from `rag_structured_notes`; do not use a wildcard import.

`generate_structured_note()` must emit `generating` before the LLM call and `validating` before parsing. It raises `InterruptedError("structured note generation cancelled")` when the cancellation event is set before or after generation. It returns a typed `StructuredNoteArtifact`; it never returns a success-looking error string.

Add `structured_note` to the tool parameter description and `execute()` routing for compatibility. The route stores a JSON-safe artifact in `_last_action_data`, but assistant and worker code use the typed methods directly.

The typed methods use this control flow:

```python
def prepare_structured_note_evidence(
    self, document_id: str, limit: int = 24
) -> StructuredNoteEvidence:
    pipeline = self._get_pipeline()
    results = pipeline.get_document_summary_context(document_id, limit=limit)
    if not results:
        raise KeyError("document has no available evidence")
    prepared = prepare_results(results)
    fixed_prompt = build_structured_note_prompt(
        document_id=document_id,
        document_name=document_id,
        context="",
        allowed_citation_ids=tuple(
            item["citation_id"] for item in prepared
        ),
    )
    budget = self._context_budget(fixed_prompt)
    context, fitted, truncated = self._build_context(
        prepared, token_budget=budget, return_details=True
    )
    sources = tuple(self._structured_note_source(item) for item in fitted)
    document_name = next(
        (
            str(source["file_name"])
            for source in sources
            if source.get("file_name")
        ),
        document_id,
    )
    return StructuredNoteEvidence(
        document_id=document_id,
        document_name=document_name,
        context=context,
        sources=sources,
        truncated=bool(truncated),
    )


def generate_structured_note(
    self,
    evidence: StructuredNoteEvidence,
    progress_callback: Any = None,
    cancel_event: Any = None,
) -> StructuredNoteArtifact:
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("structured note generation cancelled")
    if progress_callback is not None:
        progress_callback("generating", 1, 1, "Generating structured note")
    allowed = tuple(source["citation_id"] for source in evidence.sources)
    prompt = build_structured_note_prompt(
        document_id=evidence.document_id,
        document_name=evidence.document_name,
        context=evidence.context,
        allowed_citation_ids=allowed,
    )
    raw = self._generate(prompt)
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("structured note generation cancelled")
    if progress_callback is not None:
        progress_callback("validating", 1, 1, "Validating citations")
    parsed = parse_structured_note(raw, allowed_citation_ids=set(allowed))
    return StructuredNoteArtifact(
        content_markdown=render_structured_note_markdown(parsed),
        sources=evidence.sources,
        document_type=str(parsed["document_type"]),
        prompt_version=STRUCTURED_NOTE_PROMPT_VERSION,
        truncated=evidence.truncated,
    )
```

Implement `_structured_note_source()` beside `_format_answer()` using the same stable ID, file name, page number, excerpt, reference, and truncation fields. It is a private `RAGTool` method, so the snippet above has no cross-layer dependency.

- [ ] **Step 5: Implement the assistant method with a short evidence lock**

Add this public signature:

```python
def generate_structured_note(
    self,
    document_id: str,
    progress_callback: Any = None,
    cancel_event: Any = None,
) -> tuple[str, StructuredNoteArtifact]:
```

Inside the user lock, reload history, find the exact `document_id`, require a non-empty `import_task_id`, and call `prepare_structured_note_evidence()`. Release the lock before `RAGTool.generate_structured_note()`. Return `(source_import_task_id, artifact)`.

- [ ] **Step 6: Run assistant and RAG tests**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_structured_note_assistant.py tests/tools/test_rag_structured_notes.py tests/tools/test_rag_tool_multi_document.py -q --basetemp=.pytest-tmp-structured-note-rag-green
```

Expected: PASS.

- [ ] **Step 7: Commit the generation use case**

```powershell
git add hello_agents/tools/builtin/rag_tool.py assistants/pdf_learning_assistant.py tests/test_structured_note_assistant.py tests/tools/test_rag_tool_multi_document.py
git commit -m "feat: generate cited structured study notes"
```

---

### Task 5: Authenticated Service, Runner, Cancellation, and Worker Lifecycle

**Files:**
- Create: `app/structured_note_service.py`
- Create: `app/structured_note_worker.py`
- Create: `tests/test_structured_note_service.py`
- Create: `tests/test_structured_note_worker.py`

**Interfaces:**
- Consumes: repositories from Task 2, assistant method from Task 4, `SessionRegistry`, and `UserRuntimeRegistry` background leases.
- Produces: `StructuredNoteService.start`, `get_task`, `get_current_note`, `cancel`, `retry`, `has_active_task_for_document`, `delete_document_state`, and `delete_all_state`.
- Produces: `StructuredNoteWorkerPool.start`, `stop`, `notify`, `signal_cancel`, and `signal_cancel_user`.
- Produces: `StructuredNoteTaskRunner.run(task)`.

- [ ] **Step 1: Write failing authenticated service tests**

Use the same fake-session pattern as `tests/test_import_service.py`. Cover authentication before repository access, document ownership from `runtime.history`, idempotent duplicate start, source-version capture, current-note decoding, stale-note detection, cancellation, and retry.

The key tests are:

```python
def test_start_uses_authenticated_user_and_current_document_version(service_app):
    result = service_app.service.start("valid-token", "doc-1")

    assert result.user_id == service_app.user_id
    assert result.document_id == "doc-1"
    assert result.source_import_task_id == "import-1"
    assert service_app.workers.notify_count == 1


def test_forged_session_is_rejected_before_repository_access(service_app):
    service_app.repository.fail_if_called = True

    with pytest.raises(InvalidSessionError):
        service_app.service.start("forged-token", "doc-1")


def test_other_users_document_cannot_be_started(service_app):
    with pytest.raises(KeyError, match="document was not found"):
        service_app.service.start("other-token", "doc-1")
```

- [ ] **Step 2: Write failing runner and pool tests**

Test successful atomic completion, stage/progress mapping, cancellation before generation, cancellation during generation, source-version change before persistence, validation failure preserving the old note, recovery on pool start, non-daemon threads, prompt shutdown, and runtime background lease release.

Use a fake assistant that blocks on an event so cancellation is deterministic; do not use timing-only assertions.

- [ ] **Step 3: Run service/worker tests and verify failure**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_structured_note_service.py tests/test_structured_note_worker.py -q --basetemp=.pytest-tmp-structured-note-worker-red
```

Expected: FAIL because service and worker modules do not exist.

- [ ] **Step 4: Implement authenticated service methods**

`start(session_token, document_id)` authenticates with `SessionRegistry.get_session()`, reloads `session.runtime.history`, and finds the document/version server-side. Generate a candidate UUID, pass it as `task_id`, and treat `returned.task_id == candidate_id` as the durable indication that a new task was inserted. Call `worker_pool.notify()` only in that case. Use a helper returning `(session, document_item)` so every mutation shares the same ownership check.

Implement the creation path as:

```python
def start(
    self, session_token: str, document_id: str
) -> StructuredNoteTaskRecord:
    session, document = self._owned_document(session_token, document_id)
    source_version = str(document.get("import_task_id") or "")
    if not source_version:
        raise ValueError("document has no committed import version")
    candidate_id = str(uuid.uuid4())
    task = self.task_repository.create_task(
        session.user_id,
        document_id,
        source_version,
        task_id=candidate_id,
    )
    if task.task_id == candidate_id:
        self.worker_pool.notify()
    return task
```

`get_current_note()` returns a JSON-safe dictionary with:

```python
{
    "note_id": record.note_id,
    "document_id": record.document_id,
    "content_markdown": record.content_markdown,
    "sources": json.loads(record.sources_json),
    "document_type": record.document_type,
    "prompt_version": record.prompt_version,
    "created_at": record.created_at,
    "is_stale": record.source_import_task_id != current_import_task_id,
}
```

`cancel()` and `retry()` authenticate first, then scope task retrieval to the session user. `retry()` signals `notify()` after the durable transition.

- [ ] **Step 5: Implement runner progress and safe completion**

The runner obtains a background runtime lease, creates `PDFLearningAssistant(runtime=runtime)`, registers a per-task cancellation event, and maps callbacks to progress ranges:

```python
_STAGE_PROGRESS = {
    "evidence": 10,
    "generating": 40,
    "validating": 82,
    "persisting": 94,
}
```

After generation, reacquire `runtime.lock`, reload history, and require that the document still exists and its `import_task_id` matches `task.source_import_task_id`. Update to `persisting/94`, then call `DocumentNoteRepository.complete_task()` with a new UUID. If the row was deleted or cancellation requested, do not recreate it.

Classify errors into stable codes: `cancelled`, `document_missing`, `source_changed`, `context_capacity`, `validation_failed`, `llm_unavailable`, and `unexpected_error`. Persist only `sanitize_error_message(error)[:500]`.

- [ ] **Step 6: Implement the worker pool**

Follow the import pool’s condition/queue pattern, but use two non-daemon workers. `start()` calls `recover_running()` before starting threads. Serialize running tasks per user through `blocked_user_ids` and the running-user index. `stop(wait=True)` sets cancellation on all running note events before joining. Runner crashes must be converted from `running` to safe `failed` or released for recovery; they must not kill the worker thread.

The cancellation registry is owned by the pool and exposed through exact methods:

```python
def signal_cancel(self, task_id: str) -> None:
    with self._condition:
        event = self._cancel_events.get(task_id)
        if event is not None:
            event.set()
        self._notify_generation += 1
        self._condition.notify_all()


def signal_cancel_user(self, user_id: str) -> None:
    with self._condition:
        for task_id, event in self._cancel_events.items():
            task = self.repository.get_task_by_id(task_id)
            if task is not None and task.user_id == user_id:
                event.set()
        self._notify_generation += 1
        self._condition.notify_all()


def stop(self, wait: bool = True) -> None:
    with self._condition:
        self._stop_event.set()
        for event in self._cancel_events.values():
            event.set()
        self._notify_generation += 1
        self._condition.notify_all()
        scheduler = self._scheduler_thread
        workers = list(self._worker_threads)
    if wait and scheduler is not None:
        scheduler.join()
        self._task_queue.join()
        for thread in workers:
            thread.join()
```

Add `StructuredNoteTaskRepository.get_task_by_id(task_id)` only for the pool’s internal cancellation registry; service and UI reads continue to require `user_id`.

- [ ] **Step 7: Run service and worker tests**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_structured_note_service.py tests/test_structured_note_worker.py -q --basetemp=.pytest-tmp-structured-note-worker-green
```

Expected: PASS.

- [ ] **Step 8: Commit the durable application service**

```powershell
git add app/structured_note_service.py app/structured_note_worker.py tests/test_structured_note_service.py tests/test_structured_note_worker.py
git commit -m "feat: run durable structured note tasks"
```

---

### Task 6: Runtime Injection and Document Deletion Consistency

**Files:**
- Modify: `app/runtime.py:18-112`
- Modify: `assistants/pdf_learning_assistant.py:857-981`
- Modify: `tests/test_user_runtime.py`
- Modify: `tests/test_user_mutation_coordination.py`
- Modify: `tests/test_structured_note_assistant.py`

**Interfaces:**
- Consumes: `StructuredNoteService.delete_document_state(user_id, document_id)` and `delete_all_state(user_id)`.
- Produces: `UserRuntime.structured_note_service` and `UserRuntimeRegistry.set_structured_note_service()`.
- Guarantees: deleting/clearing documents cancels and removes only matching AI note state; manual notes remain.

- [ ] **Step 1: Write failing runtime injection tests**

Extend runtime tests to prove the setter updates both an already-created runtime and future runtimes:

```python
def test_structured_note_service_injection_updates_existing_and_future_runtimes(runtime_app):
    first = runtime_app.registry.get_or_create(runtime_app.first_user_id)
    service = object()

    runtime_app.registry.set_structured_note_service(service)
    second = runtime_app.registry.get_or_create(runtime_app.second_user_id)

    assert first.structured_note_service is service
    assert second.structured_note_service is service
```

- [ ] **Step 2: Write failing deletion consistency tests**

Create assistant tests with a fake note service and assert:

```python
def test_delete_document_cleans_matching_structured_note_before_rag_delete():
    assistant, events = make_delete_assistant()
    assistant.current_document_id = "doc-1"

    assistant.delete_current_document()

    assert events[:2] == [
        ("structured-note-delete", "user-1", "doc-1"),
        ("rag-delete", "doc-1"),
    ]


def test_clear_documents_keeps_manual_notes_and_removes_ai_note_state():
    assistant, events = make_delete_assistant(
        history={"documents": [{"document_id": "doc-1"}], "questions": [], "notes": [{"note": "keep"}], "sessions": []}
    )

    assistant.clear_all_documents()

    assert ("structured-note-delete-all", "user-1") in events
    assert assistant.history_repository.load()["notes"] == [{"note": "keep"}]
```

- [ ] **Step 3: Run focused tests and verify failure**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_user_runtime.py tests/test_user_mutation_coordination.py tests/test_structured_note_assistant.py -q --basetemp=.pytest-tmp-structured-note-delete-red
```

Expected: FAIL because runtime injection and deletion calls are missing.

- [ ] **Step 4: Add runtime injection**

Mirror the existing import-service setter. Add the nullable field to `UserRuntime`, registry storage, `set_structured_note_service()`, and runtime construction. Keep the service out of `UserRuntime.close()` because the script-level owner stops the shared worker pool.

Use the same lock-protected injection shape:

```python
@dataclass
class UserRuntime:
    user_id: str
    paths: UserPaths
    lock: RLock
    coordinator: UserMutationCoordinator
    rag_tool: RAGTool
    memory_tool: MemoryTool
    history: HistoryRepository
    reports: ReportService
    recovery: RecoveryService
    active_session_count: int = 0
    active_background_count: int = 0
    import_task_service: object | None = None
    structured_note_service: object | None = None


def set_structured_note_service(self, service: object | None) -> None:
    with self._lock:
        self.structured_note_service = service
        for runtime in self._runtimes.values():
            runtime.structured_note_service = service
```

Initialize `self.structured_note_service = None` in `UserRuntimeRegistry.__init__()` and pass it into each new `UserRuntime`.

- [ ] **Step 5: Add note cleanup to destructive assistant operations**

Inside the existing per-user runtime lock, call note cleanup before removing RAG/history/files:

```python
structured_note_service = getattr(runtime, "structured_note_service", None)
if structured_note_service is not None:
    structured_note_service.delete_document_state(self.user_id, document_id)
```

For clear-all, call `delete_all_state(self.user_id)`. Service cleanup must signal process cancellation events before deleting durable rows. Do not call this path from `clear_all_notes()`.

- [ ] **Step 6: Run deletion/runtime tests**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_user_runtime.py tests/test_user_mutation_coordination.py tests/test_structured_note_assistant.py -q --basetemp=.pytest-tmp-structured-note-delete-green
```

Expected: PASS.

- [ ] **Step 7: Commit consistency integration**

```powershell
git add app/runtime.py assistants/pdf_learning_assistant.py tests/test_user_runtime.py tests/test_user_mutation_coordination.py tests/test_structured_note_assistant.py
git commit -m "feat: clean notes with document deletion"
```

---

### Task 7: Gradio Structured Note Tab and Script Lifecycle

**Files:**
- Modify: `ui/gradio_app.py:15-109,918-1538`
- Create: `tests/ui/test_structured_note_handlers.py`
- Modify: `tests/ui/test_authenticated_handlers.py`
- Modify: `tests/ui/test_import_handlers.py`

**Interfaces:**
- Consumes: `StructuredNoteService` and `StructuredNoteWorkerPool` from Task 5.
- Produces handlers: `load_structured_note`, `start_structured_note`, `poll_structured_note`, `cancel_structured_note`, `retry_structured_note`, and `refresh_structured_note_documents`.
- Produces UI state: selected document label, task ID, polling timer, status Markdown, note Markdown, and source Markdown.

- [ ] **Step 1: Write failing handler authentication and formatting tests**

Add every mutating handler to `STATE_CHANGING_HANDLERS` and every read handler to `AUTHENTICATED_READ_HANDLERS` in `tests/ui/test_authenticated_handlers.py`.

In `tests/ui/test_structured_note_handlers.py`, test the service adapter with a fake service:

```python
def test_start_handler_returns_existing_note_while_regeneration_runs(monkeypatch):
    module, service = load_ui_with_fake_structured_note_service(monkeypatch)
    service.current_note = {
        "content_markdown": "# Existing",
        "sources": [{"reference": "[S-one] doc-1 p.1"}],
        "is_stale": False,
    }

    task_id, status, note, sources, timer = module.start_structured_note(
        "valid-token", "notes.pdf | doc-1"
    )

    assert task_id == "task-1"
    assert "排队中" in status
    assert note == "# Existing"
    assert "[S-one]" in sources
    assert timer["active"] is True


def test_poll_terminal_status_stops_timer(monkeypatch):
    module, service = load_ui_with_fake_structured_note_service(monkeypatch)
    service.task.status = "succeeded"
    service.current_note = {
        "content_markdown": "# New",
        "sources": [],
        "is_stale": False,
    }

    status, note, sources, timer = module.poll_structured_note(
        "valid-token", "task-1", "notes.pdf | doc-1"
    )

    assert "已完成" in status
    assert note == "# New"
    assert timer["active"] is False
```

Also test stale banners, empty state, retry button behavior, cancel behavior, source excerpt formatting, and single-document selection rejection.

- [ ] **Step 2: Write failing script lifecycle tests**

Extend `tests/ui/test_import_handlers.py` so plain module import starts neither pool, supported launch starts both once, and `launch_app()` stops both pools in `finally` even when `demo.launch()` raises.

- [ ] **Step 3: Run UI tests and verify failure**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests/ui/test_structured_note_handlers.py tests/ui/test_authenticated_handlers.py tests/ui/test_import_handlers.py -q --basetemp=.pytest-tmp-structured-note-ui-red
```

Expected: FAIL because services, handlers, components, and lifecycle hooks are missing.

- [ ] **Step 4: Initialize services and worker lifecycle**

Add globals for repository, pool, and service. In `initialize_app_services()`, create them after `SessionRegistry`, then call `runtime_registry.set_structured_note_service(service)`. Add `start_structured_note_workers()` with the same idempotent script-owned lifecycle as import workers.

In `launch_app()` start both pools before `demo.launch()`. In `finally`, stop the structured-note pool first so no note generation reads a runtime while import workers and runtimes are shutting down; then stop the import pool.

The initialization order is:

```python
structured_note_task_repository = StructuredNoteTaskRepository(database_path)
document_note_repository = DocumentNoteRepository(database_path)
structured_note_worker_pool = StructuredNoteWorkerPool(
    structured_note_task_repository,
    document_note_repository,
    session_registry.runtime_registry,
)
structured_note_service = StructuredNoteService(
    session_registry,
    structured_note_task_repository,
    document_note_repository,
    structured_note_worker_pool,
)
session_registry.runtime_registry.set_structured_note_service(
    structured_note_service
)
```

`launch_app()` uses two explicit stop guards:

```python
def launch_app() -> None:
    initialize_app_services()
    try:
        start_import_workers()
        start_structured_note_workers()
        demo.launch(**load_launch_config().as_gradio_kwargs())
    finally:
        if structured_note_worker_pool is not None:
            structured_note_worker_pool.stop(wait=True)
        if import_worker_pool is not None:
            import_worker_pool.stop(wait=True)
```

- [ ] **Step 5: Implement authenticated handlers**

Every handler calls `_require_session(session_token)` before accessing the service. Parse the label with `primary_document_label()` and extract the last `|` segment as `document_id`.

Map stages to Chinese labels and render status with task progress. `poll_structured_note()` activates the timer only for `queued/running`; terminal statuses stop it. On `succeeded`, reload the current note. On failed/cancelled regeneration, keep returning the previously persisted note.

Use a shared response helper so start and poll return the same five outputs:

```python
def _structured_note_view(session_token, document_id, task):
    current = structured_note_service.get_current_note(
        session_token, document_id
    )
    note_markdown = current["content_markdown"] if current else ""
    sources_markdown = _format_answer_sources(
        current["sources"] if current else []
    )
    active = task.status in {"queued", "running"}
    return (
        task.task_id,
        _format_structured_note_task(task, current),
        note_markdown,
        sources_markdown,
        gr.update(active=active),
    )


def start_structured_note(session_token, selected_document):
    _require_session(session_token)
    label = primary_document_label(selected_document)
    document_id = label.split("|")[-1].strip()
    task = structured_note_service.start(session_token, document_id)
    return _structured_note_view(session_token, document_id, task)
```

- [ ] **Step 6: Add the Gradio tab**

Place a new “3. 结构化笔记” tab after document QA and renumber later visible tab labels. Use one non-multiselect document dropdown, buttons for generate/regenerate, cancel, retry, and refresh, one `gr.Timer`, status Markdown, a read-only `gr.Markdown` reader, and an initially collapsed sources accordion.

Add a small `STRUCTURED_NOTE_CSS` constant and construct the root as `gr.Blocks(title="文档 智能学习助手", css=STRUCTURED_NOTE_CSS)` for readable line width, heading rhythm, code blocks, warning blocks, and source cards. Use `elem_classes=["structured-note-reader"]` on the note Markdown. Do not imitate the future React sidebar inside Gradio.

Create the tab with explicit component state:

```python
with gr.Tab("3. 结构化笔记"):
    structured_note_document = gr.Dropdown(
        label="选择一篇文档", choices=[], multiselect=False, interactive=True
    )
    with gr.Row():
        structured_note_generate = gr.Button("生成学习笔记", variant="primary")
        structured_note_cancel = gr.Button("取消任务")
        structured_note_retry = gr.Button("重试")
        structured_note_refresh = gr.Button("刷新文档")
    structured_note_task_id = gr.State("")
    structured_note_timer = gr.Timer(1.0, active=False)
    structured_note_status = gr.Markdown()
    structured_note_reader = gr.Markdown(
        elem_classes=["structured-note-reader"]
    )
    with gr.Accordion("来源依据", open=False):
        structured_note_sources = gr.Markdown()
```

Bind generate to the five `_structured_note_view` outputs. Bind timer polling to status, reader, sources, and timer; keep task ID as the input state. Bind document change to `load_structured_note()` so a persisted note appears without starting a task.

- [ ] **Step 7: Run UI tests**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests/ui/test_structured_note_handlers.py tests/ui/test_authenticated_handlers.py tests/ui/test_import_handlers.py -q --basetemp=.pytest-tmp-structured-note-ui-green
```

Expected: PASS.

- [ ] **Step 8: Commit the Gradio experience**

```powershell
git add ui/gradio_app.py tests/ui/test_structured_note_handlers.py tests/ui/test_authenticated_handlers.py tests/ui/test_import_handlers.py
git commit -m "feat: add structured note Gradio workflow"
```

---

### Task 8: End-to-End Acceptance, Regression, and Documentation

**Files:**
- Create: `tests/integration/test_structured_note_acceptance.py`
- Modify: `README.md`
- Modify: `PROJECT_KNOWLEDGE.md`

**Interfaces:**
- Consumes: the complete service, worker, assistant, RAG, repository, and UI contracts.
- Produces: offline acceptance coverage and verified operating documentation.

- [ ] **Step 1: Write failing offline acceptance tests**

Build an isolated application fixture using real SQLite, `UserStorage`, `SessionRegistry`, repositories, service, and a one-worker pool with an offline assistant factory. Cover:

```python
def test_document_import_does_not_automatically_create_a_note_task(
    structured_note_app,
):
    token = structured_note_app.login("alice")
    structured_note_app.import_document(
        token, "chapter.pdf", document_id="doc-a"
    )

    assert structured_note_app.task_repository.get_active_task(
        structured_note_app.user_id("alice"), "doc-a"
    ) is None
    assert structured_note_app.note_repository.get_current(
        structured_note_app.user_id("alice"), "doc-a"
    ) is None


def test_generate_restart_login_and_read_current_note(structured_note_app):
    token = structured_note_app.login("alice")
    structured_note_app.import_document(token, "chapter.pdf", document_id="doc-a")
    task = structured_note_app.service.start(token, "doc-a")
    structured_note_app.run_task(task)

    structured_note_app.restart()
    token = structured_note_app.login("alice")
    note = structured_note_app.service.get_current_note(token, "doc-a")

    assert note["content_markdown"].startswith("# ")
    assert note["sources"][0]["page_number"] == 19


def test_same_filename_is_isolated_between_users(structured_note_app):
    alice = structured_note_app.login("alice")
    bob = structured_note_app.login("bob")
    structured_note_app.import_document(alice, "same.pdf", document_id="doc-a")
    structured_note_app.import_document(bob, "same.pdf", document_id="doc-b")

    structured_note_app.generate(alice, "doc-a", title="Alice note")

    with pytest.raises(KeyError):
        structured_note_app.service.get_current_note(bob, "doc-a")


def test_failed_regeneration_and_document_delete_preserve_expected_scopes(structured_note_app):
    token = structured_note_app.login("alice")
    structured_note_app.generate(token, "doc-a", title="Stable")
    structured_note_app.fail_next_generation("validation_failed")
    structured_note_app.regenerate(token, "doc-a")

    assert "Stable" in structured_note_app.service.get_current_note(
        token, "doc-a"
    )["content_markdown"]

    assistant = structured_note_app.assistant(token)
    assistant.select_document("chapter.pdf | doc-a")
    assistant.delete_current_document()

    with pytest.raises(KeyError, match="document was not found"):
        structured_note_app.service.get_current_note(token, "doc-a")
    assert assistant.history_repository.load()["notes"] == [{"note": "keep-me"}]
```

Add parametrized TXT, MD, and DOCX cases where `page_number` is null but `excerpt` and `reference` are non-empty. Add cancellation and interrupted-task recovery cases.

- [ ] **Step 2: Run acceptance tests against the completed feature**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests/integration/test_structured_note_acceptance.py -q --basetemp=.pytest-tmp-structured-note-acceptance-red
```

Expected: PASS. If a test fails, stop this task, identify the owning earlier task from the failing interface, correct that task’s implementation and focused test, then rerun this command before editing documentation. Do not weaken an acceptance assertion to match faulty behavior.

- [ ] **Step 3: Run the complete structured-note test slice**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_structured_note_repository.py tests/tools/test_rag_structured_notes.py tests/test_structured_note_assistant.py tests/test_structured_note_service.py tests/test_structured_note_worker.py tests/ui/test_structured_note_handlers.py tests/integration/test_structured_note_acceptance.py -q --basetemp=.pytest-tmp-structured-note-focused
```

Expected: PASS.

- [ ] **Step 4: Update user and project documentation**

Update `README.md` to state:

- users select one imported document and explicitly click generate;
- generation runs in the background with status, cancellation, retry, and regeneration;
- AI notes are read-only and separate from manual notes;
- citations show PDF pages or best available non-PDF location;
- regeneration keeps the current note until a valid replacement succeeds;
- document deletion removes only its AI note state;
- the first release is Gradio, while the approved React detail-page design remains future work.

Update `PROJECT_KNOWLEDGE.md` only with behavior proven by the green acceptance and full regression runs. Include the exact verification commands and results.

- [ ] **Step 5: Run the full repository suite**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest -q --basetemp=.pytest-tmp-structured-note-full
```

Expected: all tests pass; only environment-dependent tests already marked skip may be skipped.

- [ ] **Step 6: Start the Gradio app and perform a smoke check**

Run:

```powershell
.\venv\Scripts\python.exe .\ui\gradio_app.py
```

Verify registration/login, upload, document selection, note generation status, completed Markdown, sources, regeneration with old-note retention, cancellation, retry, logout/login persistence, and document deletion. Stop the app after the check.

- [ ] **Step 7: Inspect final diff and commit documentation/acceptance**

Run:

```powershell
git diff --check
git status --short
```

Confirm no upload, database, generated note, report, cache, `.env`, or temporary test directory is staged.

Commit:

```powershell
git add tests/integration/test_structured_note_acceptance.py README.md PROJECT_KNOWLEDGE.md
git commit -m "test: verify structured study note workflow"
```

---

## Final Verification Checklist

- [ ] One authenticated user can create, poll, cancel, retry, and regenerate a note for one owned document.
- [ ] Duplicate clicks return the existing active task.
- [ ] Completed notes survive refresh, logout/login, and application restart.
- [ ] Re-generation failure or cancellation leaves the previous note current.
- [ ] Every rendered knowledge section and summary uses only allowed citation IDs.
- [ ] PDF sources contain page numbers; non-PDF sources contain a usable excerpt/reference.
- [ ] Source document version changes mark the note stale and never trigger automatic generation.
- [ ] Document deletion cancels/deletes matching AI-note state and keeps manual notes.
- [ ] Cross-user and cross-document reads/mutations are rejected.
- [ ] Import, QA, search, summaries, manual notes, reports, recovery, and multi-user tests remain green.
- [ ] Only source, tests, and approved documentation are committed; runtime artifacts remain untracked/ignored.
