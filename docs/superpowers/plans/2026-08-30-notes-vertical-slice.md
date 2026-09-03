# 知研学习笔记垂直切片实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `/notes` 从迁移占位页升级为以 SQLite 为事实源、支持 Markdown、标签、QA 来源、旧数据迁移和可恢复 Memory 投影的三档响应式学习笔记产品切片。

**Architecture:** 新建独立 Note 聚合、FTS5 索引、来源关联、迁移账本和持久化投影任务；`NoteService` 统一 React、QA 转笔记和 legacy 入口，`ApplicationServices` 统一启动投影 worker。文档/QA 删除在现有 durable fence 事务中清理 Note 来源，Memory 仅作为可重建投影。

**Tech Stack:** Python 3.11、SQLite/FTS5、FastAPI/Pydantic、React 19、TypeScript 5.9、TanStack Query 5、`react-markdown@10.1.0`、`remark-gfm@4.0.1`、`rehype-sanitize@6.0.0`、Vitest、Playwright、Penpot。

## Global Constraints

- SQLite 中的 Note 聚合是唯一产品事实源；Memory 不得成为正文读取或保存成功的前置条件。
- 正文为 1–20,000 字符 Markdown；概念最多 120 字符；标签最多 10 个且每个最多 32 字符；来源最多 10 个。
- 公开 API 不接受 `user_id`；服务端从 HttpOnly 会话解析用户，所有 mutation 要求 CSRF。
- 跨用户、不存在和已删除 Note 统一安全 `404`；更新/删除使用版本条件，陈旧请求不得覆盖新版本或复活墓碑。
- 删除文档或 QA 来源保留 Note 正文、概念和标签，但清空来源 ID、定位、标题快照与摘录，仅显示无敏感内容的“来源已删除”。
- 旧 `history.json.notes` 幂等迁移后停止双写；legacy 添加、清空、回忆和统计必须调用同一 Note 领域。
- 浏览器不把 Note 草稿、会话或任务状态写入 `localStorage`/`sessionStorage`。
- Penpot 是唯一视觉源；desktop `1440×1024`、tablet `1024×768`、mobile `390×844`，移动触控目标至少 `44×44`。
- 首版不实现文件夹、双向链接、协作、可见修订历史、离线编辑、AI 自动改写或文档框选摘录。
- 多副本部署前仍保持单应用副本、单 Uvicorn worker；本计划不假装进程内锁已经分布式化。
- 使用 `D:\python_self_agent\venv\Scripts\python.exe`；pytest `--basetemp` 必须位于仓库 `.runtime/`。

---

## Execution Workflow Gate

Before Task 1 starts, Codex must follow `docs/agent-workflow/README.md`: review this plan against the actual base commit, write `REVIEW.md`, and create ordered, self-contained numbered packets under `docs/agent-workflow/task-packets/2026-08-30-notes-vertical-slice/`. Only packets with `status: ready` may be implemented. After every implementation packet is `done`, Codex performs the mandatory combined-diff review and writes `FINAL_INTEGRATION_REVIEW.md`; implementation evidence must never be pre-filled.

---

### Task 1: 创建并验证 Penpot Notes 权威设计源

**Files:**
- Modify: `docs/product-ui/penpot-handoff.md`
- Modify: `docs/product-ui/penpot-component-map.json`
- Create: `docs/product-ui/reference/penpot/desktop-notes.png`
- Create: `docs/product-ui/reference/penpot/desktop-notes-source-deleted.png`
- Create: `docs/product-ui/reference/penpot/desktop-notes-projection-failed.png`
- Create: `docs/product-ui/reference/penpot/desktop-notes-clear.png`
- Create: `docs/product-ui/reference/penpot/desktop-notes-empty.png`
- Create: `docs/product-ui/reference/penpot/tablet-notes.png`
- Create: `docs/product-ui/reference/penpot/tablet-notes-sources.png`
- Create: `docs/product-ui/reference/penpot/tablet-notes-projection-failed.png`
- Create: `docs/product-ui/reference/penpot/tablet-notes-empty.png`
- Create: `docs/product-ui/reference/penpot/mobile-notes.png`
- Create: `docs/product-ui/reference/penpot/mobile-notes-editor.png`
- Create: `docs/product-ui/reference/penpot/mobile-notes-filters.png`
- Create: `docs/product-ui/reference/penpot/mobile-notes-sources.png`
- Create: `docs/product-ui/reference/penpot/mobile-notes-conflict.png`
- Create: `docs/product-ui/reference/penpot/mobile-notes-empty.png`
- Create: `tests/deploy/test_notes_product_contract.py`
- Modify: `tests/design/test_penpot_component_map.mjs`

**Interfaces:**
- Consumes: existing Penpot file `3be9e5e1-190f-8090-8008-6ff3f3dcd54c`, semantic tokens, linked AppShell/Button/TextField/Drawer/Dialog components, approved spec `docs/superpowers/specs/2026-08-30-notes-vertical-slice-design.md`.
- Produces: fifteen fresh-read boards and exact-size direct PNG exports; verified IDs and responsive/state semantics for Tasks 7–9.

- [ ] **Step 1: Write the failing design contract**

Create `tests/deploy/test_notes_product_contract.py` with exact filenames and dimensions:

```python
from pathlib import Path
from struct import unpack


ROOT = Path(__file__).resolve().parents[2]
REFERENCE = ROOT / "docs" / "product-ui" / "reference" / "penpot"
EXPECTED = {
    "desktop-notes.png": (1440, 1024),
    "desktop-notes-source-deleted.png": (1440, 1024),
    "desktop-notes-projection-failed.png": (1440, 1024),
    "desktop-notes-clear.png": (1440, 1024),
    "desktop-notes-empty.png": (1440, 1024),
    "tablet-notes.png": (1024, 768),
    "tablet-notes-sources.png": (1024, 768),
    "tablet-notes-projection-failed.png": (1024, 768),
    "tablet-notes-empty.png": (1024, 768),
    "mobile-notes.png": (390, 844),
    "mobile-notes-editor.png": (390, 844),
    "mobile-notes-filters.png": (390, 844),
    "mobile-notes-sources.png": (390, 844),
    "mobile-notes-conflict.png": (390, 844),
    "mobile-notes-empty.png": (390, 844),
}


def _png_size(path: Path) -> tuple[int, int]:
    payload = path.read_bytes()
    assert payload[:8] == b"\x89PNG\r\n\x1a\n"
    return unpack(">II", payload[16:24])


def test_notes_penpot_exports_have_exact_viewports():
    assert {path.name for path in REFERENCE.glob("*-notes*.png")} >= set(EXPECTED)
    for name, size in EXPECTED.items():
        assert _png_size(REFERENCE / name) == size


def test_notes_handoff_records_exact_board_names_and_sample_boundary():
    handoff = (ROOT / "docs" / "product-ui" / "penpot-handoff.md").read_text("utf-8")
    for board in (
        "Desktop / Notes / Default",
        "Desktop / Notes / Source deleted",
        "Desktop / Notes / Projection failed",
        "Desktop / Notes / Clear confirm",
        "Desktop / Notes / Empty",
        "Tablet / Notes / Default",
        "Tablet / Notes / Sources drawer",
        "Tablet / Notes / Projection failed",
        "Tablet / Notes / Empty",
        "Mobile / Notes / List",
        "Mobile / Notes / Editor",
        "Mobile / Notes / Filters drawer",
        "Mobile / Notes / Sources drawer",
        "Mobile / Notes / Version conflict",
        "Mobile / Notes / Empty",
    ):
        assert board in handoff
    assert "Notes boards use illustrative sample data only" in handoff
```

- [ ] **Step 2: Run the contract to prove RED**

Run:

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/deploy/test_notes_product_contract.py --basetemp=.runtime/pytest-notes-penpot-red
```

Expected: FAIL because the eight exports and Notes handoff records do not exist.

- [ ] **Step 3: Build the fifteen boards in the connected Penpot file**

Use the existing linked components and semantic tokens. Create exactly:

```text
Desktop / Notes / Default              1440 × 1024
Desktop / Notes / Source deleted       1440 × 1024
Desktop / Notes / Projection failed    1440 × 1024
Desktop / Notes / Clear confirm        1440 × 1024
Desktop / Notes / Empty                1440 × 1024
Tablet / Notes / Default               1024 × 768
Tablet / Notes / Sources drawer        1024 × 768
Tablet / Notes / Projection failed     1024 × 768
Tablet / Notes / Empty                 1024 × 768
Mobile / Notes / List                  390 × 844
Mobile / Notes / Editor                390 × 844
Mobile / Notes / Filters drawer        390 × 844
Mobile / Notes / Sources drawer        390 × 844
Mobile / Notes / Version conflict      390 × 844
Mobile / Notes / Empty                 390 × 844
```

Desktop uses list/editor/source columns; tablet uses list/editor plus Drawer; mobile uses list→editor and bottom sheets. Verify zero broken links, text overflow, actual-bounds overflow, and undersized mobile actions before export.

- [ ] **Step 4: Direct-export, inspect, and record fresh-read evidence**

Export the fifteen boards directly from Penpot to the exact paths above. Inspect original-size PNGs. Append a handoff table containing final Penpot revision, page/board IDs, linked component IDs, viewport, state semantics, token bindings, deliberate differences, and this exact line:

```text
Notes boards use illustrative sample data only; production renders authenticated server records and never seeds these values.
```

- [ ] **Step 5: Run GREEN design verification**

Run:

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/deploy/test_notes_product_contract.py --basetemp=.runtime/pytest-notes-penpot-green
node --test tests/design/test_penpot_handoff.mjs
node --test tests/design/test_penpot_component_map.mjs
git diff --check
```

Expected: Notes contract PASS, existing handoff tests PASS, diff check silent.

- [ ] **Step 6: Commit the design source evidence**

```powershell
git add docs/product-ui/penpot-handoff.md docs/product-ui/penpot-component-map.json docs/product-ui/reference/penpot tests/deploy/test_notes_product_contract.py tests/design/test_penpot_component_map.mjs
git commit -m "design: add learning notes source boards"
```

---

### Task 2: 实现 Note SQLite 聚合、FTS 与仓库

**Files:**
- Modify: `app/database.py`
- Create: `app/note_models.py`
- Create: `app/note_repository.py`
- Create: `tests/test_note_models.py`
- Create: `tests/test_note_repository.py`

**Interfaces:**
- Consumes: `app.database.connect()` and existing composite user ownership conventions.
- Produces: `NoteRepository` CRUD/search/source/outbox primitives and immutable models consumed by Tasks 3–5.

- [ ] **Step 1: Write failing schema and model tests**

Create tests that initialize a real temporary database and assert FTS5, foreign keys, tombstones and validation:

```python
def test_note_schema_supports_fts_and_composite_ownership(tmp_path):
    db = tmp_path / "app.db"
    initialize_database(db)
    with connect(db) as conn:
        names = {row["name"] for row in conn.execute(
            "select name from sqlite_master where type in ('table','index')"
        )}
    assert {"notes", "note_tags", "note_sources", "note_projection_tasks", "note_legacy_imports", "notes_fts"} <= names


def test_note_models_reject_invalid_body_and_too_many_tags():
    with pytest.raises(ValueError, match="body_markdown"):
        validate_note_input("", None, ())
    with pytest.raises(ValueError, match="tags"):
        validate_note_input("valid", None, tuple(f"tag-{i}" for i in range(11)))
```

- [ ] **Step 2: Run model/schema tests to prove RED**

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/test_note_models.py tests/test_note_repository.py --basetemp=.runtime/pytest-notes-repository-red
```

Expected: FAIL because Note schema/modules are absent.

- [ ] **Step 3: Add the Note tables and FTS5 initialization**

Extend `SCHEMA` with the exact tables from the spec. Add an explicit FTS check in `initialize_database()`:

```python
def _verify_fts5(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("create virtual table if not exists notes_fts using fts5(note_id UNINDEXED, user_id UNINDEXED, body_markdown, concept, tags_text)")
    except sqlite3.OperationalError as exc:
        raise RuntimeError("SQLite FTS5 support is required for learning notes") from exc
```

Use `check` constraints for `projection_state`, projection `operation/status`, positive versions, tag/source limits enforced in service, and composite foreign keys `(note_id, user_id) → notes(id, user_id)`.

- [ ] **Step 4: Implement immutable models and validators**

Create `app/note_models.py`:

```python
from dataclasses import dataclass
from typing import Literal

ProjectionState = Literal["pending", "ready", "failed"]
SourceKind = Literal["qa_message", "qa_citation"]


@dataclass(frozen=True)
class NoteSource:
    id: str
    kind: SourceKind
    deleted: bool
    qa_message_id: str | None
    citation_id: str | None
    document_id: str | None
    locator: dict[str, object] | None
    excerpt: str | None


@dataclass(frozen=True)
class Note:
    id: str
    user_id: str
    body_markdown: str
    concept: str | None
    tags: tuple[str, ...]
    sources: tuple[NoteSource, ...]
    version: int
    projection_state: ProjectionState
    created_at: str
    updated_at: str
    deleted_at: str | None


def validate_note_input(body_markdown: str, concept: str | None, tags: tuple[str, ...]) -> tuple[str, str | None, tuple[str, ...]]:
    body = body_markdown.strip()
    if not 1 <= len(body) <= 20_000:
        raise ValueError("body_markdown must contain 1 to 20000 characters")
    normalized_concept = concept.strip() if concept else None
    if normalized_concept and len(normalized_concept) > 120:
        raise ValueError("concept must contain at most 120 characters")
    normalized_tags = tuple(dict.fromkeys(tag.strip() for tag in tags if tag.strip()))
    if len(normalized_tags) > 10 or any(len(tag) > 32 for tag in normalized_tags):
        raise ValueError("tags must contain at most 10 values of 32 characters")
    return body, normalized_concept, normalized_tags
```

- [ ] **Step 5: Implement repository creation, paging, search and optimistic updates**

Create `NoteRepository` with these exact public methods:

```python
class NoteRepository:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)

    # Implement create/get/list_page/update/soft_delete/clear_all and
    # retry_failed_projections with the transaction rules below.
```

Every mutation uses `BEGIN IMMEDIATE`, updates `notes_fts`, and inserts a unique projection task before commit. Cursor encoding/decoding must validate type/shape and use `(updated_at, id)` descending without overlap.

- [ ] **Step 6: Add repository concurrency, FTS and ownership tests**

Cover exact behavior:

```python
def test_update_rejects_stale_version(repository, user):
    note = repository.create(user, "first", None, (), "request-1")
    updated = repository.update(user, note.id, expected_version=1, body_markdown="second", concept=None, tags=())
    assert updated.version == 2
    with pytest.raises(NoteVersionConflict):
        repository.update(user, note.id, expected_version=1, body_markdown="stale", concept=None, tags=())


def test_search_is_user_scoped_and_excludes_tombstones(repository, alice, bob):
    alice_note = repository.create(alice, "量子纠缠", "物理", ("论文",), "alice-1")
    repository.create(bob, "量子纠缠", "私有", (), "bob-1")
    assert [item.id for item in repository.list_page(alice, query="量子").items] == [alice_note.id]
    repository.soft_delete(alice, alice_note.id, expected_version=1)
    assert repository.list_page(alice, query="量子").items == ()
```

Also test 55 records → default newest 20, older cursor, tied timestamps, tag intersection, invalid cursor, create idempotency and conflicting request payload.

- [ ] **Step 7: Run GREEN repository verification**

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/test_note_models.py tests/test_note_repository.py --basetemp=.runtime/pytest-notes-repository-green
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/test_qa_repository.py tests/test_qa_deletion.py --basetemp=.runtime/pytest-notes-repository-regression
git diff --check
```

Expected: Note tests PASS; QA storage/deletion regressions PASS; diff check silent.

- [ ] **Step 8: Commit persistence**

```powershell
git add app/database.py app/note_models.py app/note_repository.py tests/test_note_models.py tests/test_note_repository.py
git commit -m "feat: persist learning notes"
```

---

### Task 3: 实现旧笔记迁移与可恢复 Memory 投影

**Files:**
- Create: `app/note_migration.py`
- Create: `app/note_projection.py`
- Create: `tests/test_note_migration.py`
- Create: `tests/test_note_projection.py`

**Interfaces:**
- Consumes: Task 2 `NoteRepository`, `note_projection_tasks`, `note_legacy_imports`; `UserStorage`, `UserRuntimeRegistry`, `MemoryManager.add_memory` with an explicit `memory_id` and `remove_memory()`.
- Produces: `NoteMigrationService`, `NoteProjectionRepository`, `NoteProjectionWorker` for Tasks 4–5.

- [ ] **Step 1: Write failing migration and worker tests**

```python
def test_migration_keeps_duplicate_legacy_notes_once_each(migration, history, repository, user_id):
    history.save({"documents": [], "questions": [], "sessions": [], "notes": [
        {"note": "same", "concept": "x", "session_id": "s", "created_at": "2026-01-01T00:00:00"},
        {"note": "same", "concept": "x", "session_id": "s", "created_at": "2026-01-01T00:00:00"},
    ]})
    migration.ensure_user_migrated(user_id)
    migration.ensure_user_migrated(user_id)
    assert len(repository.list_page(user_id).items) == 2


def test_claim_pass_terminalizes_expired_final_projection(repository, clock):
    task = repository.seed_running_task(attempt_count=3, lease_expires_at=clock.past)
    assert repository.claim_next("worker", now=clock.now) is None
    assert repository.get(task.id).status == "failed"
```

- [ ] **Step 2: Run RED migration/projection tests**

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/test_note_migration.py tests/test_note_projection.py --basetemp=.runtime/pytest-notes-projection-red
```

Expected: FAIL because services and worker do not exist.

- [ ] **Step 3: Implement deterministic migration**

Create:

```python
class NoteMigrationService:
    MIGRATION_VERSION = 1

    def __init__(self, db_path: Path | str, storage: UserStorage, repository: NoteRepository, runtime_registry: UserRuntimeRegistry) -> None:
        self.db_path = Path(db_path)
        self.storage = storage
        self.repository = repository
        self.runtime_registry = runtime_registry

    # Implement ensure_user_migrated(user_id) and migrate_known_users()
    # with the deterministic transaction and ledger algorithm below.
```

Canonicalize each legacy record with JSON sorted keys, group identical canonical payloads, and include occurrence index in `legacy_import_key`. Use UUIDv5 under a user-derived namespace. Under the user runtime lock, write Note/tag/ledger/projection task in one DB transaction. Do not mutate `history.json.notes`.

Scan semantic memories only for exact `knowledge_type=learning_note` plus content/concept/session matches. Store one exact legacy memory ID in the ledger; unmatched records remain untouched and produce a safe count-only warning.

- [ ] **Step 4: Implement projection repository and worker**

Create these exact boundaries:

```python
class NoteProjectionRepository:
    # Implement claim_next, heartbeat, complete, fail_or_retry and
    # recover_expired with owner-token and lease predicates.
    raise NotImplementedError


class NoteMemoryProjection(Protocol):
    def upsert(self, runtime: UserRuntime, note: Note) -> None:
        raise NotImplementedError

    def remove(self, runtime: UserRuntime, note_id: str) -> None:
        raise NotImplementedError


class NoteProjectionWorker:
    # Implement start, stop, notify and run_once using the repository contract.
    raise NotImplementedError
```

`claim_next()` must execute expired recovery and claim under one `BEGIN IMMEDIATE`. Stable Memory ID is `note:{user_id}:{note_id}`. Before upsert, reload the current Note and no-op stale versions or tombstones. After successful stable upsert, remove the exact matched legacy memory ID and mark `legacy_memory_cleaned_at`; never broad-delete by metadata.

- [ ] **Step 5: Test restart, stale owner, stable IDs and legacy cleanup ordering**

Add tests proving:

```python
def test_stable_projection_precedes_exact_legacy_cleanup(worker, fake_memory, seeded_legacy_task):
    worker.run_once()
    assert fake_memory.calls == [
        ("upsert", seeded_legacy_task.stable_memory_id),
        ("remove", seeded_legacy_task.legacy_memory_id),
    ]


def test_stale_worker_cannot_complete_reclaimed_task(repository, expired_task):
    replacement = repository.claim_next("replacement")
    assert replacement is not None
    assert repository.complete(expired_task.id, "stale") is False
```

Also cover worker start recovery, final attempt, transient retry, delete projection, upsert after edit, upsert no-op after delete, multi-user isolation and `stop()` joining threads.

- [ ] **Step 6: Run GREEN migration/projection verification**

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/test_note_migration.py tests/test_note_projection.py tests/test_user_runtime.py tests/test_assistant_user_isolation.py --basetemp=.runtime/pytest-notes-projection-green
git diff --check
```

Expected: all selected tests PASS; diff check silent.

- [ ] **Step 7: Commit migration and projection**

```powershell
git add app/note_migration.py app/note_projection.py tests/test_note_migration.py tests/test_note_projection.py
git commit -m "feat: migrate and project learning notes"
```

---

### Task 4: 实现 NoteService 与来源删除一致性

**Files:**
- Create: `app/note_service.py`
- Modify: `app/qa_deletion.py`
- Create: `tests/test_note_service.py`
- Create: `tests/test_note_source_deletion.py`

**Interfaces:**
- Consumes: Tasks 2–3 的 `NoteRepository`、`NoteMigrationService`、`NoteProjectionWorker`，现有 `SessionRegistry`、`QaRepository` 与 durable QA deletion fence。
- Produces: 所有 UI/legacy 共用的 `NoteService`；文档/QA 删除事务可调用的 `scrub_sources_in_transaction()`。

- [ ] **Step 1: Write failing service and source-resolution tests**

Create tests for a manual Note, an answer-linked Note and a citation-linked Note. The service must resolve source labels and excerpts from the authenticated user's QA rows instead of trusting the request:

```python
def test_create_from_citation_uses_server_owned_snapshot(note_service, seeded_citation, session):
    note = note_service.create(
        session,
        body_markdown="复习检索增强生成。",
        concept="RAG",
        tags=("检索",),
        client_request_id="req-1",
        source=NoteSourceSelector(
            kind="qa_citation",
            qa_message_id=seeded_citation.message_id,
            citation_id=seeded_citation.citation_id,
        ),
    )
    assert note.sources[0].title_snapshot == seeded_citation.document_name
    assert note.sources[0].excerpt_snapshot == seeded_citation.excerpt


def test_cross_user_source_is_safe_not_found(note_service, alice_session, bob_citation):
    with pytest.raises(NoteNotFoundError):
        note_service.create(
            alice_session,
            body_markdown="不能绑定别人的来源",
            concept=None,
            tags=(),
            client_request_id="req-cross-user",
            source=NoteSourceSelector(
                kind="qa_citation",
                qa_message_id=bob_citation.message_id,
                citation_id=bob_citation.citation_id,
            ),
        )
```

Also cover authenticated manual creation, request idempotency, list/search/filter/paging, get, versioned update, delete, clear, projection retry, deleted Note safety, and migration-on-first-access.

- [ ] **Step 2: Run the focused tests to prove RED**

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/test_note_service.py tests/test_note_source_deletion.py --basetemp=.runtime/pytest-notes-service-red
```

Expected: FAIL because `NoteService` and source scrubbing do not exist.

- [ ] **Step 3: Implement the single Note domain facade**

Create these public methods in `app/note_service.py`:

```python
@dataclass(frozen=True)
class NoteSourceSelector:
    kind: Literal["qa_answer", "qa_citation"]
    qa_message_id: str
    citation_id: str | None = None


class NoteService:
    def list_notes(self, session: UserSession, filters: NoteFilters) -> NotePage:
        self.migration.ensure_user_migrated(session.user_id)
        return self.repository.list_page(session.user_id, **filters.as_repository_kwargs())

    def get_note(self, session: UserSession, note_id: str) -> Note:
        self.migration.ensure_user_migrated(session.user_id)
        note = self.repository.get(session.user_id, note_id)
        if note is None:
            raise NoteNotFoundError(note_id)
        return note
```

Implement `create()`, `update()`, `delete()`, `clear_all()` and `retry_projection()` with the same migration gate and authenticated `session.user_id`. `create()` accepts at most one `NoteSourceSelector` in this slice, resolves it through `QaRepository`, builds a trusted `NewNoteSource`, writes Note + outbox atomically, and calls `projection_worker.notify()` only after commit. Manual creation passes no source.

Map duplicate `client_request_id` to the existing Note only when the original request belongs to the same user. Never accept title, excerpt, document name, user ID, projection state, version or timestamps from the client.

- [ ] **Step 4: Add source scrubbing to the existing durable deletion transaction**

Add this repository boundary and call it from `QaDeletionRepository` immediately before deleting the matching QA rows:

```python
def scrub_sources_in_transaction(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    document_id: str | None = None,
    thread_id: str | None = None,
    deleted_at: str,
) -> int:
    """Remove source identifiers and snapshots while preserving the owning Note."""
```

The update must be scoped by `user_id` plus the selected document/thread, clear `document_id`, `qa_thread_id`, `qa_message_id`, `citation_id`, `locator_json`, `title_snapshot` and `excerpt_snapshot`, set `source_deleted_at`, bump each affected Note version/update time once, and enqueue one latest-version projection per Note in the same transaction. It must not wait for Memory cleanup.

Keep the existing document/QA deletion fence as the concurrency authority. A source-based create racing deletion must either commit before the fence and be scrubbed, or observe the fence/deleted source and fail safely; add a two-connection barrier test for both orderings.

- [ ] **Step 5: Run GREEN service and deletion verification**

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/test_note_service.py tests/test_note_source_deletion.py tests/test_qa_deletion.py tests/test_document_library.py --basetemp=.runtime/pytest-notes-service-green
git diff --check
```

Expected: all selected tests PASS; diff check silent.

- [ ] **Step 6: Commit the domain service and deletion integration**

```powershell
git add app/note_service.py app/qa_deletion.py tests/test_note_service.py tests/test_note_source_deletion.py tests/test_qa_deletion.py
git commit -m "feat: add learning note domain service"
```

---

### Task 5: 暴露 Notes API 并接入应用生命周期

**Files:**
- Modify: `app/bootstrap.py`
- Modify: `app/runtime.py`
- Modify: `api/app.py`
- Modify: `api/config.py`
- Modify: `api/dependencies.py`
- Modify: `api/errors.py`
- Create: `api/schemas/notes.py`
- Create: `api/routes/notes.py`
- Modify: `tests/test_app_bootstrap.py`
- Modify: `tests/test_user_runtime.py`
- Modify: `tests/api/test_app_lifecycle.py`
- Create: `tests/api/test_note_routes.py`

**Interfaces:**
- Consumes: Task 4 `NoteService`; current cookie session, CSRF and structured-error conventions.
- Produces: `/api/v1/notes` resource API, Notes capability flag, migrated startup lifecycle and runtime injection used by Tasks 6–8.

- [ ] **Step 1: Write failing API contract tests**

Test these exact routes and response classes:

```text
GET    /api/v1/notes/capabilities
GET    /api/v1/notes?limit=&cursor=&query=&tags=&source_kind=&sort=updated_desc
POST   /api/v1/notes
GET    /api/v1/notes/{note_id}
PATCH  /api/v1/notes/{note_id}
DELETE /api/v1/notes/{note_id}
POST   /api/v1/notes/clear
POST   /api/v1/notes/projections/retry
```

Use a real `ApplicationServices` and two authenticated users. Assert:

```python
def test_update_requires_version_and_returns_conflict(client, auth, created_note):
    advance_note_version(client, auth, created_note)
    response = client.patch(
        f"/api/v1/notes/{created_note['id']}",
        headers={"X-CSRF-Token": auth.csrf_token},
        json={"body_markdown": "stale", "concept": None, "tags": [], "expected_version": 1},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "NOTE_VERSION_CONFLICT"


def test_create_ignores_untrusted_source_snapshot_fields(client, auth, citation):
    payload = {
        "body_markdown": "正文",
        "concept": "概念",
        "tags": ["标签"],
        "client_request_id": "api-request-1",
        "source": {
            "kind": "qa_citation",
            "qa_message_id": citation.message_id,
            "citation_id": citation.citation_id,
        },
    }
    assert "excerpt_snapshot" not in payload["source"]
    assert client.post("/api/v1/notes", headers={"X-CSRF-Token": auth.csrf_token}, json=payload).status_code == 201
```

Also cover unauthenticated 401, missing/bad CSRF 403, safe 404, invalid cursor 400, validation 422, idempotent create, tombstone non-resurrection, clear-all scope and retry eligibility.

- [ ] **Step 2: Prove RED for routes and lifecycle**

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/api/test_note_routes.py tests/api/test_app_lifecycle.py tests/test_app_bootstrap.py tests/test_user_runtime.py --basetemp=.runtime/pytest-notes-api-red
```

Expected: FAIL because Notes services/routes/capability are absent.

- [ ] **Step 3: Define strict request and response schemas**

Create `api/schemas/notes.py` with `extra="forbid"` request models and immutable response models. Keep source selectors separate from resolved sources:

```python
class NoteSourceSelectorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["qa_answer", "qa_citation"]
    qa_message_id: str = Field(min_length=1, max_length=128)
    citation_id: str | None = Field(default=None, min_length=1, max_length=128)


class NoteUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body_markdown: str = Field(min_length=1, max_length=20_000)
    concept: str | None = Field(default=None, max_length=120)
    tags: list[str] = Field(default_factory=list, max_length=10)
    expected_version: int = Field(ge=1)
```

Define `NoteCreateRequest`, `NoteSourceResponse`, `NoteResponse`, `NotePageResponse`, `NoteClearRequest` and `NoteProjectionRetryResponse`. `NoteClearRequest.confirmation` must equal `清空全部笔记`. Responses expose scrubbed source state and projection status, but never internal retry payload, legacy IDs, worker ownership or user ID.

- [ ] **Step 4: Implement dependencies, errors and routes**

Add `get_note_service(request)` beside existing service dependencies. Route mutations call the existing session and CSRF dependencies before invoking `NoteService`. Return `201` for first create, `200` for idempotent replay/update/clear/retry and `204` for single delete.

Register stable public error codes:

```text
NOTE_NOT_FOUND             404 non-retryable
NOTE_SOURCE_NOT_FOUND      404 non-retryable
NOTE_VERSION_CONFLICT      409 non-retryable
NOTE_SOURCE_DELETING       409 non-retryable
NOTE_IDEMPOTENCY_CONFLICT  409 non-retryable
NOTE_VALIDATION_ERROR      422 non-retryable
NOTE_PROJECTION_UNAVAILABLE 503 retryable
```

Do not leak SQLite, Memory, path, source ownership or migration details.

- [ ] **Step 5: Wire lifecycle and route capability**

Add `notes_route_enabled: bool = True` to `ApiConfig`, sourced from `NOTES_ROUTE_ENABLED`; expose it through the existing authenticated capability response. Include `notes_router` regardless of the flag and make the route dependency return the existing feature-disabled response when false, so SPA routing and API semantics agree.

Construct `NoteRepository`, `NoteMigrationService`, `NoteProjectionRepository`, `NoteProjectionWorker` and `NoteService` in `ApplicationServices.create()`. Add `note_service` to `UserRuntime` and `UserRuntimeRegistry.set_note_service()` so future and already-created runtimes receive the same facade. `PDFLearningAssistant` will consume this injected field in Task 8.

Startup order:

```text
initialize schema → migrate known users → recover expired note projections
→ start import worker → QA worker → QA deletion worker → Note projection worker
```

Stop in exact reverse order. Migration and projection recovery run even when `NOTES_ROUTE_ENABLED=false`; disabling the route must not strand durable work.

- [ ] **Step 6: Run GREEN API and lifecycle verification**

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/api/test_note_routes.py tests/api/test_app_lifecycle.py tests/test_app_bootstrap.py tests/test_user_runtime.py --basetemp=.runtime/pytest-notes-api-green
git diff --check
```

Expected: all selected tests PASS; start/stop/recovery ordering assertions PASS.

- [ ] **Step 7: Commit API and lifecycle integration**

```powershell
git add app/bootstrap.py app/runtime.py api/app.py api/config.py api/dependencies.py api/errors.py api/schemas/notes.py api/routes/notes.py tests/test_app_bootstrap.py tests/test_user_runtime.py tests/api/test_app_lifecycle.py tests/api/test_note_routes.py
git commit -m "feat: expose learning notes api"
```

---

### Task 6: 实现前端 Notes 数据层与安全 Markdown 预览

**Files:**
- Modify: `web/package.json`
- Modify: `web/package-lock.json`
- Create: `web/src/features/notes/types.ts`
- Create: `web/src/features/notes/api.ts`
- Create: `web/src/features/notes/queries.ts`
- Create: `web/src/features/notes/api.test.ts`
- Create: `web/src/components/MarkdownPreview/MarkdownPreview.tsx`
- Create: `web/src/components/MarkdownPreview/MarkdownPreview.test.tsx`
- Create: `web/src/components/MarkdownPreview/markdown-preview.css`

**Interfaces:**
- Consumes: Task 5 API, existing `apiClient`, CSRF mutation conventions and TanStack Query provider.
- Produces: typed Notes queries/mutations and one sanitized Markdown renderer consumed by Task 7.

- [ ] **Step 1: Add pinned renderer dependencies**

```powershell
Set-Location web
npm install --save-exact react-markdown@10.1.0 remark-gfm@4.0.1 rehype-sanitize@6.0.0
Set-Location ..
```

Commit both manifests later in this task. Do not add `rehype-raw`; raw HTML remains text or is discarded by the renderer pipeline.

- [ ] **Step 2: Write failing API and renderer tests**

Test DTO decoding, repeated tag query parameters, cursor propagation, CSRF mutation headers and structured errors. Test Markdown behavior with links, GFM tables/checklists, fenced code, raw HTML, `javascript:` URLs and image/event-handler payloads:

```tsx
it("sanitizes untrusted markdown", () => {
  const { container } = render(
    <MarkdownPreview markdown={'<script>alert(1)</script>\n[bad](javascript:alert(1))'} />,
  );
  expect(container.querySelector("script")).toBeNull();
  expect(container.querySelector('a[href^="javascript:"]')).toBeNull();
});
```

Run RED:

```powershell
Set-Location web
npm test -- --run src/features/notes/api.test.ts src/components/MarkdownPreview/MarkdownPreview.test.tsx
Set-Location ..
```

Expected: FAIL because Notes modules do not exist.

- [ ] **Step 3: Implement typed API and query keys**

Define discriminated source kinds/statuses and the server version as required fields:

```typescript
export type NoteProjectionStatus = "pending" | "running" | "succeeded" | "failed";
export type NoteSourceKind = "qa_answer" | "qa_citation";

export interface NoteRecord {
  id: string;
  title: string;
  bodyMarkdown: string;
  concept: string | null;
  tags: string[];
  sources: NoteSource[];
  version: number;
  projectionStatus: NoteProjectionStatus;
  createdAt: string;
  updatedAt: string;
}
```

Implement `listNotes`, `getNote`, `createNote`, `updateNote`, `deleteNote`, `clearNotes` and `retryFailedNoteProjections`. Encode the normalized tag set in the `tags` query parameter with the exact server format, preserving AND semantics. Define query keys as `notesKeys.all`, `notesKeys.lists(filters)`, and `notesKeys.detail(id)`. Use `useInfiniteQuery` for opaque cursor pages; mutations invalidate list/detail selectively and never cache draft text.

- [ ] **Step 4: Implement the safe renderer**

Render with this explicit pipeline:

```tsx
<ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeSanitize]}>
  {markdown}
</ReactMarkdown>
```

Do not pass `skipHtml={false}`, custom raw HTML components or unsafe URL transforms. Style code blocks, tables, lists, blockquotes, links and task lists using semantic tokens; preserve keyboard focus and horizontal scrolling inside code/table containers.

- [ ] **Step 5: Run GREEN frontend data verification**

```powershell
Set-Location web
npm test -- --run src/features/notes/api.test.ts src/components/MarkdownPreview/MarkdownPreview.test.tsx
npm run typecheck
npm run lint
Set-Location ..
git diff --check
```

Expected: selected Vitest suites, typecheck and lint PASS; diff check silent.

- [ ] **Step 6: Commit frontend foundations**

```powershell
git add web/package.json web/package-lock.json web/src/features/notes web/src/components/MarkdownPreview
git commit -m "feat: add notes frontend data layer"
```

---

### Task 7: 实现 `/notes` 三档响应式工作台

**Files:**
- Modify: `web/src/App.tsx`
- Modify: `web/src/layout/navigation.ts`
- Create: `web/src/pages/NotesPage.tsx`
- Create: `web/src/pages/NotesPage.test.tsx`
- Create: `web/src/components/NotesWorkspace/NotesWorkspace.tsx`
- Create: `web/src/components/NotesWorkspace/NotesWorkspace.test.tsx`
- Create: `web/src/components/NotesWorkspace/NoteList.tsx`
- Create: `web/src/components/NotesWorkspace/NoteEditor.tsx`
- Create: `web/src/components/NotesWorkspace/NoteSourcePanel.tsx`
- Create: `web/src/components/NotesWorkspace/NoteClearDialog.tsx`
- Create: `web/src/components/NotesWorkspace/notes-workspace.css`

**Interfaces:**
- Consumes: Tasks 1 and 6 design/data contracts plus authenticated route capabilities.
- Produces: desktop/tablet/mobile Notes library, editor, source surfaces and all durable/error states used by Tasks 8–9.

- [ ] **Step 1: Write failing route, interaction and responsive tests**

Cover route selection, capability-off fallback, loading/empty/error states, filter/query parameters, explicit save, unsaved-navigation guard, delete, clear, cursor pagination, source-deleted state, projection failure/retry, version conflict and responsive surfaces.

```tsx
it("keeps the draft local until explicit save", async () => {
  renderNotesPage();
  await userEvent.type(screen.getByLabelText("笔记正文"), "新的学习笔记");
  expect(createNote).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "保存笔记" }));
  expect(createNote).toHaveBeenCalledTimes(1);
});

it("preserves a stale draft and offers reload after 409", async () => {
  updateNote.mockRejectedValue(noteVersionConflict());
  renderNotesPage({ viewport: "mobile" });
  await editAndSave("本地尚未保存的内容");
  expect(screen.getByRole("dialog", { name: "笔记已在其他窗口更新" })).toBeVisible();
  expect(screen.getByDisplayValue("本地尚未保存的内容")).toBeVisible();
});
```

Run RED:

```powershell
Set-Location web
npm test -- --run src/pages/NotesPage.test.tsx src/components/NotesWorkspace/NotesWorkspace.test.tsx
Set-Location ..
```

Expected: FAIL because the route still renders `MigrationPage`.

- [ ] **Step 2: Implement route and state ownership**

Replace only the `/notes` placeholder when the authenticated capability is enabled. `NotesPage` owns URL-backed filters and selection; `NotesWorkspace` owns the in-memory draft and edit/preview mode. Never persist draft text in browser storage.

Use this state boundary:

```typescript
export interface NoteDraft {
  noteId: string | null;
  bodyMarkdown: string;
  concept: string;
  tags: string[];
  source: NoteSourceSelector | null;
  baseVersion: number | null;
  dirty: boolean;
}
```

Read optional trusted identifiers from `/notes?source_kind=&qa_message_id=&citation_id=` into a prefilled source selector, but do not create a Note or fetch source content until the user saves. Strip unknown parameters and never accept user/body/excerpt fields from the URL.

- [ ] **Step 3: Implement desktop master/detail/source layout**

At `>=1200px`, render a 320px searchable list, flexible editor, and 280px source panel inside the existing AppShell. The editor has title, concept, tags, edit/preview tabs, body, dirty indicator, last-saved time and explicit save. Disable destructive actions during mutation; keep keyboard focus visible.

Use stable semantic labels and test IDs only when roles/names cannot select the element. Render projection state as secondary status: pending/running does not block editing, failed offers retry, succeeded is quiet.

- [ ] **Step 4: Implement tablet and mobile adaptations**

At `768–1199px`, retain list + editor and move sources into the existing focus-trapped Drawer pattern. Below `768px`, show list and editor as separate navigation states, source/filter/clear confirmation as sheets/dialogs, and bottom actions with at least 44px targets. Escape closes overlays and returns focus; browser Back from editor returns to the list without losing a dirty draft until the user decides.

- [ ] **Step 5: Implement conflict, deletion and empty/error states**

On `NOTE_VERSION_CONFLICT`, keep the local draft, show the approved modal, and offer only:

```text
重新加载服务端版本
复制本地草稿
取消并继续查看本地草稿
```

Do not silently retry with a new version. A deleted source renders “来源已删除” with no old title/excerpt. A Note deleted elsewhere returns safely to the list while retaining a copyable local draft. Clear-all requires the exact confirmation dialog and invalidates every Notes list/detail query after success.

- [ ] **Step 6: Run GREEN UI verification**

```powershell
Set-Location web
npm test -- --run src/pages/NotesPage.test.tsx src/components/NotesWorkspace/NotesWorkspace.test.tsx
npm run typecheck
npm run lint
npm run build
Set-Location ..
git diff --check
```

Expected: tests, typecheck, lint and production build PASS.

- [ ] **Step 7: Commit the Notes workspace**

```powershell
git add web/src/App.tsx web/src/layout/navigation.ts web/src/pages/NotesPage.tsx web/src/pages/NotesPage.test.tsx web/src/components/NotesWorkspace
git commit -m "feat: build responsive learning notes workspace"
```

---

### Task 8: 接通 QA 转笔记与 legacy 单一事实源

**Files:**
- Modify: `web/src/components/QaWorkspace/QaWorkspace.tsx`
- Modify: `web/src/components/QaWorkspace/QaWorkspace.test.tsx`
- Modify: `web/src/pages/QaPage.test.tsx`
- Modify: `assistants/pdf_learning_assistant.py`
- Modify: `ui/gradio_app.py`
- Create: `tests/assistants/test_pdf_learning_assistant_notes.py`
- Create: `tests/ui/test_note_handlers.py`
- Create: `tests/integration/test_note_legacy_cutover.py`

**Interfaces:**
- Consumes: Task 5 runtime-injected `NoteService`, Task 7 Notes prefill query contract, stable QA message/citation IDs.
- Produces: QA answer/citation “保存为笔记”入口；legacy 添加、清空、回忆、统计和报告全部改读 Note aggregate。

- [ ] **Step 1: Write failing QA handoff tests**

Add actions only to completed assistant messages and citations. Clicking does not save immediately; it navigates to a prefilled Notes draft using identifiers only:

```tsx
it("opens a citation-backed note draft without placing excerpts in the URL", async () => {
  renderQaWorkspaceWithCompletedAnswer();
  await userEvent.click(screen.getAllByRole("button", { name: "保存引用为笔记" })[0]);
  expect(mockNavigate).toHaveBeenCalledWith(
    `/notes?source_kind=qa_citation&qa_message_id=${messageId}&citation_id=${citationId}`,
  );
  expect(mockNavigate.mock.calls[0][0]).not.toContain("excerpt");
});
```

Test answer-level `source_kind=qa_answer`, keyboard labels, capability disabled behavior, pending/failed message suppression and mobile target size class.

- [ ] **Step 2: Write failing legacy cutover tests**

With a real `ApplicationServices`, authenticate through the Gradio handler path and assert:

```python
def test_legacy_add_and_react_list_share_one_note_store(services, legacy_session):
    assistant = services.session_registry.get_assistant(legacy_session.token)
    legacy_notes_before = list(assistant._load_latest_history()["notes"])
    result = assistant.add_note("间隔复习可以降低遗忘", "学习策略")
    page = services.note_service.list_notes(legacy_session, NoteFilters())
    assert "保存成功" in result
    assert [note.body_markdown for note in page.items] == ["间隔复习可以降低遗忘"]
    assert assistant._load_latest_history()["notes"] == legacy_notes_before
```

Also prove legacy clear creates tombstones/outbox tasks, recall searches FTS Notes plus existing document/question history, stats/report counts come from NoteService, and no new semantic `learning_note` memory with a random ID is created.

- [ ] **Step 3: Run QA and legacy tests to prove RED**

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/integration/test_note_legacy_cutover.py tests/assistants/test_pdf_learning_assistant_notes.py tests/ui/test_note_handlers.py --basetemp=.runtime/pytest-notes-legacy-red
Set-Location web
npm test -- --run src/components/QaWorkspace/QaWorkspace.test.tsx src/pages/QaPage.test.tsx
Set-Location ..
```

Expected: FAIL because QA actions and legacy delegation are absent.

- [ ] **Step 4: Implement QA-to-Notes navigation**

Render answer/citation actions through the existing completed-message and citation controls. Use `useNavigate()` and the exact identifier-only query contract from Task 7. The Notes editor may suggest the answer or citation text after authenticated server resolution, but saving remains explicit and sends the stable IDs back to the server for a fresh ownership/deletion check.

- [ ] **Step 5: Cut legacy note operations over to NoteService**

In `PDFLearningAssistant`, obtain `self.note_service = getattr(runtime, "note_service", None)`. The supported application path always injects it; standalone assistant tests may use a small repository-backed adapter constructed from their isolated runtime root rather than falling back to old dual writes.

Replace:

```text
add_note        → NoteService.create(manual source, session/user context)
clear_all_notes → NoteService.clear_all()
recall          → NoteService.search() plus existing document/question recall
get_stats       → NoteService.count_active()
generate_report → NoteService.list_recent(limit=10) and count_active()
```

Add an internal user-context entry point on `NoteService` for trusted legacy callers; it still requires a known user ID and never accepts a browser-supplied ID. Do not append to or clear `history.json.notes`. Do not call `MemoryTool.execute("add", knowledge_type="learning_note")`; the projection worker owns all Note Memory changes.

Keep UI handler function names and Chinese success/error messages stable where tests or Gradio bindings depend on them. Update help text so it no longer claims only local history is cleared.

- [ ] **Step 6: Run GREEN cross-entry verification**

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/integration/test_note_legacy_cutover.py tests/assistants/test_pdf_learning_assistant_notes.py tests/ui/test_note_handlers.py tests/test_assistant_user_isolation.py tests/test_p0_data_integrity.py --basetemp=.runtime/pytest-notes-legacy-green
Set-Location web
npm test -- --run src/components/QaWorkspace/QaWorkspace.test.tsx src/pages/QaPage.test.tsx src/pages/NotesPage.test.tsx
npm run typecheck
Set-Location ..
git diff --check
```

Expected: both entry points see the same rows; no dual-write regression; selected suites PASS.

- [ ] **Step 7: Commit QA and legacy integration**

```powershell
git add web/src/components/QaWorkspace/QaWorkspace.tsx web/src/components/QaWorkspace/QaWorkspace.test.tsx web/src/pages/QaPage.test.tsx assistants/pdf_learning_assistant.py ui/gradio_app.py tests/assistants/test_pdf_learning_assistant_notes.py tests/ui/test_note_handlers.py tests/integration/test_note_legacy_cutover.py
git commit -m "feat: connect qa and legacy learning notes"
```

---

### Task 9: 完成 E2E、设计绑定、发布门禁与交接

**Files:**
- Create: `web/e2e/notes.spec.ts`
- Create: `web/e2e/notes-runtime.py`
- Modify: `web/e2e/fixtures.ts`
- Create: `web/e2e/notes.spec.ts-snapshots/notes-default-desktop.png`
- Create: `web/e2e/notes.spec.ts-snapshots/notes-default-tablet.png`
- Create: `web/e2e/notes.spec.ts-snapshots/notes-list-mobile.png`
- Create: `web/e2e/notes.spec.ts-snapshots/notes-editor-mobile.png`
- Modify: `docs/product-ui/penpot-component-map.json`
- Modify: `docs/product-ui/penpot-handoff.md`
- Modify: `docs/product-ui/README.md`
- Modify: `README.md`
- Modify: `tests/deploy/test_notes_product_contract.py`

**Interfaces:**
- Consumes: all prior tasks and the existing product/design/deployment test gates.
- Produces: authenticated browser proof, code↔Penpot traceability, release documentation and a clean branch ready for the mandatory plan-review workflow.

- [ ] **Step 1: Write failing live E2E scenarios**

Use a real FastAPI process, built React assets and per-test temporary data root. Do not mock Notes endpoints. Cover:

```text
register/login → create Markdown Note → preview → reload → edit with version
QA completed answer → save-as-note draft → explicit save → source shown
search + tag/source filter + opaque next cursor
source document deletion → Note retained → “来源已删除” → no stale excerpt
two browser contexts edit same Note → second save gets conflict and keeps draft
failed projection → retry → UI remains usable
clear-all confirmation → active list empty → tombstones remain via API invariant
desktop/tablet/mobile navigation and focus behavior
```

Add screenshot assertions for the four representative viewports and verify no horizontal page overflow or sub-44px mobile primary action.

- [ ] **Step 2: Run E2E to prove RED**

```powershell
Set-Location web
npm run build
npx playwright test e2e/notes.spec.ts --workers=1
Set-Location ..
```

Expected: at least one new Notes acceptance scenario fails before final wiring/documentation.

- [ ] **Step 3: Complete product/design traceability**

Map each implemented Notes component to final Penpot component/board IDs and tokens in `penpot-component-map.json`; refresh handoff evidence only from a connected, fresh Penpot read. Document:

```text
API and SQLite are the Note source of truth.
Memory is an asynchronous rebuildable projection.
Legacy history notes are migrated idempotently and are no longer written.
NOTES_ROUTE_ENABLED hides access but does not stop migration/recovery.
Single process/single worker remains the supported topology for this phase.
```

Do not claim visual parity from generated HTML or screenshots alone. If Penpot fresh read/export is unavailable, record the exact blocker and do not mark the design gate complete.

- [ ] **Step 4: Make E2E GREEN and inspect screenshots at original size**

Fix only mismatches within this approved slice. Compare screenshot output to Task 1 boards for hierarchy, spacing, states and responsive behavior. Record deliberate differences in `penpot-handoff.md`; do not change the approved layout silently.

```powershell
Set-Location web
npx playwright test e2e/notes.spec.ts --workers=1
Set-Location ..
```

Expected: all Notes E2E scenarios PASS and screenshots are manually inspected.

- [ ] **Step 5: Run the focused release gate**

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/test_note_models.py tests/test_note_repository.py tests/test_note_migration.py tests/test_note_projection.py tests/test_note_service.py tests/test_note_source_deletion.py tests/api/test_note_routes.py tests/integration/test_note_legacy_cutover.py tests/deploy/test_notes_product_contract.py --basetemp=.runtime/pytest-notes-focused
Set-Location web
npm test -- --run
npm run typecheck
npm run lint
npm run build
npx playwright test e2e/notes.spec.ts --workers=1
Set-Location ..
node --test tests/design/test_penpot_handoff.mjs tests/design/test_penpot_component_map.mjs
git diff --check
```

Expected: every command PASS; no unreviewed snapshot update.

- [ ] **Step 6: Run the full repository gate**

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q --basetemp=.runtime/pytest-notes-full
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pip check
Set-Location web
npm audit --audit-level=moderate
Set-Location ..
docker compose config
if (-not (Test-Path -LiteralPath deploy/.env)) { Copy-Item -LiteralPath deploy/.env.example -Destination deploy/.env }
docker compose --env-file deploy/.env up --build -d
& 'D:\python_self_agent\venv\Scripts\python.exe' deploy/smoke_test.py --env-file deploy/.env
docker compose --env-file deploy/.env down
git status --short
git diff --check
```

Expected: full Python suite, dependency checks, npm audit, Docker config/build/health/smoke and diff checks PASS. If Docker daemon is unavailable, the gate is blocked rather than waived.

- [ ] **Step 7: Commit release evidence**

```powershell
git add web/e2e/notes.spec.ts web/e2e/notes-runtime.py web/e2e/fixtures.ts web/e2e/notes.spec.ts-snapshots docs/product-ui README.md tests/deploy/test_notes_product_contract.py
git commit -m "test: verify learning notes release slice"
git status --short
```

Expected: clean worktree.

- [ ] **Step 8: Run mandatory Codex final integration review**

After every reviewed implementation packet is `done`, follow `docs/agent-workflow/README.md` and create this file only from real combined implementation evidence:

```text
docs/agent-workflow/task-packets/2026-08-30-notes-vertical-slice/FINAL_INTEGRATION_REVIEW.md
```

The review must cite actual final commit hashes, exact test counts, Penpot revision/board IDs, Docker smoke evidence and known residual risks. Do not pre-fill an acceptance verdict before evidence exists. If the result is `changes-required`, create corrective numbered packets and repeat the review after they are done.

---

## Completion Criteria

- `/notes` is a real authenticated desktop/tablet/mobile product slice, not a migration placeholder.
- SQLite/FTS5 is the only Note fact source; Memory projection may fail and recover without losing Note data.
- Manual, QA and legacy entry points converge on one versioned/tombstoned aggregate with strict user isolation.
- Document/QA deletion scrubs source metadata transactionally while preserving user-authored Note content.
- Old history Notes migrate idempotently, stop dual-writing and are cleaned from Memory only by exact ID after stable projection.
- Markdown preview is GFM-capable and sanitized; drafts stay memory-only until explicit save.
- Conflict, source-deleted, projection-failed, empty, loading and destructive confirmation states match the approved Penpot source.
- Focused/full Python, frontend, E2E, design, dependency and Docker release gates all pass.
- Mandatory plan review, implementation packet and final independent integration review are completed with real evidence.
