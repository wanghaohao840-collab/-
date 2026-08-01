# 批量导入、异步任务、上传进度与重试队列设计

## 目标

为当前多用户文档学习助手增加可靠的批量导入能力。用户一次可提交多个文档，提交后立即获得批次信息；文档在后台异步导入，页面可持续查看批次和单文件进度；瞬时失败按固定策略自动重试，最终失败可由用户手动重新排队；应用重启、用户退出登录或浏览器关闭均不丢失任务。

本功能继续遵守现有架构边界和数据规则：

- 依赖方向保持为 `UI -> Assistant/Application Service -> Tool -> Memory/RAG/Storage`。
- 所有任务、文件、Runtime、RAG、历史记录和查询同时按不可变 `user_id` 隔离。
- 每个文件在提交时获得固定 `document_id`，自动重试、手动重试和重启恢复都复用该 ID。
- PDF 页码、文件来源、原始文件名和现有多格式解析能力保持不变。
- JSON 与 Qdrant 后端继续遵守相同的文档级导入契约。

## 范围

第一版包含：

1. PDF、TXT、Markdown（`.md`）和 DOCX 多文件批量提交。
2. SQLite 持久化的批次与单文件任务。
3. 进程内后台 Worker，跨用户并行、同用户串行。
4. 批次汇总、单文件阶段和百分比进度。
5. 瞬时错误的自动退避重试。
6. 单个失败任务和一个批次全部失败任务的手动重试。
7. 应用重启后的任务恢复。
8. 退出登录或关闭页面后继续执行，重新登录后恢复查看。
9. 完整的用户隔离、幂等性、清理和回归测试。

第一版不包含任务取消、优先级、任务暂停、分布式 Worker、Redis、Celery、RQ、多进程协调、失败文件自动过期或任务记录删除界面。当前实现明确面向单进程 Gradio 部署；未来可在保持任务仓储和执行接口不变的前提下替换外部队列。

## 方案选择

采用“SQLite 持久化任务仓储 + 进程内 WorkerPool”。

仅使用 Gradio 原生事件队列无法可靠支持应用重启恢复、持久化失败队列和服务端任务生命周期。Celery/RQ 与 Redis 能支持分布式执行，但会引入当前本地产品阶段不需要的基础设施和运维复杂度。SQLite 已是项目的身份和报告索引存储，进程内 Worker 也与现有单进程用户 Runtime 和用户级写锁模型相符。

## 架构

```text
Gradio UI
  -> ImportTaskService
  -> ImportTaskRepository (SQLite)
  -> ImportWorkerPool
  -> UserRuntimeRegistry lease
  -> background PDFLearningAssistant
  -> RAGTool / MemoryTool
  -> RAG / Storage
```

### `ImportTaskRepository`

任务仓储只负责持久化和状态转换：

- 原子创建批次及其全部任务。
- 按用户读取批次、任务和汇总。
- 原子领取一个可运行任务。
- 更新任务阶段、进度、尝试次数和结果。
- 将到期的 `retry_wait` 任务重新排队。
- 在应用启动时恢复被进程中断的 `running` 任务。
- 将单个失败任务或一个批次内的全部失败任务手动重新排队。

仓储不读取上传文件，不创建 Runtime，不调用 Assistant，也不根据异常文本猜测重试类别。

### `ImportTaskService`

应用服务是 UI 与任务系统之间的授权边界：

- 从服务端会话令牌解析当前用户，拒绝前端传入或伪造的用户 ID。
- 校验批次大小、文件数量、扩展名和文件路径。
- 将 Gradio 临时文件复制到当前用户受控的 `imports/` 暂存目录。
- 在全部文件成功暂存后创建批次和任务。
- 查询当前用户的最近批次、批次汇总和任务。
- 校验任务归属后提交单项或批次手动重试。
- 在当前用户存在活动导入任务时阻止“清空全部文档”。

### `ImportWorkerPool`

WorkerPool 负责调度，不包含文档解析规则：

- 进程级最多同时运行 4 个任务。
- 同一 `user_id` 同时最多运行 1 个任务。
- 不同用户可并行执行。
- 一个文件失败不会阻止同批次的其他文件。
- Worker 捕获任务执行异常并始终把任务落到 `succeeded`、`retry_wait` 或 `failed`，避免可恢复异常遗留为 `running`。
- 应用关闭时停止领取新任务；正在执行的任务允许完成。若进程被强制终止，启动恢复流程负责重新排队。

WorkerPool 在数据库、用户存储和共享 RuntimeRegistry 初始化完成后启动。提交任务、手动重试和到期重试都会唤醒调度条件变量；调度器另以 1 秒超时重新检查数据库，保证没有进程内唤醒信号时仍能领取持久化任务。应用关闭入口先停止调度器，再等待已领取任务完成，最后关闭无租约的 Runtime。

### `ImportTaskRunner`

Runner 执行一个已经被领取的任务：

1. 校验暂存文件仍存在且位于当前用户根目录内。
2. 为任务取得用户 Runtime 后台租约。
3. 将暂存文件复制到正式文档路径的 `.uploading` 临时文件，再以 `os.replace()` 原子落到固定 `document_id` 对应路径。
4. 创建后台专用 `PDFLearningAssistant`，使用固定 `document_id` 和原始文件名调用导入。
5. 接收 Assistant/RAG Pipeline 的进度回调并持久化节流后的进度。
6. 成功时标记任务完成并删除暂存副本。
7. 失败时删除本次正式文件和 `.uploading` 文件，但保留暂存副本供重试。
8. 释放 Runtime 后台租约。

后台 Assistant 不属于浏览器会话，不参与当前文档选择；任务完成不会修改任一浏览器会话的 `current_document_id`。用户重新登录或刷新文档列表后可看到新文档。

Runner 在执行前先按 `document_id` 同时检查 History 和当前 RAG 后端：两者均已存在且 History 的 `import_task_id` 与当前任务一致时，不重复执行 RAG 导入，只确保同一 `import_task_id` 的 episodic Memory 事件存在，然后完成协调恢复；只有 RAG 或 History 一侧存在时重新执行同一 `document_id` 的覆盖导入和 History upsert，使两侧重新一致。

## SQLite 数据模型

### `import_batches`

- `id text primary key`：随机 UUID。
- `user_id text not null`：外键关联 `users(id)`。
- `created_at text not null`：UTC ISO-8601 时间。
- `updated_at text not null`：UTC ISO-8601 时间。
- `unique(id, user_id)`：供任务表复合外键约束批次归属。

批次状态和数量不单独存储，始终由所属任务实时汇总，避免重复计数失真。

### `import_tasks`

- `id text primary key`：随机 UUID。
- `batch_id text not null`。
- `user_id text not null`。
- `document_id text not null`：提交时生成，整个任务生命周期固定不变。
- `original_name text not null`：仅文件名，不包含客户端或服务器目录。
- `file_suffix text not null`：经过白名单规范化的小写扩展名。
- `size_bytes integer not null`。
- `staged_relative_path text not null`：相对当前用户根目录的受控路径。
- `status text not null`：`queued`、`running`、`retry_wait`、`succeeded` 或 `failed`。
- `stage text not null`：当前执行阶段的稳定代码。
- `progress integer not null`：0 到 100。
- `total_attempt_count integer not null default 0`。
- `auto_retry_count integer not null default 0`：当前自动重试周期已使用的次数。
- `manual_retry_count integer not null default 0`。
- `max_auto_retries integer not null default 3`。
- `next_attempt_at text`：仅 `retry_wait` 使用的 UTC 时间。
- `error_code text`：稳定的结构化错误代码。
- `error_summary text`：最长 500 个字符的脱敏摘要。
- `created_at text not null`。
- `started_at text`。
- `finished_at text`。
- `updated_at text not null`。
- 复合外键 `(batch_id, user_id)` 关联 `import_batches(id, user_id)`。

数据库增加以下约束和索引：

- `check(progress between 0 and 100)`。
- `check(status in ('queued','running','retry_wait','succeeded','failed'))`。
- `unique(user_id, document_id)`，防止同一用户重复创建同一导入文档任务。
- 部分唯一索引 `unique(user_id) where status = 'running'`，在数据库层保证同用户单任务执行。
- `(status, next_attempt_at, created_at)` 调度索引。
- `(user_id, created_at)` 批次与任务读取索引。

数据库初始化沿用现有幂等 `initialize_database()`，新表和索引使用 `create ... if not exists`，不得破坏现有数据库。

## 状态机

```text
queued -> running -> succeeded
             |
             +-> retry_wait -> queued
             |
             +-> failed -> queued  (manual retry)
```

合法转换如下：

- 创建任务：`queued`，`stage=queued`，`progress=10`，因为源文件已经安全暂存。
- Worker 领取：`queued -> running`，增加 `total_attempt_count`，写入 `started_at`。
- 瞬时失败且当前周期仍可重试：`running -> retry_wait`。
- 到达 `next_attempt_at`：`retry_wait -> queued`。
- 永久失败或自动重试耗尽：`running -> failed`。
- 手动重试：`failed -> queued`，`manual_retry_count + 1`，`auto_retry_count=0`，清空错误和 `next_attempt_at`，保留 `total_attempt_count`。
- 成功：`running -> succeeded`，`progress=100`，清空错误和 `next_attempt_at`。

`succeeded` 不允许重新排队。Repository 拒绝所有未列出的转换，并且每个修改查询同时包含任务 ID 和当前用户 ID。

批次汇总状态按任务计算：

- 存在 `running`：处理中。
- 不存在 `running` 但存在 `queued` 或 `retry_wait`：排队或等待重试。
- 全部 `succeeded`：全部成功。
- 全部为终态且成功、失败均存在：部分失败。
- 全部为 `failed`：全部失败。

## 批量提交与文件生命周期

Gradio 文件控件使用多文件模式。服务端限制为：

- 每批最多 20 个文件。
- 单文件最多 100 MiB。
- 单批总大小最多 500 MiB。
- 允许 `.pdf`、`.txt`、`.md`、`.docx`。

提交过程先对全部文件进行只读校验，再为每个文件分配任务 ID 和 `document_id`，并复制到：

```text
data/users/<user_id>/imports/<batch_id>/<task_id><suffix>
```

所有路径由 `UserStorage` 生成并再次通过 `assert_within_user()` 校验。原始文件名只存数据库和文档 metadata，不参与服务器路径拼接。

只有全部文件成功暂存后才在一个 SQLite 事务中创建批次和全部任务。如果校验、复制或数据库事务任一步失败，删除本次批次目录内已经创建的暂存文件，并且不留下半批任务。清理必须只针对已经解析并验证位于当前用户 `imports/<batch_id>/` 下的显式路径。

浏览器到 Gradio 的文件传输使用文件控件自带的上传反馈。点击“提交导入”后，处理函数使用 `gr.Progress` 按已校验和已复制文件数展示服务端暂存进度 0%–100%；持久化任务只在安全暂存完成后创建，因此任务表中的后台导入进度从 `staged=10%` 开始。浏览器上传/服务端暂存进度与后台导入进度是两个连续但不同的阶段，UI 文案必须明确区分。

任务成功后删除对应暂存文件；失败任务的暂存文件不自动过期或静默删除，以保证以后仍能手动重试。成功的正式文档继续位于现有 `documents/<document_id><suffix>`。失败任务不出现在文档历史或文档下拉框中。

## 进度模型

进度回调使用稳定接口：

```python
ProgressCallback = Callable[[str, int, int, str], None]
```

参数依次为阶段代码、阶段已完成单位、阶段总单位和可安全展示的短消息。Assistant、RAG Tool、共享文档准备层和 JSON/Qdrant Pipeline 增加可选回调；未传回调时行为与现有同步调用一致。

任务进度映射为：

- `staged`：10%。
- `parsing`：10%–25%。
- `chunking`：25%–40%。
- `embedding`：40%–80%。
- `persisting`：80%–92%。
- `committing`：92%–99%。
- `succeeded`：100%。

解析阶段按 PDF 页数、DOCX 段落或文本输入完成度报告；切块阶段按 segment 数量报告；嵌入阶段按 chunk 数量报告；Qdrant 持久化按批次数量报告。无法进一步细分的单次后端操作只更新阶段起点和终点，不伪造中间进度。

进度必须单调不减。Runner 只在阶段变化、整数百分比增加或安全消息变化时写 SQLite，避免每个内部 token 或字节产生数据库写入。一次失败后，下一次尝试从对应的 `queued` 状态重新显示阶段；累计尝试次数不清零。

Neo4j 图谱保持现有弱一致性。图谱构建失败不将已经成功的 RAG 导入改为任务失败；图谱继续使用自身的失败状态和显式重试入口。导入进度中的持久化阶段可显示“RAG 已完成，正在构建可选图谱”，但图谱失败后的任务结果为成功并附带安全警告。

## 并发与 Runtime 生命周期

同一用户的导入必须串行，因为现有 JSON RAG cache、History、Memory 和用户级写协调共享状态。不同用户拥有独立目录和 Runtime，可以并行。

`UserRuntimeRegistry` 增加后台租约计数。会话注册表和 WorkerPool 共享同一个 RuntimeRegistry：

- 登录会话创建和退出继续维护活动会话计数。
- Worker 开始任务前取得后台租约，任务结束后释放。
- Runtime 仅在活动会话计数和后台租约计数都为 0 时关闭。
- 用户退出、会话过期或关闭浏览器不会关闭仍被任务租用的 Runtime。

租约的取得、释放和 Runtime 缓存变更由 RuntimeRegistry 自身的 `RLock` 保护。Worker 不直接操作 SessionRegistry 的私有会话字典。

任务执行继续通过现有用户级 `RLock` 协调 RAG、历史和 Memory 写入。全局 Worker 数量固定为 4；数据库的部分唯一索引是同用户串行的最终防线。

## 重试与错误分类

一次自动重试周期包含初次执行和最多 3 次自动重试，共最多 4 次执行。三次重试前分别等待 2、10、30 秒。

自动重试的错误：

- `RAGConnectionError`。
- 网络连接中断、连接超时和读取超时。
- 明确映射为后端 5xx 的 `RAGOperationError`。
- SQLite `locked`/`busy` 且在短事务重试后仍失败。
- 其他由底层稳定错误代码明确标记为 transient 的错误。

不自动重试的错误：

- 不支持的文件类型、超过大小限制或暂存文件不存在。
- PDF/DOCX 损坏、无法解析或文档没有有效文本。
- `RAGConfigError`、`RAGAuthenticationError`、`RAGCollectionError`、`RAGDocumentTooLargeError`、`RAGEmbeddingError`。
- 后端 4xx、权限错误、路径越界和数据归属错误。
- 未知且未分类的编程错误。未知错误记录为 `unexpected_error` 并直接失败，不能通过无限重试掩盖缺陷。

`RAGActionResult` 增加稳定 `error_code` 和 `retryable` 字段。RAG Tool 在捕获已知异常时保留结构化类别，并只向 UI 返回脱敏摘要。Worker 只依据结构化字段或已知异常类型决定重试，不搜索中文或英文消息文本。

瞬时失败时：

1. 当前 `auto_retry_count` 增加 1。
2. 若增加后的值不超过 3，按 2、10、30 秒设置 `next_attempt_at` 并进入 `retry_wait`。
3. 若已使用三次自动重试，则进入 `failed`。

手动重试开启新的自动重试周期，复用任务、暂存文件和 `document_id`。`manual_retry_count` 增加，`auto_retry_count` 归零，`total_attempt_count` 保留。

## 重启恢复与幂等性

WorkerPool 启动前，Repository 在一个事务内查找所有 `running` 任务：

- 暂存文件仍存在且任务仍有可执行输入时，任务恢复为 `queued`，错误代码记为 `process_interrupted`。
- 暂存文件缺失时，任务进入 `failed`，错误代码记为 `staged_file_missing`。

恢复操作不增加自动重试次数；真正再次被 Worker 领取时才增加总尝试次数。

任务需要覆盖以下崩溃窗口：

1. 正式文件已落盘、RAG 尚未成功：恢复时使用同一 `document_id` 重新覆盖。
2. RAG 已成功、History 尚未提交：Assistant 的现有补偿逻辑继续尝试删除目标 RAG 数据；恢复仍可幂等覆盖。
3. RAG 与 History 已成功、任务状态尚未变为 `succeeded`：History 按 `document_id` upsert，不重复 append；恢复导入只更新同一记录。
4. History 已成功、episodic Memory 尚未提交：导入历史记录保存任务 ID；在用户锁内按 Memory metadata 中的 `import_task_id` 查询，缺失时才新增导入事件。Memory 快照成功持久化后，同一任务的恢复执行能识别已有事件并跳过，避免重复。
5. 任务成功、暂存文件尚未删除：启动或成功结果清理再次执行定向 `missing_ok` 删除。

History 文档记录和 episodic Memory metadata 都增加 `import_task_id`。同一任务的重试更新同一 `document_id` 记录；不同任务不得复用该用户已存在的 `document_id`。RAG 与 History 是任务成功的必要条件；导入 episodic Memory 写入失败时任务进入失败或重试分类，不能先把任务标为成功。

## UI 设计

上传页改为批量任务面板：

- 多文件上传控件。
- “提交导入”和“手动刷新”按钮。
- 最近 50 个批次的选择器，登录后默认选择最近批次。
- 批次摘要：总数、排队、执行、等待重试、成功和失败。
- 只读任务表：文件名、状态、阶段、进度、总尝试次数、下次重试时间和安全错误摘要。
- 失败任务选择器及“重试所选失败项”按钮。
- “重试本批次全部失败项”按钮。

页面使用 `gr.Timer` 每秒查询一次当前用户的任务表；轮询事件 `queue=False`，只执行短 SQLite 查询，不占用长任务队列。保留手动刷新作为显式兜底。文件控件使用 `file_count="multiple"`。

为保证与仓库已验证运行环境一致，Gradio 依赖固定为 `gradio==6.19.0`。实现和测试以该版本为准，不继续使用当前开放的 `gradio>=4.0.0` 范围。

登录成功后加载最近批次；退出登录时清空批次选择、汇总、任务表和失败任务选择器。轮询在会话令牌为空时直接返回空组件且不查询数据库；非空但无效的令牌按现有会话错误处理。查询、提交和重试处理函数都首先验证当前会话，并且 Repository 查询必须再次按 `user_id` 过滤。

UI 不展示用户 UUID、任务暂存路径、服务器绝对路径、凭据、带凭据 URL或完整堆栈。

## 与删除操作的交互

当前用户存在 `queued`、`running` 或 `retry_wait` 任务时，“清空全部文档”返回明确提示并且不执行删除，避免清空后后台任务重新写入文档。

“删除当前文档”只会看到已经成功写入 History 的文档，因此不会直接删除尚未成功的任务输入。失败任务保留在失败队列，重试后仍使用原 `document_id`。

任务成功后，现有删除当前文档和清空全部文档语义不变：只作用于当前用户，继续清理目标 RAG、History、问答和正式源文件，保留学习笔记。

## 安全与数据隔离

- 所有公开服务方法从会话解析用户身份，不接受 UI 传入的用户 ID。
- 所有批次和任务读写同时按主键与 `user_id` 过滤。
- 暂存路径以相对路径保存，解析后必须位于当前用户根目录。
- 文件名不能参与目录构造，扩展名必须来自共享白名单。
- 错误摘要使用现有凭据和 URL 脱敏逻辑，最长 500 个字符。
- SQLite 连接开启外键约束；跨表创建和状态领取使用事务。
- 失败任务不影响其他用户、同批次其他文件或已经成功的文档。
- 运行数据、上传文件、任务暂存文件和数据库不得加入 Git。

## 测试策略

### Repository 单元测试

- 批次和全部任务在一个事务中创建。
- 任务查询、汇总和重试严格按用户隔离。
- 合法状态转换成功，非法转换被拒绝且状态不变。
- 原子领取不会让两个 Worker 领取同一任务。
- 部分唯一索引阻止同一用户出现两个 `running` 任务。
- 到期重试、手动重试计数和批次汇总正确。
- 启动恢复分别处理存在和缺失的暂存文件。

### 调度器测试

- 同一用户任务严格串行。
- 两个用户可以并行。
- 全局活动任务不超过 4。
- 一个任务失败不阻塞同批其他任务。
- 可注入时钟验证 2、10、30 秒退避，不进行真实等待。
- Worker 异常后任务不会遗留为 `running`。

### Runner 与进度测试

- 成功导入删除暂存文件并保留正式文件。
- 失败删除正式文件并保留暂存文件。
- 每种支持格式产生正确阶段和单调进度。
- JSON 与 Qdrant 的嵌入和持久化进度覆盖全部 chunk。
- 瞬时和永久错误按结构化类别进入正确状态。
- 自动与手动重试复用原 `document_id`。
- 图谱失败保留 RAG 成功结果并返回警告。

### 幂等与生命周期测试

- 在各个崩溃窗口恢复时不产生重复 RAG 文档、History 或 episodic Memory。
- 退出登录和会话过期后任务继续运行。
- 重新登录后能够查看自己的任务进度。
- Runtime 在后台租约存在时不关闭，最后一个会话和租约释放后关闭。
- 活动任务存在时清空全部文档被拒绝且数据不变。

### UI 与授权测试

- 缺失、伪造和过期令牌不能提交批次、读取任务或手动重试。
- 用户 A 不能读取、选择或重试用户 B 的批次和任务。
- 多文件提交立即返回批次信息。
- 登录加载最近批次，退出清空任务组件。
- Timer 轮询只读且不进入 Gradio 长任务队列。
- 文件数量、单文件大小和批次总大小限制在服务端生效。

### 回归测试

现有完整测试套件必须继续通过，重点包括：

- JSON/Qdrant 后端契约和 `document_id` 隔离。
- PDF 页码与来源。
- 多文档问答、对比和总结。
- 多用户认证、会话、Runtime、History、Memory 和报告隔离。
- 删除当前文档、清空全部文档和清空笔记语义。
- Neo4j 图谱构建、恢复、重试和定向删除。

所有默认测试使用临时 SQLite、临时用户目录、伪造时钟和伪造外部后端，不读取工作区真实上传数据，不要求真实 Qdrant、Neo4j 或 LLM 服务。

## 验收标准

1. 已登录用户可一次提交最多 20 个受支持文档，处理函数在安全暂存和任务创建后立即返回。
2. 用户能看到批次汇总及每个文件的状态、阶段、进度、尝试次数、下次重试和安全错误。
3. 同一用户任务串行执行，不同用户最多 4 个任务并行，且数据完全隔离。
4. 瞬时错误按 2、10、30 秒自动重试，永久错误直接失败。
5. 自动重试耗尽后，用户可重试单个失败任务或一个批次的全部失败任务。
6. 自动、手动和重启恢复均复用原 `document_id`，不产生重复文档、历史或导入记忆。
7. 用户退出或关闭页面后任务继续；重新登录后可恢复查看。
8. 应用重启后排队、等待重试和被中断的运行任务都能得到确定处理。
9. 失败保留暂存源文件，成功清理暂存源文件，任何路径操作都不能越过当前用户根目录。
10. 活动任务存在时清空全部文档被拒绝；任务完成后的现有删除语义保持不变。
11. JSON、Qdrant、Neo4j、多用户、多文档和 UI 授权回归测试全部通过。
