# 批量异步导入运维治理与单机多进程设计

日期：2026-08-09

状态：已由用户批准，待实施计划

适用模块：`app/import_*`、`app/storage.py`、`app/runtime.py`、
`assistants/pdf_learning_assistant.py`、`ui/gradio_app.py`、部署与测试代码

## 1. 背景与目标

当前批量异步导入已经具备 SQLite 持久化、进程内四线程 Worker、同用户串行、
跨用户并行、阶段进度、自动与手动重试、重启恢复、staging 安全路径和用户隔离。
已验收的任务状态为 `queued/running/retry_wait/succeeded/failed`，成功任务删除
staging，失败任务保留 staging。

下一阶段将模块从“功能正确的单进程任务队列”升级为“可观测、可治理、可安全控制，
并支持单机多进程执行的任务系统”。实施必须保持现有文档隔离、来源追踪、RAG、
History、Memory 和文件安全边界。

目标按顺序为：

1. 建立只读可观测性和结构化日志；
2. 建立健康、心跳、租约与卡死任务治理；
3. 建立 staging 配额、源生命周期和失败源治理；
4. 建立单进程容量与稳定性基线；
5. 增加协作式取消、暂停和恢复；
6. 增加单机多进程 Worker；
7. 完成多进程容量、部署和集成验收。

## 2. 已确认的产品决策

- 最终扩展边界是单机多进程 Worker，继续使用 SQLite 租约协调，不引入
  Redis、Celery、PostgreSQL 或跨主机共享对象存储。
- 取消和暂停采用持久化协作式控制，在安全阶段边界生效，不强杀执行线程或进程。
- staging 默认配额为每用户 2 GiB、全局 10 GiB，并保留至少 1 GiB 可用磁盘；
  三项均可通过环境变量调整。
- 用户可手动删除失败或取消任务的源文件。自动保留清理完整实现但默认关闭；
  启用后支持 dry-run。
- Worker 每 5 秒续租；30 秒无心跳进入不健康告警；90 秒租约过期后才能接管。
- 容量硬门禁为四个 Worker、100 个用户、1000 个离线任务的零丢失、零重复、
  零串用户和全部终态。另运行 30 分钟 soak，性能数值形成机器相关基线，
  不设置脱离硬件的固定吞吐阈值。
- 普通 UI 只展示当前用户数据；全局数据通过本机 CLI JSON、部署健康检查和
  结构化日志提供，不新增管理员角色或远程管理端点。

## 3. 范围

### 3.1 范围内

- 用户级与全局运维快照；
- 安全结构化生命周期日志；
- Worker 注册、心跳、任务租约、过期接管和 fencing；
- SQLite WAL、`busy_timeout` 与有界写重试；
- staging reservation、真实大小复验、配额、孤儿和清理积压治理；
- 用户手动源文件删除、可配置保留清理和 dry-run；
- 协作式取消、暂停、恢复和文档作用域补偿；
- embedded 与 external 两种 Worker 模式；
- 单机 1–4 个独立 Worker 进程；
- 跨进程用户写租约；
- 离线压力、故障注入、soak、部署和恢复测试。

### 3.2 范围外

- 跨主机分布式执行、自动伸缩或高可用集群；
- Redis/Celery/RQ、PostgreSQL 或云对象存储；
- 管理员角色、远程运维 API 或 Prometheus 服务端；
- 任务优先级、截止时间和任意阶段的硬中断；
- 将既有 JSON、Qdrant、Neo4j 后端整体迁移到新存储系统；
- 与本阶段无关的 RAG 质量、GraphRAG 或产品 UI 重构。

## 4. 总体架构

### 4.1 进程职责

最终运行形态包含：

- **Web 进程**：认证、受限上传、用户级查询、控制请求及普通文档操作；
- **Worker 进程**：注册心跳、领取租约、执行导入、补偿、提交和源清理；
- **SQLite 控制面**：任务、租约、心跳、控制请求、配额、源生命周期和运维聚合；
- **用户数据目录**：继续保存 staging、正式文档、JSON RAG、History、Memory 和报告。

运行模式：

- `embedded`：Web 进程内启动 Worker，保留开发和兼容入口；
- `external`：Web 只提交任务，独立 Worker 命令执行；部署和多进程验收使用该模式。

模式必须由明确配置选择。external 模式下 Web 进程不得隐式启动 embedded Worker；
embedded 模式不得同时接受外部 Worker，以避免不同协调模型混用。

### 4.2 跨进程协调

现有 `threading.RLock` 继续用于单进程内部对象安全，但不再作为数据一致性的唯一保障。
新增 SQLite 用户写租约和单机内核文件锁，导入、删除、清空以及其他会改变 RAG、
History、Memory 或正式文件的入口必须同时持有同一用户的有效写租约与独占文件锁。
Windows 使用 `msvcrt` 文件锁，POSIX 使用 `fcntl` 文件锁；锁文件位于数据根目录内的
UUID 派生 `.locks` 路径，不包含用户提供的名称。任务状态、指标和控制请求不获取该长
租约，确保运行中仍能查询、暂停或取消；会读取 RAG、History、Memory 或正式文档的
业务操作必须通过相同协调器取得一致快照。

SQLite generation 防止旧 owner 更新控制面，内核锁防止仍存活但失联的旧进程与新进程
同时修改文件、History、Memory 或外部 RAG。租约过期只使任务具备接管资格；新 Worker
还必须成功取得用户内核锁。如果旧进程仍持锁，系统保持 `unhealthy` 并等待进程退出，
不得冒险并发执行。

Worker 领取任务时同时获得：

- `worker_id`；
- 不可猜测的 `claim_token`；
- 单调递增的 `claim_generation`；
- 任务租约和用户写租约到期时间。

所有进度、控制确认和终态写入必须同时匹配任务 ID、用户 ID、claim token 与
generation。租约过期后的旧 Worker 不能再更新或提交。

所有入口遵守唯一锁序：

1. 有界等待当前进程的用户 `RLock`；
2. 在没有打开 SQLite 写事务时，有界等待用户内核文件锁；
3. 用短 `BEGIN IMMEDIATE` 事务条件取得 SQLite 用户租约；
4. 若租约不可得，立即逆序释放文件锁和 `RLock`，退避后从第 1 步重试；
5. 成功后才读取或修改用户数据；
6. 结束时条件释放 SQLite 租约，再释放文件锁和 `RLock`。

禁止在等待内核锁时持有 SQLite 事务。任何获取失败都必须逆序释放已取得资源。控制
请求只写任务控制字段，不进入上述锁序。

前台业务操作最多等待 `IMPORT_USER_LOCK_TIMEOUT_SECONDS=30`，超时返回稳定
`user_busy`；调度器使用非阻塞尝试，锁不可得时跳过该用户并继续寻找其他用户任务，
不得让一个用户阻塞全局调度。

协调器必须在同一进程、同一线程内可重入：记录 owner thread 与 depth，只有最外层进入
和退出实际取得或释放内核锁与 SQLite 租约，嵌套 Assistant/Tool 调用只调整 depth。
不同线程和进程仍保持互斥。锁文件创建为固定一字节文件，并对父目录执行与 staging
相同的 symlink/reparse 安全校验。

### 4.3 进程间 Runtime 一致性

仅靠串行文件锁不能防止长期缓存的旧 Runtime 覆盖新数据。新增
`user_data_versions`，按用户保存 `committed_version`、`pending_version`、pending owner、
token 和时间戳。每个 Runtime 保存自己已加载的 committed version。

取得三层协调锁后：

1. Runtime 比较本地版本与 SQLite committed version；不一致时强制重载 JSON RAG、
   History、Memory 和文档索引；
2. 变更操作用条件更新预留 `pending_version = committed_version + 1`；
3. 所有文件写入使用 owner/token 唯一临时文件并原子替换，禁止固定 `.tmp` 名；
4. 业务提交成功后把 pending version 原子发布为 committed version；
5. 进程崩溃留下 pending version 时，下一 owner 在取得内核锁和过期租约后先完成任务补偿
   或幂等恢复，再发布或撤销 pending version；
6. 用户数据读取同样通过协调器；有已提交新版本时先重载，因此已有 Web Runtime 不会用
   旧内存快照覆盖 Worker 结果，也不会读取导入中的半成品。

当前后端能力矩阵：

- JSON：完成版本校验、强制重载、唯一临时文件和原子替换后支持 external；
- Qdrant：向量服务端写入仍按用户 namespace 与 `document_id` 过滤，外围文件状态执行
  同一版本协议后支持 external；
- Neo4j：事务写入仍按 namespace 与 `document_id` 过滤，并经过用户协调锁后支持 external。

每个后端必须显式声明 external capability 并通过跨进程契约测试。未知或未通过能力检查的
后端在 external 模式启动时失败；当前默认 JSON 后端必须实现上述能力，不能因默认配置
而使 external 模式不可用。

## 5. 数据模型

### 5.1 任务状态

最终状态集合为：

- `queued`
- `running`
- `retry_wait`
- `paused`
- `succeeded`
- `failed`
- `cancelled`

控制请求单独保存为 `none/pause/cancel`，不使用瞬时状态冒充已确认结果。

状态迁移规则：

- 创建：`queued`；
- `queued|retry_wait -> running`：成功领取并获得租约；
- `running -> succeeded|retry_wait|failed`：正常执行结果；
- `queued|retry_wait -> paused|cancelled`：尚未执行时立即确认；
- `running -> paused|cancelled`：Runner 在提交边界前检查请求、完成补偿后确认；
- `paused -> queued`：显式恢复，复用任务和 `document_id`；
- `failed -> queued`：仅限源为 `available`、没有 pending/failed compensation 且错误允许
  手动重试的普通失败；
- 过期 `running -> queued|failed`：仅由租约恢复流程决定；
- `succeeded` 与 `cancelled` 为终态，不接受普通重试。

控制动作矩阵：

- `pause(queued|retry_wait)` 立即进入 `paused`；`pause(running)` 在提交边界前写入请求；
- `pause(paused)` 幂等成功；其他终态拒绝 pause；
- `cancel(queued|retry_wait|paused)` 立即进入 `cancelled`；`cancel(running)` 在提交边界前
  写入请求；
- `cancel(cancelled)` 幂等成功；`succeeded`、普通 `failed` 和已进入 `committing` 拒绝
  cancel；
- 已请求 pause 后再 cancel 会原子升级为 cancel；已请求 cancel 后的 pause 返回现有 cancel，
  cancel 始终优先；
- `resume(paused)` 只有源为 `available` 且 compensation 已成功时进入 `queued`；
  `resume(queued)` 幂等成功，其他状态拒绝；
- 重复相同请求返回相同结果，不重复写 attempt、时间戳或计数；
- 请求被 Runner 确认或立即转换完成时，`requested_action` 清为 `none` 并写入
  `control_acknowledged_at`；恢复时也必须保持 `none`；
- compensation 失败时保留请求及目标状态，直到补偿成功后才确认并清除。

对 compensation 失败任务重复原控制请求返回当前 pending 目标；cancel 可以把 pending
pause 目标升级为 cancel。它不按普通 `failed` 处理。

暂停恢复不会增加失败次数、自动重试次数或手动失败重试次数。重新领取后执行尝试次数
可以记录为独立执行计数，但不得改变用户看到的失败重试语义。

`paused` 不占 Worker，却仍属于文档冲突保护范围；清空全部文档或删除相同
`document_id` 必须被拒绝。`cancelled` 不再阻止文档操作，但存在未完成补偿的任务无论
显示状态为何，都继续阻止相同文档和清空操作。

### 5.2 任务字段

`import_tasks` 直接增加以下字段：

- `worker_id`、`claim_token`、`claim_generation`；
- `lease_expires_at`、`heartbeat_at`、`last_progress_at`；
- `requested_action`、`control_requested_at`、`control_acknowledged_at`；
- `paused_at`、`cancelled_at`。

新增以下独立表：

- `import_staging_sources`：源 ID、任务/批次/用户 ID、相对路径、真实字节数、状态、
  清理错误码和各状态时间戳；
- `import_staging_reservations`：批次 reservation ID、批次/用户 ID、预留总字节、状态、
  到期时间和创建时间；
- `import_staging_reservation_items`：reservation、任务/文档 ID、受控 `.part` 与 final
  相对路径、复制状态、声明字节、已复制字节和文件后缀；
- `import_workers`：Worker ID、启动标识、模式、执行槽容量、状态、启动时间和最后心跳；
- `import_user_leases`：用户 ID、owner、token、generation、租约到期和最后心跳；
- `import_task_attempts`：任务、attempt、claim generation、Worker、开始/结束、结果；
- `import_task_stage_timings`：attempt、阶段、开始和结束时间；
- `import_task_compensations`：任务/attempt、触发原因、目标状态、补偿状态、允许列表错误码
  和更新时间；
- `user_data_versions`：用户 ID、committed/pending version、pending owner/token 和时间戳。

源状态、租约和阶段计时以这些表及 `import_tasks` 的明确字段为权威，不能依赖文件存在性
或日志反推唯一真相。

外键所有权固定如下，避免 task/source 双向依赖：`import_tasks` 不保存 source 外键；
`import_staging_sources` 以 `(task_id, batch_id, user_id)` 为唯一且非空外键指向任务。
任务表提供相同三列的唯一约束；attempt 以 `(task_id, batch_id, user_id, attempt_no)` 为
主键，stage timing 与 compensation 只通过该复合键引用 attempt。reservation item 通过
reservation ID 引用 header，在任务提交前不引用尚不存在的 task 行。所有用户相关表都
以复合外键或独立用户外键在数据库层阻止跨用户关联。

### 5.3 staging 生命周期与 reservation

reservation 保存在 `import_staging_reservations`，成功结算后删除 reservation 并创建
`import_staging_sources`。源生命周期为：

`available -> cleanup_pending -> deleted | missing`

- `available`：任务可读取的完整 staging；
- `cleanup_pending`：应删除但一次或多次安全删除失败；
- `deleted`：已确认不存在；
- `missing`：非预期缺失，保留诊断语义。

配额使用量包含 reservation、`available` 和 `cleanup_pending` 的字节数。成功源应立即
删除；失败、暂停和取消源默认保留。源被用户治理删除后，仓储层必须直接拒绝该任务的
重试或恢复，不能先排队再以文件缺失失败。

所有成功清理、用户手动删除和保留期删除都使用同一意图协议：

1. 条件更新 `available -> cleanup_pending`，持久化删除原因并提交；
2. 解析精确安全路径并执行 unlink；文件已经不存在视为幂等成功；
3. 条件更新 `cleanup_pending -> deleted`；
4. 任一步崩溃后只重试 `cleanup_pending`；
5. `missing` 仅用于没有删除意图时发现的非预期缺失。

因此不会出现“文件已经删除但数据库仍认为 available”或“数据库已经释放配额但文件仍
存在”的无账本窗口。`cleanup_pending` 在确认 `deleted` 前始终计入配额。

reservation 必须具有到期时间和唯一 ID。崩溃恢复只能释放过期且没有已提交任务引用的
reservation。header 状态为 `copying/ready/cleanup_pending`，item 状态为
`reserved/copying/copied/published`。final 路径在复制前写入 item；从 `.part` rename 后、
source 创建前发生崩溃时，final 文件仍由 reservation item 追踪并计费。

批次异常或 reservation 过期时，先把 header 条件更新为 `cleanup_pending`，再遍历全部
item 同时处理 `.part` 与 final 路径；只有所有受控文件确认不存在后才删除 reservation。
任何删除失败都会保留 header、item 和配额占用供下次重试。安全扫描发现完全没有账本的
UUID 结构孤儿时，先把它登记为 `cleanup_pending` 的 recovered-orphan reservation 并按
实际字节计费，再执行相同删除协议；链接或非 UUID 路径只告警，绝不跟随或删除。

### 5.4 Worker 与用户写租约

Worker 注册信息至少包括 `worker_id`、进程启动标识、模式、最后心跳、启动时间和
期望停止标记。不得记录主机敏感路径或凭据。

用户写租约按用户唯一，包含 owner、token、generation、到期时间和心跳。前台短操作
与后台导入使用同一协调接口，同时取得进程内 `RLock`、内核文件锁和 SQLite 租约。
进程死亡时内核自动释放文件锁；数据库租约到期后才能接管。旧 owner 的释放操作不得
删除新 owner 的租约。

### 5.5 数据库迁移

新增 `app_schema_migrations(version INTEGER PRIMARY KEY, name TEXT NOT NULL,
applied_at TEXT NOT NULL)`。没有该表但结构与当前验收基线匹配的数据库定义为 version 1；
最终目标为 version 5：

- version 2：Worker 注册、任务租约、attempt 与阶段计时；
- version 3：staging source、reservation 与删除意图协议；
- version 4：暂停、取消、控制字段与 compensation；
- version 5：用户数据版本协议和 external 能力元数据。

现有 `import_tasks` CHECK 只允许五种状态，version 4 不能依靠 `ALTER TABLE ADD COLUMN`
完成升级，必须使用事务化表重建：

1. 验证旧表结构和数据状态；
2. 创建新表与约束；
3. 完整复制并校验行数、主键、外键和唯一约束；
4. 原子替换表并重建索引；
5. 写入对应 migration version；
6. 失败时回滚整个事务，旧库仍可打开。

测试必须覆盖首次建库、已验收旧库升级、重复升级、迁移中故障、非法旧数据和升级后
重启。

旧任务迁移时按安全解析后的实际 staging 建立源记录：活动或失败任务的安全文件存在时
标记 `available`，不存在时标记 `missing`；成功任务仍有安全文件时标记
`cleanup_pending`，否则标记 `deleted`。旧 `running` 没有有效租约，迁移后由过期恢复
流程处理。既有占用超过新默认配额时不得删除旧数据；系统报告超配并拒绝新的 reservation，
直到治理后低于限制。

## 6. 核心流程

### 6.1 批次提交

1. 先认证会话，再读取文件元数据；
2. 校验格式、文件数量、声明大小、用户配额、全局配额和最小剩余磁盘；
3. 在 SQLite 中原子创建 reservation；
4. 将源文件受限流式复制到 UUID 派生的 `.part` 路径；
5. 复制过程中累计真实字节，超过任一限制立即终止；
6. 刷新文件数据后原子改名为正式 staging；
7. 在单个事务中创建批次和任务、标记源可用、结算真实字节并释放 reservation；
8. 通知 embedded 调度器；external Worker 通过数据库发现任务。

任一步失败均先把 reservation 标记为 `cleanup_pending`，再按全部 item 清理 `.part` 与
final；只有所有文件确认不存在后才删除 reservation。即使清理失败也没有半批可执行任务，
且遗留文件继续受账本和配额约束。复制后真实大小是账本和任务记录的权威值，不能继续
使用复制前 `stat()` 结果。

配额检查与 reservation 创建在同一个 `BEGIN IMMEDIATE` 事务中，以数据库账本阻止多
进程超配；磁盘可用空间在 reservation 前和每个流式复制块后检查。低于最小可用空间只
拒绝新的复制并把健康状态降为 `degraded`，不会阻止应用启动或删除既有数据。

### 6.2 Worker 执行

1. Worker 注册并保持进程心跳；
2. 原子领取一个符合时间条件且用户写租约可用的任务；
3. 生成新的 claim token/generation 并开始独立续租；
4. 精确解析并验证 staging 路径；
5. Assistant 通过控制令牌在 `staged/parsing/chunking/embedding/persisting`
   和 `committing` 前进行检查点；
6. 提交边界前的异常、暂停或取消统一进入文档作用域补偿；
7. Worker 只能在 claim 仍有效且 `requested_action=none` 时原子进入 `committing`；
   控制请求只能在任务尚未进入 `committing` 时原子写入。两项条件更新只允许一方成功，
   因而不存在“请求已接受但被提交忽略”的窗口；
8. 进入 `committing` 后控制令牌封闭，新请求返回“已超过控制点”，执行继续完成；
9. 终态写入通过 fencing 条件提交；
10. 任务成功后先把源条件更新为 `cleanup_pending`，再执行 unlink，成功后标记 `deleted`；
    清理失败时保持 `cleanup_pending`。普通导入失败、暂停和取消不创建删除意图，源保持
    `available`，最后释放租约。

### 6.3 补偿边界

协作式控制不得通过向进度回调抛出任意异常实现。Assistant 需要明确的控制令牌和
受支持检查点。

提交边界前，本次尝试产生的正式临时文件、目标 `document_id` 的未提交 RAG 数据、
History 和 Memory 副作用必须清理。执行清理前先持久化 compensation 记录；补偿必须按
用户与文档双重过滤、可重复执行，且不能影响先前已提交文档。只有 compensation 标记为
成功后，任务才能确认 `paused/cancelled/failed` 目标状态。

补偿失败时，任务进入 `failed`，错误码为允许列表中的 `compensation_failed`，并保留原
目标状态和待补偿记录。它不能手动重试导入，也不能解除相同文档的删除/清空保护。
Worker 维护循环和用户级“重试补偿”操作可以幂等重试；补偿成功后再进入原目标状态。
若执行异常与已接受的暂停/取消同时发生，已接受的控制请求优先决定补偿后的目标状态，
不得调度普通自动重试。

暂停确认后保留 staging、进度和最后安全阶段；恢复时从 staging 重新执行整个幂等尝试，
不承诺从某个 chunk 或 embedding 位置断点续传。

### 6.4 心跳、告警与恢复

- 每个 Worker 进程使用专用心跳线程每 5 秒续租，不能依赖解析、嵌入或进度回调发生；
- 30 秒无心跳进入 `degraded` 并产生安全告警；
- 90 秒租约到期且用户内核锁可取得后，才允许其他 Worker 原子接管；
- 启动时不再无条件重置所有 `running`；
- 只恢复过期租约、过期无引用 reservation 和可验证的清理积压；
- 过期任务已有暂停/取消请求时，恢复流程先完成补偿并进入对应目标状态，不重新执行导入；
- 接管增加 generation，旧 token 的任何写入均被拒绝；
- fencing 拒绝后的旧 Worker 只清理自己尚未发布的本地临时数据。

系统不尝试杀死仍存活但失去租约的进程。进程管理器负责最终终止；SQLite fencing 与
内核文件锁共同保证控制面和数据面的安全。

### 6.5 源治理

用户可以：

- 查看自己的 staging 已用、预留、待清理和配额；
- 删除单个 `failed/cancelled` 任务的源；
- 删除某批全部允许治理的源；
- 预览操作将释放的字节数。

删除活动、等待重试、运行、暂停或存在 pending/failed compensation 的源必须拒绝。
重复删除应幂等。部分删除失败时成功项
保持成功，失败项进入 `cleanup_pending`，并返回逐项安全结果。

自动保留策略默认关闭。启用后只扫描超过配置保留期且 compensation 已完成的
`failed/cancelled` 源；先提供 dry-run，再执行相同选择集。运行过程中状态变化的任务
必须通过条件更新和再次校验跳过。

## 7. 可观测性与运维接口

### 7.1 指标

用户级快照仅包含当前用户：

- 各状态任务数；
- 最老排队时间；
- 运行、暂停和近期终态数量；
- 用户 staging 用量、预留、待清理和配额。

本机全局快照增加：

- Worker 进程注册数、存活进程数与续租延迟；
- 期望执行槽、存活执行槽与占用执行槽；
- 活动、过期和接管租约；
- 近期成功率、允许列表失败码分布、阶段耗时和吞吐；
- 全局 staging/reservation/cleanup 字节；
- SQLite busy、接管和 fencing 拒绝计数。

所有时间聚合使用可注入时钟，测试不依赖真实等待。

### 7.2 健康等级

- `healthy`：SQLite 可读写、期望执行槽均有正常心跳、无过期未接管租约；
- `degraded`：存活执行槽不足、30 秒未续租、清理积压或队列异常，但数据边界仍安全；
- `unhealthy`：SQLite 不可用、external 模式无可用 Worker、超过 90 秒仍无法接管，
  或配额账本与源状态不一致。

普通文档失败或存在可重试失败任务不会导致全局不健康。

健康谓词使用可注入 UTC `now`，稳定原因码定义为：

- `IMPORT_WORKER_STALE`：Worker 最后心跳年龄大于等于 30 秒；
- `IMPORT_WORKER_CAPACITY_LOW`：存活执行槽大于 0 但小于 `IMPORT_WORKER_COUNT`；
- `IMPORT_QUEUE_DISPATCH_DELAY`：存在符合 claim 条件、没有用户租约阻塞的任务，在有空闲
  存活槽时仍等待至少 180 秒；
- `IMPORT_CLEANUP_BACKLOG`：source 或 reservation 的 `cleanup_pending` 持续至少 300 秒；
- `IMPORT_DISK_LOW`：数据卷可用空间小于配置的 1 GiB；
- `IMPORT_NO_LIVE_WORKER`：external 模式存活执行槽为 0；
- `IMPORT_LEASE_RECOVERY_BLOCKED`：任务租约已到期，且到期后 30 秒仍未接管或确认终态；
- `IMPORT_PENDING_MUTATION_EXPIRED`：用户 pending data version 在租约到期后 30 秒仍未恢复；
- `IMPORT_LEDGER_MISMATCH`：最近一次深度审计发现不安全路径、未登记受控文件，或账本
  字节与安全 `stat()` 不一致。

前五项产生 `degraded`；后四项和 SQLite 读写失败产生 `unhealthy`。`stuck --json` 把
30 秒无心跳但租约未到期的任务列为 `suspected`，租约到期列为 `expired`，到期后 30 秒
仍未处理列为 `blocked`。维护循环每 300 秒执行一次只读深度账本审计；`audit --json`
可以显式执行同一审计。

### 7.3 CLI 与部署健康检查

提供本机模块命令，至少支持：

- `health --json`
- `metrics --json`
- `stuck --json`
- `quota --json`
- `audit --json`
- `cleanup --dry-run --json`
- `cleanup --execute --json`

命令行输出具有版本字段和稳定原因码。`health --json` 对 healthy/degraded 返回 0，对
unhealthy 返回 1；参数或内部命令错误返回 2。其他查询成功返回 0，执行失败返回非零。
不得输出原始文件名、用户 ID、绝对路径或错误原文。部署健康检查组合验证 Gradio HTTP、
SQLite 控制面和配置模式要求的 Worker 心跳，只在 `unhealthy` 时使容器健康检查失败。

### 7.4 结构化日志

稳定事件至少覆盖：

- batch accepted/rejected；
- reservation created/released/expired；
- task claimed/retry scheduled/succeeded/failed/paused/cancelled/resumed；
- lease renewed/expired/taken over/fenced；
- staging cleanup pending/completed；
- Worker started/stopping/stopped/unhealthy。

字段仅允许事件名、opaque batch/task/worker ID、不可逆用户引用、状态、阶段、允许列表
错误码、计数与时长。禁止原始文件名、路径、用户 ID、错误摘要、凭据和原始 traceback。
可观测性失败只降级记录，不得改变任务结果。

## 8. 错误处理与安全

- 所有 Service 操作先认证，再解析空或非空资源 ID；
- Repository 查询和更新同时按当前用户与资源 ID 过滤；
- 普通 UI 不提供跨用户聚合；
- 全局 CLI 是本机接口，不创建网络监听端口；
- staging 创建、读取和删除使用 UUID 派生路径并拒绝 symlink、junction、reparse point；
- SQLite 使用 WAL、`busy_timeout` 和有界重试；耗尽后保持原状态并返回稳定错误码；
- 任务、源、reservation 和租约更新采用条件写，禁止先读后无条件覆盖；
- 日志与 JSON 运维输出复用错误码允许列表和敏感信息清理规则；
- 任何清理动作都不能跟随链接、删除正式文档或越过当前用户目录；
- external 模式下，如果某个后端不能满足跨进程写安全，启动检查必须明确拒绝该模式，
  不能静默退回不安全执行。

## 9. 配置

配置名称与默认值固定为：

- `IMPORT_WORKER_MODE=embedded`，可设为 `external`；
- `IMPORT_WORKER_COUNT=4`，允许 1–4；
- `IMPORT_HEARTBEAT_INTERVAL_SECONDS=5`；
- `IMPORT_HEALTH_STALE_SECONDS=30`；
- `IMPORT_LEASE_TTL_SECONDS=90`；
- `IMPORT_RECOVERY_GRACE_SECONDS=30`；
- `IMPORT_QUEUE_DISPATCH_DELAY_SECONDS=180`；
- `IMPORT_CLEANUP_STALE_SECONDS=300`；
- `IMPORT_LEDGER_AUDIT_INTERVAL_SECONDS=300`；
- `IMPORT_USER_LOCK_TIMEOUT_SECONDS=30`；
- `IMPORT_SQLITE_BUSY_TIMEOUT_MS=5000`；
- `IMPORT_SQLITE_WRITE_RETRIES=5`；
- `IMPORT_STAGING_USER_QUOTA_BYTES=2147483648`；
- `IMPORT_STAGING_GLOBAL_QUOTA_BYTES=10737418240`；
- `IMPORT_MIN_FREE_BYTES=1073741824`；
- `IMPORT_RETENTION_ENABLED=false`；
- `IMPORT_RETENTION_DAYS=30`，只在保留清理启用时生效；
- `IMPORT_BENCHMARK_TIMEOUT_SECONDS=300`。

external 模式使用 `IMPORT_WORKER_COUNT` 作为部署期望进程数；Worker 启动命令负责创建该
数量的独立子进程，Web 进程只读取该值用于健康判断，不创建执行进程。

embedded 模式中 `IMPORT_WORKER_COUNT` 表示同一进程内的执行线程数；该 Worker 注册的
capacity 等于线程数。external 模式每个子进程 capacity 为 1。健康判断比较所有有效
Worker 注册的总 capacity 与期望执行槽，不能把一个四线程 embedded 进程误报为只存活
一个 Worker。

所有数值配置必须验证为正数，并验证阈值关系为续租间隔小于不健康阈值、不健康阈值
小于租约过期阈值。非法配置应在启动时失败并给出无敏感信息的明确原因。

## 10. 实施顺序

阶段必须串行进入：

1. 只读可观测性基线；
2. 健康、心跳与卡死治理；
3. 配额与失败源治理；
4. embedded 单进程容量基线；
5. 协作式取消、暂停与恢复；
6. 单机 external 多进程 Worker；
7. 最终容量、部署、文档和集成验收。

可在同一阶段内部并行编写互不重叠的测试或文档，但不能在阶段门禁完成前合入下一阶段
生产代码。

## 11. 验收与零错误门禁

每阶段必须完成：

1. 新增专项测试全部通过；
2. 数据库旧版本升级、迁移失败回滚和重启恢复通过；
3. 用户隔离、路径安全、错误脱敏与现有导入状态机回归通过；
4. `compileall` 和 `git diff --check` 通过；
5. 执行固定命令 `.\venv\Scripts\python.exe -m pytest -q`，退出码为 0；
6. 独立规格合规与代码质量审查，Critical/Important 为零；
7. 独立提交并记录阶段报告后才能开始下一阶段。

现有明确依赖真实 Qdrant/Neo4j 的测试可以按既有契约 skip；所有本设计新增验收必须使用
离线确定性后端，不得通过 skip 绕过。

新增验收必须包含以下确定性故障矩阵：

- Web 已缓存 version N，external Worker 提交 N+1 后，Web 再写 History/RAG/Memory 不得
  丢失 Worker 数据；JSON、Qdrant 和 Neo4j capability 分别验证；
- Web 与 Worker 交叉竞争同一用户三层锁，无死锁、超时后无遗留租约；持锁进程死亡后
  内核锁释放，旧 token 不能释放新租约；
- 在 reservation 的复制、rename、任务/source 事务前后逐点崩溃，`.part` 和 final 文件
  始终被账本计费或安全删除；
- 在 source 进入 `cleanup_pending`、unlink 和 `deleted` 更新前后逐点崩溃，重启恢复幂等；
- pause/cancel/committing 三方竞态、pause 后 cancel、重复请求、补偿失败与补偿恢复遵守
  完整控制矩阵；
- 伪造跨用户 task/source/attempt/compensation 关联被数据库复合外键拒绝；
- 所有健康原因码使用注入时钟验证 30/90/180/300 秒边界，不依赖真实 sleep。

### 11.1 容量门禁

在阶段 4 和阶段 7 各运行一次：

- 100 个用户；
- 1000 个离线假后端任务；
- 阶段 4 使用四线程 embedded；
- 阶段 7 使用四个 external Worker 进程；
- 零任务丢失；
- 零重复业务提交；
- 零跨用户数据；
- 所有任务在 `IMPORT_BENCHMARK_TIMEOUT_SECONDS=300` 秒内进入确定终态。

阶段 4 只用可注入时钟和假租约模拟恢复，不强杀 embedded 进程。阶段 7 额外强杀
external Worker，验证只在租约到期且内核锁释放后接管，并验证旧 claim token 写入被
fencing。

另各运行 30 分钟 soak，记录环境、吞吐、P95 队列等待、SQLite busy 次数、接管次数和
进程内存变化。正确性是硬门禁；机器相关性能数字保存为可比较基线，不设置未经硬件约定
的绝对失败阈值。soak 工具本身必须有超时、正常关闭和孤儿进程检查。

### 11.2 最终运行验收

- 使用 `.\venv\Scripts\python.exe .\ui\gradio_app.py` 启动 Gradio；
- embedded 模式证明正常处理并能有界关闭；
- external 模式证明 Web 不启动隐式 Worker，独立 Worker 命令可以处理任务；
- 启动 1–4 个 Worker，逐阶段故障注入后无重复、无串用户和无永久 `running`；
- 备份恢复后任务、租约、配额和源生命周期一致；
- README、部署配置、健康检查和运维命令与实际行为一致。

## 12. 完成定义

只有以下条件同时满足，整个阶段才算完成：

- 七个实施阶段全部通过各自门禁；
- exact full suite 最终退出码为 0；
- 两次容量和 soak 记录完整；
- 没有未关闭的 Critical/Important 审查发现；
- 没有孤儿 Worker、reservation、`.part` 文件或无法解释的 staging 占用；
- 当前用户数据隔离、文档作用域、路径安全、脱敏和现有导入能力无回归；
- 所有代码、迁移、测试、README 与部署说明已提交，且未包含运行数据或敏感信息。
