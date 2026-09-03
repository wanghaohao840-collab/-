# 文档库垂直切片设计

**日期：** 2026-08-15

**状态：** 已批准设计，待实施计划

**实施基线：** `cc37078`（`codex/figma-product-ui-foundation`）

**目标分支：** `codex/document-library-vertical-slice`

## 目标

把 React 产品壳中的 `/documents` 从迁移占位页升级为第一个真实产品功能页，形成可验证的文档管理闭环：

1. 查看当前登录用户已经成功导入的文档；
2. 一次选择并提交多个 PDF、TXT、Markdown 或 DOCX 文件；
3. 查看持久化导入任务的队列、阶段、进度与结果；
4. 重试失败任务、取消尚未进入提交阶段的任务；
5. 删除已经导入的文档，并同步清理该文档的历史与 RAG 数据；
6. 在桌面、平板、手机三档中遵循现有 Penpot 设计系统和无障碍约束。

本切片必须复用现有 `ApplicationServices`、`SessionRegistry`、`ImportTaskService`、`ImportWorkerPool`、每用户 Runtime 和持久化目录，不建立第二套导入管线。Gradio 继续挂载在 `/legacy/`，但不作为文档库主流程的实现依赖。

## 当前实现事实

- React、React Router、TanStack Query、FastAPI JSON API 与 `/legacy/` Gradio 已由同一个 ASGI 应用提供。
- `/documents` 已在统一导航中，但当前只渲染 `MigrationPage`。
- 认证使用 HttpOnly session cookie；CSRF token 仅存在前端内存，所有修改请求通过 `X-CSRF-Token` 发送。
- `ImportTaskRepository`、`ImportTaskService` 和 `ImportWorkerPool` 已支持持久化批次、排队、运行、自动重试、成功、失败、手动重试、重启恢复及同用户串行执行。
- 当前任务状态为 `queued | running | retry_wait | succeeded | failed`，尚无取消语义。
- 当前导入服务只接受路径式上传对象；FastAPI `UploadFile` 是文件流，不能假设存在可访问的客户端或服务器路径。
- 文档历史仍是每用户 `history.json` 中的 `documents` 列表；正式文件位于该用户 `documents/` 目录；RAG 数据按 `document_id` 隔离。
- 当前按 ID 删除逻辑只通过 Assistant 的内部协调路径暴露，API 尚无文档列表、导入或删除路由。
- Penpot 是产品 UI 的唯一设计源；现有 handoff 只有登录、AppShell 和会话过期参考板，尚无文档库画板。

## 已确认的产品方案

采用“异步任务式文档库”。用户提交文件后立即获得持久化批次，后台 worker 继续处理；页面轮询活动任务并展示真实阶段。不同文件独立成功或失败，一个失败不阻塞同批其他文件。

界面采用已经批准的列表优先布局：

- 桌面端保留 248px 侧栏和 64px 顶栏，文档列表为主内容；
- 平板端使用 72px rail，列表仍保持单一主阅读方向；
- 手机端隐藏侧栏并保留 64px 底部导航，导入面板改为底部 sheet；
- 页面覆盖首次空库、导入中、部分失败、全部完成四种关键状态；
- 不展示后端无法可靠提供的页数、分块数、阅读进度或示例统计。

## 架构与职责边界

### `DocumentLibraryService`

新增应用服务，作为认证后的文档列表和删除边界。它依赖 `SessionRegistry`、`UserStorage` 与现有导入服务，但不直接解析 Cookie 或 HTTP 请求。

职责：

- 由 session token 解析当前用户，拒绝调用方提供 `user_id`；
- 在用户 Runtime lock 下读取最新历史并生成稳定的文档视图；
- 只为位于当前用户文档根内的源文件读取文件大小；
- 按 `document_id` 检查活动导入任务并执行协调删除；
- 返回结构化结果或领域错误，不解析 Assistant 的展示文本。

为避免 API 调用私有方法，在 `PDFLearningAssistant` 增加公开、按 ID 的结构化删除入口；现有 `delete_current_document()` 委托该入口并继续负责 Gradio 文案。底层依旧使用现有 Runtime lock、RAG 删除、HistoryRepository 和安全文件删除逻辑。

### `ImportTaskService`

保留为唯一导入写边界，并扩展两项能力：

1. 接收路径来源和 FastAPI 文件流两种输入，统一转换成内部上传候选；
2. 提供取消请求接口。

文件流必须由服务端分块复制到用户专属的持久 staging 目录。复制时统计实际字节数并执行限制，不能相信浏览器提供的文件大小或文件名。路径式 Gradio 上传继续走同一个 staging 核心，不复制业务规则。

### FastAPI 路由

新增 `documents` 与 `imports` 两组 router。路由层只负责：

- 认证与 CSRF dependency；
- multipart、路径参数和 query 参数解析；
- Pydantic schema 转换；
- 领域错误到既有 API error envelope 的映射。

路由不得直接读取用户目录、调用 RAG 或修改任务表。

### React 页面

`DocumentsPage` 只替换 `/documents` 的 `MigrationPage`；概览、问答、检索、笔记和洞察仍保持迁移态。页面拆分为：

- `DocumentToolbar`：标题、名称筛选与“导入文档”；
- `DocumentList`：真实文档列表、空态和删除入口；
- `ImportDialog`：文件选择、限制说明、提交与焦点管理；
- `ImportBatchPanel`：当前及最近批次、任务阶段、进度、重试和取消；
- 查询 hooks：封装 query key、轮询条件、mutation 与失效策略。

组件不直接读取认证存储；所有请求继续通过 `AuthProvider.request()`，由既有 401 generation 逻辑统一处理会话过期。

## 文档视图模型

`GET /api/v1/documents` 返回按 `loaded_at` 倒序排列的成功文档：

```json
{
  "items": [
    {
      "document_id": "uuid",
      "name": "研究方法.md",
      "file_suffix": ".md",
      "size_bytes": 18240,
      "loaded_at": "2026-08-15T08:30:00Z",
      "status": "ready"
    }
  ]
}
```

约束：

- `document_id` 是所有选择、删除和后续功能的唯一身份；文件名允许重复。
- `size_bytes` 和 `loaded_at` 对旧迁移数据允许为 `null`。前端对 `null` 省略对应说明，不显示伪造值。
- 路径、RAG namespace、用户 ID 和内部存储键绝不进入响应。
- 历史中相同 `document_id` 的重复记录只返回最新一项；无效记录被安全忽略并记录服务端诊断日志。
- 本切片先做客户端名称筛选和最近导入排序，不增加全文检索、分页或标签系统。

## 导入 API 契约

### 提交批次

`POST /api/v1/imports`，`multipart/form-data`，字段名为重复的 `files`，成功返回 `202 Accepted` 和完整批次摘要。

限制沿用现有后端常量：

- 每批最多 20 个文件；
- 每文件最多 100 MiB；
- 每批实际字节总和最多 500 MiB；
- 支持 `.pdf`、`.txt`、`.md`、`.markdown`、`.docx`。

服务先验证文件数量与文件名，再逐个流式写入 `.partial` 文件并计数；单文件和批次上限在写入过程中即时终止。全部暂存成功后才原子改名并创建任务记录。任一暂存失败时删除本批次已创建的 `.partial` 和正式 staging 文件，不创建半批次数据库记录。

### 查询

- `GET /api/v1/imports?limit=20`：返回当前用户最近批次，`limit` 范围 1–50；
- `GET /api/v1/imports/{batch_id}`：返回当前用户的指定批次；
- 不存在和其他用户的资源都返回同样的 404，避免资源枚举。

### 修改

- `POST /api/v1/imports/{batch_id}/tasks/{task_id}/retry`；
- `POST /api/v1/imports/{batch_id}/retry-failed`；
- `POST /api/v1/imports/{batch_id}/tasks/{task_id}/cancel`。

三者都要求有效 CSRF，返回更新后的完整批次摘要。`task_id` 必须确实属于 URL 中的 `batch_id` 且属于当前用户。

## 任务状态与取消语义

任务状态扩展为：

```text
queued ───────────────→ running ───────────────→ succeeded
  │                       │  │
  │                       │  ├───────────────→ retry_wait ─→ running
  │                       │  ├───────────────→ failed
  └───────────────────────┴──────────────────→ cancelled
```

同时增加 `cancelled` stage 和 nullable `cancel_requested_at`。

取消规则：

- `queued`、`retry_wait`：Repository 在事务中直接改为 `cancelled`，设置完成时间；事务后删除该任务的 staging 文件。
- `running` 且尚未进入 `committing`：事务中设置 `cancel_requested_at`。Runner 在每次阶段/进度回调以及进入提交前检查请求，抛出专用 `ImportCancelled`，清理临时/正式尝试文件并最终写入 `cancelled`。
- 进入 `committing` 的任务：取消接口返回 409 `import_not_cancellable`，让已经开始的原子提交完成，避免历史与 RAG 半提交。
- `succeeded`、`failed`、`cancelled`：重复取消返回当前批次，不改变状态；这是幂等终态读取，不把失败任务改成取消。
- 被取消任务不允许 retry，因为 staging 文件已清理；用户需要重新选择原文件。

staging 删除失败不允许把已取消任务重新入队。服务记录经过清洗的诊断，任务保持 `cancelled`；启动时的终态 staging reconciliation 扩展到 `cancelled`，在验证持久化路径仍与该任务完全匹配后重试清理。

提交边界必须是 Repository 的原子操作：Runner 只有在“未收到取消请求”时才能把 stage 切换为 `committing`。取消请求和提交开始竞争时，只允许一个事务获胜。

SQLite 现有 status `CHECK` 不包含 `cancelled`。初始化阶段需执行幂等迁移：在 worker 启动前创建新表、复制现有数据、替换旧表并重建三个索引，同时增加 `cancel_requested_at`。迁移必须保留既有任务、批次外键和每用户单运行任务唯一索引；迁移失败时启动失败且原表保持可恢复，不静默丢任务。

## 批次响应

批次摘要保留现有任务字段，并增加 `cancelled` 计数与每项 `cancel_requested_at`：

```json
{
  "batch_id": "uuid",
  "created_at": "2026-08-15T08:30:00Z",
  "updated_at": "2026-08-15T08:30:04Z",
  "counts": {
    "total": 3,
    "queued": 0,
    "running": 1,
    "retry_wait": 0,
    "succeeded": 1,
    "failed": 1,
    "cancelled": 0
  },
  "tasks": [
    {
      "task_id": "uuid",
      "document_id": "uuid",
      "original_name": "研究方法.md",
      "file_suffix": ".md",
      "size_bytes": 18240,
      "status": "running",
      "stage": "embedding",
      "progress": 62,
      "error_code": null,
      "error_summary": null,
      "cancel_requested_at": null,
      "created_at": "2026-08-15T08:30:00Z",
      "started_at": "2026-08-15T08:30:01Z",
      "finished_at": null,
      "updated_at": "2026-08-15T08:30:04Z"
    }
  ]
}
```

响应不暴露 `user_id`、`staged_relative_path`、尝试用临时路径或底层异常。自动/手动重试次数可以保留为数值，但 UI 首版只在失败说明中使用，不制造额外统计卡。

## 删除 API 与一致性

`DELETE /api/v1/documents/{document_id}` 要求 CSRF，成功返回 `204 No Content`。

执行顺序：

1. 解析当前 session，并在当前用户 Runtime lock 下重新读取历史；
2. 验证目标文档属于当前用户，且历史中的源路径位于该用户 documents 根内；
3. 若同一 `document_id` 存在 `queued | running | retry_wait` 任务，返回 409 `document_import_active`；
4. 通过公开的协调删除入口清理该 `document_id` 的 RAG 数据、文档历史、相关问答记录和正式源文件；
5. 通过 `SessionRegistry` 清除同一用户所有活动 session 中恰好指向该 `document_id` 的选择状态；其他文档、其他用户和各 session 的其余选择状态不变。

删除成功后前端同时失效 documents 与 imports queries。若协调删除失败，返回安全的 `document_delete_failed`；前端显示可重试提示并立即重新获取真实列表，不根据乐观状态假装删除成功。

## React 查询与交互状态

查询键固定为：

- `['documents']`；
- `['imports', { limit: 20 }]`；
- `['import', batchId]`（仅在需要单批详情时使用）。

轮询规则：

- 任一可见任务为 `queued | running | retry_wait` 时每 2 秒刷新批次；
- 所有可见批次均为终态时停止定时器；
- 页面重新聚焦时执行一次普通 refetch；
- 任一任务从活动态进入 `succeeded` 时失效 documents query；
- mutation 完成后以服务端返回摘要更新对应批次，再失效列表，不进行状态机乐观推断。

页面状态：

- 首次加载：使用现有 Skeleton，不显示空库；
- 空库：说明支持格式与限制，主操作为“导入文档”；
- 导入中：活动批次置于文档列表上方，显示文件级真实进度和“取消”；
- 部分失败：成功文档仍可使用；失败项显示安全摘要及“重试”，批次提供“重试全部失败项”；
- 全部完成：活动区收起为最近结果，文档列表展示真实数据；
- 查询失败：保留上一次成功数据并提供重新加载，不把网络错误解释为无文档。

删除必须通过确认 Dialog；确认文案包含真实文件名。操作过程中按钮进入 busy/disabled；失败后 Dialog 保持或显示 Toast，焦点不丢失。

## 错误模型

沿用现有 envelope：

```json
{
  "error": {
    "code": "import_file_too_large",
    "message": "单个文件不能超过 100 MiB",
    "retryable": false,
    "field_errors": {}
  }
}
```

稳定错误码至少包括：

- `unsupported_document_type`（422）；
- `import_batch_empty`（422）；
- `import_too_many_files`（422）；
- `import_file_too_large`（413）；
- `import_batch_too_large`（413）；
- `import_stage_failed`（500，可重试）；
- `import_batch_not_found` / `import_task_not_found`（404）；
- `import_not_retryable`（409）；
- `import_not_cancellable`（409）；
- `document_not_found`（404）；
- `document_import_active`（409）；
- `document_delete_failed`（500，可重试）。

持久化与响应中的错误摘要继续经过现有白名单和凭据/路径清洗。日志可以记录 request correlation 信息，但不能把 Cookie、CSRF token、文件内容或绝对用户路径返回浏览器。

## 安全与隔离不变量

- API 永远从 HttpOnly cookie 对应的 session 获取用户身份，不接受 body/query/header 中的 `user_id`。
- 所有 POST/DELETE 必须通过既有 CSRF dependency；GET 不改变服务端状态。
- 客户端文件名仅作为显示名：保存路径只由服务端生成的 UUID、验证后的后缀和用户根构造。
- staging 的 `.partial`、批次目录和清理目标必须经 `UserStorage.assert_within_user()` 与现有 reparse-point 防护。
- 相同 batch/task/document UUID 在其他用户作用域下表现为不存在。
- 前端不把 session、CSRF token、任务或文档状态写入 localStorage/sessionStorage。
- 保持单一 `ApplicationServices` 生命周期与单 Uvicorn worker 部署契约；本切片不引入浏览器直传、外部队列或第二数据库。

## 无障碍与响应式要求

- 页面只有一个可见 `h1`“文档库”；状态标题按层级使用 `h2/h3`。
- 桌面、平板弹窗打开时聚焦首个可操作控件，循环 `Tab/Shift+Tab`，`Escape` 关闭，关闭后焦点返回“导入文档”。
- 手机导入 bottom sheet 遵循相同焦点与关闭规则，并锁定/恢复 body 原始滚动状态。
- 手机按钮、更多菜单、删除、重试、取消和文件移除目标均至少 44×44px。
- 文件输入有可见 label、支持格式/大小说明和逐文件错误关联；拖放不是唯一入口。
- 导入阶段和终态变化通过节制的 `aria-live="polite"` 汇总播报，进度条提供名称、当前值和最大值；不在每次轮询重复播报相同值。
- 状态不只依赖颜色，必须同时包含文字和/或图标。
- 动画遵循 `prefers-reduced-motion`，不得出现无限旋转；busy 状态在 reduced-motion 下保持静态可识别。
- 所有普通小字使用满足现有背景对比度的 text-primary，不把 approved secondary token 用于不合格的 12–16px 正文。

## Penpot 设计源工作

在现有文件 `3be9e5e1-190f-8090-8008-6ff3f3dcd54c` 中增量完成，不创建第二个设计文件：

1. fresh-read 文件身份、七页唯一性、现有 Tokens 与组件 ID；不匹配时停止写入；
2. 复用 AppShell、Button、IconButton、Dialog/Drawer、TextField、Badge、EmptyState、Skeleton；仅在确有重复结构时新增 DocumentRow、ImportTaskRow 或 FilePicker 组件及必要 variants；
3. 在 `02 Desktop`、`03 Tablet`、`04 Mobile` 创建文档库完成态参考板；
4. 在 `05 States` 创建首次空库、导入中、部分失败和导入面板状态；手机导入面板表现为 bottom sheet；
5. 所有示例值明确只用于设计构图，代码与 E2E 不得据此制造业务数据；
6. 写后用原节点 ID fresh-read，检查组件链接、文字 overflow、实际 bounds overflow、44px 触控目标和 token 绑定；
7. 真实导出参考 PNG 到 `docs/product-ui/reference/penpot/`，更新 `penpot-handoff.md` 和 component mapping；
8. 代码实现完成后，用真实统一服务器生成桌面、平板、手机浏览器基线并逐张人工对照。允许字体抗锯齿差异；未记录的结构、间距或响应式差异必须修复或写入 deliberate differences。

## 测试策略

所有实现任务遵循 RED → GREEN，并保持测试文件与生产边界对应。

### 数据库与 Repository

- 旧 schema 到 cancelled schema 的幂等迁移、数据/索引保留及失败回滚；
- queued/retry_wait 立即取消、running 设置请求、committing 拒绝、终态幂等；
- 取消与 begin-committing 竞争只产生一个合法结果；
- cancelled 不可 claim、不可 retry，重启恢复不把 cancelled 重新入队；
- 用户、batch 与 task 作用域隔离。

### Service 与 Worker

- 路径来源和 FastAPI 流来源共用 staging 核心；
- 文件数、真实单文件字节和真实批次字节边界；
- 中途读失败、超限或 DB 创建失败时不残留半批次；
- 解析、切块、嵌入、持久化阶段的协作取消均清理尝试文件并进入 cancelled；
- committing 之后不取消，成功路径与现有重试语义无回归；
- 文档列表只返回当前用户的安全字段；删除阻止活动导入并只删除目标 ID。

### API

- 未登录为 401；所有 mutation 缺失/伪造 CSRF 为 403；
- multipart 提交返回 202，限制错误使用稳定 envelope；
- list/get/retry/cancel/delete 的 404 不泄露其他用户资源；
- API 响应不包含绝对路径、user_id、staging path 或秘密；
- 未知 `/api/*` 继续返回 JSON 404，不落入 SPA fallback。

### React

- `/documents` 渲染真实 DocumentsPage，其他迁移路由不变；
- 空库、加载、导入中、部分失败、完成和查询失败状态；
- 活动态按 2 秒轮询、终态停止、成功后文档 query 失效；
- 上传前端校验只是反馈，服务端错误仍可正确呈现；
- retry/cancel/delete 使用 CSRF 且不产生未处理 Promise rejection；
- Dialog/sheet 的 focus trap、Escape、return focus、scroll restore 与 reduced-motion。

### 真实浏览器验收

Playwright 使用真实统一 FastAPI/React/worker，不使用 `page.route().fulfill()`、history state 注入或测试专用后门：

1. 注册并进入文档库；
2. 上传小型 TXT/Markdown fixture，观察真实任务到 succeeded 和文档出现；
3. 使用受控失败 fixture 验证单项失败与真实 retry；若无法通过合法输入稳定触发失败，则在 service/integration 层覆盖失败，不给生产服务增加 E2E 控制端点；
4. 通过足够大的可取消 fixture 观察 running 后取消，或在 worker integration 层精确验证竞态；浏览器用例不得依赖不稳定时序；
5. 删除文档并确认 session restore 后仍不存在；
6. 第二用户不能读取、取消、重试或删除第一用户资源；
7. desktop 1440×1024、tablet 1024×768、mobile 390×844 通过 axe serious/critical=0、键盘焦点和视觉 no-update 门禁。

## 验收标准

- `/documents` 不再是迁移态，能够完成“选择文件 → 持久任务 → 成功文档 → 删除”的真实闭环。
- 批量导入限制、自动/手动重试和原有 Gradio 导入行为无回归。
- 取消状态在 SQLite、Repository、Service、Worker、API 和 React 中端到端一致；committing 竞态有确定结果。
- 活动任务轮询会自动停止，不产生后台无限请求或重复 live-region 噪声。
- 删除只作用于当前用户和目标 `document_id`，活动导入时明确阻止。
- 三档页面与批准的 Penpot 参考在结构、布局、换行、触控和交互状态上匹配。
- targeted Python、API、frontend unit、typecheck、lint、build、design token/mapping、Playwright 与 unified smoke 全部通过。
- 最终检查无 secret、Figma URL/路径、raw brand color、tracked runtime、上传文档或生成缓存；`git diff --check` 通过。
- 任务经独立审查后无 Critical/Important；提交只包含本切片授权范围，未经用户要求不推送。

## 实施顺序

1. Penpot 文档库画板、组件和 handoff；
2. cancelled schema migration、Repository 状态机与竞态门；
3. 流式 staging、Service 取消与 Worker 协作取消；
4. DocumentLibraryService、结构化按 ID 删除与 API routers/schemas；
5. React DocumentsPage、查询/mutation 与响应式交互；
6. 真实浏览器、视觉、无障碍、统一服务 smoke 和最终整分支审查。

每一步只在前一步契约稳定后开始。Penpot 和后端基础任务可在文件所有权完全不重叠时并行规划，但不得由多个执行者同时写同一个工作树或同一个设计页面。

## 非目标

- 不实现问答、全文检索、笔记或洞察页面；
- 不增加文档预览、重命名、标签、文件夹、排序偏好持久化或批量删除；
- 不显示无法可靠获取的页数、chunk 数、阅读进度或业务统计；
- 不增加外部任务队列、Redis、Celery、多 Uvicorn worker 或浏览器直传对象存储；
- 不改变 session cookie、CSRF、12 小时滑动过期或“保持登录状态”决策；
- 不修改 Qdrant/JSON 后端选择、RAG 检索算法、Memory、引用或报告语义；
- 不移除 `/legacy/`，也不复制 Gradio 组件树到 React。

## 已关闭的设计问题

- 布局选择：列表优先，不采用双栏工作台或卡片画廊。
- 导入方式：持久化异步任务，不采用同步阻塞上传或仅跳转 Gradio。
- 响应式：桌面、平板、手机三档全部实现。
- 取消：仅提交前可协作取消，提交阶段明确拒绝。
- 元数据：只展示可从历史与正式文件可靠取得的字段。
- 设计源：继续使用现有 Penpot 文件，不转回 Figma，不建立平行设计源。
