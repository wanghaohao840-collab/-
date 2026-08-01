# GraphRAG 混合答案检索设计

## 目标

在现有 `RAGTool.ask` 的向量检索结果上，按当前
`(rag_namespace, document_id)` 范围补充 Neo4j 实体、关系和证据上下文，
让模型能够利用文档内的知识图谱回答实体关系问题，同时保持没有 Neo4j
或图谱尚未 ready 时的原有 RAG 行为。

## 范围

本阶段实现：

- 对普通 `ask`/joint 问答执行向量 + 图谱混合上下文。
- 图谱查询只读、文档隔离、参数化查询，并限制实体、关系和证据数量。
- 图谱上下文使用独立的 `G-*` 引用 ID，并在最终答案中展示。
- 图查询失败、图谱未就绪或 Neo4j 未配置时，默认回退到向量 RAG。
- 允许调用方通过 `graph_mode=off|auto|required` 控制行为；默认 `auto`。

本阶段不实现：

- 图谱驱动的向量召回排序或跨文档实体合并。
- `compare`、`summary` 模式的图谱改写。
- 图谱可视化 UI 或后台异步队列。

## 架构

`Neo4jGraphStore` 新增一个文档范围内的关键词实体检索方法。该方法只匹配
`Concept`、`KnowledgePoint`、`Person`、`Chapter`，再读取一跳关系和关系
属性中的证据标识，不返回 Chunk 正文。`KnowledgeGraphService` 负责 ready
门控、关键词规范化和统一错误 envelope。

`RAGTool._ask` 在普通问答路径先取得向量结果，再按选中文档调用图服务。
图谱上下文被限制在固定实体/关系数量，并追加到 LLM prompt。默认模式下
图服务异常不会改变问答成功结果；`required` 模式才返回明确错误。最终
格式化输出同时列出向量来源和图谱来源。

## 错误与隔离

- 所有 Neo4j 查询必须绑定 `rag_namespace` 和 `document_id`。
- 查询词通过 Cypher 参数传入，不拼接用户文本。
- `graph_mode=auto` 遇到 `GraphNotReady`、`GraphNotConfigured` 或驱动异常
  时仅记录降级信息并继续向量 RAG。
- `graph_mode=required` 在无法获取图上下文时返回可识别的失败信息，不调用
  LLM 生成无依据答案。
- 图谱上下文不包含 Chunk 正文，正文仍由向量检索提供。

## 验证

- Neo4j store：关键词实体、一跳关系、文档隔离和限制测试。
- Graph service：ready 门控、规范化词、错误 envelope 测试。
- RAGTool：auto/required/off、图谱引用、降级和 prompt 注入测试。
- 回归运行完整测试套件；真实 Neo4j live test 在端口可用时执行。
