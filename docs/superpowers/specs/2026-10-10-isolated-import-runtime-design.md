# 隔离环境认证导入与独立 Worker 接入设计

- 状态：用户已批准设计（“可以采用”）；实施与验收尚未完成。
- 日期：2026-10-10，Asia/Shanghai。
- 当前交付基线：`3b365107814a5616867b6f2121e77db7c1e0cc12`；已验收发布/恢复源码：`5d0e66a88d3f9d39231c757f3b2d07768fe51cb9`。
- 父任务：`2026-09-24-distributed-cutover-vertical-slice`。本阶段是隔离接入里程碑，不能代替完整业务迁移、镜像回退或生产验收。

## 1. 目标与范围

让真实独立 API 和 Worker 进程，通过 PostgreSQL 会话、S3 不可变上传、受信向量身份以及已验收发布/恢复服务，完成认证导入、持久任务查询和提交结果读回。用实际进程死亡、替代进程和旧 Worker 恢复执行，验证数据库权威和故障处理。

第一阶段仅服务已具备可证明 History、Memory、RAG、episode 基线的隔离账号。公开注册暂不开放。验收可在全新一次性 schema/bucket/collection 中预配置两个账号和与真实解析/嵌入引擎一致的空基线；这些是验收数据，不是生产新用户初始化实现，更不证明历史向量迁移。

不改普通 `ApplicationServices.create()` 的 distributed 拒绝，不接 QA、笔记、学习、报告、删除、Gradio 或本地用户 runtime，不开放生产写入。不启动或修改受保护源 Qdrant，不触及保留的迁移 schema/bucket/generation。历史文本和元数据保留，向量回忆仍待来源证明。

## 2. 方案比较与选择

| 方案 | 收益 | 代价与范围 |
|---|---|---|
| 推荐：独立隔离 auth/import/readback composition | 可验证真实跨进程调用链；不会启用未迁移业务；不必改已验收发布协议 | 首期要求预先证明账号基线，不能宣称新注册用户或完整产品可用 |
| 同期实现生产新用户可信空基线 | 可覆盖注册后首次导入 | 新增幂等初始化、并发与崩溃恢复、索引身份及双基线发布设计，须单独实施与审查 |
| 一次开启完整 distributed bootstrap | 一次覆盖所有产品入口 | QA/删除/投影等仍有本地权威与 Worker 依赖，会显著扩大范围；当前不采用 |

选择第一项作为先行里程碑。后续依次实现受信新用户初始化、其余业务接入、保留数据迁移与两个不同兼容镜像的回退验证；父任务完整验收继续保持开放。

## 3. 已核对的仓库事实

| 实际位置 | 当前能力与接入约束 |
|---|---|
| `app/bootstrap.py:81` / `:91` / `:216` | 普通创建流程拒绝 distributed，随后构造 SQLite/session/local Worker；start 会启动所有本地 Worker，不能注入部分 PG 服务后复用 |
| `api/app.py:52` / `:88` | 当前 factory 注册全部业务路由，不能作为本阶段受限 API 的默认工厂 |
| `api/routes/auth.py` / `api/dependencies.py` | 已有 cookie、CSRF、login/session/logout 契约；其 SessionRegistry 目前带本地 runtime，需 PG facade |
| `app/postgres_auth.py` / `app/postgres_sessions.py` | PG 用户认证、持久 session、CSRF、跨进程 logout 与锁后数据库时间已经存在 |
| `app/import_service.py:118` / `app/import_worker.py:126` | 本地 staging/runtime/assistant 和旧终态提交路径，不能用于本阶段 |
| `app/postgres_import_artifacts.py:38` / `:100` | 已有不可变上传后 SQL batch/source 原子发布；源文件按 pinned version/hash/size 读取 |
| `app/import_repository.py:404` / `app/import_persistence.py` | PG task repository 可用继承的用户范围读/控制方法；旧 Worker transition 明确禁用 |
| `app/postgres_import_leases.py:90` / `:189` | 同时 claim 与 heartbeat task/user authority；progress、failure、expiry 需原租约 |
| `app/import_document_preparation.py:78` | `prepare_import_document` 真实内存解析与嵌入，无 claim/object read/vector write；要求 profile 与受信 scope 一致 |
| `app/import_memory_publication.py:640` / `:922` / `:988` | 原 C issuance 建立 intent/gate/reservations/queue；要求已有严格双基线；C 自己负责终态完成 |
| `app/import_publication_worker_adapter.py:35` | 默认 OFF、原 service/live/database 身份、最多执行一次，Unknown 保持 reconciliation |
| `app/import_publication_recovery.py:265` / `:755` | 独立 recovery claim，证明成功或精确撤销/保持，不能授权重新执行旧 publication |

这些是静态读取和独立 Sol/Astra 设计核对；本阶段尚未运行新增测试、启动进程或修改产品代码。

## 4. composition 与进程生命周期

新增独立隔离 composition、受限 FastAPI factory 和 Worker CLI，明确分开 `api` 与 `worker` 角色。默认禁用，要求显式启用隔离功能及受信配置；普通 local 和普通 distributed 行为不变。

配置需完整给出隔离 PostgreSQL schema、版本化 S3 bucket、目标 Qdrant collection、允许的隔离 tenant 和精确 index/profile identity。数据库/schema/隔离身份不合法时拒绝启动。普通导入 admission 另外校验对象/向量服务、profile、已发布 head/receipt/History witness/Memory 一致性；缺失或矛盾时拒绝该账号的普通导入，不创建缺失基线，不改变现有索引，不降级为 SQLite、JSON、文件锁或简单嵌入。凭据只通过现有安全配置进入，不写进规格或公开日志。

ordinary admission 的基线或 provider 拒绝不能阻止受信 schema 中已有 queue 的恢复及状态查询。Worker 在数据库/schema 身份验证成功后仍能运行严格 recovery；不把不完整 terminal/head/witness 作为关闭恢复入口的理由。恢复服务按原协议输出 ack/abandon/manual_hold，不依赖新的 embedding 或外部写入，健康状态分别报告 ordinary-ready 与 recovery-ready。

tenant allowlist 同时约束 HTTP 和数据库任务选择。隔离 schema 必须专用；普通与 recovery 的 `claim_next` 以及普通 `recover_expired` 新增可选 keyword-only `allowed_user_ids: frozenset[str] | None = None`，实际候选 SQL 在锁定/认领/expiry修改前就限制 tenant，不能选中后才过滤。隔离调用强制传非空的配置集合；`None` 保留既有库调用行为，不弱化任何原 lease/gate 检查。三处最小边界改动必须在专属实施 packet 和 Astra 审查中锁定，过滤集合内的公平/锁序保持原协议。配置外账户、任务和 queue 永远不能被本阶段 Worker 认领或由 expiry scanner 修改；不能仅靠启动时查询规避并发插入竞态。

API 角色只拥有会话、上传与读回服务，零嵌入 Worker/恢复线程。Worker 角色拥有独立数据库 pool 和 vector/object clients，运行有限普通导入循环与公平恢复循环，不提供用户 HTTP 路由。停止接受新任务、停止续租并有界等待；不能把仍在执行的后台线程误记为退出成功。恢复循环与普通任务循环使用不同 authority，任何一方停止不改变已持久化证据。

同一受信配置可启动两个 API 和替代 Worker；不能依赖 API 内存或共享本地业务目录。并发与队列调度复用已验收原语。

## 5. API 边界

复用现有 schema、cookie/CSRF 语义和 `/api/v1` 路径。仅挂载 login、session、logout，以及导入提交、批次/任务查询和必要的已发布文档只读查询。无公开 register、QA、notes、learning、reports、delete、search 或 Gradio 路由；未挂载的功能不能通过本地服务回退。

PG session facade 提供现有路由需要的 `login/get_session/validate_csrf/logout`。全部 tenant 身份来自服务端 session，不接受 body、query、上传文件或 Worker payload 声称的 user_id。账号须处于 active 且受信范围；CSRF 在读取上传内容或写入存储前完成。

导入 facade 使用 `PostgresImportArtifactService.submit_uploads(user_id, uploads)`，通过 PG repository 获取 list/get batch/task。首阶段不挂载 cancel/retry 写控制路由；未开放的 register/control/其他业务路径必须实际返回404/405，不能整挂现有 router 意外开放。未来开放控制操作时仍需原 gate/lease 和独立验收。

错误映射覆盖 submit：通过权威读取确认当前用户 unresolved gate 的业务拒绝，返回 `409 needs_reconciliation`、`retryable=false`；不能沿用当前 local route 的 `500 import_stage_failed, retryable=true`。不能把全部 `PublicationEvidenceError` 视为 gate：它也表示非活动事务、非 RC 或证据异常。权限/allowlist 拒绝走稳定403；缺失基线、依赖不可用或权威配置/证据不一致走稳定503 failclosed，不暗示普通上传重试可解除未知结果。公开错误不包含 SQL、源文本或凭据。

公开状态来自 PG 原始持久记录，不来自 Worker 内存。未知结果保持已有 reconciliation 语义；不得将超时改为成功或可随意重试。读回按 tenant 限定 History、document reference/witness、pinned S3 与已发布 generation；原始 capability、SQL tuple、凭据和内部 proof 不加入产品 API。

## 6. 普通 Worker 与唯一发布

调用链：

1. `PostgresImportLeaseRepository.claim_next(worker_id, allowed_user_ids=trusted_ids)` 获得原 `ImportAttempt`，包含 task/user lease；该过滤参数是本阶段拟新增接口，尚未实现。
2. 读取 SQL pinned source 并通过 `read_source_bytes(user_id, task_id)` 校验 S3 version/hash/size。
3. 从受信配置/PG authority 得到 tenant 的 RAG 与 episode scope，使用匹配 profile 的真实 embedding runtime 调用 `prepare_import_document(...)`；事件文本和事件嵌入规则保持 C 现有契约。
4. 使用原 service 调 `_issue_live_publication(rag_scope, attempt, points, event_vector=..., event_profile=...)`；只把实际返回的原 handle 交给原 adapter。
5. `ImportPublicationWorkerAdapter(..., enabled=True).run_once(live)` 执行一次。外层不得再调 complete/mark_succeeded，也不传普通 publication callback，不自行重演对象上传、seal 或 terminal work。
6. 停止续租，释放进程内资源；held 后不做普通 fail/cancel/release/retry。成功由 PG 与独立 proof/readback 验证。

续租使用 `PostgresImportLeaseRepository.heartbeat` 同时续 task/user；保留原 issuance 身份，不用续租返回的新 task snapshot 替换原 live handle。隔离首期默认 lease60秒、heartbeat10秒、单 attempt 预算300秒、provider 单次超时30秒、停止等待10秒；配置需满足 heartbeat小于lease的三分之一、provider超时不超过attempt预算，provider重试有限。超预算或失去租约停止后续动作和续租，不能假装已终止仍在运行的 provider/thread。迟到返回仍受原 authority 拒写；如果停止等待超过预算，报告退出失败而非虚构干净退出。每个不可撤销步骤仍由现有 authority 原语做 fresh-clock/fence 检查。

intent 前明确的解析/配置失败可以通过原普通 lease failure 路径收尾。issuance 抛错或数据库结果模糊时不能推断 intent 没提交，不能直接普通失败；停止续租，通过权威证据与到期恢复判定。成功与 heartbeat lease-lost 竞态只停止 heartbeat 并重新查 proof，不能降格已经提交的成功。

事件向量必须由与 episode profile 完全匹配的引擎计算，不能使用现有测试固定点或 4D 常量。一次性验收可显式使用真实 SimpleEmbedding 的匹配新隔离 profile；该结果不证明保留 BGE-M3 profile 的网络服务可用或历史召回等价。既有 BGE-M3 身份不得自动替换为 SimpleEmbedding。

## 7. 恢复与中断

普通 expiry scanner 使用本阶段增加过滤后的 `recover_expired(limit=..., allowed_user_ids=trusted_ids)`。无 evidence 的原租约可以按已有规则恢复；有 evidence 必须保留 gate、reservations 与 queue，不能交回普通旧 attempt。

独立恢复循环调用 `claim_next(worker_id, allowed_user_ids=trusted_ids) → prove_or_hold`，必要时仅通过 recovery `renew` 续其自己的 claim。短队列 capture 先提交，再等 user authority；保留 user-first 锁序和锁后数据库时钟。

Unknown/held 立即停止普通续租，让两条普通 lease 有限到期。证明 terminal 成功只执行 recovery ack；证明不足按现有协议保持或在合格 preterminal 且双 lease 到期后同时永久撤销两个 UUID，再安排全新的 attempt。瞬时失败的退避/上限/manual_hold 使用已有协议，不另加无限重试循环。进程死在 issuance 返回前，也从原子持久队列发现，不重建 Python capability。

## 8. 基线、数据隔离与后续范围

首期不增加生产 fresh-user initializer，不从 missing 推断 empty。未证明基线的账号明确拒绝导入。隔离验收准备须在新的唯一 schema/bucket/collection 中，使用已有真实底层服务建立一致空基线，绑定 profile、双 generation/receipt、History witness 与 Memory；准备报告明确是验收 fixture。

该 fixture 不能导入产品 runtime，不能指向受保护源或保留 target，不能据此宣称历史用户初始化已实现。后续生产 initializer 应作为独立串行任务，要求真正新用户检查、原子/可重入/并发/崩溃语义及完整独立审查。

保留数据迁移必须继续遵循同一停写边界和 provenance 口径；完整产品业务及两个不同兼容镜像回退仍按父规格验收。任何生产迁移/切换仍需要后续单独授权。

## 9. 验收条件

| 验收 | 必须保存的实际观察 |
|---|---|
| 两 API 共享认证 | 同一 cookie 在第二进程可用；CSRF 拒绝；logout 跨进程失效；另一账号不能读 task/document |
| 实际上传与发布 | PDF/text 真实解析/嵌入，PG/S3/Qdrant 实际调用；task/source/document/history/episode 及双 receipt/proof 一致 |
| API 死亡 | 上传持久入库后终止提交 API，独立 Worker 完成，替代 API 可以读回 |
| Worker 真重启 | 终止旧 PID，启动新 PID，由 DB 状态接管；记录源码、进程和依赖身份，不用仅重建对象冒充重启 |
| 中断窗口 | claim 后 intent 前、intent commit 后返回前、document put 后、两次 seal 后、terminal commit 后响应前分别验证 |
| Unknown 与恢复 | held 后续租停止；恢复暂停时保持；恢复后 ack 或双 UUID 撤销+fresh attempt，没有旧 callback replay |
| 旧 Worker | 暂停到租约接管，恢复旧 PID 后拒写；迟到 vector bytes 不成为可见 head，旧 UUID 永久失效 |
| 时间/锁竞争 | heartbeat 与 terminal/expiry/recovery 的锁等待竞争；锁后 fresh DB clock；同用户串行、另一用户可推进、公平队列 |
| 默认关闭 | 普通 local 回归通过；普通 distributed bootstrap 仍拒绝；禁用隔离配置失败；无本地 business fallback |
| 证据完整性 | final frozen source、原 unfiltered commands、actual exit/log/XML/resource IDs、每个独立审查和提交字节绑定 |

测试由 Sol High 的唯一 pytest lane 执行；Astra High 独立审查租约、旧 Worker、跨存储与回退关键行为；Luna High 负责机械证据与日志。具体原命令、文件所有权和每个 packet 的接口在设计复核后写进实施计划及仓库自包含 task packets；本草案不把未创建的测试文件当成可运行证据。

## 10. 实施分解与完成边界

串行里程碑：A）受信隔离配置/PG会话/API导入 facade；B）唯一发布 Worker/有界续租/恢复循环；C）受限 bootstrap/实际多进程故障验收；D）独立 Spec/Quality/Delivery、最终集成和分支交付。所有涉及共享接口的任务不可并行改同一文件。

预期新边界分别位于 `app/isolated_import_runtime.py`、`app/distributed_import_sessions.py`、`app/distributed_import_service.py`、`app/distributed_import_worker.py`、`api/isolated_import_app.py` 和 `scripts/run_isolated_import_worker.py`；最终文件名与精确方法在计划审查锁定。既有普通 bootstrap/local service 不承担此首期 composition。没有 schema 变更需求时不新增 migration。

首期完成只表示已证明基线账号的隔离认证导入及恢复链通过，不关闭父任务“完整业务迁移与回退”。用户已复核批准本规格；接下来按自包含任务包串行实施与独立验收，不把设计批准视为实现或运行验收。
