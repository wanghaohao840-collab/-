# 批量导入异步任务 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有多用户 Gradio 文档学习助手中实现可恢复的批量导入、后台异步执行、阶段进度展示和失败重试队列。

**Architecture:** 使用 SQLite 持久化批次与单文件任务，`ImportWorkerPool` 在单进程内最多运行 4 个任务并通过数据库部分唯一索引保证同一用户串行。任务通过 `UserRuntimeRegistry` 的后台租约调用现有 `PDFLearningAssistant` 导入边界；Gradio 只负责安全提交、轮询和重试操作，不在浏览器会话中持有任务执行状态。

**Tech Stack:** Python 3.10+、SQLite、`ThreadPoolExecutor`、Gradio 6.19.0、现有 JSON/Qdrant RAG、现有多用户 Runtime/History/Memory、pytest 8.4.1。

## Global Constraints

- Gradio 依赖固定为 `gradio==6.19.0`，使用 `gr.File(file_count="multiple")` 和 `gr.Timer.tick`。
- 所有 Python、pytest、compileall 和应用导入验证都使用仓库解释器 `.\venv\Scripts\python.exe`；pytest 临时目录由仓库 `pytest.ini` 固定在工作区内。
- 每批最多 20 个文件；单文件最多 100 MiB；批次总大小最多 500 MiB。
- 进程级最多 4 个后台任务；同一 `user_id` 同时最多 1 个 `running` 任务；不同用户可以并行。
- 自动重试包含初次执行之外最多 3 次，退避等待严格为 2、10、30 秒；永久错误不自动重试。
- 任务状态只允许 `queued`、`running`、`retry_wait`、`succeeded`、`failed`，状态转换必须由仓储在事务内校验。
- 每个任务提交时分配一次 `document_id`，所有自动、手动和重启恢复都复用它；History 以 `document_id` 幂等 upsert。
- 成功删除暂存文件；失败任务暂存文件继续保留，不自动过期或静默删除。
- 所有任务、批次、路径、RAG、History、Memory 和 UI 查询必须同时按当前不可变 `user_id` 隔离。
- 应用重启恢复遗留 `running`；用户退出登录或关闭浏览器不取消后台任务。
- 不引入 Redis、Celery、RQ、分布式锁、任务取消、优先级、自动过期或任务删除界面。
- 不提交 `.env`、API key、用户上传文件、`data/`、数据库、缓存或测试生成运行数据。

---

## 文件与接口地图

### 新建文件

- `app/import_models.py`：任务状态、阶段、限制、请求/结果 dataclass 和进度类型别名；不执行 I/O。
- `app/import_repository.py`：SQLite 批次/任务 CRUD、原子领取、状态转换、汇总和重启恢复。
- `app/import_service.py`：面向已认证会话的批量暂存、批次创建、查询和手动重试。
- `app/import_worker.py`：WorkerPool、单任务 Runner、重试分类和 Runtime 后台租约。
- `tests/test_import_models.py`：纯模型与限制测试。
- `tests/test_import_repository.py`：SQLite 仓储、状态机、并发领取和用户隔离测试。
- `tests/test_import_service.py`：批量暂存、大小限制、路径隔离和重试授权测试。
- `tests/test_import_worker.py`：Worker、进度、失败分类、重启恢复和 Runtime 租约测试。
- `tests/ui/test_import_handlers.py`：Gradio 批次提交、轮询、重试、登录和退出 UI 测试。

### 修改文件

- `app/database.py`：加入 `import_batches`、`import_tasks`、约束和索引。
- `app/storage.py`：加入用户级 `imports` 路径、批次目录和受控暂存路径方法。
- `app/runtime.py`：增加会话引用与后台任务租约计数。
- `app/session.py`：使用 RuntimeRegistry 的会话租约 API，避免退出登录关闭仍在运行的任务。
- `hello_agents/memory/rag/contracts.py`：为 `RAGActionResult` 增加结构化 `error_code` 和 `retryable`。
- `hello_agents/memory/rag/prepare.py`：增加阶段回调并按 chunk 报告嵌入进度。
- `hello_agents/memory/rag/pipeline.py`：JSON pipeline 的 `add_text`/`replace_document` 接收并转发进度回调。
- `hello_agents/memory/rag/qdrant_pipeline.py`：Qdrant pipeline 接收并转发相同回调。
- `hello_agents/tools/builtin/rag_tool.py`：解析阶段、持久化阶段、结构化失败和进度回调贯通。
- `hello_agents/memory/manager.py`：支持显式 memory ID，保证导入事件幂等。
- `hello_agents/tools/builtin/memory_tool.py`：透传显式 memory ID，并提供导入事件的幂等入口。
- `hello_agents/memory/types/episodic.py`：相同显式 ID 更新事件时清理旧 session 索引，避免重复 session 引用。
- `assistants/pdf_learning_assistant.py`：`load_document()` 支持 `import_task_id`、进度回调和 History upsert。
- `ui/gradio_app.py`：批量上传、任务面板、Timer 轮询、单项/批次失败重试、登录退出刷新。
- `requirements.txt`：固定 `gradio==6.19.0`。
- `tests/test_user_runtime.py`、`tests/test_session_registry.py`、`tests/assistants/test_pdf_learning_assistant_multi_document.py`：新增后台租约和导入幂等回归。

---

### Task 1: 数据模型、SQLite Schema 与受控暂存路径

**Files:**

- Create: `app/import_models.py`
- Create: `app/import_repository.py`
- Create: `tests/test_import_models.py`
- Create: `tests/test_import_repository.py`
- Modify: `app/database.py`
- Modify: `app/storage.py`

**Interfaces:**

- Consumes: `app.database.connect()`, `app.database.transaction()`, `app.storage.UserStorage`, 现有 `users(id)`。
- Produces:
  - `ImportStatus = Literal["queued", "running", "retry_wait", "succeeded", "failed"]`
  - `ImportStage = Literal["queued", "staged", "parsing", "chunking", "embedding", "persisting", "committing", "succeeded", "failed"]`
  - `ImportLimits(max_files=20, max_file_bytes=100*1024*1024, max_batch_bytes=500*1024*1024)`
  - `ImportTaskCreate`
  - `ImportTaskRecord`
  - `ImportBatchSummary`
  - `ImportTaskRepository.create_batch()`, `list_batches()`, `get_batch()`, `claim_next()`, `update_progress()`, `mark_succeeded()`, `mark_retry_wait()`, `mark_failed()`, `retry_task()`, `retry_failed_in_batch()`, `recover_running()`, `has_active_tasks()`
  - `ImportTaskRepository.get_task()`
  - `UserStorage.import_batch_dir()` 和 `UserStorage.staged_import_path()`

- [ ] **Step 1: 写模型与限制失败测试**

```python
# tests/test_import_models.py
from app.import_models import ImportLimits, validate_batch_sizes


def test_validate_batch_sizes_accepts_20_files_and_500_mib():
    limits = ImportLimits()
    validate_batch_sizes([100 * 1024 * 1024] * 5, limits)


def test_validate_batch_sizes_rejects_21_files():
    limits = ImportLimits()
    sizes = [1] * 21
    try:
        validate_batch_sizes(sizes, limits)
    except ValueError as exc:
        assert "20" in str(exc)
    else:
        raise AssertionError("expected file-count validation error")
```

- [ ] **Step 2: 运行模型测试确认失败**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_import_models.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'app.import_models'`.

- [ ] **Step 3: 实现纯模型和精确限制**

```python
# app/import_models.py
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Literal, Sequence

ImportStatus = Literal["queued", "running", "retry_wait", "succeeded", "failed"]
ImportStage = Literal[
    "queued", "staged", "parsing", "chunking", "embedding",
    "persisting", "committing", "succeeded", "failed",
]
ProgressCallback = Callable[[str, int, int, str], None]


@dataclass(frozen=True)
class ImportLimits:
    max_files: int = 20
    max_file_bytes: int = 100 * 1024 * 1024
    max_batch_bytes: int = 500 * 1024 * 1024


@dataclass(frozen=True)
class ImportTaskCreate:
    task_id: str
    batch_id: str
    user_id: str
    document_id: str
    original_name: str
    file_suffix: str
    size_bytes: int
    staged_relative_path: str


def validate_batch_sizes(sizes: Sequence[int], limits: ImportLimits) -> None:
    if not sizes:
        raise ValueError("at least one file is required")
    if len(sizes) > limits.max_files:
        raise ValueError(f"batch cannot contain more than {limits.max_files} files")
    if any(size < 0 or size > limits.max_file_bytes for size in sizes):
        raise ValueError(f"each file must be at most {limits.max_file_bytes} bytes")
    if sum(sizes) > limits.max_batch_bytes:
        raise ValueError(f"batch must be at most {limits.max_batch_bytes} bytes")
```

- [ ] **Step 4: 写仓储状态机失败测试**

```python
# tests/test_import_repository.py
from app.database import initialize_database
from app.import_models import ImportTaskCreate
from app.import_repository import ImportTaskRepository, InvalidImportTransition


def make_repo(tmp_path):
    db_path = tmp_path / "app.db"
    initialize_database(db_path)
    return ImportTaskRepository(db_path)


def test_create_claim_and_complete_task(tmp_path):
    repo = make_repo(tmp_path)
    task = ImportTaskCreate(
        task_id="task-1", batch_id="batch-1", user_id="user-a",
        document_id="doc-1", original_name="a.md", file_suffix=".md",
        size_bytes=3, staged_relative_path="imports/batch-1/task-1.md",
    )
    repo.create_batch("user-a", [task], now="2026-07-30T00:00:00Z")
    claimed = repo.claim_next(blocked_user_ids=set(), now="2026-07-30T00:00:00Z")
    assert claimed.task_id == "task-1"
    repo.mark_succeeded("user-a", "task-1", now="2026-07-30T00:01:00Z")
    assert repo.get_batch("user-a", "batch-1").succeeded == 1


def test_invalid_succeeded_to_queued_transition_is_rejected(tmp_path):
    repo = make_repo(tmp_path)
    task = ImportTaskCreate(
        task_id="task-1", batch_id="batch-1", user_id="user-a",
        document_id="doc-1", original_name="a.md", file_suffix=".md",
        size_bytes=3, staged_relative_path="imports/batch-1/task-1.md",
    )
    repo.create_batch("user-a", [task])
    repo.claim_next(blocked_user_ids=set())
    repo.mark_succeeded("user-a", "task-1")
    try:
        repo.retry_task("user-a", "task-1")
    except InvalidImportTransition:
        pass
    else:
        raise AssertionError("succeeded task must not be retried")
```

- [ ] **Step 5: 运行仓储测试确认失败**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_import_repository.py -q`

Expected: FAIL because the schema and repository methods do not exist.

- [ ] **Step 6: 增加 Schema、Storage 路径和事务仓储**

在 `app/database.py` 的 `SCHEMA` 中加入以下表和索引，使用 `check`、复合外键和部分唯一索引：

```sql
create table if not exists import_batches (
    id text primary key,
    user_id text not null references users(id) on delete cascade,
    created_at text not null,
    updated_at text not null,
    unique(id, user_id)
);

create table if not exists import_tasks (
    id text primary key,
    batch_id text not null,
    user_id text not null,
    document_id text not null,
    original_name text not null,
    file_suffix text not null,
    size_bytes integer not null,
    staged_relative_path text not null,
    status text not null check(status in ('queued','running','retry_wait','succeeded','failed')),
    stage text not null,
    progress integer not null check(progress between 0 and 100),
    total_attempt_count integer not null default 0,
    auto_retry_count integer not null default 0,
    manual_retry_count integer not null default 0,
    max_auto_retries integer not null default 3,
    next_attempt_at text,
    error_code text,
    error_summary text,
    created_at text not null,
    started_at text,
    finished_at text,
    updated_at text not null,
    foreign key(batch_id, user_id) references import_batches(id, user_id)
        on delete cascade,
    unique(user_id, document_id)
);

create unique index if not exists uq_import_tasks_running_user
on import_tasks(user_id) where status = 'running';
create index if not exists ix_import_tasks_scheduler
on import_tasks(status, next_attempt_at, created_at);
create index if not exists ix_import_tasks_user_created
on import_tasks(user_id, created_at);
```

`UserPaths` 增加 `imports: Path`；`ensure_user_dirs()` 创建 `imports`；`import_batch_dir(user_id, batch_id)` 和 `staged_import_path(user_id, batch_id, task_id, suffix)` 只接受 UUID 字符串和白名单扩展名，并返回 `assert_within_user()` 的绝对路径。

`ImportTaskRepository` 的每个公开方法都使用 `transaction()` 或 `connect()`，并将 `user_id` 放入查询条件。`claim_next()` 在事务内用 `BEGIN IMMEDIATE`，筛选 `status='queued'` 且 `next_attempt_at` 为空或不晚于当前 UTC 时间、排除 `blocked_user_ids`，更新为 `running` 并递增 `total_attempt_count`；部分唯一索引冲突时回滚并返回下一个任务。`get_batch()` 返回任务列表和由 SQL 聚合计算的 `ImportBatchSummary`。

- [ ] **Step 7: 运行模型与仓储测试确认通过**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_import_models.py tests/test_import_repository.py -q`

Expected: PASS；同时运行 `.\venv\Scripts\python.exe -m pytest tests/test_auth_service.py tests/test_user_storage.py -q`，确认既有 schema 与路径测试继续 PASS。

- [ ] **Step 8: 提交**

```powershell
git add app/import_models.py app/import_repository.py app/database.py app/storage.py tests/test_import_models.py tests/test_import_repository.py
git commit -m "feat: add durable import task repository"
```

---

### Task 2: Runtime 会话与后台任务租约

**Files:**

- Modify: `app/runtime.py`
- Modify: `app/session.py`
- Create: `tests/test_runtime_import_leases.py`
- Modify: `tests/test_user_runtime.py`
- Modify: `tests/test_session_registry.py`

**Interfaces:**

- Consumes: `UserRuntimeRegistry.get_or_create()`, `SessionRegistry` 的登录、退出和过期清理。
- Produces:
  - `UserRuntimeRegistry.acquire_session(user_id) -> UserRuntime`
  - `UserRuntimeRegistry.release_session(user_id) -> None`
  - `UserRuntimeRegistry.acquire_background(user_id) -> UserRuntime`
  - `UserRuntimeRegistry.release_background(user_id) -> None`
  - `UserRuntimeRegistry.has_runtime(user_id) -> bool`
  - `UserRuntime.active_session_count` 和 `UserRuntime.active_background_count`

- [ ] **Step 1: 写租约失败测试**

```python
# tests/test_runtime_import_leases.py
from app.database import initialize_database
from app.runtime import UserRuntimeRegistry
from app.session import SessionRegistry
from app.storage import UserStorage


def make_registry(tmp_path):
    db_path = tmp_path / "app.db"
    initialize_database(db_path)
    return UserRuntimeRegistry(db_path, UserStorage(tmp_path / "data"))


def test_runtime_stays_open_until_background_lease_is_released(tmp_path):
    registry = make_registry(tmp_path)
    runtime = registry.acquire_session("user-a")
    registry.acquire_background("user-a")
    registry.release_session("user-a")
    assert registry.get_or_create("user-a") is runtime
    registry.release_background("user-a")
    assert registry.has_runtime("user-a") is False


def test_logout_does_not_close_runtime_used_by_import_worker(tmp_path):
    db_path = tmp_path / "app.db"
    initialize_database(db_path)
    sessions = SessionRegistry(db_path, UserStorage(tmp_path / "data"))
    token = sessions.register("UserA", "correct horse battery")
    user_id = sessions.get_session(token).user_id
    runtime = sessions.runtime_registry.get_or_create(user_id)
    sessions.runtime_registry.acquire_background(user_id)
    sessions.logout(token)
    assert sessions.runtime_registry.get_or_create(user_id) is runtime
    sessions.runtime_registry.release_background(user_id)
    assert sessions.runtime_registry.has_runtime(user_id) is False
```

- [ ] **Step 2: 运行测试确认当前行为失败**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_runtime_import_leases.py -q`

Expected: FAIL because `acquire_background`, `release_background` and `has_runtime` do not exist.

- [ ] **Step 3: 实现引用计数和关闭条件**

在 `UserRuntime` 中增加两个整数计数，`UserRuntimeRegistry` 在 `get_or_create()` 初始化为 0。实现统一的 `_release_if_unused_locked(user_id)`：

```python
def _release_if_unused_locked(self, user_id: str) -> None:
    runtime = self._runtimes.get(user_id)
    if runtime is None:
        return
    if runtime.active_session_count or runtime.active_background_count:
        return
    self._runtimes.pop(user_id, None)
    runtime.close()
```

`acquire_session()` 和 `acquire_background()` 都在 Registry 的 `RLock` 内取得或创建 Runtime 并递增相应计数；释放方法递减但不允许低于 0，然后调用上述关闭检查。保留 `get_or_create()` 作为只读兼容入口，但新会话和 Worker 必须使用租约 API。

- [ ] **Step 4: 修改 SessionRegistry 使用会话租约**

`_create_session()` 改用 `runtime_registry.acquire_session(user_id)`；`logout()` 和 `_cleanup_expired_locked()` 在销毁 Assistant 后调用 `release_session(user_id)`。删除现有根据 `_sessions` 重新计算数量后调用 `release_if_unused()` 的关闭逻辑，避免绕过后台计数。保留一个兼容 `release_if_unused()` 包装器只供旧测试调用，并让它调用统一关闭检查。

- [ ] **Step 5: 运行回归测试**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_runtime_import_leases.py tests/test_user_runtime.py tests/test_session_registry.py -q`

Expected: PASS；退出登录后有后台租约时 Runtime 仍存在，无会话且无后台租约时才关闭。

- [ ] **Step 6: 提交**

```powershell
git add app/runtime.py app/session.py tests/test_runtime_import_leases.py tests/test_user_runtime.py tests/test_session_registry.py
git commit -m "feat: keep user runtimes alive for background imports"
```

---

### Task 3: RAG 结构化错误与阶段进度回调

**Files:**

- Modify: `hello_agents/memory/rag/contracts.py`
- Modify: `hello_agents/memory/rag/prepare.py`
- Modify: `hello_agents/memory/rag/pipeline.py`
- Modify: `hello_agents/memory/rag/qdrant_pipeline.py`
- Modify: `hello_agents/tools/builtin/rag_tool.py`
- Create: `tests/memory/rag/test_import_progress.py`
- Modify: `tests/memory/rag/test_backend_selection_and_contracts.py`

**Interfaces:**

- Consumes: `DocumentSegment`, `prepare_document_chunks()`, JSON/Qdrant `replace_document()`、现有 `RAGBackendError` 子类。
- Produces:
  - `ProgressCallback` from `app.import_models`
  - `RAGActionResult.error_code: str`
  - `RAGActionResult.retryable: bool`
  - `RAGTool.execute_result(...).data["error_code"]`
  - `RAGTool._add_document(..., progress_callback=None)`
  - `SimpleRAGPipeline.replace_document(..., progress_callback=None)`
  - `QdrantRAGPipeline.replace_document(..., progress_callback=None)`

- [ ] **Step 1: 写进度和结构化错误失败测试**

```python
# tests/memory/rag/test_import_progress.py
from hello_agents.memory.rag.contracts import DocumentSegment, RAGActionResult
from hello_agents.memory.rag.prepare import prepare_document_chunks


def test_prepare_reports_monotonic_embedding_progress():
    updates = []
    chunks = prepare_document_chunks(
        document_id="doc-1",
        segments=[DocumentSegment("alpha beta", {"page_number": 1})],
        rag_namespace="user-a",
        split_text=lambda text: ["alpha", "beta"],
        embed_text=lambda text: [1.0, 0.0],
        progress_callback=lambda stage, done, total, message:
            updates.append((stage, done, total, message)),
    )
    assert len(chunks) == 2
    assert updates == [
        ("embedding", 1, 2, "embedding"),
        ("embedding", 2, 2, "embedding"),
    ]


def test_structured_authentication_error_is_not_retryable():
    result = RAGActionResult(
        action="add_document",
        success=False,
        message="authentication failed",
        data={},
        error="authentication failed",
        error_code="rag_authentication",
        retryable=False,
    )
    assert result.success is False
    assert result.error_code == "rag_authentication"
    assert result.retryable is False
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.\venv\Scripts\python.exe -m pytest tests/memory/rag/test_import_progress.py -q`

Expected: FAIL because `prepare_document_chunks()` has no `progress_callback` and `RAGActionResult` has no structured error fields.

- [ ] **Step 3: 扩展 contracts 和准备层**

在 `RAGActionResult` 增加带默认值的字段，保持既有 positional 构造兼容：

```python
@dataclass(frozen=True)
class RAGActionResult:
    action: str
    success: bool
    message: str
    data: dict[str, Any]
    error: str = ""
    error_code: str = ""
    retryable: bool = False
```

修改 `prepare_document_chunks()` 签名：

```python
def prepare_document_chunks(
    document_id: str,
    segments: Sequence[DocumentSegment],
    rag_namespace: str,
    split_text: Callable[[str], list[str]],
    embed_text: Callable[[str], list[float]],
    id_for_chunk: Callable[[str, str, int], str] | None = None,
    progress_callback: ProgressCallback | None = None,
) -> list[PreparedChunk]:
```

先完成所有 segment 的切块并保留 `(segment_metadata, chunk_text)` 列表，得到确定的 `total_chunks`；随后逐个调用 `embed_text`，在每个 chunk 完成后调用 `progress_callback("embedding", index, total_chunks, "embedding")`。当没有 chunk 时不调用进度回调。

- [ ] **Step 4: 贯通两个 Pipeline 的阶段**

`replace_document()` 和 `add_text()` 接收 `progress_callback`，在调用准备层前后分别报告 `chunking` 和 `persisting`。JSON Pipeline 在每次 vector point 写入/缓存保存后报告批次数；Qdrant Pipeline 在每个 upsert 批完成后报告批次。回调异常必须被吞掉并记录 warning，不能让 UI 回调破坏 RAG 导入。

- [ ] **Step 5: 在 RAGTool 解析分支传递回调并映射错误**

`_add_document()` 从 `kwargs.pop("progress_callback", None)` 取回调；PDF 页解析时报告 `parsing`，DOCX 段落和 TXT/MD 读取后报告解析完成；调用 pipeline 时传递回调。`execute_result()` 将已知异常映射为：

```python
RAGConnectionError -> ("rag_connection", True)
RAGAuthenticationError -> ("rag_authentication", False)
RAGConfigError -> ("rag_config", False)
RAGCollectionError -> ("rag_collection", False)
RAGDocumentTooLargeError -> ("rag_document_too_large", False)
RAGEmbeddingError -> ("rag_embedding", False)
RAGOperationError -> ("rag_operation", error_is_transient)
ValueError/FileNotFoundError -> ("document_invalid", False)
未知异常 -> ("unexpected_error", False)
```

错误摘要使用 `sanitize_error_message()`，最长 500 个字符；不要把 `file_path`、凭据 URL 或完整堆栈放进 `RAGActionResult.data`。

- [ ] **Step 6: 运行 RAG 回归测试**

Run: `.\venv\Scripts\python.exe -m pytest tests/memory/rag tests/tools/test_rag_tool_backend_contract.py tests/memory/storage/test_vector_store_contract.py -q`

Expected: PASS；新增进度测试 PASS，既有 JSON/Qdrant 结果契约不变。

- [ ] **Step 7: 提交**

```powershell
git add hello_agents/memory/rag/contracts.py hello_agents/memory/rag/prepare.py hello_agents/memory/rag/pipeline.py hello_agents/memory/rag/qdrant_pipeline.py hello_agents/tools/builtin/rag_tool.py tests/memory/rag/test_import_progress.py tests/memory/rag/test_backend_selection_and_contracts.py
git commit -m "feat: expose structured import progress and errors"
```

---

### Task 4: Assistant 导入幂等、History upsert 与导入 Memory 事件

**Files:**

- Modify: `assistants/pdf_learning_assistant.py`
- Modify: `app/history.py`
- Modify: `hello_agents/memory/manager.py`
- Modify: `hello_agents/tools/builtin/memory_tool.py`
- Modify: `hello_agents/memory/types/episodic.py`
- Create: `tests/assistants/test_import_idempotency.py`
- Modify: `tests/assistants/test_pdf_learning_assistant_multi_document.py`

**Interfaces:**

- Consumes: Task 3 的 `RAGActionResult.error_code/retryable` 和 `ProgressCallback`。
- Produces:
  - `HistoryRepository.upsert_document(item) -> dict[str, Any]`
  - `PDFLearningAssistant.load_document(pdf_path, document_id=None, original_name=None, import_task_id=None, progress_callback=None) -> str`
  - `MemoryManager.add_memory(..., memory_id=None) -> str`
  - `MemoryTool.ensure_import_event(import_task_id, content, metadata, session_id) -> str`

- [ ] **Step 1: 写 History 和 Memory 幂等失败测试**

```python
# tests/assistants/test_import_idempotency.py
from app.history import HistoryRepository
from hello_agents.memory.base import MemoryConfig
from hello_agents.memory.manager import MemoryManager


def test_history_upsert_does_not_duplicate_document(tmp_path):
    repo = HistoryRepository(tmp_path / "history.json")
    item = {"document_id": "doc-1", "import_task_id": "task-1", "document_name": "a.md"}
    repo.upsert_document(item)
    repo.upsert_document({**item, "document_name": "a-renamed.md"})
    assert repo.load()["documents"] == [
        {"document_id": "doc-1", "import_task_id": "task-1", "document_name": "a-renamed.md"}
    ]


def test_retry_reuses_one_import_memory_event(tmp_path):
    manager = MemoryManager(
        config=MemoryConfig(database_path=str(tmp_path / "memory.db")),
        user_id="user-a",
        enable_working=False,
        enable_episodic=True,
        enable_semantic=False,
    )
    first = manager.add_memory(
        content="用户导入了文档：a.md",
        memory_type="episodic",
        metadata={"user_id": "user-a", "import_task_id": "task-1"},
        memory_id="import-task-1",
    )
    second = manager.add_memory(
        content="用户导入了文档：a.md",
        memory_type="episodic",
        metadata={"user_id": "user-a", "import_task_id": "task-1"},
        memory_id="import-task-1",
    )
    assert first == second == "import-task-1"
    assert len(manager.memory_types["episodic"]._episodes) == 1
    manager.close()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.\venv\Scripts\python.exe -m pytest tests/assistants/test_import_idempotency.py -q`

Expected: FAIL because `upsert_document`, `import_task_id` and `ensure_import_event` do not exist.

- [ ] **Step 3: 实现 History upsert**

`HistoryRepository.upsert_document(item)` 在一次 `update()` 中按 `document_id` 查找记录：找到则原位置替换，找不到则 append；只处理 `documents`，不删除 questions、notes 或 sessions。保留现有 `add_document()` 行为供旧调用使用。

```python
def upsert_document(self, item: dict[str, Any]) -> dict[str, Any]:
    document_id = str(item["document_id"])

    def mutate(data):
        for index, current in enumerate(data["documents"]):
            if str(current.get("document_id", "")) == document_id:
                data["documents"][index] = dict(item)
                return
        data["documents"].append(dict(item))

    return self.update(mutate)
```

- [ ] **Step 4: 实现确定性导入 Memory 事件**

`MemoryManager.add_memory()` 增加可选 `memory_id`，把它传入 `MemoryItem(id=memory_id)`；默认仍使用 dataclass 的随机 ID。`MemoryTool.ensure_import_event()` 在已有 `coordination_lock` 下执行：先检查 episodic memory 的 episode context 中是否存在相同 `import_task_id`，存在则返回“已存在”，否则用 `memory_id = "import-" + uuid5(PROJECT_POINT_NAMESPACE_UUID, user_id + ":" + import_task_id)` 调用 `MemoryManager.add_memory()`。`EpisodicMemory.add()` 对相同 episode ID 先从旧 session 列表移除再覆盖 `_episodes`，避免同一事件产生重复 session 引用。

- [ ] **Step 5: 扩展 Assistant 导入签名和崩溃恢复**

`load_document()` 接收 `import_task_id` 和 `progress_callback`，把二者写入 RAG metadata、History item 和 episodic Memory metadata；在 RAG Tool 调用中传递回调。锁内使用 `upsert_document()` 替换当前 append。History 与 RAG 都已存在且任务 ID 相同时，跳过重复 RAG 写入，仅调用 `ensure_import_event()`；只有一侧存在时执行同一 `document_id` 的覆盖导入。RAG 或 History 失败时不写历史/Memory，保留现有补偿逻辑。Memory 失败作为结构化异常返回给 Runner，不能静默将任务标记成功。

- [ ] **Step 6: 运行 Assistant 和 Memory 回归**

Run: `.\venv\Scripts\python.exe -m pytest tests/assistants tests/test_memory_repository.py tests/memory/test_episodic_vector_cleanup.py -q`

Expected: PASS；原有 `current_document_id`、统计和失败不更新状态测试继续通过。

- [ ] **Step 7: 提交**

```powershell
git add assistants/pdf_learning_assistant.py app/history.py hello_agents/memory/manager.py hello_agents/tools/builtin/memory_tool.py hello_agents/memory/types/episodic.py tests/assistants/test_import_idempotency.py tests/assistants/test_pdf_learning_assistant_multi_document.py
git commit -m "feat: make document imports idempotent"
```

---

### Task 5: ImportTaskService、Runner 与 WorkerPool

**Files:**

- Create: `app/import_service.py`
- Create: `app/import_worker.py`
- Create: `tests/test_import_service.py`
- Create: `tests/test_import_worker.py`
- Modify: `app/import_models.py`
- Modify: `app/runtime.py`

**Interfaces:**

- Consumes: Tasks 1–4 的模型、Repository、Storage、Runtime lease、Assistant 导入签名。
- Produces:
  - `ImportTaskService(session_registry, repository, storage, worker_pool, limits=ImportLimits())`
  - `ImportTaskService.submit_batch(session_token, files, progress=None) -> ImportBatchSummary`
  - `ImportTaskService.list_batches(session_token, limit=50) -> list[ImportBatchSummary]`
  - `ImportTaskService.get_batch(session_token, batch_id) -> ImportBatchSummary`
  - `ImportTaskService.retry_task(session_token, task_id) -> ImportBatchSummary`
  - `ImportTaskService.retry_failed_in_batch(session_token, batch_id) -> ImportBatchSummary`
  - `ImportTaskService.has_active_tasks(user_id) -> bool`
  - `ImportTasksActiveError`
  - `ImportWorkerPool.start()`, `.stop(wait=True)`, `.notify()`
  - `ImportWorkerPool.runner`
  - `ImportTaskRunner.run(task: ImportTaskRecord) -> None`
  - `classify_import_failure(exc) -> tuple[str, bool, str]`

- [ ] **Step 1: 写 Service 暂存与 Worker 重试失败测试**

```python
# tests/test_import_service.py
def test_submit_batch_stages_all_files_before_creating_tasks(tmp_path):
    service, repo, storage = make_import_service(tmp_path)
    result = service.submit_batch("valid-token", [FakeFile("a.md", b"alpha"), FakeFile("b.md", b"beta")])
    assert result.total == 2
    assert repo.get_batch("user-a", result.batch_id).queued == 2
    assert len(list((storage.user_paths("user-a").imports / result.batch_id).iterdir())) == 2


def test_submit_batch_cleans_partial_stage_on_copy_failure(tmp_path):
    service, repo, storage = make_import_service(tmp_path, failing_copy_name="b.md")
    with pytest.raises(ValueError, match="could not stage"):
        service.submit_batch("valid-token", [FakeFile("a.md", b"a"), FakeFile("b.md", b"b")])
    assert repo.list_batches("user-a") == []


# tests/test_import_worker.py
def test_transient_failure_schedules_2_second_retry(worker, fake_clock):
    worker.run_one()
    task = worker.repository.get_task("user-a", "task-1")
    assert task.status == "retry_wait"
    assert task.next_attempt_at == "2026-07-30T00:00:02Z"
    assert task.auto_retry_count == 1


def test_three_auto_retries_then_failed(worker, fake_clock):
    worker.run_until_idle()
    task = worker.repository.get_task("user-a", "task-1")
    assert task.status == "failed"
    assert task.auto_retry_count == 3
    assert task.total_attempt_count == 4
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_import_service.py tests/test_import_worker.py -q`

Expected: FAIL because the Service and WorkerPool modules do not exist.

- [ ] **Step 3: 实现认证 Service 和原子批次暂存**

`submit_batch()` 先解析 `session_registry.get_session(session_token)` 得到 `user_id`；读取每个 `file.name`，校验 `Path(file.name).suffix` 和大小，不接受前端路径作为目标路径。先完成全部复制到 `UserStorage.staged_import_path()`，复制过程通过 `gr.Progress` 报告文件序号；任何复制异常删除当前批次目录并抛出安全的 `ValueError`。全部成功后构造 `ImportTaskCreate` 列表，在 Repository 一个事务中创建批次，最后调用 `worker_pool.notify()`。

`list_batches()`、`get_batch()`、`retry_task()` 和 `retry_failed_in_batch()` 都首先调用 `get_session()`；Repository 查询再按解析出的 `user_id` 过滤。手动重试只接受 `failed`，不接受 `succeeded`；成功后调用 `notify()`。

- [ ] **Step 4: 实现失败分类和单任务 Runner**

```python
def classify_import_failure(exc: BaseException) -> tuple[str, bool, str]:
    if isinstance(exc, (RAGConnectionError, TimeoutError, ConnectionError)):
        return "rag_connection", True, sanitize_error_message(exc)[:500]
    if isinstance(exc, RAGAuthenticationError):
        return "rag_authentication", False, "RAG authentication failed"
    if isinstance(exc, RAGConfigError):
        return "rag_config", False, "RAG configuration is invalid"
    if isinstance(exc, (RAGCollectionError, RAGDocumentTooLargeError, RAGEmbeddingError)):
        return exc.__class__.__name__.lower(), False, exc.__class__.__name__
    if isinstance(exc, (FileNotFoundError, ValueError)):
        return "document_invalid", False, sanitize_error_message(exc)[:500]
    return "unexpected_error", False, exc.__class__.__name__
```

`ImportTaskRunner.run()` 取得 `runtime_registry.acquire_background(task.user_id)`，创建 `PDFLearningAssistant(user_id=task.user_id, runtime_dir=runtime.paths.root, runtime=runtime)`，调用：

```python
assistant.load_document(
    str(formal_path),
    document_id=task.document_id,
    original_name=task.original_name,
    import_task_id=task.task_id,
    progress_callback=self._progress_callback(task),
)
```

成功后 `mark_succeeded()`、删除显式暂存文件；异常时调用 `classify_import_failure()`，瞬时且 `auto_retry_count < 3` 写入 `retry_wait`（等待 2、10 或 30 秒），否则 `mark_failed()`。每个异常路径都删除正式文件但不删除暂存文件，并在 `finally` 释放后台租约和关闭仅由 Runner 创建的 Assistant。

- [ ] **Step 5: 实现 WorkerPool 的领取、并发和重启恢复**

`start()` 首先调用 `repository.recover_running(storage)`，然后创建 4 个非 daemon worker 线程和一个调度循环。调度循环维护 `blocked_user_ids`，每次最多领取 `4 - active_count` 个任务；领取后立即加入 blocked 集合，任务结束后移除并 `notify()`。`claim_next()` 返回 `None` 时等待条件变量最多 1 秒。`stop(wait=True)` 设置停止事件，不再领取新任务，等待已领取任务完成。

启动恢复规则：`running` 任务的暂存文件存在则更新为 `queued` 和 `error_code="process_interrupted"`；暂存文件不存在则更新为 `failed` 和 `error_code="staged_file_missing"`。恢复不增加 `total_attempt_count`。

- [ ] **Step 6: 运行 Service/Worker/Runtime 联合测试**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_import_service.py tests/test_import_worker.py tests/test_runtime_import_leases.py -q`

Expected: PASS；另运行 `pytest tests/test_user_mutation_coordination.py tests/test_assistant_user_isolation.py -q`，确认用户级锁和跨用户隔离不回归。

- [ ] **Step 7: 提交**

```powershell
git add app/import_service.py app/import_worker.py app/import_models.py app/runtime.py tests/test_import_service.py tests/test_import_worker.py
git commit -m "feat: run durable imports in background workers"
```

---

### Task 6: Gradio 批量导入面板、轮询和失败重试

**Files:**

- Modify: `ui/gradio_app.py`
- Create: `tests/ui/test_import_handlers.py`
- Modify: `tests/ui/test_authenticated_handlers.py`
- Modify: `requirements.txt`

**Interfaces:**

- Consumes: `ImportTaskService.submit_batch/list_batches/get_batch/retry_task/retry_failed_in_batch`、Gradio 6.19.0 `File`、`Timer`、`Progress`。
- Produces:
  - `submit_import_batch(session_token, files, progress=gr.Progress())`
  - `refresh_import_batches(session_token)`
  - `refresh_import_batch(session_token, batch_id)`
  - `retry_import_task(session_token, task_id)`
  - `retry_import_batch_failures(session_token, batch_id)`
  - `format_batch_summary(summary) -> str`
  - `format_task_table(summary) -> list[list[object]]`

- [ ] **Step 1: 写 UI handler 授权和格式化失败测试**

```python
# tests/ui/test_import_handlers.py
def test_submit_import_batch_rejects_missing_token(monkeypatch):
    with pytest.raises(gr.Error, match="log in"):
        submit_import_batch("", [FakeFile("a.md", b"a")])


def test_task_table_contains_no_user_id_or_absolute_stage_path():
    summary = fake_summary_with_error_path()
    table = format_task_table(summary)
    rendered = repr(table)
    assert "user-id" not in rendered
    assert "D:\\data\\users" not in rendered
    assert "api_key" not in rendered


def test_retry_handler_passes_only_current_session_token(monkeypatch):
    service = FakeImportService()
    monkeypatch.setattr("ui.gradio_app.import_service", service)
    retry_import_task("token-a", "task-a")
    assert service.calls == [("retry_task", "token-a", "task-a")]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.\venv\Scripts\python.exe -m pytest tests/ui/test_import_handlers.py -q`

Expected: FAIL because the new handlers and formatters are not defined.

- [ ] **Step 3: 初始化全局 ImportService 与 WorkerPool**

在 `ui/gradio_app.py` 的数据库初始化和 `session_registry` 创建之后，创建 `ImportTaskRepository(DATA_ROOT / "app.db")`、`ImportWorkerPool(repository, session_registry.runtime_registry, session_registry.storage)`、`ImportTaskService(session_registry, repository, session_registry.storage, worker_pool)`，并在 Blocks 构建前调用 `worker_pool.start()`。注册 `atexit.register(worker_pool.stop)`，保证进程正常退出时不再领取新任务。

保持旧 `upload_document()` 名称作为兼容入口，但将其改为调用 `submit_import_batch()` 的单文件列表形式；新的 Gradio File 控件返回 `list[FileData]`，旧测试传入 `None` 时仍在会话校验后返回 Gradio 错误。

- [ ] **Step 4: 实现 handler 和安全格式化**

`submit_import_batch()` 先 `_require_session(session_token)`，再调用 Service；返回批次 ID、`format_batch_summary()`、`format_task_table()` 和当前文档下拉刷新值。`refresh_import_batches()` 返回最近 50 个批次的 choices/value、摘要和表格；空令牌直接返回空组件，不查询数据库；非空伪造/过期令牌抛 `gr.Error`。重试 handler 只把 token、task/batch ID 交给 Service，不接受用户 ID、路径或 document ID。

`format_task_table()` 只输出 `original_name`、状态中文标签、阶段中文标签、整数进度、尝试次数、下次重试的本地化时间和已脱敏错误摘要。

- [ ] **Step 5: 修改 Blocks 布局和事件绑定**

上传 Tab 使用：

```python
import_files = gr.File(
    label="批量上传文档",
    file_count="multiple",
    file_types=[".pdf", ".txt", ".md", ".docx"],
    type="filepath",
)
submit_import_btn = gr.Button("提交导入")
import_batch_dropdown = gr.Dropdown(label="最近批次", choices=[], interactive=True)
import_summary = gr.Markdown()
import_tasks = gr.Dataframe(
    headers=["文件名", "状态", "阶段", "进度", "尝试次数", "下次重试", "错误"],
    interactive=False,
)
retry_selected_btn = gr.Button("重试所选失败项")
retry_batch_btn = gr.Button("重试本批次全部失败项")
refresh_import_btn = gr.Button("手动刷新")
import_timer = gr.Timer(value=1, active=True)
```

用 `import_timer.tick(fn=refresh_import_batch, inputs=[session_token, import_batch_dropdown], outputs=[import_summary, import_tasks])` 绑定只读轮询，并设置 `queue=False`。登录和注册输出增加最近批次、摘要、表格；退出登录清空这些组件。提交成功后自动选择新批次。Dataframe 行选择只用于读取任务 ID 映射，实际重试前仍由 Service 校验任务状态和用户归属。

- [ ] **Step 6: 扩展授权回归测试**

把 `upload_document`、批次提交、批次读取、单项重试、批次重试加入 `tests/ui/test_authenticated_handlers.py` 的缺失/伪造/过期 token 参数化表，并断言拒绝请求不改变 SQLite、History、RAG 或暂存目录。

- [ ] **Step 7: 更新依赖并运行 UI 测试**

将 `requirements.txt` 的第一行改为：

```text
gradio==6.19.0
```

Run: `.\venv\Scripts\python.exe -m pytest tests/ui/test_import_handlers.py tests/ui/test_authenticated_handlers.py tests/ui/test_document_selection.py -q`

Expected: PASS；若环境尚未安装依赖，先运行 `.\venv\Scripts\python.exe -m pip install -r requirements.txt`，再重复测试。

- [ ] **Step 8: 提交**

```powershell
git add ui/gradio_app.py tests/ui/test_import_handlers.py tests/ui/test_authenticated_handlers.py tests/ui/test_document_selection.py requirements.txt
git commit -m "feat: add batch import progress panel and retries"
```

---

### Task 7: 多用户集成、删除保护、重启验收与文档

**Files:**

- Create: `tests/integration/test_batch_import_acceptance.py`
- Modify: `tests/integration/test_multi_user_acceptance.py`
- Modify: `tests/integration/test_qdrant_document_scope.py`
- Modify: `assistants/pdf_learning_assistant.py`
- Modify: `README.md`

**Interfaces:**

- Consumes: Tasks 1–6 的完整任务服务、WorkerPool、Assistant 导入和 UI handler。
- Produces: 可重复执行的端到端验收套件，以及使用说明中的批量任务、进度、失败重试和重启恢复章节。

- [ ] **Step 1: 写两个用户端到端验收测试**

```python
# tests/integration/test_batch_import_acceptance.py
def test_two_users_can_import_same_name_without_cross_scope(tmp_path):
    app = make_isolated_import_app(tmp_path, fake_rag=True, worker_count=2)
    token_a = app.sessions.register("UserA", "correct horse battery")
    token_b = app.sessions.register("UserB", "correct horse battery")
    batch_a = app.service.submit_batch(token_a, [FakeFile("same.md", b"A")])
    batch_b = app.service.submit_batch(token_b, [FakeFile("same.md", b"B")])
    app.pool.run_until(lambda: app.service.get_batch(token_a, batch_a.batch_id).succeeded == 1)
    app.pool.run_until(lambda: app.service.get_batch(token_b, batch_b.batch_id).succeeded == 1)
    assert app.service.get_batch(token_a, batch_b.batch_id).total == 0
    assert app.runtime("user-a").rag_tool.list_documents() == [batch_a.tasks[0].document_id]
    assert app.runtime("user-b").rag_tool.list_documents() == [batch_b.tasks[0].document_id]


def test_clear_documents_is_rejected_while_task_is_active(tmp_path):
    app = make_isolated_import_app(tmp_path, blocking_runner=True)
    token = app.sessions.register("UserA", "correct horse battery")
    app.service.submit_batch(token, [FakeFile("a.md", b"A")])
    with pytest.raises(ImportTasksActiveError):
        app.sessions.get_session(token).assistant.clear_all_documents()
```

- [ ] **Step 2: 运行集成测试确认失败**

Run: `.\venv\Scripts\python.exe -m pytest tests/integration/test_batch_import_acceptance.py -q`

Expected: FAIL until the application fixture and clear protection are wired.

- [ ] **Step 3: 在 Assistant 清空入口加入活动任务保护**

在 `PDFLearningAssistant._clear_documents_coordinated()` 入口前通过 Runtime 注入的 `import_task_service.has_active_tasks(self.user_id)` 检查 `queued`、`running`、`retry_wait`；存在时返回固定提示并且不进入 RAG、History 或文件删除。将 service 以可选 Runtime 字段注入，轻量测试构造的无 Runtime Assistant 保持原行为。

- [ ] **Step 4: 实现重启和失败队列验收**

集成测试使用临时 SQLite：创建一个 `running` 任务后销毁 WorkerPool，重新初始化 Repository/WorkerPool，断言任务被恢复为 `queued` 并最终成功；用 fake clock 验证第 4 次执行失败后 `failed`，调用 `retry_failed_in_batch()` 后 `auto_retry_count=0` 且任务重新 `queued`。

- [ ] **Step 5: 更新 README**

在文档导入章节加入以下明确说明：

- 可一次选择最多 20 个文件，单文件 100 MiB，批次 500 MiB。
- 提交后任务在服务端持久化，浏览器关闭或退出登录不会取消。
- 同用户串行、不同用户最多 4 个并行。
- 阶段进度含暂存、解析、切块、嵌入、RAG 持久化和提交。
- 瞬时失败自动按 2/10/30 秒重试，最终失败可单项或整批失败项重试。
- 应用重启会恢复中断任务；失败源文件保留，成功后删除暂存副本。

同时说明第一版不支持取消、优先级和分布式 Worker，并更新安装命令反映 `gradio==6.19.0`。

- [ ] **Step 6: 运行完整回归**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest -q
```

Expected: 全部测试 PASS；不得访问真实 Qdrant、Neo4j、LLM 或工作区上传数据。若集成测试显式标记为 live，则只执行默认离线路径并单独记录 live 测试未运行。

- [ ] **Step 7: 运行静态检查和应用导入检查**

Run:

```powershell
.\venv\Scripts\python.exe -m compileall app assistants hello_agents ui
.\venv\Scripts\python.exe -c "import ui.gradio_app as app; print(type(app.demo).__name__)"
git diff --check
```

Expected: `compileall` 无错误；应用导入打印 `Blocks`；`git diff --check` 无空白错误。确认没有生成 `data/`、上传文件、数据库或缓存进入 Git。

- [ ] **Step 8: 提交**

```powershell
git add tests/integration/test_batch_import_acceptance.py tests/integration/test_multi_user_acceptance.py tests/integration/test_qdrant_document_scope.py assistants/pdf_learning_assistant.py README.md
git commit -m "test: verify durable batch import acceptance"
```

---

## 计划自查

### 规格覆盖

- 批量暂存和 20/100 MiB/500 MiB 限制：Task 1、Task 5、Task 6。
- SQLite 持久化、状态机、原子领取、用户隔离：Task 1。
- 同用户串行、跨用户并行、4 Worker：Task 2、Task 5。
- Runtime 后台租约和退出登录继续执行：Task 2、Task 5。
- 解析、切块、嵌入、持久化进度：Task 3、Task 6。
- 浏览器/服务端上传进度与后台进度分段：Task 5、Task 6。
- 结构化错误、2/10/30 秒退避、手动重试：Task 3、Task 5、Task 6。
- `document_id`、History、Memory 幂等和崩溃窗口：Task 4、Task 5、Task 7。
- Gradio 最近 50 批次、Timer 轮询、登录/退出：Task 6。
- 活动任务阻止清空全部文档：Task 7。
- JSON/Qdrant/Neo4j、多用户、UI 授权回归：Task 3、Task 6、Task 7。
- README、依赖固定和离线完整测试：Task 6、Task 7。

### 占位符与类型一致性

- 未使用未决占位语或不确定步骤。
- `ImportStatus`、`ImportStage`、`ProgressCallback` 在 Task 1 定义，Task 3–6 使用同一名称。
- `ImportTaskRepository` 的任务 ID、批次 ID、用户 ID 参数在所有任务中保持一致。
- `PDFLearningAssistant.load_document()` 的新增参数在 Task 4 定义，Task 5 使用同一顺序和名称。
- `ImportTaskService` 的 token、batch ID、task ID 接口在 Task 5 定义，Task 6 和 Task 7 使用同一接口。

## 执行交接

计划完成并保存于 `docs/superpowers/plans/2026-07-30-batch-import-async-tasks.md`。有两种执行方式：

1. **Subagent-Driven（推荐）**：每个任务启动一个新 subagent，任务间进行两阶段审查。
2. **Inline Execution**：在当前会话中按任务批次执行，并在每个检查点暂停复核。
