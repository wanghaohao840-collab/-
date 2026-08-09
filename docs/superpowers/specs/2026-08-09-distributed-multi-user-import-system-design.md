# 分布式多用户导入系统设计

**日期：** 2026-08-09
**状态：** 已确认
**范围：** 将现有单进程、SQLite、本地文件驱动的多用户批量导入模块升级为可由多服务器、多容器共同消费任务的生产架构，同时保留单机开发模式。

## 1. 背景与目标

当前系统通过 SQLite 保存用户、会话、导入批次和任务，通过用户目录保存暂存文件、正式文档、History、Memory 和报告，并由应用进程内的 `ImportWorkerPool` 执行后台导入。这套实现已具备持久任务、用户隔离、同用户串行、跨用户并行、自动重试、启动恢复和安全暂存，但其协调与文件边界只适用于单进程或共享本地数据目录。

本阶段目标是：

- 支持多个 API 实例和多个独立 Worker 实例跨主机协作。
- 保证同一用户最多一个导入任务正在执行，不同用户可以并行。
- 使用租约、心跳和 fencing token 恢复失联 Worker 的任务并拒绝陈旧写入。
- 将权威结构化状态迁至 PostgreSQL，将大型文件迁至 S3 兼容对象存储。
- 保持至少一次投递下的幂等执行、租户隔离、重试和恢复语义。
- 增加任务取消、配额、背压、可观测性、审计、负载及故障验证。
- 保留 `local` 模式，继续支持 SQLite、本地文件和进程内 Worker。

## 2. 非目标

- 不重写 RAG、GraphRAG、问答、报告生成或学习业务逻辑。
- 不移除 Qdrant 或 Neo4j，也不改变 `user_id`、`document_id` 和 RAG namespace 隔离规则。
- 不引入 Redis、Celery、Kafka 或第二套权威任务状态。
- 不在应用事务中实现 PostgreSQL、对象存储、Qdrant 和 Neo4j 的全局两阶段提交。
- 不进行本地模式与分布式模式的实时双写。
- 不在生产 Kubernetes 清单中自建 PostgreSQL、S3、Qdrant 或 Neo4j；生产优先连接外部托管服务。

## 3. 总体架构

```text
Web / API replicas
    |-- PostgreSQL: users, sessions, batches, tasks, leases,
    |               attempts, History/Memory coordination,
    |               report metadata, audit and outbox
    |-- S3/MinIO: staged imports, formal documents and reports

Independent Worker replicas
    |-- PostgreSQL: claim, heartbeat, progress and terminal state
    |-- S3/MinIO: download staged input and publish artifacts
    |-- Qdrant: vector persistence
    `-- Neo4j: optional graph persistence
```

API 在 `distributed` 模式下不得启动进程内 Worker。独立 Worker 使用同一业务服务和 Repository/Storage 协议，但拥有单独的启动入口、健康状态和优雅停机流程。本地磁盘只保存可丢弃的下载、解析和生成临时文件。

## 4. 运行模式

### 4.1 `local`

- SQLite 保存现有关系状态。
- 本地用户目录保存暂存文件、文档、History、Memory 和报告。
- 应用可继续启动进程内 Worker。
- 用于开发、测试和轻量单机部署。

### 4.2 `distributed`

- PostgreSQL 是唯一结构化事实源。
- S3 兼容对象存储是唯一持久文件源。
- Worker 使用独立进程或容器运行。
- 缺少数据库、对象存储、租约或 Worker 必要配置时必须启动失败，不能静默回退到 `local`。
- 两种模式共享业务协议和验收契约，但允许使用各自适合的 SQL 领取实现。

## 5. PostgreSQL 数据模型

现有用户、会话、批次和任务字段迁移到 PostgreSQL，并保留 UUID、用户作用域、状态约束和批次汇总语义。导入任务新增：

- `claimed_by text null`
- `lease_token uuid null`
- `lease_version bigint not null default 0`
- `heartbeat_at timestamptz null`
- `lease_expires_at timestamptz null`
- `cancellation_requested_at timestamptz null`
- `cancelled_at timestamptz null`
- `priority smallint not null default 0`
- `content_sha256 text not null`
- `object_key text not null`

新增 `import_task_attempts`，按每次任务领取保存：

- 任务、用户、Worker 和租约版本。
- 开始、心跳、结束时间。
- 结束原因、错误代码和安全错误摘要。
- 是否由租约恢复、自动重试、手动重试或取消结束。

新增 `import_workers`，保存实例 ID、版本、能力、启动时间、最后心跳、并发容量及 `running`、`draining`、`offline` 状态。

新增 transactional outbox、审计事件、用户队列调度状态和配额使用记录。所有租约时间比较以 PostgreSQL 服务器时间为准，避免依赖 Worker 主机时钟。

## 6. 领取、公平调度与租约

Worker 在单个 PostgreSQL 事务中：

1. 回收已过期且可恢复的任务。
2. 排除正在执行任务的用户。
3. 按“最久未获得执行机会的用户、该用户最早可执行任务”选择候选项。
4. 使用 `FOR UPDATE SKIP LOCKED` 跳过其他 Worker 已锁定的候选行。
5. 将任务更新为 `running`，生成新的 `lease_token`，递增 `lease_version`，写入 Worker 和到期时间。
6. 创建 attempt 记录并提交。

数据库约束与领取事务共同保证同一用户最多一个执行中任务。默认租约 60 秒、心跳 15 秒，均可配置，但到期时间必须至少覆盖两个心跳周期。

所有进度、重试、成功、失败、取消和租约释放写入都必须匹配：

```text
user_id + task_id + claimed_by + lease_token + lease_version + required_status
```

不匹配表示 Worker 已失去所有权，调用方必须停止执行且不得提交终态。Worker 连续无法续约时设置本地停止信号，并在下一个安全检查点终止。

## 7. 状态机、重试与取消

任务状态为：

- `queued`
- `running`
- `retry_wait`
- `cancelling`
- `succeeded`
- `failed`
- `cancelled`

合法转换：

```text
queued/retry_wait -> running
running -> succeeded/retry_wait/failed/cancelling
queued/retry_wait -> cancelled
cancelling -> cancelled
expired running -> queued
expired cancelling -> cancelled
failed -> queued               (manual retry)
```

租约过期恢复不消耗自动重试次数。真正重新领取时才增加总尝试次数。排队或等待重试的任务可立即取消；执行中的任务先进入 `cancelling`，Worker 在解析、切块、嵌入、持久化和提交等安全检查点协作停止。取消和失败均清理本地临时文件；只有终态提交和对象保留策略确定后才删除暂存对象。

Worker 优雅停机时先注册为 `draining`，停止领取新任务，继续心跳当前任务；在宽限期内完成任务，或在安全检查点释放租约后退出。

## 8. 幂等性与跨存储一致性

系统采用至少一次执行，不声称跨存储 exactly-once。

- 使用固定 `document_id`、`import_task_id`、对象键和内容 SHA-256。
- S3 写入使用确定性键；重复上传验证内容一致后视为成功。
- Qdrant 和 Neo4j 使用现有文档作用域覆盖/upsert，并携带任务及租约版本元数据。
- History 和 Memory 的分布式实现使用版本列或条件更新，拒绝旧租约版本。
- Worker 先完成外部持久化，再以当前租约提交 PostgreSQL 终态。
- 如果终态提交失败，后续执行对相同文档执行幂等覆盖。
- PostgreSQL outbox 驱动对象清理、后续一致性检查和其他异步副作用。
- 定期扫描任务、对象、History、Qdrant 和 Neo4j 状态，生成差异并提供受控修复命令。

## 9. 对象存储

定义 ObjectStore 协议，并提供本地和 S3 两个实现。生产对象键只由规范化 ID 组成，例如：

```text
users/{user_id}/imports/{batch_id}/{task_id}{suffix}
users/{user_id}/documents/{document_id}{suffix}
users/{user_id}/reports/{report_id}.md
```

原始文件名只作为数据库元数据，不参与对象键拼接。提交批次时先上传暂存对象并计算大小与 SHA-256，再在一个 PostgreSQL 事务中创建批次和任务。事务失败时立即尝试删除已上传对象；后台孤儿扫描器兜底清理。

Worker 下载后再次校验租户前缀、扩展名、大小和 SHA-256。下载和解析目录按任务隔离，禁止跟随符号链接，任务完成后递归清理已验证的任务临时目录。

## 10. History、Memory 与报告

分布式模式不能依赖共享 NFS 上的 SQLite 或 JSON 文件。权威 History、Memory 快照、报告元数据迁入 PostgreSQL；大型报告内容存入对象存储。可重建的 RAG 本地缓存只能作为实例缓存，不得成为权威状态。

迁移后的 Repository 必须继续执行用户作用域条件，并保留当前公开服务契约。Qdrant namespace 和 Neo4j 的用户/文档过滤保持不变。

## 11. 配额与背压

服务端强制执行：

- 单文件大小、单批文件数和批次总大小。
- 每用户排队任务数、活动批次数、对象存储用量和每日提交量。
- 全局未完成任务数和系统最大排队容量。
- Worker 实例并发数及集群可用容量。

用户配额耗尽返回 `429` 和 `Retry-After`；全局容量不足返回 `503` 和 `Retry-After`。只有已完整暂存并持久化任务的请求才返回已接收。公平调度以用户为单位轮转，防止高提交量用户长期占用所有 Worker。

## 12. 可观测性、健康与审计

结构化日志包含 `request_id`、`batch_id`、`task_id`、`worker_id` 和 `lease_version`，但不得包含会话令牌、密钥、原始上传路径或未经脱敏的异常。

Prometheus 指标覆盖：

- 各状态任务数和最老任务年龄。
- 领取、完成、失败、重试、取消和租约恢复计数。
- 各阶段耗时、心跳延迟和 Worker 容量。
- PostgreSQL 连接池、对象存储和外部后端错误。

指标标签禁止使用 `user_id`、任务 ID、文件名等高基数或敏感值。API 暴露 `/livez` 和 `/readyz`；Worker 存活和就绪通过实例心跳及依赖检查体现。审计表追加记录操作者、动作、目标、结果和安全摘要。

## 13. 数据迁移与切换

提供显式离线迁移命令，支持 `--dry-run`、断点续传、重复执行、校验报告和失败清单。迁移流程为：

1. 进入维护模式，阻止新写入并停止旧 Worker。
2. 备份 SQLite 和完整用户数据目录。
3. 迁移用户、会话、任务、History、Memory 和报告元数据。
4. 上传暂存文件、正式文档和报告，并校验 SHA-256。
5. 比对用户数、任务数、状态汇总、文件数、总字节数及抽样内容。
6. 切换为 `distributed` 配置，先启动 Worker，再启动 API。
7. 执行认证、提交、领取、完成、查询和取消冒烟测试后开放流量。

旧数据在观察期内只读保留。分布式系统开始接收写入后，不承诺自动无损回退到 SQLite。

## 14. 部署

Docker Compose 增加 PostgreSQL、MinIO 和独立 Worker，用于本地分布式集成与验收。生产提供 Kubernetes 基础清单：

- API Deployment 与 Service。
- Worker Deployment，支持优雅停机和滚动升级。
- readiness/liveness probe。
- ConfigMap/Secret 引用，不提交真实秘密。
- PodDisruptionBudget、资源请求/限制和 Worker HPA 指标接口。

生产清单连接外部 PostgreSQL、S3、Qdrant 和可选 Neo4j，不在集群内默认创建有状态数据服务。

## 15. 实施顺序

1. Repository/ObjectStore 协议、配置校验和本地回归基线。
2. PostgreSQL、Alembic、连接池及用户/会话/任务迁移。
3. S3/MinIO 暂存、文档与报告对象存储。
4. 独立 Worker、原子领取、租约、心跳、fencing及恢复。
5. History/Memory 分布式存储闭环。
6. 取消、draining和安全清理。
7. 配额、公平调度和全局背压。
8. 指标、健康检查、审计和管理查询。
9. 离线迁移、一致性扫描与修复命令。
10. 多实例集成、负载、故障注入和完整回归。
11. 部署、备份、恢复、扩缩容和故障处理文档。

## 16. 测试与验收

必须覆盖：

- SQLite/PostgreSQL Repository 契约测试。
- Local/S3 ObjectStore 契约测试。
- PostgreSQL、MinIO、Qdrant 容器化集成测试。
- 多 API、多 Worker 并发领取和公平调度。
- 同用户串行、跨用户并行和用户数据不可见性。
- Worker 强制终止、心跳中断、数据库短暂不可用、对象存储超时和重复投递。
- 任务取消、draining、自动/手动重试及租约恢复。
- 配额和背压响应。
- 迁移 dry-run、断点续传、重复执行和校验失败。
- 至少 100 个用户的负载场景。
- 现有认证、RAG、Memory、报告、GraphRAG、批量导入和部署回归。

最终验收要求：

- 不发生跨用户读取或写入。
- 同一用户任意时刻最多一个导入任务执行。
- Worker 终止后任务在租约窗口及扫描间隔内恢复。
- 陈旧 Worker 不能提交进度或终态。
- 重复执行不产生重复文档或冲突终态。
- 背压期间已接收任务保持持久并最终可处理。
- `local` 模式现有行为保持兼容。
- `distributed` 模式在缺少必要配置时明确失败。

## 17. 风险与控制

- **跨存储部分成功：** 使用幂等键、outbox、版本标识及一致性修复。
- **失联 Worker 继续执行：** 短租约、心跳、阶段检查和 fencing 条件写入。
- **共享状态迁移范围扩大：** 按 Repository 边界逐步迁移，每阶段保持可验证。
- **双后端行为漂移：** 使用共享契约测试，不以复制业务逻辑实现差异。
- **对象泄漏：** 即时补偿、孤儿扫描、保留期和校验报告共同控制。
- **高基数监控：** 用户和任务维度只进入受控日志/审计，不进入指标标签。
- **上线不可逆：** 维护窗口、完整备份、dry-run、观察期和明确切换门槛。
