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
- `QDRANT_COLLECTION` 可选，用于部署级覆盖；未设置时使用 RAG Tool 初始化时确定的 collection 名称，若调用方也未提供则固定为 `rag_knowledge_base`。
- Qdrant collection 名称在 RAG Tool 创建时确定，同一实例及其所有 namespace 共用该 collection。各 action 和 namespace pipeline 不得在运行时覆盖或动态切换 collection。
- 无效后端、缺少 URL、连接失败或 Qdrant 请求失败均明确报错，不自动回退。

## 架构

RAG Tool 及其上层 Assistant、UI 保持现有调用方式。RAG 层建立统一后端契约，由工厂根据配置创建 JSON 或 Qdrant 实现。

两种实现共同支持：

- `add_text`
- `replace_document`
- `search`
- `stats`
- `delete_document`
- `clear`
- `get_document_summary_context`

`replace_document(document_id, segments)` 是公开的文档级导入接口。`segments` 是整篇文档的有序文本段及 metadata；PDF 每页形成一个 segment 并保留 `page_number`。共享 RAG 准备层在一次调用内完成所有 segment 的切块、嵌入，并为整篇文档分配从 0 连续递增的全局 `chunk_index`，随后把完整 chunks 集合交给选定后端。RAG Tool 不得再调用 `_remove_document_chunks()`、`_save_cache()` 等后端私有方法。

`add_text` 保留给直接文本添加场景。现有文本切块、嵌入、检索结果格式及按页去重规则保持一致。JSON 实现保留当前持久化格式和启动恢复行为。Qdrant 实现通过官方 Python 客户端连接独立服务，不提供本地嵌入模式。

依赖方向继续保持为 `UI -> Assistant -> Tool -> RAG/Storage`，存储层不反向依赖 Tool。

## Qdrant collection 与 payload

Qdrant collection 使用余弦距离，向量维度与当前 embedder 的输出维度一致。后端在首次使用时创建不存在的 collection；已存在且配置兼容的 collection 直接复用，不重复创建；已存在 collection 的向量维度或距离配置不兼容时明确失败，避免破坏已有数据。

每个 point 的 payload 至少包含：

- `content`
- `document_id`
- `rag_namespace`
- `chunk_index`
- 完整且可序列化的 `metadata`

为便于过滤和返回兼容结果，来源、页码等 metadata 继续完整保留。point ID 使用 Qdrant 支持的 UUID，并按以下确定性规则生成：

```python
point_name = canonical_json([rag_namespace, document_id, chunk_index])
point_id = uuid.uuid5(PROJECT_POINT_NAMESPACE_UUID, point_name)
```

`PROJECT_POINT_NAMESPACE_UUID` 使用本项目专属固定值 `c273c00a-40ac-47a9-b475-164f135ada18`，该值首次设计时随机生成并硬编码到代码库，不从部署环境动态读取。`canonical_json` 使用固定分隔符和 UTF-8 字符语义序列化列表，避免字符串拼接歧义。该位置型 ID 在 namespace、文档和 chunk 位置不变时保持稳定，即使内容改变也会覆盖同一 point，适合现有“按 document_id 覆盖导入”的语义。

多项目共享同一 Qdrant collection 时，每个项目必须使用不同的项目 namespace UUID；仅共享服务但使用不同 collection 时不受此限制。`rag_namespace` 只约束查询和删除过滤，不能防止同一 collection 内不同项目生成相同 point ID 后在写入时互相覆盖。

## 批量写入与失败语义

Qdrant 写入每批最多 100 个 points，使用等待服务端确认的 upsert。确定性 point ID 使同一批重试保持幂等。孤儿清理、`delete_document` 和 `clear` 同样等待服务端确认后才返回成功，确保紧随其后的检索和统计能观察到修改结果。

覆盖导入按以下顺序执行：

1. 逐批 upsert 新 chunks，所有批次确认成功后才进入清理步骤；
2. 删除相同 `rag_namespace + document_id` 下 `chunk_index >= new_chunk_count` 的旧 points，即新文档缩短后遗留的孤儿 chunks。

当前切块逻辑保证 `chunk_index` 从 0 连续递增，因此范围清理与“不在本次写入集合中”等价。此顺序消除了先删除造成的空数据窗口；但 Qdrant 不提供跨批事务，多批写入期间或最终失败后，检索仍可能短暂读到新旧 chunk 混合结果。任何批次在重试后仍失败，则按根因抛出对应的 `RAGBackendError` 子类，不继续后续批次、不清理孤儿 chunks，也不尝试不可靠的客户端回滚。错误信息必须指出目标文档可能处于部分更新状态，可通过重新导入完成幂等覆盖和孤儿清理。

统一重试策略适用于幂等的远程操作：确定性 ID upsert、search/query、scroll、count、按过滤条件删除和 collection 读取。网络超时、连接中断和 Qdrant 5xx 响应最多重试 3 次，采用指数退避，三次重试前分别等待 0.5 秒、1 秒和 2 秒。Qdrant 4xx 响应不重试。collection 创建若遇到网络中断或响应不确定，不盲目再次创建；先重新读取同名 collection，存在时校验维度和距离，不存在时再重试创建。

重试耗尽后的异常按根因映射：网络、连接中断或 5xx 为 `RAGConnectionError`；401/403 认证或授权失败也为 `RAGConnectionError`，但直接失败；collection 404 或配置不兼容为 `RAGCollectionError`；其他操作 4xx 为带操作上下文的 `RAGOperationError`。

## 数据隔离与操作语义

`rag_namespace` 是 collection 内所有操作的强制过滤条件。`document_id` 是文档范围操作的附加过滤条件。

- 添加文档：RAG Tool 先把完整文档解析为有序 segments，共享准备层统一切块、嵌入并分配全局索引，再由 `replace_document()` 将完整 chunks 集合批量写入后端。
- 覆盖导入：先幂等 upsert 新 chunks，全部成功后再删除相同 `rag_namespace + document_id` 下超出新 chunk 范围的孤儿 points。
- 追加文本：`add_text(replace_existing=False)` 在 Qdrant 下先仅滚动读取目标 namespace/document 的 `chunk_index`，以最大值加一作为本次起始索引。当前版本假定同一文档只有单写者，不保证并发追加；文档导入不使用此路径。
- 检索：始终过滤当前 `rag_namespace`；传入 `document_id` 时叠加文档过滤，随后沿用现有页码去重规则。
- 全文总结：使用 page size 256，仅滚动读取目标 namespace 和文档的 payload（不返回向量），按 `chunk_index` 或页码形成稳定顺序，再沿用现有跨位置抽样策略。当前实现先分页读取后抽样，以保持现有总结行为；最多扫描 10,000 个 chunks，超过上限时明确报错，避免无界内存占用。后续若超大文档成为真实需求，再引入位置索引或服务端分段抽样。
- 删除文档：仅删除当前 namespace 下指定 `document_id` 的 points。
- 清空知识库：仅删除当前 namespace 的 points，不删除同 collection 中其他 namespace 的数据，也不删除 collection 本身。
- 统计：只统计当前 namespace，返回文档数、chunk 数、向量维度、collection 和当前后端信息。chunk 数使用带 namespace 过滤的 Qdrant count；文档数使用 page size 512 的 scroll，仅请求 `document_id` payload、不返回向量，并在客户端精确去重。文档数统计复杂度为 O(namespace chunks)，因此 `stats` 定位为显式诊断操作，不进入每次检索的热路径；本阶段不依赖版本相关的 facet 能力。

这些规则保证多个用户或用途共享 collection 时不会发生跨 namespace 读取或误删，并继续保证 `document_id` 隔离。

## 错误处理

RAG 后端层提供明确的配置错误和后端操作错误。Qdrant 客户端异常应保留操作上下文并转换为稳定的项目异常信息，不暴露 API key。异常和日志中的 URL 只保留脱敏后的 scheme、host、port 与 path，移除 userinfo，并隐藏 query string 中的凭据参数；不得记录原始请求 headers、Authorization、API key 或客户端异常中的 request 对象。现有 RAG Tool 继续负责将安全转换后的异常变成用户可读结果。

选择 Qdrant 即代表要求使用 Qdrant。服务不可用时，初始化或首次操作必须失败，不得创建 JSON 缓存作为替代品。

项目定义以下异常类型，全部继承自 `RAGBackendError`：

| 异常类型 | 使用场景 |
|---|---|
| `RAGConfigError` | 后端名称错误、Qdrant URL 缺失等配置无效；在初始化阶段抛出。 |
| `RAGConnectionError` | 服务不可达、连接中断或认证失败。 |
| `RAGCollectionError` | collection 不存在，或已有 collection 的维度、距离配置不兼容。 |
| `RAGDocumentTooLargeError` | 全文总结扫描超过 10,000 chunks；用户提示应建议拆分文档后分别导入。 |
| `RAGOperationError` | 写入、删除、检索、统计等操作失败；附带安全的操作类型及目标 `document_id` 上下文。 |

RAG Tool 捕获 `RAGBackendError` 基类并转换为用户可读结果；未预期的编程错误不伪装成普通后端错误。

RAG Tool 提供结构化内部入口 `execute_result()`，返回统一的 `RAGActionResult`，至少包含 `success`、`message`、`data`、`error_code`。Assistant 对导入、删除和清空操作使用该入口，并且只有 `success=True` 时才修改当前文档、统计、学习历史或问答记录。现有 `execute()` 调用 `execute_result()` 后格式化为兼容的文本结果，继续服务 Agent、UI 展示和既有示例。

初始化阶段的 `RAGConfigError`、`RAGConnectionError` 和 `RAGCollectionError` 允许从 RAG Tool 构造过程向上传播，使应用启动明确失败；操作阶段的后端异常由 `execute_result()` 转换为失败结果。这样既避免服务不可用时创建一个表面可用的 Assistant，也避免后端操作失败后业务状态被错误更新。

## 兼容性

- `RAG_BACKEND` 未设置时继续使用 JSON，现有示例和 UI 无需 Qdrant 即可运行。
- RAG Tool 对外 action、参数和返回文本保持兼容。
- Assistant 改用 `execute_result()` 获取可靠成功状态；Agent 和现有 `execute()` 调用方无需修改。
- `add_text(replace_existing=False)` 在两个后端均表示追加；同一文档并发追加不在本阶段支持范围。
- `save_cache` 对 JSON 后端保持原语义；Qdrant 后端接受该兼容参数但忽略其值，因为每次已确认的远程修改都会持久化。
- JSON 缓存结构和恢复逻辑不做迁移或重写。
- Qdrant 与 JSON 不进行双写，也不自动迁移已有 JSON 数据。
- 当前后端选择在进程启动/对象创建时确定，不支持运行中的热切换。

## 测试与验收

单元测试使用可注入的 Qdrant 客户端 fake 或 mock，不要求测试环境启动真实服务。至少覆盖：

- 默认选择 JSON、显式选择 Qdrant 及无效配置失败；
- Qdrant collection 创建和不兼容 collection 拒绝；
- 进程重启后可正常复用维度与距离配置兼容的已有 collection，且不重复创建；
- 写入 payload 保留内容、来源、页码、namespace 和 `document_id`；
- UUID5 point ID 对同一 namespace、文档和 chunk 位置保持稳定，且不同位置不会碰撞；
- PDF/TXT/Markdown/DOCX 都先准备完整文档 chunks，再通过 `replace_document()` 一次提交，PDF 页码 metadata 保持不变；
- RAG Tool 不依赖后端私有方法，整篇文档的 `chunk_index` 跨 segment 连续；
- 批次切分、先 upsert 后清理孤儿的覆盖顺序，以及缩短文档后不存在遗留 points；
- 幂等远程操作仅对瞬时网络/5xx 错误按 0.5、1、2 秒退避重试，4xx 不重试；
- collection 创建响应不确定时先读取校验，避免盲目重复创建；
- 重试耗尽后按根因映射 `RAGConnectionError`、`RAGCollectionError` 或 `RAGOperationError`，并提示可能存在新旧混合的部分更新；
- 检索不会跨 namespace 或跨指定文档；
- 覆盖导入只替换目标文档；
- 删除文档只删除目标范围；
- `clear` 只清空当前 namespace；
- 全文总结读取和抽样顺序稳定；
- 全文总结分页且不读取向量，并在超过 10,000 chunks 时拒绝无界扫描；
- stats 精确统计当前 namespace 的 chunk 数和去重文档数，不读取向量；
- URL、query string、headers 和客户端异常 request 信息不会泄露凭据；
- 10,000 chunks 上限抛出 `RAGDocumentTooLargeError`，其他异常按定义的层次映射；
- 后端导入、删除或清空失败时，Assistant 不修改当前文档、统计或学习历史；
- `execute()` 文本兼容包装和 `execute_result()` 结构化结果表达同一成功/失败状态；
- `replace_existing=False` 追加到现有最大 `chunk_index` 之后，`save_cache` 在 Qdrant 下无副作用；
- upsert、孤儿清理、文档删除和 namespace 清空均等待服务端确认；
- JSON 持久化、检索、删除和重启恢复回归测试。

自动化测试不依赖外部服务。连接独立 Qdrant 的人工集成验证步骤见文末附录。

依赖固定为 `qdrant-client==1.18.0`，人工集成验证使用 `qdrant/qdrant:v1.18.2`。升级任一版本时必须重新运行 Qdrant 单元测试与本附录的集成验证，再同步更新这两个版本。

验收成功标准：同一套 RAG Tool 调用可以由配置选择 JSON 或 Qdrant；两种后端的核心行为一致；Qdrant 严格执行 namespace/document 隔离；服务不可用时明确失败；JSON 原有流程不回归。

## 非目标

- JSON 到 Qdrant 的自动数据迁移；
- JSON/Qdrant 双写或自动故障转移；
- Qdrant 本地嵌入模式；
- 多后端运行时热切换；
- Neo4j 或 GraphRAG 集成。

## 附录：Qdrant 集成验证步骤（人工执行）

### 前置条件

启动本地独立 Qdrant 服务：

```powershell
docker run --rm --name qdrant-rag-test -p 6333:6333 qdrant/qdrant:v1.18.2
$env:RAG_BACKEND = "qdrant"
$env:QDRANT_URL = "http://localhost:6333"
$env:QDRANT_COLLECTION = "rag_knowledge_base"
```

Linux/macOS 使用：

```bash
docker run --rm --name qdrant-rag-test -p 6333:6333 qdrant/qdrant:v1.18.2
export RAG_BACKEND=qdrant
export QDRANT_URL=http://localhost:6333
export QDRANT_COLLECTION=rag_knowledge_base
```

Qdrant Dashboard 地址为 `http://localhost:6333/dashboard`。

### 验证步骤

1. 启动应用，确认日志显示选用 Qdrant 且 collection 创建成功；在 Dashboard 中确认存在 `rag_knowledge_base`。
2. 导入一篇文档，确认 points 数量增加，payload 包含 `document_id`、`rag_namespace`、`chunk_index`、内容及来源 metadata。
3. 执行检索，确认结果只来自目标 namespace/文档，且来源、页码等字段与导入文档一致。
4. 执行 `stats`，确认精确文档数和 chunk 数与已导入内容匹配。
5. 修改同一文档后覆盖导入：等长切块时 points 数量不增加，缩短时数量减少；检索不再返回被替换或已成为孤儿的旧内容。
6. 删除该文档，确认对应 points 消失，其他文档和 namespace 的 points 不受影响。
7. 执行 `clear`，确认当前 namespace 的 points 全部删除，预先放置的其他 namespace points 保持不变。
8. 重新导入数据并重启应用，确认兼容的已有 collection 被直接复用，数据仍可检索。
9. 停止 Qdrant 服务后重启应用，确认得到 `RAGConnectionError`，且未创建或写入 JSON 缓存。
