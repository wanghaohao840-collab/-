# Neo4j 知识图谱接入设计

## 1. 目标与范围

本期为智能文档学习助手接入真实 Neo4j，并在文档成功进入现有 RAG 后，通过 LLM 结构化抽取和持久化以下图谱内容：

- 文档实体；
- 概念关系；
- 知识点依赖；
- 人物关系；
- 章节结构。

本期提供图谱构建、状态、重试、查询和删除 API，不把图谱接入问答召回，也不修改 Gradio UI。现有 RAG 是主链路：LLM 抽取或 Neo4j 写入失败不得阻断文档导入和向量问答。

## 2. 架构边界

保持现有依赖方向：`UI -> Assistant -> Tool -> Memory/RAG/Storage`。

新增独立的 `KnowledgeGraphService`，由 `RAGTool` 在文档导入成功后调用。服务层负责：

1. 从文档 metadata 和 chunks 构建章节候选；
2. 调用 LLM 抽取结构化图数据；
3. 校验、规范化和去重；
4. 调用 Neo4j 存储层完成文档级事务写入；
5. 持久化构建状态并提供显式重试。

`Neo4jGraphStore` 只负责连接管理、约束初始化、参数化 Cypher、事务写入和查询，不导入 Tool 或 UI。当前内存字典兜底实现将被真实 Neo4j driver 实现替代；连接未配置或不可达时明确失败，不伪装为已写入图数据库。

## 3. 一致性策略

RAG 与图谱采用弱一致性：

- RAG 导入成功后同步尝试建图；
- 建图成功时返回 `graph_status=ready`；
- 建图失败时 RAG 仍返回成功，并附带 `graph_status=failed` 和安全的错误摘要；
- 失败任务可通过 API 显式重试，本期不引入 Celery、消息队列或后台 worker；`retry_document_graph` 只接受 `failed` 和 `cleanup_pending` 状态，其他状态返回“文档当前状态不支持重试”且不执行 LLM 或 Neo4j 操作；
- 本地图谱状态清单是服务读取图谱状态的权威来源，记录 `document_id`、`build_id`、状态、错误类型、截断错误摘要、整文档构建次数、LLM 调用次数和更新时间，不保存密码、密钥或完整 chunk 内容；Neo4j 中的构建状态只作为事务提交标记和崩溃恢复依据；
- 文档重复导入时，Neo4j 在单个事务内删除该文档旧子图并写入完整新子图。事务失败时回滚，避免半成品覆盖旧图。

文档删除同样采用弱一致性：RAG 删除优先完成；Neo4j 删除失败时状态记为 `cleanup_pending`，可重试。任何删除语句都必须限定目标 `document_id`。

## 4. 图数据模型

所有知识节点按 `document_id` 隔离。本期不跨文档自动合并同名实体。

### 4.1 节点

- `Document {document_id, build_id, name, source, graph_status, graph_error, updated_at}`
- `Chapter {chapter_id, document_id, title, level, order, heading_path}`
- `Chunk {chunk_id, document_id, content, page_number, chunk_index}`
- `Concept {concept_id, document_id, name, normalized_name, description}`
- `KnowledgePoint {knowledge_point_id, document_id, name, normalized_name, description}`
- `Person {person_id, document_id, name, normalized_name, description}`

知识节点的稳定标识由 `document_id + 节点类型 + normalized_name` 确定。章节和 chunk 使用文档内稳定 ID。Neo4j 初始化时为这些组合键建立唯一约束或索引，初始化操作必须幂等。

### 4.2 关系

- `(Document)-[:HAS_CHAPTER]->(Chapter)`
- `(Chapter)-[:PARENT_OF]->(Chapter)`
- `(Chapter)-[:HAS_CHUNK]->(Chunk)`
- `(Document)-[:HAS_CHUNK]->(Chunk)`：仅用于无法识别所属章节的 chunk
- `(Chunk)-[:MENTIONS {evidence, confidence}]->(Concept|KnowledgePoint|Person)`
- `(Concept)-[:RELATED_TO|PART_OF|IS_A|CONTRASTS_WITH]->(Concept)`
- `(KnowledgePoint)-[:DEPENDS_ON|PREREQUISITE_OF]->(KnowledgePoint)`
- `(Person)-[:RELATED_TO {relation_name, evidence, confidence}]->(Person)`

概念和知识依赖关系类型使用固定白名单。人物自然语言关系名称存入 `relation_name`，不动态拼接为 Cypher 关系类型。LLM 产生的关系保留 `chunk_id`、证据和置信度，以支持来源追溯。

## 5. 抽取与校验流程

### 5.1 章节结构

章节优先来自解析器已有的标题和 `heading_path` metadata。缺少可靠标题时，LLM 可以从 chunk 标题与内容推断章节名称和层级，但最终章节顺序必须由原始 `chunk_index` 和 `page_number` 决定，不能采用模型生成顺序。

### 5.2 LLM 结构化抽取

chunks 按受控批次发送给项目现有 LLM 适配层。每批同时满足“最多 5 个 chunk”和“chunk 内容估算合计不超过 4,000 token”；优先使用 LLM 适配器提供的 tokenizer，缺失 tokenizer 时按每个 Unicode 字符计 1 token 做保守估算。现有单个 chunk 若意外超过预算，则单独成批并在调用前由抽取器按窗口切分，所有窗口继续引用原始 `chunk_id`，不得截断或丢弃原文。提示词要求模型只输出符合 JSON Schema 的对象，其中包含：

- concepts；
- knowledge_points；
- persons；
- concept_relations；
- knowledge_dependencies；
- person_relations；
- mentions 及其证据、置信度和来源 chunk。

Python 校验层必须执行：

- JSON Schema 与字段类型校验；
- 关系类型白名单校验；
- `confidence` 归一到 `[0, 1]`；
- 名称清洗与 `normalized_name` 生成；
- 同类型实体和重复关系去重；
- 来源 `chunk_id` 属于当前文档的校验；
- 检查关系两端实体是否都出现在本次整文档抽取形成的内存节点集合中；该检查不查询 Neo4j；
- `document_id` 强制覆盖，禁止信任模型返回的租户或文档范围字段。

每个批次最多尝试 3 次。网络超时、限流、服务端错误、非法 JSON 和 schema 校验失败允许重试；认证失败、配置错误和明确的内容策略拒绝不重试。第二、第三次调用前分别等待 `1s + jitter` 和 `2s + jitter`，其中 `jitter` 均匀分布在 `0-250ms`；服务端提供 `Retry-After` 时优先采用该值，但单次等待最多 30 秒。重试非法输出时附加精简的校验错误，但不回传其他批次内容。若单批 3 次尝试仍失败，则本次整文档构建失败：此前成功批次的结果只存在于当前进程内存，不写入 Neo4j，也不建立持久化批次缓存。未知关系类型或悬空引用在整文档内存校验阶段被发现时同样导致整文档失败，不覆盖已有图谱。

## 6. Neo4j 写入与安全

所有属性值都通过 Neo4j driver 参数传递，不把模型或用户文本拼接进 Cypher。只有代码内白名单允许的 label 和关系类型可以参与静态查询模板选择。

单文档替换流程在一个事务中执行：

1. 服务生成本次唯一 `build_id`，把本地状态原子写为 `building`；
2. 删除目标 `document_id` 的旧节点与关系；
3. `MERGE` 文档、章节、chunk 和知识节点；
4. 创建或合并白名单关系；
5. 把 Neo4j `Document.build_id` 写为本次值，同时更新 `graph_status=ready` 和时间戳；
6. 提交事务；
7. 事务提交成功后，把本地相同 `build_id` 的状态原子写为 `ready`。

若进程在第 6 步之后、第 7 步之前崩溃，启动恢复通过比对 Neo4j 与本地 `build_id` 确认事务是否已经提交。失败路径不得先把本地状态写为 `ready`。

Neo4j 连接或事务异常必须回滚。日志和对外错误不得包含密码、认证 URI、完整 prompt 或完整 chunk 内容。

连接配置使用：

- `NEO4J_URI`
- `NEO4J_USERNAME`
- `NEO4J_PASSWORD`
- `NEO4J_DATABASE`

密码从环境变量读取到局部变量后只用于初始化 Neo4j driver，不保存为 `Neo4jGraphStore` 的公开或可序列化实例属性。存储实例只保留 driver 和 database 名称；自定义 `__repr__`、`__str__`、状态对象和日志不得包含 URI、用户名、密码或 driver 认证对象。Neo4j driver 内部的凭据生命周期由官方 driver 管理，应用代码不得反射、序列化或输出 driver 内部状态。

## 7. 服务与 Tool API

所有服务接口使用统一响应信封：

`{success, document_id, status, data, error, page}`

- `data` 为接口特定对象；无数据时返回对应的空对象或空列表，不返回 `null`；
- `error` 无错误时为 `null`，否则为 `{type, message}`，其中 `message` 适用 500 字符及脱敏规则；
- `page` 仅分页接口使用；单集合查询格式为 `{limit, next_cursor}`，完整图查询格式为 `{node_limit, relation_limit, next_node_cursor, next_relation_cursor}`，非分页接口为 `null`；
- 非 `ready` 状态的查询返回 `success=false`、当前 `status` 和明确 `error`，不返回旧子图。

服务层提供以下接口：

- `build_document_graph(document_id, chunks, metadata)`；`data={build_id, node_count, relation_count, attempt_count, llm_attempt_count, updated_at}`
- `get_document_graph(document_id, node_cursor=None, relation_cursor=None, node_limit=100, relation_limit=100, include_chunk_content=False)`；两个 limit 的范围均为 `1-500`，节点和关系按各自稳定顺序独立分页；`data={nodes, relations}`，`nodes` 包含 `id`、`type` 和公开属性，默认排除 `Chunk.content`，显式请求时才返回；每条关系包含 `source_id`、`target_id`、`type`、公开属性以及两端最小摘要 `{id, type, name}`，即使端点节点不在当前节点页中也可独立解释关系
- `get_chapter_tree(document_id)`；`data={chapters}`，章节是按 `order` 排序的嵌套列表，每项包含 `chapter_id`、`title`、`level`、`order`、`heading_path`、`chunk_ids` 和 `children`；最多返回 2,000 个章节，超限时返回 `ChapterTreeTooLarge`，不得静默截断树
- `get_concept_relations(document_id, concept=None, cursor=None, limit=100)`；`data={concepts, relations}`，传入 `concept` 时只返回该概念及其一跳关系，`limit` 最大 500；cursor 只分页关系，当前页关系的端点概念随页返回且不计入 limit
- `get_knowledge_dependencies(document_id, knowledge_point=None, cursor=None, limit=100)`；`data={knowledge_points, dependencies}`，传入知识点时返回其直接前置和直接后继依赖，`limit` 最大 500；cursor 只分页依赖关系，当前页关系的端点知识点随页返回且不计入 limit
- `get_person_relations(document_id, person=None, cursor=None, limit=100)`；`data={persons, relations}`，传入人物时只返回该人物的一跳关系，`limit` 最大 500；cursor 只分页人物关系，当前页关系的端点人物随页返回且不计入 limit
- `get_graph_status(document_id)`；`data={build_id, attempt_count, llm_attempt_count, updated_at}`，错误详情通过统一 `error` 返回
- `retry_document_graph(document_id)`；使用统一信封和对应构建或清理操作的 `data`；`failed` 触发重建，`cleanup_pending` 只重试删除
- `delete_document_graph(document_id)`；`data={nodes_removed, relations_removed, updated_at}`

`RAGTool` 增加对应 action，并在 `add_document` 与 `delete_document` 的返回信息中包含图谱状态。查询参数中的 `document_id` 为必填；概念、知识点或人物名称是可选过滤条件。

重试构建时从现有 RAG pipeline 按 `document_id` 获取 chunks，不在状态清单重复保存文档正文。

## 8. 状态模型与错误边界

图谱状态至少包含：

- `pending`：已登记但尚未完成首次尝试；
- `building`：正在抽取或写入；
- `ready`：图谱可查询；
- `failed`：抽取、校验或写入失败，可重试；
- `cleanup_pending`：RAG 已删除但图子图清理失败；
- `deleted`：图子图已删除。

每次状态变更记录 `attempt_count`、`llm_attempt_count` 和 `updated_at`。`attempt_count` 只统计整文档构建入口实际开始执行的次数；`llm_attempt_count` 统计当前构建内所有批次的实际 LLM 调用次数，包括批次重试，清理操作不增加这两个计数。错误摘要最多保留 500 个字符，超出部分截断为前 499 个字符加单字符省略号 `…`；摘要写入前必须移除已知连接凭据和完整 LLM 响应。

状态存储只允许由服务实例初始化一次。初始化时扫描所有 `building` 记录：若 Neo4j 中同一文档的 `build_id` 与本地匹配且 `graph_status=ready`，则原子恢复为 `ready`；若标识不匹配，则改为 `failed`，错误类型设为 `InterruptedBuild`，错误摘要设为“进程中断，请重试”；若 Neo4j 暂时不可达，则改为 `failed`，错误类型设为 `RecoveryCheckFailed`，保留安全错误摘要并允许重试。恢复过程幂等，不能在已有构建线程运行期间再次执行。

同一进程内使用以 `document_id` 为键的 `threading.Lock` 串行化构建、重试和删除。只有公开服务入口获取文档锁；入口持锁后调用不再加锁的私有实现，如 `_build_document_graph_locked` 和 `_delete_document_graph_locked`，禁止私有实现反向调用公开入口，从结构上避免非重入锁死锁。

锁注册表的创建和访问由独立全局互斥锁保护，并为每个文档锁维护包含持有者和等待者的引用计数：线程取得锁对象引用前增加计数，完成或取消等待后在 `finally` 中减少计数，只有计数归零时才能从注册表删除。这样既避免同一文档产生不同锁，也避免文档数量增长导致锁永久堆积。

本机制覆盖单进程内多线程调用，但不覆盖多个 uvicorn worker 或多个服务实例。当前实现只支持单进程/单 worker 部署；若部署层无法保证同一 `document_id` 不被跨进程并发触发，则不得启用自动建图。多 worker 支持需要后续引入分布式锁或唯一任务协调机制。

## 9. 测试策略

### 9.1 单元测试

覆盖：

- 合法 LLM JSON 转换为图数据；
- 非法 JSON、未知关系、悬空引用和越界置信度；
- 稳定 ID 与名称规范化；
- 章节树构建和 chunk 归属；
- 实体与关系去重；
- chunk 数与 4,000 token 双重分批、超长 chunk 窗口切分及原始 `chunk_id` 保留；
- 可重试与不可重试错误分类、最多 3 次尝试、退避抖动和 `Retry-After` 上限；
- 状态转换、基于 `build_id` 的遗留 `building` 恢复、500 字符失败摘要截断及敏感信息隐藏；
- `attempt_count` 与 `llm_attempt_count` 分别计数；
- `failed`、`cleanup_pending` 和其他状态的重试准入规则；
- 同进程内相同文档串行、不同文档可独立执行；
- 公开入口不重入加锁，异常和取消路径正确回收锁引用。

### 9.2 存储契约测试

使用可注入的假 Neo4j driver 验证：

- 查询使用参数，不拼接属性值；
- 约束初始化幂等；
- 单文档原子替换；
- 事务失败触发回滚；
- 所有查询和删除都限定 `document_id`；
- 删除一个文档不会影响另一个文档；
- 存储对象的字符串表示和可序列化属性不包含 Neo4j 凭据；
- 查询接口返回第 7 节定义的统一信封，空集合返回空列表；
- 完整图的节点和关系使用独立游标与限制，关系页始终携带端点最小摘要，默认不返回 `Chunk.content`；
- 章节树超限时明确失败，不返回被截断的无效树。

### 9.3 RAG 集成测试

使用固定 LLM 响应和假图存储验证：

- 图谱成功后导入结果包含 `ready`；
- LLM 或图存储失败时 RAG 导入仍成功；
- 重复导入不会累加旧图；
- 删除图失败时 RAG 删除仍成功并产生 `cleanup_pending`；
- 重试只读取目标 `document_id` 的 chunks。
- `ready` 和 `building` 状态不会触发额外 LLM 调用。
- Neo4j 事务提交后、本地 `ready` 写入前模拟崩溃时，启动恢复可通过匹配的 `build_id` 恢复为 `ready`；不匹配和恢复检查失败进入可重试的 `failed`。

### 9.4 可选真实 Neo4j 测试

检测到 `NEO4J_TEST_URI` 时运行真实 Neo4j 集成测试，验证约束、写入、查询、替换和删除；未配置时测试明确标记为 skipped。默认测试套件不依赖外部 Neo4j 或真实 LLM 即可通过。

## 10. 验收标准

- 固定 LLM 样例可以稳定构建并查询文档、章节、概念、知识点和人物子图；
- 五类目标结构均保留来源 chunk、证据或章节归属；
- 所有读写、重试和删除操作都强制使用 `document_id`；
- Neo4j 或 LLM 故障不影响现有 RAG 导入、检索和问答；
- 重复导入采用文档级原子替换，不产生残留或重复子图；
- Neo4j 已提交但本地状态写入中断时，可通过 `build_id` 恢复一致状态；
- 删除只作用于目标文档，失败可追踪和重试；
- 同一进程内相同文档操作不会并发或死锁，空闲文档锁不会永久累积；
- 图查询使用统一响应信封并受分页或章节数量上限保护；
- 默认自动化测试无需外部服务即可通过，真实 Neo4j 测试可按环境变量启用；
- 本期不包含 GraphRAG 问答融合、图谱 UI、跨文档实体合并或后台任务队列。
