# Import Task Controls and History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add restart-safe cooperative pause/cancel controls, filtered task history, event timelines, batch history deletion, and opt-in retention to the durable import module.

**Architecture:** Extend the existing SQLite task state machine and single-process WorkerPool. Requested controls are durable, atomically claimed work; the import pipeline exposes a separate control-checkpoint callback whose exceptions are never swallowed, and the runner compensates task-owned RAG, History, Memory, and files before committing paused/cancelled. A focused maintenance component owns two-phase history deletion and retention while Gradio remains an authenticated read/control surface.

**Tech Stack:** Python 3.12, SQLite, Gradio 6.19.0, pytest 8.4.1, existing JSON/Qdrant RAG adapters, existing Memory and History repositories.

## Global Constraints

- Read `PROJECT_KNOWLEDGE.md` and treat current code/tests as authoritative.
- Run every Python command with `D:\python_self_agent\venv\Scripts\python.exe`.
- Preserve all unrelated dirty files; stage only paths explicitly owned by the current task.
- Do not add Redis, Celery, RQ, priorities, distributed locks, hard thread termination, or exact chunk-offset resume.
- Every public task/batch operation authenticates a session and derives immutable `user_id`; callers never provide user IDs or filesystem paths.
- Every repository query and transition filters by `user_id` plus task/batch identity.
- `pause_requested` and `cancel_requested` are durable control work; `control_claimed_at` makes claims atomic and restart-recoverable.
- The scheduler serializes all import/control work for one user and prioritizes control work before normal imports.
- Pause retains the validated staged file; cancelled is written only after compensation and staged cleanup succeed.
- All task-owned cleanup uses exact document/task IDs and controlled staging paths that reject traversal, symlinks, junctions, and reparse points.
- Persist only sanitized bounded errors/events; never persist or render secrets, private IDs, staging paths, absolute paths, or stack traces.
- History retention is disabled when unset or `0`; live Qdrant/Neo4j/LLM access is never required by the default suite.
- Parse `IMPORT_TASK_RETENTION_DAYS` once at composition time: unset/empty/`0` means disabled, a positive base-10 integer enables retention, and negative or malformed values raise `ValueError("IMPORT_TASK_RETENTION_DAYS must be a non-negative integer")` before workers start.
- An isolated implementation worktree must copy the existing ignored fixture `evals/data/multi_document_qa.json` into its own `evals/data/` directory when the source fixture exists; never stage that local copy. The complete offline suite depends on it.

## File and Responsibility Map

- `app/import_models.py`: control/history literals and immutable records.
- `app/database.py`: fresh schema plus idempotent legacy-table rebuild.
- `app/import_repository.py`: authoritative transitions, claims, events, history queries, and deletion lifecycle rows.
- `app/import_worker.py`: control-aware runner, scheduler priority, recovery, and maintenance integration.
- `app/import_maintenance.py`: two-phase staged cleanup, deletion recovery, and retention passes.
- `app/import_service.py`: authenticated task/batch controls, history/events, and manual deletion.
- `assistants/pdf_learning_assistant.py`: control callback forwarding and task-owned compensation boundary.
- `hello_agents/memory/rag/contracts.py`, `prepare.py`, `pipeline.py`, `qdrant_pipeline.py`, `hello_agents/tools/builtin/rag_tool.py`: I/O-free control types and non-swallowed import control checkpoints. Keeping the types here preserves the dependency direction `app/UI -> Assistant -> Tool -> RAG`.
- `hello_agents/tools/builtin/memory_tool.py`, `hello_agents/memory/types/episodic.py`: deterministic import-event removal.
- `ui/gradio_app.py`: active controls, confirmation, history filters/pagination, and event timeline.
- Focused tests live in new control/history test files so existing broad suites remain readable.

---

### Task 1: Control Models, SQLite Schema, and Legacy Migration

**Files:**
- Modify: `app/import_models.py`
- Modify: `app/database.py`
- Create: `tests/test_import_control_schema.py`
- Modify: `tests/test_import_models.py`

**Interfaces:**
- Consumes: existing `ImportTaskRecord`, `ImportBatchSummary`, `initialize_database()`.
- Produces: expanded `ImportStatus`/`ImportStage`; `BatchLifecycleState`; `ImportTaskEventRecord`; `ImportHistoryFilters`; `ImportHistoryPage`; `ImportBatchSummary.lifecycle_state`, paused/requested/cancelled counts; task control timestamps.

- [ ] **Step 1: Write fresh-schema and legacy-upgrade tests**

Create a legacy database with the current five-state CHECK constraint, insert a queued task, run `initialize_database()`, and assert the row survives while new states/columns/tables work:

```python
def test_initialize_database_upgrades_legacy_import_tasks(tmp_path):
    db_path = tmp_path / "legacy.db"
    create_legacy_import_schema_and_task(db_path)

    initialize_database(db_path)

    with connect(db_path) as connection:
        connection.execute(
            "update import_tasks set status = 'paused' where id = 'task-a'"
        )
        task = connection.execute(
            "select status, control_requested_at, control_claimed_at "
            "from import_tasks where id = 'task-a'"
        ).fetchone()
        batch = connection.execute(
            "select lifecycle_state from import_batches where id = 'batch-a'"
        ).fetchone()
        event_table = connection.execute(
            "select name from sqlite_master where name = 'import_task_events'"
        ).fetchone()

    assert tuple(task) == ("paused", None, None)
    assert batch["lifecycle_state"] == "active"
    assert event_table is not None
```

Also assert running/pause-requested uniqueness and that multiple cancel-requested tasks for one user are accepted.

- [ ] **Step 2: Run the schema tests and verify RED**

Run:

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest tests/test_import_control_schema.py tests/test_import_models.py -q --basetemp=.runtime/pytest-import-controls-schema-red
```

Expected: failure because new model fields, states, and migration are absent.

- [ ] **Step 3: Extend immutable model types**

Add these exact public shapes in `app/import_models.py`:

```python
ImportStatus = Literal[
    "queued", "running", "retry_wait", "pause_requested", "paused",
    "cancel_requested", "cancelled", "succeeded", "failed",
]
BatchLifecycleState = Literal["active", "deleting"]

@dataclass(frozen=True)
class ImportTaskEventRecord:
    event_id: int
    batch_id: str
    task_id: str
    user_id: str
    event_type: str
    status: ImportStatus
    stage: str
    message: str | None
    created_at: str

@dataclass(frozen=True)
class ImportHistoryFilters:
    statuses: tuple[ImportStatus, ...] = ()
    filename_query: str = ""
    created_from: str | None = None
    created_to: str | None = None
    batch_id: str | None = None

@dataclass(frozen=True)
class ImportHistoryPage:
    batches: tuple[ImportBatchSummary, ...]
    next_cursor: str | None
```

Add `control_requested_at` and `control_claimed_at` to `ImportTaskRecord`. Add `lifecycle_state`, `paused`, `pause_requested`, `cancel_requested`, and `cancelled` to `ImportBatchSummary`. Add `paused` and `cancelled` stage literals.

- [ ] **Step 4: Implement fresh schema and idempotent rebuild**

Update fresh SQL to include all statuses, batch lifecycle/error fields, and control timestamps. Rebuild only a legacy `import_tasks` table whose SQL lacks `pause_requested`:

```python
def _ensure_import_control_schema(conn: sqlite3.Connection) -> None:
    table_sql = conn.execute(
        "select sql from sqlite_master where type='table' and name='import_tasks'"
    ).fetchone()["sql"]
    if "pause_requested" not in table_sql:
        _rebuild_import_tasks_with_controls(conn)
    _ensure_column(conn, "import_batches", "lifecycle_state", "text not null default 'active'")
    _ensure_column(conn, "import_batches", "delete_requested_at", "text")
    _ensure_column(conn, "import_batches", "cleanup_error_code", "text")
    _ensure_column(conn, "import_batches", "cleanup_error_summary", "text")
```

The rebuild creates `import_tasks_control_upgrade`, copies every existing column plus null control timestamps, drops the old table, renames the new table, and recreates:

```sql
create unique index uq_import_tasks_running_user
on import_tasks(user_id)
where status in ('running', 'pause_requested');
```

Execute the `import_task_events` table/index script only after `_ensure_import_control_schema()` completes. This ordering prevents a legacy parent-table rebuild from cascading through a newly created child table. Create events with `(user_id, task_id, created_at, id)` and `(user_id, batch_id, created_at, id)` indexes and `on delete cascade` foreign keys. Re-running initialization must preserve all task and event rows.

- [ ] **Step 5: Run schema/model tests and existing database tests**

Run:

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest tests/test_import_control_schema.py tests/test_import_models.py tests/test_import_repository.py -q --basetemp=.runtime/pytest-import-controls-schema
```

Expected: PASS; running and pause-requested remain unique per user, while multiple cancel-requested rows are valid.

- [ ] **Step 6: Commit Task 1**

```powershell
git add app/import_models.py app/database.py tests/test_import_control_schema.py tests/test_import_models.py
git commit -m "feat: persist import control state"
```

---

### Task 2: Repository Control State Machine, Atomic Claims, and Events

**Files:**
- Modify: `app/import_repository.py`
- Create: `tests/test_import_control_repository.py`
- Modify: `tests/test_import_repository.py`

**Interfaces:**
- Consumes: Task 1 control states and event records.
- Produces:
  - `request_pause(user_id, task_id, now=None) -> ImportTaskRecord`
  - `request_cancel(user_id, task_id, now=None) -> ImportTaskRecord`
  - `resume_task(user_id, task_id, now=None) -> ImportTaskRecord`
  - batch equivalents returning `ImportBatchSummary`
  - `claim_next(...)` returning requested-control work before imports
  - `mark_paused`, `mark_cancelled`, `mark_control_failed`, `release_control_claim`
  - `list_task_events(user_id, task_id, limit=200)`.

- [ ] **Step 1: Write exhaustive transition and event tests**

Parameterize legal source states and assert illegal/foreign-user requests make no changes:

```python
@pytest.mark.parametrize("source", ["queued", "retry_wait", "paused", "running", "pause_requested"])
def test_request_cancel_moves_eligible_task_to_cancel_requested(repository, source):
    task = task_in_state(repository, source)
    changed = repository.request_cancel(task.user_id, task.task_id, now=NOW)
    assert changed.status == "cancel_requested"
    assert changed.control_requested_at == NOW
    assert repository.list_task_events(task.user_id, task.task_id)[-1].event_type == "cancel_requested"
```

Add event de-duplication assertions for concurrent duplicate commands and verify event messages are sanitized/truncated before persistence.

- [ ] **Step 2: Write atomic scheduling tests**

Create two users with queued imports and multiple cancel-requested tasks. Run concurrent claimers and assert:

```python
assert first.status == "cancel_requested"
assert first.control_claimed_at is not None
assert repository.get_task(first.user_id, sibling.task_id).control_claimed_at is None
assert import_for_same_user.status == "queued"
assert other_user_work.user_id != first.user_id
```

Verify stale control claims are released on shutdown and cleared by recovery.

- [ ] **Step 3: Run repository tests and verify RED**

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest tests/test_import_control_repository.py tests/test_import_repository.py -q --basetemp=.runtime/pytest-import-controls-repository-red
```

Expected: failures for missing transition and claim APIs.

- [ ] **Step 4: Implement transactional controls and event writes**

Use one private transition helper that updates and appends the event in the same transaction:

```python
def _set_control_state(
    self, conn, user_id: str, task_id: str, *,
    allowed: tuple[str, ...], target: str, event_type: str, timestamp: str,
) -> ImportTaskRecord:
    placeholders = ",".join("?" for _ in allowed)
    updated = conn.execute(
        f"""
        update import_tasks
        set status = ?, control_requested_at = ?, control_claimed_at = null,
            next_attempt_at = null, updated_at = ?
        where id = ? and user_id = ? and status in ({placeholders})
        """,
        (target, timestamp, timestamp, task_id, user_id, *allowed),
    )
    if updated.rowcount != 1:
        self._raise_transition_error(conn, user_id, task_id)
    row = conn.execute(
        "select * from import_tasks where id=? and user_id=?", (task_id, user_id)
    ).fetchone()
    self._insert_event(conn, row, event_type, None, timestamp)
    return _task_from_row(row)
```

`request_pause` allows queued/retry-wait -> paused and running -> pause-requested. `request_cancel` allows queued/retry-wait/paused/running/pause-requested plus failed only when error code is `pause_cleanup_failed` or `cancel_cleanup_failed`. `resume_task` allows paused -> queued, clears retry/control timestamps, retains IDs/counters/progress, and appends `resumed`.

- [ ] **Step 5: Implement priority claim/release/recovery**

Within `BEGIN IMMEDIATE`, select unclaimed requested-control work first, excluding blocked users and users already executing in another process:

```sql
select id, status from import_tasks t
where status in ('pause_requested','cancel_requested')
  and control_claimed_at is null
  and not exists (
      select 1 from import_tasks x
      where x.user_id=t.user_id and x.id<>t.id
        and (x.status in ('running','pause_requested') or x.control_claimed_at is not null)
  )
order by control_requested_at, created_at, id
```

Atomically set only `control_claimed_at` for control work. If none exists, claim queued/retry-ready imports only for users with no pause/cancel-requested rows. `release_control_claim` clears the timestamp without changing requested status. Startup recovery clears every stale `control_claimed_at` and leaves paused unchanged.

- [ ] **Step 6: Implement terminal control methods and event reads**

`mark_paused` conditionally accepts `pause_requested`; `mark_cancelled` conditionally accepts `cancel_requested`. They may be called either by the already-running attempt that observed the request or by a scheduler-claimed cleanup-only attempt, so the state predicate—not a non-null control claim—is authoritative. Both clear claim/request fields, set stage/finished timestamps, and append events. `mark_control_failed` writes `failed` with whitelisted safe codes and appends failure. `list_task_events` orders by `created_at, id`, caps limit at 200, and filters by user/task.

- [ ] **Step 7: Run repository, concurrency, and migration tests**

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest tests/test_import_control_repository.py tests/test_import_repository.py tests/test_import_control_schema.py -q --basetemp=.runtime/pytest-import-controls-repository
```

Expected: PASS, including concurrent claims and cross-user isolation.

- [ ] **Step 8: Commit Task 2**

```powershell
git add app/import_repository.py tests/test_import_control_repository.py tests/test_import_repository.py
git commit -m "feat: add durable import control transitions"
```

---

### Task 3: Non-Swallowed Import Control Checkpoints

**Files:**
- Modify: `hello_agents/memory/rag/contracts.py`
- Modify: `hello_agents/memory/rag/prepare.py`
- Modify: `hello_agents/memory/rag/pipeline.py`
- Modify: `hello_agents/memory/rag/qdrant_pipeline.py`
- Modify: `hello_agents/tools/builtin/rag_tool.py`
- Modify: `assistants/pdf_learning_assistant.py`
- Create: `tests/memory/rag/test_import_control_checkpoints.py`

**Interfaces:**
- Consumes: existing `progress_callback`; Task 2 repository will provide the runner callback in Task 4.
- Produces: `ControlAction`, `ImportControlSignal`, `ControlCheckpoint`, and `PDFLearningAssistant.load_document(..., control_checkpoint=None)` propagated through both JSON and Qdrant imports.

- [ ] **Step 1: Write propagation and exception tests**

Test parsing, chunking, embedding batch, JSON persistence batch, Qdrant upsert batch, and pre-commit checkpoints. The essential assertion is that control signals propagate while progress callback failures keep their existing warning-only behavior:

```python
def test_qdrant_import_does_not_swallow_control_signal(pipeline, document):
    observed = []
    def checkpoint(stage):
        observed.append(stage)
        if stage == "embedding":
            raise ImportControlSignal("pause")

    with pytest.raises(ImportControlSignal, match="pause"):
        pipeline.add_document(document, control_checkpoint=checkpoint)

    assert "parsing" in observed
    assert "chunking" in observed
    assert "embedding" in observed
```

- [ ] **Step 2: Run checkpoint tests and verify RED**

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest tests/memory/rag/test_import_control_checkpoints.py tests/memory/rag/test_import_progress.py -q --basetemp=.runtime/pytest-import-control-checkpoints-red
```

Expected: missing `control_checkpoint` parameters and signal types.

- [ ] **Step 3: Add I/O-free control types in the RAG contract layer**

```python
from typing import Callable, Literal

ControlAction = Literal["pause", "cancel"]
ControlCheckpoint = Callable[[str], None]

class ImportControlSignal(RuntimeError):
    retryable = False
    def __init__(self, action: ControlAction):
        super().__init__(action)
        self.action = action
```

- [ ] **Step 4: Thread the checkpoint through every import backend**

Add keyword-only `control_checkpoint: ControlCheckpoint | None = None` beside progress callbacks. Call it directly through a small helper that does not catch exceptions:

```python
def run_control_checkpoint(callback, stage: str) -> None:
    if callback is not None:
        callback(stage)
```

Invoke it before/after parsing, after chunk preparation, before each embedding/upsert/persistence batch, and immediately before cache/History commit. Do not reuse the progress helper because progress exceptions are intentionally swallowed.

- [ ] **Step 5: Forward from RAGTool and Assistant**

`RAGTool` forwards the keyword only for `add_document`. `PDFLearningAssistant.load_document` accepts it, passes it in `add_kwargs`, and calls `control_checkpoint("committing")` before History/Memory commit.

- [ ] **Step 6: Run JSON/Qdrant parity and existing progress tests**

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest tests/memory/rag/test_import_control_checkpoints.py tests/memory/rag/test_import_progress.py tests/tools/test_rag_tool_backend_contract.py tests/tools/test_rag_tool_multi_document.py -q --basetemp=.runtime/pytest-import-control-checkpoints
```

Expected: PASS on offline adapters; no callback behavior regression.

- [ ] **Step 7: Commit Task 3**

```powershell
git add hello_agents/memory/rag/contracts.py hello_agents/memory/rag/prepare.py hello_agents/memory/rag/pipeline.py hello_agents/memory/rag/qdrant_pipeline.py hello_agents/tools/builtin/rag_tool.py assistants/pdf_learning_assistant.py tests/memory/rag/test_import_control_checkpoints.py
git commit -m "feat: propagate import control checkpoints"
```

---

### Task 4: Task-Owned Compensation and Control-Aware Runner

**Files:**
- Modify: `hello_agents/memory/types/episodic.py`
- Modify: `hello_agents/tools/builtin/memory_tool.py`
- Modify: `assistants/pdf_learning_assistant.py`
- Modify: `app/import_worker.py`
- Create: `tests/assistants/test_import_control_compensation.py`
- Create: `tests/test_import_control_worker.py`
- Modify: `tests/test_import_worker.py`

**Interfaces:**
- Consumes: Task 2 claim/terminal methods; Task 3 signal/checkpoint.
- Produces:
  - `MemoryTool.remove_import_event(import_task_id) -> bool`
  - `PDFLearningAssistant.compensate_import(document_id, import_task_id) -> None`
  - runner branches for import, pause cleanup, and cancel cleanup.

- [ ] **Step 1: Write Memory and Assistant compensation tests**

Create two document/task identities and assert exact cleanup preserves the other document, questions, notes, RAG IDs, and Memory event:

```python
assistant.compensate_import(document_id="doc-a", import_task_id="task-a")

assert assistant.rag_tool.list_documents() == ["doc-b"]
assert [d["document_id"] for d in assistant.history_repository.load()["documents"]] == ["doc-b"]
assert assistant.memory_tool.has_import_event("task-a") is False
assert assistant.memory_tool.has_import_event("task-b") is True
assert assistant.history_repository.load()["notes"] == original_notes
```

Inject RAG, History, and episodic-vector cleanup failures and assert the method raises without reporting success.

- [ ] **Step 2: Write runner checkpoint/control tests**

Use event-gated fake assistants to request pause/cancel at every Task 3 checkpoint. Assert no retry counter is consumed, pause retains staging, cancel removes it, and compensation failure writes `pause_cleanup_failed`/`cancel_cleanup_failed`.

- [ ] **Step 3: Run tests and verify RED**

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest tests/assistants/test_import_control_compensation.py tests/test_import_control_worker.py -q --basetemp=.runtime/pytest-import-control-worker-red
```

- [ ] **Step 4: Add deterministic import-event removal**

Expose exact episodic deletion without reaching into private maps from Assistant:

```python
def remove_import_event(self, import_task_id: str) -> bool:
    memory_id = self._import_event_id(import_task_id)
    episodic = self.memory_manager.memory_types.get("episodic")
    if episodic is None:
        return False
    removed = episodic.delete_ids([memory_id])
    if removed:
        self.memory_manager._save_snapshot()
    return bool(removed)
```

`EpisodicMemory.delete_ids()` filters to IDs present in `_episodes`, delegates to its compensating `_delete_episode_ids`, then updates `_episodes` and `sessions` only after durable deletion succeeds, returning the number removed. Extract `_import_event_id()` and reuse it in `ensure_import_event`; `MemoryTool` never reaches into episodic private maps.

- [ ] **Step 5: Implement Assistant compensation under the user write lock**

Reload History, verify the matching document is owned by `import_task_id`, delete that exact RAG document, delete the History document/questions, remove the import event, and refresh in-memory history. Do not delete notes or unrelated documents. Make repeated calls succeed when artifacts are already absent.

- [ ] **Step 6: Implement repository-backed runner checkpoints**

The callback reloads the task and raises only for requested states:

```python
def _control_checkpoint(self, task):
    def check(_stage: str) -> None:
        current = self.repository.get_task(task.user_id, task.task_id)
        if current is None:
            raise ImportControlSignal("cancel")
        if current.status == "pause_requested":
            raise ImportControlSignal("pause")
        if current.status == "cancel_requested":
            raise ImportControlSignal("cancel")
    return check
```

Call the same checkpoint once more immediately after `load_document` returns and before `mark_succeeded`, closing the race after the Assistant's internal pre-commit checkpoint. Catch `ImportControlSignal` before the ordinary failure handler. Compensate, remove attempt files, retain/remove staged source according to action, and call `mark_paused` or `mark_cancelled`. On cleanup error call `mark_control_failed` with a safe code. For a claimed cancel-requested task that never began import, execute cleanup-only without calling `load_document`.

- [ ] **Step 7: Update WorkerPool claim release and crash fallback**

Dispatch both normal and requested-control records to `runner.run`. On shutdown release `control_claimed_at` for control work, otherwise use existing `release_claim`. Crash fallback recognizes claimed requested-control states and writes a control failure or releases the control claim; it never overwrites paused/cancelled/succeeded/failed.

- [ ] **Step 8: Run compensation, runner, idempotency, and Memory tests**

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest tests/assistants/test_import_control_compensation.py tests/test_import_control_worker.py tests/test_import_worker.py tests/assistants/test_import_idempotency.py tests/memory/test_episodic_vector_cleanup.py -q --basetemp=.runtime/pytest-import-control-worker
```

Expected: PASS; no real backend required.

- [ ] **Step 9: Commit Task 4**

```powershell
git add hello_agents/memory/types/episodic.py hello_agents/tools/builtin/memory_tool.py assistants/pdf_learning_assistant.py app/import_worker.py tests/assistants/test_import_control_compensation.py tests/test_import_control_worker.py tests/test_import_worker.py
git commit -m "feat: execute import pause and cancellation safely"
```

---

### Task 5: Authenticated Task and Batch Controls

**Files:**
- Modify: `app/import_service.py`
- Create: `tests/test_import_control_service.py`
- Modify: `tests/test_import_service.py`
- Modify: `tests/test_runtime_import_leases.py`
- Modify: `tests/assistants/test_import_active_guard.py`

**Interfaces:**
- Consumes: Task 2 repository controls and Task 4 worker notification.
- Produces six task/batch control methods: `pause_task`, `resume_task`, `cancel_task`, `pause_batch`, `resume_batch`, and `cancel_batch`. History/deletion methods arrive in Task 6.

- [ ] **Step 1: Write authorization and synchronization tests**

For every new method, cover missing/expired tokens, cross-user IDs, stale state, duplicate commands, and zero repository/file mutation on rejection. **Approved correction (2026-09-05):** pause/cancel must durably commit during real Assistant RAG work. Use a short per-runtime `import_control_lock`, separate from the data-write lock, for task/batch requests. Clear/delete hold the data-write lock then the request gate across active guards and mutation, including cleanup-failed cancellation races. No request path acquires the data-write lock while holding the gate. Resume, retry, staging submission, import writes, and compensation retain their existing data-write synchronization. SQLite ownership/lifecycle predicates and conditional transitions remain authoritative.

```python
with runtime.lock:
    thread = Thread(target=service.cancel_task, args=(token, task_id))
    thread.start()
    thread.join(timeout=3)
    assert not thread.is_alive()
assert repository.get_task(user_id, task_id).status == "cancel_requested"
```

- [ ] **Step 2: Run tests and verify RED**

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest tests/test_import_control_service.py tests/test_import_service.py -q --basetemp=.runtime/pytest-import-control-service-red
```

- [ ] **Step 3: Implement the authenticated control facade**

Each method resolves one session and derives user ID. Pause/cancel take `_control_lock(session)`; resume retains `_runtime_lock(session)`. Delegate to the matching repository method, release the gate/lock, notify only after a change, and return the current batch summary. Example:

```python
def cancel_task(self, session_token: str, task_id: str) -> ImportBatchSummary:
    session = self._session(session_token)
    user_id = str(session.user_id)
    with self._control_lock(session):
        task = self.repository.request_cancel(user_id, task_id)
        summary = self.repository.get_batch(user_id, task.batch_id)
    self.worker_pool.notify()
    if summary is None:
        raise KeyError("import batch was not found")
    return summary
```

Batch methods delegate to one repository transaction, not task-by-task service loops.

- [ ] **Step 4: Extend destructive-operation guards**

Update repository active predicates and Assistant guard tests so paused/requested-control work blocks clear-all and a requested control for a matching document blocks delete-current until compensation reaches a terminal/paused state.

- [ ] **Step 5: Run service/runtime/guard tests**

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest tests/test_import_control_service.py tests/test_import_service.py tests/test_runtime_import_leases.py tests/assistants/test_import_active_guard.py -q --basetemp=.runtime/pytest-import-control-service
```

- [ ] **Step 6: Commit Task 5**

```powershell
git add app/import_service.py tests/test_import_control_service.py tests/test_import_service.py tests/test_runtime_import_leases.py tests/assistants/test_import_active_guard.py
git commit -m "feat: expose authenticated import controls"
```

---

### Task 6: History, Event Queries, Two-Phase Deletion, and Retention

**Files:**
- Create: `app/import_maintenance.py`
- Modify: `app/import_models.py`
- Modify: `app/import_repository.py`
- Modify: `app/import_service.py`
- Modify: `app/import_worker.py`
- Create: `tests/test_import_history.py`
- Create: `tests/test_import_maintenance.py`

**Interfaces:**
- Consumes: Task 1 history records, Task 2 events, controlled staging resolution.
- Produces:
  - `list_history(user_id, filters, cursor, limit) -> ImportHistoryPage`
  - `mark_batch_deleting`, `list_deleting_batches`, `finish_batch_deletion`, `record_batch_cleanup_failure`
  - `ImportHistoryMaintenance.delete_batch`, `.resume_batch_deletion`, `.recover_deleting`, `.run_retention`
  - service `list_task_history`, `list_task_events`, `delete_batch_history`.

- [ ] **Step 1: Write filtered cursor-history tests**

Create equal-timestamp batches for two users. Assert opaque cursor pagination is stable, bounded, and applies all filters before the cursor:

```python
page1 = repository.list_history(user_a, ImportHistoryFilters(statuses=("failed",)), None, 2)
page2 = repository.list_history(user_a, ImportHistoryFilters(statuses=("failed",)), page1.next_cursor, 2)
assert not ({b.batch_id for b in page1.batches} & {b.batch_id for b in page2.batches})
assert all(batch.user_id == user_a for batch in (*page1.batches, *page2.batches))
```

Reject invalid cursor, limit above 100, invalid dates, and raw SQL wildcard abuse.

- [ ] **Step 2: Write deletion/recovery/retention tests**

Cover nonterminal rejection, success-document preservation, failed/cancelled staged cleanup, crash after `deleting`, missing files, SQLite busy, symlink/junction rejection, cross-user isolation, default retention disabled, age threshold, and bounded cleanup count.

- [ ] **Step 3: Run history/maintenance tests and verify RED**

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest tests/test_import_history.py tests/test_import_maintenance.py -q --basetemp=.runtime/pytest-import-history-red
```

- [ ] **Step 4: Implement stable cursor history and event service methods**

Encode cursor payload as URL-safe base64 JSON containing only `created_at` and `batch_id`; validate exact keys/types on decode. Clamp limit to 1..100. Use escaped `LIKE` for filename search and an `exists` subquery for task status/filename filters. Exclude `lifecycle_state='deleting'`.

- [ ] **Step 5: Implement two-phase maintenance**

```python
class ImportHistoryMaintenance:
    def __init__(self, repository, storage):
        self.repository = repository
        self.storage = storage

    def delete_batch(self, user_id: str, batch_id: str) -> None:
        summary = self.repository.mark_batch_deleting(user_id, batch_id)
        self._finish_marked_batch(user_id, summary)

    def resume_batch_deletion(self, user_id: str, batch_id: str) -> None:
        summary = self.repository.get_deleting_batch(user_id, batch_id)
        self._finish_marked_batch(user_id, summary)

    def _finish_marked_batch(self, user_id, summary) -> None:
        try:
            for task in summary.tasks:
                staged = self.storage.resolve_staged_import_path(
                    user_id, summary.batch_id, task.task_id,
                    task.file_suffix, task.staged_relative_path,
                )
                staged.unlink(missing_ok=True)
            self.repository.finish_batch_deletion(user_id, summary.batch_id)
        except Exception as error:
            self.repository.record_batch_cleanup_failure(
                user_id, summary.batch_id, error
            )
            raise
```

`mark_batch_deleting` accepts only fully terminal active batches. `finish_batch_deletion` deletes the batch row and relies on cascades. Error persistence uses canonical sanitization. Recovery lists bounded deleting batches. Retention queries fully terminal active batches older than the UTC threshold and calls the same method.

`recover_deleting(limit)` calls `resume_batch_deletion` directly; it must never call `mark_batch_deleting` for an already-deleting row. Manual deletion calls `mark_batch_deleting` once under the user's runtime lock, releases the lock, then finishes filesystem/row cleanup. Repeated recovery tolerates missing staging files. No cleanup path resolves or unlinks the formal document location.

- [ ] **Step 6: Integrate startup and bounded maintenance**

`ImportWorkerPool` receives a maintenance instance plus the already-validated `retention_days=0`. `start()` calls `recover_deleting(limit=20)`. The scheduler performs at most 10 retention deletions after an idle timeout and no more than once per hour. Maintenance exceptions log safely and never stop import scheduling.

- [ ] **Step 7: Add authenticated history/deletion methods**

Service parses public filter values into `ImportHistoryFilters`, delegates with current user ID, and returns safe records. `delete_batch_history` holds the runtime lock for the durable delete request, then lets maintenance complete cleanup; it never accepts a path or user ID.

- [ ] **Step 8: Run history, maintenance, storage-safety, and worker tests**

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest tests/test_import_history.py tests/test_import_maintenance.py tests/test_staged_path_safety.py tests/test_import_worker.py tests/test_import_service.py -q --basetemp=.runtime/pytest-import-history
```

- [ ] **Step 9: Commit Task 6**

```powershell
git add app/import_maintenance.py app/import_models.py app/import_repository.py app/import_service.py app/import_worker.py tests/test_import_history.py tests/test_import_maintenance.py
git commit -m "feat: add import history and retention cleanup"
```

---

### Task 7: Active Import Controls in Gradio

**Files:**
- Modify: `ui/gradio_app.py`
- Create: `tests/ui/test_import_controls.py`
- Modify: `tests/ui/test_import_handlers.py`
- Modify: `tests/ui/test_authenticated_handlers.py`

**Interfaces:**
- Consumes: Task 5 service controls and expanded summaries.
- Produces handlers for task/batch pause/resume/cancel, confirmation state, localized statuses/stages, and selection invalidation.

- [ ] **Step 1: Write handler authorization and stale-selection tests**

Parameterize missing, forged, expired, cross-user, wrong-batch, wrong-state, and duplicate actions. Assert service receives only token plus hidden IDs. Add formatting assertions for all new states and verify raw IDs/paths/secrets never render.

- [ ] **Step 2: Write Blocks binding tests**

Assert task actions consume `[session_token, import_batch_dropdown, selected_import_task_id]`; cancel also consumes a confirmation checkbox/state. Batch actions use the visible batch state plus confirmation for cancel. Every output clears or retains selection according to eligibility and has exact component counts.

- [ ] **Step 3: Run UI tests and verify RED**

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest tests/ui/test_import_controls.py tests/ui/test_import_handlers.py tests/ui/test_authenticated_handlers.py -q --basetemp=.runtime/pytest-import-controls-ui-red
```

- [ ] **Step 4: Implement safe handlers**

Add handlers with one shared selection validator:

```python
def pause_import_task(session_token, batch_id, selected_task):
    _require_session(session_token)
    selected_batch_id, task_id = _require_import_task_selection(
        batch_id, selected_task
    )
    summary = import_service.pause_task(session_token, task_id)
    return _render_import_action(summary, selected_batch_id, "")
```

Implement matching resume/cancel and batch handlers. Cancel handlers reject unless the explicit confirmation value is true. Catch only expected transition/not-found errors and convert them to safe `gr.Error` messages.

- [ ] **Step 5: Extend active layout and polling**

Add localized status counts, selected-task pause/resume/cancel buttons, batch buttons, and confirmation controls. Timer remains `queue=False` and read-only. Login loads active batches; logout clears task selection and confirmation. Selection is retained only while the selected task remains eligible for at least one action.

- [ ] **Step 6: Run UI and lazy-initialization regressions**

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest tests/ui/test_import_controls.py tests/ui/test_import_handlers.py tests/ui/test_authenticated_handlers.py tests/test_corruption_recovery.py::TestUISessionRejection -q --basetemp=.runtime/pytest-import-controls-ui
```

- [ ] **Step 7: Commit Task 7**

```powershell
git add ui/gradio_app.py tests/ui/test_import_controls.py tests/ui/test_import_handlers.py tests/ui/test_authenticated_handlers.py
git commit -m "feat: add import pause and cancel controls"
```

---

### Task 8: History UI, End-to-End Acceptance, and Documentation

**Files:**
- Modify: `ui/gradio_app.py`
- Create: `tests/ui/test_import_history_ui.py`
- Create: `tests/integration/test_import_controls_acceptance.py`
- Modify: `tests/integration/test_batch_import_acceptance.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: Task 6 history/events/deletion and Task 7 active control UI.
- Produces history filters, cursor navigation, task timeline, confirmed batch deletion, final offline acceptance, and user documentation.

- [ ] **Step 1: Write history UI tests**

Cover filter parsing, first/next page cursor state, empty pages, timeline expansion, deletion confirmation, session rejection, ID redaction, and logout clearing. Ensure history refresh is manual/filter-driven and not part of the one-second active poll.

- [ ] **Step 2: Write integrated offline acceptance tests**

Use real SQLite repository/service/WorkerPool/UserRuntimeRegistry with fake RAG/Assistant adapters. Implement these named tests with concrete setup and assertions:

- `test_pause_restart_resume_preserves_identity_without_duplicates`: block the fake at a checkpoint, request pause, wait for `paused`, replace the pool, resume, and assert the same task ID succeeds once with one formal file/RAG document/History row/Memory event.
- `test_cancel_during_each_checkpoint_removes_all_task_owned_artifacts`: parameterize every checkpoint; release the fake after cancel is durable and assert `cancelled`, missing staging/formal files, absent RAG document, History row, and Memory event.
- `test_batch_cancel_serializes_cleanup_and_preserves_succeeded_tasks`: finish the first task, block the second, cancel the batch, and assert the first remains succeeded while remaining tasks cancel one at a time.
- `test_compensation_failure_can_be_cancelled_with_cleanup_only`: inject one compensation failure, assert safe `failed`, retry cancel with cleanup restored, and assert `cancelled` without a second import attempt.
- `test_history_filters_events_and_cross_user_controls_are_isolated`: create two users' batches, query every filter/event page as the owner, and assert foreign task/batch controls and reads do not mutate or disclose either user's IDs.
- `test_interrupted_history_delete_recovers_without_deleting_document`: mark a terminal batch deleting, simulate restart, run recovery, and assert only staging/history rows disappear while the formal document remains.
- `test_retention_is_disabled_by_default_and_bounded_when_enabled`: prove unset/zero performs no deletion, then use a positive threshold with more than the per-pass limit and assert exactly the bounded eligible set is removed.

Use `threading.Event` barriers rather than sleeps for concurrency, assert event order through repository reads, and monkeypatch constructors so any real network/backend construction fails the test.

- [ ] **Step 3: Run acceptance/UI tests and verify RED**

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest tests/ui/test_import_history_ui.py tests/integration/test_import_controls_acceptance.py -q --basetemp=.runtime/pytest-import-controls-acceptance-red
```

- [ ] **Step 4: Implement history UI**

Add status multi-select, filename text, from/to dates, history table, previous/next cursor state, event timeline, delete confirmation, and delete button. Handler inputs never include user ID or paths. Render event type/status/stage/time/safe message only.

- [ ] **Step 5: Document controls and retention**

Update README with state meanings, cooperative checkpoint behavior, pause restart-from-task semantics, cancellation cleanup/failure behavior, batch controls, history filters/deletion, `IMPORT_TASK_RETENTION_DAYS=0` default, and first-version exclusions.

- [ ] **Step 6: Run all import/control/history regression suites**

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest tests/test_import_models.py tests/test_import_control_schema.py tests/test_import_repository.py tests/test_import_control_repository.py tests/test_import_worker.py tests/test_import_control_worker.py tests/test_import_service.py tests/test_import_control_service.py tests/test_import_history.py tests/test_import_maintenance.py tests/assistants/test_import_control_compensation.py tests/memory/rag/test_import_control_checkpoints.py tests/ui/test_import_controls.py tests/ui/test_import_history_ui.py tests/integration/test_batch_import_acceptance.py tests/integration/test_import_controls_acceptance.py -q --basetemp=.runtime/pytest-import-controls-focused
```

Expected: PASS with only explicit environment-dependent skips.

- [ ] **Step 7: Run the complete offline suite**

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest -q --basetemp=.runtime/pytest-import-controls-full
```

Expected: all default tests pass; no real Qdrant, Neo4j, LLM, or project runtime data access.

- [ ] **Step 8: Run static and side-effect gates**

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m compileall app assistants hello_agents ui
$env:PDF_ASSISTANT_DATA_DIR = Join-Path $env:TEMP ("import-control-import-check-" + [guid]::NewGuid().ToString("N"))
D:\python_self_agent\venv\Scripts\python.exe -c "from pathlib import Path; import os; root=Path(os.environ['PDF_ASSISTANT_DATA_DIR']); import ui.gradio_app as app; print(type(app.demo).__name__); assert not root.exists()"
git diff --check
```

Expected: `Blocks`, no probe data root, successful compilation, and no whitespace errors.

- [ ] **Step 9: Commit Task 8**

```powershell
git add ui/gradio_app.py tests/ui/test_import_history_ui.py tests/integration/test_import_controls_acceptance.py tests/integration/test_batch_import_acceptance.py README.md
git commit -m "test: verify import controls and history lifecycle"
```

- [ ] **Step 10: Independent whole-branch review**

Review the complete feature range against `docs/superpowers/specs/2026-08-09-import-task-controls-history-design.md`. The gate passes only with explicit Overall/Spec/Quality PASS and no Critical or Important findings. Fix every blocking finding, rerun affected tests, and repeat the review before delivery.

---

## Plan Self-Review

- Spec coverage: all state transitions, durable claims, checkpoints, compensation, service authorization, active UI, history/events, two-phase deletion, retention, recovery, documentation, and final review map to Tasks 1-8.
- Placeholders: no TBD/TODO/follow-up-only steps; every code task names exact interfaces, files, commands, and expected results.
- Type consistency: `pause_requested`, `paused`, `cancel_requested`, `cancelled`, `control_requested_at`, `control_claimed_at`, history page/filter records, and service/repository method names are defined before downstream use.
- Dependency order: schema/models -> repository -> pipeline checkpoints -> compensation/runner -> service -> history maintenance -> active UI -> history acceptance.
- Ownership: tasks execute sequentially because repository, worker, service, Assistant, and UI files intentionally overlap.
