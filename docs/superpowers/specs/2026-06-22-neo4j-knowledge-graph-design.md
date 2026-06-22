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
- 失败任务可通过 API 显式重试，本期不引入 Celery、消息队列或后台 worker；
- 本地图谱状态清单记录 `document_id`、状态、错误类型、截断错误摘要、尝试次数和更新时间，不保存密码、密钥或完整 chunk 内容；
- 文档重复导入时，Neo4j 在单个事务内删除该文档旧子图并写入完整新子图。事务失败时回滚，避免半成品覆盖旧图。

文档删除同样采用弱一致性：RAG 删除优先完成；Neo4j 删除失败时状态记为 `cleanup_pending`，可重试。任何删除语句都必须限定目标 `document_id`。

## 4. 图数据模型

所有知识节点按 `document_id` 隔离。本期不跨文档自动合并同名实体。

### 4.1 节点

- `Document {document_id, name, source, graph_status, graph_error, updated_at}`
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

chunks 按受控批次发送给项目现有 LLM 适配层。提示词要求模型只输出符合 JSON Schema 的对象，其中包含：

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
- 关系端点存在性检查；
- `document_id` 强制覆盖，禁止信任模型返回的租户或文档范围字段。

任一批次返回非法 JSON、未知关系类型或悬空引用时，本次整文档构建失败，不覆盖已有图谱。

## 6. Neo4j 写入与安全

所有属性值都通过 Neo4j driver 参数传递，不把模型或用户文本拼接进 Cypher。只有代码内白名单允许的 label 和关系类型可以参与静态查询模板选择。

单文档替换流程在一个事务中执行：

1. 删除目标 `document_id` 的旧节点与关系；
2. `MERGE` 文档、章节、chunk 和知识节点；
3. 创建或合并白名单关系；
4. 更新 `Document.graph_status` 和时间戳；
5. 提交事务。

Neo4j 连接或事务异常必须回滚。日志和对外错误不得包含密码、认证 URI、完整 prompt 或完整 chunk 内容。

连接配置使用：

- `NEO4J_URI`
- `NEO4J_USERNAME`
- `NEO4J_PASSWORD`
- `NEO4J_DATABASE`

## 7. 服务与 Tool API

服务层提供以下接口：

- `build_document_graph(document_id, chunks, metadata)`
- `get_document_graph(document_id)`
- `get_chapter_tree(document_id)`
- `get_concept_relations(document_id, concept=None)`
- `get_knowledge_dependencies(document_id, knowledge_point=None)`
- `get_person_relations(document_id, person=None)`
- `get_graph_status(document_id)`
- `retry_document_graph(document_id)`
- `delete_document_graph(document_id)`

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

每次状态变更记录 `attempt_count` 和 `updated_at`。同一进程内对相同 `document_id` 的构建必须串行化，避免并发替换互相覆盖。本期不承诺跨进程分布式锁；生产多进程部署前需另行设计任务协调。

## 9. 测试策略

### 9.1 单元测试

覆盖：

- 合法 LLM JSON 转换为图数据；
- 非法 JSON、未知关系、悬空引用和越界置信度；
- 稳定 ID 与名称规范化；
- 章节树构建和 chunk 归属；
- 实体与关系去重；
- 状态转换、失败摘要截断及敏感信息隐藏。

### 9.2 存储契约测试

使用可注入的假 Neo4j driver 验证：

- 查询使用参数，不拼接属性值；
- 约束初始化幂等；
- 单文档原子替换；
- 事务失败触发回滚；
- 所有查询和删除都限定 `document_id`；
- 删除一个文档不会影响另一个文档。

### 9.3 RAG 集成测试

使用固定 LLM 响应和假图存储验证：

- 图谱成功后导入结果包含 `ready`；
- LLM 或图存储失败时 RAG 导入仍成功；
- 重复导入不会累加旧图；
- 删除图失败时 RAG 删除仍成功并产生 `cleanup_pending`；
- 重试只读取目标 `document_id` 的 chunks。

### 9.4 可选真实 Neo4j 测试

检测到 `NEO4J_TEST_URI` 时运行真实 Neo4j 集成测试，验证约束、写入、查询、替换和删除；未配置时测试明确标记为 skipped。默认测试套件不依赖外部 Neo4j 或真实 LLM 即可通过。

## 10. 验收标准

- 固定 LLM 样例可以稳定构建并查询文档、章节、概念、知识点和人物子图；
- 五类目标结构均保留来源 chunk、证据或章节归属；
- 所有读写、重试和删除操作都强制使用 `document_id`；
- Neo4j 或 LLM 故障不影响现有 RAG 导入、检索和问答；
- 重复导入采用文档级原子替换，不产生残留或重复子图；
- 删除只作用于目标文档，失败可追踪和重试；
- 默认自动化测试无需外部服务即可通过，真实 Neo4j 测试可按环境变量启用；
- 本期不包含 GraphRAG 问答融合、图谱 UI、跨文档实体合并或后台任务队列。
