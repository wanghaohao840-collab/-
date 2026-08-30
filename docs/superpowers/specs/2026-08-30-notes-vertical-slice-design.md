# 知研学习笔记垂直切片设计

**日期：** 2026-08-30  
**状态：** 已完成交互确认，等待书面规格审阅  
**目标路由：** `/notes`  
**依赖基线：** 已集成并验收的文档库与智能问答 `/qa` 垂直切片

## 1. 目标

把现有 `history.json.notes` 和 `MemoryTool` 双写式原型升级为可产品化、可恢复、
可迁移且可长期演进的学习笔记领域。用户能够在 React `/notes` 中创建、查找、
编辑和删除 Markdown 笔记，也能从 QA 回答或引用一键创建带可验证来源的笔记。

SQLite 中的 Note 聚合是唯一产品事实源。Memory 只保存可重建的语义检索投影；
投影不可用不得导致笔记正文丢失、回滚或不可读。

## 2. 已批准的产品决策

1. 使用独立关系型 Note 聚合，不把笔记、洞察、书签提前抽象为通用
   `KnowledgeItem`，也不采用事件溯源。
2. 笔记采用扁平库，通过概念、多个标签、关键词、来源和排序组织；首版不提供
   文件夹或笔记本。
3. 正文以 Markdown 源文本保存，阅读时安全渲染；不使用富文本块 JSON。
4. `/notes` 支持手动创建；QA 回答和引用提供“一键记为笔记”。未来文档阅读摘录
   复用同一来源模型。
5. 当前记录带版本号和乐观并发控制。首版不展示修订历史，但删除使用墓碑，边界
   允许以后增加修订表和恢复能力。
6. 删除文档或 QA 来源时保留用户撰写的正文、概念和标签，清除来源标识、定位与
   系统摘录，仅保留无敏感信息的“来源已删除”状态。
7. UI 采用列表与编辑器主从布局：桌面保留来源侧栏，平板使用来源 Drawer，手机
   在笔记列表与编辑页间导航。

## 3. 范围

### 3.1 首版包含

- 笔记创建、读取、编辑、软删除和清空全部笔记；
- Markdown 编辑与安全预览；
- 可选概念、0–10 个标签；
- 关键词搜索、标签筛选、来源筛选、更新时间排序和游标分页；
- QA 回答或单条引用转笔记；
- 来源删除后的安全墓碑；
- 旧 `history.json.notes` 幂等迁移；
- SQLite 到 Memory 的持久化异步投影、失败状态、重试和重建边界；
- desktop、tablet、mobile 三档 UI、键盘操作、Axe 和视觉基线；
- Penpot 权威画板、组件映射、仓库参考导出与实现交接；
- 功能开关、旧 `/legacy/` 回滚窗口、完整发布与 Docker Linux 验收。

### 3.2 首版不包含

- 文件夹、笔记本、双向链接或知识图谱可视化；
- 多人共享、评论、协作光标或 CRDT；
- 用户可见的修订历史和版本恢复；
- 离线编辑、浏览器持久草稿或本地优先同步；
- AI 自动生成整套笔记、自动聚类或自动改写用户正文；
- 文档阅读器中的框选摘录；
- Word/PDF 导出；导出能力在后续学习中心或洞察切片中复用 Markdown 事实源实现；
- SSE/WebSocket；首版复用现有有界轮询与资源读取模式。

## 4. 架构边界

依赖方向保持：

```text
React /notes
  → FastAPI notes routes / DTO
    → NoteService
      → NoteRepository / NoteMigrationService / NoteProjectionRepository
        → SQLite
      → NoteProjectionWorker
        → MemoryTool
```

- `ApplicationServices` 继续是唯一组合与生命周期根。
- `NoteService` 负责权限后的领域验证、幂等、版本检查、来源解析和用户运行时协调。
- `NoteRepository` 只处理 SQLite 聚合和游标，不调用 Memory、RAG 或文件系统。
- `NoteProjectionWorker` 只消费持久化任务；它不能成为读取笔记正文的前置条件。
- QA 来源由服务端从 `QaRepository` 读取。客户端不得提交摘录正文、文档名或任意
  `user_id` 来伪造来源。
- 现有 `HistoryRepository` 在迁移后不再接受产品笔记双写；旧 Gradio 添加、清空和
  回忆入口必须改为调用同一 `NoteService`，而不是继续写 `history.json.notes`。

## 5. 数据模型

所有表使用 SQLite 外键、明确状态约束和按用户的复合所有权。时间使用 UTC ISO
时间戳，公开 API 不暴露内部路径或存储键。

### 5.1 `notes`

| 字段 | 语义 |
|---|---|
| `id` | 稳定 UUID，主键 |
| `user_id` | 所有者；所有读取和变更都必须带入 |
| `body_markdown` | 1–20,000 字符的 Markdown 事实文本 |
| `concept` | 可空；非空时最多 120 字符 |
| `version` | 从 1 开始，每次用户可见变更递增 |
| `projection_state` | `pending`、`ready` 或 `failed` |
| `client_request_id` | 创建幂等键；同用户唯一 |
| `created_at` | 创建时间 |
| `updated_at` | 最后用户可见变更时间 |
| `deleted_at` | 可空；非空表示墓碑 |

约束与索引：

- `(user_id, id)` 唯一，用作所有子表复合外键目标；
- `(user_id, client_request_id)` 唯一；
- 当前列表索引覆盖 `(user_id, deleted_at, updated_at, id)`；
- 列表标题不单独持久化：优先显示非空概念，否则显示 Markdown 去格式后的第一条
  非空文本行；没有可显示行时使用“未命名笔记”。
- 关键词搜索使用下述 `notes_fts`，不把 Memory 结果混入确定性列表。

### 5.2 `note_tags`

| 字段 | 语义 |
|---|---|
| `user_id`、`note_id` | 复合所有权外键 |
| `normalized_tag` | Unicode 规范化并去除首尾空白后的比较值 |
| `display_tag` | 用户最后保存的展示值，最多 32 字符 |

主键为 `(user_id, note_id, normalized_tag)`。同一笔记最多 10 个标签；空标签和规范化
后重复标签在服务层拒绝或折叠为一个展示值。

### 5.3 `note_sources`

| 字段 | 语义 |
|---|---|
| `id` | 稳定来源关联 UUID |
| `user_id`、`note_id` | 笔记复合所有权 |
| `source_kind` | 首版为 `qa_message` 或 `qa_citation` |
| `source_resource_id` | 活动来源的服务端资源 ID；来源删除后置空 |
| `parent_resource_id` | 例如 citation 所属 QA message；删除后置空 |
| `document_id` | 可空；活动引用来源的文档 ID；删除后置空 |
| `locator_json` | 服务端验证的页码、章节或 citation 位置；删除后置空 |
| `excerpt_snapshot` | 服务端生成的系统摘录；删除后置空 |
| `source_deleted_at` | 可空；非空时 UI 只显示“来源已删除” |
| `created_at` | 关联建立时间 |

来源删除后保留关联行及 `source_kind`、`source_deleted_at`，但清空三个资源 ID、定位、
摘录和标题快照。这样既能保留用户理解所需的墓碑，又不会泄漏已删除内容。

每条笔记首版最多 10 个来源。创建来源时必须在服务端验证 QA message、citation 和
document 都属于同一用户，且对应删除 fence 不活动。

### 5.4 `note_projection_tasks`

每次创建、编辑、删除或迁移都在与 Note 变更相同的 SQLite 事务中写入任务：

| 字段 | 语义 |
|---|---|
| `id` | 任务 UUID |
| `user_id`、`note_id` | 目标笔记 |
| `note_version` | 任务对应版本 |
| `operation` | `upsert` 或 `delete` |
| `status` | `queued`、`running`、`failed` 或 `completed` |
| `attempt_count` | 领取次数 |
| `available_at` | 退避后可领取时间 |
| `lease_owner`、`lease_expires_at` | 条件写入和陈旧 worker 防护 |
| `last_error_code` | 安全内部错误码，不保存密钥或路径 |
| `created_at`、`finished_at` | 生命周期时间 |

唯一键 `(user_id, note_id, note_version, operation)` 保证幂等。worker 领取前在同一
`BEGIN IMMEDIATE` 事务中回收过期租约。执行前重新读取当前 Note：旧版本 upsert、
已删除 Note 的 upsert 和已被新版本覆盖的任务安全完成为 no-op。最终失败只把
`projection_state` 设为 `failed`，不回滚 SQLite 笔记。

### 5.5 `note_legacy_imports`

迁移账本保存 `(user_id, legacy_import_key, note_id, source_digest,
legacy_memory_id, imported_at, legacy_memory_cleaned_at)`；两个 Memory 字段可空。
`legacy_import_key` 来自规范化旧记录内容、原始 `created_at`、`session_id` 及相同记录
的出现序号。Note ID 使用用户命名空间下的 UUIDv5，因此启动扫描和首次访问扫描可
重复执行而不会重复导入；完全相同的两条旧笔记仍按出现序号保留为两条。

迁移服务还会扫描当前用户 semantic Memory 中
`knowledge_type=learning_note` 的旧随机 ID 记录。只有内容、概念、session 与迁移
账本记录能够精确对应时，才把该旧 Memory ID 记录到迁移账本；稳定 Note 投影写入
成功后再删除这个精确旧 ID。无法精确对应的 Memory 不自动删除，而是记录安全告警，
避免误删用户的其他语义记忆。

### 5.6 `notes_fts`

关键词搜索使用 SQLite FTS5，索引当前未删除 Note 的 `body_markdown`、`concept` 和
规范化标签文本。Note、tag 与 FTS 行在同一事务内更新；软删除立即移除 FTS 行。
搜索结果仍以 `notes` 表的用户范围和墓碑条件做二次约束，不能只信任 FTS 命中。
若运行时 SQLite 不支持 FTS5，应用启动明确失败并给出部署错误，不静默退化为无界
全表扫描。

## 6. 领域操作与一致性

### 6.1 创建

1. 从会话得到 `user_id`，验证 CSRF 与 `client_request_id`。
2. 在用户运行时锁内规范化 Markdown、概念和标签。
3. 若带 QA 来源，在同一锁范围内从 `QaRepository` 读取并验证 message/citation、
   document 所有权及删除 fence。
4. SQLite `BEGIN IMMEDIATE` 后写入 Note、标签、来源和 projection task，一次提交。
5. 返回已保存 Note 与 `projection_state=pending`；唤醒 worker 是提示，不是可靠性
   边界。

同一用户和 `client_request_id` 重试返回原 Note。请求体不同却复用幂等键返回
`409 NOTE_IDEMPOTENCY_CONFLICT`。

### 6.2 编辑

- 请求携带 `expected_version`；更新条件包含 `user_id`、`note_id`、`deleted_at is null`
  和当前版本。
- 成功时版本递增，标签整体替换，来源首版保持不变，并写入新 upsert task。
- 条件未命中但 Note 存在时返回 `409 NOTE_VERSION_CONFLICT`；不存在、已删除或跨用户
  统一返回安全 `404`。

### 6.3 单条删除与清空

- 单条删除需要 `expected_version`，原子设置 `deleted_at`、递增版本并写入 delete task。
- `POST /notes/clear` 需要确认短语“清空全部笔记”。同一事务软删除当前用户所有活动
  Note，并为每条写入幂等 delete task。
- 清空笔记不得删除文档、QA conversation/message/citation、报告或底层全部 Memory。
- 旧 projection worker 通过版本与墓碑检查，不能复活已删除 Note。

### 6.4 来源删除

- QA conversation 或 document 删除建立 fence 后，删除 worker 在自己的持久化阶段
  中调用 Note 来源清理。
- 清理仅处理同一用户和被删除资源范围内的 `note_sources`，清空来源数据并设置
  `source_deleted_at`；Note 正文、概念、标签和 Note ID 不变。
- 阶段推进和来源清理可安全重放；任何陈旧 owner 或过期 lease 都不能提交。

### 6.5 Memory 投影

- upsert 投影使用稳定键 `note:{user_id}:{note_id}`，metadata 至少包含 Note ID、版本、
  concept、tags 和 `knowledge_type=learning_note`。
- 投影正文不包含已删除来源摘录；来源墓碑也不进入 Memory。
- delete 只删除该稳定键，不调用“清空全部 Memory”。
- `POST /notes/projections/retry` 只把当前用户最终失败且仍与当前 Note 版本匹配的任务
  重新排队。
- 旧随机 ID 的 learning-note Memory 只在对应稳定投影成功后按精确 ID 删除；投影失败
  时保留旧记录，避免迁移过程丢失唯一可用的语义副本。

## 7. API 契约

统一前缀 `/api/v1/notes`。公开请求不接受 `user_id`，所有 mutation 要求有效
`X-CSRF-Token`。

### 7.1 列表

`GET /notes?cursor=&limit=20&query=&tags=&source_kind=&sort=updated_desc`

- `limit` 范围 1–50，默认 20；
- 游标编码 `(updated_at, note_id)`，不暴露 offset；
- 默认返回最新页且按更新时间倒序；相同时间以 Note ID 稳定排序；
- `query` 匹配正文、概念和标签；`tags` 使用交集语义；
- 只返回未删除 Note；跨用户数据不可见。

响应包含 `items`、`next_cursor`，列表 item 包含摘要、概念、标签、来源状态、版本、
投影状态和时间戳，不返回完整来源摘录。

### 7.2 创建与读取

- `POST /notes`：`body_markdown`、`concept`、`tags`、`client_request_id`，可选
  `source={kind, qa_message_id, citation_id}`。`citation_id` 只在
  `kind=qa_citation` 时出现。
- `GET /notes/{note_id}`：返回完整 Markdown、标签和安全来源 DTO。

服务端自行生成来源摘录与定位。客户端提交的未知字段被拒绝，不能覆盖投影状态、
版本或来源快照。

### 7.3 更新、删除、清空与重试

- `PATCH /notes/{note_id}`：`body_markdown`、`concept`、`tags`、`expected_version`；
- `DELETE /notes/{note_id}`：`expected_version`；
- `POST /notes/clear`：`confirmation="清空全部笔记"`；
- `POST /notes/projections/retry`：无目标 ID 时重试当前用户全部可重试失败任务。

### 7.4 错误

| HTTP | 错误码 | 行为 |
|---|---|---|
| 404 | `NOTE_NOT_FOUND` | 不存在、已删除或跨用户统一返回 |
| 404 | `NOTE_SOURCE_NOT_FOUND` | 来源不存在或不属于当前用户 |
| 409 | `NOTE_VERSION_CONFLICT` | 保留客户端草稿，返回当前安全版本元数据 |
| 409 | `NOTE_SOURCE_DELETING` | 来源 fence 活动，禁止建立新关联 |
| 409 | `NOTE_IDEMPOTENCY_CONFLICT` | 同一幂等键对应不同创建请求 |
| 422 | `NOTE_VALIDATION_ERROR` | 正文、概念、标签、来源或确认短语非法 |
| 503 | `NOTE_PROJECTION_UNAVAILABLE` | 仅显式投影重试无法调度时使用 |

错误响应不包含绝对路径、SQL、堆栈、凭据或其他用户资源是否存在的信息。

## 8. React 体验

### 8.1 桌面 `1440 × 1024`

- 应用侧栏保持现有导航；内容区依次为笔记列表、Markdown 编辑器和来源侧栏。
- 列表顶部提供搜索、标签和来源筛选；选中项在编辑器中保持稳定。
- 编辑器包含概念、Markdown 工具栏/正文、编辑与安全预览切换、标签、删除和显式保存。
- 来源侧栏展示活动来源卡和“来源已删除”墓碑；不得展示被清理的标题或摘录。

### 8.2 平板 `1024 × 768`

- 使用现有收缩导航；保留笔记列表与编辑器双栏。
- 来源通过可聚焦 Drawer 打开；Escape 关闭并把焦点返回触发按钮。
- 所有主要触控目标至少 44×44 CSS px。

### 8.3 手机 `390 × 844`

- `/notes` 首先展示搜索、筛选和笔记列表；浮动或底部主操作用于新建。
- 选择笔记进入独立编辑视图；返回时保留列表查询和滚动位置。
- 保存、来源和删除操作位于单手可达区域，但不得遮挡正文或系统键盘。
- 来源和筛选使用底部 Drawer；页面禁止非预期横向滚动。

### 8.4 状态与交互

- 空状态解释笔记价值，并提供“新建笔记”和“从 QA 记录”入口；不显示虚构数量。
- 显式保存。未保存草稿只在 React 内存中保留，不写 `localStorage` 或
  `sessionStorage`；离开前确认。
- 网络失败保留草稿；成功后以服务器响应替换缓存并播报保存状态。
- `409` 冲突保留本地 Markdown，可复制草稿或重新加载服务器版本；首版不自动合并。
- `projection_state=failed` 显示非阻塞提示和重试入口，阅读、编辑、删除仍可用。
- 清空全部笔记使用危险确认对话框，明确说明文档和 QA 不会删除。

### 8.5 QA 转笔记

- 已完成的 assistant message 提供“记为笔记”；每条 citation 提供“记录此引用”。
- 操作打开预填草稿，用户可编辑 Markdown、概念和标签后显式保存。
- QA 页面不直接写 Memory 或 History；它调用 notes API，并使用稳定
  `client_request_id` 防止双击重复创建。

## 9. Markdown 安全

- 保存原始 Markdown 文本，但渲染禁用 raw HTML。
- 只允许标题、段落、列表、引用、强调、行内/块代码和安全链接。
- 链接协议只允许 `https`、`http` 和安全的相对站内链接；拒绝 `javascript:`、
  `data:` 和凭据化 URL。
- 外部链接使用 `rel="noopener noreferrer"`；渲染器不得执行脚本、内联事件或
  任意 iframe。
- 搜索摘要和列表摘要按纯文本生成，不把未净化 HTML 注入 DOM。

## 10. 迁移与回滚

1. 数据库初始化幂等创建 Note、tag、source、projection task 和 migration ledger 表。
2. 启动恢复扫描已知用户目录；`NoteService` 首次访问也调用同一
   `ensure_user_migrated(user_id)`，覆盖启动后发现的用户。
3. 迁移在用户运行时锁和数据库事务内读取旧 notes、写 Note 聚合、迁移账本和
   projection task。
4. 成功迁移后产品读写只使用 SQLite。旧 `history.json.notes` 不删除、不追加，供
   明确回滚窗口只读检查。
5. `/legacy/` 的添加和清空动作改接 `NoteService`；旧回忆展示可以在回滚窗口读取
   SQLite 适配结果，不能恢复双写。具体地，legacy `recall` 的“历史笔记”部分调用
   NoteService 关键词搜索；学习报告和旧统计中的笔记数量从 SQLite 当前 Note 计算，
   不再读取 `history.json.notes` 或进程内 `stats["notes_added"]`。
6. `NOTES_ROUTE_ENABLED=false` 只把 React `/notes` 切回明确迁移页，不停止迁移、
   投影恢复或 legacy 服务写入同一 Note 领域。

回滚不得删除 SQLite Note 表、迁移账本或投影任务，也不得把旧 JSON 当成较新的事实源。

## 11. Penpot 与视觉交接

Penpot 继续是唯一权威视觉源。实现前在现有“知研 · 智能文档学习助手”文件中增加：

- desktop：默认列表/编辑、来源已删除、投影失败、清空确认；
- tablet：默认双栏、来源 Drawer；
- mobile：列表、编辑、筛选 Drawer、来源 Drawer、版本冲突；
- empty：三档真实空状态。

创建后 fresh-read 文件、页面、画板和组件 ID，将无密钥 ID 与映射写入
`docs/product-ui/penpot-handoff.md` 和 `penpot-component-map.json`。仓库导出命名为
`desktop-notes*.png`、`tablet-notes*.png`、`mobile-notes*.png`。浏览器截图不能冒充
Penpot 源导出；视觉基线只能在批准的 Penpot 变化或真实实现变化后逐张更新。

## 12. 测试与验收

### 12.1 后端

- 数据模型、约束、复合所有权和迁移幂等；
- 创建幂等、正文/标签验证、游标稳定性和默认 20 条；
- 同用户并发编辑版本冲突、跨用户安全 404；
- QA message/citation 来源服务端解析与删除 fence；
- 单条删除、清空、陈旧 worker、防复活和来源墓碑重放；
- projection 每轮租约恢复、最终失败、显式重试和 Memory 重建；
- 旧 JSON 两条相同笔记按出现序号保留且不重复导入；
- legacy Gradio 与 React API 使用同一 NoteService，无新 notes 双写。

### 12.2 前端

- API 类型、游标分页、筛选缓存键和 mutation 失效范围；
- Markdown 编辑/安全预览、草稿离开确认和冲突保留；
- QA message/citation 转笔记幂等；
- 删除、清空确认、来源墓碑、projection pending/failed；
- 桌面/平板/手机布局、Drawer 焦点、44px 触控和键盘顺序。

### 12.3 E2E 与发布门

- 真实服务器创建、编辑、刷新恢复、过滤、QA 转笔记、删除和清空；
- 三档 Playwright 视觉与 Axe serious/critical 为零；
- 旧笔记迁移后在 `/notes` 可见，重复启动不重复；
- Memory 投影失败时笔记仍可读写，恢复后显式重试收敛；
- 删除文档后笔记保留且来源内容已清理；
- 完整 Python、Vitest、类型检查、Lint、构建、Playwright、设计契约、`pip check`、
  `npm audit --audit-level=moderate`、`git diff --check`；
- Docker Linux 单 worker build/up、健康检查、登录、笔记 CRUD、QA 转笔记、重启恢复和
  清理验证。

任何 notes 验收场景不得新增 skip。测试产生的数据库、上传文件、Memory、报告、
trace、截图差异和凭据不得提交。

## 13. 长期演进

- 集合/笔记本以后通过独立 `note_collections` 和关联表添加，不改变 Note ID。
- 修订历史以后通过 `note_revisions(note_id, version, ...)` 添加；当前 `version` 与墓碑
  已提供并发和回放边界。
- 语义搜索只查询 Memory 投影并返回 Note ID，再由 SQLite 读取当前正文，不能直接把
  旧投影当成事实。
- SSE/WebSocket 以后只替换 projection 状态通知；资源 ID、状态读取和恢复 API 保持。
- 多副本部署前把进程内用户锁、worker 唤醒、Session 和任务调度替换为共享实现；
  SQLite 事务、幂等键、版本条件和租约所有权语义必须保留。
- 未来文档摘录、文献检索收藏和学习洞察通过 `note_sources` 或只读 Note API 集成，
  不复制笔记正文到新的事实源。

## 14. 完成定义

只有同时满足以下条件，学习笔记垂直切片才可标记完成：

1. Penpot 权威画板、组件映射和三档导出通过 fresh-read 与设计测试；
2. SQLite Note 领域、旧数据迁移、来源清理和 Memory 投影通过独立代码评审；
3. React `/notes` 和 QA 转笔记替换迁移占位页，三档真实流程通过；
4. `/legacy/` 使用同一 NoteService，旧 notes 不再双写；
5. 完整测试、依赖审计、Docker Linux smoke 和差异检查全部通过；
6. 任务包逐项评审，最终整分支独立复审无 Critical 或 Important；
7. `FINAL_INTEGRATION_REVIEW.md` 记录实际提交、命令、结果、残余风险并标记 accepted。
