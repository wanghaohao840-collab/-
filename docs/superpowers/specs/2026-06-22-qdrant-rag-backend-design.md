# JSON/Qdrant 可切换 RAG 后端设计

## 目标

为现有 RAG 流程引入连接独立服务的 Qdrant 后端，同时保留现有 JSON 后端和重启恢复能力。后端由配置显式选择；选择 Qdrant 后，任何配置或连接错误都应明确失败，不得静默降级到 JSON。

## 配置

支持以下环境变量：

```env
RAG_BACKEND=json
QDRANT_URL=http://localhost:6333
QDRANT_API_KEY=
QDRANT_COLLECTION=doc_learning_vectors
```

- `RAG_BACKEND` 仅接受 `json` 或 `qdrant`，默认值为 `json`。
- `RAG_BACKEND=qdrant` 时，`QDRANT_URL` 必填。
- `QDRANT_API_KEY` 可选，用于受保护的独立 Qdrant 服务。
- `QDRANT_COLLECTION` 可选，默认沿用调用方传入的 collection 名称；环境变量用于部署级覆盖。
- 无效后端、缺少 URL、连接失败或 Qdrant 请求失败均明确报错，不自动回退。

## 架构

RAG Tool 及其上层 Assistant、UI 保持现有调用方式。RAG 层建立统一后端契约，由工厂根据配置创建 JSON 或 Qdrant 实现。

两种实现共同支持：

- `add_text`
- `search`
- `stats`
- `delete_document`
- `clear`
- `get_document_summary_context`

现有文本切块、嵌入、检索结果格式及按页去重规则保持一致。JSON 实现保留当前持久化格式和启动恢复行为。Qdrant 实现通过官方 Python 客户端连接独立服务，不提供本地嵌入模式。

依赖方向继续保持为 `UI -> Assistant -> Tool -> RAG/Storage`，存储层不反向依赖 Tool。

## Qdrant collection 与 payload

Qdrant collection 使用余弦距离，向量维度与当前 embedder 的输出维度一致。后端在首次使用时创建不存在的 collection；已存在 collection 的向量维度或距离配置不兼容时明确失败，避免破坏已有数据。

每个 point 的 payload 至少包含：

- `content`
- `document_id`
- `rag_namespace`
- `chunk_index`
- 完整且可序列化的 `metadata`

为便于过滤和返回兼容结果，来源、页码等 metadata 继续完整保留。point ID 必须满足 Qdrant ID 约束，并在同一 chunk 重建时保持稳定。

## 数据隔离与操作语义

`rag_namespace` 是 collection 内所有操作的强制过滤条件。`document_id` 是文档范围操作的附加过滤条件。

- 添加文档：沿用现有切块和嵌入流程，将向量和 payload 批量写入 Qdrant。
- 覆盖导入：仅删除相同 `rag_namespace + document_id` 的旧 points，再写入新 chunks。
- 检索：始终过滤当前 `rag_namespace`；传入 `document_id` 时叠加文档过滤，随后沿用现有页码去重规则。
- 全文总结：滚动读取目标 namespace 和文档的 chunks，按 `chunk_index` 或页码形成稳定顺序，再沿用现有跨位置抽样策略。
- 删除文档：仅删除当前 namespace 下指定 `document_id` 的 points。
- 清空知识库：仅删除当前 namespace 的 points，不删除同 collection 中其他 namespace 的数据，也不删除 collection 本身。
- 统计：只统计当前 namespace，返回文档数、chunk 数、向量维度、collection 和当前后端信息。

这些规则保证多个用户或用途共享 collection 时不会发生跨 namespace 读取或误删，并继续保证 `document_id` 隔离。

## 错误处理

RAG 后端层提供明确的配置错误和后端操作错误。Qdrant 客户端异常应保留操作上下文并转换为稳定的项目异常信息，不暴露 API key。现有 RAG Tool 继续负责将异常转换为用户可读结果。

选择 Qdrant 即代表要求使用 Qdrant。服务不可用时，初始化或首次操作必须失败，不得创建 JSON 缓存作为替代品。

## 兼容性

- `RAG_BACKEND` 未设置时继续使用 JSON，现有示例和 UI 无需 Qdrant 即可运行。
- RAG Tool 对外 action、参数和返回文本保持兼容。
- JSON 缓存结构和恢复逻辑不做迁移或重写。
- Qdrant 与 JSON 不进行双写，也不自动迁移已有 JSON 数据。
- 当前后端选择在进程启动/对象创建时确定，不支持运行中的热切换。

## 测试与验收

单元测试使用可注入的 Qdrant 客户端 fake 或 mock，不要求测试环境启动真实服务。至少覆盖：

- 默认选择 JSON、显式选择 Qdrant 及无效配置失败；
- Qdrant collection 创建和不兼容 collection 拒绝；
- 写入 payload 保留内容、来源、页码、namespace 和 `document_id`；
- 检索不会跨 namespace 或跨指定文档；
- 覆盖导入只替换目标文档；
- 删除文档只删除目标范围；
- `clear` 只清空当前 namespace；
- 全文总结读取和抽样顺序稳定；
- JSON 持久化、检索、删除和重启恢复回归测试。

提供连接独立 Qdrant 服务的集成验证说明，用于人工验证创建 collection、导入、检索、统计、定向删除和清空。自动化测试不依赖外部服务。

验收成功标准：同一套 RAG Tool 调用可以由配置选择 JSON 或 Qdrant；两种后端的核心行为一致；Qdrant 严格执行 namespace/document 隔离；服务不可用时明确失败；JSON 原有流程不回归。

## 非目标

- JSON 到 Qdrant 的自动数据迁移；
- JSON/Qdrant 双写或自动故障转移；
- Qdrant 本地嵌入模式；
- 多后端运行时热切换；
- Neo4j 或 GraphRAG 集成。
